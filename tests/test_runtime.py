import os
import time
from pathlib import Path

import pytest
from PIL import Image

from palette_card.runtime import ProductionSettings, cleanup_generated_cards, validate_upload_image


def test_upload_validation_normalizes_and_bounds_pixels():
    image = Image.new("RGBA", (80, 60), (1, 2, 3, 100))
    assert validate_upload_image(image, max_pixels=5_000).mode == "RGB"
    with pytest.raises(ValueError, match="too large"):
        validate_upload_image(image, max_pixels=4_000)
    with pytest.raises(ValueError, match="32"):
        validate_upload_image(Image.new("RGB", (10, 10)), max_pixels=5_000)


def test_cleanup_removes_only_expired_generated_cards(tmp_path: Path):
    expired = tmp_path / "palette-card-old-1.png"
    recent = tmp_path / "palette-card-new-1.png"
    unrelated = tmp_path / "family-photo.png"
    for path in (expired, recent, unrelated):
        path.write_bytes(b"x")
    now = time.time()
    os.utime(expired, (now - 48 * 3600, now - 48 * 3600))
    assert cleanup_generated_cards(tmp_path, retention_hours=24, now=now) == 1
    assert not expired.exists()
    assert recent.exists() and unrelated.exists()


def test_production_settings_reject_partial_auth(monkeypatch):
    monkeypatch.setenv("PALETTECARD_USERNAME", "user")
    monkeypatch.delenv("PALETTECARD_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="set together"):
        ProductionSettings.from_env()
