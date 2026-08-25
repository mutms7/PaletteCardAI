from pathlib import Path

from PIL import Image, ImageDraw

from palette_card.app import analyze_image
from palette_card.card import render_card_set
from palette_card.design import (
    derive_design_palette,
    oklab_to_srgb,
    oklch_to_srgb,
    srgb_to_oklab,
    srgb_to_oklch,
)
from palette_card.palette import Color, contrast_ratio, extract_palette


def rgb_balloon_photo() -> Image.Image:
    image = Image.new("RGB", (180, 100), (248, 248, 246))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 10, 72, 76), fill=(225, 22, 40))
    draw.ellipse((57, 20, 123, 84), fill=(25, 60, 225))
    draw.ellipse((108, 8, 174, 76), fill=(24, 205, 58))
    return image


def test_rgb_source_gets_neutral_surfaces_and_restrained_derived_accents():
    source = extract_palette(rgb_balloon_photo(), n_colors=5)
    design = derive_design_palette(source)
    assert srgb_to_oklch(design.background)[1] < 0.03
    assert srgb_to_oklch(design.surface)[1] < 0.03

    source_chroma = max(srgb_to_oklch(color)[1] for color in source)
    assert all(srgb_to_oklch(tint)[0] > 0.70 for tint in design.tints)
    assert all(srgb_to_oklch(tint)[1] <= source_chroma + 0.01 for tint in design.tints)

    accent_hues = {round(srgb_to_oklch(color)[2] / 12) for color in (design.primary, design.secondary, design.accent) if srgb_to_oklch(color)[1] > 0.10}
    assert len(accent_hues) <= 2


def test_muted_source_tints_never_gain_chroma_over_their_related_source():
    source = [Color((170, 165, 160), 0.7), Color((180, 175, 170), 0.3)]
    design = derive_design_palette(source)
    primary_chroma = srgb_to_oklch(source[0])[1]
    secondary_chroma = srgb_to_oklch(source[1])[1]
    # Tints 1/3 follow primary; tints 2/4 follow secondary. The tolerance
    # covers the unavoidable sub-8-bit Oklab round-trip noise.
    for index in (0, 2):
        assert srgb_to_oklch(design.tints[index])[1] <= primary_chroma + 1e-3
    for index in (1, 3):
        assert srgb_to_oklch(design.tints[index])[1] <= secondary_chroma + 1e-3
    assert max(srgb_to_oklch(tint)[1] for tint in design.tints) <= 0.06
    assert design.quality.strong_accent_count == 0


def test_quality_breakdown_is_calibrated_to_rendered_accent_usage():
    muted = derive_design_palette([Color((170, 165, 160))])
    red = derive_design_palette([Color((220, 30, 45))])
    rgb = derive_design_palette([Color((220, 30, 45)), Color((30, 50, 220)), Color((30, 210, 50))])
    assert muted.quality.strong_accent_count == 0
    assert red.quality.strong_accent_count == 1
    assert rgb.quality.strong_accent_count == 2
    assert 0.0 < red.quality.strong_accent_area < rgb.quality.strong_accent_area
    assert muted.quality.accent_restraint > red.quality.accent_restraint > rgb.quality.accent_restraint
    # Relationship uses the derived role hue/chroma, not the number of
    # source hues; all of these remain tied to the observed source colors.
    assert 0.0 <= muted.quality.source_relationship <= 1.0
    assert 0.0 <= rgb.quality.source_relationship <= 1.0


def test_semantic_role_pairs_are_wcag_safe():
    design = derive_design_palette([Color((240, 15, 35)), Color((25, 55, 230)), Color((25, 215, 55))])
    for fill, text in (
        (design.background, design.on_background),
        (design.surface, design.on_surface),
        (design.primary, design.on_primary),
        (design.secondary, design.on_secondary),
        (design.accent, design.on_accent),
    ):
        assert contrast_ratio(fill, text) >= 4.5
    assert design.quality.contrast_pass is True
    assert design.quality.score > 0


def test_extreme_and_monochrome_sources_are_safe_and_deterministic():
    colors = [Color((0, 0, 0)), Color((255, 255, 255)), Color((128, 128, 128))]
    first = derive_design_palette(colors)
    second = derive_design_palette(colors)
    assert first == second
    for role, on_role in (
        (first.background, first.on_background),
        (first.surface, first.on_surface),
        (first.primary, first.on_primary),
        (first.secondary, first.on_secondary),
        (first.accent, first.on_accent),
    ):
        assert contrast_ratio(role, on_role) >= 4.5


def test_oklab_round_trip_and_oklch_gamut_mapping():
    for rgb in ((0, 0, 0), (255, 255, 255), (255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0), (128, 128, 128)):
        reconstructed = oklab_to_srgb(srgb_to_oklab(rgb))
        assert max(abs(one - two) for one, two in zip(rgb, reconstructed)) <= 1
    mapped = oklch_to_srgb((0.72, 0.8, 35.0))
    assert all(0 <= channel <= 255 for channel in mapped)
    assert srgb_to_oklch(mapped)[0] == srgb_to_oklch(mapped)[0]  # finite


def test_status_exposes_source_and_derived_design_decisions(tmp_path: Path):
    _, files, status = analyze_image(rgb_balloon_photo(), "balloon", "Colors", "A test card.", output_dir=tmp_path)
    assert len(files) == 3
    assert "Source colors (observed)" in status
    assert "Design roles (derived)" in status
    assert "Design rationale:" in status
    assert "heuristic design quality" in status


def test_cards_use_design_palette_and_remain_distinct():
    photo = rgb_balloon_photo()
    cards = render_card_set(photo, extract_palette(photo), "Balloons", "A colorful message.", "balloon")
    assert len(cards) == 3
    assert all(card.size == (1200, 1600) for card in cards)
    assert len({card.tobytes() for card in cards}) == 3
