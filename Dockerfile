# One image for both the API (default command) and the ingestion job
# (`olake-ingest run`). Build: docker build -t ask-olake .

# ---- 1. build the widget ----
FROM node:22-slim AS widget
WORKDIR /src/widget
COPY apps/widget/package.json apps/widget/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY apps/widget/ ./
# vite writes ../api/static/widget.js
RUN mkdir -p ../api/static && npm run build

# ---- 2. Python app ----
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
# git is needed by `olake-ingest fetch`
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv
WORKDIR /app

# dependencies first, so code changes don't reinstall them
COPY pyproject.toml uv.lock ./
COPY packages/olake_shared/pyproject.toml packages/olake_shared/
COPY apps/ingest/pyproject.toml apps/ingest/
COPY apps/api/pyproject.toml apps/api/
RUN uv sync --frozen --no-dev --no-install-workspace

COPY packages packages
COPY apps/ingest apps/ingest
COPY apps/api apps/api
COPY eval eval
COPY --from=widget /src/api/static/widget.js apps/api/static/widget.js
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 app \
 && mkdir -p apps/ingest/.work apps/ingest/out \
 && chown -R app /app/apps/ingest/.work /app/apps/ingest/out /app/eval
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
CMD ["olake-api", "serve", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
