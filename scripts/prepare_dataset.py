"""Create the empty, five-class dataset layout."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from palette_card.training import prepare_main


if __name__ == "__main__":
    raise SystemExit(prepare_main())

