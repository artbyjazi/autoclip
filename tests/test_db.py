"""Schema migrations and the data layer."""

from __future__ import annotations

import sqlite3

import pytest
from clippy import db, paths
from clippy.db import store
from clippy.db.models import Clip, ClipEdit, Export, Job, Source, Transcript, new_id


def test_init_creates_database_at_schema_version() -> None:
    version = db.init()

    assert version == db.SCHEMA_VERSION
    assert paths.db_path().exists()


def test_init_is_idempotent() -> None:
    first = db.init()
    second = db.init()

    assert first == second == db.SCHEMA_VERSION


def test_all_six_tables_exist(initialised_db: int) -> None:
    with db.connection() as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    names = {r["name"] for r in rows}

    assert {"sources", "jobs", "transcripts", "clips", "clip_edits", "exports"} <= names


def test_foreign_keys_are_enforced(initialised_db: int) -> None:
    job = Job(id=new_id(), source_id="does-not-exist")

    with pytest.raises(sqlite3.IntegrityError):
        store.create_job(job)


def test_newer_schema_version_is_refused(initialised_db: int) -> None:
    with db.connection() as conn:
        conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 5}")

    with pytest.raises(RuntimeError, match="newer than this Clippy build"):
        db.init()


def _make_source(**overrides) -> Source:
    defaults = {
        "id": new_id(),
        "type": "youtube",
        "path": "C:/media/video.mp4",
        "title": "Test video",
        "url": "https://youtube.com/watch?v=abc",
        "duration_s": 1800.0,
        "width": 1920,
        "height": 1080,
        "fps": 30.0,
    }
    return Source(**{**defaults, **overrides})


class TestSources:
    def test_create_and_get(self, initialised_db: int) -> None:
        source = store.create_source(_make_source())

        loaded = store.get_source(source.id)

        assert loaded is not None
        assert loaded.title == "Test video"
        assert loaded.duration_s == 1800.0
        assert loaded.has_audio is True

    def test_get_missing_returns_none(self, initialised_db: int) -> None:
        assert store.get_source("nope") is None

    def test_list_is_newest_first(self, initialised_db: int) -> None:
        old = store.create_source(
            _make_source(title="older", created_at="2026-01-01T00:00:00+00:00")
        )
        new = store.create_source(
            _make_source(title="newer", created_at="2026-06-01T00:00:00+00:00")
        )

        listed = store.list_sources()

        assert [s.id for s in listed] == [new.id, old.id]

    def test_type_is_constrained(self, initialised_db: int) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            store.create_source(_make_source(type="vimeo"))


class TestJobs:
    @pytest.fixture
    def source(self, initialised_db: int) -> Source:
        return store.create_source(_make_source())

    def test_create_and_get_with_settings(self, source: Source) -> None:
        job = store.create_job(
            Job(id=new_id(), source_id=source.id, provider="anthropic", settings={"max_clips": 5})
        )

        loaded = store.get_job(job.id)

        assert loaded is not None
        assert loaded.status == "queued"
        assert loaded.settings == {"max_clips": 5}

    def test_update_patches_only_supplied_fields(self, source: Source) -> None:
        job = store.create_job(Job(id=new_id(), source_id=source.id, provider="ollama"))

        store.update_job(job.id, status="running", current_stage="transcribe", progress=0.4)

        loaded = store.get_job(job.id)
        assert loaded is not None
        assert loaded.status == "running"
        assert loaded.current_stage == "transcribe"
        assert loaded.progress == pytest.approx(0.4)
        assert loaded.provider == "ollama"  # untouched

    def test_update_refreshes_the_timestamp(self, source: Source) -> None:
        job = store.create_job(
            Job(id=new_id(), source_id=source.id, updated_at="2020-01-01T00:00:00+00:00")
        )

        store.update_job(job.id, progress=0.1)

        loaded = store.get_job(job.id)
        assert loaded is not None
        assert loaded.updated_at != "2020-01-01T00:00:00+00:00"

    def test_next_queued_is_fifo(self, source: Source) -> None:
        first = store.create_job(
            Job(id=new_id(), source_id=source.id, created_at="2026-01-01T00:00:00+00:00")
        )
        store.create_job(
            Job(id=new_id(), source_id=source.id, created_at="2026-02-01T00:00:00+00:00")
        )

        assert store.next_queued_job().id == first.id

        store.update_job(first.id, status="running")
        assert store.next_queued_job().id != first.id

    def test_deleting_a_source_cascades_to_jobs(self, source: Source) -> None:
        job = store.create_job(Job(id=new_id(), source_id=source.id))

        with db.connection() as conn:
            conn.execute("DELETE FROM sources WHERE id = ?", (source.id,))

        assert store.get_job(job.id) is None

    def test_status_is_constrained(self, source: Source) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            store.create_job(Job(id=new_id(), source_id=source.id, status="banana"))


class TestClips:
    @pytest.fixture
    def job(self, initialised_db: int) -> Job:
        source = store.create_source(_make_source())
        return store.create_job(Job(id=new_id(), source_id=source.id))

    def _clip(self, job: Job, rank: int, **overrides) -> Clip:
        defaults = {
            "id": new_id(),
            "job_id": job.id,
            "rank": rank,
            "start_s": 10.0 * rank,
            "end_s": 10.0 * rank + 45.0,
            "start_word": 100 * rank,
            "end_word": 100 * rank + 80,
            "title": f"Clip {rank}",
            "score": 90 - rank,
        }
        return Clip(**{**defaults, **overrides})

    def test_replace_clips_orders_by_rank(self, job: Job) -> None:
        store.replace_clips(job.id, [self._clip(job, 2), self._clip(job, 1), self._clip(job, 3)])

        listed = store.list_clips(job.id)

        assert [c.rank for c in listed] == [1, 2, 3]

    def test_replace_clips_discards_the_previous_run(self, job: Job) -> None:
        store.replace_clips(job.id, [self._clip(job, 1), self._clip(job, 2)])
        store.replace_clips(job.id, [self._clip(job, 1, title="Only survivor")])

        listed = store.list_clips(job.id)

        assert len(listed) == 1
        assert listed[0].title == "Only survivor"

    def test_duration_is_derived(self, job: Job) -> None:
        store.replace_clips(job.id, [self._clip(job, 1, start_s=12.5, end_s=57.5)])

        assert store.list_clips(job.id)[0].duration_s == pytest.approx(45.0)

    def test_update_clip_records_a_trim(self, job: Job) -> None:
        clip = self._clip(job, 1)
        store.replace_clips(job.id, [clip])

        store.update_clip(clip.id, start_s=15.0, end_s=50.0, user_trimmed=True, status="kept")

        loaded = store.get_clip(clip.id)
        assert loaded is not None
        assert loaded.start_s == 15.0
        assert loaded.user_trimmed is True
        assert loaded.status == "kept"

    def test_update_clip_with_no_fields_is_a_noop(self, job: Job) -> None:
        clip = self._clip(job, 1)
        store.replace_clips(job.id, [clip])

        store.update_clip(clip.id)

        assert store.get_clip(clip.id).title == "Clip 1"

    def test_clip_status_is_constrained(self, job: Job) -> None:
        with pytest.raises(sqlite3.IntegrityError):
            store.replace_clips(job.id, [self._clip(job, 1, status="maybe")])


class TestTranscriptsEditsAndExports:
    @pytest.fixture
    def job(self, initialised_db: int) -> Job:
        source = store.create_source(_make_source())
        return store.create_job(Job(id=new_id(), source_id=source.id))

    def test_transcript_upsert_overwrites(self, job: Job) -> None:
        store.upsert_transcript(
            Transcript(job_id=job.id, json_path="a.json", model="small", word_count=100)
        )
        store.upsert_transcript(
            Transcript(
                job_id=job.id,
                json_path="b.json",
                model="large-v3",
                word_count=250,
                has_diarization=True,
            )
        )

        loaded = store.get_transcript(job.id)

        assert loaded is not None
        assert loaded.json_path == "b.json"
        assert loaded.model == "large-v3"
        assert loaded.has_diarization is True

    def test_clip_edit_round_trips_word_edits(self, job: Job) -> None:
        clip = Clip(id=new_id(), job_id=job.id, start_s=0.0, end_s=30.0)
        store.replace_clips(job.id, [clip])

        words = [{"word": "Hello", "start": 0.0, "end": 0.4}]
        store.upsert_clip_edit(
            ClipEdit(clip_id=clip.id, edited_words=words, caption_style="karaoke_fill")
        )

        loaded = store.get_clip_edit(clip.id)
        assert loaded is not None
        assert loaded.edited_words == words
        assert loaded.caption_style == "karaoke_fill"

    def test_exports_are_listed_for_a_clip(self, job: Job) -> None:
        clip = Clip(id=new_id(), job_id=job.id, start_s=0.0, end_s=30.0)
        store.replace_clips(job.id, [clip])

        store.create_export(
            Export(id=new_id(), clip_id=clip.id, path="out_9x16.mp4", size_bytes=1234)
        )
        store.create_export(Export(id=new_id(), clip_id=clip.id, path="out_1x1.mp4", ratio="1:1"))

        assert len(store.list_exports(clip.id)) == 2
