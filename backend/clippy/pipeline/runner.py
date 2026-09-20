"""Pipeline orchestration.

Runs a job stage by stage, writing each stage's artifacts into
``work/{job_id}/``. Because every stage's output is a file on disk, a retry
skips everything already done and resumes at the stage that failed — which
matters when stage two took eleven minutes and stage four hit a rate limit.

Progress is reported through a callback rather than written directly, so the
same runner serves the CLI (a progress bar) and the web API (an SSE stream).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .. import paths
from ..config import Settings
from ..config import load as load_settings
from ..db import store
from ..db.models import Clip, Export, Job, Source, new_id, utcnow
from ..db.models import Transcript as TranscriptRow
from ..providers import build_provider, detection_config
from ..providers.router import ProviderRouter
from . import Stage, asr, captions, export, ffmpeg, highlights, prepare, transcribe
from .prepare import Silence
from .reframe import ReframeConfig, build_crop_path
from .reframe.croppath import CropPath
from .transcript import Transcript

log = logging.getLogger(__name__)

#: Weight of each stage in the overall progress bar. Rough proportions of wall
#: time on a mid-range machine — transcription and export dominate.
STAGE_WEIGHTS: dict[Stage, float] = {
    Stage.INGEST: 0.09,
    Stage.PREPARE: 0.05,
    Stage.TRANSCRIBE: 0.40,
    Stage.HIGHLIGHTS: 0.20,
    Stage.REFRAME: 0.25,
    Stage.CAPTIONS: 0.01,
}


class JobCancelled(RuntimeError):
    """The job was cancelled by the user."""


class PipelineError(RuntimeError):
    """A stage failed in a way the user should see."""

    def __init__(self, message: str, *, stage: Stage) -> None:
        super().__init__(message)
        self.stage = stage


@dataclass
class ProgressEvent:
    stage: Stage
    #: Progress within the stage, 0..1.
    stage_progress: float
    #: Progress across the whole job, 0..1.
    overall: float
    message: str = ""


ProgressHandler = Callable[[ProgressEvent], None]


class JobWorkspace:
    """Paths for one job's intermediate artifacts."""

    def __init__(self, job_id: str) -> None:
        self.root = paths.job_work_dir(job_id)
        self.root.mkdir(parents=True, exist_ok=True)

    @property
    def audio(self) -> Path:
        return self.root / "audio.wav"

    @property
    def transcript(self) -> Path:
        return self.root / "transcript.json"

    @property
    def silences(self) -> Path:
        return self.root / "silences.json"

    @property
    def thumbnails(self) -> Path:
        return self.root / "thumbnails"

    def crop_path(self, clip_id: str) -> Path:
        return self.root / "crops" / f"{clip_id}.json"

    @property
    def captions_dir(self) -> Path:
        return self.root / "captions"


class PipelineRunner:
    """Executes one job."""

    def __init__(
        self,
        job: Job,
        source: Source,
        *,
        settings: Settings | None = None,
        on_progress: ProgressHandler | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> None:
        self.job = job
        self.source = source
        self.settings = settings or load_settings()
        self.on_progress = on_progress
        self._is_cancelled = is_cancelled or (lambda: False)
        self.workspace = JobWorkspace(job.id)
        self._completed_weight = 0.0

    # -- progress ----------------------------------------------------------

    def _check_cancelled(self) -> None:
        if self._is_cancelled():
            raise JobCancelled("Job cancelled.")

    def _emit(
        self,
        stage: Stage,
        stage_progress: float,
        message: str = "",
        *,
        overall: float | None = None,
    ) -> None:
        if overall is None:
            overall = self._completed_weight + STAGE_WEIGHTS[stage] * stage_progress
        overall = min(1.0, overall)
        store.update_job(self.job.id, current_stage=stage.value, progress=round(overall, 4))
        if self.on_progress:
            self.on_progress(
                ProgressEvent(
                    stage=stage,
                    stage_progress=stage_progress,
                    overall=overall,
                    message=message or stage.label,
                )
            )

    def _finish_stage(self, stage: Stage) -> None:
        """Mark a stage complete.

        ``overall`` is passed explicitly rather than derived. Deriving it after
        incrementing the accumulated weight counts the stage twice, so the bar
        jumped past the true total and then snapped backwards when the next
        stage reported 0 — visibly, at every stage boundary.
        """
        self._completed_weight += STAGE_WEIGHTS[stage]
        self._emit(stage, 1.0, overall=self._completed_weight)

    def _stage_progress(self, stage: Stage) -> Callable[[float], None]:
        def report(fraction: float) -> None:
            self._emit(stage, max(0.0, min(1.0, fraction)))

        return report

    # -- entry point -------------------------------------------------------

    async def run(self) -> list[Clip]:
        """Run analysis through review readiness; rendering is a separate job."""
        store.update_job(self.job.id, status="running", started_at=utcnow(), error=None)

        try:
            self._stage_ingest()
            audio = self._stage_prepare()
            transcript = self._stage_transcribe(audio)
            silences = self._load_or_detect_silences(audio)
            clips = await self._stage_highlights(transcript, silences)
            self._stage_reframe(clips, transcript)
            self._stage_captions(clips, transcript)
        except JobCancelled:
            store.update_job(self.job.id, status="cancelled", finished_at=utcnow(), progress=0.0)
            raise
        except Exception as exc:
            log.exception("Job %s failed.", self.job.id)
            store.update_job(self.job.id, status="failed", error=str(exc), finished_at=utcnow())
            raise

        store.update_job(
            self.job.id,
            status="done",
            current_stage=Stage.READY.value,
            progress=1.0,
            finished_at=utcnow(),
        )
        return clips

    # -- stages ------------------------------------------------------------

    def _stage_ingest(self) -> None:
        stage = Stage.INGEST
        self._check_cancelled()
        if self.source.path and Path(self.source.path).is_file():
            self._finish_stage(stage)
            return
        if self.source.type != "youtube" or not self.source.url:
            raise PipelineError("The source media is missing.", stage=stage)

        from . import ingest

        self._emit(stage, 0.0, "Downloading YouTube video")
        downloaded = ingest.ingest_youtube(
            self.source.url,
            self.settings.ingest,
            source_id=self.source.id,
            on_progress=self._stage_progress(stage),
        )
        store.update_source(downloaded)
        self.source = downloaded
        self._finish_stage(stage)

    def _stage_prepare(self) -> Path:
        stage = Stage.PREPARE
        self._check_cancelled()
        source_path = Path(self.source.path)

        if self.workspace.audio.exists() and self.workspace.audio.stat().st_size > 0:
            log.info("Reusing existing audio for job %s.", self.job.id)
        else:
            self._emit(stage, 0.0, "Extracting audio")
            prepare.extract_audio(
                source_path,
                self.workspace.audio,
                duration_s=self.source.duration_s,
                on_progress=self._stage_progress(stage),
            )

        if self.source.has_video and not self.workspace.thumbnails.exists():
            prepare.generate_thumbnails(source_path, self.workspace.thumbnails)

        self._finish_stage(stage)
        return self.workspace.audio

    def _stage_transcribe(self, audio: Path) -> Transcript:
        stage = Stage.TRANSCRIBE
        self._check_cancelled()

        if self.workspace.transcript.exists():
            log.info("Reusing existing transcript for job %s.", self.job.id)
            transcript = Transcript.load(self.workspace.transcript)
            self._finish_stage(stage)
            return transcript

        self._emit(stage, 0.0, "Transcribing")
        transcript = asr.transcribe_with_router(
            audio,
            self.settings,
            job_id=self.job.id,
            work_dir=self.workspace.root,
            duration_s=self.source.duration_s,
            on_progress=self._stage_progress(stage),
            cancelled=self._is_cancelled,
        )

        if self.settings.whisper.diarization:
            from ..config import HF_TOKEN_KEY, get_secret

            self._emit(stage, 0.95, "Identifying speakers")
            transcribe.diarize(audio, transcript, hf_token=get_secret(HF_TOKEN_KEY, self.settings))

        transcript.save(self.workspace.transcript)
        store.upsert_transcript(
            TranscriptRow(
                job_id=self.job.id,
                json_path=str(self.workspace.transcript),
                language=transcript.language,
                model=transcript.model,
                has_diarization=transcript.has_diarization,
                word_count=len(transcript.words),
                source=transcript.source,
            )
        )

        self._finish_stage(stage)
        return transcript

    def _load_or_detect_silences(self, audio: Path) -> list[Silence]:
        if self.workspace.silences.exists():
            raw = json.loads(self.workspace.silences.read_text(encoding="utf-8"))
            return [Silence(**item) for item in raw]

        silences = prepare.detect_silences(audio)
        self.workspace.silences.write_text(
            json.dumps([{"start": s.start, "end": s.end} for s in silences]),
            encoding="utf-8",
        )
        return silences

    async def _stage_highlights(
        self, transcript: Transcript, silences: list[Silence]
    ) -> list[Clip]:
        stage = Stage.HIGHLIGHTS
        self._check_cancelled()

        existing = store.list_clips(self.job.id)
        if existing:
            log.info("Reusing %d existing clips for job %s.", len(existing), self.job.id)
            self._finish_stage(stage)
            return existing

        provider_name = self.job.provider or "automatic"
        provider = (
            ProviderRouter(self.settings, job_id=self.job.id)
            if provider_name in ("automatic", "router")
            else build_provider(provider_name, self.settings)
        )
        config = detection_config(self.settings)

        self._emit(stage, 0.0, f"Finding highlights with {provider.name}")
        clips = await highlights.detect(
            transcript,
            provider,
            config,
            job_id=self.job.id,
            silences=silences,
            on_progress=self._stage_progress(stage),
        )

        store.replace_clips(self.job.id, clips)
        self._finish_stage(stage)
        return clips

    def _stage_reframe(self, clips: list[Clip], transcript: Transcript) -> dict[str, CropPath]:
        stage = Stage.REFRAME
        self._check_cancelled()
        source_path = Path(self.source.path)

        crop_paths: dict[str, CropPath] = {}

        if not self.source.has_video:
            # Audio-only sources render as captions on a solid background, so
            # there is nothing to reframe.
            self._finish_stage(stage)
            return crop_paths

        config = ReframeConfig(
            aspect_w=9 if self.settings.export.ratio == "9:16" else 1,
            aspect_h=16 if self.settings.export.ratio == "9:16" else 1,
        )
        if self.settings.export.ratio == "16:9":
            config = ReframeConfig(aspect_w=16, aspect_h=9)

        for index, clip in enumerate(clips):
            self._check_cancelled()
            cached = self.workspace.crop_path(clip.id)
            if cached.exists():
                crop_paths[clip.id] = CropPath.load(cached)
            else:
                crop_paths[clip.id] = build_crop_path(
                    source_path,
                    start_s=clip.start_s,
                    end_s=clip.end_s,
                    transcript=transcript,
                    config=config,
                )
                crop_paths[clip.id].save(cached)

            self._emit(stage, (index + 1) / len(clips), f"Reframing clip {index + 1}")

        self._finish_stage(stage)
        return crop_paths

    def _stage_captions(self, clips: list[Clip], transcript: Transcript) -> None:
        stage = Stage.CAPTIONS
        self._check_cancelled()
        # Caption files are written during export, where the output dimensions
        # are known. This stage validates the style so a typo fails fast rather
        # than after the reframe work is already done.
        captions.get_style(self.settings.export.caption_style)
        self._finish_stage(stage)

    def _stage_export(
        self,
        clips: list[Clip],
        transcript: Transcript,
        crop_paths: dict[str, CropPath],
    ) -> None:
        stage = Stage.EXPORT
        self._check_cancelled()

        style = captions.get_style(self.settings.export.caption_style)
        ratio = self.settings.export.ratio
        source_path = Path(self.source.path)
        destination_dir = paths.exports_dir() / self.job.id
        destination_dir.mkdir(parents=True, exist_ok=True)

        for index, clip in enumerate(clips):
            self._check_cancelled()

            crop_path = crop_paths.get(clip.id) or self._fallback_crop_path(clip, ratio)
            words = transcript.slice(clip.start_word, clip.end_word)
            destination = destination_dir / export.output_filename(
                clip.title or f"clip-{clip.rank}", ratio
            )

            request = export.ExportRequest(
                source=source_path,
                destination=destination,
                start_s=clip.start_s,
                end_s=clip.end_s,
                crop_path=crop_path,
                words=words,
                style=style,
                ratio=ratio,
            )

            def clip_progress(fraction: float, i: int = index) -> None:
                self._emit(stage, (i + fraction) / len(clips), f"Exporting clip {i + 1}")

            export.export_clip(
                request,
                work_dir=self.workspace.captions_dir,
                settings=self.settings.export,
                on_progress=clip_progress,
                cancelled=self._is_cancelled,
            )

            store.create_export(
                Export(
                    id=new_id(),
                    clip_id=clip.id,
                    path=str(destination),
                    ratio=ratio,
                    style=style.key,
                    size_bytes=destination.stat().st_size,
                )
            )
            store.update_clip(clip.id, status="exported")

        self._finish_stage(stage)

    def _fallback_crop_path(self, clip: Clip, ratio: str) -> CropPath:
        """Centre crop for sources with no reframe data (audio-only, or a failure)."""
        from .reframe.croppath import centre_crop

        width, height = export.ratio_dimensions(ratio)
        info = ffmpeg.probe(Path(self.source.path))
        return centre_crop(
            info.width or width,
            info.height or height,
            clip.end_s - clip.start_s,
            aspect_w=9 if ratio == "9:16" else (1 if ratio == "1:1" else 16),
            aspect_h=16 if ratio == "9:16" else (1 if ratio == "1:1" else 9),
        )


async def run_job(
    job_id: str,
    *,
    settings: Settings | None = None,
    on_progress: ProgressHandler | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> list[Clip]:
    """Load a job and run it to completion."""
    job = store.get_job(job_id)
    if job is None:
        raise PipelineError(f"Job {job_id} not found.", stage=Stage.PREPARE)

    source = store.get_source(job.source_id)
    if source is None:
        raise PipelineError(f"Source {job.source_id} not found.", stage=Stage.PREPARE)

    runner = PipelineRunner(
        job, source, settings=settings, on_progress=on_progress, is_cancelled=is_cancelled
    )
    # The pipeline is mostly blocking work (ffmpeg, Whisper, MediaPipe) with one
    # async stage. Running it in a worker thread keeps the web server's event
    # loop responsive while a job is going.
    return await asyncio.get_running_loop().run_in_executor(None, _run_sync, runner)


def _run_sync(runner: PipelineRunner) -> list[Clip]:
    return asyncio.run(runner.run())


def run_render_job(
    job: Job,
    source: Source,
    on_progress: ProgressHandler | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> Export:
    """Render one queued preview/final job and persist its artifact."""
    cancelled = is_cancelled or (lambda: False)
    if not job.clip_id:
        raise PipelineError("Render job has no clip.", stage=Stage.RENDER)
    clip = store.get_clip(job.clip_id)
    if clip is None:
        raise PipelineError("Render job clip was deleted.", stage=Stage.RENDER)

    stage = Stage.PREVIEW if job.kind == "preview" else Stage.RENDER
    store.update_job(
        job.id,
        status="running",
        current_stage=stage.value,
        started_at=utcnow(),
        error=None,
    )

    def report(fraction: float) -> None:
        fraction = max(0.0, min(1.0, fraction))
        store.update_job(job.id, current_stage=stage.value, progress=round(fraction, 4))
        if on_progress:
            on_progress(ProgressEvent(stage, fraction, fraction, stage.label))

    try:
        if cancelled():
            raise JobCancelled("Job cancelled.")

        analysis_job_id = job.parent_job_id or clip.job_id
        workspace = JobWorkspace(analysis_job_id)
        if not workspace.transcript.exists():
            raise PipelineError("The analysis transcript is missing.", stage=stage)
        transcript = Transcript.load(workspace.transcript)
        edit = store.get_clip_edit(clip.id)
        if edit and edit.edited_words:
            from .transcript import Word

            words = [Word(**word) for word in edit.edited_words]
        else:
            words = transcript.slice(clip.start_word, clip.end_word)

        settings = Settings.model_validate(job.settings) if job.settings else load_settings()
        settings.export.ratio = "9:16"
        settings.export.crf = 28 if job.kind == "preview" else 21
        settings.export.preset = "ultrafast" if job.kind == "preview" else "veryfast"
        settings.export.prefer_hardware_encoder = (
            False if job.kind == "preview" else (settings.export.prefer_hardware_encoder)
        )
        style = captions.get_style(edit.caption_style if edit else settings.export.caption_style)

        cached = workspace.crop_path(clip.id)
        if cached.exists():
            crop_path = CropPath.load(cached)
        else:
            from .reframe.croppath import centre_crop

            crop_path = centre_crop(
                source.width or 1920,
                source.height or 1080,
                clip.end_s - clip.start_s,
                aspect_w=9,
                aspect_h=16,
            )

        if job.kind == "preview":
            destination = workspace.root / "previews" / f"{clip.id}.mp4"
            output_width, output_height = 540, 960
            artifact_kind = "preview"
        else:
            destination = (
                paths.exports_dir()
                / analysis_job_id
                / export.output_filename(clip.title or f"clip-{clip.rank}", "9:16")
            )
            output_width = output_height = None
            artifact_kind = "final"

        export.export_clip(
            export.ExportRequest(
                source=Path(source.path),
                destination=destination,
                start_s=clip.start_s,
                end_s=clip.end_s,
                crop_path=crop_path,
                words=words,
                style=style,
                ratio="9:16",
                output_width=output_width,
                output_height=output_height,
            ),
            work_dir=workspace.captions_dir,
            settings=settings.export,
            on_progress=report,
            cancelled=cancelled,
        )
        record = Export(
            id=new_id(),
            clip_id=clip.id,
            path=str(destination),
            ratio="9:16",
            style=style.key,
            size_bytes=destination.stat().st_size,
            job_id=job.id,
            kind=artifact_kind,
        )
        store.create_export(record)
        if artifact_kind == "final":
            store.update_clip(clip.id, status="exported")
        store.update_job(
            job.id,
            status="done",
            progress=1.0,
            current_stage=stage.value,
            finished_at=utcnow(),
        )
        return record
    except JobCancelled:
        store.update_job(job.id, status="cancelled", finished_at=utcnow())
        raise
    except Exception as exc:
        store.update_job(job.id, status="failed", error=str(exc), finished_at=utcnow())
        raise
