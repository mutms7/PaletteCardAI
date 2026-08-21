from pathlib import Path

from fastapi.testclient import TestClient

from palette_card.runtime import ProductionSettings
from palette_card.server import create_production_app


def test_health_and_fail_closed_readiness(tmp_path: Path):
    settings = ProductionSettings(
        host="127.0.0.1",
        port=7860,
        output_dir=tmp_path / "cards",
        checkpoint=tmp_path / "missing-object.pt",
        palette_checkpoint=tmp_path / "missing-palette.pt",
        max_upload_mb=2,
        max_pixels=1_000_000,
        concurrency=1,
        queue_size=2,
        retention_hours=24,
        require_models=True,
        allowed_hosts=("testserver",),
        username=None,
        password=None,
        log_level="warning",
    )
    with TestClient(create_production_app(settings)) as client:
        health = client.get("/healthz")
        ready = client.get("/readyz")
    assert health.status_code == 200
    assert health.headers["x-content-type-options"] == "nosniff"
    assert ready.status_code == 503
    assert ready.json()["status"] == "not_ready"
