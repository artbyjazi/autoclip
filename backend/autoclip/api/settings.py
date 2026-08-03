"""Settings, secrets, provider health, and system status."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import config, models, paths, system
from ..providers import PROVIDERS, build_provider
from ..providers.base import ProviderStatus
from .schemas import (
    FolderEntryOut,
    FolderListingOut,
    FolderSuggestionOut,
    ProviderStatusOut,
    SecretIn,
    SettingsIn,
    SettingsOut,
    SystemOut,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["settings"])

#: Cap on how many subdirectories one listing returns. A media-heavy folder can
#: have thousands of siblings; the folder picker is for navigating toward a
#: destination, not rendering the whole filesystem in one response.
MAX_FOLDER_ENTRIES = 500


def _settings_out(settings: config.Settings) -> SettingsOut:
    payload = settings.model_dump(mode="json")
    return SettingsOut(
        active_provider=payload["active_provider"],
        providers=payload["providers"],
        whisper=payload["whisper"],
        clips=payload["clips"],
        ingest=payload["ingest"],
        export=payload["export"],
        insecure_secret_storage=payload["insecure_secret_storage"],
        keys_present={
            name: config.get_secret(name, settings) is not None for name in config.KEYED_PROVIDERS
        }
        | {config.HF_TOKEN_KEY: config.get_secret(config.HF_TOKEN_KEY, settings) is not None},
    )


@router.get("/settings", response_model=SettingsOut)
async def get_settings() -> SettingsOut:
    return _settings_out(config.load())


@router.put("/settings", response_model=SettingsOut)
async def put_settings(payload: SettingsIn) -> SettingsOut:
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

    for section in ("whisper", "clips", "ingest", "export"):
        if section in updates:
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
    return _settings_out(settings)


@router.put("/settings/secrets", status_code=204)
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


@router.delete("/settings/secrets/{key}", status_code=204)
async def delete_secret(key: str) -> None:
    config.delete_secret(key)


@router.get("/providers/status", response_model=list[ProviderStatusOut])
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
            status: ProviderStatus = await provider.health_check()
        except Exception as exc:
            status = ProviderStatus(name=name, available=False, detail=str(exc)[:200])

        return ProviderStatusOut(
            name=name,
            available=status.available,
            detail=status.detail,
            models=status.models,
            requires_key=provider_cls.requires_key,
            has_key=has_key,
        )

    return list(await asyncio.gather(*(check(name) for name in PROVIDERS)))


@router.get("/system", response_model=SystemOut)
async def system_status() -> SystemOut:
    report = await asyncio.to_thread(system.refresh)
    return SystemOut(
        ready=report.ready,
        python_version=report.python_version,
        platform=report.platform,
        ffmpeg_version=report.ffmpeg.version,
        has_libass=report.ffmpeg.has_libass,
        nvenc_works=report.ffmpeg.nvenc_works,
        accel=report.gpu.accel,
        gpu_name=report.gpu.name,
        compute_type=report.gpu.compute_type,
        diarization_available=report.deps.whisperx,
    )


@router.post("/system/models", status_code=204)
async def fetch_models() -> None:
    """Download any missing ML model bundles."""
    for key in models.MODELS:
        if not models.is_available(key):
            try:
                await asyncio.to_thread(models.ensure, key)
            except models.ModelDownloadError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc


def _folder_suggestions() -> list[FolderSuggestionOut]:
    """Well-known folders, filtered to ones that actually exist on this machine."""
    home = Path.home()
    candidates = [
        ("Videos", home / "Videos"),
        ("Desktop", home / "Desktop"),
        ("Downloads", home / "Downloads"),
        ("Documents", home / "Documents"),
        ("Default save location", paths.exports_dir()),
    ]
    return [
        FolderSuggestionOut(label=label, path=str(candidate))
        for label, candidate in candidates
        if candidate.is_dir()
    ]


@router.get("/settings/folders", response_model=FolderListingOut)
async def browse_folders(path: str | None = None) -> FolderListingOut:
    """List subdirectories of `path`, for the save-location picker.

    Browsers can't hand a web page a server-side directory picker, and this
    server runs on the user's own machine, so the picker is implemented here
    instead: walk the real filesystem, directories only.
    """
    target = Path(path).expanduser() if path else Path.home()

    try:
        target = await asyncio.to_thread(target.resolve)
    except (OSError, RuntimeError) as exc:
        raise HTTPException(
            status_code=400, detail="That location doesn't exist or can't be opened."
        ) from exc

    if not target.is_dir():
        raise HTTPException(
            status_code=400, detail="That location doesn't exist or can't be opened."
        )

    def _list_children() -> list[Path]:
        return sorted(target.iterdir(), key=lambda p: p.name.lower())

    try:
        children = await asyncio.to_thread(_list_children)
    except PermissionError as exc:
        raise HTTPException(
            status_code=403, detail="AutoClip doesn't have permission to open that folder."
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=400, detail="That location doesn't exist or can't be opened."
        ) from exc

    entries: list[FolderEntryOut] = []
    for child in children:
        if len(entries) >= MAX_FOLDER_ENTRIES:
            break
        try:
            if child.is_dir():
                entries.append(FolderEntryOut(name=child.name, path=str(child)))
        except PermissionError:
            # A single unreadable child (a locked system folder, a broken
            # symlink) shouldn't take down the whole listing.
            continue

    parent = target.parent
    return FolderListingOut(
        path=str(target),
        parent=str(parent) if parent != target else None,
        writable=os.access(target, os.W_OK),
        entries=entries,
        suggestions=_folder_suggestions(),
    )
