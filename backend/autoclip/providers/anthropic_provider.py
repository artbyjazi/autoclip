"""Anthropic (Claude) provider."""

from __future__ import annotations

import logging

from .base import DetectionConfig, LLMProvider, ProviderError, ProviderStatus

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-sonnet-5"

#: Shown in the settings UI. Kept short and current rather than exhaustive.
SUGGESTED_MODELS = [
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-4-5-20251001",
]


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    requires_key = True

    def __init__(self, model: str = "", *, api_key: str | None = None, base_url: str | None = None):
        super().__init__(model or DEFAULT_MODEL, api_key=api_key, base_url=base_url)

    def _client(self):
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover - anthropic is a core dep
            raise ProviderError(
                "The anthropic package is not installed.", provider=self.name
            ) from exc

        if not self.api_key:
            raise ProviderError(
                "No Anthropic API key is set.",
                provider=self.name,
                hint="Add one with `autoclip config set-secret anthropic`.",
            )

        kwargs = {"api_key": self.api_key}
        if self.base_url:
            kwargs["base_url"] = self.base_url
        return AsyncAnthropic(**kwargs)

    async def _complete(self, system: str, user: str, config: DetectionConfig) -> str:
        client = self._client()
        try:
            # No assistant prefill and no temperature: current Claude models
            # (Opus 4.6+, Sonnet 4.6+, Opus 5, Sonnet 5) reject both with a 400.
            # The prompt asks for bare JSON, and extract_json_object strips any
            # prose or fences the model adds anyway.
            message = await client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except Exception as exc:
            raise _translate(exc, self.name, self.model) from exc

        return "".join(block.text for block in message.content if hasattr(block, "text"))

    async def health_check(self) -> ProviderStatus:
        if not self.api_key:
            return ProviderStatus(
                name=self.name, available=False, detail="No API key set", models=SUGGESTED_MODELS
            )
        try:
            client = self._client()
            await client.messages.create(
                model=self.model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ok"}],
            )
        except Exception as exc:
            return ProviderStatus(
                name=self.name, available=False, detail=str(exc)[:200], models=SUGGESTED_MODELS
            )
        return ProviderStatus(
            name=self.name, available=True, detail=self.model, models=SUGGESTED_MODELS
        )


def _translate(exc: Exception, provider: str, model: str) -> ProviderError:
    message = str(exc)
    lowered = message.lower()

    if "authentication" in lowered or "invalid x-api-key" in lowered or "401" in lowered:
        return ProviderError(
            "Anthropic rejected the API key.",
            provider=provider,
            hint="Re-add it with `autoclip config set-secret anthropic`.",
        )
    if "rate limit" in lowered or "429" in lowered:
        return ProviderError(
            "Anthropic rate limit reached.",
            provider=provider,
            hint="Wait a moment and retry the job, or switch to a different provider.",
        )
    if "not_found" in lowered or "model" in lowered and "404" in lowered:
        return ProviderError(
            f"Anthropic does not recognise the model '{model}'.",
            provider=provider,
            hint=f"Try one of: {', '.join(SUGGESTED_MODELS)}",
        )
    if "credit" in lowered or "billing" in lowered:
        return ProviderError(
            "Anthropic reports a billing or credit problem.",
            provider=provider,
            hint="Check your plan at console.anthropic.com.",
        )
    return ProviderError(f"Anthropic request failed: {message}", provider=provider)
