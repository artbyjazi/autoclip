"""Provider contract handling.

The retry-with-feedback loop and the tolerant JSON extraction are what keep
small local models usable, so both are tested against the specific malformed
shapes models actually emit.
"""

from __future__ import annotations

import pytest
from clippy.providers import ClipCandidates, DetectionConfig, TranscriptWindow
from clippy.providers.base import (
    LLMProvider,
    ProviderError,
    ProviderStatus,
    extract_json_object,
)

VALID = '{"clips":[{"start_word_index":10,"end_word_index":50,"title":"T","score":80}]}'


class ScriptedProvider(LLMProvider):
    """A provider that returns queued responses, for testing the shared loop."""

    name = "scripted"
    requires_key = False

    def __init__(self, responses: list[str]) -> None:
        super().__init__("test-model")
        self.responses = list(responses)
        self.prompts: list[str] = []

    async def _complete(self, system: str, user: str, config: DetectionConfig) -> str:
        self.prompts.append(user)
        return self.responses.pop(0) if self.responses else "{}"

    async def health_check(self) -> ProviderStatus:
        return ProviderStatus(name=self.name, available=True)


@pytest.fixture
def window() -> TranscriptWindow:
    return TranscriptWindow(text="[0]hello [1]world", first_word=0, last_word=100)


class TestJsonExtraction:
    def test_plain_object(self) -> None:
        assert extract_json_object('{"clips":[]}') == {"clips": []}

    def test_markdown_fenced(self) -> None:
        raw = '```json\n{"clips":[{"start_word_index":1,"end_word_index":2}]}\n```'

        assert len(extract_json_object(raw)["clips"]) == 1

    def test_unlabelled_fence(self) -> None:
        assert extract_json_object('```\n{"clips":[]}\n```') == {"clips": []}

    def test_leading_prose_is_ignored(self) -> None:
        raw = 'Here are the clips I found:\n{"clips":[]}'

        assert extract_json_object(raw) == {"clips": []}

    def test_trailing_prose_is_ignored(self) -> None:
        raw = '{"clips":[]}\n\nLet me know if you want more.'

        assert extract_json_object(raw) == {"clips": []}

    def test_bare_array_is_wrapped(self) -> None:
        # Some models skip the wrapper object entirely.
        raw = '[{"start_word_index":1,"end_word_index":2}]'

        assert len(extract_json_object(raw)["clips"]) == 1

    def test_braces_inside_strings_do_not_confuse_matching(self) -> None:
        raw = '{"clips":[{"start_word_index":1,"end_word_index":2,"title":"a } brace"}]}'

        assert extract_json_object(raw)["clips"][0]["title"] == "a } brace"

    def test_escaped_quotes_inside_strings(self) -> None:
        raw = '{"clips":[{"start_word_index":1,"end_word_index":2,"title":"say \\"hi\\""}]}'

        assert extract_json_object(raw)["clips"][0]["title"] == 'say "hi"'

    def test_no_json_raises(self) -> None:
        with pytest.raises(ValueError):
            extract_json_object("I could not find any clips in this transcript.")


class TestCandidateCoercion:
    def test_fractional_score_is_scaled_to_percent(self) -> None:
        result = ClipCandidates.model_validate(
            {"clips": [{"start_word_index": 1, "end_word_index": 2, "score": 0.87}]}
        )

        assert result.clips[0].score == 87

    def test_float_score_is_rounded(self) -> None:
        result = ClipCandidates.model_validate(
            {"clips": [{"start_word_index": 1, "end_word_index": 2, "score": 82.6}]}
        )

        assert result.clips[0].score == 83

    def test_out_of_range_score_is_clamped(self) -> None:
        result = ClipCandidates.model_validate(
            {"clips": [{"start_word_index": 1, "end_word_index": 2, "score": 250}]}
        )

        assert result.clips[0].score == 100

    def test_null_text_fields_become_empty_strings(self) -> None:
        result = ClipCandidates.model_validate(
            {"clips": [{"start_word_index": 1, "end_word_index": 2, "title": None}]}
        )

        assert result.clips[0].title == ""

    def test_missing_score_defaults_to_the_middle(self) -> None:
        result = ClipCandidates.model_validate(
            {"clips": [{"start_word_index": 1, "end_word_index": 2}]}
        )

        assert result.clips[0].score == 50

    def test_negative_index_is_rejected(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ClipCandidates.model_validate(
                {"clips": [{"start_word_index": -1, "end_word_index": 2}]}
            )


class TestDetectionLoop:
    async def test_valid_response_needs_no_retry(self, window: TranscriptWindow) -> None:
        provider = ScriptedProvider([VALID])

        result = await provider.detect_highlights(window, DetectionConfig())

        assert len(result.clips) == 1
        assert len(provider.prompts) == 1

    async def test_malformed_response_triggers_one_retry(self, window: TranscriptWindow) -> None:
        provider = ScriptedProvider(["this is not json at all", VALID])

        result = await provider.detect_highlights(window, DetectionConfig())

        assert len(result.clips) == 1
        assert len(provider.prompts) == 2

    async def test_retry_prompt_carries_the_validation_error(
        self, window: TranscriptWindow
    ) -> None:
        provider = ScriptedProvider(["nonsense", VALID])

        await provider.detect_highlights(window, DetectionConfig())

        assert "did not match the required schema" in provider.prompts[1]

    async def test_two_failures_raise_a_provider_error(self, window: TranscriptWindow) -> None:
        provider = ScriptedProvider(["nope", "still nope"])

        with pytest.raises(ProviderError, match="malformed clip data twice"):
            await provider.detect_highlights(window, DetectionConfig())

    async def test_indices_are_clamped_into_the_window(self) -> None:
        window = TranscriptWindow(text="x", first_word=100, last_word=200)
        provider = ScriptedProvider(['{"clips":[{"start_word_index":0,"end_word_index":9999}]}'])

        result = await provider.detect_highlights(window, DetectionConfig())

        assert result.clips[0].start_word_index == 100
        assert result.clips[0].end_word_index == 200

    async def test_zero_length_candidates_are_dropped(self, window: TranscriptWindow) -> None:
        provider = ScriptedProvider(
            [
                '{"clips":[{"start_word_index":50,"end_word_index":50},'
                '{"start_word_index":60,"end_word_index":40}]}'
            ]
        )

        result = await provider.detect_highlights(window, DetectionConfig())

        assert result.clips == []

    async def test_empty_clip_list_is_a_valid_answer(self, window: TranscriptWindow) -> None:
        # "Nothing here is worth clipping" is a correct response, not a failure.
        provider = ScriptedProvider(['{"clips":[]}'])

        result = await provider.detect_highlights(window, DetectionConfig())

        assert result.clips == []
        assert len(provider.prompts) == 1


class TestRegistry:
    def test_all_providers_are_registered(self) -> None:
        from clippy.providers import PROVIDERS

        assert set(PROVIDERS) == {"anthropic", "openai", "gemini", "groq", "openrouter", "ollama"}

    def test_ollama_needs_no_key(self) -> None:
        from clippy.providers import OllamaProvider

        assert OllamaProvider.requires_key is False

    def test_unknown_provider_raises(self) -> None:
        from clippy.providers import build_provider

        with pytest.raises(ProviderError, match="Unknown provider"):
            build_provider("not-a-provider")

    def test_openai_provider_accepts_a_custom_base_url(self) -> None:
        from clippy.providers import OpenAIProvider

        provider = OpenAIProvider("llama-3.1", base_url="https://openrouter.ai/api/v1")

        assert provider.base_url == "https://openrouter.ai/api/v1"
