"""Download a reviewed, license-aware Wikimedia Commons starter dataset."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palette_card.commons_dataset import CLASS_NAMES, CommonsClient, DEFAULT_CATEGORIES, Paths, run_acquisition


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Download a balanced Wikimedia Commons starter dataset; no deletion is performed")
    parser.add_argument("--data-dir", type=Path, default=Paths().data)
    parser.add_argument("--audit-dir", type=Path, default=Paths().artifacts / "dataset_audit")
    parser.add_argument("--per-class", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--resume", action="store_true", help="Permit adding to an existing target and preserve existing manifest assignments")
    parser.add_argument("--dry-run", action="store_true", help="List accepted metadata candidates without downloading")
    parser.add_argument("--list-candidates", action="store_true", help="Alias for --dry-run")
    parser.add_argument("--category", action="append", default=[], metavar="CLASS=CATEGORY", help="Override a source category; repeat for multiple classes")
    parser.add_argument("--max-bytes", type=int, default=15_000_000)
    parser.add_argument("--min-width", type=int, default=64)
    parser.add_argument("--min-height", type=int, default=64)
    parser.add_argument("--pace-seconds", type=float, default=0.25, help="Delay between successful Commons requests")
    parser.add_argument("--media-timeout", type=float, default=10.0, help="Short timeout for individual media downloads")
    parser.add_argument("--media-retries", type=int, default=1, help="Retries for media downloads; API retries remain separate")
    parser.add_argument("--quiet", action="store_true", help="Suppress streamed progress lines")
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True, help="Show concise progress as images commit")
    parser.add_argument("--user-agent", required=True, help="Descriptive PaletteCard app name plus https URL, email, or User: contact")
    args = parser.parse_args(argv)
    categories = dict(DEFAULT_CATEGORIES)
    for override in args.category:
        if "=" not in override:
            parser.error("--category must be CLASS=CATEGORY")
        class_name, category = override.split("=", 1)
        if class_name not in CLASS_NAMES or not category.strip():
            parser.error(f"--category class must be one of {list(CLASS_NAMES)}")
        categories[class_name] = category.strip()
    try:
        client = CommonsClient(user_agent=args.user_agent, max_bytes=args.max_bytes, pacing=args.pace_seconds, media_timeout=args.media_timeout, media_max_retries=args.media_retries)
        records = run_acquisition(data_root=args.data_dir, audit_root=args.audit_dir, categories=categories, per_class=args.per_class, seed=args.seed, resume=args.resume, dry_run=args.dry_run, list_candidates=args.list_candidates, client=client, max_bytes=args.max_bytes, min_width=args.min_width, min_height=args.min_height, pace_seconds=args.pace_seconds, progress=args.progress, quiet=args.quiet)
    except KeyboardInterrupt:
        print("Dataset acquisition interrupted safely; resume is available.")
        return 130
    except (FileExistsError, RuntimeError, ValueError) as exc:
        print(f"Dataset acquisition failed: {exc}")
        return 2
    if not (args.dry_run or args.list_candidates):
        print(f"Acquisition complete: {len(records)} records. Review contact sheets under {args.audit_dir} and verify licenses before use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
