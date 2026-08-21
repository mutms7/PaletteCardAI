"""Explainable, perceptually-aware card color design.

This module deliberately has no model or learned weights.  ``palette.py``
extracts the *source palette* (colors that are actually observed in a photo),
then this module turns those observations into a separate *design palette*.
The latter assigns semantic roles to colors and applies conservative color
theory rules so that a photograph with many strong colors still gets calm
negative space, a readable hierarchy, and only a small number of accents.

The conversion code follows Björn Ottosson's Oklab matrices and the W3C CSS
Color 4 guidance: hue and lightness are held while chroma is reduced when an
OKLCH color would leave the sRGB gamut.  It is intentionally small and
dependency-free apart from NumPy (already required by PaletteCard).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np


RGB = tuple[int, int, int]
Oklab = tuple[float, float, float]
Oklch = tuple[float, float, float]


# Björn Ottosson's Oklab matrices, with linear-light sRGB as the endpoint.
_M1 = np.array(
    [[0.4122214708, 0.5363325363, 0.0514459929],
     [0.2119034982, 0.6806995451, 0.1073969566],
     [0.0883024619, 0.2817188376, 0.6299787005]],
    dtype=float,
)
_M2 = np.array(
    [[0.2104542553, 0.7936177850, -0.0040720468],
     [1.9779984951, -2.4285922050, 0.4505937099],
     [0.0259040371, 0.7827717662, -0.8086757660]],
    dtype=float,
)
_M1_INV = np.array(
    [[4.0767416621, -3.3077115913, 0.2309699292],
     [-1.2684380046, 2.6097574011, -0.3413193965],
     [-0.0041960863, -0.7034186147, 1.7076147010]],
    dtype=float,
)
_M2_INV = np.array(
    [[1.0, 0.3963377774, 0.2158037573],
     [1.0, -0.1055613458, -0.0638541728],
     [1.0, -0.0894841775, -1.2914855480]],
    dtype=float,
)


def _srgb_to_linear(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, dtype=float)
    return np.where(value <= 0.04045, value / 12.92, ((value + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, dtype=float)
    positive = np.maximum(value, 0.0)
    return np.where(value <= 0.0031308, 12.92 * value, 1.055 * positive ** (1.0 / 2.4) - 0.055)


def _rgb_tuple(rgb: Sequence[int] | object) -> RGB:
    values = getattr(rgb, "rgb", rgb)
    if len(values) != 3:
        raise ValueError("RGB values must have exactly three channels")
    return tuple(int(np.clip(round(float(channel)), 0, 255)) for channel in values)  # type: ignore[return-value]


def srgb_to_oklab(rgb: Sequence[int] | object) -> Oklab:
    """Convert an 8-bit sRGB triplet to Oklab (L in 0..1)."""

    values = _srgb_to_linear(np.asarray(_rgb_tuple(rgb), dtype=float) / 255.0)
    lms = _M1 @ values
    lms_cbrt = np.cbrt(lms)
    lab = _M2 @ lms_cbrt
    return tuple(float(v) for v in lab)  # type: ignore[return-value]


def oklab_to_srgb(lab: Sequence[float], *, gamut_map: bool = True) -> RGB:
    """Convert Oklab to 8-bit sRGB, optionally clipping at the endpoint.

    Use :func:`oklch_to_srgb` when preserving hue/lightness while reducing
    chroma is important.  This rectangular conversion is useful for round
    trips and returns a displayable triplet for all finite inputs.
    """

    if len(lab) != 3:
        raise ValueError("Oklab values must have exactly three channels")
    lms_cbrt = _M2_INV @ np.asarray(tuple(float(v) for v in lab), dtype=float)
    lms = lms_cbrt ** 3
    linear = _M1_INV @ lms
    if gamut_map:
        linear = np.clip(linear, 0.0, 1.0)
    encoded = _linear_to_srgb(linear)
    return tuple(int(np.clip(round(float(v) * 255.0), 0, 255)) for v in encoded)  # type: ignore[return-value]


def oklab_to_oklch(lab: Sequence[float]) -> Oklch:
    """Convert rectangular Oklab to cylindrical OKLCH."""

    if len(lab) != 3:
        raise ValueError("Oklab values must have exactly three channels")
    lightness, a, b = (float(v) for v in lab)
    chroma = math.hypot(a, b)
    hue = (math.degrees(math.atan2(b, a)) % 360.0) if chroma > 1e-12 else 0.0
    return lightness, chroma, hue


def oklch_to_oklab(lch: Sequence[float]) -> Oklab:
    """Convert cylindrical OKLCH to rectangular Oklab."""

    if len(lch) != 3:
        raise ValueError("OKLCH values must have exactly three channels")
    lightness, chroma, hue = (float(v) for v in lch)
    radians = math.radians(hue % 360.0)
    return lightness, chroma * math.cos(radians), chroma * math.sin(radians)


def srgb_to_oklch(rgb: Sequence[int] | object) -> Oklch:
    return oklab_to_oklch(srgb_to_oklab(rgb))


def _in_srgb_gamut(lch: Oklch) -> bool:
    lab = oklch_to_oklab(lch)
    if not (0.0 <= lab[0] <= 1.0):
        return False
    values = _M1_INV @ ((_M2_INV @ np.asarray(lab, dtype=float)) ** 3)
    return bool(np.all(values >= -1e-7) and np.all(values <= 1.0 + 1e-7))


def oklch_to_srgb(lch: Sequence[float], *, gamut_map: bool = True) -> RGB:
    """Convert OKLCH to sRGB with constant-hue chroma gamut reduction.

    The binary search is the constant-lightness/constant-hue approach
    described by CSS Color 4 §14.1.3.  It avoids the hue shifts introduced by
    naively clipping RGB channels for vivid source colors.
    """

    if len(lch) != 3:
        raise ValueError("OKLCH values must have exactly three channels")
    lightness, chroma, hue = (float(v) for v in lch)
    lightness = float(np.clip(lightness, 0.0, 1.0))
    chroma = max(0.0, chroma)
    if not gamut_map or _in_srgb_gamut((lightness, chroma, hue)):
        return oklab_to_srgb(oklch_to_oklab((lightness, chroma, hue)))
    if lightness <= 0.0:
        return (0, 0, 0)
    if lightness >= 1.0:
        return (255, 255, 255)
    low, high = 0.0, chroma
    # 32 iterations is well below 1/255 channel precision and deterministic.
    for _ in range(32):
        middle = (low + high) / 2.0
        if _in_srgb_gamut((lightness, middle, hue)):
            low = middle
        else:
            high = middle
    return oklab_to_srgb(oklch_to_oklab((lightness, low, hue)))


# Friendly aliases commonly used in examples and tests.
rgb_to_oklab = srgb_to_oklab
oklab_to_rgb = oklab_to_srgb
rgb_to_oklch = srgb_to_oklch
oklch_to_rgb = oklch_to_srgb


@dataclass(frozen=True)
class DesignQuality:
    """A transparent sanity heuristic, not a claim of objective beauty."""

    score: float
    contrast_pass: bool
    contrast_ratio_min: float
    large_surface_neutrality: float
    accent_restraint: float
    perceptual_separation: float
    source_relationship: float
    # These describe the colors actually used by the card templates, rather
    # than merely counting how many hues happened to be in the photograph.
    strong_accent_count: int = 0
    strong_accent_area: float = 0.0

    @property
    def breakdown(self) -> dict[str, float | bool]:
        return {
            "contrast_pass": self.contrast_pass,
            "contrast_ratio_min": round(self.contrast_ratio_min, 2),
            "large_surface_neutrality": round(self.large_surface_neutrality, 2),
            "accent_restraint": round(self.accent_restraint, 2),
            "perceptual_separation": round(self.perceptual_separation, 2),
            "source_relationship": round(self.source_relationship, 2),
            "strong_accent_count": self.strong_accent_count,
            "strong_accent_area": round(self.strong_accent_area, 4),
            "score": round(self.score, 1),
        }


@dataclass(frozen=True)
class DesignPalette:
    """Semantic colors derived from, but intentionally distinct from, source colors."""

    source: tuple[object, ...]
    background: object
    surface: object
    primary: object
    secondary: object
    accent: object
    on_background: object
    on_surface: object
    on_primary: object
    on_secondary: object
    on_accent: object
    tints: tuple[object, ...]
    harmony: str
    rationale: str
    quality: DesignQuality
    dark: bool = False

    # Compatibility with the original five-role API.  ``text`` is the safe
    # text color for the main surface, not a universal color for every fill.
    @property
    def text(self) -> object:
        return self.on_surface

    @property
    def source_colors(self) -> tuple[object, ...]:
        return self.source

    @property
    def abstract_tints(self) -> tuple[object, ...]:
        return self.tints

    def as_dict(self) -> dict[str, str]:
        def hx(value: object) -> str:
            return str(getattr(value, "hex", value))

        return {
            "background": hx(self.background),
            "surface": hx(self.surface),
            "primary": hx(self.primary),
            "secondary": hx(self.secondary),
            "accent": hx(self.accent),
            "on_background": hx(self.on_background),
            "on_surface": hx(self.on_surface),
            "on_primary": hx(self.on_primary),
            "on_secondary": hx(self.on_secondary),
            "on_accent": hx(self.on_accent),
            "tints": ", ".join(hx(tint) for tint in self.tints),
            "harmony": self.harmony,
        }


def _make_color(rgb: RGB, proportion: float = 0.0) -> object:
    # Import lazily so palette.py can re-export the design function without a
    # module import cycle.
    from .palette import Color

    return Color(rgb, proportion)


def _rgb(value: object) -> RGB:
    return _rgb_tuple(value)


def _relative_luminance(value: object) -> float:
    rgb = np.asarray(_rgb(value), dtype=float) / 255.0
    channels = np.where(rgb <= 0.03928, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    return float(0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2])


def _contrast(first: object, second: object) -> float:
    one, two = _relative_luminance(first), _relative_luminance(second)
    return (max(one, two) + 0.05) / (min(one, two) + 0.05)


def _choose_on(fill: object) -> object:
    black, white = _make_color((24, 24, 28)), _make_color((255, 255, 255))
    return black if _contrast(fill, black) >= _contrast(fill, white) else white


def _safe_pair(fill: object, minimum: float = 4.5) -> tuple[object, object]:
    """Return a fill/on-fill pair meeting WCAG by moving L if necessary."""

    on = _choose_on(fill)
    if _contrast(fill, on) >= minimum:
        return fill, on
    lightness, chroma, hue = srgb_to_oklch(fill)
    # A semantic accent may be used as a text-bearing container in a future
    # template, so make the role itself safe even if it is not text-bearing in
    # the current layouts.
    target = 0.38 if _contrast(fill, _make_color((255, 255, 255))) >= _contrast(fill, _make_color((24, 24, 28))) else 0.78
    candidate = _make_color(oklch_to_srgb((target, min(chroma, 0.16), hue)))
    on = _choose_on(candidate)
    if _contrast(candidate, on) < minimum:
        candidate = _make_color((52, 52, 58)) if _contrast(_make_color((52, 52, 58)), _make_color((255, 255, 255))) >= minimum else _make_color((240, 240, 244))
        on = _choose_on(candidate)
    return candidate, on


def _hue_distance(first: float, second: float) -> float:
    distance = abs((first - second) % 360.0)
    return min(distance, 360.0 - distance)


def _unique_source_colors(colors: Sequence[object]) -> list[object]:
    """Keep chromatic source colors with perceptually distinct hues."""

    candidates: list[tuple[float, float, object, Oklch]] = []
    for color in colors:
        lch = srgb_to_oklch(color)
        if lch[1] < 0.022:  # near-neutral pixels cannot make useful accents
            continue
        proportion = float(getattr(color, "proportion", 0.0))
        # Chroma has priority; proportion only breaks close ties.
        candidates.append((lch[1], proportion, color, lch))
    candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
    selected: list[object] = []
    selected_hues: list[float] = []
    for _, _, color, lch in candidates:
        if all(_hue_distance(lch[2], hue) >= 24.0 for hue in selected_hues):
            selected.append(color)
            selected_hues.append(lch[2])
        if len(selected) >= 4:
            break
    return selected


def _harmony(colors: Sequence[object]) -> str:
    chromatic = [srgb_to_oklch(c) for c in colors if srgb_to_oklch(c)[1] >= 0.022]
    if len(chromatic) <= 1:
        return "monochromatic tonal"
    hues = [entry[2] for entry in chromatic]
    distances = [_hue_distance(hues[0], hue) for hue in hues[1:]]
    if max(distances, default=0.0) <= 55.0:
        return "analogous"
    if len(hues) == 2 and 145.0 <= distances[0] <= 215.0:
        return "complementary-ish"
    return "multicolor neutral-support"


def _source_relationship(source: Sequence[object], derived: Sequence[object]) -> float:
    """Measure hue/chroma fidelity of derived roles against observed colors."""

    if not source or not derived:
        return 0.0
    source_lch = [srgb_to_oklch(c) for c in source]
    derived_lch = [srgb_to_oklch(c) for c in derived]
    chromatic_source = [entry for entry in source_lch if entry[1] >= 0.001]
    if not chromatic_source:
        # A truly neutral photograph should produce neutral derived roles. A
        # synthetic vivid accent would therefore score poorly here.
        mean_chroma = float(np.mean([entry[1] for entry in derived_lch]))
        return float(np.clip(1.0 - mean_chroma / 0.03, 0.0, 1.0))

    role_scores: list[float] = []
    for lightness, chroma, hue in derived_lch:
        best = 0.0
        for source_lightness, source_chroma, source_hue in chromatic_source:
            hue_score = max(0.0, 1.0 - _hue_distance(hue, source_hue) / 60.0)
            if chroma <= 1e-8 or source_chroma <= 1e-8:
                chroma_score = 0.0
            else:
                # Roles intentionally mute chroma, so ratio is symmetric:
                # both an unexpected neon boost and an unrelated neutral are
                # penalized without demanding exact source saturation.
                chroma_score = min(chroma / source_chroma, source_chroma / chroma)
                chroma_score = float(np.clip(chroma_score, 0.0, 1.0))
            best = max(best, 0.65 * hue_score + 0.35 * chroma_score)
        role_scores.append(best)
    return float(np.clip(np.mean(role_scores), 0.0, 1.0))


def _rendered_accent_metrics(roles: Sequence[object]) -> tuple[int, float]:
    """Estimate strong accent count and area from the three actual templates.

    Variant one uses a narrow primary rule twice (roughly 1% of its canvas);
    the second variant uses tiny primary/secondary dots.  Tints and source
    swatches are intentionally excluded because they are not strong design
    accents.  This keeps the quality score tied to rendered usage, not source
    hue count.
    """

    # (role, normalized maximum area across the templates where it is drawn)
    usages = ((roles[0], 0.010), (roles[1], 0.003), (roles[2], 0.0))
    strong: list[tuple[float, object]] = []
    for color, area in usages:
        lightness, chroma, hue = srgb_to_oklch(color)
        if chroma < 0.08:
            continue
        if any(_hue_distance(hue, prior_hue) < 24.0 for prior_hue, _ in strong):
            continue
        strong.append((hue, color))
    area = sum(area for color, area in usages if any(color is selected for _, selected in strong))
    return len(strong), float(area)


def _separation(selected: Sequence[object]) -> float:
    if len(selected) < 2:
        return 0.55
    distances: list[float] = []
    for index, first in enumerate(selected):
        first_lab = np.asarray(srgb_to_oklab(first))
        for second in selected[index + 1:]:
            distances.append(float(np.linalg.norm(first_lab - np.asarray(srgb_to_oklab(second)))))
    # Distances around .12 are distinguishable without requiring neon colors.
    return float(np.clip(np.mean(distances) / 0.18, 0.0, 1.0))


def derive_design_palette(colors: Sequence[object], *, dark: bool = False) -> DesignPalette:
    """Derive semantic design roles from a source palette.

    Large surfaces are deliberately low-chroma neutrals.  Up to two source
    hues become stronger accents; all other decorative regions are light,
    desaturated OKLCH derivatives.  The result is deterministic for a given
    source sequence.
    """

    if not colors:
        colors = (_make_color((128, 128, 128), 1.0),)
    source = tuple(colors)
    selected = _unique_source_colors(source)
    if not selected:
        # A muted photo stays muted. Use its most chromatic observed color as
        # the hue/chroma anchor (rather than introducing a synthetic slate),
        # which guarantees that decorative tints cannot exceed the source.
        selected = [max(source, key=lambda color: srgb_to_oklch(color)[1])]
    primary_source = selected[0]
    secondary_source = selected[1] if len(selected) > 1 else selected[0]
    primary_lch = srgb_to_oklch(primary_source)
    secondary_lch = srgb_to_oklch(secondary_source)

    if dark:
        background = _make_color(oklch_to_srgb((0.19, 0.012, 85.0)))
        surface = _make_color(oklch_to_srgb((0.25, 0.014, 85.0)))
    else:
        # Warm ivory is a gentle default for multicolor photographs.  Chroma
        # is capped below .02, so no extracted vivid color becomes a canvas.
        background = _make_color(oklch_to_srgb((0.975, 0.008, 85.0)))
        surface = _make_color(oklch_to_srgb((0.995, 0.004, 85.0)))

    # Cap accent chroma. The role keeps the source hue and relationship while
    # remaining suitable for a small, deliberate graphic accent.
    primary_target = (float(np.clip(primary_lch[0], 0.44, 0.64)), min(primary_lch[1] * 0.82, 0.18), primary_lch[2])
    secondary_target = (float(np.clip(secondary_lch[0], 0.48, 0.70)), min(secondary_lch[1] * 0.68, 0.14), secondary_lch[2])
    primary = _make_color(oklch_to_srgb(primary_target), float(getattr(primary_source, "proportion", 0.0)))
    secondary = _make_color(oklch_to_srgb(secondary_target), float(getattr(secondary_source, "proportion", 0.0)))
    accent = primary

    # Light tints carry source hues into abstract areas without competing with
    # the photo. Chroma is explicitly relative to each source (and capped at
    # pastel levels), so a muted photo can never acquire a neon decoration.
    primary_tint_chroma = min(primary_lch[1] * 0.22, 0.06)
    secondary_tint_chroma = min(secondary_lch[1] * 0.18, 0.05)
    primary_tint_lightness = min(0.98, max(0.80, primary_lch[0] + 0.15))
    secondary_tint_lightness = min(0.98, max(0.82, secondary_lch[0] + 0.14))
    tint_specs = (
        (primary_tint_lightness, primary_tint_chroma, primary_lch[2]),
        (secondary_tint_lightness, secondary_tint_chroma, secondary_lch[2]),
        (min(0.99, primary_tint_lightness + 0.06), primary_tint_chroma * 0.65, primary_lch[2]),
        (min(0.99, secondary_tint_lightness + 0.05), secondary_tint_chroma * 0.65, secondary_lch[2]),
    )
    tints = tuple(_make_color(oklch_to_srgb(spec)) for spec in tint_specs)

    background, on_background = _safe_pair(background)
    surface, on_surface = _safe_pair(surface)
    primary, on_primary = _safe_pair(primary)
    secondary, on_secondary = _safe_pair(secondary)
    accent, on_accent = _safe_pair(accent)

    pairs = ((background, on_background), (surface, on_surface), (primary, on_primary), (secondary, on_secondary), (accent, on_accent))
    ratios = [_contrast(fill, text) for fill, text in pairs]
    surface_chroma = (srgb_to_oklch(background)[1] + srgb_to_oklch(surface)[1]) / 2.0
    neutrality = float(np.clip(1.0 - surface_chroma / 0.025, 0.0, 1.0))
    strong_accent_count, strong_accent_area = _rendered_accent_metrics((primary, secondary, accent))
    count_score = float(np.clip(1.0 - max(0, strong_accent_count - 2) * 0.35, 0.0, 1.0))
    area_score = float(np.clip(1.0 - strong_accent_area / 0.04, 0.0, 1.0))
    accent_restraint = 0.75 * count_score + 0.25 * area_score
    separation = _separation(selected[:2])
    relationship = _source_relationship(source, (primary, secondary))
    contrast_pass = min(ratios) >= 4.5
    score = 100.0 * (0.35 * float(contrast_pass) + 0.25 * neutrality + 0.20 * accent_restraint + 0.10 * separation + 0.10 * relationship)
    quality = DesignQuality(score, contrast_pass, min(ratios), neutrality, accent_restraint, separation, relationship, strong_accent_count, strong_accent_area)
    harmony = _harmony(source)
    rationale = (
        f"{harmony.title()}: large areas use a {'dark neutral' if dark else 'warm near-white'} canvas and surface; "
        f"{min(2, len(selected))} separated source hue{'s' if min(2, len(selected)) != 1 else ''} become restrained accents, "
        "with lighter OKLCH tints for abstract detail and WCAG-safe on-colors."
    )
    return DesignPalette(source, background, surface, primary, secondary, accent, on_background, on_surface, on_primary, on_secondary, on_accent, tints, harmony, rationale, quality, dark)


# Names that read naturally in the rest of the project and in tutorials.
create_design_palette = derive_design_palette
design_palette = derive_design_palette


__all__ = [
    "DesignPalette", "DesignQuality", "derive_design_palette", "create_design_palette",
    "srgb_to_oklab", "oklab_to_srgb", "oklab_to_oklch", "oklch_to_oklab",
    "srgb_to_oklch", "oklch_to_srgb", "rgb_to_oklab", "oklab_to_rgb",
    "rgb_to_oklch", "oklch_to_rgb",
]
