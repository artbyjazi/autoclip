"""Golden reframe acceptance bar (§6.4) on a fixed three-video set.

Release gate: no visible jitter, no cut-off faces, speaker on screen for at
least 95% of speaking time. Runs with ``pytest -m golden`` — excluded from
the default unit pass because it needs the committed footage in
``tests/data/golden/`` plus MediaPipe and ffmpeg.

Footage: Tears of Steel (Blender Foundation, CC-BY 3.0), three ~20 s
dialogue segments with real actors, cuts, and movement. See
``tests/data/golden/MANIFEST.md`` for sources, ranges, and checksums.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from clippy.pipeline.reframe import ReframeConfig, build_crop_path
from clippy.pipeline.reframe.croppath import CropPath
from clippy.pipeline.reframe.faces import sample_faces

pytestmark = pytest.mark.golden

DATA = Path(__file__).parent / "data" / "golden"

CLIPS = ("tos_dialogue_1.mp4", "tos_dialogue_2.mp4", "tos_dialogue_3.mp4")

#: Max frame-to-frame jump of the crop window on a tracked static shot,
#: in source pixels at 720p. Anything above reads as visible jitter.
JITTER_MAX_PX = 14.0

#: Minimum fraction of each detected face that must stay inside the crop.
FACE_INSIDE_MIN = 0.85

#: Minimum fraction of face observations that must satisfy FACE_INSIDE_MIN.
FACE_OBSERVATIONS_MIN_FRAC = 0.95

#: Minimum fraction of face centres that must fall inside the crop
#: (speaker-framing coverage).
COVERAGE_MIN = 0.95

#: A golden clip without enough detections proves nothing — fail loudly
#: instead of passing vacuously.
MIN_OBSERVATIONS = 20


def crop_rect_at(path: CropPath, t: float) -> tuple[float, float, float, float]:
    """Interpolate the crop window (x, y, w, h) at clip-relative time t."""
    for segment in path.segments:
        if segment.start_s - 1e-6 <= t <= segment.end_s + 1e-6:
            keys = segment.keyframes
            if not keys:
                # Static centre fallback: centre the crop in the source frame.
                return (
                    (path.source_width - segment.width) / 2,
                    (path.source_height - segment.height) / 2,
                    segment.width,
                    segment.height,
                )
            if len(keys) == 1 or t <= keys[0].t:
                first = keys[0]
                return first.x, first.y, segment.width, segment.height
            for prev, nxt in zip(keys, keys[1:], strict=False):
                if t <= nxt.t:
                    span = max(nxt.t - prev.t, 1e-6)
                    f = min(max((t - prev.t) / span, 0.0), 1.0)
                    return (
                        prev.x + (nxt.x - prev.x) * f,
                        prev.y + (nxt.y - prev.y) * f,
                        segment.width,
                        segment.height,
                    )
            last = keys[-1]
            return last.x, last.y, segment.width, segment.height
    last_segment = path.segments[-1]
    last_key = last_segment.keyframes[-1]
    return last_key.x, last_key.y, last_segment.width, last_segment.height


def _face_box_inside(
    crop: tuple[float, float, float, float],
    left: float,
    top: float,
    right: float,
    bottom: float,
) -> float:
    cx, cy, w, h = crop
    inter_w = max(0.0, min(right, cx + w) - max(left, cx))
    inter_h = max(0.0, min(bottom, cy + h) - max(top, cy))
    area = max((right - left) * (bottom - top), 1e-6)
    return (inter_w * inter_h) / area


@pytest.fixture(params=CLIPS)
def golden_clip(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Path, CropPath]:
    name = request.param
    video = DATA / name
    assert video.is_file(), f"Golden footage missing: {video} (see MANIFEST.md)"
    path = build_crop_path(video, start_s=0.0, end_s=_duration(video), config=ReframeConfig())
    assert path.segments, f"No crop segments for {name}"
    return video, path


def _duration(video: Path) -> float:
    from clippy.pipeline import ffmpeg

    info = ffmpeg.probe(video)
    assert info.duration_s and info.duration_s > 0, f"No duration for {video.name}"
    return info.duration_s


def test_tracking_has_no_visible_jitter(golden_clip: tuple[Path, CropPath]) -> None:
    _, path = golden_clip
    worst = 0.0
    for segment in path.segments:
        keys = segment.keyframes
        for prev, nxt in zip(keys, keys[1:], strict=False):
            dt = max(nxt.t - prev.t, 1e-6)
            jump = max(abs(nxt.x - prev.x), abs(nxt.y - prev.y)) / dt
            worst = max(worst, jump)
    # Per-second-normalised: a slow push-in moves steadily, jitter spikes.
    assert worst <= JITTER_MAX_PX * 5, f"Crop jump {worst:.1f} px/s exceeds the jitter bar"


def test_faces_are_not_cut_off(golden_clip: tuple[Path, CropPath]) -> None:
    video, path = golden_clip
    observations = sample_faces(video, sample_fps=2.0)
    assert len(observations) >= MIN_OBSERVATIONS, (
        f"Only {len(observations)} face observations — footage has too few faces to gate on"
    )
    inside = sum(
        1
        for obs in observations
        if _face_box_inside(crop_rect_at(path, obs.t), obs.left, obs.top, obs.right, obs.bottom)
        >= FACE_INSIDE_MIN
    )
    frac = inside / len(observations)
    assert frac >= FACE_OBSERVATIONS_MIN_FRAC, (
        f"Only {frac:.0%} of faces stay inside the crop (bar: {FACE_OBSERVATIONS_MIN_FRAC:.0%})"
    )


def test_speaker_stays_on_screen(golden_clip: tuple[Path, CropPath]) -> None:
    video, path = golden_clip
    observations = sample_faces(video, sample_fps=2.0)
    assert len(observations) >= MIN_OBSERVATIONS, (
        f"Only {len(observations)} face observations — footage has too few faces to gate on"
    )
    covered = 0
    for obs in observations:
        cx, cy, w, h = crop_rect_at(path, obs.t)
        if cx <= obs.cx <= cx + w and cy <= obs.cy <= cy + h:
            covered += 1
    frac = covered / len(observations)
    assert frac >= COVERAGE_MIN, (
        f"Speaker on screen only {frac:.0%} of speaking time (bar: {COVERAGE_MIN:.0%})"
    )
