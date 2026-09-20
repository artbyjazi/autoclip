"""Prepare stage — normalise media into what later stages expect.

Produces a 16 kHz mono WAV (what Whisper wants), a thumbnail strip for UI
scrubbing, and a silence map used later to place clip boundaries in audio
troughs rather than mid-breath.
"""

from __future__ import annotations

import logging
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import ffmpeg

log = logging.getLogger(__name__)

#: Whisper resamples to 16 kHz mono internally; doing it once up front avoids
#: repeating the work on every model invocation.
AUDIO_SAMPLE_RATE = 16_000

THUMBNAIL_INTERVAL_S = 5
THUMBNAIL_WIDTH = 160


@dataclass
class Silence:
    start: float
    end: float

    @property
    def midpoint(self) -> float:
        return (self.start + self.end) / 2

    @property
    def duration(self) -> float:
        return self.end - self.start


def extract_audio(
    source: Path,
    destination: Path,
    *,
    duration_s: float | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Extract mono 16 kHz PCM WAV from any input."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(
        [
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(AUDIO_SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(destination),
        ],
        total_duration_s=duration_s,
        on_progress=on_progress,
    )
    return destination


def generate_thumbnails(
    source: Path,
    destination_dir: Path,
    *,
    interval_s: int = THUMBNAIL_INTERVAL_S,
    width: int = THUMBNAIL_WIDTH,
) -> list[Path]:
    """Write one thumbnail every ``interval_s`` seconds for UI scrubbing.

    Returns the generated files in chronological order. An audio-only source
    yields an empty list rather than an error.
    """
    destination_dir.mkdir(parents=True, exist_ok=True)
    pattern = destination_dir / "thumb_%05d.jpg"

    try:
        ffmpeg.run(
            [
                "-i",
                str(source),
                "-vf",
                f"fps=1/{interval_s},scale={width}:-2",
                "-q:v",
                "5",
                str(pattern),
            ]
        )
    except ffmpeg.FFmpegError:
        # Audio-only input has no video stream to sample.
        log.debug("No thumbnails generated for %s (likely audio-only).", source.name)
        return []

    return sorted(destination_dir.glob("thumb_*.jpg"))


_SILENCE_START = re.compile(r"silence_start:\s*([-\d.]+)")
_SILENCE_END = re.compile(r"silence_end:\s*([-\d.]+)")


def detect_silences(
    audio: Path,
    *,
    noise_db: float = -32.0,
    min_duration_s: float = 0.20,
) -> list[Silence]:
    """Find silent spans using ffmpeg's ``silencedetect``.

    Clip boundaries land far better inside these than at a fixed offset from a
    sentence end: a fixed pad clips breaths and plosives, while a trough is
    where a human editor would cut.

    Preconditions:
        audio is a decodable audio file; video inputs work but waste decode time.
    """
    command = [
        ffmpeg.ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-i",
        str(audio),
        "-af",
        f"silencedetect=noise={noise_db}dB:d={min_duration_s}",
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(
        command, capture_output=True, text=True, check=False, encoding="utf-8", errors="replace"
    )
    if proc.returncode != 0:
        log.warning("silencedetect failed; boundary refinement will fall back to fixed padding.")
        return []

    silences: list[Silence] = []
    pending_start: float | None = None
    for line in (proc.stderr or "").splitlines():
        if match := _SILENCE_START.search(line):
            pending_start = float(match.group(1))
        elif match := _SILENCE_END.search(line):
            end = float(match.group(1))
            start = pending_start if pending_start is not None else end
            silences.append(Silence(start=max(0.0, start), end=end))
            pending_start = None

    return silences
