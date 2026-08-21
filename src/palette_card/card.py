"""High-resolution card rendering with three deliberately different layouts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

from .palette import Color, derive_palette_roles


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _rgb(color: Color | tuple[int, int, int]) -> tuple[int, int, int]:
    return color.rgb if isinstance(color, Color) else color


def _rounded(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _wrapped_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for raw_word in words:
        # Break a URL/long token too; otherwise it could cross a decorative
        # shape and invalidate the contrast guarantee for part of the glyphs.
        chunks: list[str] = []
        remaining = raw_word
        while remaining and draw.textbbox((0, 0), remaining, font=font)[2] > max_width:
            split_at = max(1, len(remaining) // 2)
            while split_at > 1 and draw.textbbox((0, 0), remaining[:split_at], font=font)[2] > max_width:
                split_at -= 1
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]
        chunks.append(remaining)
        for word in chunks:
            candidate = word if not current else current + " " + word
            if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
    if current:
        lines.append(current)
    return lines


def _line_height(draw: ImageDraw.ImageDraw, font, spacing: int = 0) -> int:
    box = draw.textbbox((0, 0), "Ag", font=font)
    return max(1, box[3] - box[1]) + spacing


def _ellipsis_line(draw: ImageDraw.ImageDraw, line: str, font, max_width: int) -> str:
    """Trim one line to its width while retaining a visible ellipsis."""

    suffix = "…"
    candidate = line.rstrip()
    while candidate and draw.textbbox((0, 0), candidate + suffix, font=font)[2] > max_width:
        candidate = candidate[:-1]
    return (candidate + suffix) if candidate else suffix


def _bounded_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> tuple[str, ...]:
    """Wrap and cap editable text so its allocated block stays bounded."""

    max_lines = max(1, int(max_lines))
    lines = _wrapped_lines(draw, text, font, max_width)
    if len(lines) <= max_lines:
        return tuple(lines)
    kept = lines[:max_lines]
    kept[-1] = _ellipsis_line(draw, kept[-1], font, max_width)
    return tuple(kept)


def _block_height(draw: ImageDraw.ImageDraw, lines: Sequence[str], font, spacing: int) -> int:
    return _line_height(draw, font) * max(1, len(lines)) + spacing * max(0, len(lines) - 1)


@dataclass(frozen=True)
class TextLayout:
    """Bounded text lines and allocated boxes for a card variation."""

    title_lines: tuple[str, ...]
    message_lines: tuple[str, ...]
    title_box: tuple[int, int, int, int]
    message_box: tuple[int, int, int, int]


def _card_fonts(size: tuple[int, int]):
    """Keep custom small canvases usable while preserving default styling."""

    scale = min(size[0] / 1600, size[1] / 1000)
    return _font(max(24, int(76 * scale)), True), _font(max(16, int(38 * scale))), _font(max(14, int(28 * scale)))


def calculate_text_layout(size: tuple[int, int], variant: int, title: str, message: str) -> TextLayout:
    """Calculate non-overlapping title/message boxes for one card variant.

    The returned boxes are allocation bounds (including line spacing), making
    the layout easy to test without OCR or pixel inspection. Both fields are
    capped to the same maximum lengths used by :func:`render_card`.
    """

    if size[0] < 400 or size[1] < 300:
        raise ValueError("card size is too small for a readable export")
    scratch = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(scratch)
    title_font, message_font, _ = _card_fonts(size)
    title = (title or "A little color for you")[:80]
    message = (message or "Made from the colors in your photo.")[:240]
    title_spacing, message_spacing = 8, 12 if variant % 3 != 2 else 14
    title_line_height = _line_height(draw, title_font)
    message_line_height = _line_height(draw, message_font)

    if variant % 3 == 0:
        x, title_y, max_width = 100, 220, 700
        footer_top = size[1] - 145
        title_capacity = max(1, min(3, (footer_top - title_y - 24 - message_line_height) // title_line_height))
        title_lines = _bounded_lines(draw, title, title_font, max_width, title_capacity)
        title_height = _block_height(draw, title_lines, title_font, title_spacing)
        message_y = title_y + title_height + 24
        message_capacity = max(1, (footer_top - message_y) // (message_line_height + message_spacing))
        message_lines = _bounded_lines(draw, message, message_font, max_width, message_capacity)
    elif variant % 3 == 1:
        x, title_y, max_width = 100, 190, 760
        top_panel_bottom = int(size[1] * 0.48) - 24
        title_capacity = max(1, min(2, (top_panel_bottom - title_y) // title_line_height))
        title_lines = _bounded_lines(draw, title, title_font, max_width, title_capacity)
        title_height = _block_height(draw, title_lines, title_font, title_spacing)
        message_y, max_width = 600, 700
        swatch_top = size[1] - 180
        message_capacity = max(1, (swatch_top - message_y) // (message_line_height + message_spacing))
        message_lines = _bounded_lines(draw, message, message_font, max_width, message_capacity)
    else:
        max_width, title_y = 980, 280
        swatch_top = size[1] - 190
        title_capacity = max(1, min(2, (swatch_top - title_y - 24 - message_line_height) // title_line_height))
        title_lines = _bounded_lines(draw, title, title_font, max_width, title_capacity)
        title_height = _block_height(draw, title_lines, title_font, title_spacing)
        message_y = max(450, title_y + title_height + 24)
        message_capacity = max(1, (swatch_top - message_y) // (message_line_height + message_spacing))
        message_lines = _bounded_lines(draw, message, message_font, max_width, message_capacity)

    title_height = _block_height(draw, title_lines, title_font, title_spacing)
    message_height = _block_height(draw, message_lines, message_font, message_spacing)
    return TextLayout(
        title_lines=title_lines,
        message_lines=message_lines,
        title_box=(x if variant % 3 != 2 else (size[0] - max_width) // 2, title_y, (x + max_width) if variant % 3 != 2 else (size[0] + max_width) // 2, title_y + title_height),
        message_box=(x if variant % 3 != 2 else (size[0] - max_width) // 2, message_y, (x + max_width) if variant % 3 != 2 else (size[0] + max_width) // 2, message_y + message_height),
    )


def render_card(image: Image.Image | None, colors: Sequence[Color], title: str, message: str, object_label: str, variant: int = 0, size: tuple[int, int] = (1600, 1000), design_palette=None) -> Image.Image:
    """Render one high-resolution design variation."""

    if size[0] < 400 or size[1] < 300:
        raise ValueError("card size is too small for a readable export")
    roles = design_palette or derive_palette_roles(colors)
    # ``roles`` is the researched design palette: every text-bearing semantic
    # fill has an explicit WCAG-safe on-color. Source colors are not stretched
    # across the canvas as saturated backgrounds.
    bg, surface, accent = map(_rgb, (roles.background, roles.surface, roles.accent))
    tint_one, tint_two, tint_three, tint_four = map(_rgb, roles.tints)
    text_on_bg = _rgb(roles.on_background)
    text_on_surface = _rgb(roles.on_surface)
    canvas = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(canvas)
    width, height = size
    title_font, message_font, small_font = _card_fonts(size)
    title = (title or "A little color for you")[:80]
    message = (message or "Made from the colors in your photo.")[:240]
    label = (object_label or "object").replace("_", " ").title()
    layout = calculate_text_layout(size, variant, title, message)

    if variant % 3 == 0:
        # Elegant editorial layout: left copy, right photo, stacked swatches.
        draw.rectangle((0, 0, width * 0.58, height), fill=surface)
        # Lighter, desaturated derivatives carry the photo relationship into
        # the abstract areas. The vivid accent is a narrow edge detail only.
        draw.ellipse((width - 480, -180, width + 120, 420), fill=tint_one)
        draw.ellipse((width - 300, height - 290, width + 120, height + 120), fill=tint_two)
        draw.rounded_rectangle((width - 690, 178, width - 676, 782), radius=7, fill=accent)
        if image is not None:
            # Contain (including a deliberate upscale for small uploads) so
            # the photo remains a prominent subject in the editorial layout.
            thumb = ImageOps.contain(image.convert("RGB"), (580, 580), method=Image.Resampling.LANCZOS)
            x, y = width - 680, 190
            photo = Image.new("RGB", (580, 580), text_on_surface)
            photo.paste(thumb, ((580 - thumb.width) // 2, (580 - thumb.height) // 2))
            photo = photo.filter(ImageFilter.UnsharpMask(radius=1, percent=80, threshold=3))
            mask = Image.new("L", photo.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, 579, 579), radius=42, fill=255)
            canvas.paste(photo, (x, y), mask)
        draw.text((100, 120), label.upper(), fill=text_on_surface, font=small_font)
        draw.multiline_text((layout.title_box[0], layout.title_box[1]), "\n".join(layout.title_lines), fill=text_on_surface, font=title_font, spacing=8)
        draw.multiline_text((layout.message_box[0], layout.message_box[1]), "\n".join(layout.message_lines), fill=text_on_surface, font=message_font, spacing=12)
        draw.text((100, height - 110), "PALETTECARD AI  •  DEMO DESIGN", fill=text_on_surface, font=small_font)
    elif variant % 3 == 1:
        # Playful collage with neutral negative space and tinted abstract
        # shapes. This avoids making a multihue photo compete with a vivid
        # full-width color block.
        draw.rectangle((0, 0, width, height * 0.48), fill=surface)
        draw.rectangle((0, height * 0.48, width, height), fill=bg)
        draw.ellipse((width - 500, 80, width - 20, 560), fill=tint_three)
        draw.ellipse((width - 700, 390, width - 100, 990), fill=tint_four)
        draw.rounded_rectangle((100, 128, 440, 142), radius=7, fill=accent)
        draw.text((100, 90), label.upper(), fill=text_on_surface, font=small_font)
        draw.multiline_text((layout.title_box[0], layout.title_box[1]), "\n".join(layout.title_lines), fill=text_on_surface, font=title_font, spacing=8)
        draw.multiline_text((layout.message_box[0], layout.message_box[1]), "\n".join(layout.message_lines), fill=text_on_bg, font=message_font, spacing=12)
        for index, color in enumerate((roles.background, roles.surface, roles.primary, roles.secondary)):
            x = 100 + index * 88
            draw.ellipse((x, height - 155, x + 58, height - 97), fill=_rgb(color))
    else:
        # Minimalist framed card: best for a formal greeting or invitation.
        draw.rectangle((54, 54, width - 54, height - 54), outline=tint_one, width=5)
        draw.rectangle((76, 76, width - 76, height - 76), outline=tint_two, width=2)
        draw.text((width // 2, 170), label.upper(), fill=text_on_bg, font=small_font, anchor="ma")
        draw.multiline_text((width // 2, (layout.title_box[1] + layout.title_box[3]) // 2), "\n".join(layout.title_lines), fill=text_on_bg, font=title_font, anchor="mm", align="center", spacing=8)
        draw.multiline_text((width // 2, (layout.message_box[1] + layout.message_box[3]) // 2), "\n".join(layout.message_lines), fill=text_on_bg, font=message_font, anchor="mm", align="center", spacing=14)
        swatch_y = height - 190
        swatch_width = 150
        start = (width - swatch_width * 5) // 2
        # Keep observed source swatches visible in this variation; these are
        # intentionally different from the semantic design-role colors.
        for index, color in enumerate(tuple(roles.source[:5])):
            x = start + index * swatch_width
            draw.rectangle((x, swatch_y, x + swatch_width, swatch_y + 66), fill=_rgb(color))
        # Keep the footer above the inner frame and comfortably below source
        # swatches (the old y=height-90 placement touched the border).
        draw.text((width // 2, height - 120), "COLOR STORY  /  PALETTECARD AI", fill=text_on_bg, font=small_font, anchor="ma")
    return canvas


def render_card_set(image: Image.Image | None, colors: Sequence[Color], title: str, message: str, object_label: str, size: tuple[int, int] = (1600, 1000), design_palette=None) -> list[Image.Image]:
    """Return the three card designs shown in the application gallery."""

    return [render_card(image, colors, title, message, object_label, variant=i, size=size, design_palette=design_palette) for i in range(3)]


def save_cards(cards: Sequence[Image.Image], directory: str | Path, stem: str = "palette-card") -> list[Path]:
    """Save cards as PNG files and return their paths."""

    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, card in enumerate(cards, 1):
        path = folder / f"{stem}-{index}.png"
        card.save(path, format="PNG", optimize=True)
        paths.append(path)
    return paths
