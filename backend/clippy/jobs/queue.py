"""In-process job queue.

One video is processed at a time, FIFO. That's a deliberate constraint rather
than a limitation: transcription, face detection, and encoding each want the
whole GPU, and running two jobs concurrently on a 6 GB card makes both slower
than running them in sequence.

The queue is durable because it lives in SQLite — a crash mid-job leaves the row
as ``running``, and startup requeues it so the resumable stages pick up where
they left off.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..db import store
from ..db.models import Job, new_id
from ..pipeline.runner import JobCancelled, PipelineRunner
from .events import Event, broker, progress_event

log = logging.getLogger(__name__)


@dataclass
class QueueStatus:
    running_job_id: str | None
    queued: int


class JobQueue:
    """Runs queued jobs one at a time."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        # Created in start(), not here: an asyncio.Event binds to the loop that
        # is running when it is constructed, and this object is a module-level
        # singleton built at import time. Constructing it eagerly makes every
        # subsequent event loop in the process fail with "bound to a different
        # event loop".
        self._wakeup: asyncio.Event | None = None
        self._running_job_id: str | None = None
        self._cancel_requested: set[str] = set()
        self._stopping = False

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Start the worker and requeue anything left running by a crash."""
        self._requeue_interrupted()
        self._reconcile_auto_previews()
        self._stopping = False
        self._wakeup = asyncio.Event()
        self._task = asyncio.create_task(self._worker(), name="clippy-job-queue")
        self.notify()

    async def stop(self) -> None:
        self._stopping = True
        self.notify()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._wakeup = None

    def notify(self) -> None:
        """Wake the worker — call after enqueuing a job.

        A no-op when the worker isn't running, so enqueuing still works in a
        process started without one.
        """
        if self._wakeup is not None:
            self._wakeup.set()

    def _requeue_interrupted(self) -> None:
        interrupted = store.list_jobs(limit=100, status="running")
        for job in interrupted:
            log.info("Requeuing job %s, interrupted by a restart.", job.id)
            store.update_job(job.id, status="queued", error=None)

    def _reconcile_auto_previews(self) -> None:
        """Ensure completed analyses have their configured preview children.

        This is deliberately idempotent so a restart between child inserts can
        safely finish the batch without duplicating work.
        """

        for job in store.list_jobs(limit=500):
            if job.kind == "analysis" and job.status == "done":
                self._ensure_auto_previews(job, store.list_clips(job.id))

    def _ensure_auto_previews(self, analysis: Job, clips) -> int:
        settings = Settings.model_validate(analysis.settings or {})
        if not settings.clips.auto_preview:
            return 0
        source = store.get_source(analysis.source_id)
        if source is None or not source.path or not Path(source.path).is_file():
            return 0
        existing_jobs = store.list_jobs_for_source(analysis.source_id)
        created = 0
        for clip in clips:
            if store.latest_export(clip.id, kind="preview") is not None:
                continue
            if any(
                item.kind == "preview"
                and item.parent_job_id == analysis.id
                and item.clip_id == clip.id
                for item in existing_jobs
            ):
                continue
            store.create_job(
                Job(
                    id=new_id(),
                    source_id=analysis.source_id,
                    kind="preview",
                    clip_id=clip.id,
                    parent_job_id=analysis.id,
                    settings=analysis.settings,
                )
            )
            created += 1
        return created

    # -- control -----------------------------------------------------------

    def cancel(self, job_id: str) -> bool:
        """Request cancellation. Returns True if the job was running or queued."""
        job = store.get_job(job_id)
        if job is None or job.status in ("done", "failed", "cancelled"):
            return False

        self._cancel_requested.add(job_id)
        if job.status == "queued":
            store.update_job(job_id, status="cancelled")
            broker.publish(Event(type="cancelled", job_id=job_id))
        return True

    def is_cancelled(self, job_id: str) -> bool:
        return job_id in self._cancel_requested

    def status(self) -> QueueStatus:
        return QueueStatus(
            running_job_id=self._running_job_id,
            queued=len(store.list_jobs(limit=100, status="queued")),
        )

    # -- worker ------------------------------------------------------------

    async def _worker(self) -> None:
        wakeup = self._wakeup
        assert wakeup is not None

        while not self._stopping:
            job = await asyncio.to_thread(store.next_queued_job)
            if job is None:
                wakeup.clear()
                await wakeup.wait()
                continue

            await self._run_one(job)

    async def _run_one(self, job: Job) -> None:
        source = await asyncio.to_thread(store.get_source, job.source_id)
        if source is None:
            store.update_job(job.id, status="failed", error="Source not found.")
            broker.publish(Event(type="failed", job_id=job.id, data={"error": "Source not found."}))
            return

        self._running_job_id = job.id
        broker.publish(Event(type="started", job_id=job.id))
        log.info("Starting job %s (%s).", job.id, source.title)

        def on_progress(event) -> None:
            broker.publish(progress_event(job.id, event))

        try:
            if job.kind == "analysis":
                runner = PipelineRunner(
                    job,
                    source,
                    settings=Settings.model_validate(job.settings) if job.settings else None,
                    on_progress=on_progress,
                    is_cancelled=lambda: self.is_cancelled(job.id),
                )
                clips = await asyncio.to_thread(_run_blocking, runner)
                result_count = len(clips)
                await asyncio.to_thread(self._ensure_auto_previews, job, clips)
            else:
                from ..pipeline.runner import run_render_job

                await asyncio.to_thread(
                    run_render_job,
                    job,
                    source,
                    on_progress,
                    lambda: self.is_cancelled(job.id),
                )
                result_count = 1
        except JobCancelled:
            broker.publish(Event(type="cancelled", job_id=job.id))
            log.info("Job %s cancelled.", job.id)
        except Exception as exc:
            broker.publish(Event(type="failed", job_id=job.id, data={"error": str(exc)}))
            log.exception("Job %s failed.", job.id)
        else:
            broker.publish(Event(type="done", job_id=job.id, data={"result_count": result_count}))
            log.info("Job %s finished with %d result(s).", job.id, result_count)
        finally:
            self._running_job_id = None
            self._cancel_requested.discard(job.id)


def _run_blocking(runner: PipelineRunner):
    """Run the pipeline in a worker thread with its own event loop.

    The pipeline is overwhelmingly blocking work with one async stage, so it
    gets a private loop rather than competing for the server's.
    """
    return asyncio.run(runner.run())


#: Process-wide queue.
queue = JobQueue()
