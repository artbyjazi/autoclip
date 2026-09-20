# syntax=docker/dockerfile:1

# Clippy — CPU image.
#
# Docker is the guaranteed path: it pins ffmpeg with libass, the exact Python
# version MediaPipe has wheels for, and the font situation, all of which are the
# usual sources of "works on my machine" in this space.
#
# For NVIDIA GPUs, build with:
#   docker build -f docker/Dockerfile --target gpu -t clippy:gpu .

# ---------------------------------------------------------------------------
# Frontend build
# ---------------------------------------------------------------------------
FROM node:22-slim AS frontend

WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY frontend/ ./
# Vite writes into ../backend/clippy/static, so that path has to exist.
RUN mkdir -p /backend/clippy && npm run build -- --outDir /backend/clippy/static

# ---------------------------------------------------------------------------
# Runtime base
# ---------------------------------------------------------------------------
# 3.12 rather than 3.13: MediaPipe publishes no 3.13 wheels, and the reframe
# stage cannot run without it.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CLIPPY_HOME=/data

# ffmpeg from Debian is built with libass and libx264. fonts-liberation gives
# fontconfig a sane fallback if a caption style ever asks for a missing face.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      fonts-liberation \
      libgl1 \
      libglib2.0-0 \
      curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY backend/ ./backend/
COPY --from=frontend /backend/clippy/static ./backend/clippy/static

RUN pip install --no-cache-dir .

# Artifacts live on a volume so clips, transcripts, and settings survive a
# container rebuild.
RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

# 0.0.0.0 is required inside the container. Compose publishes it on host
# loopback because the deprecated UI API is not Bearer-protected.
CMD ["clippy", "serve", "--host", "0.0.0.0", "--port", "8000", "--no-open"]

# ---------------------------------------------------------------------------
# GPU variant
# ---------------------------------------------------------------------------
FROM base AS gpu

# CUDA 12 runtime libraries for CTranslate2. Without these, faster-whisper on a
# GPU fails with a missing cudnn_ops shared library.
RUN pip install --no-cache-dir \
      "nvidia-cublas-cu12>=12.4" \
      "nvidia-cudnn-cu12>=9.1"
