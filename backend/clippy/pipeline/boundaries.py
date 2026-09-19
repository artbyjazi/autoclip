"""Clip boundary refinement.

An LLM proposes a word range. Turning that into a cut that sounds deliberate
takes three passes:

1. **Sentence snap** — move the edges to sentence boundaries, so a clip never
   opens or closes mid-thought.
2. **Duration clamp** — pull the end back (or push it out) to land inside the
   configured length range, always stopping on a sentence boundary.
3. **Silence alignment** — nudge the final seconds into a measured audio trough.

Step 3 is what a fixed ±0.25s pad can't do. Padding by a constant clips breaths
and plosives, because the gap before a word varies with how the speaker breathes.
Cutting inside actual silence is where a human editor would put the blade.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .prepare import Silence
from .transcript import Transcript

log = logging.getLogger(__name__)

#: How many words either side of the model's guess we'll search for a boundary.
SENTENCE_SEARCH_WORDS = 12

#: Fallback padding when no silence is measurable near the boundary.
FALLBACK_LEAD_S = 0.25
FALLBACK_TAIL_S = 0.35

#: How far from a boundary we'll look for a usable silence.
SILENCE_SEARCH_RADIUS_S = 0.75

#: How far into the silence before the first word the clip should start.
#: Enough to catch the breath before speech without a dead beat at the front.
SILENCE_LEAD_S = 0.12
#: How long to hold after the last word before cutting.
SILENCE_TAIL_S = 0.28

#: Never cut closer than this to the edge of a detected silence — silence
#: detection has hysteresis, and the true edge sits slightly inside.
SILENCE_MARGIN_S = 0.04


@dataclass
class Boundary:
    start_s: float
    end_s: float
    start_word: int
    end_word: int

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def refine(
    transcript: Transcript,
    start_word: int,
    end_word: int,
    *,
    silences: list[Silence] | None = None,
    min_duration_s: float = 20.0,
    max_duration_s: float = 90.0,
) -> Boundary | None:
    """Refine a proposed word range into a cuttable boundary.

    Returns None when the range cannot be made to satisfy the duration
    constraints — a candidate that would need to be butchered is better dropped.

    Preconditions:
        transcript has word-level timings; start_word and end_word are valid
        indices with start_word < end_word.
    """
    if not transcript.words:
        return None

    last_index = len(transcript.words) - 1
    start_word = max(0, min(start_word, last_index))
    end_word = max(start_word, min(end_word, last_index))

    start_word = snap_start_to_sentence(transcript, start_word)
    end_word = snap_end_to_sentence(transcript, end_word)

    if end_word <= start_word:
        return None

    end_word = _clamp_duration(transcript, start_word, end_word, min_duration_s, max_duration_s)
    if end_word is None:
        return None

    start_s, end_s = transcript.time_range(start_word, end_word)
    start_s = align_start(start_s, silences or [])
    end_s = align_end(end_s, silences or [])

    # Alignment can only ever widen the clip, so re-check the ceiling.
    if end_s - start_s > max_duration_s:
        end_s = start_s + max_duration_s

    return Boundary(
        start_s=max(0.0, start_s), end_s=end_s, start_word=start_word, end_word=end_word
    )


# --------------------------------------------------------------------------
# Sentence snapping
# --------------------------------------------------------------------------


def snap_start_to_sentence(transcript: Transcript, index: int) -> int:
    """Move ``index`` to the first word of the nearest sentence.

    A word starts a sentence if the word before it ended one. Index 0 always
    qualifies. Ties prefer moving earlier, since starting slightly wide is
    recoverable while clipping the hook is not.
    """
    if index <= 0:
        return 0

    for offset in range(SENTENCE_SEARCH_WORDS + 1):
        for candidate in (index - offset, index + offset):
            if candidate <= 0:
                return 0
            if candidate >= len(transcript.words):
                continue
            if transcript.words[candidate - 1].ends_sentence:
                return candidate

    return index


def snap_end_to_sentence(transcript: Transcript, index: int) -> int:
    """Move ``index`` to the last word of the nearest sentence.

    Ties prefer moving later — ending on the complete thought rather than
    truncating it.
    """
    last_index = len(transcript.words) - 1
    if index >= last_index:
        return last_index

    for offset in range(SENTENCE_SEARCH_WORDS + 1):
        for candidate in (index + offset, index - offset):
            if candidate >= last_index:
                return last_index
            if candidate < 0:
                continue
            if transcript.words[candidate].ends_sentence:
                return candidate

    return index


def _clamp_duration(
    transcript: Transcript,
    start_word: int,
    end_word: int,
    min_duration_s: float,
    max_duration_s: float,
) -> int | None:
    """Adjust ``end_word`` so the clip fits the duration range.

    Returns the adjusted end index, or None if no sentence boundary produces a
    valid length.
    """
    start_s = transcript.words[start_word].start

    def duration_at(index: int) -> float:
        return transcript.words[index].end - start_s

    if duration_at(end_word) > max_duration_s:
        # Walk back to the last sentence end that fits.
        for candidate in range(end_word, start_word, -1):
            if (
                duration_at(candidate) <= max_duration_s
                and transcript.words[candidate].ends_sentence
            ):
                end_word = candidate
                break
        else:
            # No sentence boundary fits — the speaker is running long without
            # punctuation. Take the last word that fits rather than dropping it.
            for candidate in range(end_word, start_word, -1):
                if duration_at(candidate) <= max_duration_s:
                    end_word = candidate
                    break
            else:
                return None

    if duration_at(end_word) < min_duration_s:
        last_index = len(transcript.words) - 1
        for candidate in range(end_word + 1, last_index + 1):
            if duration_at(candidate) > max_duration_s:
                break
            if transcript.words[candidate].ends_sentence:
                end_word = candidate
                if duration_at(end_word) >= min_duration_s:
                    break

    duration = duration_at(end_word)
    if duration < min_duration_s or duration > max_duration_s:
        return None

    return end_word


# --------------------------------------------------------------------------
# Silence alignment
# --------------------------------------------------------------------------


def align_start(start_s: float, silences: list[Silence]) -> float:
    """Pull the clip start back into the silence preceding the first word."""
    silence = _silence_ending_near(start_s, silences)
    if silence is None:
        return max(0.0, start_s - FALLBACK_LEAD_S)

    # A silence shorter than twice the margin can't accommodate it; clamping to
    # the silence's own end keeps the cut out of the preceding word, which is
    # the point of aligning at all.
    earliest = min(silence.start + SILENCE_MARGIN_S, silence.end)
    preferred = silence.end - SILENCE_LEAD_S
    return max(0.0, max(earliest, min(preferred, start_s)))


def align_end(end_s: float, silences: list[Silence]) -> float:
    """Push the clip end into the silence following the last word."""
    silence = _silence_starting_near(end_s, silences)
    if silence is None:
        return end_s + FALLBACK_TAIL_S

    latest = max(silence.end - SILENCE_MARGIN_S, silence.start)
    preferred = silence.start + SILENCE_TAIL_S
    return max(end_s, min(preferred, latest))


def _silence_ending_near(t: float, silences: list[Silence]) -> Silence | None:
    """Find the silence whose end sits closest to ``t``, within the radius."""
    best: Silence | None = None
    best_distance = SILENCE_SEARCH_RADIUS_S
    for silence in silences:
        distance = abs(silence.end - t)
        if distance <= best_distance:
            best_distance = distance
            best = silence
    return best


def _silence_starting_near(t: float, silences: list[Silence]) -> Silence | None:
    """Find the silence whose start sits closest to ``t``, within the radius."""
    best: Silence | None = None
    best_distance = SILENCE_SEARCH_RADIUS_S
    for silence in silences:
        distance = abs(silence.start - t)
        if distance <= best_distance:
            best_distance = distance
            best = silence
    return best
