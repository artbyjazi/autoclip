"""OpenRouter's OpenAI-compatible endpoint."""

from __future__ import annotations

from .openai_provider import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"

    #: Only `:free` variants cost nothing. Verified against the live
    #: catalogue; the free set rotates, so prefer the DeepSeek flash model
    #: which is JSON-reliable, with Qwen as backup.
    SUGGESTED_MODELS = [
        "deepseek/deepseek-v4-flash-0731:free",
        "qwen/qwen3.8-27b:free",
    ]

    def __init__(self, model: str = "", *, api_key: str | None = None, base_url: str | None = None):
        super().__init__(
            model or "deepseek/deepseek-v4-flash-0731:free",
            api_key=api_key,
            base_url=base_url or "https://openrouter.ai/api/v1",
        )
