# Production runbook

This document covers the hardened PaletteCard ASGI service. The visual design
can change independently; the controls below should remain in place.

## Current launch decision

The application is technically deployable, but the current object checkpoint
was trained from Google-cached thumbnails whose individual Commons licenses
are recorded as **unverified**. Do not represent the dataset as cleared for
commercial redistribution. Before a public or commercial launch, verify every
source page and usage term, replace uncertain records, retrain, and retain the
attribution manifest and review receipt. Obtain qualified legal advice for the
intended jurisdiction and use; this project does not provide a copyright
determination.

The classifier test set contains only 28 images. Its 78.6% result is a useful
prototype measurement, not a production service-level guarantee. Collect a
larger, representative, session-separated evaluation set before making
accuracy claims.

## Runtime protections

- FastAPI/ASGI entry point with /healthz and fail-closed /readyz endpoints.
- Startup validation for both model checkpoints; readiness returns HTTP 503
  when required models are unavailable.
- SHA-256 model identities reported by readiness for deployment and rollback.
- Upload file-size and decoded-pixel limits.
- EXIF orientation normalization and minimum image dimensions.
- Bounded inference concurrency and queue length.
- Generated PNG expiration; originals are not deliberately copied into the
  project.
- Downloads restricted to the exact output directory.
- Trusted-host validation, security headers, disabled monitoring UI, hidden
  internal errors, and optional basic authentication.
- Non-root container user and container readiness health check.

## Local production-mode smoke test

Both checkpoints must exist:

    artifacts/checkpoints/best.pt
    artifacts/checkpoints/palette.pt

Set the environment, then run:

    $env:PALETTECARD_ALLOWED_HOSTS="localhost,127.0.0.1"
    python -m palette_card.server

Check:

    Invoke-RestMethod http://127.0.0.1:7860/healthz
    Invoke-RestMethod http://127.0.0.1:7860/readyz

The readiness endpoint must return HTTP 200 and show both models as loaded.

## Container

Build only after placing the approved model files in artifacts/checkpoints:

    docker build -t palettecard-ai:local .
    docker run --rm -p 7860:7860 --env-file .env palettecard-ai:local

For a multi-instance deployment, local generated files are not shared between
replicas. Start with one replica or add an object-storage adapter before
horizontal scaling.

## Vercel

The current `palettecardai.vercel.app` production site is intentionally a
static frontend preview configured by `vercel.json`. It does not install
Python, load checkpoints, upload images, or invoke the ASGI app. Images selected
in that preview stay within the browser tab.

The notes below describe the optional future AI-backed deployment.

`vercel_app.py` exposes the hardened FastAPI/Gradio application as a Vercel
Python Function. New Vercel projects use Fluid Compute and can place large AI
dependencies on the large-function path. The deployment keeps generated cards
under `/tmp/palettecard-cards` because the function bundle is read-only.

Deploy from the repository root:

    vercel
    vercel --prod

Vercel instances do not share or durably retain `/tmp`. A card download will
normally work during the active session, but durable cross-instance downloads
require an object-storage adapter. The current app does not rely on WebSockets;
if a future Gradio upgrade introduces that requirement, Vercel Functions do
not provide a WebSocket server and the UI must be retested or moved.

## Edge and hosting requirements

The application should sit behind a managed HTTPS reverse proxy or load
balancer. Configure the platform for:

- TLS certificates and HTTP-to-HTTPS redirects;
- request and connection rate limits;
- DDoS and bot protection appropriate to a public image-upload service;
- secret injection for optional authentication;
- centralized stdout and access logs plus error alerts;
- storage matching the published privacy policy;
- CPU and memory limits plus autoscaling alarms.

Set PALETTECARD_ALLOWED_HOSTS to the real domain. Set
PALETTECARD_FORWARDED_ALLOW_IPS only to trusted proxy addresses; never use a
wildcard unless the network boundary independently prevents spoofing.

## Privacy and retention

Publish a privacy notice explaining that users upload images for inference and
that generated cards are downloadable. The default output retention is 24
hours and is configured through PALETTECARD_RETENTION_HOURS. Cleanup removes
only files matching palette-card-*.png in the configured output directory.
Confirm host-level backups and logs do not silently extend the stated
retention period.

Do not enable Gradio public sharing in production. Authentication credentials
must be supplied through the hosting platform secret manager.

## Release checklist

1. CI is green and the container builds.
2. Approved object and palette checkpoints are present.
3. /readyz is HTTP 200 in the release environment.
4. Model SHA-256 values match the release record.
5. A representative smoke image produces three downloadable cards.
6. Upload limits, queue saturation, and expiration cleanup are tested.
7. Domain, TLS, rate limits, logs, alerts, and rollback are configured.
8. Accessibility and responsive-layout QA is complete after the redesign.
9. Dataset provenance, privacy notice, terms, and launch approval are signed
   off by the appropriate humans.

## Rollback

Keep the previous two approved checkpoint pairs and container tags. Roll back
the container and both checkpoints together, then verify hashes through
/readyz. Do not mix independently versioned object and palette checkpoints
without a recorded compatibility test.
