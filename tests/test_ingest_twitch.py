"""Twitch URL support: detection, live-stream refusal, errors, and the schema migration."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import yt_dlp
from autoclip import db
from autoclip.config import IngestSettings
from autoclip.db import schema
from autoclip.pipeline import ingest


class TestUrlDetection:
    @pytest.mark.parametrize(
        "url",
        [
            "https://www.twitch.tv/videos/2345678901",
            "https://twitch.tv/somestreamer/clip/FunnyClipSlug",
            "https://clips.twitch.tv/FunnyClipSlug",
            "https://m.twitch.tv/videos/2345678901",
        ],
    )
    def test_twitch_urls_are_recognised(self, url: str) -> None:
        assert ingest.is_twitch_url(url)
        assert ingest.is_supported_url(url)
        assert not ingest.is_youtube_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://twitch.tv.evil.example/videos/1",
            "https://nottwitch.tv/videos/1",
            "https://vimeo.com/12345",
            "C:/media/video.mp4",
            "http://[::1",  # malformed: urlparse raises ValueError
        ],
    )
    def test_other_urls_are_not_twitch(self, url: str) -> None:
        assert not ingest.is_twitch_url(url)

    def test_youtube_is_still_supported(self) -> None:
        assert ingest.is_supported_url("https://youtu.be/abc")

    def test_ingest_url_rejects_unsupported_hosts(self) -> None:
        with pytest.raises(ingest.IngestError, match="not a YouTube or Twitch URL"):
            ingest.ingest_url("https://vimeo.com/12345")


class _FakeYoutubeDL:
    """Stands in for yt_dlp.YoutubeDL; records whether a download was attempted."""

    resolved: dict = {}
    downloaded = False

    def __init__(self, options: dict) -> None:
        self.options = options

    def __enter__(self) -> _FakeYoutubeDL:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict:
        assert not download, "Twitch URLs must be resolved before downloading"
        return type(self).resolved

    def process_ie_result(self, info: dict, download: bool = True) -> dict:
        type(self).downloaded = True
        return info


class TestLiveStreams:
    def test_live_channel_is_refused_before_download(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_FakeYoutubeDL, "resolved", {"is_live": True, "title": "LIVE"})
        monkeypatch.setattr(_FakeYoutubeDL, "downloaded", False)
        monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYoutubeDL)

        with pytest.raises(ingest.IngestError, match="live right now") as caught:
            ingest.ingest_twitch("https://www.twitch.tv/somestreamer")

        assert not _FakeYoutubeDL.downloaded
        assert "twitch.tv/videos" in caught.value.hint

    def test_youtube_only_caption_option_is_not_sent_for_twitch(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[dict] = []

        class Recording(_FakeYoutubeDL):
            def __init__(self, options: dict) -> None:
                seen.append(options)

        monkeypatch.setattr(Recording, "resolved", {"is_live": True})
        monkeypatch.setattr(yt_dlp, "YoutubeDL", Recording)

        with pytest.raises(ingest.IngestError):
            ingest.ingest_twitch(
                "https://www.twitch.tv/videos/1",
                IngestSettings(prefer_youtube_captions=True, cookies_from_browser="firefox"),
            )

        assert "writeautomaticsub" not in seen[0]
        assert seen[0]["cookiesfrombrowser"] == ("firefox",)


class TestTwitchErrors:
    def _translate(self, text: str, **settings: str) -> ingest.IngestError:
        exc = yt_dlp.utils.DownloadError(text)
        return ingest._translate_twitch_error(exc, IngestSettings(**settings))

    def test_offline_channel(self) -> None:
        error = self._translate("ERROR: [twitch:stream] someone: The channel is not currently live")
        assert "isn't live" in str(error)

    def test_subscriber_only_suggests_cookies(self) -> None:
        error = self._translate("ERROR: This video is only available to subscribers")
        assert "subscriber-only" in str(error)
        assert "cookies_from_browser" in error.hint

    def test_subscriber_only_with_cookies_already_set(self) -> None:
        error = self._translate("ERROR: subscriber only", cookies_from_browser="chrome")
        assert "already being read from chrome" in error.hint

    def test_unknown_error_keeps_the_original(self) -> None:
        error = self._translate("ERROR: something new and strange")
        assert "something new and strange" in error.hint


class TestMigrationV2:
    def _v1_database(self, path: Path) -> sqlite3.Connection:
        conn = db.connect(path)
        with conn:
            schema._migration_v1(conn)
            conn.execute("PRAGMA user_version = 1")
        with conn:
            conn.execute(
                "INSERT INTO sources (id, type, path, created_at) "
                "VALUES ('s1', 'youtube', '/m/a.mp4', '2026-01-01')"
            )
            conn.execute(
                "INSERT INTO jobs (id, source_id, status, created_at, updated_at) "
                "VALUES ('j1', 's1', 'done', '2026-01-01', '2026-01-01')"
            )
        return conn

    def test_existing_rows_survive_and_twitch_is_allowed(self, tmp_path: Path) -> None:
        conn = self._v1_database(tmp_path / "v1.db")

        assert schema.migrate(conn) == schema.SCHEMA_VERSION

        # The rebuild must not have cascaded the DROP through to jobs.
        assert conn.execute("SELECT id FROM jobs").fetchall()[0][0] == "j1"
        assert conn.execute("SELECT type FROM sources").fetchall()[0][0] == "youtube"
        with conn:
            conn.execute(
                "INSERT INTO sources (id, type, path, created_at) "
                "VALUES ('s2', 'twitch', '/m/b.mp4', '2026-01-01')"
            )
        with pytest.raises(sqlite3.IntegrityError), conn:
            conn.execute(
                "INSERT INTO sources (id, type, path, created_at) "
                "VALUES ('s3', 'vimeo', '/m/c.mp4', '2026-01-01')"
            )

    def test_foreign_keys_are_back_on_and_still_cascade(self, tmp_path: Path) -> None:
        conn = self._v1_database(tmp_path / "v1.db")
        schema.migrate(conn)

        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        with conn:
            conn.execute("DELETE FROM sources WHERE id = 's1'")
        assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


class TestProgress:
    def test_hls_fragment_counts_drive_progress(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Downloading(_FakeYoutubeDL):
            def process_ie_result(self, info: dict, download: bool = True) -> dict:
                hook = self.options["progress_hooks"][0]
                # Twitch HLS: no byte total, just fragment counters.
                hook({"status": "downloading", "fragment_index": 25, "fragment_count": 100})
                hook({"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100})
                return info

        monkeypatch.setattr(Downloading, "resolved", {"is_live": False})
        monkeypatch.setattr(yt_dlp, "YoutubeDL", Downloading)
        seen: list[float] = []

        # The fake writes no file, so ingest ends in an error after the hooks ran.
        with pytest.raises(ingest.IngestError, match="no media file"):
            ingest.ingest_twitch("https://www.twitch.tv/videos/1", on_progress=seen.append)

        assert seen == [0.25, 0.5]
