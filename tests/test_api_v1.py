"""Frozen v1 API, authentication, and on-demand render contracts."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from clippy import app as app_module
from clippy.app import create_app
from clippy.db import store
from clippy.db.models import Clip, Job, Source, new_id
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
    assert client.get(
        "/api/v1/videos", headers={"Authorization": "Bearer clp_wrong"}
    ).status_code == 401
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
    render = store.create_job(
        Job(id=new_id(), source_id=source.id, kind="render", status="done")
    )

    assert client.get(f"/api/v1/jobs/{analysis.id}", headers=headers).json()[
        "status"
    ] == "ready_for_review"
    assert client.get(f"/api/v1/jobs/{render.id}", headers=headers).json()[
        "status"
    ] == "completed"


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
