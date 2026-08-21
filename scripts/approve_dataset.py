"""Explicitly approve a manually reviewed Commons acquisition manifest."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palette_card.commons_dataset import approve_dataset
from palette_card.config import Paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record human review approval for a Commons dataset; never deletes files"
    )
    parser.add_argument("--data-dir", type=Path, default=Paths().data)
    parser.add_argument("--reviewer", required=True, help="Your name or handle")
    parser.add_argument("--note", default="", help="Optional summary of the visual/license checks")
    args = parser.parse_args(argv)
    try:
        receipt = approve_dataset(args.data_dir.expanduser().resolve(), args.reviewer, args.note)
    except (FileNotFoundError, OSError, ValueError) as exc:
        print(f"Dataset approval failed: {exc}")
        return 2
    print(f"Review receipt written: {receipt}")
    print("Training may proceed only while this receipt matches attribution.csv.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
