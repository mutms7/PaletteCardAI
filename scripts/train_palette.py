"""Train the compact palette-role student network."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palette_card.palette_model import train_palette_model


def main() -> int:
    parser = argparse.ArgumentParser(description="Train PaletteCard's learned palette-role model")
    parser.add_argument("--data", type=Path, default=Path("data/palette_training/palettes.npz"))
    parser.add_argument("--checkpoint", type=Path, default=Path("artifacts/checkpoints/palette.pt"))
    parser.add_argument("--metrics", type=Path, default=Path("artifacts/metrics/palette/history.json"))
    parser.add_argument("--epochs", type=int, default=60)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    report = train_palette_model(
        args.data, args.checkpoint, args.metrics, epochs=args.epochs,
        batch_size=args.batch_size, learning_rate=args.learning_rate, seed=args.seed,
    )
    print(
        f"Palette training complete. Mean test ΔEOK: {report['test_mean_delta_e_ok']:.2f}; "
        f"90th percentile: {report['test_p90_delta_e_ok']:.2f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
