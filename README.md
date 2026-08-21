# PaletteCard AI

> **Turn one photo into a color-smart greeting card.**

PaletteCard AI recognizes a flower, heart, ring, cake, or balloon, studies the
photo's colors, and turns them into three downloadable card designs. I built it
because I wanted to learn what “training an AI” actually means, then make the
result useful instead of stopping at a notebook and an accuracy number.

🌐 **Live app:** [palettecardai.vercel.app](https://palettecardai.vercel.app/)

The current public deployment is a **frontend-only preview**: it loads
instantly, keeps selected images inside the browser tab, and does not load or
call either trained model. The Python/PyTorch project remains here for local
training and for a future inference backend.

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
5. **Make the cards.** Three layouts are rendered as high-resolution PNGs for
   preview and download.

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

Open the local URL printed by Gradio. Both trained checkpoints are included in
the published app. If they are missing, PaletteCard falls back honestly to
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
app.py                         beginner-friendly local launcher
vercel_app.py                  FastAPI/Gradio entry point for Vercel
src/palette_card/model.py      MobileNet checkpoint loading and inference
src/palette_card/palette.py    source-color extraction and contrast checks
src/palette_card/palette_model.py  learned palette-role model
src/palette_card/design.py     OKLCH/Oklab design rules and guardrails
src/palette_card/card.py       three Pillow card renderers
src/palette_card/app.py        Gradio interface and end-to-end pipeline
src/palette_card/server.py     hardened ASGI service and health endpoints
src/palette_card/training.py   training, validation, metrics, and evaluation
scripts/                       dataset, training, and review commands
tests/                         unit, integration, and end-to-end tests
```

## Production and deployment

Vercel currently serves the static files in `frontend/`; there is no Python
installation, AI bundle, image upload, or function cold start in the public
preview. `vercel.json` deliberately selects the static folder and adds basic
browser security headers. The trained application still runs locally through
`python app.py`, and `vercel_app.py` remains available as a future FastAPI
entry point when an inference backend is wanted again.

```bash
vercel
vercel --prod
```

For a long-running container host instead:

```bash
docker build -t palettecard-ai .
docker run --rm -p 7860:7860 --env-file .env palettecard-ai
```

Read [the production runbook](docs/PRODUCTION.md) before reconnecting the AI or
treating this as a public or commercial service. Durable file storage, dataset
clearance, a larger evaluation set, monitoring, abuse controls, and a published
privacy notice are still real launch requirements.

## Tech stack

Python · PyTorch · torchvision · MobileNetV3 · Pillow · NumPy · scikit-learn ·
Gradio · FastAPI · pytest · GitHub Actions · Vercel

## Checks

```bash
python -m compileall -q src
python -m pytest -q
python -m pip check
```

The source code is available under the [MIT License](LICENSE). That license
does not grant rights to third-party training images or replace their original
usage terms.
