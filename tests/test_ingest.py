"""URL classification, timestamp parsing, and time-range handling.

These are the parts of ingestion that run before yt-dlp is touched, so they can
be tested without a network or a real download. The download itself is covered
by the pipeline end-to-end test against a local file.
"""

from __future__ import annotations

import pytest
from autoclip.pipeline import ingest


class TestUrlClassification:
    @pytest.mark.parametrize(
        "url",
        [
            "https://youtube.com/watch?v=abc",
            "https://www.youtube.com/watch?v=abc",
            "https://m.youtube.com/watch?v=abc",
            "https://youtu.be/abc",
            "https://www.youtube-nocookie.com/embed/abc",
        ],
    )
    def test_youtube_hosts(self, url: str) -> None:
        assert ingest.is_youtube_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "https://twitch.tv/videos/123456",
            "https://www.twitch.tv/videos/123456",
            "https://m.twitch.tv/videos/123456",
            "https://clips.twitch.tv/SomeClipSlug",
            "https://www.twitch.tv/somestreamer/clip/SomeClipSlug",
        ],
    )
    def test_twitch_hosts(self, url: str) -> None:
        assert ingest.is_twitch_url(url)
        assert not ingest.is_youtube_url(url)

    def test_lookalike_domains_are_not_matched(self) -> None:
        # Suffix matching must respect the dot boundary, or "nottwitch.tv" and
        # "myyoutube.com" would both pass.
        assert not ingest.is_twitch_url("https://nottwitch.tv/videos/1")
        assert not ingest.is_youtube_url("https://myyoutube.com/watch?v=abc")

    @pytest.mark.parametrize(
        "url",
        [
            "https://twitch.tv/videos/1",
            "https://vimeo.com/12345",
            "https://kick.com/somestreamer",
            "http://example.com/video.mp4",
        ],
    )
    def test_remote_urls_are_accepted_broadly(self, url: str) -> None:
        # Deliberately permissive — deciding what yt-dlp supports is yt-dlp's job.
        assert ingest.is_remote_url(url)

    @pytest.mark.parametrize(
        "value",
        ["C:/videos/clip.mp4", "/home/lin/clip.mp4", "clip.mp4", "", "ftp://example.com/v"],
    )
    def test_non_urls_are_rejected(self, value: str) -> None:
        assert not ingest.is_remote_url(value)

    def test_source_type_labels(self) -> None:
        assert ingest.source_type_for("https://youtu.be/abc") == "youtube"
        assert ingest.source_type_for("https://twitch.tv/videos/1") == "twitch"
        assert ingest.source_type_for("https://vimeo.com/1") == "url"


class TestTimestampParsing:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1h2m3s", 3723),
            ("2h", 7200),
            ("90m", 5400),
            ("45s", 45),
            ("45", 45),
            ("1:02:03", 3723),
            ("2:30", 150),
            ("0", 0),
        ],
    )
    def test_valid(self, value: str, expected: float) -> None:
        assert ingest.parse_timestamp(value) == expected

    @pytest.mark.parametrize("value", ["", "  ", "banana", "1:2:3:4", "h", "12x", "-30"])
    def test_invalid_returns_none(self, value: str) -> None:
        assert ingest.parse_timestamp(value) is None

    def test_reads_twitch_style_share_timestamp(self) -> None:
        url = "https://www.twitch.tv/videos/123456?t=1h20m30s"
        assert ingest.timestamp_from_url(url) == 4830

    def test_reads_youtube_style_share_timestamp(self) -> None:
        assert ingest.timestamp_from_url("https://youtu.be/abc?t=90") == 90

    def test_no_timestamp_is_none(self) -> None:
        assert ingest.timestamp_from_url("https://twitch.tv/videos/123456") is None


class TestRangeValidation:
    def test_end_before_start_is_rejected(self) -> None:
        with pytest.raises(ingest.IngestError, match="after start"):
            ingest._validate_range(600.0, 300.0)

    def test_negative_start_is_rejected(self) -> None:
        with pytest.raises(ingest.IngestError, match="negative"):
            ingest._validate_range(-5.0, None)

    def test_zero_start_with_no_end_means_no_range(self) -> None:
        # Passing a range to yt-dlp forces a re-encode at the cut points, so an
        # empty range must collapse to None rather than costing one for nothing.
        assert ingest._validate_range(0.0, None) == (None, None)

    def test_valid_range_passes_through(self) -> None:
        assert ingest._validate_range(60.0, 300.0) == (60.0, 300.0)

    def test_range_func_reports_the_requested_window(self) -> None:
        window = ingest._range_func(60.0, 300.0)({}, None)

        assert window == [{"start_time": 60.0, "end_time": 300.0}]

    def test_open_ended_range_runs_to_the_end(self) -> None:
        window = ingest._range_func(60.0, None)({}, None)

        assert window[0]["start_time"] == 60.0
        assert window[0]["end_time"] == float("inf")


class TestIngestUrlGuards:
    def test_rejects_a_non_url(self) -> None:
        with pytest.raises(ingest.IngestError, match="not a URL"):
            ingest.ingest_url("C:/videos/clip.mp4")

    def test_rejects_a_bad_range_before_downloading(self) -> None:
        # No network is touched: validation happens before yt-dlp is invoked.
        with pytest.raises(ingest.IngestError, match="after start"):
            ingest.ingest_url("https://twitch.tv/videos/1", start_s=600.0, end_s=60.0)

    def test_youtube_alias_still_exists(self) -> None:
        assert ingest.ingest_youtube is ingest.ingest_url
