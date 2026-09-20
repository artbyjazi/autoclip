"""Cloud-first speech transcription with chunk persistence and local fallback."""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..config import Settings, get_secret
from ..db import store
from ..db.models import ProviderTarget
from ..providers.router import classify_error, record_result, target_available
from . import ffmpeg
from . import transcribe as local_transcribe
from .transcript import Segment, Transcript, Word

log = logging.getLogger(__name__)

CHUNK_SECONDS = 15 * 60
CHUNK_OVERLAP_SECONDS = 5


class ASRError(RuntimeError):
    pass


class TranscriptionProvider(Protocol):
    name: str
    model: str

    def transcribe(self, audio: Path, *, language: str = "") -> Transcript: ...


@dataclass
class GroqWhisperProvider:
    api_key: str
    model: str = "whisper-large-v3-turbo"
    name: str = "groq"

    def transcribe(self, audio: Path, *, language: str = "") -> Transcript:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise ASRError("The openai package is required for Groq ASR.") from exc
        client = OpenAI(api_key=self.api_key, base_url="https://api.groq.com/openai/v1")
        with audio.open("rb") as handle:
            response = client.audio.transcriptions.create(
                file=handle,
                model=self.model,
                language=language or None,
                response_format="verbose_json",
                timestamp_granularities=["word", "segment"],
            )
        return _transcript_from_openai(response, model=self.model, source=self.name)


@dataclass
class GeminiTranscriptionProvider:
    api_key: str
    model: str = "gemini-3.5-transcribe"
    name: str = "gemini"

    def transcribe(self, audio: Path, *, language: str = "") -> Transcript:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise ASRError("The google-genai package is required for Gemini ASR.") from exc
        client = genai.Client(api_key=self.api_key)
        uploaded = client.files.upload(file=str(audio))
        input_item = {
            "type": "audio",
            "uri": uploaded.uri,
            "mime_type": getattr(uploaded, "mime_type", "audio/flac"),
        }
        mode: dict[str, Any] = {
            "type": "verbatim",
            "timestamp_granularities": ["word"],
            "diarization_mode": "speaker",
        }
        if language:
            mode["language_codes"] = [language]
        try:
            interaction = client.interactions.create(
                model=self.model,
                input=[input_item],
                mode=mode,
            )
            return _transcript_from_gemini(interaction, model=self.model)
        finally:
            name = getattr(uploaded, "name", None)
            if name:
                try:
                    client.files.delete(name=name)
                except Exception:
                    log.debug("Could not delete temporary Gemini file %s", name)


@dataclass
class LocalWhisperProvider:
    settings: Settings
    model: str = "small"
    name: str = "local"

    def transcribe(self, audio: Path, *, language: str = "") -> Transcript:
        whisper = self.settings.whisper.model_copy(
            update={"model": self.model, "language": language or self.settings.whisper.language}
        )
        transcript = local_transcribe.transcribe(audio, whisper)
        transcript.source = self.name
        return transcript


def transcribe_with_router(
    audio: Path,
    settings: Settings,
    *,
    job_id: str,
    work_dir: Path,
    duration_s: float,
    on_progress=None,
    cancelled=None,
) -> Transcript:
    """Transcribe persisted chunks, trying each healthy target per chunk."""
    chunk_dir = work_dir / "asr"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    spans = _chunk_spans(duration_s)
    merged = Transcript(language=settings.whisper.language, model="router", source="router")
    manifest: list[dict[str, Any]] = []

    for index, (start, end) in enumerate(spans):
        if cancelled and cancelled():
            raise ASRError("Transcription cancelled.")
        transcript_path = chunk_dir / f"chunk-{index:04d}.json"
        meta_path = chunk_dir / f"chunk-{index:04d}.meta.json"
        if transcript_path.exists() and meta_path.exists():
            chunk = Transcript.load(transcript_path)
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        else:
            chunk_audio = chunk_dir / f"chunk-{index:04d}.flac"
            if not chunk_audio.exists():
                _extract_chunk(audio, chunk_audio, start, end - start)
            chunk, target_id = _transcribe_chunk(
                chunk_audio, settings, job_id=job_id, audio_seconds=end - start
            )
            meta = {"start": start, "end": end, "target_id": target_id}
            chunk.save(transcript_path)
            meta_path.write_text(json.dumps(meta), encoding="utf-8")

        _merge_chunk(merged, chunk, offset=start)
        manifest.append(meta)
        if on_progress:
            on_progress((index + 1) / len(spans))

    if not merged.words:
        raise ASRError("No speech was detected by any transcription provider.")
    merged.model = ",".join(dict.fromkeys(str(item.get("target_id", "")) for item in manifest))
    merged.source = "router"
    (chunk_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return merged


def _chunk_spans(duration_s: float) -> list[tuple[float, float]]:
    if duration_s <= 0:
        return [(0.0, CHUNK_SECONDS)]
    spans: list[tuple[float, float]] = []
    cursor = 0.0
    while cursor < duration_s:
        end = min(duration_s, cursor + CHUNK_SECONDS)
        spans.append((cursor, end))
        if end >= duration_s:
            break
        cursor = max(cursor + 1, end - CHUNK_OVERLAP_SECONDS)
    return spans


def _extract_chunk(source: Path, destination: Path, start: float, duration: float) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg.run(
        [
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(source),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "flac",
            str(destination),
        ]
    )


def _provider_for(target: ProviderTarget, settings: Settings) -> TranscriptionProvider | None:
    if target.provider == "groq":
        key = get_secret("groq", settings)
        return GroqWhisperProvider(key, target.model) if key else None
    if target.provider == "gemini":
        key = get_secret("gemini", settings)
        return GeminiTranscriptionProvider(key, target.model) if key else None
    if target.provider == "local":
        return LocalWhisperProvider(settings, target.model)
    return None


def _transcribe_chunk(
    audio: Path, settings: Settings, *, job_id: str, audio_seconds: float
) -> tuple[Transcript, str]:
    errors: list[str] = []
    for target in store.list_provider_targets("asr"):
        if not target_available(target):
            continue
        provider = _provider_for(target, settings)
        if provider is None:
            continue
        started = time.monotonic()
        transcript = None
        for attempt in range(2):
            try:
                transcript = provider.transcribe(audio, language=settings.whisper.language)
                break
            except Exception as exc:
                error_type, _ = classify_error(exc)
                if attempt == 0 and error_type == "transient":
                    time.sleep(0.5 + random.random() * 0.5)
                    continue
                record_result(
                    target,
                    job_id=job_id,
                    operation="transcribe",
                    started=started,
                    error=exc,
                    audio_seconds=audio_seconds,
                )
                errors.append(f"{target.id}: {exc}")
                break
        if transcript is None:
            continue
        record_result(
            target,
            job_id=job_id,
            operation="transcribe",
            started=started,
            audio_seconds=audio_seconds,
        )
        return transcript, target.id
    raise ASRError("All ASR targets failed: " + "; ".join(errors[-3:]))


def _transcript_from_openai(response: Any, *, model: str, source: str) -> Transcript:
    raw_words = getattr(response, "words", None) or _value(response, "words", [])
    words = [
        Word(
            text=str(_value(item, "word", "")).strip(),
            start=float(_value(item, "start", 0)),
            end=float(_value(item, "end", 0)),
        )
        for item in raw_words
        if str(_value(item, "word", "")).strip()
    ]
    return _from_words(words, model=model, source=source)


def _transcript_from_gemini(interaction: Any, *, model: str) -> Transcript:
    words: list[Word] = []
    for step in getattr(interaction, "steps", []) or []:
        for content in getattr(step, "content", []) or []:
            for annotation in getattr(content, "annotations", []) or []:
                if _value(annotation, "type", "") != "word_info":
                    continue
                text = str(_value(annotation, "word", _value(annotation, "text", ""))).strip()
                if text:
                    words.append(
                        Word(
                            text=text,
                            start=_seconds(_value(annotation, "start_offset", 0)),
                            end=_seconds(_value(annotation, "end_offset", 0)),
                            speaker=_value(annotation, "speaker", None),
                        )
                    )
    if not words:
        raise ASRError("Gemini returned no word-level annotations.")
    return _from_words(words, model=model, source="gemini")


def _from_words(words: list[Word], *, model: str, source: str) -> Transcript:
    transcript = Transcript(words=words, model=model, source=source)
    first = 0
    for index, word in enumerate(words):
        if word.ends_sentence or index == len(words) - 1:
            span = words[first : index + 1]
            transcript.segments.append(
                Segment(
                    text=" ".join(item.text for item in span),
                    start=span[0].start,
                    end=span[-1].end,
                    first_word=first,
                    last_word=index,
                    speaker=span[0].speaker,
                )
            )
            first = index + 1
    return transcript


def _merge_chunk(destination: Transcript, chunk: Transcript, *, offset: float) -> None:
    threshold = destination.words[-1].end - 0.1 if destination.words else -1.0
    for word in chunk.words:
        shifted = Word(
            text=word.text,
            start=word.start + offset,
            end=word.end + offset,
            speaker=word.speaker,
        )
        if shifted.start < threshold:
            continue
        destination.words.append(shifted)
        threshold = shifted.end - 0.1
    rebuilt = _from_words(destination.words, model=destination.model, source=destination.source)
    destination.segments = rebuilt.segments


def _value(item: Any, key: str, default: Any) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _seconds(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().removesuffix("s")
    try:
        return float(text)
    except ValueError:
        return 0.0
