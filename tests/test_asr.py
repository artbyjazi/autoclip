"""ASR chunking, timestamp normalization, and provider failover."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from clippy import config
from clippy.db import store
from clippy.db.models import Job, ProviderTarget, Source, new_id
from clippy.pipeline import asr
from clippy.pipeline.transcript import Transcript, Word


def test_chunk_spans_have_five_second_overlap() -> None:
    spans = asr._chunk_spans(1801)

    assert spans == [(0.0, 900), (895, 1795), (1790, 1801)]


def test_overlap_words_are_deduplicated_and_shifted() -> None:
    merged = Transcript(words=[Word("before", 894.0, 895.2)])
    chunk = Transcript(
        words=[
            Word("before", 0.0, 0.2),
            Word("after", 0.4, 0.8, speaker="SPEAKER_01"),
        ]
    )

    asr._merge_chunk(merged, chunk, offset=895.0)

    assert [word.text for word in merged.words] == ["before", "after"]
    assert merged.words[-1].start == pytest.approx(895.4)
    assert merged.words[-1].speaker == "SPEAKER_01"


def test_openai_words_normalize_to_common_contract() -> None:
    response = SimpleNamespace(
        words=[
            SimpleNamespace(word="Hello", start=0.1, end=0.4),
            SimpleNamespace(word="world.", start=0.5, end=1.0),
        ]
    )

    transcript = asr._transcript_from_openai(response, model="whisper", source="groq")

    assert transcript.source == "groq"
    assert transcript.words[1].text == "world."
    assert transcript.segments[0].end == pytest.approx(1.0)


def test_chunk_fails_over_and_records_usage(initialised_db, monkeypatch, tmp_path) -> None:
    source = store.create_source(Source(id=new_id(), type="upload", path="x", title="x"))
    job = store.create_job(Job(id=new_id(), source_id=source.id))
    first = ProviderTarget("first", "asr", "groq", "one", 20)
    second = ProviderTarget("second", "asr", "local", "small", 10)
    store.upsert_provider_target(first)
    store.upsert_provider_target(second)

    class Broken:
        def transcribe(self, audio, *, language=""):
            raise RuntimeError("401 invalid API key")

    class Working:
        def transcribe(self, audio, *, language=""):
            return Transcript(words=[Word("Works.", 0, 1)])

    monkeypatch.setattr(
        asr,
        "_provider_for",
        lambda target, settings: Broken() if target.id == "first" else Working(),
    )

    transcript, target_id = asr._transcribe_chunk(
        tmp_path / "audio.flac",
        config.Settings(),
        job_id=job.id,
        audio_seconds=8.0,
    )

    assert transcript.text == "Works."
    assert target_id == "second"
    targets = {target.id: target for target in store.list_provider_targets("asr")}
    assert targets["first"].health == "auth_failed"
    usage = {row["target_id"]: row for row in store.provider_usage_summary()}
    assert usage["first"]["errors"] == 1
    assert usage["second"]["audio_seconds"] == pytest.approx(8.0)
