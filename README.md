# PaletteCard AI ✏️

> **Turn one photo into a color-smart greeting card.**

PaletteCard AI recognizes a flower, heart, ring, cake, or balloon, studies the
photo's colors, and turns them into three portrait card covers. You can pick one,
paint on it with layers, and export the finished cover as a PNG. I built it
because I wanted to learn what “training an AI” actually means, then make the
result useful instead of stopping at a notebook and an accuracy number.

🌐 **Live working prototype:** [palettecardai.vercel.app](https://palettecardai.vercel.app/)

The public Vercel deployment runs the committed object classifier and learned
palette model through a FastAPI endpoint. Upload a photo, leave recognition on
Auto or correct it manually, generate three real card renders, and download all
three from the browser. The response carries the generated cards inline, so it
does not depend on temporary files surviving across serverless instances. The
card editor runs in the browser and exports a flattened PNG without another
server request.

## Model integration status

Both custom models are integrated into the local and deployed applications and
were verified end to end on August 24, 2026. They are committed under
`artifacts/checkpoints/` and loaded at application startup.

| Stage | Checkpoint | Verified result |
| --- | --- | --- |
| Object recognition | `best.pt` · MobileNetV3 Small · epoch 8 | Loaded with the expected five-class order and 224 px input; stored validation accuracy is 92.9% |
| Palette role selection | `palette.pt` · `palette_mlp_v1` · epoch 60 | Loaded with its 20-value input and 6-value output contract; stored validation loss is 0.0312 |
| Full app pipeline | Both models + flower photo | Predicted `flower` at 97.0%, produced five observed colors and guarded design roles, then rendered three downloadable cards |

The app exposes the same distinction at runtime:

- **Model Mode** means the object checkpoint loaded and Auto classification is enabled.
- **Color model ready** means learned role selection loaded. OKLCH gamut,
  restraint, and WCAG contrast guardrails still validate its output.
- **Demo Mode** is an explicit fallback when a checkpoint is missing or invalid;
  it never invents confidence.

The model readiness flags also feed `/readyz` in the hardened FastAPI service.
Production can require both with `PALETTECARD_REQUIRE_MODELS=true`.

## The studio

The interface follows one plain sequence: pick a photo, meet three covers, then
paint one yourself. Its craft-table visual system uses hand-drawn outlines,
paper panels, tape, bright crayon colors, and Schoolbell for every text role.
Schoolbell is vendored under its Apache 2.0 license, so the studio does not need
a font request.

The redesign includes:

- real Create, How it works, Card editor, and Privacy screens;
- a child-drawn visual map that explains both models from pixels upward;
- visible status for both trained models before a photo is submitted;
- three portrait covers with the unchanged source photo in the center;
- a pointer-based editor that works with a mouse, pen, or finger;
- brush and eraser tools, size and opacity settings, undo, redo, and PNG export;
- a layer panel with visibility, ordering, duplication, renaming, and deletion;
- five model colors plus black, white, custom colors, and a water cup;
- paint wells that shift the brush color as you circle inside them;
- responsive layouts for desktop, tablet, and mobile;
- keyboard focus states and reduced-motion support;
- one consistent visual language across the public FastAPI studio and local Gradio app.

The important design choice is that the app does **not** paint the whole card
with the exact colors it finds. A photo full of bright red, blue, and green
balloons usually needs a quiet neutral background, pale related shapes, a
restrained accent, and readable text. PaletteCard separates observed colors
from design colors so the image stays the focus.

## What it does

1. **Upload one photo.** A centered object works best.
2. **Recognize the object.** A fine-tuned MobileNetV3 Small predicts flower,
   heart, ring, cake, or balloon. You can always correct it manually.
3. **Read the photo.** Pixel clustering finds five representative source
   colors and how much of the image each one occupies.
4. **Design a palette.** A second neural network proposes background,
   secondary, and accent roles. An OKLCH/Oklab color-theory layer then keeps
   large areas calm, preserves contrast, and checks WCAG readability.
5. **Make the covers.** Three portrait layouts place the unchanged photo at the
   center. The local app saves PNGs, while the serverless app returns optimized
   JPEG downloads to stay within its response limit.
6. **Paint one yourself.** The browser editor puts the chosen cover on a locked
   base layer. New paint stays on separate layers, and export flattens only the
   visible stack into one PNG.

## What I actually trained

There are two small models in this project.

The **object classifier** uses transfer learning. MobileNetV3 Small begins with
general visual features learned from ImageNet, and I trained its classifier for
the five PaletteCard objects. Its current held-out result is **78.6% on 28 test
images**. That proves the pipeline works, but it is a prototype measurement,
not a production accuracy promise.

The **palette model** was trained on programmatically generated examples built
from color-theory rules. It learns which colors should take primary and
secondary design roles. Deterministic contrast and accessibility guardrails
still make the final decision, because “looks good” is subjective and a small
model should not be trusted blindly. The score shown in the app is a heuristic,
not an objective rating of beauty.

One thing I want to be upfront about: the current classifier was trained from
Google-cached Wikimedia thumbnails whose individual licenses are recorded as
unverified. The training photos are not included in this repository, and the
checkpoint should be treated as an educational prototype until every source is
verified or the model is retrained on a cleared dataset.

## Run it locally

Requires Python 3.12 (Python 3.10+ also works).

```bash
git clone https://github.com/mutms7/PaletteCardAI.git
cd PaletteCardAI
python -m venv .venv
```

Windows PowerShell:

```powershell
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
python app.py
```

macOS/Linux:

```bash
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
python app.py
```

Open `http://127.0.0.1:7860` (or the local URL printed by Gradio). Both trained
checkpoints are included in the repository. If they are missing, PaletteCard falls back honestly to
Demo Mode: you select the object yourself and the app does not invent a model
confidence.

## Train your own version

Put session-separated images in `data/train`, `data/val`, and `data/test`, with
one folder per class. Start with at least 30 images per class; 100–200 varied,
correctly licensed images per class is a much better target.

```text
data/
├── train/
│   ├── flower/
│   ├── heart/
│   ├── ring/
│   ├── cake/
│   └── balloon/
├── val/
└── test/
```

Train and evaluate the object model:

```bash
python scripts/train.py --allow-weight-download --epochs 12
```

`--allow-weight-download` explicitly permits the first ImageNet weight
download. Use `--no-pretrained` to train from random initialization as an
experiment; it normally needs far more data and performs worse here.

Build the synthetic palette examples and train the palette model:

```bash
python scripts/generate_palette_dataset.py
python scripts/train_palette.py
```

The commands write `artifacts/checkpoints/best.pt` and
`artifacts/checkpoints/palette.pt`. Restart the app to load new checkpoints.
[The color-design notes](docs/COLOR_DESIGN.md) explain the palette rules and
their sources; [the dataset guide](data/README.md) covers licensing, splits,
review receipts, and the optional Wikimedia acquisition workflow.

## Architecture

```text
app.py                         beginner-friendly local Gradio launcher
vercel_app.py                  FastAPI entry point for Vercel
frontend/                      public studio, visual AI map, privacy, and layer editor
src/palette_card/model.py      MobileNet checkpoint loading and inference
src/palette_card/palette.py    source-color extraction and contrast checks
src/palette_card/palette_model.py  learned palette-role model
src/palette_card/design.py     OKLCH/Oklab design rules and guardrails
src/palette_card/card.py       three Pillow card renderers
src/palette_card/app.py        Gradio interface and end-to-end pipeline
src/palette_card/assets/       craft-table UI, licensed font, and theme bridge
src/palette_card/server.py     ASGI UI, AI API, readiness, and security headers
src/palette_card/training.py   training, validation, metrics, and evaluation
scripts/                       dataset, training, and review commands
tests/                         unit, integration, and end-to-end tests
```

## Production and deployment

Vercel runs `vercel_app.py` as one FastAPI Function. The root route serves the
studio, `/readyz` validates both checkpoint hashes, and `/api/generate` accepts
one bounded image upload and returns three inline card renders. This avoids the
cross-instance `/tmp` download failure that occurs when a serverless Gradio
response points to a temporary file on one function instance and the follow-up
request lands on another.

The web endpoint accepts JPG, PNG, and WebP files up to 4 MB in the browser and
compresses its three outputs below a 2.8 MB binary budget. Vercel's request and
response hard limit is 4.5 MB. Local/container Gradio remains available for
full PNG output and can be mounted at `/studio` by leaving
`PALETTECARD_MOUNT_GRADIO=true`.

```bash
vercel
vercel --prod
```

For a long-running container host instead:

```bash
docker build -t palettecard-ai .
docker run --rm -p 7860:7860 --env-file .env palettecard-ai
```

Read [the production runbook](docs/PRODUCTION.md) before treating this prototype
as a public or commercial product. Dataset clearance, a larger evaluation set,
monitoring, abuse controls, and a published privacy notice are still real launch
requirements.

## Tech stack

Python · PyTorch · torchvision · MobileNetV3 · Pillow · NumPy · scikit-learn ·
Gradio · FastAPI · pytest · GitHub Actions · Vercel

## Verification

```bash
python -m compileall -q src
python -m pytest -q
python -m pip check
```

The current suite contains 69 unit, integration, runtime, server, design, and
end-to-end tests. For a production-style readiness check, start
`palette-card-serve` and request `/readyz`; it returns HTTP 200 only when the
configured model policy is satisfied.

The August 25, 2026 workflow was also checked in the running application at a
desktop viewport and at 390 × 844 mobile size. The check covered model
generation, all three portrait covers, paint mixing, mouse and touch drawing,
water dilution, layer controls, undo, redo, privacy navigation, and PNG export.
A local flower image was used as the end-to-end smoke-test input and was not
added to the repository.

The source code is available under the [MIT License](LICENSE). That license
does not grant rights to third-party training images or replace their original
usage terms.
