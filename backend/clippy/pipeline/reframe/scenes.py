"""Shot boundary detection.

The crop path is computed per shot and never interpolated across a cut — panning
through an edit is the single most obvious "this was auto-reframed" artifact.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: PySceneDetect content-detector threshold. Lower finds more cuts. 27 is the
#: library default and holds up well on talking-head and podcast footage; going
#: lower starts firing on lighting changes and gesture blur.
DEFAULT_THRESHOLD = 27.0

#: Shots shorter than this are merged into their neighbour. A sub-second shot
#: isn't worth its own framing decision, and re-framing across one looks frantic.
MIN_SHOT_S = 0.8


@dataclass
class Shot:
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    def contains(self, t: float) -> bool:
        return self.start_s <= t < self.end_s


def detect_shots(
    video: Path,
    *,
    start_s: float = 0.0,
    end_s: float | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    min_shot_s: float = MIN_SHOT_S,
) -> list[Shot]:
    """Return shots within ``[start_s, end_s)``, in clip-relative seconds.

    Always returns at least one shot: footage with no detectable cuts is a
    single shot, not an error.
    """
    duration = (end_s - start_s) if end_s is not None else None

    try:
        shots = _detect_with_scenedetect(video, start_s, end_s, threshold)
    except Exception as exc:
        log.warning("Scene detection failed (%s); treating the clip as one shot.", exc)
        shots = []

    if not shots:
        return [Shot(0.0, duration if duration is not None else 0.0)]

    return _merge_short_shots(shots, min_shot_s)


def _detect_with_scenedetect(
    video: Path, start_s: float, end_s: float | None, threshold: float
) -> list[Shot]:
    from scenedetect import ContentDetector, SceneManager, open_video

    stream = open_video(str(video))
    manager = SceneManager()
    manager.add_detector(ContentDetector(threshold=threshold))

    if start_s > 0:
        stream.seek(start_s)

    manager.detect_scenes(video=stream, end_time=end_s, show_progress=False)
    scenes = manager.get_scene_list()

    shots: list[Shot] = []
    for scene_start, scene_end in scenes:
        # Rebase onto the clip's own timeline so downstream code never has to
        # care where the clip sat in the source.
        relative_start = max(0.0, scene_start.get_seconds() - start_s)
        relative_end = scene_end.get_seconds() - start_s
        if end_s is not None:
            relative_end = min(relative_end, end_s - start_s)
        if relative_end > relative_start:
            shots.append(Shot(relative_start, relative_end))

    return shots


def _merge_short_shots(shots: list[Shot], min_shot_s: float) -> list[Shot]:
    """Absorb sub-threshold shots into the neighbour they're closest to."""
    if len(shots) <= 1:
        return shots

    merged: list[Shot] = [shots[0]]
    for shot in shots[1:]:
        if shot.duration_s < min_shot_s or merged[-1].duration_s < min_shot_s:
            merged[-1] = Shot(merged[-1].start_s, shot.end_s)
        else:
            merged.append(shot)

    return merged
