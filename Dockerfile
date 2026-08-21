FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    GRADIO_ANALYTICS_ENABLED=False \
    PALETTECARD_HOST=0.0.0.0 \
    PALETTECARD_PORT=7860

WORKDIR /app

RUN groupadd --gid 10001 palettecard \
    && useradd --uid 10001 --gid palettecard --create-home --shell /usr/sbin/nologin palettecard

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --upgrade pip \
    && python -m pip install .

COPY app.py ./
COPY artifacts/checkpoints ./artifacts/checkpoints
RUN mkdir -p /app/artifacts/cards \
    && chown -R palettecard:palettecard /app/artifacts

USER palettecard
EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/readyz', timeout=3)"

CMD ["palette-card-serve"]
