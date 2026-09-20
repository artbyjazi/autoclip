"""FastAPI application — REST API plus the built frontend, on one port.

Serving the compiled React bundle from the same origin as the API is what keeps
Clippy a single process with no CORS configuration, no second port, and no
Node.js at runtime.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__, db, paths
from .api import api_router
from .jobs.events import broker
from .jobs.queue import queue

log = logging.getLogger(__name__)

#: Set to "1" to serve the API without starting the background job worker.
ENV_NO_WORKER = "CLIPPY_NO_WORKER"

#: Where the built frontend is looked for, in priority order. The first is the
#: packaged location (inside the wheel); the second is the dev build output.
_STATIC_CANDIDATES = (
    Path(__file__).resolve().parent / "static",
    Path(__file__).resolve().parents[2] / "frontend" / "dist",
)


def static_dir() -> Path | None:
    for candidate in _STATIC_CANDIDATES:
        if (candidate / "index.html").exists():
            return candidate
    return None


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    import asyncio

    paths.ensure_layout()
    db.init()
    from .providers.router import seed_default_targets

    seed_default_targets()

    broker.bind_loop(asyncio.get_running_loop())

    # Serving the API without a worker is useful for debugging a stuck queue and
    # makes API tests deterministic — jobs stay queued instead of racing off.
    worker_enabled = os.environ.get(ENV_NO_WORKER) != "1"
    if worker_enabled:
        await queue.start()
    else:
        log.info("Job worker disabled by %s=1.", ENV_NO_WORKER)

    log.info("Clippy %s ready. Artifacts in %s", __version__, paths.root())

    try:
        yield
    finally:
        if worker_enabled:
            await queue.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Clippy",
        version=__version__,
        description="Local-first AI video clipper.",
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    app.include_router(api_router)

    @app.get("/api/health")
    async def health() -> dict:
        status = queue.status()
        return {
            "status": "ok",
            "version": __version__,
            "running_job": status.running_job_id,
            "queued": status.queued,
        }

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    directory = static_dir()

    if directory is None:

        @app.get("/", include_in_schema=False)
        async def missing_frontend() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "error": "The frontend has not been built.",
                    "hint": (
                        "Run `npm install && npm run build` in the frontend/ "
                        "directory, then restart. The API itself is available "
                        "at /docs."
                    ),
                },
            )

        log.warning("No built frontend found; serving the API only.")
        return

    app.mount(
        "/assets",
        StaticFiles(directory=directory / "assets"),
        name="assets",
    )

    # response_model=None because the return type is a union of Response
    # subclasses, which FastAPI would otherwise try to turn into a Pydantic
    # response model and reject.
    @app.get("/{full_path:path}", include_in_schema=False, response_model=None)
    async def spa(request: Request, full_path: str) -> FileResponse | JSONResponse:
        """Serve the SPA, letting the client router own unknown paths.

        API routes are registered first and win; anything else that isn't a real
        file falls through to index.html so a deep link like /jobs/abc123 works
        on a hard refresh.
        """
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found."})

        candidate = directory / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)

        return FileResponse(directory / "index.html")

    log.info("Serving the frontend from %s", directory)


app = create_app()
