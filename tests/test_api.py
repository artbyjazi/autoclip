"""REST API contract tests.

Exercised through the real app with its lifespan running, so the database
migration, event broker binding, and job queue startup are all covered too.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator

import pytest
from autoclip import app as app_module
from autoclip import config
from autoclip.app import create_app
from autoclip.db import store
from autoclip.db.models import Clip, Job, Source, new_id
from fastapi.testclient import TestClient


@pytest.fixture
def client(autoclip_home, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    # The background worker is off here so queued jobs stay queued: these tests
    # are about the HTTP contract, and a worker racing to pick jobs up would
    # make every status assertion flaky. The worker has its own tests.
    monkeypatch.setenv(app_module.ENV_NO_WORKER, "1")
    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def source() -> Source:
    return store.create_source(
        Source(
            id=new_id(),
            type="upload",
            path="C:/media/video.mp4",
            title="Test source",
            duration_s=600.0,
            width=1920,
            height=1080,
            fps=30.0,
        )
    )


class TestHealthAndSystem:
    def test_health(self, client: TestClient) -> None:
        response = client.get("/api/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_system_reports_this_machine(self, client: TestClient) -> None:
        body = client.get("/api/system").json()

        # Compared against the running interpreter, not a hardcoded version:
        # the project supports 3.11 and 3.12, and pinning the assertion to one
        # of them made the test contradict the CI matrix it runs under.
        running = f"{sys.version_info.major}.{sys.version_info.minor}"
        assert body["python_version"].startswith(running)
        assert (3, 11) <= sys.version_info[:2] < (3, 13)

        assert body["accel"] in ("cuda", "mps", "cpu")
        assert isinstance(body["nvenc_works"], bool)

    def test_unknown_api_route_is_404_not_the_spa(self, client: TestClient) -> None:
        # The SPA catch-all must never swallow a mistyped API path — that turns
        # a clear 404 into an HTML page the client can't parse.
        assert client.get("/api/does-not-exist").status_code == 404


class TestCaptionStyles:
    def test_lists_all_four(self, client: TestClient) -> None:
        styles = client.get("/api/caption-styles").json()

        assert {s["key"] for s in styles} == {
            "bold_pop",
            "karaoke_fill",
            "clean_lower",
            "boxed",
        }

    def test_includes_preview_metadata(self, client: TestClient) -> None:
        styles = client.get("/api/caption-styles").json()
        preview = next(s for s in styles if s["key"] == "bold_pop")["preview"]

        assert preview["accent"]
        assert preview["allCaps"] is True
        assert preview["maxWords"] > 0


class TestSettings:
    def test_get_returns_defaults(self, client: TestClient) -> None:
        body = client.get("/api/settings").json()

        assert body["active_provider"] == "anthropic"
        assert body["whisper"]["model"] == "small"

    def test_partial_update_leaves_other_sections_alone(self, client: TestClient) -> None:
        client.put("/api/settings", json={"whisper": {"model": "large-v3"}})

        body = client.get("/api/settings").json()
        assert body["whisper"]["model"] == "large-v3"
        assert body["clips"]["max_clips"] == 10

    def test_provider_switch(self, client: TestClient) -> None:
        response = client.put("/api/settings", json={"active_provider": "ollama"})

        assert response.status_code == 200
        assert response.json()["active_provider"] == "ollama"

    def test_unknown_provider_is_rejected(self, client: TestClient) -> None:
        response = client.put("/api/settings", json={"active_provider": "skynet"})

        assert response.status_code == 400

    def test_inverted_clip_lengths_are_rejected(self, client: TestClient) -> None:
        response = client.put(
            "/api/settings", json={"clips": {"min_duration_s": 90, "max_duration_s": 20}}
        )

        assert response.status_code == 400

    def test_secrets_are_never_returned(self, client: TestClient, fake_keyring) -> None:
        client.put("/api/settings/secrets", json={"key": "anthropic", "value": "sk-secret-value"})

        body = client.get("/api/settings").json()

        assert "sk-secret-value" not in str(body)
        assert body["keys_present"]["anthropic"] is True

    def test_empty_secret_is_rejected(self, client: TestClient, fake_keyring) -> None:
        response = client.put("/api/settings/secrets", json={"key": "anthropic", "value": "   "})

        assert response.status_code == 400

    def test_unknown_secret_key_is_rejected(self, client: TestClient) -> None:
        response = client.put("/api/settings/secrets", json={"key": "aws", "value": "x"})

        assert response.status_code == 400

    def test_secret_can_be_deleted(self, client: TestClient, fake_keyring) -> None:
        client.put("/api/settings/secrets", json={"key": "openai", "value": "sk-x"})
        client.delete("/api/settings/secrets/openai")

        assert client.get("/api/settings").json()["keys_present"]["openai"] is False


class TestSources:
    def test_empty_initially(self, client: TestClient) -> None:
        assert client.get("/api/sources").json() == []

    def test_lists_a_created_source(self, client: TestClient, source: Source) -> None:
        body = client.get("/api/sources").json()

        assert len(body) == 1
        assert body[0]["title"] == "Test source"

    def test_never_exposes_the_filesystem_path(self, client: TestClient, source: Source) -> None:
        body = client.get(f"/api/sources/{source.id}").json()

        assert "path" not in body
        assert "C:/media" not in str(body)

    def test_missing_source_is_404(self, client: TestClient) -> None:
        assert client.get("/api/sources/nope").status_code == 404

    def test_non_url_input_is_rejected(self, client: TestClient) -> None:
        response = client.post("/api/sources/url", json={"url": "C:/videos/clip.mp4"})

        assert response.status_code == 400

    def test_backwards_range_is_rejected_before_any_download(self, client: TestClient) -> None:
        response = client.post(
            "/api/sources/url",
            json={"url": "https://twitch.tv/videos/1", "start_s": 600, "end_s": 60},
        )

        assert response.status_code == 422
        assert "after start" in response.json()["detail"]["message"]

    def test_negative_start_fails_schema_validation(self, client: TestClient) -> None:
        response = client.post(
            "/api/sources/url", json={"url": "https://twitch.tv/videos/1", "start_s": -5}
        )

        assert response.status_code == 422

    def test_deprecated_youtube_route_still_answers(self, client: TestClient) -> None:
        # Scripts written against the YouTube-only API must keep working; the
        # alias reaches the same handler, so the same 400 comes back.
        response = client.post("/api/sources/youtube", json={"url": "not-a-url"})

        assert response.status_code == 400

    def test_unsupported_upload_type_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/api/sources/upload",
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )

        assert response.status_code == 415
        assert "supported" in response.json()["detail"]["message"]


class TestJobs:
    def test_create_and_fetch(self, client: TestClient, source: Source) -> None:
        created = client.post("/api/jobs", json={"source_id": source.id})

        assert created.status_code == 201
        job_id = created.json()["id"]

        fetched = client.get(f"/api/jobs/{job_id}").json()
        assert fetched["status"] == "queued"
        assert fetched["source"]["title"] == "Test source"

    def test_overrides_are_applied(self, client: TestClient, source: Source) -> None:
        response = client.post(
            "/api/jobs",
            json={
                "source_id": source.id,
                "settings": {"provider": "ollama", "max_clips": 3},
            },
        )

        assert response.json()["provider"] == "ollama"

    def test_missing_source_is_404(self, client: TestClient) -> None:
        response = client.post("/api/jobs", json={"source_id": "nope"})

        assert response.status_code == 404

    def test_inverted_durations_are_rejected(self, client: TestClient, source: Source) -> None:
        response = client.post(
            "/api/jobs",
            json={
                "source_id": source.id,
                "settings": {"min_duration_s": 90, "max_duration_s": 20},
            },
        )

        assert response.status_code == 400

    def test_cancel_a_queued_job(self, client: TestClient, source: Source) -> None:
        job_id = client.post("/api/jobs", json={"source_id": source.id}).json()["id"]

        response = client.post(f"/api/jobs/{job_id}/cancel")

        assert response.status_code == 200
        assert client.get(f"/api/jobs/{job_id}").json()["status"] == "cancelled"

    def test_cancelling_a_finished_job_conflicts(self, client: TestClient, source: Source) -> None:
        job = store.create_job(Job(id=new_id(), source_id=source.id, status="done"))

        assert client.post(f"/api/jobs/{job.id}/cancel").status_code == 409

    def test_retry_requeues_a_failed_job(self, client: TestClient, source: Source) -> None:
        job = store.create_job(Job(id=new_id(), source_id=source.id, status="failed", error="boom"))

        response = client.post(f"/api/jobs/{job.id}/retry")

        assert response.status_code == 200
        assert response.json()["status"] == "queued"
        assert response.json()["error"] is None

    def test_retrying_a_running_job_conflicts(self, client: TestClient, source: Source) -> None:
        job = store.create_job(Job(id=new_id(), source_id=source.id, status="running"))

        assert client.post(f"/api/jobs/{job.id}/retry").status_code == 409

    def test_missing_job_is_404(self, client: TestClient) -> None:
        assert client.get("/api/jobs/nope").status_code == 404
        assert client.post("/api/jobs/nope/cancel").status_code == 404
        assert client.post("/api/jobs/nope/retry").status_code == 404


class TestClips:
    @pytest.fixture
    def job_with_clips(self, source: Source) -> Job:
        job = store.create_job(Job(id=new_id(), source_id=source.id, status="done"))
        store.replace_clips(
            job.id,
            [
                Clip(
                    id=new_id(),
                    job_id=job.id,
                    rank=rank,
                    start_s=rank * 60.0,
                    end_s=rank * 60.0 + 40.0,
                    start_word=rank * 100,
                    end_word=rank * 100 + 80,
                    title=f"Clip {rank}",
                    score=90 - rank,
                )
                for rank in (1, 2, 3)
            ],
        )
        return job

    def test_list_clips_for_a_job(self, client: TestClient, job_with_clips: Job) -> None:
        clips = client.get(f"/api/jobs/{job_with_clips.id}/clips").json()

        assert len(clips) == 3
        assert [c["rank"] for c in clips] == [1, 2, 3]
        assert clips[0]["duration_s"] == pytest.approx(40.0)

    def test_patch_title_and_status(self, client: TestClient, job_with_clips: Job) -> None:
        clip_id = client.get(f"/api/jobs/{job_with_clips.id}/clips").json()[0]["id"]

        response = client.patch(
            f"/api/clips/{clip_id}", json={"title": "Renamed", "status": "kept"}
        )

        assert response.status_code == 200
        assert response.json()["title"] == "Renamed"
        assert response.json()["status"] == "kept"

    def test_inverted_trim_is_rejected(self, client: TestClient, job_with_clips: Job) -> None:
        clip_id = client.get(f"/api/jobs/{job_with_clips.id}/clips").json()[0]["id"]

        response = client.patch(f"/api/clips/{clip_id}", json={"start_s": 90.0, "end_s": 30.0})

        assert response.status_code == 400

    def test_caption_style_is_persisted(self, client: TestClient, job_with_clips: Job) -> None:
        clip_id = client.get(f"/api/jobs/{job_with_clips.id}/clips").json()[0]["id"]

        response = client.patch(
            f"/api/clips/{clip_id}/captions", json={"caption_style": "karaoke_fill"}
        )

        assert response.status_code == 200
        assert response.json()["caption_style"] == "karaoke_fill"

    def test_unknown_caption_style_is_rejected(
        self, client: TestClient, job_with_clips: Job
    ) -> None:
        clip_id = client.get(f"/api/jobs/{job_with_clips.id}/clips").json()[0]["id"]

        response = client.patch(
            f"/api/clips/{clip_id}/captions", json={"caption_style": "explosion"}
        )

        assert response.status_code == 400

    def test_missing_clip_is_404(self, client: TestClient) -> None:
        assert client.get("/api/clips/nope").status_code == 404
        assert client.patch("/api/clips/nope", json={"title": "x"}).status_code == 404
        assert client.get("/api/clips/nope/crop-path").status_code == 404

    def test_crop_path_is_404_before_reframe(self, client: TestClient, job_with_clips: Job) -> None:
        # The review player treats this as "fall back to a centre crop", which
        # is what the renderer does for these clips too.
        clip_id = client.get(f"/api/jobs/{job_with_clips.id}/clips").json()[0]["id"]

        assert client.get(f"/api/clips/{clip_id}/crop-path").status_code == 404

    def test_crop_path_is_served_when_present(
        self, client: TestClient, job_with_clips: Job, autoclip_home
    ) -> None:
        from autoclip.pipeline.reframe.croppath import centre_crop
        from autoclip.pipeline.runner import JobWorkspace

        clip = client.get(f"/api/jobs/{job_with_clips.id}/clips").json()[0]
        path = centre_crop(1920, 1080, clip["duration_s"])
        path.save(JobWorkspace(job_with_clips.id).crop_path(clip["id"]))

        body = client.get(f"/api/clips/{clip['id']}/crop-path").json()

        assert body["source_width"] == 1920
        assert body["source_height"] == 1080
        assert len(body["segments"]) == 1
        # The client needs these to position the video; a missing field means a
        # silently centre-cropped preview that disagrees with the export.
        segment = body["segments"][0]
        assert {"start_s", "end_s", "width", "height", "keyframes", "fit"} <= segment.keys()

    def test_words_need_a_transcript(self, client: TestClient, job_with_clips: Job) -> None:
        # The job has clips but no transcript on disk, which is exactly the
        # state a partially-restored workspace would be in.
        clip_id = client.get(f"/api/jobs/{job_with_clips.id}/clips").json()[0]["id"]

        assert client.get(f"/api/clips/{clip_id}/words").status_code == 404


class TestProviderStatus:
    def test_reports_every_provider(self, client: TestClient, fake_keyring) -> None:
        statuses = client.get("/api/providers/status").json()

        assert {s["name"] for s in statuses} == {
            "anthropic",
            "openai",
            "gemini",
            "ollama",
        }

    def test_missing_key_is_reported_as_unavailable(self, client: TestClient, fake_keyring) -> None:
        statuses = {s["name"]: s for s in client.get("/api/providers/status").json()}

        assert statuses["anthropic"]["has_key"] is False
        assert statuses["anthropic"]["available"] is False

    def test_ollama_never_requires_a_key(self, client: TestClient) -> None:
        statuses = {s["name"]: s for s in client.get("/api/providers/status").json()}

        assert statuses["ollama"]["requires_key"] is False


class TestEventStream:
    def test_finished_job_streams_a_snapshot_and_closes(
        self, client: TestClient, source: Source
    ) -> None:
        job = store.create_job(Job(id=new_id(), source_id=source.id, status="done", progress=1.0))

        with client.stream("GET", f"/api/jobs/{job.id}/events") as response:
            assert response.status_code == 200
            body = "".join(response.iter_text())

        # A client opening the stream after completion must still learn the
        # final state rather than hanging on an empty connection.
        assert "snapshot" in body
        assert "done" in body

    def test_missing_job_is_404(self, client: TestClient) -> None:
        assert client.get("/api/jobs/nope/events").status_code == 404


class TestSecretsNeverLeak:
    def test_settings_response_has_no_secret_values(self, client: TestClient, fake_keyring) -> None:
        for key in ("anthropic", "openai", "gemini"):
            client.put("/api/settings/secrets", json={"key": key, "value": f"sk-{key}-DEADBEEF"})

        payload = str(client.get("/api/settings").json())

        assert "DEADBEEF" not in payload

    def test_config_file_has_no_secrets_when_keyring_works(
        self, client: TestClient, fake_keyring, autoclip_home
    ) -> None:
        client.put("/api/settings/secrets", json={"key": "anthropic", "value": "sk-DEADBEEF"})
        client.put("/api/settings", json={"whisper": {"model": "medium"}})

        from autoclip import paths

        assert "DEADBEEF" not in paths.config_path().read_text(encoding="utf-8")
        assert config.get_secret("anthropic") == "sk-DEADBEEF"
