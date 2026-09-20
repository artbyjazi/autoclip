"""Stable, authenticated API used by automation and the Clippy UI.

This is the only REST surface: the deprecated ``/api/*`` aliases were removed
after the frontend migration. Everything here requires either a Bearer key or
a trusted Tailscale Serve identity except ``GET /api/v1/health``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import FileResponse
from sse_starlette.sse import EventSourceResponse

from .. import config, paths, storage
from ..db import store
from ..db.models import ClipEdit, Job, Source, new_id
from ..jobs.events import broker
from ..jobs.queue import queue
from ..pipeline import captions, ingest
from ..pipeline.runner import JobWorkspace
from ..pipeline.transcript import Transcript
from ..providers import PROVIDERS, build_provider
from ..providers.base import ProviderStatus
from ..security import create_api_key, require_access
from .schemas import (
    ApiKeyCreatedOut,
    ApiKeyCreateIn,
    ApiKeyOut,
    ArtifactStateOut,
    ClipOut,
    ClipPatchIn,
    ClipTranscriptOut,
    ExportOut,
    JobOut,
    JobSettingsIn,
    ProviderStatusOut,
    RenderJobIn,
    SecretIn,
    SettingsIn,
    SettingsOut,
    SourceOut,
    VideoCreateIn,
    VideoCreateOut,
    VideoSummaryOut,
    WordOut,
)

log = logging.getLogger(__name__)

public_router = APIRouter(prefix="/api/v1", tags=["v1"])
router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(require_access)],
)


def apply_job_overrides(settings: config.Settings, overrides: JobSettingsIn) -> config.Settings:
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
    if overrides.auto_preview is not None:
        merged.clips.auto_preview = overrides.auto_preview
    if overrides.caption_style:
        merged.export.caption_style = overrides.caption_style
    if overrides.ratio:
        merged.export.ratio = overrides.ratio

    return merged


def _artifact_state(clip_id: str, kind: str, edited_at: str | None = None) -> ArtifactStateOut:
    exported = store.latest_export(clip_id, kind=kind)
    if exported is not None and Path(exported.path).is_file():
        artifact_status = "stale" if edited_at and edited_at > exported.created_at else "ready"
        return ArtifactStateOut(
            status=artifact_status,
            job_id=exported.job_id,
            export=ExportOut.of(exported),
        )
    if exported is not None:
        return ArtifactStateOut(
            status="failed",
            job_id=exported.job_id,
            export=ExportOut.of(exported),
            error="The exported file is no longer available.",
        )
    job_kind = "render" if kind == "final" else kind
    jobs = [
        job for job in store.list_jobs(limit=500) if job.clip_id == clip_id and job.kind == job_kind
    ]
    if not jobs:
        return ArtifactStateOut(status="missing")
    job = jobs[0]
    status_name = "ready" if job.status == "done" else job.status
    return ArtifactStateOut(status=status_name, job_id=job.id, error=job.error)


def _clip_out(clip) -> ClipOut:
    edit = store.get_clip_edit(clip.id)
    exports = store.list_exports(clip.id)
    return ClipOut.of(
        clip,
        edit=edit,
        exports=exports,
        preview=_artifact_state(clip.id, "preview", edit.updated_at if edit else None),
        final=_artifact_state(clip.id, "final", edit.updated_at if edit else None),
        caption_style=_clip_style(clip, edit),
    )


def _clip_style(clip, edit: ClipEdit | None = None) -> str:
    if edit is not None:
        return captions.get_style(edit.caption_style).key
    analysis = store.get_job(clip.job_id)
    settings = (
        config.Settings.model_validate(analysis.settings)
        if analysis is not None and analysis.settings
        else config.load()
    )
    return captions.get_style(settings.export.caption_style).key


def _video_summary(source: Source) -> VideoSummaryOut:
    jobs = store.list_jobs_for_source(source.id)
    analysis = next((job for job in jobs if job.kind == "analysis"), None)
    clips = store.list_clips(analysis.id) if analysis else []
    return VideoSummaryOut(
        **SourceOut.of(source).model_dump(),
        latest_analysis_job=JobOut.of(analysis, source, public_status=True) if analysis else None,
        clip_count=len(clips),
        preview_ready_count=sum(
            _artifact_state(clip.id, "preview").status == "ready" for clip in clips
        ),
        final_ready_count=sum(
            _artifact_state(clip.id, "final").status == "ready" for clip in clips
        ),
    )


async def list_clips_for_job(job_id: str) -> list[ClipOut]:
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    clips = await asyncio.to_thread(store.list_clips, job_id)
    return [await asyncio.to_thread(_clip_out, clip) for clip in clips]


def _load_transcript(job_id: str) -> Transcript:
    workspace = JobWorkspace(job_id)
    if not workspace.transcript.exists():
        raise HTTPException(status_code=404, detail="This job has no transcript yet.")
    return Transcript.load(workspace.transcript)


def settings_out(settings: config.Settings) -> SettingsOut:
    payload = settings.model_dump(mode="json")
    ingest_settings = payload["ingest"]
    # The cookie file is an operator-controlled server path. Returning it would
    # disclose filesystem layout and make a write-only credential accidentally
    # readable through the settings API.
    cookies_file = ingest_settings.pop("cookies_file", "")
    ingest_settings["cookies_file_configured"] = bool(cookies_file)
    return SettingsOut(
        active_provider=payload["active_provider"],
        providers=payload["providers"],
        whisper=payload["whisper"],
        clips=payload["clips"],
        ingest=ingest_settings,
        export=payload["export"],
        storage=payload["storage"],
        insecure_secret_storage=payload["insecure_secret_storage"],
        keys_present={
            name: config.get_secret(name, settings) is not None for name in config.KEYED_PROVIDERS
        }
        | {config.HF_TOKEN_KEY: config.get_secret(config.HF_TOKEN_KEY, settings) is not None},
    )


@public_router.get("/health", tags=["v1"])
async def health() -> dict:
    state = queue.status()
    return {"status": "ok", "queued": state.queued, "running_job": state.running_job_id}


@router.post(
    "/videos",
    response_model=VideoCreateOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["videos"],
)
async def create_video(payload: VideoCreateIn) -> VideoCreateOut:
    if not ingest.is_youtube_url(payload.youtube_url):
        raise HTTPException(status_code=400, detail="That is not a YouTube URL.")

    source = Source(
        id=new_id(),
        type="youtube",
        url=payload.youtube_url,
        path="",
        title=payload.youtube_url,
    )
    settings = apply_job_overrides(config.load(), payload.options)
    settings.export.ratio = "9:16"
    if settings.clips.min_duration_s >= settings.clips.max_duration_s:
        raise HTTPException(
            status_code=400, detail="Minimum clip length must be below the maximum."
        )
    if not await asyncio.to_thread(storage.enough_space, settings.storage):
        raise HTTPException(status_code=507, detail="Not enough free space for a new video job.")
    job = Job(
        id=new_id(),
        source_id=source.id,
        kind="analysis",
        provider="automatic",
        settings=settings.model_dump(mode="json"),
    )
    await asyncio.to_thread(store.create_source, source)
    await asyncio.to_thread(store.create_job, job)
    queue.notify()
    return VideoCreateOut(video_id=source.id, job_id=job.id)


@router.get("/videos", response_model=list[VideoSummaryOut], tags=["videos"])
async def list_videos(limit: int = 50) -> list[VideoSummaryOut]:
    sources = await asyncio.to_thread(store.list_sources, min(max(limit, 1), 200))
    return [
        await asyncio.to_thread(_video_summary, source)
        for source in sources
        if source.type == "youtube"
    ]


@router.get("/videos/{video_id}", response_model=VideoSummaryOut, tags=["videos"])
async def get_video(video_id: str) -> VideoSummaryOut:
    source = await asyncio.to_thread(store.get_source, video_id)
    if source is None or source.type != "youtube":
        raise HTTPException(status_code=404, detail="Video not found.")
    return await asyncio.to_thread(_video_summary, source)


def _link_or_copy(source: Path, destination: Path) -> None:
    if not source.is_file() or destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


@router.post(
    "/videos/{video_id}/reanalyze",
    response_model=VideoCreateOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["videos"],
)
async def reanalyze_video(
    video_id: str, payload: JobSettingsIn = JobSettingsIn()
) -> VideoCreateOut:
    source = await asyncio.to_thread(store.get_source, video_id)
    if source is None or source.type != "youtube":
        raise HTTPException(status_code=404, detail="Video not found.")
    jobs = await asyncio.to_thread(store.list_jobs_for_source, video_id)
    previous = next((job for job in jobs if job.kind == "analysis" and job.status == "done"), None)
    settings = apply_job_overrides(config.load(), payload)
    settings.export.ratio = "9:16"
    job = Job(
        id=new_id(),
        source_id=source.id,
        kind="analysis",
        provider="automatic",
        parent_job_id=previous.id if previous else None,
        settings=settings.model_dump(mode="json"),
    )
    if previous:
        old = JobWorkspace(previous.id)
        new = JobWorkspace(job.id)
        for src, dst in (
            (old.audio, new.audio),
            (old.transcript, new.transcript),
            (old.silences, new.silences),
        ):
            await asyncio.to_thread(_link_or_copy, src, dst)
    await asyncio.to_thread(store.create_job, job)
    queue.notify()
    return VideoCreateOut(video_id=source.id, job_id=job.id)


@router.get("/videos/{video_id}/media", tags=["videos"])
async def video_media(video_id: str) -> FileResponse:
    source = await asyncio.to_thread(store.get_source, video_id)
    if source is None or source.type != "youtube":
        raise HTTPException(status_code=404, detail="Video not found.")
    path = Path(source.path)
    if not source.path or not path.is_file():
        raise HTTPException(
            status_code=410, detail="The retained source video is no longer available."
        )
    return FileResponse(path, media_type="video/mp4")


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
    return await list_clips_for_job(analysis.id)


@router.get("/jobs/{job_id}", response_model=JobOut, tags=["jobs"])
async def get_job(job_id: str) -> JobOut:
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    source = await asyncio.to_thread(store.get_source, job.source_id)
    return JobOut.of(job, source, public_status=True)


@router.get("/jobs/{job_id}/events", tags=["jobs"])
async def job_events(job_id: str, request: Request) -> EventSourceResponse:
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
                "data": JobOut.of(current, public_status=True).model_dump_json(),
            }
            if current.status in ("done", "failed", "cancelled"):
                return

        async for event in broker.subscribe(job_id):
            if await request.is_disconnected():
                break
            yield {"event": event.type, "data": event.to_sse().split("data: ", 1)[-1].strip()}

    return EventSourceResponse(stream())


@router.post("/jobs/{job_id}/retry", response_model=JobOut, tags=["jobs"])
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
    return JobOut.of(updated or job, public_status=True)


@router.post("/jobs/{job_id}/cancel", response_model=JobOut, tags=["jobs"])
async def cancel_job(job_id: str) -> JobOut:
    job = await asyncio.to_thread(store.get_job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if not queue.cancel(job_id):
        raise HTTPException(status_code=409, detail="This job is already finished.")
    updated = await asyncio.to_thread(store.get_job, job_id)
    return JobOut.of(updated or job, public_status=True)


@router.get("/clips/{clip_id}", response_model=ClipOut, tags=["clips"])
async def get_clip(clip_id: str) -> ClipOut:
    clip = await asyncio.to_thread(store.get_clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    return await asyncio.to_thread(_clip_out, clip)


@router.get("/clips/{clip_id}/transcript", response_model=ClipTranscriptOut, tags=["clips"])
async def clip_transcript(clip_id: str) -> ClipTranscriptOut:
    clip = await asyncio.to_thread(store.get_clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    edit = await asyncio.to_thread(store.get_clip_edit, clip.id)
    if edit and edit.edited_words:
        words = edit.edited_words
        offset = clip.start_word
    else:
        transcript = await asyncio.to_thread(_load_transcript, clip.job_id)
        selected = transcript.words[clip.start_word : clip.end_word + 1]
        words = [
            {"text": word.text, "start": word.start, "end": word.end, "speaker": word.speaker}
            for word in selected
        ]
        offset = clip.start_word
    output = [WordOut(index=offset + i, **word) for i, word in enumerate(words)]
    return ClipTranscriptOut(text=" ".join(word.text.strip() for word in output), words=output)


@router.patch("/clips/{clip_id}", response_model=ClipOut, tags=["clips"])
async def patch_clip(clip_id: str, payload: ClipPatchIn) -> ClipOut:
    """Trim, retitle, or keep/discard a clip.

    A trim re-derives the word range from the new times, so captions stay in
    step with the boundaries without the client having to compute indices.
    """
    clip = await asyncio.to_thread(store.get_clip, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")

    start_s = payload.start_s if payload.start_s is not None else clip.start_s
    end_s = payload.end_s if payload.end_s is not None else clip.end_s
    if end_s <= start_s:
        raise HTTPException(status_code=400, detail="End must come after start.")
    if payload.caption_style is not None:
        try:
            captions.get_style(payload.caption_style)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    trimmed = payload.start_s is not None or payload.end_s is not None
    start_word = end_word = None
    existing = await asyncio.to_thread(store.get_clip_edit, clip_id)
    requested_style = (
        captions.get_style(payload.caption_style).key if payload.caption_style is not None else None
    )
    style_changed = requested_style is not None and requested_style != _clip_style(clip, existing)

    if trimmed:
        transcript = await asyncio.to_thread(_load_transcript, clip.job_id)
        first, last = transcript.indices_in_range(start_s, end_s)
        start_word, end_word = first, max(first, last - 1)

    await asyncio.to_thread(
        store.update_clip,
        clip_id,
        start_s=start_s if trimmed else None,
        end_s=end_s if trimmed else None,
        start_word=start_word,
        end_word=end_word,
        title=payload.title,
        status=payload.status,
        user_trimmed=True if trimmed else None,
    )

    if trimmed or style_changed:
        await asyncio.to_thread(
            store.upsert_clip_edit,
            ClipEdit(
                clip_id=clip_id,
                edited_words=existing.edited_words if existing else None,
                caption_style=requested_style or _clip_style(clip, existing),
                ratio=existing.ratio if existing else "9:16",
            ),
        )

    updated = await asyncio.to_thread(store.get_clip, clip_id)
    return await asyncio.to_thread(_clip_out, updated)


def _new_render_job(clip_id: str, kind: str, payload: RenderJobIn) -> Job:
    clip = store.get_clip(clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found.")
    parent = store.get_job(clip.job_id)
    if parent is None:
        raise HTTPException(status_code=404, detail="Analysis job not found.")
    settings = config.Settings.model_validate(parent.settings) if parent.settings else config.load()
    edit = store.get_clip_edit(clip.id)
    if payload.style:
        try:
            requested_style = captions.get_style(payload.style).key
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        settings.export.caption_style = requested_style
        if requested_style != _clip_style(clip, edit):
            store.upsert_clip_edit(
                ClipEdit(
                    clip_id=clip.id,
                    edited_words=edit.edited_words if edit else None,
                    caption_style=requested_style,
                    ratio=edit.ratio if edit else "9:16",
                )
            )
    elif edit:
        settings.export.caption_style = edit.caption_style
    settings.export.ratio = "9:16"
    return store.create_job(
        Job(
            id=new_id(),
            source_id=parent.source_id,
            kind=kind,
            clip_id=clip.id,
            parent_job_id=parent.id,
            settings=settings.model_dump(mode="json"),
        )
    )


@router.post(
    "/clips/{clip_id}/preview",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["clips"],
)
async def preview_clip(clip_id: str, payload: RenderJobIn = RenderJobIn()) -> JobOut:
    job = await asyncio.to_thread(_new_render_job, clip_id, "preview", payload)
    queue.notify()
    return JobOut.of(job, public_status=True)


@router.post(
    "/clips/{clip_id}/render",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["clips"],
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


@router.get("/exports/{export_id}/download", tags=["clips"])
async def download_export(export_id: str) -> FileResponse:
    """Download any single export (preview or final) by its id."""
    record = await asyncio.to_thread(store.get_export, export_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Export not found.")

    path = Path(record.path)
    if not path.exists():
        raise HTTPException(status_code=410, detail="The exported file has been moved or deleted.")

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
async def providers_status() -> list[ProviderStatusOut]:
    """Live health for every provider, checked concurrently."""
    settings = config.load()

    async def check(name: str) -> ProviderStatusOut:
        provider_cls = PROVIDERS[name]
        has_key = (
            config.get_secret(name, settings) is not None if provider_cls.requires_key else True
        )
        try:
            provider = build_provider(name, settings)
            provider_status: ProviderStatus = await provider.health_check()
        except Exception as exc:
            log.warning("Provider %s health check failed: %s", name, exc)
            provider_status = ProviderStatus(name=name, available=False, detail=str(exc)[:200])

        return ProviderStatusOut(
            name=name,
            available=provider_status.available,
            detail=provider_status.detail,
            models=provider_status.models,
            requires_key=provider_cls.requires_key,
            has_key=has_key,
        )

    return list(await asyncio.gather(*(check(name) for name in PROVIDERS)))


@router.get("/settings", response_model=SettingsOut, tags=["settings"])
async def get_settings() -> SettingsOut:
    return settings_out(config.load())


@router.patch("/settings", response_model=SettingsOut, tags=["settings"])
async def patch_settings(payload: SettingsIn) -> SettingsOut:
    """Merge a partial settings update and persist it."""
    settings = config.load()
    updates = payload.model_dump(exclude_none=True)

    if "active_provider" in updates:
        if updates["active_provider"] not in PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown provider. Available: {', '.join(PROVIDERS)}",
            )
        settings.active_provider = updates["active_provider"]

    for section in ("whisper", "clips", "ingest", "export", "storage"):
        if section in updates:
            if section == "ingest" and "cookies_file" in updates[section]:
                raise HTTPException(
                    status_code=400,
                    detail="cookies_file is server-managed and cannot be changed by API.",
                )
            current = getattr(settings, section)
            try:
                setattr(
                    settings,
                    section,
                    current.model_copy(update=updates[section]).model_validate(
                        current.model_dump() | updates[section]
                    ),
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=400, detail=f"Invalid {section} settings: {exc}"
                ) from exc

    if "providers" in updates:
        for name, values in updates["providers"].items():
            provider_settings = settings.provider(name)
            settings.providers[name] = provider_settings.model_copy(update=values)

    if settings.clips.min_duration_s >= settings.clips.max_duration_s:
        raise HTTPException(
            status_code=400, detail="Minimum clip length must be below the maximum."
        )

    config.save(settings)
    return settings_out(settings)


@router.put("/settings/secrets", status_code=204, tags=["settings"])
async def put_secret(payload: SecretIn) -> None:
    """Store an API key or token. Values are write-only — never read back."""
    valid = (*config.KEYED_PROVIDERS, config.HF_TOKEN_KEY)
    if payload.key not in valid:
        raise HTTPException(
            status_code=400, detail=f"Unknown secret. Expected one of: {', '.join(valid)}"
        )
    if not payload.value.strip():
        raise HTTPException(status_code=400, detail="The value cannot be empty.")

    config.set_secret(payload.key, payload.value.strip())


@router.delete("/settings/secrets/{key}", status_code=204, tags=["settings"])
async def delete_secret(key: str) -> None:
    config.delete_secret(key)


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
        ApiKeyOut(**{key: value for key, value in vars(item).items() if key != "token_hash"})
        for item in records
    ]


@router.post(
    "/api-keys",
    response_model=ApiKeyCreatedOut,
    status_code=status.HTTP_201_CREATED,
    tags=["api-keys"],
)
async def new_api_key(payload: ApiKeyCreateIn) -> ApiKeyCreatedOut:
    record, token = await asyncio.to_thread(create_api_key, payload.name)
    return ApiKeyCreatedOut(
        id=record.id,
        name=record.name,
        prefix=record.prefix,
        created_at=record.created_at,
        token=token,
    )


@router.delete("/api-keys/{key_id}", status_code=204, tags=["api-keys"])
async def revoke_api_key(key_id: str) -> None:
    if not await asyncio.to_thread(store.revoke_api_key, key_id):
        raise HTTPException(status_code=404, detail="API key not found.")
