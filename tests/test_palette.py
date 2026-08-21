from PIL import Image

from palette_card.palette import Color, choose_text_color, contrast_ratio, derive_palette_roles, extract_palette, text_and_background_for_contrast


def test_extract_palette_returns_five_colors_for_uniform_rgb_image():
    image = Image.new("RGB", (3, 2), (220, 40, 60))
    colors = extract_palette(image)
    assert len(colors) == 5
    assert colors[0].rgb == (220, 40, 60)
    assert all(color.hex.startswith("#") for color in colors)


def test_extract_palette_handles_transparent_grayscale_and_is_deterministic():
    image = Image.new("RGBA", (18, 11), (20, 80, 200, 0))
    image.putpixel((2, 2), (255, 0, 0, 255))
    first = extract_palette(image)
    second = extract_palette(image)
    assert first == second
    gray = Image.new("L", (1, 1), 128)
    assert len(extract_palette(gray)) == 5


def test_roles_choose_readable_text():
    roles = derive_palette_roles([Color((250, 250, 250)), Color((20, 40, 80))])
    assert contrast_ratio(roles.surface, roles.text) >= 4.5 or contrast_ratio(roles.background, roles.text) >= 3
    assert choose_text_color(Color((250, 250, 250))).rgb[0] < 100


def test_adversarial_fills_are_adjusted_until_text_is_readable():
    for rgb in ((220, 60, 100), (128, 128, 128), (35, 110, 130), (245, 110, 30)):
        safe_background, safe_text = text_and_background_for_contrast(rgb)
        assert contrast_ratio(safe_background, safe_text) >= 4.5

