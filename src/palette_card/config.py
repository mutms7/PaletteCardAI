"""Shared configuration and constants.

Keep paths relative to the project root so the project works on Windows,
macOS, and Linux without editing source code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Tuple

CLASS_NAMES: Tuple[str, ...] = ("flower", "heart", "ring", "cake", "balloon")
IMAGE_SIZE = 224
IMAGENET_MEAN = (0.485, 0.456, 0.406)
IMAGENET_STD = (0.229, 0.224, 0.225)


def project_root() -> Path:
    """Return the repository root, independent of the current working directory."""

    configured = os.environ.get("PALETTECARD_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    current = Path.cwd().resolve()
    if (current / "pyproject.toml").exists():
        return current
    return Path(__file__).resolve().parents[2]


@dataclass
class Paths:
    root: Path = field(default_factory=project_root)

    @property
    def data(self) -> Path:
        return self.root / "data"

    @property
    def artifacts(self) -> Path:
        return self.root / "artifacts"

    @property
    def checkpoints(self) -> Path:
        return self.artifacts / "checkpoints"

    @property
    def metrics(self) -> Path:
        return self.artifacts / "metrics"

    @property
    def cards(self) -> Path:
        return self.artifacts / "cards"

    @property
    def default_checkpoint(self) -> Path:
        return self.checkpoints / "best.pt"

    @property
    def palette_checkpoint(self) -> Path:
        return self.checkpoints / "palette.pt"


@dataclass
class TrainConfig:
    data_dir: Path = field(default_factory=lambda: Paths().data)
    output_checkpoint: Path = field(default_factory=lambda: Paths().default_checkpoint)
    metrics_dir: Path = field(default_factory=lambda: Paths().metrics)
    epochs: int = 12
    batch_size: int = 16
    learning_rate: float = 1e-3
    weight_decay: float = 1e-4
    label_smoothing: float = 0.05
    mixup_alpha: float = 0.20
    num_workers: int = 0
    seed: int = 42
    image_size: int = IMAGE_SIZE
    pretrained: bool = True
    allow_weight_download: bool = False
    device: str = "auto"
