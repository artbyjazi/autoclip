"""Golden reframe acceptance bar (§6.4) on a fixed three-video set.

Release gate. Runs with ``pytest -m golden`` — excluded from the default
unit pass because it needs the committed footage in ``tests/data/golden/``
plus MediaPipe and ffmpeg. Runs on Linux (CI, LXC); MediaPipe's Metal
delegate crashes on macOS, so golden is not runnable on a Mac.

Footage: Tears of Steel (Blender Foundation, CC-BY 3.0), dialogue segments
with real actors, cuts, and movement. See ``tests/data/golden/MANIFEST.md``.

What the three tests pin, calibrated against measured pipeline output:

- follow: at any moment someone is visible, the crop frames *somebody*
  (measured 0.70–0.95 across the set — two-shots physically cannot frame
  both faces in 9:16, so this is per-moment, not per-face).
- eyes: every framed face keeps its eyes inside the crop with margin
  (measured 1.00 — close-up cheeks may overflow, eyes must not).
- smooth: per track segment, total-variation/displacement <= 4.0 and no
  single keyframe jump above 70 px at 720p (measured ratio <= 3.38,
  max jump 59 px). Cuts between segments are excluded: reframing on a
  cut is a feature, not jitter.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from clippy.pipeline import ffmpeg
from clippy.pipeline.reframe import ReframeConfig, build_crop_path
from clippy.pipeline.reframe.croppath import CropPath
from clippy.pipeline.reframe.faces import FaceObservation, sample_faces

pytestmark = pytest.mark.golden

DATA = Path(__file__).parent / "data" / "golden"

CLIPS = ("tos_dialogue_1.mp4", "tos_dialogue_2.mp4", "tos_dialogue_3.mp4")

#: Minimum fraction of sampled moments-with-faces where the crop frames somebody.
FOLLOW_MIN = 0.65

#: Minimum fraction of framed faces whose eyes stay inside the crop.
EYES_MIN = 0.95

#: Eye margin as a fraction of crop width.
EYE_MARGIN_FRAC = 0.05

#: Max total-variation/displacement ratio inside one track segment.
SMOOTH_RATIO_MAX = 4.0

#: Max single keyframe jump (source px at 720p) inside one track segment.
SMOOTH_JUMP_MAX_PX = 70.0

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


def face_covered(crop: tuple[float, float, float, float], obs: FaceObservation) -> bool:
    """The face centre is inside the crop window."""
    cx, cy, w, h = crop
    return cx <= obs.cx <= cx + w and cy <= obs.cy <= cy + h


def eyes_inside(crop: tuple[float, float, float, float], obs: FaceObservation) -> bool:
    """The eyes are inside the crop with margin — no cut-off faces."""
    cx, cy, w, h = crop
    margin = EYE_MARGIN_FRAC * w
    return cx + margin <= obs.cx <= cx + w - margin and cy <= obs.eye_y <= cy + h


@pytest.fixture(params=CLIPS)
def golden_clip(request: pytest.FixtureRequest) -> tuple[list[FaceObservation], CropPath]:
    name = request.param
    video = DATA / name
    assert video.is_file(), f"Golden footage missing: {video} (see MANIFEST.md)"
    info = ffmpeg.probe(video)
    assert info.duration_s and info.duration_s > 0, f"No duration for {video.name}"
    path = build_crop_path(video, start_s=0.0, end_s=info.duration_s, config=ReframeConfig())
    assert path.segments, f"No crop segments for {name}"
    observations = sample_faces(video, sample_fps=2.0)
    assert len(observations) >= MIN_OBSERVATIONS, (
        f"Only {len(observations)} face observations in {name} — "
        "footage has too few faces to gate on"
    )
    return observations, path


def test_crop_frames_somebody(golden_clip: tuple[list[FaceObservation], CropPath]) -> None:
    observations, path = golden_clip
    by_moment: dict[float, list[FaceObservation]] = {}
    for obs in observations:
        by_moment.setdefault(round(obs.t, 1), []).append(obs)
    framed = sum(
        1
        for t, group in by_moment.items()
        if any(face_covered(crop_rect_at(path, t), o) for o in group)
    )
    frac = framed / len(by_moment)
    assert frac >= FOLLOW_MIN, (
        f"Crop frames somebody only {frac:.0%} of face-visible moments (bar: {FOLLOW_MIN:.0%})"
    )


def test_eyes_are_not_cut_off(golden_clip: tuple[list[FaceObservation], CropPath]) -> None:
    observations, path = golden_clip
    framed = [o for o in observations if face_covered(crop_rect_at(path, o.t), o)]
    assert framed, "No framed faces at all — follow test should have caught this first"
    intact = sum(1 for o in framed if eyes_inside(crop_rect_at(path, o.t), o))
    frac = intact / len(framed)
    assert frac >= EYES_MIN, (
        f"Only {frac:.0%} of framed faces keep eyes inside the crop (bar: {EYES_MIN:.0%})"
    )


def test_tracking_is_smooth(golden_clip: tuple[list[FaceObservation], CropPath]) -> None:
    _, path = golden_clip
    for segment in path.segments:
        if segment.strategy.value != "track" or len(segment.keyframes) < 2:
            continue
        xs = [k.x for k in segment.keyframes]
        ys = [k.y for k in segment.keyframes]
        jumps = [max(abs(xs[i + 1] - xs[i]), abs(ys[i + 1] - ys[i])) for i in range(len(xs) - 1)]
        displacement = max(abs(xs[-1] - xs[0]), abs(ys[-1] - ys[0]))
        total = sum(jumps)
        ratio = total / max(displacement, 1e-6)
        assert ratio <= SMOOTH_RATIO_MAX, (
            f"Segment [{segment.start_s:.1f}-{segment.end_s:.1f}]: "
            f"variation/displacement {ratio:.2f} exceeds {SMOOTH_RATIO_MAX} (jitter)"
        )
        assert max(jumps) <= SMOOTH_JUMP_MAX_PX, (
            f"Segment [{segment.start_s:.1f}-{segment.end_s:.1f}]: "
            f"single jump {max(jumps):.0f}px exceeds {SMOOTH_JUMP_MAX_PX:.0f}px"
        )
