"""Grouping face observations into per-person tracks.

Detection gives independent boxes per sampled frame with no identity. Linking
them into tracks is what lets the crop follow *a person* rather than whichever
box happened to be detected first that frame.

Matching uses IoU plus a centre-distance fallback: IoU alone breaks when someone
moves more than their own face width between samples, which at 5 fps is common.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field

from .faces import FaceObservation

log = logging.getLogger(__name__)

#: Minimum IoU to link an observation to an existing track.
IOU_THRESHOLD = 0.25
#: Fallback: link if centres are within this multiple of the face width.
CENTRE_DISTANCE_FACTOR = 1.2
#: A track with no observation for this long is closed.
MAX_GAP_S = 0.8
#: Tracks shorter than this are detection noise, not people.
MIN_TRACK_DURATION_S = 0.5


@dataclass
class FaceTrack:
    id: int
    observations: list[FaceObservation] = field(default_factory=list)

    @property
    def start_s(self) -> float:
        return self.observations[0].t if self.observations else 0.0

    @property
    def end_s(self) -> float:
        return self.observations[-1].t if self.observations else 0.0

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s

    @property
    def mean_area(self) -> float:
        return statistics.fmean(o.area for o in self.observations) if self.observations else 0.0

    @property
    def mean_cx(self) -> float:
        return statistics.fmean(o.cx for o in self.observations) if self.observations else 0.0

    @property
    def mean_cy(self) -> float:
        return statistics.fmean(o.cy for o in self.observations) if self.observations else 0.0

    @property
    def last(self) -> FaceObservation | None:
        return self.observations[-1] if self.observations else None

    def observations_between(self, start_s: float, end_s: float) -> list[FaceObservation]:
        return [o for o in self.observations if start_s <= o.t < end_s]

    def mouth_activity(self, start_s: float, end_s: float) -> float:
        """Variance of the mouth-aspect ratio over a window.

        A talking mouth opens and closes; a listening one holds roughly still.
        Variance captures that without needing to model phonemes, and is
        comparable between faces because the ratio is width-normalised.
        """
        window = [o.mar for o in self.observations_between(start_s, end_s)]
        if len(window) < 3:
            return 0.0
        return statistics.pvariance(window)

    def centrality(self, frame_width: float) -> float:
        """1.0 when the track sits on the frame's centre line, 0.0 at the edge."""
        if frame_width <= 0:
            return 0.0
        offset = abs(self.mean_cx - frame_width / 2) / (frame_width / 2)
        return max(0.0, 1.0 - offset)

    def position_at(self, t: float) -> FaceObservation | None:
        """Nearest observation to ``t``, or None if the track is empty."""
        if not self.observations:
            return None
        return min(self.observations, key=lambda o: abs(o.t - t))


def build_tracks(
    observations: list[FaceObservation],
    *,
    max_gap_s: float = MAX_GAP_S,
    min_duration_s: float = MIN_TRACK_DURATION_S,
) -> list[FaceTrack]:
    """Link observations into tracks, newest-first by prominence.

    Preconditions:
        observations may arrive in any order; they are sorted internally.
    """
    if not observations:
        return []

    by_time: dict[float, list[FaceObservation]] = {}
    for observation in observations:
        by_time.setdefault(observation.t, []).append(observation)

    tracks: list[FaceTrack] = []
    open_tracks: list[FaceTrack] = []
    next_id = 0

    for timestamp in sorted(by_time):
        frame_observations = by_time[timestamp]

        open_tracks = [t for t in open_tracks if timestamp - t.end_s <= max_gap_s]

        # Greedy best-first matching: strongest pairing claimed first, so a
        # weak-but-valid match can't steal a track from a better candidate.
        pairs: list[tuple[float, FaceTrack, FaceObservation]] = []
        for track in open_tracks:
            for observation in frame_observations:
                score = _match_score(track, observation)
                if score > 0:
                    pairs.append((score, track, observation))
        pairs.sort(key=lambda p: p[0], reverse=True)

        claimed_tracks: set[int] = set()
        claimed_observations: set[int] = set()
        for _, track, observation in pairs:
            if track.id in claimed_tracks or id(observation) in claimed_observations:
                continue
            track.observations.append(observation)
            claimed_tracks.add(track.id)
            claimed_observations.add(id(observation))

        for observation in frame_observations:
            if id(observation) in claimed_observations:
                continue
            track = FaceTrack(id=next_id, observations=[observation])
            next_id += 1
            tracks.append(track)
            open_tracks.append(track)

    surviving = [t for t in tracks if t.duration_s >= min_duration_s or len(t.observations) >= 3]
    surviving.sort(key=lambda t: t.mean_area * t.duration_s, reverse=True)

    log.debug("Built %d tracks from %d observations.", len(surviving), len(observations))
    return surviving


def _match_score(track: FaceTrack, observation: FaceObservation) -> float:
    """Score linking ``observation`` to ``track``; 0 means no link."""
    last = track.last
    if last is None:
        return 0.0

    iou = last.iou(observation)
    if iou >= IOU_THRESHOLD:
        return 1.0 + iou

    # At 5 fps a person can move further than their own face width between
    # samples, which zeroes IoU on a perfectly good match. Fall back to
    # proximity scaled by face size.
    distance = ((last.cx - observation.cx) ** 2 + (last.cy - observation.cy) ** 2) ** 0.5
    tolerance = max(last.width, observation.width) * CENTRE_DISTANCE_FACTOR
    if distance <= tolerance:
        return 1.0 - (distance / tolerance)

    return 0.0
