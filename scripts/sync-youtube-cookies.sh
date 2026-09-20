#!/bin/sh
set -eu

browser="${1:-chrome}"
pve_host="${2:-pve}"
vmid="${3:-103}"
cookie_tmp="$(mktemp -t clippy-youtube-cookies.XXXXXX)"
remote_tmp="/tmp/clippy-youtube-cookies-${vmid}.txt"
trap 'rm -f "$cookie_tmp"' EXIT HUP INT TERM

yt-dlp \
  --cookies-from-browser "$browser" \
  --cookies "$cookie_tmp" \
  --simulate \
  'https://www.youtube.com/watch?v=BaW_jenozKc'
chmod 0600 "$cookie_tmp"
scp -q "$cookie_tmp" "$pve_host:$remote_tmp"
ssh "$pve_host" \
  "pct push '$vmid' '$remote_tmp' /var/lib/clippy/secrets/youtube-cookies.txt --perms 0600 && rm -f '$remote_tmp' && pct exec '$vmid' -- chown clippy:clippy /var/lib/clippy/secrets/youtube-cookies.txt"

echo "Clippy YouTube cookies refreshed in LXC $vmid."
