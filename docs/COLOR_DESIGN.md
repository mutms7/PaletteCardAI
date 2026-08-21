# PaletteCard color design notes

PaletteCard has two different palettes:

* **Source palette** — five representative colors observed in the uploaded
  image. These are useful evidence about the photo, but they are not
  automatically good canvas colors.
* **Design palette** — semantic roles assigned to the card: background,
  surface, primary, secondary, accent, on_background, on_surface,
  on_primary, on_secondary, and on_accent, plus lighter abstract tints. The
  renderer uses these roles, not a dominant-pixel color, to build a hierarchy.

This is an explainable deterministic color-design layer. The object
classifier is the machine-learning component; the current design intelligence
is color science and rules, not a trained model. An optional future project
could collect human preference rankings and train a palette/layout ranker,
but a quality score in the current app is only a sanity heuristic and does
not claim objective beauty.

## Rules used by Auto

Large surfaces default to a warm near-white neutral in the light design (and
an intentionally near-black neutral in the optional dark design). Their
OKLCH chroma is tightly capped, so a red/green/blue balloon photo gets
negative space rather than a red/green/blue canvas. Abstract circles and
bands use lighter, desaturated derivatives of selected source hues. At most
two separated source hues are allowed to be stronger accents; the rest of the
color story is carried by tints and the photograph.

Source colors are ranked by usable OKLCH chroma and perceptual hue separation,
not by dominance alone. Near-identical hues are deduplicated. The result
includes a short harmony label (monochromatic tonal, analogous,
complementary-ish, or multicolor neutral-support) and a rationale that states
why the roles were selected.

Oklab lightness/chroma/hue are used for generating tints and shades. When a
requested OKLCH color falls outside sRGB, the implementation reduces chroma
with a constant-lightness/constant-hue binary search rather than clipping
RGB channels. This preserves the intended relationship as much as a small
sRGB display gamut permits.

Every semantic text/fill pair has an on-* role and is checked at **4.5:1**
or higher. The check follows WCAG normal-text guidance. Individual layouts
also choose text based on the surface they actually draw on; decorative
shapes never carry a text label.

## Why these references

These are primary standards or original technical sources:

* [W3C WCAG 2.2, Success Criterion 1.4.3](https://www.w3.org/TR/WCAG22/#contrast-minimum)
  defines the 4.5:1 minimum for normal text.
* [W3C CSS Color 4, Oklab/OKLCH](https://www.w3.org/TR/css-color-4/#oklab)
  defines the cylindrical space and its [constant-hue chroma reduction
  gamut-mapping guidance](https://www.w3.org/TR/css-color-4/#css-gamut-mapping).
* [Björn Ottosson, “A perceptual color space for image
  processing”](https://bottosson.github.io/posts/oklab/) is the original
  Oklab formulation and matrix reference used here.
* [Material Design color system](https://m2.material.io/guidelines/material-design/introduction.html)
  motivates semantic background, surface, primary, secondary, and on-* roles.
  PaletteCard borrows the role idea; it is not a claim that these cards are
  Material components.

Color harmony names are useful communication shorthand, not a scientific
prediction of taste. The quality breakdown reports contrast, surface
neutrality, rendered strong-accent count/area, perceptual separation, and
source hue/chroma relationship so a learner can inspect the decisions.
