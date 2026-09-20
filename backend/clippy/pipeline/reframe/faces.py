"""Face observation via MediaPipe's FaceLandmarker.

Landmarks rather than plain detection boxes, for two reasons: eye positions give
the headroom rule something real to anchor to, and lip landmarks give a
mouth-aspect ratio whose variance over time is the cheapest usable
active-speaker signal.

MediaPipe 1.0 removed the legacy ``mp.solutions`` API, so this uses the Tasks
API with a downloaded model bundle.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

#: Frames per second to sample. Faces don't move fast enough to justify more,
#: and the crop path is smoothed anyway — this is the main cost lever in the
#: whole reframe stage.
DEFAULT_SAMPLE_FPS = 5.0

#: Maximum simultaneous faces. Beyond four, framing decisions stop being
#: meaningful and we fall back to a wide shot regardless.
MAX_FACES = 4

# Canonical FaceMesh landmark indices.
_UPPER_INNER_LIP = 13
_LOWER_INNER_LIP = 14
_LEFT_MOUTH_CORNER = 61
_RIGHT_MOUTH_CORNER = 291
_EYE_LANDMARKS = (33, 133, 362, 263)


@dataclass
class FaceObservation:
    """One face seen at one moment, in source-pixel coordinates."""

    t: float
    cx: float
    cy: float
    width: float
    height: float
    #: Vertical position of the eye line — what the headroom rule aligns.
    eye_y: float
    #: Mouth aspect ratio: opening height over mouth width.
    mar: float

    @property
    def area(self) -> float:
        return self.width * self.height

    @property
    def left(self) -> float:
        return self.cx - self.width / 2

    @property
    def right(self) -> float:
        return self.cx + self.width / 2

    @property
    def top(self) -> float:
        return self.cy - self.height / 2

    @property
    def bottom(self) -> float:
        return self.cy + self.height / 2

    def iou(self, other: FaceObservation) -> float:
        overlap_w = max(0.0, min(self.right, other.right) - max(self.left, other.left))
        overlap_h = max(0.0, min(self.bottom, other.bottom) - max(self.top, other.top))
        intersection = overlap_w * overlap_h
        if intersection <= 0:
            return 0.0
        union = self.area + other.area - intersection
        return intersection / union if union else 0.0


class FaceDetectionUnavailable(RuntimeError):
    """Face detection could not run; the caller should fall back to a centre crop."""


def sample_faces(
    video: Path,
    *,
    start_s: float = 0.0,
    end_s: float | None = None,
    sample_fps: float = DEFAULT_SAMPLE_FPS,
    max_faces: int = MAX_FACES,
) -> list[FaceObservation]:
    """Sample the video and return every face observed, in clip-relative time.

    Preconditions:
        video exists and has a video stream.
    """
    try:
        import cv2
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions
        from mediapipe.tasks.python.vision import (
            FaceLandmarker,
            FaceLandmarkerOptions,
            RunningMode,
        )
    except ImportError as exc:
        raise FaceDetectionUnavailable(f"MediaPipe or OpenCV is unavailable: {exc}") from exc

    from ... import models

    model_file = models.ensure("face_landmarker")

    options = FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model_file)),
        running_mode=RunningMode.VIDEO,
        num_faces=max_faces,
        min_face_detection_confidence=0.4,
        min_tracking_confidence=0.4,
    )

    observations: list[FaceObservation] = []

    with FaceLandmarker.create_from_options(options) as landmarker:
        for timestamp, frame, width, height in _iter_frames(cv2, video, start_s, end_s, sample_fps):
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame)
            # VIDEO mode demands monotonically increasing integer milliseconds.
            result = landmarker.detect_for_video(image, int(timestamp * 1000))

            for landmarks in result.face_landmarks or []:
                observation = _to_observation(landmarks, timestamp, width, height)
                if observation is not None:
                    observations.append(observation)

    log.info("Sampled %d face observations from %s.", len(observations), video.name)
    return observations


def _iter_frames(
    cv2, video: Path, start_s: float, end_s: float | None, sample_fps: float
) -> Iterator[tuple[float, object, int, int]]:
    """Yield ``(clip_relative_t, rgb_frame, width, height)`` at the sample rate."""
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise FaceDetectionUnavailable(f"OpenCV could not open {video}.")

    try:
        source_fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width <= 0 or height <= 0:
            raise FaceDetectionUnavailable(f"{video} reports no frame dimensions.")

        step = max(1, round(source_fps / sample_fps))
        start_frame = int(start_s * source_fps)
        end_frame = int(end_s * source_fps) if end_s is not None else None

        capture.set(cv2.CAP_PROP_POS_FRAMES, start_frame)
        frame_index = start_frame
        # Timestamps come from the frame index rather than CAP_PROP_POS_MSEC,
        # which drifts on variable-frame-rate sources.
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if end_frame is not None and frame_index >= end_frame:
                break

            if (frame_index - start_frame) % step == 0:
                timestamp = (frame_index / source_fps) - start_s
                yield timestamp, cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), width, height

            frame_index += 1
    finally:
        capture.release()


def _to_observation(landmarks, timestamp: float, width: int, height: int) -> FaceObservation | None:
    """Convert normalised landmarks into a pixel-space observation."""
    if not landmarks:
        return None

    xs = [lm.x * width for lm in landmarks]
    ys = [lm.y * height for lm in landmarks]
    if not xs or not ys:
        return None

    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    face_width = max_x - min_x
    face_height = max_y - min_y
    if face_width <= 1 or face_height <= 1:
        return None

    eye_y = sum(ys[i] for i in _EYE_LANDMARKS) / len(_EYE_LANDMARKS)

    return FaceObservation(
        t=timestamp,
        cx=(min_x + max_x) / 2,
        cy=(min_y + max_y) / 2,
        width=face_width,
        height=face_height,
        eye_y=eye_y,
        mar=_mouth_aspect_ratio(xs, ys),
    )


def _mouth_aspect_ratio(xs: list[float], ys: list[float]) -> float:
    """Mouth opening height divided by mouth width.

    Normalising by mouth width makes the value comparable between a face close
    to camera and one further away, which matters when comparing two people in
    the same frame.
    """
    try:
        opening = abs(ys[_LOWER_INNER_LIP] - ys[_UPPER_INNER_LIP])
        mouth_width = abs(xs[_RIGHT_MOUTH_CORNER] - xs[_LEFT_MOUTH_CORNER])
    except IndexError:
        return 0.0
    return opening / mouth_width if mouth_width > 1 else 0.0
