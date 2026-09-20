"""Database access.

Connections are per-thread: ``sqlite3`` objects can't be shared across threads,
and the API layer runs blocking DB calls in a worker pool. WAL mode lets the
pipeline worker write while the web layer reads, which is the whole concurrency
story for a single-user local app.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .. import paths
from .schema import SCHEMA_VERSION, migrate

__all__ = ["SCHEMA_VERSION", "connect", "connection", "init", "migrate", "reset_connections"]

_local = threading.local()


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    # WAL lets readers proceed during writes; without it the UI stalls whenever
    # the pipeline commits progress.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    # NORMAL is the right durability trade for local artifacts we can regenerate.
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    """Open a new configured connection. The caller owns closing it."""
    target = db_path or paths.db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(target, timeout=5.0)
    _configure(conn)
    return conn


def _thread_connection() -> sqlite3.Connection:
    """Return this thread's shared connection, opening it on first use.

    Keyed by database path so a test that repoints ``CLIPPY_HOME`` doesn't
    keep talking to the previous database.
    """
    target = paths.db_path()
    existing: sqlite3.Connection | None = getattr(_local, "conn", None)
    if existing is not None and getattr(_local, "path", None) == target:
        return existing

    if existing is not None:
        existing.close()

    conn = connect(target)
    _local.conn = conn
    _local.path = target
    return conn


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    """Yield this thread's connection inside a transaction.

    Commits on clean exit, rolls back on exception. Nested use is safe because
    ``sqlite3`` treats an already-open transaction as a no-op here.
    """
    conn = _thread_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init() -> int:
    """Create the directory layout and bring the schema up to date."""
    paths.ensure_layout()
    conn = _thread_connection()
    return migrate(conn)


def reset_connections() -> None:
    """Close this thread's connection. Used by tests between temp databases."""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
    _local.conn = None
    _local.path = None
