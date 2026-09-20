"""The transcript model — the pipeline's shared vocabulary.

Every downstream stage addresses the source video through this structure.
Highlight detection returns *word indices*, not seconds, so clip timing is
always derived from measured word timestamps rather than arithmetic an LLM did
in its head. Trim handles snap to word boundaries. Captions are built from the
same words. One model, one source of truth.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_left, bisect_right
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

#: A word ending in one of these closes a sentence, which is where we prefer to
#: place clip boundaries.
SENTENCE_END = re.compile(r"[.!?…]+[\"'”’)\]]*$")

#: Mid-sentence breaks — usable as fallback boundaries when no sentence end is
#: near enough.
CLAUSE_END = re.compile(r"[,;:—–]+[\"'”’)\]]*$")


@dataclass
class Word:
    text: str
    start: float
    end: float
    #: Diarization label such as "SPEAKER_00". None when diarization didn't run.
    speaker: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def ends_sentence(self) -> bool:
        return bool(SENTENCE_END.search(self.text.strip()))

    @property
    def ends_clause(self) -> bool:
        stripped = self.text.strip()
        return bool(SENTENCE_END.search(stripped) or CLAUSE_END.search(stripped))


@dataclass
class Segment:
    """A sentence-ish span as emitted by the ASR model."""

    text: str
    start: float
    end: float
    #: Index range into :attr:`Transcript.words`, half-open.
    first_word: int = 0
    last_word: int = 0
    speaker: str | None = None


@dataclass
class Transcript:
    words: list[Word] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    language: str = ""
    model: str = ""
    #: "whisper" or "youtube".
    source: str = "whisper"

    # -- basic properties -------------------------------------------------

    def __len__(self) -> int:
        return len(self.words)

    @property
    def has_diarization(self) -> bool:
        return any(w.speaker for w in self.words)

    @property
    def duration(self) -> float:
        return self.words[-1].end if self.words else 0.0

    @property
    def speakers(self) -> list[str]:
        """Distinct speaker labels in first-appearance order."""
        seen: dict[str, None] = {}
        for word in self.words:
            if word.speaker:
                seen.setdefault(word.speaker, None)
        return list(seen)

    # -- index and time lookup --------------------------------------------

    def _starts(self) -> list[float]:
        # Recomputed rather than cached: transcripts are edited in the review UI,
        # and a stale index would silently desync captions from audio.
        return [w.start for w in self.words]

    def index_at_time(self, t: float) -> int:
        """Return the index of the word being spoken at ``t``.

        Clamped to the valid range, so a time before the first word returns 0
        and a time past the end returns the last index.
        """
        if not self.words:
            return 0
        idx = bisect_right(self._starts(), t) - 1
        return max(0, min(idx, len(self.words) - 1))

    def indices_in_range(self, start: float, end: float) -> tuple[int, int]:
        """Return the half-open word index range covering ``[start, end)``."""
        if not self.words:
            return (0, 0)
        starts = self._starts()
        first = bisect_left(starts, start)
        # Include a word that began before `start` but is still being spoken.
        if first > 0 and self.words[first - 1].end > start:
            first -= 1
        last = bisect_left(starts, end)
        return (max(0, first), max(first, min(last, len(self.words))))

    def time_range(self, first_word: int, last_word: int) -> tuple[float, float]:
        """Return the ``(start, end)`` seconds spanned by an inclusive index range."""
        if not self.words:
            return (0.0, 0.0)
        first = max(0, min(first_word, len(self.words) - 1))
        last = max(first, min(last_word, len(self.words) - 1))
        return (self.words[first].start, self.words[last].end)

    def slice(self, first_word: int, last_word: int) -> list[Word]:
        """Return words in an inclusive index range."""
        if not self.words:
            return []
        first = max(0, min(first_word, len(self.words) - 1))
        last = max(first, min(last_word, len(self.words) - 1))
        return self.words[first : last + 1]

    def text_between(self, first_word: int, last_word: int) -> str:
        return " ".join(w.text.strip() for w in self.slice(first_word, last_word)).strip()

    @property
    def text(self) -> str:
        return " ".join(w.text.strip() for w in self.words).strip()

    # -- boundary helpers --------------------------------------------------

    def sentence_end_indices(self) -> list[int]:
        """Indices of words that close a sentence."""
        return [i for i, w in enumerate(self.words) if w.ends_sentence]

    def pause_before(self, index: int) -> float:
        """Silence in seconds between word ``index - 1`` and word ``index``."""
        if index <= 0 or index >= len(self.words):
            return 0.0
        return max(0.0, self.words[index].start - self.words[index - 1].end)

    def dominant_speaker(self, first_word: int, last_word: int) -> str | None:
        """The speaker holding the most words in a range, by speaking time."""
        totals: dict[str, float] = {}
        for word in self.slice(first_word, last_word):
            if word.speaker:
                totals[word.speaker] = totals.get(word.speaker, 0.0) + word.duration
        if not totals:
            return None
        return max(totals, key=lambda key: totals[key])

    def speaker_turns(self) -> list[tuple[str, float, float]]:
        """Collapse per-word speaker labels into ``(speaker, start, end)`` turns.

        The reframe stage uses these to decide when the framing should follow a
        different person.
        """
        turns: list[tuple[str, float, float]] = []
        for word in self.words:
            if not word.speaker:
                continue
            if turns and turns[-1][0] == word.speaker:
                speaker, start, _ = turns[-1]
                turns[-1] = (speaker, start, word.end)
            else:
                turns.append((word.speaker, word.start, word.end))
        return turns

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "model": self.model,
            "source": self.source,
            "words": [asdict(w) for w in self.words],
            "segments": [asdict(s) for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        return cls(
            words=[Word(**w) for w in data.get("words", [])],
            segments=[Segment(**s) for s in data.get("segments", [])],
            language=data.get("language", ""),
            model=data.get("model", ""),
            source=data.get("source", "whisper"),
        )

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> Transcript:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))
