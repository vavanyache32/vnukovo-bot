# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ---------- builder ----------
FROM base AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential gcc curl && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY src ./src
RUN python -m venv /opt/venv && /opt/venv/bin/pip install -U pip wheel \
 && /opt/venv/bin/pip install .

# ---------- runtime ----------
FROM base AS runtime
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates tini && rm -rf /var/lib/apt/lists/* \
 && useradd -r -u 10001 -g root -d /app -m bot

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY src ./src
COPY stations.yaml alembic.ini ./
RUN mkdir -p data/raw && chown -R bot:root /app

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONPATH=/app

USER bot

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
                   port=os.environ.get('HEALTH_PORT','8080'); \
                   r=urllib.request.urlopen(f'http://127.0.0.1:{port}/healthz', timeout=3); \
                   sys.exit(0 if r.status==200 else 1)"

ENTRYPOINT ["tini", "--", "python", "-m", "src.cli"]
CMD ["monitor"]
