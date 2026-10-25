"""Source ingestion endpoints."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..config import IngestSettings
from ..config import load as load_settings
from ..db import store
from ..db.models import new_id
from ..pipeline import ingest
from .schemas import DownloadOut, SourceOut, UrlIngestIn, YouTubeIngestIn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sources", tags=["sources"])

#: Uploads stream to disk in chunks rather than being buffered whole — source
#: videos routinely exceed available RAM.
UPLOAD_CHUNK = 4 * 1024 * 1024


@router.get("", response_model=list[SourceOut])
async def list_sources(limit: int = 50) -> list[SourceOut]:
    sources = await asyncio.to_thread(store.list_sources, limit)
    return [SourceOut.of(s) for s in sources]


@router.get("/{source_id}", response_model=SourceOut)
async def get_source(source_id: str) -> SourceOut:
    source = await asyncio.to_thread(store.get_source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")
    return SourceOut.of(source)


@router.post("/youtube", response_model=SourceOut, status_code=201)
async def ingest_youtube(payload: YouTubeIngestIn) -> SourceOut:
    """Download a YouTube video and register it as a source."""
    if not ingest.is_youtube_url(payload.url):
        raise HTTPException(status_code=400, detail="That is not a YouTube URL.")
    return await _ingest_url(payload)


@router.post("/url", response_model=SourceOut, status_code=201)
async def ingest_url(payload: UrlIngestIn) -> SourceOut:
    """Download a YouTube or Twitch video and register it as a source."""
    if not ingest.is_supported_url(payload.url):
        raise HTTPException(status_code=400, detail="That is not a YouTube or Twitch URL.")
    return await _ingest_url(payload)


async def _ingest_url(payload: UrlIngestIn) -> SourceOut:
    settings = load_settings().ingest
    if payload.cookies_from_browser is not None:
        settings = settings.model_copy(
            update={"cookies_from_browser": payload.cookies_from_browser}
        )

    try:
        source = await asyncio.to_thread(ingest.ingest_url, payload.url, settings)
    except ingest.IngestError as exc:
        # 422 rather than 500: the request was well-formed, but the content
        # can't be fetched, and the hint tells the user what to change.
        raise HTTPException(
            status_code=422, detail={"message": str(exc), "hint": exc.hint}
        ) from exc

    await asyncio.to_thread(store.create_source, source)
    return SourceOut.of(source)


@router.post("/upload", response_model=SourceOut, status_code=201)
async def upload_source(file: UploadFile = File(...)) -> SourceOut:
    """Accept a media upload and register it as a source."""
    filename = Path(file.filename or "upload")
    suffix = filename.suffix.lower()
    if suffix not in ingest.ACCEPTED_SUFFIXES:
        raise HTTPException(
            status_code=415,
            detail={
                "message": f"{suffix or 'This file'} is not a supported format.",
                "hint": f"Accepted: {', '.join(sorted(ingest.ACCEPTED_SUFFIXES))}",
            },
        )

    # Stage to a temp file first so a failed or abandoned upload never leaves a
    # half-written file in the media store.
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as staging:
        staging_path = Path(staging.name)
        try:
            while chunk := await file.read(UPLOAD_CHUNK):
                staging.write(chunk)
        except Exception as exc:
            staging_path.unlink(missing_ok=True)
            raise HTTPException(status_code=500, detail="Upload failed.") from exc

    try:
        source = await asyncio.to_thread(
            ingest.ingest_file, staging_path, move=True, title=filename.stem
        )
    except ingest.IngestError as exc:
        staging_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=422, detail={"message": str(exc), "hint": exc.hint}
        ) from exc
    finally:
        shutil.rmtree(staging_path.parent / staging_path.name, ignore_errors=True)

    await asyncio.to_thread(store.create_source, source)
    return SourceOut.of(source)


# --------------------------------------------------------------------------
# Background URL downloads
#
# A long VOD can take a long time to download, and a request that blocks for
# all of it shows the user nothing. These endpoints start the download in a
# thread and let the UI poll for progress. State lives in memory: a download
# dies with the server process anyway, so there's nothing worth persisting.
# --------------------------------------------------------------------------

_downloads: dict[str, DownloadOut] = {}
_downloads_lock = threading.Lock()


@router.post("/downloads", response_model=DownloadOut, status_code=202)
async def start_download(payload: UrlIngestIn) -> DownloadOut:
    """Start downloading a YouTube or Twitch URL; poll GET /downloads/{id}."""
    if not ingest.is_supported_url(payload.url):
        raise HTTPException(status_code=400, detail="That is not a YouTube or Twitch URL.")

    with _downloads_lock:
        # A second submit of the same link joins the first rather than
        # downloading the whole video again alongside it.
        for existing in _downloads.values():
            if existing.url == payload.url and existing.status == "running":
                return existing.model_copy()
        download = DownloadOut(id=new_id(), url=payload.url, status="running")
        _downloads[download.id] = download

    settings = load_settings().ingest
    if payload.cookies_from_browser is not None:
        settings = settings.model_copy(
            update={"cookies_from_browser": payload.cookies_from_browser}
        )

    threading.Thread(
        target=_run_download, args=(download.id, payload.url, settings), daemon=True
    ).start()
    return download.model_copy()


@router.get("/downloads/{download_id}", response_model=DownloadOut)
async def get_download(download_id: str) -> DownloadOut:
    with _downloads_lock:
        download = _downloads.get(download_id)
        if download is None:
            raise HTTPException(status_code=404, detail="Download not found.")
        return download.model_copy()


def _run_download(download_id: str, url: str, settings: IngestSettings) -> None:
    def on_progress(fraction: float) -> None:
        with _downloads_lock:
            _downloads[download_id].progress = fraction

    try:
        source = ingest.ingest_url(url, settings, on_progress=on_progress)
        store.create_source(source)
    except ingest.IngestError as exc:
        # args[0], not str(): IngestError.__str__ appends the hint.
        error = {"message": exc.args[0], "hint": exc.hint}
    except Exception as exc:
        log.exception("Download of %s failed", url)
        error = {"message": f"Download failed: {exc}", "hint": ""}
    else:
        with _downloads_lock:
            current = _downloads[download_id]
            current.status, current.progress = "done", 1.0
            current.source = SourceOut.of(source)
        return

    with _downloads_lock:
        current = _downloads[download_id]
        current.status, current.error = "failed", error
