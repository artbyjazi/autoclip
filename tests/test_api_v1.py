"""Frozen v1 API, authentication, and on-demand render contracts."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from clippy import app as app_module
from clippy.app import create_app
from clippy.db import store
from clippy.db.models import Clip, Job, Source, new_id
from clippy.jobs.queue import JobQueue
from clippy.pipeline.runner import JobWorkspace
from clippy.pipeline.transcript import Transcript, Word
from clippy.security import create_api_key, hash_token
from fastapi.testclient import TestClient


@pytest.fixture
def v1_client(clippy_home, monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[TestClient, dict]]:
    monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
    with TestClient(create_app()) as client:
        _, token = create_api_key("test")
        yield client, {"Authorization": f"Bearer {token}"}


def test_health_and_openapi_are_public(v1_client) -> None:
    client, _ = v1_client

    assert client.get("/api/v1/health").status_code == 200
    schema = client.get("/api/openapi.json")
    assert schema.status_code == 200
    assert "/api/v1/videos" in schema.json()["paths"]


def test_v1_requires_a_valid_bearer(v1_client) -> None:
    client, headers = v1_client

    assert client.get("/api/v1/videos").status_code == 401
    assert (
        client.get("/api/v1/videos", headers={"Authorization": "Bearer clp_wrong"}).status_code
        == 401
    )
    assert client.get("/api/v1/videos", headers=headers).status_code == 200


def test_key_is_stored_only_as_hash_and_revocation_is_immediate(v1_client) -> None:
    client, headers = v1_client
    token = headers["Authorization"].removeprefix("Bearer ")
    record = store.find_api_key(hash_token(token))

    assert record is not None
    assert record.token_hash != token
    assert record.prefix == token[:12]

    response = client.delete(f"/api/v1/api-keys/{record.id}", headers=headers)
    assert response.status_code == 204
    assert client.get("/api/v1/videos", headers=headers).status_code == 401


def test_create_youtube_video_enqueues_analysis(v1_client) -> None:
    client, headers = v1_client

    response = client.post(
        "/api/v1/videos",
        headers=headers,
        json={"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    job = store.get_job(body["job_id"])
    assert job is not None
    assert job.kind == "analysis"
    assert job.provider == "automatic"
    assert store.get_source(body["video_id"]).path == ""


def test_public_job_states_are_stable(v1_client) -> None:
    client, headers = v1_client
    source = store.create_source(Source(id=new_id(), type="youtube", path="", title="x"))
    analysis = store.create_job(
        Job(id=new_id(), source_id=source.id, kind="analysis", status="done")
    )
    render = store.create_job(Job(id=new_id(), source_id=source.id, kind="render", status="done"))

    assert (
        client.get(f"/api/v1/jobs/{analysis.id}", headers=headers).json()["status"]
        == "ready_for_review"
    )
    assert client.get(f"/api/v1/jobs/{render.id}", headers=headers).json()["status"] == "completed"


def test_preview_and_render_are_separate_fifo_jobs(v1_client) -> None:
    client, headers = v1_client
    source = store.create_source(
        Source(id=new_id(), type="youtube", path="/tmp/source.mp4", title="x")
    )
    analysis = store.create_job(
        Job(id=new_id(), source_id=source.id, kind="analysis", status="done")
    )
    clip = Clip(id=new_id(), job_id=analysis.id, start_s=1, end_s=31)
    store.replace_clips(analysis.id, [clip])

    preview = client.post(
        f"/api/v1/clips/{clip.id}/preview", headers=headers, json={"style": "clean"}
    )
    render = client.post(f"/api/v1/clips/{clip.id}/render", headers=headers, json={})

    assert preview.status_code == render.status_code == 202
    preview_job = store.get_job(preview.json()["id"])
    render_job = store.get_job(render.json()["id"])
    assert (preview_job.kind, render_job.kind) == ("preview", "render")
    assert preview_job.parent_job_id == render_job.parent_job_id == analysis.id
    assert store.next_queued_job().id == preview_job.id


def test_cookie_file_path_is_never_exposed_or_api_mutable(v1_client) -> None:
    client, headers = v1_client
    response = client.get("/api/v1/settings", headers=headers)

    assert "cookies_file" not in response.json()["ingest"]
    assert response.json()["ingest"]["cookies_file_configured"] is False
    changed = client.patch(
        "/api/v1/settings",
        headers=headers,
        json={"ingest": {"cookies_file": "/private/secret/cookies.txt"}},
    )
    assert changed.status_code == 400


def test_legacy_aliases_are_gone(v1_client) -> None:
    client, headers = v1_client

    for path in (
        "/api/health",
        "/api/jobs",
        "/api/sources",
        "/api/settings",
        "/api/providers/status",
        "/api/caption-styles",
    ):
        assert client.get(path, headers=headers).status_code == 404


def test_retry_requeues_failed_jobs(v1_client) -> None:
    client, headers = v1_client
    source = store.create_source(Source(id=new_id(), type="youtube", path="", title="x"))

    assert client.post("/api/v1/jobs/missing/retry", headers=headers).status_code == 404

    running = store.create_job(Job(id=new_id(), source_id=source.id, kind="analysis"))
    assert client.post(f"/api/v1/jobs/{running.id}/retry", headers=headers).status_code == 409

    failed = store.create_job(
        Job(id=new_id(), source_id=source.id, kind="analysis", status="failed")
    )
    response = client.post(f"/api/v1/jobs/{failed.id}/retry", headers=headers)
    assert response.status_code == 200
    assert response.json()["status"] == "queued"
    assert store.get_job(failed.id).status == "queued"


def test_export_download_needs_an_export(v1_client) -> None:
    client, headers = v1_client
    source = store.create_source(Source(id=new_id(), type="youtube", path="", title="x"))
    analysis = store.create_job(
        Job(id=new_id(), source_id=source.id, kind="analysis", status="done")
    )
    clip = Clip(id=new_id(), job_id=analysis.id, start_s=1, end_s=31)
    store.replace_clips(analysis.id, [clip])

    assert client.get("/api/v1/exports/missing/download", headers=headers).status_code == 404
    assert client.get(f"/api/v1/clips/{clip.id}/download", headers=headers).status_code == 404


def test_tailscale_identity_is_only_trusted_from_loopback(
    clippy_home, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
    identity = {"Tailscale-User-Login": "owner@example.com"}
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/v1/videos", headers=identity).status_code == 200
    with TestClient(create_app(), client=("100.64.0.2", 50000)) as client:
        assert client.get("/api/v1/videos", headers=identity).status_code == 401

    monkeypatch.setenv("CLIPPY_TAILSCALE_USERS", "someone-else@example.com")
    with TestClient(create_app(), client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/v1/videos", headers=identity).status_code == 403


def test_video_summary_transcript_style_cancel_and_media(v1_client, tmp_path) -> None:
    client, headers = v1_client
    media = tmp_path / "source.mp4"
    media.write_bytes(b"0123456789")
    source = store.create_source(
        Source(id=new_id(), type="youtube", path=str(media), title="Project", duration_s=30)
    )
    analysis = store.create_job(
        Job(id=new_id(), source_id=source.id, kind="analysis", status="done")
    )
    clip = Clip(id=new_id(), job_id=analysis.id, start_s=1, end_s=3, start_word=0, end_word=1)
    store.replace_clips(analysis.id, [clip])
    transcript = Transcript(words=[Word("Hello", 1, 1.5), Word("world", 1.6, 2.2)])
    transcript.save(JobWorkspace(analysis.id).transcript)

    summary = client.get(f"/api/v1/videos/{source.id}", headers=headers).json()
    assert summary["clip_count"] == 1
    assert summary["latest_analysis_job"]["status"] == "ready_for_review"
    words = client.get(f"/api/v1/clips/{clip.id}/transcript", headers=headers).json()
    assert words["text"] == "Hello world"
    assert [word["index"] for word in words["words"]] == [0, 1]

    changed = client.patch(
        f"/api/v1/clips/{clip.id}", headers=headers, json={"caption_style": "karaoke"}
    )
    assert changed.status_code == 200
    assert changed.json()["caption_style"] == "karaoke"
    edit_updated_at = store.get_clip_edit(clip.id).updated_at
    unchanged = client.patch(
        f"/api/v1/clips/{clip.id}", headers=headers, json={"caption_style": "karaoke"}
    )
    assert unchanged.status_code == 200
    assert store.get_clip_edit(clip.id).updated_at == edit_updated_at
    same_style_render = client.post(
        f"/api/v1/clips/{clip.id}/render", headers=headers, json={"style": "karaoke"}
    )
    assert same_style_render.status_code == 202
    assert store.get_clip_edit(clip.id).updated_at == edit_updated_at

    queued = store.create_job(Job(id=new_id(), source_id=source.id, kind="render"))
    assert client.post(f"/api/v1/jobs/{queued.id}/cancel", headers=headers).status_code == 200
    assert store.get_job(queued.id).status == "cancelled"

    ranged = client.get(
        f"/api/v1/videos/{source.id}/media", headers={**headers, "Range": "bytes=0-3"}
    )
    assert ranged.status_code == 206
    assert ranged.content == b"0123"


def test_auto_preview_reconciliation_is_idempotent(initialised_db, tmp_path) -> None:
    media = tmp_path / "source.mp4"
    media.write_bytes(b"video")
    source = store.create_source(Source(id=new_id(), type="youtube", path=str(media)))
    analysis = store.create_job(
        Job(id=new_id(), source_id=source.id, kind="analysis", status="done", settings={})
    )
    clips = [
        Clip(id=new_id(), job_id=analysis.id, start_s=0, end_s=20),
        Clip(id=new_id(), job_id=analysis.id, start_s=20, end_s=40),
    ]
    store.replace_clips(analysis.id, clips)
    queue = JobQueue()

    assert queue._ensure_auto_previews(analysis, clips) == 2
    assert queue._ensure_auto_previews(analysis, clips) == 0
    previews = [job for job in store.list_jobs_for_source(source.id) if job.kind == "preview"]
    assert len(previews) == 2
