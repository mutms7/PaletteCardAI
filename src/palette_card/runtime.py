"""Production runtime policy, upload validation, and bounded output cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import time

from PIL import Image, ImageOps

from .config import Paths


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    value = int(os.environ.get(name, default))
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


@dataclass(frozen=True)
class ProductionSettings:
    host: str
    port: int
    output_dir: Path
    checkpoint: Path
    palette_checkpoint: Path
    max_upload_mb: int
    max_pixels: int
    concurrency: int
    queue_size: int
    retention_hours: int
    require_models: bool
    allowed_hosts: tuple[str, ...]
    username: str | None
    password: str | None
    log_level: str

    @classmethod
    def from_env(cls) -> "ProductionSettings":
        paths = Paths()
        username = os.environ.get("PALETTECARD_USERNAME") or None
        password = os.environ.get("PALETTECARD_PASSWORD") or None
        if bool(username) != bool(password):
            raise ValueError("PALETTECARD_USERNAME and PALETTECARD_PASSWORD must be set together")
        allowed = tuple(
            item.strip() for item in os.environ.get("PALETTECARD_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
            if item.strip()
        )
        if not allowed:
            raise ValueError("PALETTECARD_ALLOWED_HOSTS must not be empty")
        log_level = os.environ.get("PALETTECARD_LOG_LEVEL", "info").strip().lower()
        if log_level not in {"critical", "error", "warning", "info", "debug"}:
            raise ValueError("PALETTECARD_LOG_LEVEL is invalid")
        return cls(
            host=os.environ.get("PALETTECARD_HOST", "0.0.0.0"),
            port=_env_int("PALETTECARD_PORT", 7860, 1, 65535),
            output_dir=Path(os.environ.get("PALETTECARD_OUTPUT_DIR", paths.cards)).expanduser().resolve(),
            checkpoint=Path(os.environ.get("PALETTECARD_CHECKPOINT", paths.default_checkpoint)).expanduser().resolve(),
            palette_checkpoint=Path(os.environ.get("PALETTECARD_PALETTE_CHECKPOINT", paths.palette_checkpoint)).expanduser().resolve(),
            max_upload_mb=_env_int("PALETTECARD_MAX_UPLOAD_MB", 10, 1, 100),
            max_pixels=_env_int("PALETTECARD_MAX_PIXELS", 16_000_000, 100_000, 100_000_000),
            concurrency=_env_int("PALETTECARD_CONCURRENCY", 2, 1, 32),
            queue_size=_env_int("PALETTECARD_QUEUE_SIZE", 32, 1, 1000),
            retention_hours=_env_int("PALETTECARD_RETENTION_HOURS", 24, 1, 720),
            require_models=_env_bool("PALETTECARD_REQUIRE_MODELS", True),
            allowed_hosts=allowed,
            username=username,
            password=password,
            log_level=log_level,
        )


def validate_upload_image(image: Image.Image, max_pixels: int = 16_000_000) -> Image.Image:
    """Normalize EXIF orientation and reject pathological image dimensions."""

    if image is None:
        raise ValueError("Upload an image first")
    width, height = image.size
    if width < 32 or height < 32:
        raise ValueError("Image must be at least 32×32 pixels")
    if width * height > max_pixels:
        raise ValueError(f"Image is too large; maximum decoded size is {max_pixels:,} pixels")
    normalized = ImageOps.exif_transpose(image)
    normalized.load()
    return normalized.convert("RGB")


def cleanup_generated_cards(directory: str | Path, retention_hours: int, now: float | None = None) -> int:
    """Delete only expired PaletteCard PNG outputs in one exact directory."""

    root = Path(directory).expanduser().resolve()
    if retention_hours < 1:
        raise ValueError("retention_hours must be at least 1")
    if not root.exists():
        return 0
    cutoff = float(now if now is not None else time.time()) - retention_hours * 3600
    removed = 0
    for path in root.glob("palette-card-*.png"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
