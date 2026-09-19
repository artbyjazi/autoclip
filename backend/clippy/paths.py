"""Filesystem layout for Clippy artifacts.

Everything Clippy writes lives under a single root (``~/.clippy`` by
default). The root is resolved through ``CLIPPY_HOME`` so tests — and users
who keep media on another drive — can relocate it without touching code.

Layout::

    <root>/
      media/            source videos, one directory per source id
      work/<job_id>/    stage intermediates (audio, transcript, crop path)
      exports/          finished clips
      clippy.db       SQLite database
      config.json       settings (secrets live in the OS keyring)
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

ENV_HOME = "CLIPPY_HOME"
LEGACY_ENV_HOME = "AUTOCLIP_HOME"

DEFAULT_DIR_NAME = ".clippy"

LEGACY_DIR_NAMES = (".autoclip", ".clipforge")


def root() -> Path:
    """Return the Clippy home directory.

    Resolved fresh on every call rather than cached at import time, so tests
    that patch ``CLIPPY_HOME`` take effect without reloading the module.
    """
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser().resolve()
    return (Path.home() / DEFAULT_DIR_NAME).resolve()


def media_dir() -> Path:
    return root() / "media"


def work_dir() -> Path:
    return root() / "work"


def exports_dir() -> Path:
    return root() / "exports"


DB_NAME = "clippy.db"
LEGACY_DB_NAMES = ("autoclip.db", "clipforge.db")


def db_path() -> Path:
    """Return the SQLite database path.

    Adopts a pre-rename database in place rather than starting an empty one
    beside it, which would silently orphan every job the user already has.
    """
    base = root()
    current = base / DB_NAME
    if not current.exists():
        for name in LEGACY_DB_NAMES:
            legacy = base / name
            if legacy.is_file():
                return legacy
    return current


def config_path() -> Path:
    return root() / "config.json"


def secrets_path() -> Path:
    return root() / "secrets.json"


def job_work_dir(job_id: str) -> Path:
    """Return the per-job scratch directory for pipeline stage artifacts."""
    return work_dir() / job_id


def source_media_dir(source_id: str) -> Path:
    """Return the directory holding a source's downloaded/uploaded media."""
    return media_dir() / source_id


def ensure_layout() -> Path:
    """Create the directory tree if absent and return the root.

    Safe to call repeatedly; used on startup and at the top of each CLI command.
    """
    base = root()
    _migrate_legacy_layout(base)
    for path in (base, media_dir(), work_dir(), exports_dir()):
        path.mkdir(parents=True, exist_ok=True)
    return base


def _legacy_roots(destination: Path) -> list[Path]:
    candidates: list[Path] = []
    env = os.environ.get(LEGACY_ENV_HOME)
    if env:
        candidates.append(Path(env).expanduser().resolve())
    if ENV_HOME not in os.environ:
        candidates.extend((Path.home() / name).resolve() for name in LEGACY_DIR_NAMES)
    return [path for path in candidates if path != destination and path.is_dir()]


def _migrate_legacy_layout(destination: Path) -> None:
    """Adopt legacy data without deleting or duplicating large media.

    Small control files are copied into the new Clippy root. Large artifact
    directories are symlinked so existing absolute DB paths remain valid and a
    first start never doubles disk usage. The legacy tree is deliberately left
    untouched and the operation is safe to repeat.
    """
    if destination.exists() and any(destination.iterdir()):
        return

    legacy = next(iter(_legacy_roots(destination)), None)
    if legacy is None:
        return

    destination.mkdir(parents=True, exist_ok=True)
    for old_name, new_name in (
        ("autoclip.db", DB_NAME),
        ("clipforge.db", DB_NAME),
        ("config.json", "config.json"),
        ("secrets.json", "secrets.json"),
    ):
        source = legacy / old_name
        target = destination / new_name
        if source.is_file() and not target.exists():
            shutil.copy2(source, target)

    for name in ("media", "work", "exports"):
        source = legacy / name
        target = destination / name
        if source.is_dir() and not target.exists():
            target.symlink_to(source, target_is_directory=True)

    marker = destination / ".migrated-from"
    if not marker.exists():
        marker.write_text(str(legacy) + "\n", encoding="utf-8")
    log.warning("Migrated Clippy control data from %s; the source was preserved.", legacy)
