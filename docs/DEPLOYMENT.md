# Private Debian LXC deployment

The production profile is a dedicated unprivileged Debian 13 LXC. Clippy binds
only to `127.0.0.1:8000`; Tailscale Serve is the sole network entry point.
There is no Funnel, port forwarding, LAN bind, or Cloudflare Tunnel.

## Filesystem and services

- Application: `/opt/clippy`
- Mutable home: `/var/lib/clippy` (`0700`, owner `clippy`)
- Provider keys: `/var/lib/clippy/secrets.json` (`0600`)
- YouTube cookies: `/var/lib/clippy/secrets/youtube-cookies.txt` (`0600`)
- API service: `clippy.service`
- Daily retention: `clippy-cleanup.timer`

Install after copying the repository (including the built frontend) to
`/opt/clippy`:

```bash
chmod 0755 /opt/clippy/deploy/install-debian.sh
/opt/clippy/deploy/install-debian.sh
```

Join the tailnet once, then publish only the loopback service:

```bash
tailscale up --hostname clippy
tailscale serve --bg http://127.0.0.1:8000
tailscale serve status
```

The installer adds Tailscale from its official Debian repository. The
`tailscale up` authorization URL is the only interactive deployment step.
Do not use `tailscale funnel`.

Create the first API key as the service user:

```bash
runuser -u clippy -- env CLIPPY_HOME=/var/lib/clippy \
  /opt/clippy/.venv/bin/clippy api-key create --name primary
```

Provider secrets are prompted invisibly:

```bash
runuser -u clippy -- env CLIPPY_HOME=/var/lib/clippy \
  /opt/clippy/.venv/bin/clippy config set-secret gemini
```

Repeat for `groq` and `openrouter`. Refresh YouTube cookies from the Mac with:

```bash
scripts/sync-youtube-cookies.sh chrome pve 103
```

Close the selected browser first if its cookie database is locked. The script
uses a temporary `0600` file, pushes through Proxmox, fixes ownership inside the
LXC, and deletes the temporary Mac copy on exit.

## Verification

```bash
systemctl --no-pager --full status clippy.service clippy-cleanup.timer
ss -lntp
curl --fail http://127.0.0.1:8000/api/v1/health
tailscale serve status
```

`ss` must show Clippy only on `127.0.0.1:8000`. Verify the HTTPS URL from a
second tailnet machine, and verify `192.168.1.103:8000` is unreachable.
