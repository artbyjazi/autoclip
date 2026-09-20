"""Job lifecycle and the SSE progress stream."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request
from sse_starlette.sse import EventSourceResponse

from ..config import load as load_settings
from ..db import store
from ..db.models import Job, new_id
from ..jobs.events import broker
from ..jobs.queue import queue
from .schemas import JobCreateIn, JobOut, JobSettingsIn

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _apply_overrides(settings, overrides: JobSettingsIn):
    """Layer per-job overrides on top of saved settings."""
    merged = settings.model_copy(deep=True)

    if overrides.provider:
        merged.active_provider = overrides.provider
    if overrides.whisper_model:
        merged.whisper.model = overrides.whisper_model
    if overrides.language is not None:
        merged.whisper.language = overrides.language
    if overrides.diarization is not None:
        merged.whisper.diarization = overrides.diarization
    if overrides.min_duration_s is not None:
        merged.clips.min_duration_s = overrides.min_duration_s
    if overrides.max_duration_s is not None:
        merged.clips.max_duration_s = overrides.max_duration_s
    if overrides.max_clips is not None:
        merged.clips.max_clips = overrides.max_clips
    if overrides.caption_style:
        merged.export.caption_style = overrides.caption_style
    if overrides.ratio:
        merged.export.ratio = overrides.ratio

    return merged


@router.post("", response_model=JobOut, status_code=201)
async def create_job(payload: JobCreateIn) -> JobOut:
    source = await asyncio.to_thread(store.get_source, payload.source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="Source not found.")

    settings = _apply_overrides(load_settings(), payload.settings)
    if settings.clips.min_duration_s >= settings.clips.max_duration_s:
        raise HTTPException(
            status_code=400, detail="Minimum clip length must be below the maximum."
        )

    job = Job(
        id=new_id(),
        source_id=source.id,
        provider=settings.active_provider,
        settings=settings.model_dump(mode="json"),
    )
    await asyncio.to_thread(store.create_job, job)
    queue.notify()

    return JobOut.of(job, source)


@router.get("", response_model=list[JobOut])
async def list_jobs(limit: int = 25) -> list[JobOut]:
    jobs = await asyncio.to_thread(store.list_jobs, limit)
    out: list[JobOut] = []
    for job in jobs:
        source = await asyncio.to_thread(store.get_source, job.source_id)
        out.append(JobOut.of(job, source))
    return out


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: str) -> JobOut:
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    source = await asyncio.to_thread(store.get_source, job.source_id)
    return JobOut.of(job, source)


@router.get("/{job_id}/events")
async def job_events(
    job_id: str, request: Request, public_status: bool = False
) -> EventSourceResponse:
    """Stream progress for a job as Server-Sent Events."""
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    async def stream():
        # Send the current state immediately: a client connecting mid-job (or
        # reconnecting) must not wait for the next progress tick to render.
        current = await asyncio.to_thread(store.get_job, job_id)
        if current is not None:
            yield {
                "event": "snapshot",
                "data": JobOut.of(current, public_status=public_status).model_dump_json(),
            }
            if current.status in ("done", "failed", "cancelled"):
                return

        async for event in broker.subscribe(job_id):
            if await request.is_disconnected():
                break
            yield {"event": event.type, "data": event.to_sse().split("data: ", 1)[-1].strip()}

    return EventSourceResponse(stream())


@router.post("/{job_id}/cancel", response_model=JobOut)
async def cancel_job(job_id: str) -> JobOut:
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    if not queue.cancel(job_id):
        raise HTTPException(
            status_code=409, detail=f"Job is already {job.status}; nothing to cancel."
        )

    updated = await asyncio.to_thread(store.get_job, job_id)
    return JobOut.of(updated or job)


@router.post("/{job_id}/retry", response_model=JobOut)
async def retry_job(job_id: str) -> JobOut:
    """Requeue a failed or cancelled job.

    Completed stages left their artifacts in ``work/{job_id}/``, so the retry
    resumes at the stage that failed rather than starting over.
    """
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.status not in ("failed", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"Only failed or cancelled jobs can be retried (this one is {job.status}).",
        )

    await asyncio.to_thread(store.update_job, job_id, status="queued", error=None, progress=0.0)
    queue.notify()

    updated = await asyncio.to_thread(store.get_job, job_id)
    return JobOut.of(updated or job)


@router.get("/{job_id}/clips")
async def job_clips(job_id: str):
    from .clips import list_clips_for_job

    return await list_clips_for_job(job_id)
