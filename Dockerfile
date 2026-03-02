# ============================================================
# Video Moments — Backend Dockerfile (Multi-target)
#
# Targets:
#   api    — FastAPI server (uvicorn, port 7005)
#   worker — Pipeline worker (Redis Streams consumer)
#
# Build commands:
#   docker build --target api    -t moments-api    moments-backend/
#   docker build --target worker -t moments-worker moments-backend/
#
# Docker Compose handles target selection via the `target` field.
# ============================================================

# ---- Base stage: shared system deps, Python deps, app code ----
FROM python:3.11-slim AS base

# System dependencies:
#   ffmpeg       — video clip extraction and audio extraction (subprocess calls)
#   curl         — used by HEALTHCHECK on the api target
#   libgl1, libglib2.0-0 — required by opencv-python (not opencv-python-headless)
#                          Note: libgl1-mesa-glx was renamed to libgl1 in Debian Trixie
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better layer caching — pip install
# only reruns when requirements.txt changes, not on code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code (.dockerignore excludes venv, logs, temp, credentials, etc.)
COPY . .

# Pre-create temp directory structure used by the pipeline worker
# and audio/clip extraction services (configurable via TEMP_BASE_DIR env var).
RUN mkdir -p temp/videos temp/audio temp/clips temp/thumbnails

# ---- API target: FastAPI via Uvicorn ----
FROM base AS api

EXPOSE 7005

# Prevent the embedded pipeline worker from starting inside the API container.
# The worker runs as a separate container (target: worker).
ENV RUN_PIPELINE_WORKER=false

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:7005/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7005"]

# ---- Worker target: Redis Streams pipeline consumer ----
FROM base AS worker

# No EXPOSE — the worker has no HTTP server; it reads from Redis Streams.
# Migrations are handled by the API container (set RUN_MIGRATIONS=false in compose).

CMD ["python", "run_worker.py"]
