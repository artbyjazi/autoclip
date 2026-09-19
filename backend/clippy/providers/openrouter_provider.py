"""OpenRouter's OpenAI-compatible endpoint."""

from __future__ import annotations

from .openai_provider import OpenAIProvider


class OpenRouterProvider(OpenAIProvider):
    name = "openrouter"

    def __init__(self, model: str = "", *, api_key: str | None = None, base_url: str | None = None):
        super().__init__(
            model or "openrouter/free",
            api_key=api_key,
            base_url=base_url or "https://openrouter.ai/api/v1",
        )
