"""Portrait card-cover rendering with three handmade layouts."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .palette import Color, derive_palette_roles


_SCHOOLBELL = Path(__file__).resolve().parent / "assets" / "Schoolbell-Regular.ttf"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load the project typeface for every exported text role."""

    try:
        return ImageFont.truetype(str(_SCHOOLBELL), size)
    except OSError:
        return ImageFont.load_default()


def _rgb(color: Color | tuple[int, int, int]) -> tuple[int, int, int]:
    return color.rgb if isinstance(color, Color) else color


def _wrapped_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    words = (text or "").split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for raw_word in words:
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
            candidate = word if not current else f"{current} {word}"
            if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
    if current:
        lines.append(current)
    return lines


def _ellipsis_line(draw: ImageDraw.ImageDraw, line: str, font, max_width: int) -> str:
    suffix = "..."
    candidate = line.rstrip()
    while candidate and draw.textbbox((0, 0), candidate + suffix, font=font)[2] > max_width:
        candidate = candidate[:-1]
    return (candidate + suffix) if candidate else suffix


def _bounded_lines(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> tuple[str, ...]:
    lines = _wrapped_lines(draw, text, font, max_width)
    if len(lines) <= max_lines:
        return tuple(lines)
    kept = lines[:max_lines]
    kept[-1] = _ellipsis_line(draw, kept[-1], font, max_width)
    return tuple(kept)


def _line_height(draw: ImageDraw.ImageDraw, font, spacing: int = 0) -> int:
    box = draw.textbbox((0, 0), "Ag", font=font)
    return max(1, box[3] - box[1]) + spacing


def _block_height(draw: ImageDraw.ImageDraw, lines: Sequence[str], font, spacing: int) -> int:
    return _line_height(draw, font) * max(1, len(lines)) + spacing * max(0, len(lines) - 1)


@dataclass(frozen=True)
class TextLayout:
    """Bounded text lines and their reserved portrait-card boxes."""

    title_lines: tuple[str, ...]
    message_lines: tuple[str, ...]
    title_box: tuple[int, int, int, int]
    message_box: tuple[int, int, int, int]


def _card_fonts(size: tuple[int, int]):
    scale = min(size[0] / 1200, size[1] / 1600)
    return _font(max(28, int(88 * scale))), _font(max(20, int(48 * scale))), _font(max(16, int(30 * scale)))


def calculate_text_layout(size: tuple[int, int], variant: int, title: str, message: str) -> TextLayout:
    """Reserve readable copy above and below the central photo."""

    if size[0] < 300 or size[1] < 400:
        raise ValueError("card size is too small for a readable export")
    draw = ImageDraw.Draw(Image.new("RGB", size, "white"))
    title_font, message_font, _ = _card_fonts(size)
    title = (title or "A little color for you")[:80]
    message = (message or "Made from the colors in your photo.")[:240]
    scale_x, scale_y = size[0] / 1200, size[1] / 1600
    max_width = int(940 * scale_x)
    title_y = int((110, 105, 120)[variant % 3] * scale_y)
    message_y = int((1320, 1315, 1305)[variant % 3] * scale_y)
    title_lines = _bounded_lines(draw, title, title_font, max_width, 2)
    message_lines = _bounded_lines(draw, message, message_font, max_width, 3)
    title_height = _block_height(draw, title_lines, title_font, int(8 * scale_y))
    message_height = _block_height(draw, message_lines, message_font, int(8 * scale_y))
    x = (size[0] - max_width) // 2
    return TextLayout(
        title_lines=title_lines,
        message_lines=message_lines,
        title_box=(x, title_y, x + max_width, title_y + title_height),
        message_box=(x, message_y, x + max_width, message_y + message_height),
    )


def _centered_text(draw: ImageDraw.ImageDraw, box, lines, font, fill, spacing=8):
    draw.multiline_text(
        ((box[0] + box[2]) // 2, box[1]),
        "\n".join(lines), fill=fill, font=font, anchor="ma", align="center", spacing=spacing,
    )


def _photo_window(canvas: Image.Image, image: Image.Image | None, box, fill, outline, radius=48):
    """Contain the unchanged source photo inside a hand-drawn central frame."""

    left, top, right, bottom = map(int, box)
    width, height = right - left, bottom - top
    panel = Image.new("RGB", (width, height), fill)
    if image is not None:
        source = ImageOps.exif_transpose(image).convert("RGB")
        photo = ImageOps.contain(source, (width - 36, height - 36), Image.Resampling.LANCZOS)
        panel.paste(photo, ((width - photo.width) // 2, (height - photo.height) // 2))
    mask = Image.new("L", panel.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    canvas.paste(panel, (left, top), mask)
    ImageDraw.Draw(canvas).rounded_rectangle((left, top, right, bottom), radius=radius, outline=outline, width=max(3, canvas.width // 240))


def _spark(draw: ImageDraw.ImageDraw, center, radius, fill, width=7):
    x, y = center
    draw.line((x - radius, y, x + radius, y), fill=fill, width=width)
    draw.line((x, y - radius, x, y + radius), fill=fill, width=width)


def _star(draw: ImageDraw.ImageDraw, center, radius, fill, outline, points=5):
    """Draw a slightly wonky paper star."""

    x, y = center
    vertices = []
    for index in range(points * 2):
        angle = -math.pi / 2 + index * math.pi / points
        reach = radius if index % 2 == 0 else radius * 0.44
        if index % 3 == 0:
            reach *= 0.92
        vertices.append((x + math.cos(angle) * reach, y + math.sin(angle) * reach))
    draw.polygon(vertices, fill=fill, outline=outline)


def _heart(draw: ImageDraw.ImageDraw, center, radius, fill, outline):
    """Draw a simple cut-paper heart."""

    x, y = center
    draw.ellipse((x - radius, y - radius * .7, x, y + radius * .3), fill=fill, outline=outline, width=3)
    draw.ellipse((x, y - radius * .7, x + radius, y + radius * .3), fill=fill, outline=outline, width=3)
    draw.polygon(((x - radius, y), (x + radius, y), (x, y + radius * 1.25)), fill=fill)
    draw.line(((x - radius, y), (x, y + radius * 1.25), (x + radius, y)), fill=outline, width=3)


def _dashed_line(draw: ImageDraw.ImageDraw, start, end, fill, width=5, dash=18, gap=13):
    """Draw a stitched line between two points."""

    x1, y1 = start
    x2, y2 = end
    distance = math.hypot(x2 - x1, y2 - y1)
    if not distance:
        return
    dx, dy = (x2 - x1) / distance, (y2 - y1) / distance
    position = 0.0
    while position < distance:
        stop = min(distance, position + dash)
        draw.line((x1 + dx * position, y1 + dy * position, x1 + dx * stop, y1 + dy * stop), fill=fill, width=width)
        position += dash + gap


def _petal(draw: ImageDraw.ImageDraw, center, radius, fill, outline, angle=0):
    """Draw one pressed-paper petal as a rotated oval."""

    petal = Image.new("RGBA", (radius * 3, radius * 4), (0, 0, 0, 0))
    petal_draw = ImageDraw.Draw(petal)
    petal_draw.ellipse((radius // 2, radius // 2, radius * 5 // 2, radius * 7 // 2), fill=fill, outline=outline, width=3)
    petal = petal.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True)
    draw._image.paste(petal, (int(center[0] - petal.width / 2), int(center[1] - petal.height / 2)), petal)


def render_card(
    image: Image.Image | None,
    colors: Sequence[Color],
    title: str,
    message: str,
    object_label: str,
    variant: int = 0,
    size: tuple[int, int] = (1200, 1600),
    design_palette=None,
) -> Image.Image:
    """Render one portrait cover with the source photo at its center."""

    if size[0] < 300 or size[1] < 400:
        raise ValueError("card size is too small for a readable export")
    roles = design_palette or derive_palette_roles(colors)
    bg, surface, accent = map(_rgb, (roles.background, roles.surface, roles.accent))
    primary, secondary = map(_rgb, (roles.primary, roles.secondary))
    tints = tuple(map(_rgb, roles.tints))
    ink = _rgb(roles.on_background)
    surface_ink = _rgb(roles.on_surface)
    canvas = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(canvas)
    width, height = size
    title_font, message_font, small_font = _card_fonts(size)
    title = (title or "A little color for you")[:80]
    message = (message or "Made from the colors in your photo.")[:240]
    label = (object_label or "object").replace("_", " ").upper()
    layout = calculate_text_layout(size, variant, title, message)
    scale_x, scale_y = width / 1200, height / 1600

    if variant % 3 == 0:
        # A pressed-flower keepsake with a roomy photo and little paper petals.
        draw.rounded_rectangle((48, 46, width - 48, height - 46), radius=72, fill=surface, outline=ink, width=max(4, width // 180))
        draw.rounded_rectangle((72, 70, width - 72, height - 70), radius=58, outline=tints[2], width=max(3, width // 300))
        photo_box = (210 * scale_x, 385 * scale_y, 990 * scale_x, 1205 * scale_y)
        for x, y, color, turn in ((145, 410, primary, -28), (1060, 450, secondary, 32), (145, 1090, secondary, 18), (1050, 1110, primary, -24)):
            stem_end = (x + (-35 if x > width / 2 else 35), y + 95)
            draw.arc((min(x, stem_end[0]) - 30, y - 5, max(x, stem_end[0]) + 30, stem_end[1] + 35), 165 if x < width / 2 else 15, 300 if x < width / 2 else 150, fill=ink, width=5)
            _petal(draw, (x, y), 25, color, ink, turn)
            _petal(draw, (x + (-25 if x > width / 2 else 25), y + 36), 18, tints[(turn // 12) % len(tints)], ink, turn + 48)
        _photo_window(canvas, image, photo_box, surface, ink, radius=int(66 * scale_x))
        for x, y, color in ((118, 290, accent), (1080, 290, secondary), (102, 1260, primary), (1090, 1260, accent)):
            _spark(draw, (int(x * scale_x), int(y * scale_y)), int(20 * scale_x), color, width=max(4, width // 260))
        draw.text((width // 2, 58 * scale_y), f"A LITTLE {label} KEEPSAKE", fill=accent, font=small_font, anchor="ma")
        _centered_text(draw, layout.title_box, layout.title_lines, title_font, surface_ink)
        _centered_text(draw, layout.message_box, layout.message_lines, message_font, surface_ink)
    elif variant % 3 == 1:
        # A layered scrapbook with torn paper, stitches, tape, and stickers.
        draw.rounded_rectangle((54, 52, width - 54, height - 52), radius=40, fill=surface, outline=ink, width=max(4, width // 200))
        back_one = ((82, 344), (1115, 310), (1130, 1210), (70, 1250))
        back_two = ((120, 370), (1070, 350), (1095, 1235), (100, 1205))
        draw.polygon(back_one, fill=tints[0], outline=ink)
        draw.polygon(back_two, fill=tints[1], outline=ink)
        _dashed_line(draw, (100, 365), (1095, 330), accent, width=5, dash=20, gap=15)
        _dashed_line(draw, (105, 1230), (1100, 1200), accent, width=5, dash=20, gap=15)
        _photo_window(canvas, image, (185 * scale_x, 395 * scale_y, 1015 * scale_x, 1195 * scale_y), surface, ink, radius=int(24 * scale_x))
        tape = (255, 218, 92)
        draw.polygon(((155, 365), (360, 345), (368, 418), (164, 432)), fill=tape, outline=ink)
        draw.polygon(((830, 1165), (1040, 1148), (1035, 1220), (838, 1234)), fill=tape, outline=ink)
        _star(draw, (100, 555), 40, primary, ink)
        _star(draw, (1090, 930), 34, secondary, ink)
        _heart(draw, (1085, 560), 27, accent, ink)
        _heart(draw, (112, 980), 22, primary, ink)
        draw.text((width // 2, 58 * scale_y), f"FOUND COLORS · {label}", fill=accent, font=small_font, anchor="ma")
        _centered_text(draw, layout.title_box, layout.title_lines, title_font, surface_ink)
        _centered_text(draw, layout.message_box, layout.message_lines, message_font, surface_ink)
    else:
        # A cheerful postage card with a perforated photo frame and ink marks.
        draw.rounded_rectangle((42, 40, width - 42, height - 40), radius=86, fill=surface, outline=ink, width=max(4, width // 200))
        draw.rounded_rectangle((70, 68, width - 70, height - 68), radius=68, outline=tints[2], width=max(3, width // 300))
        frame = (160, 365, 1040, 1225)
        draw.rectangle(frame, fill=primary, outline=ink, width=4)
        hole_radius = 12
        for x in range(frame[0] + 24, frame[2], 42):
            draw.ellipse((x - hole_radius, frame[1] - hole_radius, x + hole_radius, frame[1] + hole_radius), fill=surface)
            draw.ellipse((x - hole_radius, frame[3] - hole_radius, x + hole_radius, frame[3] + hole_radius), fill=surface)
        for y in range(frame[1] + 24, frame[3], 42):
            draw.ellipse((frame[0] - hole_radius, y - hole_radius, frame[0] + hole_radius, y + hole_radius), fill=surface)
            draw.ellipse((frame[2] - hole_radius, y - hole_radius, frame[2] + hole_radius, y + hole_radius), fill=surface)
        _photo_window(canvas, image, (205 * scale_x, 410 * scale_y, 995 * scale_x, 1180 * scale_y), bg, ink, radius=int(26 * scale_x))
        draw.text((width // 2, 58 * scale_y), f"POSTED WITH LOVE · {label}", fill=accent, font=small_font, anchor="ma")
        _centered_text(draw, layout.title_box, layout.title_lines, title_font, surface_ink)
        _centered_text(draw, layout.message_box, layout.message_lines, message_font, surface_ink)
        swatch_y = int(1510 * scale_y)
        swatch_radius = max(12, int(20 * scale_x))
        source = tuple(roles.source[:5])
        start = width // 2 - int((len(source) - 1) * 58 * scale_x)
        for index, color in enumerate(source):
            x = start + int(index * 116 * scale_x)
            draw.ellipse((x - swatch_radius, swatch_y - swatch_radius, x + swatch_radius, swatch_y + swatch_radius), fill=_rgb(color), outline=ink, width=3)
        draw.arc((70, 245, 320, 410), 188, 340, fill=primary, width=10)
        draw.arc((width - 320, 250, width - 70, 415), 20, 172, fill=secondary, width=10)
        draw.arc((70, 260, 330, 430), 192, 338, fill=accent, width=4)
        _star(draw, (1075, 1260), 34, secondary, ink)
    return canvas


def render_card_set(
    image: Image.Image | None,
    colors: Sequence[Color],
    title: str,
    message: str,
    object_label: str,
    size: tuple[int, int] = (1200, 1600),
    design_palette=None,
) -> list[Image.Image]:
    """Return the three portrait covers shown in the application."""

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
