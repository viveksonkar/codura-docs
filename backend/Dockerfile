# One backend image for migrate, api and worker (ADR-0013). Build context: repo root.
# syntax=docker/dockerfile:1.7

FROM python:3.12.8-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

# ---- build: resolve and install locked dependencies into /app/.venv --------------------------
FROM base AS build
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- runtime ---------------------------------------------------------------------------------
FROM base AS runtime
# OCR (ADR-0016): Tesseract + language packs (tessdata_fast). Adjust packs with OCR_LANG_PACKS.
ARG OCR_LANG_PACKS="eng hin spa fra deu ara chi-sim"
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr \
      $(for l in ${OCR_LANG_PACKS}; do printf 'tesseract-ocr-%s ' "$l"; done) \
 && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid 10001 codura \
 && useradd --system --uid 10001 --gid codura --home-dir /app --shell /usr/sbin/nologin codura \
 && mkdir -p /data/blobs && chown codura:codura /data/blobs
WORKDIR /app
COPY --from=build --chown=codura:codura /app/.venv /app/.venv
COPY --chown=codura:codura backend/migrations ./migrations
COPY --chown=codura:codura config ./config
ENV PATH="/app/.venv/bin:${PATH}" \
    MIGRATIONS_DIR=/app/migrations \
    MODEL_CONFIG_FILE=/app/config/models.yaml \
    BLOB_LOCAL_PATH=/data/blobs
USER codura
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status == 200 else 1)" || exit 1
CMD ["codura-docs", "serve"]
