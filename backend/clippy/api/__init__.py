"""REST API routers (v1 only)."""

from __future__ import annotations

from fastapi import APIRouter

from . import v1

__all__ = ["api_router"]

api_router = APIRouter()
api_router.include_router(v1.public_router)
api_router.include_router(v1.router)
