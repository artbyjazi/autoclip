"""REST API routers."""

from __future__ import annotations

from fastapi import APIRouter

from . import clips, jobs, settings, sources, v1

__all__ = ["api_router"]

api_router = APIRouter()
api_router.include_router(sources.router)
api_router.include_router(jobs.router)
api_router.include_router(clips.router)
api_router.include_router(settings.router)
api_router.include_router(v1.public_router)
api_router.include_router(v1.router)
