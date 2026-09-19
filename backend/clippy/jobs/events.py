"""Server-Sent Event broker.

The pipeline runs in a worker thread (ffmpeg, Whisper, and MediaPipe are all
blocking), while SSE subscribers live on the web server's event loop. Every
publish therefore has to cross a thread boundary, which is what
``call_soon_threadsafe`` is doing here — calling ``queue.put_nowait`` directly
from the worker thread would corrupt the loop's internal state.

Each subscriber gets its own bounded queue. A slow client drops events rather
than growing memory without limit; progress is a stream of snapshots, so a
dropped intermediate frame costs nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: Per-subscriber buffer. Deep enough to absorb a burst, shallow enough that a
#: dead client is noticed quickly.
QUEUE_SIZE = 64

#: Emitted when no job event has occurred for this long, so proxies and browsers
#: don't time the connection out.
HEARTBEAT_S = 15.0


@dataclass
class Event:
    """One SSE message."""

    type: str
    job_id: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        payload = json.dumps({"type": self.type, "job_id": self.job_id, **self.data})
        return f"event: {self.type}\ndata: {payload}\n\n"


class EventBroker:
    """Fan-out of job events to any number of SSE subscribers."""

    def __init__(self) -> None:
        self._subscribers: dict[str, set[asyncio.Queue[Event]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock = asyncio.Lock()

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the loop that owns the subscriber queues.

        Called once at startup. Publishes from other threads are marshalled onto
        this loop.
        """
        self._loop = loop

    # -- publishing --------------------------------------------------------

    def publish(self, event: Event) -> None:
        """Publish an event from any thread."""
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        # Raises if the loop closed between the check above and this call.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(self._publish_on_loop, event)

    def _publish_on_loop(self, event: Event) -> None:
        for queue in tuple(self._subscribers.get(event.job_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # The client isn't keeping up. Losing an intermediate progress
                # frame is harmless; the next one carries the current state.
                log.debug("Dropped an event for a slow subscriber on job %s.", event.job_id)

    # -- subscribing -------------------------------------------------------

    async def subscribe(self, job_id: str) -> AsyncIterator[Event]:
        """Yield events for ``job_id`` until the client disconnects."""
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=QUEUE_SIZE)
        async with self._lock:
            self._subscribers.setdefault(job_id, set()).add(queue)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except TimeoutError:
                    yield Event(type="heartbeat", job_id=job_id)
                    continue

                yield event
                if event.type in ("done", "failed", "cancelled"):
                    return
        finally:
            async with self._lock:
                subscribers = self._subscribers.get(job_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(job_id, None)

    def subscriber_count(self, job_id: str) -> int:
        return len(self._subscribers.get(job_id, ()))


#: Process-wide broker. A local single-process app has exactly one.
broker = EventBroker()


def progress_event(job_id: str, event) -> Event:
    """Convert a pipeline ProgressEvent into an SSE event."""
    return Event(
        type="progress",
        job_id=job_id,
        data={
            "stage": str(event.stage),
            "stage_progress": round(event.stage_progress, 4),
            "overall": round(event.overall, 4),
            "message": event.message,
        },
    )


def as_dict(event: Event) -> dict[str, Any]:
    return asdict(event)


@contextlib.asynccontextmanager
async def bound_loop():
    """Bind the broker to the running loop for the lifetime of the app."""
    broker.bind_loop(asyncio.get_running_loop())
    try:
        yield broker
    finally:
        broker.bind_loop(None)  # type: ignore[arg-type]
