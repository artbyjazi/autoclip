"""CRUD operations over the Clippy database.

Every function opens its own transaction via :func:`clippy.db.connection`,
so callers don't manage commits. Functions are synchronous; async callers should
wrap them in ``asyncio.to_thread``.
"""

from __future__ import annotations

import json
from typing import Any

from . import connection
from .models import (
    ApiKey,
    Clip,
    ClipEdit,
    ClipStatus,
    Export,
    Job,
    JobStatus,
    ProviderTarget,
    ProviderUsage,
    Source,
    Transcript,
    utcnow,
)

# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------


def create_source(source: Source) -> Source:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO sources (id, type, url, filename, path, title, channel,
                                 duration_s, width, height, fps, has_audio,
                                 has_video, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source.id,
                source.type,
                source.url,
                source.filename,
                source.path,
                source.title,
                source.channel,
                source.duration_s,
                source.width,
                source.height,
                source.fps,
                int(source.has_audio),
                int(source.has_video),
                source.created_at,
            ),
        )
    return source


def get_source(source_id: str) -> Source | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return Source.from_row(row) if row else None


def update_source(source: Source) -> None:
    with connection() as conn:
        conn.execute(
            """
            UPDATE sources SET type=?, url=?, filename=?, path=?, title=?, channel=?,
                duration_s=?, width=?, height=?, fps=?, has_audio=?, has_video=?
            WHERE id=?
            """,
            (
                source.type,
                source.url,
                source.filename,
                source.path,
                source.title,
                source.channel,
                source.duration_s,
                source.width,
                source.height,
                source.fps,
                int(source.has_audio),
                int(source.has_video),
                source.id,
            ),
        )


def list_sources(limit: int = 50) -> list[Source]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM sources ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return [Source.from_row(r) for r in rows]


# --------------------------------------------------------------------------
# Jobs
# --------------------------------------------------------------------------


def create_job(job: Job) -> Job:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO jobs (id, source_id, status, kind, clip_id, parent_job_id,
                              current_stage, progress, error,
                              provider, settings_json, created_at, updated_at,
                              started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job.id,
                job.source_id,
                job.status,
                job.kind,
                job.clip_id,
                job.parent_job_id,
                job.current_stage,
                job.progress,
                job.error,
                job.provider,
                json.dumps(job.settings),
                job.created_at,
                job.updated_at,
                job.started_at,
                job.finished_at,
            ),
        )
    return job


def get_job(job_id: str) -> Job | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return Job.from_row(row) if row else None


def list_jobs(limit: int = 50, status: JobStatus | None = None) -> list[Job]:
    query = "SELECT * FROM jobs"
    params: list[Any] = []
    if status:
        query += " WHERE status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    with connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [Job.from_row(r) for r in rows]


def list_jobs_for_source(source_id: str) -> list[Job]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE source_id = ? ORDER BY created_at DESC", (source_id,)
        ).fetchall()
    return [Job.from_row(row) for row in rows]


class _Unset:
    """Sentinel distinguishing "leave this alone" from "set this to NULL".

    Without it, nullable columns can only ever be written, never cleared — a
    retried job would keep displaying the error message from its failed run.
    """

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "UNSET"


UNSET = _Unset()


def update_job(
    job_id: str,
    *,
    status: JobStatus | None = None,
    current_stage: str | None = None,
    progress: float | None = None,
    error: str | None | _Unset = UNSET,
    started_at: str | None | _Unset = UNSET,
    finished_at: str | None | _Unset = UNSET,
) -> None:
    """Patch the supplied fields. Omitted fields are left untouched.

    Nullable fields take :data:`UNSET` as their default, so passing ``None``
    explicitly clears them.

    ``updated_at`` is always refreshed so the UI can detect staleness.
    """
    fields: dict[str, Any] = {"updated_at": utcnow()}
    for name, value in (
        ("status", status),
        ("current_stage", current_stage),
        ("progress", progress),
    ):
        if value is not None:
            fields[name] = value

    for name, value in (
        ("error", error),
        ("started_at", started_at),
        ("finished_at", finished_at),
    ):
        if not isinstance(value, _Unset):
            fields[name] = value

    assignments = ", ".join(f"{name} = ?" for name in fields)
    with connection() as conn:
        conn.execute(
            f"UPDATE jobs SET {assignments} WHERE id = ?",
            (*fields.values(), job_id),
        )


def next_queued_job() -> Job | None:
    """Return the oldest queued job — the FIFO the worker pulls from."""
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at ASC LIMIT 1"
        ).fetchone()
    return Job.from_row(row) if row else None


# --------------------------------------------------------------------------
# Transcripts
# --------------------------------------------------------------------------


def upsert_transcript(transcript: Transcript) -> Transcript:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO transcripts (job_id, json_path, language, model,
                                     has_diarization, word_count, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                json_path = excluded.json_path,
                language = excluded.language,
                model = excluded.model,
                has_diarization = excluded.has_diarization,
                word_count = excluded.word_count,
                source = excluded.source
            """,
            (
                transcript.job_id,
                transcript.json_path,
                transcript.language,
                transcript.model,
                int(transcript.has_diarization),
                transcript.word_count,
                transcript.source,
                transcript.created_at,
            ),
        )
    return transcript


def get_transcript(job_id: str) -> Transcript | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM transcripts WHERE job_id = ?", (job_id,)).fetchone()
    return Transcript.from_row(row) if row else None


# --------------------------------------------------------------------------
# Clips
# --------------------------------------------------------------------------


def replace_clips(job_id: str, clips: list[Clip]) -> list[Clip]:
    """Swap in a fresh set of clips for a job.

    Used when highlight detection reruns — a retry should not leave the previous
    run's candidates behind.
    """
    with connection() as conn:
        conn.execute("DELETE FROM clips WHERE job_id = ?", (job_id,))
        conn.executemany(
            """
            INSERT INTO clips (id, job_id, rank, start_s, end_s, start_word, end_word,
                               title, hook, score, reason, score_breakdown_json,
                               metadata_json, degraded, provider_target, status,
                               user_trimmed, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    c.id,
                    c.job_id,
                    c.rank,
                    c.start_s,
                    c.end_s,
                    c.start_word,
                    c.end_word,
                    c.title,
                    c.hook,
                    c.score,
                    c.reason,
                    json.dumps(c.score_breakdown),
                    json.dumps(c.metadata),
                    int(c.degraded),
                    c.provider_target,
                    c.status,
                    int(c.user_trimmed),
                    c.created_at,
                )
                for c in clips
            ],
        )
    return clips


def get_clip(clip_id: str) -> Clip | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
    return Clip.from_row(row) if row else None


def list_clips(job_id: str) -> list[Clip]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM clips WHERE job_id = ? ORDER BY rank ASC", (job_id,)
        ).fetchall()
    return [Clip.from_row(r) for r in rows]


def update_clip(
    clip_id: str,
    *,
    start_s: float | None = None,
    end_s: float | None = None,
    start_word: int | None = None,
    end_word: int | None = None,
    title: str | None = None,
    status: ClipStatus | None = None,
    user_trimmed: bool | None = None,
) -> None:
    fields: dict[str, Any] = {}
    for name, value in (
        ("start_s", start_s),
        ("end_s", end_s),
        ("start_word", start_word),
        ("end_word", end_word),
        ("title", title),
        ("status", status),
    ):
        if value is not None:
            fields[name] = value
    if user_trimmed is not None:
        fields["user_trimmed"] = int(user_trimmed)

    if not fields:
        return

    assignments = ", ".join(f"{name} = ?" for name in fields)
    with connection() as conn:
        conn.execute(
            f"UPDATE clips SET {assignments} WHERE id = ?",
            (*fields.values(), clip_id),
        )


# --------------------------------------------------------------------------
# Clip edits
# --------------------------------------------------------------------------


def upsert_clip_edit(edit: ClipEdit) -> ClipEdit:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO clip_edits (clip_id, edited_words_json, caption_style, ratio, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(clip_id) DO UPDATE SET
                edited_words_json = excluded.edited_words_json,
                caption_style = excluded.caption_style,
                ratio = excluded.ratio,
                updated_at = excluded.updated_at
            """,
            (
                edit.clip_id,
                json.dumps(edit.edited_words) if edit.edited_words is not None else None,
                edit.caption_style,
                edit.ratio,
                utcnow(),
            ),
        )
    return edit


def get_clip_edit(clip_id: str) -> ClipEdit | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM clip_edits WHERE clip_id = ?", (clip_id,)).fetchone()
    return ClipEdit.from_row(row) if row else None


# --------------------------------------------------------------------------
# Exports
# --------------------------------------------------------------------------


def create_export(export: Export) -> Export:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO exports (id, clip_id, path, ratio, style, size_bytes,
                                 job_id, kind, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                export.id,
                export.clip_id,
                export.path,
                export.ratio,
                export.style,
                export.size_bytes,
                export.job_id,
                export.kind,
                export.created_at,
            ),
        )
    return export


def get_export(export_id: str) -> Export | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM exports WHERE id = ?", (export_id,)).fetchone()
    return Export.from_row(row) if row else None


def list_exports(clip_id: str) -> list[Export]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT * FROM exports WHERE clip_id = ? ORDER BY created_at DESC", (clip_id,)
        ).fetchall()
    return [Export.from_row(r) for r in rows]


def latest_export(clip_id: str, *, kind: str = "final") -> Export | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM exports WHERE clip_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
            (clip_id, kind),
        ).fetchone()
    return Export.from_row(row) if row else None


def delete_source(source_id: str) -> bool:
    with connection() as conn:
        cursor = conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
    return cursor.rowcount > 0


# --------------------------------------------------------------------------
# Provider routing and usage
# --------------------------------------------------------------------------


def upsert_provider_target(target: ProviderTarget) -> ProviderTarget:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO provider_targets
                (id, purpose, provider, model, priority, enabled, health,
                 cooldown_until, consecutive_errors, last_error, last_success, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                purpose=excluded.purpose, provider=excluded.provider,
                model=excluded.model, priority=excluded.priority,
                enabled=excluded.enabled, updated_at=excluded.updated_at
            """,
            (
                target.id,
                target.purpose,
                target.provider,
                target.model,
                target.priority,
                int(target.enabled),
                target.health,
                target.cooldown_until,
                target.consecutive_errors,
                target.last_error,
                target.last_success,
                target.updated_at,
            ),
        )
    return target


def list_provider_targets(purpose: str | None = None) -> list[ProviderTarget]:
    query = "SELECT * FROM provider_targets"
    params: tuple[Any, ...] = ()
    if purpose:
        query += " WHERE purpose = ?"
        params = (purpose,)
    query += " ORDER BY priority DESC, id"
    with connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [ProviderTarget.from_row(row) for row in rows]


def update_provider_runtime(
    target_id: str,
    *,
    health: str,
    cooldown_until: str | None = None,
    error: str | None = None,
    success: bool = False,
) -> None:
    with connection() as conn:
        conn.execute(
            """
            UPDATE provider_targets SET health=?, cooldown_until=?, last_error=?,
                last_success=CASE WHEN ? THEN ? ELSE last_success END,
                consecutive_errors=CASE WHEN ? THEN 0 ELSE consecutive_errors + 1 END,
                updated_at=? WHERE id=?
            """,
            (
                health,
                cooldown_until,
                error,
                int(success),
                utcnow(),
                int(success),
                utcnow(),
                target_id,
            ),
        )


def reset_provider_auth(provider: str) -> None:
    """Make auth-failed targets eligible after their credential is replaced."""
    with connection() as conn:
        conn.execute(
            """
            UPDATE provider_targets SET health='unknown', cooldown_until=NULL,
                consecutive_errors=0, last_error=NULL, updated_at=?
            WHERE provider=? AND health='auth_failed'
            """,
            (utcnow(), provider),
        )


def create_provider_usage(usage: ProviderUsage) -> ProviderUsage:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO provider_usage
                (id, target_id, job_id, operation, status, error_type, latency_ms,
                 input_tokens, output_tokens, audio_seconds, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                usage.id,
                usage.target_id,
                usage.job_id,
                usage.operation,
                usage.status,
                usage.error_type,
                usage.latency_ms,
                usage.input_tokens,
                usage.output_tokens,
                usage.audio_seconds,
                usage.created_at,
            ),
        )
    return usage


def provider_usage_summary() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT target_id, COUNT(*) requests,
                   SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) successes,
                   SUM(CASE WHEN status!='success' THEN 1 ELSE 0 END) errors,
                   CAST(AVG(latency_ms) AS INTEGER) average_latency_ms,
                   SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens,
                   SUM(audio_seconds) audio_seconds
            FROM provider_usage GROUP BY target_id ORDER BY target_id
            """
        ).fetchall()
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------
# Incoming API keys
# --------------------------------------------------------------------------


def create_api_key(record: ApiKey) -> ApiKey:
    with connection() as conn:
        conn.execute(
            "INSERT INTO api_keys "
            "(id, name, prefix, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
            (record.id, record.name, record.prefix, record.token_hash, record.created_at),
        )
    return record


def list_api_keys(*, include_revoked: bool = False) -> list[ApiKey]:
    query = "SELECT * FROM api_keys"
    if not include_revoked:
        query += " WHERE revoked_at IS NULL"
    query += " ORDER BY created_at DESC"
    with connection() as conn:
        rows = conn.execute(query).fetchall()
    return [ApiKey.from_row(row) for row in rows]


def find_api_key(token_hash: str) -> ApiKey | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE token_hash = ? AND revoked_at IS NULL", (token_hash,)
        ).fetchone()
    return ApiKey.from_row(row) if row else None


def touch_api_key(key_id: str) -> None:
    with connection() as conn:
        conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (utcnow(), key_id))


def revoke_api_key(key_id: str) -> bool:
    with connection() as conn:
        cursor = conn.execute(
            "UPDATE api_keys SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (utcnow(), key_id),
        )
    return cursor.rowcount > 0
