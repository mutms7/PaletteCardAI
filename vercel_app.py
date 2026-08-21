"""Vercel FastAPI entry point for PaletteCard AI."""

from __future__ import annotations

import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

# Vercel Functions may write only to /tmp. Each instance keeps its own
# short-lived output, which matches the app's published retention policy.
os.environ.setdefault("PALETTECARD_ROOT", str(ROOT))
os.environ.setdefault("PALETTECARD_OUTPUT_DIR", "/tmp/palettecard-cards")
os.environ.setdefault(
    "PALETTECARD_ALLOWED_HOSTS",
    "palettecardai.vercel.app,*.vercel.app,localhost,127.0.0.1",
)
os.environ.setdefault("PALETTECARD_REQUIRE_MODELS", "true")
os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")

from palette_card.server import create_production_app  # noqa: E402


app = create_production_app()
