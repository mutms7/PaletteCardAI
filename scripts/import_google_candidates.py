"""Import Google Images Commons candidates into a review-gated staging tree."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from palette_card.google_candidates import (  # noqa: E402
    DEFAULT_USER_AGENT,
    RateLimitPause,
    run_google_import,
)
from palette_card.config import Paths  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    paths = Paths()
    parser = argparse.ArgumentParser(
        description="Resolve Wikimedia Commons candidates collected from Google Images; never approves or trains"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=paths.root / "data" / "google_candidates" / "search_candidates.jsonl",
        help="JSONL candidate manifest exported from Google Images",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=paths.root / "data" / "google_candidates" / "imported",
        help="review-gated staging directory; existing files are preserved",
    )
    parser.add_argument("--per-prompt", type=int, default=15, help="accepted images requested for each search prompt")
    parser.add_argument("--max-candidates", type=int, default=None, help="maximum valid candidate rows to inspect")
    parser.add_argument("--max-downloads", type=int, default=None, help="maximum media download attempts")
    parser.add_argument("--resume", action="store_true", help="resume an existing staging manifest without deleting anything")
    parser.add_argument("--dry-run", action="store_true", help="resolve metadata and licenses but do not download media")
    parser.add_argument("--pace-seconds", type=float, default=1.0, help="minimum delay between successful Commons requests")
    parser.add_argument("--max-bytes", type=int, default=15_000_000)
    parser.add_argument("--min-width", type=int, default=64)
    parser.add_argument("--min-height", type=int, default=64)
    parser.add_argument("--thumb-width", type=int, default=1600, help="bounded Commons thumbnail width")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT, help="descriptive contact User-Agent (default is PaletteCardAI/0.1 (commons; User:Mutms7))")
    parser.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args(argv)
    try:
        # The library client validates the user-agent and refuses vague bots.
        from palette_card.google_candidates import GoogleCommonsClient

        client = GoogleCommonsClient(
            user_agent=args.user_agent,
            max_bytes=args.max_bytes,
            pacing=args.pace_seconds,
            media_timeout=20.0,
            media_max_retries=1,
        )
        records = run_google_import(
            input_path=args.input,
            output_root=args.output,
            per_prompt=args.per_prompt,
            max_candidates=args.max_candidates,
            max_downloads=args.max_downloads,
            resume=args.resume,
            dry_run=args.dry_run,
            pace_seconds=args.pace_seconds,
            max_bytes=args.max_bytes,
            min_width=args.min_width,
            min_height=args.min_height,
            thumb_width=args.thumb_width,
            client=client,
            progress=args.progress,
        )
    except RateLimitPause as exc:
        if exc.retry_after_seconds is None:
            print("Acquisition paused by Wikimedia HTTP 429; Retry-After was absent. Resume later.")
        else:
            print(
                f"Acquisition paused by Wikimedia HTTP 429; wait exactly {exc.retry_after_seconds:g} seconds "
                "before resuming. The limited candidate was not skipped."
            )
        return 75
    except KeyboardInterrupt:
        print("Google candidate import interrupted safely; resume is available.")
        return 130
    except (FileExistsError, RuntimeError, ValueError, OSError) as exc:
        print(f"Google candidate import failed: {exc}")
        return 2
    print(f"Import complete: {len(records)} accepted records. Review {args.output} before training.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
