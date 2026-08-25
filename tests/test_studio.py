import html
import json
import re
from pathlib import Path

from palette_card.app import (
    _color_story_html,
    _mode_badge,
    _theme_payload_html,
    _studio_theme_from_status,
    build_app,
    derive_studio_theme,
)
from palette_card.design import derive_design_palette, srgb_to_oklch
from palette_card.palette import Color, contrast_ratio


def _components_by_elem_id(demo):
    return {
        component["props"]["elem_id"]: component["props"]
        for component in demo.config["components"]
        if component.get("props", {}).get("elem_id")
    }


def test_studio_copy_and_states_are_present(tmp_path: Path):
    demo = build_app(
        checkpoint_path=tmp_path / "missing-object.pt",
        palette_checkpoint_path=tmp_path / "missing-palette.pt",
        output_dir=tmp_path / "cards",
    )
    components = _components_by_elem_id(demo)
    rendered_copy = "\n".join(str(component.get("props", {})) for component in demo.config["components"])

    assert _mode_badge(None) == "Demo Mode · choose the object yourself."
    assert "A small color studio" in rendered_copy
    assert "Your photo has a" in rendered_copy
    assert "Let's draw it out!" in rendered_copy
    assert "Drop a photo of one centered object" in rendered_copy
    assert "Flower, heart, ring, cake, or balloon · JPG, PNG, or WebP" in rendered_copy
    assert "How should we identify it?" in rendered_copy
    assert "Ta-da! Your cards are ready" in rendered_copy
    assert "Download all 3 PNGs" in rendered_copy
    assert "Color story" in rendered_copy
    assert "Runs on this computer by default." in rendered_copy

    assert components["pc-generate"]["interactive"] is False
    assert components["pc-empty-state"]["visible"] is True
    assert components["pc-results"]["visible"] is False
    assert "@media (prefers-reduced-motion:reduce)" in demo._palette_card_css
    assert "@media (max-width:520px)" in demo._palette_card_css
    assert "width:100%!important" in demo._palette_card_css
    assert "min-width:0!important" in demo._palette_card_css
    assert "Schoolbell" in demo._palette_card_css
    assert "data:font/ttf;base64," in demo._palette_card_css
    assert "pc-trail" in rendered_copy
    assert ".pc-gallery .grid-wrap" in demo._palette_card_css
    assert ".pc-gallery .grid-container" in demo._palette_card_css
    assert "repeat(3,minmax(180px,1fr))" in demo._palette_card_css
    assert "pc-theme-payload" in rendered_copy
    assert "MutationObserver" in demo._palette_card_js
    assert "prefers-reduced-motion" in demo._palette_card_js
    assert "pc-theme-transition" in demo._palette_card_js


def test_color_story_separates_observed_and_derived_roles():
    status = "\n".join(
        (
            "Palette: #aa1122 (60%)  #1133aa (40%)  [Source colors (observed)]",
            "Design roles (derived): {'background': '#f9f7f2', 'surface': '#fffdfa', 'primary': '#861b36', 'secondary': '#344992', 'accent': '#861b36'}",
        )
    )
    story = _color_story_html(status)

    assert "Observed in the photo" in story
    assert "Translated into design roles" in story
    assert story.count('<span class="pc-swatch"') == 7
    assert "#aa1122" in story
    assert "#344992" in story


def test_studio_theme_uses_derived_roles_and_safe_hex_tokens():
    roles = derive_design_palette(
        (
            Color((215, 38, 66), 0.5),
            Color((35, 72, 190), 0.3),
            Color((236, 185, 54), 0.2),
        )
    )

    theme = derive_studio_theme(roles)

    assert set(theme) == {
        "canvas",
        "surface",
        "surface-muted",
        "ink",
        "muted",
        "rule",
        "accent",
        "accent-soft",
        "accent-contrast",
        "secondary",
        "decorative",
        "photo-accent",
        "photo-tint",
    }
    assert all(re.fullmatch(r"#[0-9A-F]{6}", value) for value in theme.values())
    assert theme["accent"] == roles.accent.hex
    assert theme["photo-accent"] == theme["accent"]

    def rgb(value: str) -> tuple[int, int, int]:
        return tuple(int(value[index:index + 2], 16) for index in (1, 3, 5))

    # Soft fills and rules must stay visually light even when the source
    # accent is dark enough to use as a button. Muted copy remains readable
    # against the surface it is placed on.
    assert srgb_to_oklch(rgb(theme["accent-soft"]))[0] >= 0.80
    assert srgb_to_oklch(rgb(theme["surface-muted"]))[0] >= 0.84
    assert srgb_to_oklch(rgb(theme["rule"]))[0] >= 0.75
    assert srgb_to_oklch(rgb(theme["decorative"]))[0] >= 0.78
    assert srgb_to_oklch(rgb(theme["accent"]))[1] > srgb_to_oklch(rgb(theme["accent-soft"]))[1]
    assert contrast_ratio(rgb(theme["muted"]), rgb(theme["surface"])) >= 4.5


def test_theme_payload_is_hidden_json_and_bad_payload_fails_closed():
    theme = derive_studio_theme(
        {
            "background": "#F9F7F2",
            "surface": "#FFFDF9",
            "primary": "#861B36",
            "secondary": "#344992",
            "accent": "#861B36",
            "on_surface": "#171719",
            "on_accent": "#FFFFFF",
        }
    )
    payload = _theme_payload_html(theme)
    match = re.search(r'data-palette-card-theme="([^"]+)"', payload)
    assert match
    assert " hidden " in payload
    assert json.loads(html.unescape(match.group(1))) == theme

    assert _theme_payload_html({"accent": "url(javascript:bad)"}).endswith('data-palette-card-theme="{}" aria-hidden="true"></div>')


def test_malformed_status_does_not_create_a_theme():
    assert _studio_theme_from_status("We couldn’t create the cards. DETAILS: bad") is None
