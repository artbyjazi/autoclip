"""LLM providers for highlight detection.

Four adapters behind one interface. ``openai`` is the widest of them: because
its base URL is configurable, it also serves OpenRouter, Groq, DeepSeek,
Together, and any local OpenAI-compatible server.
"""

from __future__ import annotations

from ..config import Settings, get_secret
from .anthropic_provider import AnthropicProvider
from .base import (
    ClipCandidate,
    ClipCandidates,
    DetectionConfig,
    LLMProvider,
    ProviderError,
    ProviderStatus,
    TranscriptWindow,
)
from .gemini_provider import GeminiProvider
from .groq_provider import GroqProvider
from .ollama_provider import OllamaProvider
from .openai_provider import OpenAIProvider
from .openrouter_provider import OpenRouterProvider

__all__ = [
    "PROVIDERS",
    "AnthropicProvider",
    "ClipCandidate",
    "ClipCandidates",
    "DetectionConfig",
    "GeminiProvider",
    "LLMProvider",
    "OllamaProvider",
    "OpenAIProvider",
    "GroqProvider",
    "OpenRouterProvider",
    "ProviderError",
    "ProviderStatus",
    "TranscriptWindow",
    "build_provider",
    "detection_config",
    "provider_names",
]

PROVIDERS: dict[str, type[LLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
    "groq": GroqProvider,
    "openrouter": OpenRouterProvider,
    "ollama": OllamaProvider,
}


def provider_names() -> list[str]:
    return list(PROVIDERS)


def build_provider(name: str | None = None, settings: Settings | None = None) -> LLMProvider:
    """Construct a configured provider.

    Pulls the model and base URL from settings and the API key from the keyring,
    so callers never handle secrets themselves.
    """
    from ..config import load

    settings = settings if settings is not None else load()
    key = name or settings.active_provider

    provider_cls = PROVIDERS.get(key)
    if provider_cls is None:
        raise ProviderError(
            f"Unknown provider '{key}'.",
            hint=f"Available providers: {', '.join(PROVIDERS)}",
        )

    provider_settings = settings.provider(key)
    api_key = get_secret(key, settings) if provider_cls.requires_key else None

    return provider_cls(
        provider_settings.model,
        api_key=api_key,
        base_url=provider_settings.base_url,
    )


def detection_config(settings: Settings | None = None) -> DetectionConfig:
    """Build a :class:`DetectionConfig` from user settings."""
    from ..config import load

    settings = settings if settings is not None else load()
    return DetectionConfig(
        min_duration_s=settings.clips.min_duration_s,
        max_duration_s=settings.clips.max_duration_s,
        max_clips=settings.clips.max_clips,
        language=settings.whisper.language,
    )
