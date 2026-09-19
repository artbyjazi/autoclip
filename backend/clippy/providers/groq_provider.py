"""Groq's OpenAI-compatible chat-completions endpoint."""

from __future__ import annotations

from .openai_provider import OpenAIProvider


class GroqProvider(OpenAIProvider):
    name = "groq"

    def __init__(self, model: str = "", *, api_key: str | None = None, base_url: str | None = None):
        super().__init__(
            model or "openai/gpt-oss-120b",
            api_key=api_key,
            base_url=base_url or "https://api.groq.com/openai/v1",
        )
