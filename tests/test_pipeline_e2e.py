"""Full-pipeline integration test against real media.

Runs every stage for real — ffmpeg, faster-whisper, MediaPipe, libass — on an
actual video with actual faces and speech. Only the language model's *answer* is
scripted, because clip selection is a judgement call that needs an API key and
would make the test non-deterministic anyway. Everything the pipeline does with
that answer is exercised end to end.

This is the test that catches integration failures unit tests structurally
cannot: a crop path whose segments don't tile the clip, a caption offset that
desyncs on a real transcript, an export that succeeds on synthetic colour bars
and fails on real footage.

Point it at a file to run it::

    set CLIPPY_E2E_MEDIA=C:\\path\\to\\clip.mp4
    pytest -m e2e -q

Use something short — a few minutes. Transcription dominates the runtime.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from clippy.config import Settings
from clippy.db import store
from clippy.db.models import Job, new_id
from clippy.pipeline import Stage, ingest
from clippy.pipeline.export import ratio_dimensions
from clippy.pipeline.ffmpeg import probe
from clippy.pipeline.reframe.croppath import CropPath
from clippy.pipeline.runner import JobWorkspace, PipelineRunner
from clippy.pipeline.transcript import Transcript
from clippy.providers import ClipCandidates, DetectionConfig, LLMProvider, ProviderStatus

pytestmark = [pytest.mark.slow, pytest.mark.e2e]

ENV_MEDIA = "CLIPPY_E2E_MEDIA"


def media_path() -> Path:
    raw = os.environ.get(ENV_MEDIA)
    if not raw:
        pytest.skip(f"Set {ENV_MEDIA} to a media file to run the end-to-end test.")
    path = Path(raw)
    if not path.exists():
        pytest.skip(f"{ENV_MEDIA} points at {path}, which does not exist.")
    return path


class ScriptedProvider(LLMProvider):
    """Stands in for the LLM, returning spans anchored to the real transcript.

    Deliberately returns two overlapping candidates and one that is far too
    short, so dedupe and duration clamping are exercised rather than bypassed.
    """

    name = "scripted"
    requires_key = False

    def __init__(self) -> None:
        super().__init__("scripted-model")
        self.calls = 0

    async def _complete(self, system: str, user: str, config: DetectionConfig) -> str:
        raise AssertionError("detect_highlights is overridden; _complete is unused")

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus(name=self.name, available=True)

    async def detect_highlights(self, window, config) -> ClipCandidates:
        self.calls += 1
        first, last = window.first_word, window.last_word
        span = max(1, (last - first) // 4)

        return ClipCandidates.model_validate(
            {
                "clips": [
                    {
                        "start_word_index": first,
                        "end_word_index": min(last, first + span),
                        "title": "Opening stretch",
                        "hook": "first words",
                        "score": 91,
                        "reason": "Scripted candidate covering the start of the window.",
                    },
                    {
                        # Overlaps the first by design — dedupe must drop it.
                        "start_word_index": first + span // 4,
                        "end_word_index": min(last, first + span),
                        "title": "Overlapping duplicate",
                        "score": 60,
                        "reason": "Should be removed by dedupe.",
                    },
                    {
                        "start_word_index": min(last - 1, first + 2 * span),
                        "end_word_index": min(last, first + 3 * span),
                        "title": "Later stretch",
                        "hook": "second span",
                        "score": 78,
                        "reason": "Scripted candidate from later in the window.",
                    },
                    {
                        # Two words long — must be dropped or extended, never
                        # exported as a sub-second clip.
                        "start_word_index": first,
                        "end_word_index": min(last, first + 2),
                        "title": "Far too short",
                        "score": 55,
                        "reason": "Should not survive duration clamping.",
                    },
                ]
            }
        )


@pytest.fixture(scope="module", autouse=True)
def clippy_home(tmp_path_factory):
    """One throwaway home for the whole module.

    Shadows the function-scoped autouse fixture in conftest by name. Without
    this the pipeline runs once and then every assertion looks at a fresh empty
    database, because the shared fixture repoints CLIPPY_HOME per test.
    """
    from _pytest.monkeypatch import MonkeyPatch
    from clippy import db, paths

    patcher = MonkeyPatch()
    home = tmp_path_factory.mktemp("e2e_home")
    patcher.setenv(paths.ENV_HOME, str(home))
    db.reset_connections()

    yield home

    patcher.undo()
    db.reset_connections()


@pytest.fixture(scope="module")
def pipeline_result(clippy_home):
    """Run the whole pipeline once; every test below inspects the result."""
    from clippy import db
    from clippy.pipeline import runner as runner_module

    source_file = media_path()
    db.init()

    source = store.create_source(ingest.ingest_file(source_file, title="E2E source"))

    settings = Settings()
    settings.whisper.model = "tiny"  # keeps the run to a couple of minutes
    settings.clips.max_clips = 3
    settings.clips.min_duration_s = 12.0
    settings.clips.max_duration_s = 45.0
    settings.export.caption_style = "bold_pop"
    settings.export.ratio = "9:16"

    job = store.create_job(Job(id=new_id(), source_id=source.id, provider="scripted", settings={}))

    provider = ScriptedProvider()
    original = runner_module.build_provider
    runner_module.build_provider = lambda *args, **kwargs: provider

    events: list = []
    try:
        pipeline_runner = PipelineRunner(job, source, settings=settings, on_progress=events.append)
        import asyncio

        clips = asyncio.run(pipeline_runner.run())
    finally:
        runner_module.build_provider = original

    return {
        "source": source,
        "job": job,
        "clips": clips,
        "events": events,
        "workspace": JobWorkspace(job.id),
        "settings": settings,
        "provider": provider,
    }


class TestPipelineCompletes:
    def test_job_reaches_done(self, pipeline_result) -> None:
        job = store.get_job(pipeline_result["job"].id)

        assert job is not None
        assert job.status == "done"
        assert job.error is None
        assert job.progress == pytest.approx(1.0)

    def test_every_stage_reported_progress(self, pipeline_result) -> None:
        stages = {event.stage for event in pipeline_result["events"]}

        assert stages == set(Stage)

    def test_progress_never_goes_backwards(self, pipeline_result) -> None:
        overall = [event.overall for event in pipeline_result["events"]]

        assert overall == sorted(overall)
        assert overall[-1] == pytest.approx(1.0)


class TestTranscription:
    def test_transcript_has_word_timings(self, pipeline_result) -> None:
        transcript = Transcript.load(pipeline_result["workspace"].transcript)

        assert len(transcript.words) > 50
        assert all(word.end >= word.start for word in transcript.words)

    def test_words_are_chronological(self, pipeline_result) -> None:
        transcript = Transcript.load(pipeline_result["workspace"].transcript)
        starts = [word.start for word in transcript.words]

        assert starts == sorted(starts)

    def test_transcript_row_matches_the_file(self, pipeline_result) -> None:
        transcript = Transcript.load(pipeline_result["workspace"].transcript)
        row = store.get_transcript(pipeline_result["job"].id)

        assert row is not None
        assert row.word_count == len(transcript.words)

    def test_silence_map_was_built(self, pipeline_result) -> None:
        assert pipeline_result["workspace"].silences.exists()


class TestClipSelection:
    def test_clips_were_produced(self, pipeline_result) -> None:
        assert len(pipeline_result["clips"]) > 0

    def test_overlapping_duplicate_was_deduped(self, pipeline_result) -> None:
        titles = {clip.title for clip in pipeline_result["clips"]}

        assert "Overlapping duplicate" not in titles

    def test_every_clip_respects_the_duration_range(self, pipeline_result) -> None:
        settings = pipeline_result["settings"]

        for clip in pipeline_result["clips"]:
            assert settings.clips.min_duration_s - 1.0 <= clip.duration_s
            assert clip.duration_s <= settings.clips.max_duration_s + 1.0

    def test_clips_are_ranked_by_score(self, pipeline_result) -> None:
        scores = [clip.score for clip in pipeline_result["clips"]]

        assert scores == sorted(scores, reverse=True)
        assert [c.rank for c in pipeline_result["clips"]] == list(
            range(1, len(pipeline_result["clips"]) + 1)
        )

    def test_boundaries_lie_inside_the_source(self, pipeline_result) -> None:
        duration = pipeline_result["source"].duration_s

        for clip in pipeline_result["clips"]:
            assert 0 <= clip.start_s < clip.end_s <= duration + 1.0

    def test_word_indices_map_back_to_the_transcript(self, pipeline_result) -> None:
        transcript = Transcript.load(pipeline_result["workspace"].transcript)

        for clip in pipeline_result["clips"]:
            assert 0 <= clip.start_word <= clip.end_word < len(transcript.words)
            # The recorded seconds must agree with the words they point at,
            # which is the whole reason detection returns indices not times.
            word_start = transcript.words[clip.start_word].start
            assert abs(clip.start_s - word_start) < 2.0


class TestReframe:
    def test_a_crop_path_exists_per_clip(self, pipeline_result) -> None:
        workspace = pipeline_result["workspace"]

        for clip in pipeline_result["clips"]:
            assert workspace.crop_path(clip.id).exists()

    def test_segments_tile_the_clip_without_gaps(self, pipeline_result) -> None:
        workspace = pipeline_result["workspace"]

        for clip in pipeline_result["clips"]:
            path = CropPath.load(workspace.crop_path(clip.id))
            segments = path.segments

            assert segments
            assert segments[0].start_s == pytest.approx(0.0, abs=0.05)
            assert segments[-1].end_s == pytest.approx(clip.duration_s, abs=0.05)
            for earlier, later in zip(segments, segments[1:], strict=False):
                # A gap or overlap desyncs the concatenated render from audio.
                assert earlier.end_s == pytest.approx(later.start_s, abs=0.01)

    def test_crops_stay_inside_the_frame(self, pipeline_result) -> None:
        workspace = pipeline_result["workspace"]

        for clip in pipeline_result["clips"]:
            path = CropPath.load(workspace.crop_path(clip.id))
            for segment in path.segments:
                assert segment.width <= path.source_width
                assert segment.height <= path.source_height
                for keyframe in segment.keyframes:
                    assert -1 <= keyframe.x <= path.source_width - segment.width + 1
                    assert -1 <= keyframe.y <= path.source_height - segment.height + 1

    def test_crop_dimensions_are_even(self, pipeline_result) -> None:
        # Odd dimensions fail an h264 yuv420p encode.
        workspace = pipeline_result["workspace"]

        for clip in pipeline_result["clips"]:
            for segment in CropPath.load(workspace.crop_path(clip.id)).segments:
                assert segment.width % 2 == 0
                assert segment.height % 2 == 0

    def test_faces_were_actually_found(self, pipeline_result) -> None:
        """At least one clip should track a subject, not centre-crop everything.

        A talking-head source that produces only GENERAL segments means face
        detection silently did nothing — the failure this whole stage exists to
        avoid.
        """
        from clippy.pipeline.reframe.croppath import Strategy

        workspace = pipeline_result["workspace"]
        strategies = {
            segment.strategy
            for clip in pipeline_result["clips"]
            for segment in CropPath.load(workspace.crop_path(clip.id)).segments
        }

        assert strategies & {Strategy.TRACK, Strategy.WIDE}, (
            f"No subject was tracked in any clip (strategies: {strategies}). "
            "Face detection produced nothing on real talking-head footage."
        )


class TestExports:
    def test_one_export_per_clip(self, pipeline_result) -> None:
        for clip in pipeline_result["clips"]:
            assert len(store.list_exports(clip.id)) == 1

    def test_exported_files_exist_and_are_not_empty(self, pipeline_result) -> None:
        for clip in pipeline_result["clips"]:
            path = Path(store.list_exports(clip.id)[0].path)
            assert path.exists()
            assert path.stat().st_size > 10_000

    def test_exports_are_vertical_with_audio(self, pipeline_result) -> None:
        expected = ratio_dimensions("9:16")

        for clip in pipeline_result["clips"]:
            info = probe(Path(store.list_exports(clip.id)[0].path))
            assert (info.width, info.height) == expected
            assert info.has_audio
            assert info.video_codec == "h264"

    def test_export_duration_matches_the_clip(self, pipeline_result) -> None:
        for clip in pipeline_result["clips"]:
            info = probe(Path(store.list_exports(clip.id)[0].path))
            assert info.duration_s == pytest.approx(clip.duration_s, abs=0.6)

    def test_clips_are_marked_exported(self, pipeline_result) -> None:
        for clip in pipeline_result["clips"]:
            assert store.get_clip(clip.id).status == "exported"


class TestResume:
    def test_rerunning_reuses_completed_stages(self, pipeline_result) -> None:
        """A second run must not redo transcription.

        Resume is the difference between a rate-limit retry costing seconds and
        costing another full transcription pass.
        """
        from clippy.pipeline import runner as runner_module

        workspace = pipeline_result["workspace"]
        transcript_mtime = workspace.transcript.stat().st_mtime

        # Re-running the *same* job is exactly what the retry endpoint does; a
        # new job id would get a fresh workspace and prove nothing.
        job = pipeline_result["job"]

        provider = ScriptedProvider()
        original = runner_module.build_provider
        runner_module.build_provider = lambda *args, **kwargs: provider
        try:
            import asyncio

            runner = PipelineRunner(
                job, pipeline_result["source"], settings=pipeline_result["settings"]
            )
            asyncio.run(runner.run())
        finally:
            runner_module.build_provider = original

        assert workspace.transcript.stat().st_mtime == transcript_mtime
        assert provider.calls == 0, "Highlight detection re-ran despite existing clips."
