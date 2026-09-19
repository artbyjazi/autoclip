"""Artifact accounting and conservative retention cleanup."""

from __future__ import annotations

import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import paths
from .config import StorageSettings
from .db import store


def status() -> dict[str, Any]:
    root = paths.ensure_layout()
    disk = shutil.disk_usage(root)
    return {
        "root": str(root),
        "used_bytes": _tree_size(root),
        "disk_total_bytes": disk.total,
        "disk_free_bytes": disk.free,
    }


def enough_space(settings: StorageSettings) -> bool:
    free = shutil.disk_usage(paths.ensure_layout()).free
    return free >= settings.reject_new_jobs_below_gb * 1024**3


def cleanup(settings: StorageSettings) -> dict[str, int]:
    now = datetime.now(UTC)
    active = {
        job.id for job in store.list_jobs(limit=1000)
        if job.status in ("queued", "running")
    }
    removed = {"previews": 0, "sources": 0, "chunks": 0, "bytes": 0}

    preview_cutoff = now - timedelta(hours=settings.preview_retention_hours)
    source_cutoff = now - timedelta(days=settings.source_retention_days)

    for job in store.list_jobs(limit=1000):
        if job.id in active:
            continue
        workspace = paths.job_work_dir(job.id)
        preview_dir = workspace / "previews"
        removed["bytes"] += _remove_old_files(preview_dir, preview_cutoff, removed, "previews")
        if job.status == "done":
            for chunk in (workspace / "asr").glob("*.flac") if (workspace / "asr").is_dir() else ():
                removed["bytes"] += chunk.stat().st_size
                chunk.unlink(missing_ok=True)
                removed["chunks"] += 1

    active_sources = {
        job.source_id for job in store.list_jobs(limit=1000)
        if job.status in ("queued", "running")
    }
    for source in store.list_sources(limit=1000):
        if source.id in active_sources:
            continue
        try:
            created = datetime.fromisoformat(source.created_at)
        except ValueError:
            continue
        directory = paths.source_media_dir(source.id)
        if created < source_cutoff and directory.is_dir():
            size = _tree_size(directory)
            shutil.rmtree(directory)
            removed["bytes"] += size
            removed["sources"] += 1
    return removed


def _remove_old_files(
    directory: Path, cutoff: datetime, counters: dict[str, int], key: str
) -> int:
    freed = 0
    if not directory.is_dir():
        return freed
    for path in directory.iterdir():
        if not path.is_file():
            continue
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        if modified < cutoff:
            freed += path.stat().st_size
            path.unlink(missing_ok=True)
            counters[key] += 1
    return freed


def _tree_size(root: Path) -> int:
    total = 0
    if not root.exists():
        return total
    for path in root.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            continue
    return total
