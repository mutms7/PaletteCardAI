"""Hardened ASGI entry point for production deployments."""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
from pathlib import Path
import re

from fastapi import UploadFile
from PIL import Image

from .app import _roles_from_status, analyze_image, build_app, load_palette_predictor, load_predictor
from .config import CLASS_NAMES
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


def _encode_web_cards(gallery, max_binary_bytes: int = 2_800_000) -> list[dict[str, str]]:
    """Encode three cards inline so serverless instances need no shared disk.

    Vercel limits both request and response payloads to 4.5 MB. Base64 adds
    roughly one third, so the binary budget stays deliberately below 3 MB.
    """

    cards = [item[0] if isinstance(item, tuple) else item for item in gallery]
    attempts = ((1600, 88), (1600, 80), (1280, 80), (1120, 74))
    encoded: list[bytes] = []
    for width, quality in attempts:
        encoded = []
        for card in cards:
            candidate = card.convert("RGB")
            if candidate.width > width:
                height = round(candidate.height * width / candidate.width)
                candidate = candidate.resize((width, height), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            candidate.save(buffer, format="JPEG", quality=quality, optimize=True, progressive=True)
            encoded.append(buffer.getvalue())
        if sum(map(len, encoded)) <= max_binary_bytes:
            break
    if sum(map(len, encoded)) > max_binary_bytes:
        raise ValueError("Generated cards are too large for the web response. Try a simpler or smaller photo.")
    return [
        {
            "filename": f"palette-card-{index}.jpg",
            "media_type": "image/jpeg",
            "data_url": "data:image/jpeg;base64," + base64.b64encode(payload).decode("ascii"),
        }
        for index, payload in enumerate(encoded, 1)
    ]


def _public_status(status: str) -> dict[str, object]:
    lines = status.splitlines()
    recognition = next((line for line in lines if line.startswith("Object: ")), "Object: unavailable")
    palette_line = next((line for line in lines if line.startswith("Palette: ")), "")
    source_colors = re.findall(r"#[0-9A-Fa-f]{6}", palette_line.split("[Source", 1)[0])[:5]
    roles = _roles_from_status(status) or {}
    return {
        "recognition": recognition,
        "source_colors": [color.upper() for color in source_colors],
        "design_roles": roles,
        "details": status,
    }


def create_production_app(settings: ProductionSettings | None = None):
    os.environ.setdefault("GRADIO_ANALYTICS_ENABLED", "False")
    import gradio as gr
    from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
    from fastapi.middleware.trustedhost import TrustedHostMiddleware
    from fastapi.responses import FileResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles
    from starlette.concurrency import run_in_threadpool

    configured = settings or ProductionSettings.from_env()
    configured.output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_generated_cards(configured.output_dir, configured.retention_hours)
    mount_gradio = os.environ.get("PALETTECARD_MOUNT_GRADIO", "true").strip().lower() not in {"0", "false", "no", "off"}
    blocks = None
    if mount_gradio:
        blocks = build_app(
            configured.checkpoint,
            configured.output_dir,
            configured.palette_checkpoint,
            max_pixels=configured.max_pixels,
            retention_hours=configured.retention_hours,
            concurrency=configured.concurrency,
            queue_size=configured.queue_size,
        )
        predictor = blocks._palette_card_predictor
        palette_predictor = blocks._palette_card_palette_predictor
        mode_message = blocks._palette_card_mode_message
        palette_mode_message = blocks._palette_card_palette_mode_message
    else:
        predictor, mode_message = load_predictor(configured.checkpoint)
        palette_predictor, palette_mode_message = load_palette_predictor(configured.palette_checkpoint)
    object_ready = predictor is not None
    palette_ready = palette_predictor is not None
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

    frontend_dir = Path(__file__).resolve().parents[2] / "frontend"
    if frontend_dir.is_dir():
        app.mount("/frontend", StaticFiles(directory=frontend_dir), name="frontend")

        @app.get("/", include_in_schema=False)
        async def frontend():
            return FileResponse(frontend_dir / "index.html")

    @app.post("/api/generate", include_in_schema=False)
    async def generate(
        file: UploadFile = File(...),
        object_choice: str = Form("Auto"),
        title: str = Form("For someone wonderful"),
        message: str = Form("A tiny note, made just for you."),
    ):
        if not object_ready or not palette_ready:
            raise HTTPException(status_code=503, detail="Both AI models must be ready before cards can be generated.")
        if file.content_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise HTTPException(status_code=415, detail="Choose a JPG, PNG, or WebP image.")
        limit = configured.max_upload_mb * 1024 * 1024
        payload = await file.read(limit + 1)
        if not payload or len(payload) > limit:
            raise HTTPException(status_code=413, detail=f"Choose an image smaller than {configured.max_upload_mb} MB.")
        requested_choice = object_choice.strip().lower()
        normalized_choice = "Auto" if requested_choice == "auto" else requested_choice
        if normalized_choice != "Auto" and normalized_choice not in CLASS_NAMES:
            raise HTTPException(status_code=422, detail="Object choice is not supported.")
        if len(title) > 64 or len(message) > 160:
            raise HTTPException(status_code=422, detail="Card copy is too long.")
        try:
            image = Image.open(io.BytesIO(payload))
            image.load()
            image = image.convert("RGB")
            gallery, paths, status = await run_in_threadpool(
                lambda: analyze_image(
                    image,
                    normalized_choice,
                    title.strip() or "For someone wonderful",
                    message.strip() or "A tiny note, made just for you.",
                    predictor,
                    mode_message,
                    configured.output_dir,
                    palette_predictor,
                    palette_mode_message,
                    configured.max_pixels,
                    configured.retention_hours,
                    True,
                )
            )
            cards = await run_in_threadpool(_encode_web_cards, gallery)
            public_status = _public_status(status)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            if "paths" in locals():
                for generated in paths:
                    Path(generated).unlink(missing_ok=True)
        return {
            "status": "ready",
            "model_mode": True,
            "cards": cards,
            **public_status,
        }

    if blocks is not None:
        auth = (configured.username, configured.password) if configured.username and configured.password else None
        app = gr.mount_gradio_app(
            app,
            blocks,
            path="/studio",
            allowed_paths=[str(configured.output_dir)],
            auth=auth,
            show_error=False,
            max_file_size=f"{configured.max_upload_mb}mb",
            enable_monitoring=False,
            footer_links=[],
            css=blocks._palette_card_css,
            js=blocks._palette_card_js,
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
