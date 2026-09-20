# Clippy operations runbook (LXC 103)

Server: `clippy.taile0612a.ts.net` (Tailscale only). App in `/opt/clippy`,
mutable home `/var/lib/clippy` (`0700`, `clippy:clippy`), service
`clippy.service`, cleanup `clippy-cleanup.timer`. All LXC access goes through
`ssh pve 'pct exec 103 -- …'` —direct SSH was removed.

## Deploy from main

```bash
tar -czf /tmp/clippy-deploy.tar.gz --exclude=.git --exclude=.venv \
  --exclude=frontend/node_modules --exclude=dist . 
scp -q /tmp/clippy-deploy.tar.gz pve:/tmp/clippy-deploy.tar.gz
ssh pve 'pct push 103 /tmp/clippy-deploy.tar.gz /tmp/clippy-deploy.tar.gz --perms 0600 \
  && rm -f /tmp/clippy-deploy.tar.gz \
  && pct exec 103 -- sh -c "tar -xzf /tmp/clippy-deploy.tar.gz -C /opt/clippy \
  && rm -f /tmp/clippy-deploy.tar.gz && systemctl restart clippy.service"'
```

Frontend-only change: rebuild (`npm run build` in `frontend/`), sync only
`backend/clippy/static/`, no restart needed (files are served per request);
delete stale `index-*.js/css` hashes afterwards.

## YouTube cookies (they rotate — expect expiry)

```bash
./scripts/sync-youtube-cookies.sh chrome pve 103
```

Close the browser first. If downloads fail with bot-check or
“cookies are no longer valid”, re-export to
`/var/lib/clippy/secrets/youtube-cookies.txt` (`0600`, `clippy:clippy`).
macOS Terminal needs Full Disk Access to read Chrome's cookie DB; otherwise
export `cookies-youtube.txt` manually (Netscape format).

## yt-dlp updates

YouTube breaks extraction regularly:

```bash
ssh pve 'pct exec 103 -- runuser -u clippy -- /opt/clippy/.venv/bin/clippy update-ytdlp'
```

Requires `deno` in `/usr/local/bin` plus `remote_components: ["ejs:github"]`
(baked into `ingest.py` and `install-debian.sh`) for JS challenges.

## API keys and provider secrets

The browser UI authenticates through the identity headers added by Tailscale
Serve and does not ask for an API key. Set `CLIPPY_TAILSCALE_USERS` to a
comma-separated login allowlist when the tailnet has more than one permitted
user. Bearer keys remain the authentication mechanism for n8n and other
automation clients.

```bash
ssh pve 'pct exec 103 -- runuser -u clippy -- env CLIPPY_HOME=/var/lib/clippy \
  /opt/clippy/.venv/bin/clippy api-key create --name NAME'   # value shown once
ssh pve 'pct exec 103 -- runuser -u clippy -- env CLIPPY_HOME=/var/lib/clippy \
  /opt/clippy/.venv/bin/clippy config set-secret gemini'     # prompts invisibly
```

All LLM targets are free-tier: Gemini Flash, Groq `gpt-oss-*`, OpenRouter
`:free` models (the free catalogue rotates — re-check
`https://openrouter.ai/api/v1/models` if OpenRouter 404s on the model name).
Never print a secret; verify with presence only:
`.../python -c "from clippy import config; print({k: bool(config.get_secret(k)) ...})"`.

## Retry a failed job

```bash
curl -N -H "Authorization: Bearer $CLIPPY_TOKEN" \
  "$CLIPPY_URL/api/v1/jobs/JOB_ID/events"   # watch, then:
curl -X POST -H "Authorization: Bearer $CLIPPY_TOKEN" \
  "$CLIPPY_URL/api/v1/jobs/JOB_ID/retry"    # resumes from artifacts
```

Only `failed`/`cancelled` jobs retry (else 409).

## Backup and restore

```bash
ssh pve 'pct exec 103 -- tar -czf /tmp/clippy-backup.tar.gz -C /var/lib clippy'
# restore: stop service, extract over /var/lib, chown -R clippy:clippy, start
```

Covers SQLite, secrets (`0600`), cookies, media, and work dirs.

## Health checks

```bash
curl --fail "$CLIPPY_URL/api/v1/health"
ssh pve 'pct exec 103 -- ss -lntp | grep 8000'   # must show 127.0.0.1 only
ssh pve 'pct exec 103 -- tailscale serve status' # tailnet-only, no funnel
```

## Golden bar

```bash
uv run python -m pytest -m golden -q   # Linux only (MediaPipe crashes on macOS)
```

9/9 green is the v0.1.0 gate. Footage: `tests/data/golden/` (+MANIFEST.md).
