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
    scale = min(size[0] / 1275, size[1] / 1650)
    return _font(max(28, int(88 * scale))), _font(max(20, int(48 * scale))), _font(max(16, int(30 * scale)))


def calculate_text_layout(size: tuple[int, int], variant: int, title: str, message: str) -> TextLayout:
    """Reserve readable copy above and below the central photo."""

    if size[0] < 300 or size[1] < 400:
        raise ValueError("card size is too small for a readable export")
    draw = ImageDraw.Draw(Image.new("RGB", size, "white"))
    title_font, message_font, _ = _card_fonts(size)
    title = (title or "For someone wonderful")[:80]
    message = (message or "A tiny note, made just for you.")[:240]
    scale_x, scale_y = size[0] / 1275, size[1] / 1650
    max_width = int(1020 * scale_x)
    title_y = int((105, 100, 110)[variant % 3] * scale_y)
    message_y = int((1435, 1435, 1435)[variant % 3] * scale_y)
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


def _folk_flower(draw: ImageDraw.ImageDraw, center, radius, petal_fill, middle_fill, outline):
    """Draw a bold, flat flower inspired by painted folk motifs."""

    x, y = center
    for angle in range(0, 360, 60):
        px = x + math.cos(math.radians(angle)) * radius * .72
        py = y + math.sin(math.radians(angle)) * radius * .72
        draw.ellipse((px - radius * .42, py - radius * .58, px + radius * .42, py + radius * .58), fill=petal_fill, outline=outline, width=3)
    draw.ellipse((x - radius * .34, y - radius * .34, x + radius * .34, y + radius * .34), fill=middle_fill, outline=outline, width=3)


def _leaf(draw: ImageDraw.ImageDraw, center, radius, fill, outline, turn=0):
    """Draw a simple paper-cut leaf."""

    leaf = Image.new("RGBA", (radius * 4, radius * 3), (0, 0, 0, 0))
    leaf_draw = ImageDraw.Draw(leaf)
    leaf_draw.ellipse((radius // 2, radius // 2, radius * 7 // 2, radius * 5 // 2), fill=fill, outline=outline, width=3)
    leaf_draw.line((radius, radius * 3 // 2, radius * 3, radius * 3 // 2), fill=outline, width=3)
    leaf = leaf.rotate(turn, resample=Image.Resampling.BICUBIC, expand=True)
    draw._image.paste(leaf, (int(center[0] - leaf.width / 2), int(center[1] - leaf.height / 2)), leaf)


def render_card(
    image: Image.Image | None,
    colors: Sequence[Color],
    title: str,
    message: str,
    object_label: str,
    variant: int = 0,
    size: tuple[int, int] = (1275, 1650),
    design_palette=None,
    render_text: bool = True,
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
    title = (title or "For someone wonderful")[:80]
    message = (message or "A tiny note, made just for you.")[:240]
    del object_label
    layout = calculate_text_layout(size, variant, title, message)
    scale_x, scale_y = width / 1275, height / 1650
    line_width = max(4, width // 250)

    def card_copy():
        if not render_text:
            return
        _centered_text(draw, layout.title_box, layout.title_lines, title_font, surface_ink)
        _centered_text(draw, layout.message_box, layout.message_lines, message_font, surface_ink)

    if variant % 3 == 0:
        # Folk garden: one symmetrical botanical motif and a calm central photo.
        draw.rectangle((0, 0, width - 1, height - 1), fill=surface)
        draw.rectangle((30, 30, width - 31, height - 31), outline=ink, width=line_width)
        draw.rectangle((48, 48, width - 49, height - 49), outline=tints[2], width=max(2, line_width - 1))
        _photo_window(canvas, image, (245 * scale_x, 370 * scale_y, 1030 * scale_x, 1360 * scale_y), surface, ink, radius=int(10 * scale_x))
        for x, y, petals, middle in ((140, 420, primary, accent), (1135, 420, secondary, primary), (140, 1275, secondary, accent), (1135, 1275, primary, secondary)):
            _folk_flower(draw, (int(x * scale_x), int(y * scale_y)), int(42 * scale_x), petals, middle, ink)
        for x, y, turn, color in ((125, 545, -35, tints[0]), (1150, 550, 35, tints[1]), (128, 1150, 30, tints[1]), (1148, 1150, -30, tints[0])):
            _leaf(draw, (int(x * scale_x), int(y * scale_y)), int(22 * scale_x), color, ink, turn)
        _heart(draw, (width // 2, int(332 * scale_y)), int(22 * scale_x), accent, ink)
        card_copy()
    elif variant % 3 == 1:
        # Cut-paper party: broad shapes, a strong arch, and restrained confetti.
        draw.rectangle((0, 0, width - 1, height - 1), fill=surface)
        draw.rectangle((28, 28, width - 29, height - 29), outline=ink, width=line_width)
        draw.polygon(((70, 355), (width - 70, 320), (width - 95, 1390), (95, 1360)), fill=tints[0], outline=ink)
        draw.polygon(((115, 385), (width - 95, 365), (width - 120, 1360), (105, 1390)), fill=tints[1], outline=ink)
        draw.arc((205, 255, width - 205, 900), 180, 360, fill=primary, width=int(70 * scale_x))
        _photo_window(canvas, image, (220 * scale_x, 405 * scale_y, 1055 * scale_x, 1350 * scale_y), surface, ink, radius=int(8 * scale_x))
        for x, y, color, turn in ((95, 500, accent, -14), (1160, 555, secondary, 16), (105, 1050, primary, 12), (1160, 1125, accent, -12)):
            draw.rounded_rectangle((x - 25, y - 52, x + 25, y + 52), radius=8, fill=color, outline=ink, width=3)
        _star(draw, (115, 735), 33, primary, ink)
        _heart(draw, (1150, 820), 24, accent, ink)
        _spark(draw, (width // 2, int(330 * scale_y)), int(18 * scale_x), secondary, width=5)
        card_copy()
    else:
        # Playful postcard: graphic stamp edges and a few loose cancellation marks.
        draw.rectangle((0, 0, width - 1, height - 1), fill=surface)
        draw.rectangle((28, 28, width - 29, height - 29), outline=ink, width=line_width)
        draw.rectangle((48, 48, width - 49, height - 49), outline=tints[2], width=max(2, line_width - 1))
        frame = (170, 360, 1105, 1380)
        draw.rectangle(frame, fill=primary, outline=ink, width=4)
        hole_radius = 12
        for x in range(frame[0] + 24, frame[2], 42):
            draw.ellipse((x - hole_radius, frame[1] - hole_radius, x + hole_radius, frame[1] + hole_radius), fill=surface)
            draw.ellipse((x - hole_radius, frame[3] - hole_radius, x + hole_radius, frame[3] + hole_radius), fill=surface)
        for y in range(frame[1] + 24, frame[3], 42):
            draw.ellipse((frame[0] - hole_radius, y - hole_radius, frame[0] + hole_radius, y + hole_radius), fill=surface)
            draw.ellipse((frame[2] - hole_radius, y - hole_radius, frame[2] + hole_radius, y + hole_radius), fill=surface)
        _photo_window(canvas, image, (220 * scale_x, 410 * scale_y, 1055 * scale_x, 1330 * scale_y), bg, ink, radius=int(8 * scale_x))
        draw.arc((55, 245, 345, 430), 188, 342, fill=primary, width=10)
        draw.arc((70, 267, 360, 450), 192, 338, fill=accent, width=4)
        draw.arc((width - 360, 255, width - 55, 445), 18, 170, fill=secondary, width=10)
        _heart(draw, (int(1115 * scale_x), int(315 * scale_y)), int(28 * scale_x), accent, ink)
        _star(draw, (int(135 * scale_x), int(1410 * scale_y)), int(28 * scale_x), secondary, ink)
        card_copy()
    return canvas


def render_card_set(
    image: Image.Image | None,
    colors: Sequence[Color],
    title: str,
    message: str,
    object_label: str,
    size: tuple[int, int] = (1275, 1650),
    design_palette=None,
    render_text: bool = True,
) -> list[Image.Image]:
    """Return the three portrait covers shown in the application."""

    return [render_card(image, colors, title, message, object_label, variant=i, size=size, design_palette=design_palette, render_text=render_text) for i in range(3)]


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
