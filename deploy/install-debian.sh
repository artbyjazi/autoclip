#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root." >&2
  exit 1
fi

test -f /opt/clippy/uv.lock
test -f /opt/clippy/deploy/clippy.service

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl ffmpeg fonts-liberation git libgl1 libglib2.0-0 unzip

# Deno solves YouTube's JS challenges for yt-dlp (see ingest.py
# `remote_components`). /usr/local/bin is already on the service PATH.
if ! command -v deno >/dev/null 2>&1; then
  curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh
fi

if ! command -v uv >/dev/null 2>&1; then
  UV_INSTALL_DIR=/usr/local/bin sh -c \
    'curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh'
fi

if ! command -v tailscale >/dev/null 2>&1; then
  curl -fsSL https://tailscale.com/install.sh | sh
fi

if ! id clippy >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/clippy --shell /usr/sbin/nologin clippy
fi
install -d -o clippy -g clippy -m 0700 /var/lib/clippy /var/lib/clippy/secrets
chown -R clippy:clippy /opt/clippy

cd /opt/clippy
runuser -u clippy -- uv sync --frozen --python 3.12 --no-dev
runuser -u clippy -- env CLIPPY_HOME=/var/lib/clippy .venv/bin/clippy init
find /var/lib/clippy -maxdepth 1 -type f -exec chmod 0600 {} \;

install -o root -g root -m 0644 deploy/clippy.service /etc/systemd/system/clippy.service
install -o root -g root -m 0644 \
  deploy/clippy-cleanup.service /etc/systemd/system/clippy-cleanup.service
install -o root -g root -m 0644 \
  deploy/clippy-cleanup.timer /etc/systemd/system/clippy-cleanup.timer
systemctl daemon-reload
systemctl enable --now clippy.service clippy-cleanup.timer
