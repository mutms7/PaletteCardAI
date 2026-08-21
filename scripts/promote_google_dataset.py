"""Explicitly promote reviewed Google Commons staging data for training."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palette_card.config import Paths  # noqa: E402
from palette_card.google_candidates import promote_google_dataset  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    paths = Paths()
    parser = argparse.ArgumentParser(
        description="Promote reviewed Google Commons staging files; training approval remains a separate step"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=paths.root / "data" / "google_candidates" / "imported",
        help="review-gated Google staging directory",
    )
    parser.add_argument("--data-dir", type=Path, default=paths.data, help="standard train/val/test dataset directory")
    parser.add_argument("--audit-dir", type=Path, default=paths.artifacts / "google_dataset_audit")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume", action="store_true", help="append to an existing standard dataset without deleting anything")
    parser.add_argument(
        "--confirm-reviewed",
        action="store_true",
        help="required explicit confirmation after inspecting generated staging contact sheets",
    )
    parser.add_argument("--reviewer", required=True, help="human reviewer name; this does not approve training")
    parser.add_argument("--max-bytes", type=int, default=15_000_000)
    parser.add_argument("--min-width", type=int, default=64)
    parser.add_argument("--min-height", type=int, default=64)
    args = parser.parse_args(argv)
    try:
        records = promote_google_dataset(
            source_root=args.source,
            data_root=args.data_dir,
            audit_root=args.audit_dir,
            seed=args.seed,
            resume=args.resume,
            confirm_reviewed=args.confirm_reviewed,
            reviewer=args.reviewer,
            max_bytes=args.max_bytes,
            min_width=args.min_width,
            min_height=args.min_height,
        )
    except KeyboardInterrupt:
        print("Promotion interrupted safely; the last complete standard manifest remains resumable.")
        return 130
    except (FileExistsError, RuntimeError, ValueError, OSError) as exc:
        print(f"Promotion failed: {exc}")
        return 2
    print(
        f"Promotion complete: {len(records)} records. "
        "Review the promoted contact sheets, then run approve_dataset explicitly before training."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
