"""Incoming API-key creation and verification.

Tokens contain 256 bits of randomness, so a SHA-256 digest is sufficient: the
database never stores the bearer value and an offline brute-force attack is not
practical. Comparisons still use ``compare_digest`` to avoid timing leaks.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import store
from .db.models import ApiKey, new_id

_bearer = HTTPBearer(auto_error=False)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_api_key(name: str) -> tuple[ApiKey, str]:
    token = "clp_" + secrets.token_urlsafe(32)
    record = ApiKey(
        id=new_id(), name=name.strip() or "API key",
        prefix=token[:12], token_hash=hash_token(token),
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
