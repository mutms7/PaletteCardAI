from pathlib import Path

from PIL import Image

from palette_card.card import calculate_text_layout, render_card_set, save_cards
from palette_card.palette import contrast_ratio, extract_palette, text_and_background_for_contrast


def test_card_set_is_high_resolution_and_distinct(tmp_path: Path):
    source = Image.new("RGB", (64, 48), (220, 60, 100))
    palette = extract_palette(source)
    cards = render_card_set(source, palette, "Hello", "A message from a test.", "heart")
    assert len(cards) == 3
    assert all(card.size == (1200, 1600) for card in cards)
    assert all(card.height > card.width for card in cards)
    assert len({card.tobytes() for card in cards}) == 3
    paths = save_cards(cards, tmp_path)
    assert len(paths) == 3
    assert all(path.exists() and path.suffix == ".png" for path in paths)


def test_uniform_red_card_surface_text_pair_meets_wcag():
    safe_fill, text = text_and_background_for_contrast((220, 60, 100))
    assert contrast_ratio(safe_fill, text) >= 4.5


def test_card_wraps_unbroken_editable_text():
    source = Image.new("RGB", (24, 24), (220, 60, 100))
    cards = render_card_set(source, extract_palette(source), "X" * 80, "Y" * 240, "balloon")
    assert len(cards) == 3


def test_max_length_text_layout_has_bounded_non_overlapping_blocks():
    for variant in range(3):
        layout = calculate_text_layout((1200, 1600), variant, "T" * 80, "M" * 240)
        assert layout.title_box[3] <= layout.message_box[1]
        assert layout.title_box[2] - layout.title_box[0] > 0
        assert layout.message_box[2] - layout.message_box[0] > 0
        assert layout.title_box[3] < 390  # the source photo begins below the title
        assert layout.message_box[1] >= 1300  # the source photo ends above the message
        assert layout.message_box[3] < 1580


def test_each_portrait_cover_keeps_the_unchanged_photo_at_its_center():
    source = Image.new("RGB", (24, 24), (220, 60, 100))
    cards = render_card_set(source, extract_palette(source), "Hello", "A message.", "heart")
    assert all(card.getpixel((card.width // 2, card.height // 2)) == (220, 60, 100) for card in cards)
