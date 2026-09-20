"""Incoming API-key creation and verification.

Tokens contain 256 bits of randomness, so a SHA-256 digest is sufficient: the
database never stores the bearer value and an offline brute-force attack is not
practical. Comparisons still use ``compare_digest`` to avoid timing leaks.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets

from fastapi import HTTPException, Request, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import store
from .db.models import ApiKey, new_id

_bearer = HTTPBearer(auto_error=False)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_api_key(name: str) -> tuple[ApiKey, str]:
    token = "clp_" + secrets.token_urlsafe(32)
    record = ApiKey(
        id=new_id(),
        name=name.strip() or "API key",
        prefix=token[:12],
        token_hash=hash_token(token),
    )
    store.create_api_key(record)
    return record, token


async def require_api_key(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> ApiKey:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A Clippy Bearer API key is required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    digest = hash_token(credentials.credentials)
    record = store.find_api_key(digest)
    if record is None or not hmac.compare_digest(record.token_hash, digest):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked Clippy API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    store.touch_api_key(record.id)
    return record


TAILSCALE_USERS_ENV = "CLIPPY_TAILSCALE_USERS"


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host == "localhost"


def _tailscale_identity(request: Request) -> str | None:
    """Return the trusted Serve identity, never an arbitrary client header.

    Tailscale Serve strips spoofed identity headers before adding its own.  The
    remaining trust boundary is the reverse-proxy hop, so we only accept the
    header when FastAPI sees a loopback peer.  Clippy's production service is
    deliberately bound to 127.0.0.1 for this reason.
    """

    if not _is_loopback(request.client.host if request.client else None):
        return None
    login = request.headers.get("Tailscale-User-Login", "").strip()
    if not login:
        return None
    allowed = {
        item.strip().casefold()
        for item in os.environ.get(TAILSCALE_USERS_ENV, "").split(",")
        if item.strip()
    }
    if allowed and login.casefold() not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Tailscale user not allowed."
        )
    return login


async def require_access(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer),
) -> ApiKey | str:
    """Authorize either an automation Bearer key or a Tailscale web user."""

    if credentials is not None:
        return await require_api_key(credentials)
    identity = _tailscale_identity(request)
    if identity:
        return identity
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="A Clippy Bearer API key or Tailscale Serve identity is required.",
        headers={"WWW-Authenticate": "Bearer"},
    )
