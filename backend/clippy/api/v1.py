"""Stable, authenticated API used by automation and the next Clippy UI."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse

from .. import config, paths, storage
from ..db import store
from ..db.models import Job, Source, new_id
from ..jobs.queue import queue
from ..pipeline import captions, ingest
from ..security import create_api_key, require_api_key
from . import clips as legacy_clips
from . import jobs as legacy_jobs
from . import settings as legacy_settings
from .schemas import (
    ApiKeyCreatedOut,
    ApiKeyCreateIn,
    ApiKeyOut,
    ClipOut,
    ClipPatchIn,
    JobOut,
    RenderJobIn,
    SecretIn,
    SettingsIn,
    SettingsOut,
    SourceOut,
    VideoCreateIn,
    VideoCreateOut,
)

public_router = APIRouter(prefix="/api/v1", tags=["v1"])
router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_api_key)],
)


@public_router.get("/health", tags=["v1"])
async def health() -> dict:
    state = queue.status()
    return {"status": "ok", "queued": state.queued, "running_job": state.running_job_id}


@router.post(
    "/videos", response_model=VideoCreateOut, status_code=status.HTTP_202_ACCEPTED,
    tags=["videos"],
)
async def create_video(payload: VideoCreateIn) -> VideoCreateOut:
    if not ingest.is_youtube_url(payload.youtube_url):
        raise HTTPException(status_code=400, detail="That is not a YouTube URL.")

    source = Source(
        id=new_id(), type="youtube", url=payload.youtube_url,
        path="", title=payload.youtube_url,
    )
    settings = legacy_jobs._apply_overrides(config.load(), payload.options)
    settings.export.ratio = "9:16"
    if settings.clips.min_duration_s >= settings.clips.max_duration_s:
        raise HTTPException(
            status_code=400, detail="Minimum clip length must be below the maximum."
        )
    if not await asyncio.to_thread(storage.enough_space, settings.storage):
        raise HTTPException(status_code=507, detail="Not enough free space for a new video job.")
    job = Job(
        id=new_id(), source_id=source.id, kind="analysis",
        provider="automatic", settings=settings.model_dump(mode="json"),
    )
    await asyncio.to_thread(store.create_source, source)
    await asyncio.to_thread(store.create_job, job)
    queue.notify()
    return VideoCreateOut(video_id=source.id, job_id=job.id)


@router.get("/videos", response_model=list[SourceOut], tags=["videos"])
async def list_videos(limit: int = 50) -> list[SourceOut]:
    sources = await asyncio.to_thread(store.list_sources, min(max(limit, 1), 200))
    return [SourceOut.of(source) for source in sources if source.type == "youtube"]


@router.get("/videos/{video_id}", response_model=SourceOut, tags=["videos"])
async def get_video(video_id: str) -> SourceOut:
    source = await asyncio.to_thread(store.get_source, video_id)
    if source is None or source.type != "youtube":
        raise HTTPException(status_code=404, detail="Video not found.")
    return SourceOut.of(source)


@router.delete("/videos/{video_id}", status_code=204, tags=["videos"])
async def delete_video(video_id: str) -> None:
    source = await asyncio.to_thread(store.get_source, video_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Video not found.")
    jobs = await asyncio.to_thread(store.list_jobs_for_source, video_id)
    if any(job.status in ("queued", "running") for job in jobs):
        raise HTTPException(status_code=409, detail="Cancel active jobs before deleting the video.")
    await asyncio.to_thread(store.delete_source, video_id)
    await asyncio.to_thread(shutil.rmtree, paths.source_media_dir(video_id), True)
    for job in jobs:
        await asyncio.to_thread(shutil.rmtree, paths.job_work_dir(job.id), True)


@router.get("/videos/{video_id}/clips", response_model=list[ClipOut], tags=["videos"])
async def video_clips(video_id: str) -> list[ClipOut]:
    jobs = await asyncio.to_thread(store.list_jobs_for_source, video_id)
    analysis = next((job for job in jobs if job.kind == "analysis"), None)
    if analysis is None:
        source = await asyncio.to_thread(store.get_source, video_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Video not found.")
        return []
    return await legacy_clips.list_clips_for_job(analysis.id)


@router.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
async def get_job(job_id: str) -> JobOut:
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    source = await asyncio.to_thread(store.get_source, job.source_id)
    return JobOut.of(job, source, public_status=True)


@router.get("/jobs/{job_id}/events", tags=["jobs"])
async def job_events(job_id: str, request: Request):
    return await legacy_jobs.job_events(job_id, request, public_status=True)


@router.get("/clips/{clip_id}", response_model=ClipOut, tags=["clips"])
async def get_clip(clip_id: str) -> ClipOut:
    return await legacy_clips.get_clip(clip_id)


@router.patch("/clips/{clip_id}", response_model=ClipOut, tags=["clips"])
async def patch_clip(clip_id: str, payload: ClipPatchIn) -> ClipOut:
    return await legacy_clips.patch_clip(clip_id, payload)


def _new_render_job(clip_id: str, kind: str, payload: RenderJobIn) -> Job:
    clip = store.get_clip(clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    parent = store.get_job(clip.job_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="Analysis job not found.")
    settings = config.load()
    if payload.style:
        try:
            captions.get_style(payload.style)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        settings.export.caption_style = payload.style
    settings.export.ratio = "9:16"
    return store.create_job(
        Job(
            id=new_id(), source_id=parent.source_id, kind=kind,
            clip_id=clip.id, parent_job_id=parent.id,
            settings=settings.model_dump(mode="json"),
        )
    )


@router.post(
    "/clips/{clip_id}/preview", response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED, tags=["clips"],
)
async def preview_clip(clip_id: str, payload: RenderJobIn = RenderJobIn()) -> JobOut:
    job = await asyncio.to_thread(_new_render_job, clip_id, "preview", payload)
    queue.notify()
    return JobOut.of(job, public_status=True)


@router.post(
    "/clips/{clip_id}/render", response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED, tags=["clips"],
)
async def render_clip(clip_id: str, payload: RenderJobIn = RenderJobIn()) -> JobOut:
    job = await asyncio.to_thread(_new_render_job, clip_id, "render", payload)
    queue.notify()
    return JobOut.of(job, public_status=True)


@router.get("/clips/{clip_id}/download", tags=["clips"])
async def download_clip(clip_id: str) -> FileResponse:
    record = await asyncio.to_thread(store.latest_export, clip_id, kind="final")
    if record is None:
        raise HTTPException(status_code=404, detail="This clip has no final render yet.")
    path = Path(record.path)
    if not path.is_file():
        raise HTTPException(status_code=410, detail="The rendered file is missing.")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@router.get("/providers", tags=["providers"])
async def providers() -> dict:
    targets = await asyncio.to_thread(store.list_provider_targets)
    usage = await asyncio.to_thread(store.provider_usage_summary)
    return {
        "targets": [vars(target) for target in targets],
        "usage": usage,
    }


@router.get("/providers/status", tags=["providers"])
async def provider_status():
    return await legacy_settings.providers_status()


@router.get("/settings", response_model=SettingsOut, tags=["settings"])
async def get_settings() -> SettingsOut:
    return await legacy_settings.get_settings()


@router.patch("/settings", response_model=SettingsOut, tags=["settings"])
async def patch_settings(payload: SettingsIn) -> SettingsOut:
    return await legacy_settings.put_settings(payload)


@router.put("/settings/secrets", status_code=204, tags=["settings"])
async def put_secret(payload: SecretIn) -> None:
    return await legacy_settings.put_secret(payload)


@router.delete("/settings/secrets/{key}", status_code=204, tags=["settings"])
async def delete_secret(key: str) -> None:
    return await legacy_settings.delete_secret(key)


@router.get("/storage", tags=["storage"])
async def storage_status() -> dict:
    return await asyncio.to_thread(storage.status)


@router.post("/storage/cleanup", tags=["storage"])
async def cleanup_storage() -> dict:
    return await asyncio.to_thread(storage.cleanup, config.load().storage)


@router.get("/api-keys", response_model=list[ApiKeyOut], tags=["api-keys"])
async def api_keys() -> list[ApiKeyOut]:
    records = await asyncio.to_thread(store.list_api_keys)
    return [
        ApiKeyOut(
            **{key: value for key, value in vars(item).items() if key != "token_hash"}
        )
        for item in records
    ]


@router.post(
    "/api-keys", response_model=ApiKeyCreatedOut,
    status_code=status.HTTP_201_CREATED, tags=["api-keys"],
)
async def new_api_key(payload: ApiKeyCreateIn) -> ApiKeyCreatedOut:
    record, token = await asyncio.to_thread(create_api_key, payload.name)
    return ApiKeyCreatedOut(
        id=record.id, name=record.name, prefix=record.prefix,
        created_at=record.created_at, token=token,
    )


@router.delete("/api-keys/{key_id}", status_code=204, tags=["api-keys"])
async def revoke_api_key(key_id: str) -> None:
    if not await asyncio.to_thread(store.revoke_api_key, key_id):
        raise HTTPException(status_code=404, detail="API key not found.")
