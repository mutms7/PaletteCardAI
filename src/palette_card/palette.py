"""Deterministic color extraction and accessible palette design.

The neural network recognizes the object. Everything in this module is
ordinary, explainable image processing: pixels become colors, and colors are
assigned design roles. That separation makes the project easier to learn and
more predictable than asking a model to invent colors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class Color:
    """An RGB color with a friendly hex representation."""

    rgb: tuple[int, int, int]
    proportion: float = 1.0

    @property
    def hex(self) -> str:
        return "#%02X%02X%02X" % self.rgb


@dataclass(frozen=True)
class PaletteRoles:
    background: Color
    surface: Color
    accent: Color
    secondary: Color
    text: Color

    def as_dict(self) -> dict[str, str]:
        return {
            "background": self.background.hex,
            "surface": self.surface.hex,
            "accent": self.accent.hex,
            "secondary": self.secondary.hex,
            "text": self.text.hex,
        }


def _composite_rgb(image: Image.Image, background=(255, 255, 255)) -> Image.Image:
    """Normalize transparency, palette mode, grayscale, and unusual modes."""

    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (*background, 255))
        return Image.alpha_composite(bg, rgba).convert("RGB")
    return image.convert("RGB")


def _rgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convert an array of RGB values in [0, 255] to CIE Lab."""

    values = np.asarray(rgb, dtype=np.float64) / 255.0
    values = np.where(values <= 0.04045, values / 12.92, ((values + 0.055) / 1.055) ** 2.4)
    matrix = np.array(
        [[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]],
        dtype=np.float64,
    )
    xyz = values @ matrix.T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    delta = 6 / 29
    f = np.where(xyz > delta**3, np.cbrt(np.maximum(xyz, 0)), xyz / (3 * delta**2) + 4 / 29)
    return np.column_stack((116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])))


def _lab_to_rgb(lab: np.ndarray) -> np.ndarray:
    """Convert CIE Lab values to clipped uint8 RGB values."""

    values = np.asarray(lab, dtype=np.float64)
    fy = (values[:, 0] + 16) / 116
    fx = values[:, 1] / 500 + fy
    fz = fy - values[:, 2] / 200
    delta = 6 / 29
    xyz = np.stack(
        [np.where(fx > delta, fx**3, 3 * delta**2 * (fx - 4 / 29)), np.where(fy > delta, fy**3, 3 * delta**2 * (fy - 4 / 29)), np.where(fz > delta, fz**3, 3 * delta**2 * (fz - 4 / 29))],
        axis=1,
    )
    xyz *= np.array([0.95047, 1.0, 1.08883])
    matrix = np.array([[3.2404542, -1.5371385, -0.4985314], [-0.9692660, 1.8760108, 0.0415560], [0.0556434, -0.2040259, 1.0572252]])
    linear = xyz @ matrix.T
    rgb = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * np.maximum(linear, 0) ** (1 / 2.4) - 0.055)
    return np.clip(np.rint(rgb * 255), 0, 255).astype(np.uint8)


def _pad_colors(colors: list[Color], count: int) -> list[Color]:
    """Guarantee a requested count, including for a completely uniform photo."""

    if not colors:
        colors = [Color((128, 128, 128))]
    base = colors[0].rgb
    while len(colors) < count:
        index = len(colors)
        factor = 0.65 + 0.18 * (index % 3)
        if index % 2:
            rgb = tuple(int(round(channel * factor + 255 * (1 - factor))) for channel in base)
        else:
            rgb = tuple(int(round(channel * factor)) for channel in base)
        colors.append(Color(tuple(max(0, min(255, c)) for c in rgb), 0.0))
    return colors[:count]


def extract_palette(image: Image.Image, n_colors: int = 5, sample_size: int = 20000, random_state: int = 42) -> list[Color]:
    """Extract exactly ``n_colors`` representative colors from an image.

    A resized deterministic sample is clustered in Lab space when
    scikit-learn is available. Tiny, grayscale, transparent, and uniform
    images are normalized and padded with deliberate shade variants.
    """

    if n_colors < 1:
        raise ValueError("n_colors must be at least 1")
    if image is None:
        raise ValueError("An image is required")
    normalized = _composite_rgb(image)
    normalized.thumbnail((160, 160), Image.Resampling.LANCZOS)
    pixels = np.asarray(normalized, dtype=np.uint8).reshape(-1, 3)
    if len(pixels) == 0:
        return _pad_colors([], n_colors)
    if len(pixels) > sample_size:
        rng = np.random.default_rng(random_state)
        pixels = pixels[rng.choice(len(pixels), size=sample_size, replace=False)]
    unique, counts = np.unique(pixels, axis=0, return_counts=True)
    k = min(n_colors, len(unique))
    if k == 1:
        colors = [Color(tuple(int(v) for v in unique[0]), 1.0)]
        return _pad_colors(colors, n_colors)
    lab = _rgb_to_lab(pixels)
    try:
        from sklearn.cluster import KMeans

        model = KMeans(n_clusters=k, n_init=5, random_state=random_state)
        labels = model.fit_predict(lab)
        centers = _lab_to_rgb(model.cluster_centers_)
        proportions = np.bincount(labels, minlength=k) / len(labels)
        order = np.argsort(-proportions)
        colors = [Color(tuple(int(v) for v in centers[i]), float(proportions[i])) for i in order]
    except Exception:
        # A deterministic fallback keeps the palette feature usable when the
        # optional clustering dependency is unavailable or fails on a corner case.
        order = np.argsort(-counts)[:k]
        colors = [Color(tuple(int(v) for v in unique[i]), float(counts[i] / len(pixels))) for i in order]
    return _pad_colors(colors, n_colors)


def _relative_luminance(color: Color | Sequence[int]) -> float:
    rgb = color.rgb if isinstance(color, Color) else tuple(color)
    channels = np.asarray(rgb, dtype=float) / 255
    channels = np.where(channels <= 0.03928, channels / 12.92, ((channels + 0.055) / 1.055) ** 2.4)
    return float(0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2])


def contrast_ratio(first: Color | Sequence[int], second: Color | Sequence[int]) -> float:
    """Return WCAG contrast ratio, where 1 is no contrast and 21 is maximum."""

    one, two = _relative_luminance(first), _relative_luminance(second)
    light, dark = max(one, two), min(one, two)
    return (light + 0.05) / (dark + 0.05)


def choose_text_color(background: Color, minimum_ratio: float = 4.5) -> Color:
    """Choose black or white, preferring the option meeting the ratio."""

    black, white = Color((20, 20, 24)), Color((255, 255, 255))
    black_ratio, white_ratio = contrast_ratio(background, black), contrast_ratio(background, white)
    if black_ratio >= minimum_ratio and black_ratio >= white_ratio:
        return black
    if white_ratio >= minimum_ratio:
        return white
    return black if black_ratio >= white_ratio else white


def text_color_for_background(background: Color | Sequence[int], minimum_ratio: float = 4.5) -> Color:
    """Return a black/white text color meeting contrast on one actual fill.

    Card layouts call this helper for each surface they draw text over. A
    single palette-wide ``text`` role cannot be assumed to contrast with every
    accent, panel, or photo background.
    """

    color = background if isinstance(background, Color) else Color(tuple(int(channel) for channel in background))
    return choose_text_color(color, minimum_ratio=minimum_ratio)


def text_and_background_for_contrast(background: Color | Sequence[int], minimum_ratio: float = 4.5) -> tuple[Color, Color]:
    """Return a minimally adjusted fill and black/white text with safe contrast.

    Some saturated mid-tone fills cannot reach 4.5:1 with either pure black
    or white while preserving the exact fill. In that rare case the fill is
    nudged toward black or white; the returned pair is what a card should
    actually draw and label.
    """

    original = background if isinstance(background, Color) else Color(tuple(int(channel) for channel in background))
    direct_text = choose_text_color(original, minimum_ratio=minimum_ratio)
    if contrast_ratio(original, direct_text) >= minimum_ratio:
        return original, direct_text
    rgb = np.asarray(original.rgb, dtype=float)
    candidates: list[tuple[float, Color, Color]] = []
    for factor in np.linspace(0.0, 1.0, 201):
        for candidate_rgb in (rgb * factor, 255 - (255 - rgb) * factor):
            candidate = Color(tuple(int(np.clip(round(channel), 0, 255)) for channel in candidate_rgb))
            text = choose_text_color(candidate, minimum_ratio=minimum_ratio)
            if contrast_ratio(candidate, text) >= minimum_ratio:
                distance = float(np.abs(candidate_rgb - rgb).sum())
                candidates.append((distance, candidate, text))
    if not candidates:
        raise ValueError(f"Could not create a readable fill for {original.hex}")
    _, safe_background, safe_text = min(candidates, key=lambda item: item[0])
    return safe_background, safe_text


def derive_palette_roles(colors: Sequence[Color]) -> DesignPalette:
    """Return the researched design palette (legacy name retained).

    ``extract_palette`` remains the source-color operation.  Role assignment
    is delegated to :mod:`palette_card.design`, where OKLCH transformations,
    semantic on-colors, harmony rationale, and the explainable quality
    heuristic live.  The returned object keeps ``background``, ``surface``,
    ``accent``, ``secondary``, ``text``, and ``as_dict`` compatibility with
    the first version of PaletteCard.
    """

    from .design import derive_design_palette

    return derive_design_palette(colors)


# Public conversion aliases keep the original extraction module convenient
# for tutorials while the implementation remains in the dedicated design
# module.  Importing here avoids a module-level cycle through Color.
from .design import (  # noqa: E402  (intentionally after Color is defined)
    DesignPalette,
    DesignQuality,
    derive_design_palette,
    oklab_to_oklch,
    oklab_to_srgb,
    oklch_to_oklab,
    oklch_to_srgb,
    rgb_to_oklab,
    rgb_to_oklch,
    srgb_to_oklab,
    srgb_to_oklch,
)
