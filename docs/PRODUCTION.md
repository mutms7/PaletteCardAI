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

`palettecardai.vercel.app` is an AI-backed working prototype. `vercel_app.py`
loads both checkpoints into a FastAPI Function, serves the craft-table frontend
at `/`, reports checkpoint readiness at `/readyz`, and accepts generation
requests at `/api/generate`.

The public endpoint returns optimized card JPEGs inline in the generation
response. Do not change it back to temporary Gradio file URLs: Vercel may send a
follow-up download request to a different function instance, where the original
instance's `/tmp` file does not exist. Local and container launches retain the
Gradio workflow and full-size PNG downloads.

The browser limits uploads to 4 MB and the endpoint budgets generated binary
output to 2.8 MB so the base64 JSON response remains below Vercel's 4.5 MB
request/response limit. `PALETTECARD_MOUNT_GRADIO=false` avoids constructing the
unused Gradio tree during Vercel cold starts.

The public editor is client-side. It loads one returned portrait cover into a
locked canvas layer, keeps paint on separate browser layers, and exports a
flattened PNG locally. Drawing, palette mixing, layer changes, and export do not
create another API request.

Deploy from the repository root:

    vercel
    vercel --prod

Vercel instances do not share or durably retain `/tmp`. The API removes its
intermediate PNGs after encoding the response and does not deliberately retain
the uploaded original. Durable server-side galleries or later retrieval would
require object storage. The current public UI does not require WebSockets.

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

The app includes a plain-language privacy screen explaining when a photo is
sent and what the prototype deliberately removes. A public or commercial launch
still needs an approved legal privacy notice. The default output retention is 24
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
