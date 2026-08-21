"""Generate a synthetic, quality-filtered palette-learning curriculum."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palette_card.palette_model import generate_palette_dataset, save_palette_dataset


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate teacher-labeled Oklab palette samples")
    parser.add_argument("--count", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path, default=Path("data/palette_training/palettes.npz"))
    args = parser.parse_args()
    inputs, targets = generate_palette_dataset(args.count, args.seed)
    path = save_palette_dataset(args.output, inputs, targets, args.seed)
    print(f"Created {len(inputs)} palette samples at {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
