"""Hardened ASGI entry point for production deployments."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path

from .app import build_app
from .runtime import ProductionSettings, cleanup_generated_cards, utc_timestamp

LOGGER = logging.getLogger("palette_card.server")


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_production_app(settings: ProductionSettings | None = None):
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    import gradio as gr
    from fastapi import FastAPI, Request
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    from fastapi.responses import JSONResponse

    configured = settings or ProductionSettings.from_env()
    configured.output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_generated_cards(configured.output_dir, configured.retention_hours)
    blocks = build_app(
        configured.checkpoint,
        configured.output_dir,
        configured.palette_checkpoint,
        max_pixels=configured.max_pixels,
        retention_hours=configured.retention_hours,
        concurrency=configured.concurrency,
        queue_size=configured.queue_size,
    )
    object_ready = bool(blocks._palette_card_object_model_ready)
    palette_ready = bool(blocks._palette_card_palette_model_ready)
    started_at = utc_timestamp()
    app = FastAPI(
        title="PaletteCard AI",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(configured.allowed_hosts))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz", include_in_schema=False)
    async def health():
        return {"status": "ok", "started_at": started_at}

    @app.get("/readyz", include_in_schema=False)
    async def ready():
        ready_now = (object_ready and palette_ready) or not configured.require_models
        payload = {
            "status": "ready" if ready_now else "not_ready",
            "object_model": {
                "loaded": object_ready,
                "sha256": _sha256(configured.checkpoint),
            },
            "palette_model": {
                "loaded": palette_ready,
                "sha256": _sha256(configured.palette_checkpoint),
            },
        }
        return JSONResponse(payload, status_code=200 if ready_now else 503)

    auth = (configured.username, configured.password) if configured.username and configured.password else None
    app = gr.mount_gradio_app(
        app,
        blocks,
        path="/",
        allowed_paths=[str(configured.output_dir)],
        auth=auth,
        show_error=False,
        max_file_size=f"{configured.max_upload_mb}mb",
        enable_monitoring=False,
        footer_links=[],
    )
    app.state.palette_card_settings = configured
    return app


def main() -> int:
    import uvicorn

    logging.basicConfig(
        level=os.environ.get("PALETTECARD_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    settings = ProductionSettings.from_env()
    LOGGER.info(
        "starting host=%s port=%s concurrency=%s queue_size=%s max_upload_mb=%s",
        settings.host, settings.port, settings.concurrency, settings.queue_size, settings.max_upload_mb,
    )
    uvicorn.run(
        create_production_app(settings),
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        proxy_headers=True,
        forwarded_allow_ips=os.environ.get("PALETTECARD_FORWARDED_ALLOW_IPS", "127.0.0.1"),
        server_header=False,
        limit_concurrency=max(settings.concurrency * 4, 16),
        timeout_keep_alive=5,
        timeout_graceful_shutdown=30,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
