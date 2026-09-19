"""Persistent priority routing across hosted LLM providers."""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import UTC, datetime, timedelta

from ..config import Settings, get_secret
from ..db import store
from ..db.models import ProviderTarget, ProviderUsage, new_id
from . import PROVIDERS
from .base import (
    ClipCandidates,
    DetectionConfig,
    LLMProvider,
    ProviderError,
    ProviderStatus,
    TranscriptWindow,
)

log = logging.getLogger(__name__)

DEFAULT_TARGETS = (
    ProviderTarget("gemini-3-8-flash", "llm", "gemini", "gemini-3.8-flash", 100),
    ProviderTarget("gemini-3-6-flash", "llm", "gemini", "gemini-3.6-flash", 95),
    ProviderTarget("groq-gpt-oss-120b", "llm", "groq", "openai/gpt-oss-120b", 90),
    ProviderTarget("groq-gpt-oss-20b", "llm", "groq", "openai/gpt-oss-20b", 85),
    ProviderTarget(
        "openrouter-free", "llm", "openrouter", "deepseek/deepseek-v4-flash-0731:free", 70
    ),
    ProviderTarget("groq-whisper-turbo", "asr", "groq", "whisper-large-v3-turbo", 100),
    ProviderTarget("gemini-transcribe", "asr", "gemini", "gemini-3.5-transcribe", 90),
    ProviderTarget("local-whisper-small", "asr", "local", "small", 10),
)


def seed_default_targets() -> None:
    existing = {target.id for target in store.list_provider_targets()}
    for target in DEFAULT_TARGETS:
        if target.id not in existing:
            store.upsert_provider_target(target)


def target_available(target: ProviderTarget) -> bool:
    if not target.enabled:
        return False
    if not target.cooldown_until:
        return True
    try:
        return datetime.fromisoformat(target.cooldown_until) <= datetime.now(UTC)
    except ValueError:
        return True


def _retry_after(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return None


def classify_error(exc: Exception) -> tuple[str, float]:
    text = str(exc).lower()
    if any(marker in text for marker in ("401", "403", "api key", "permission")):
        return "auth", 365 * 24 * 60 * 60
    if any(marker in text for marker in ("429", "rate limit", "quota", "resource_exhausted")):
        return "rate_limit", _retry_after(exc) or 10 * 60
    if any(marker in text for marker in ("timeout", "timed out", "503", "502", "500", "connect")):
        return "transient", 2 * 60
    if any(marker in text for marker in ("json", "schema", "malformed", "validation")):
        return "invalid_response", 60
    return "provider_error", 5 * 60


def record_result(
    target: ProviderTarget,
    *,
    job_id: str | None,
    operation: str,
    started: float,
    error: Exception | None = None,
    audio_seconds: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> None:
    latency_ms = round((time.monotonic() - started) * 1000)
    if error is None:
        store.update_provider_runtime(target.id, health="healthy", success=True)
        error_type = None
        status = "success"
    else:
        error_type, cooldown_seconds = classify_error(error)
        cooldown = (datetime.now(UTC) + timedelta(seconds=cooldown_seconds)).isoformat(
            timespec="seconds"
        )
        store.update_provider_runtime(
            target.id,
            health="auth_failed" if error_type == "auth" else "cooldown",
            cooldown_until=cooldown,
            error=str(error)[:500],
            success=False,
        )
        status = "error"
    store.create_provider_usage(
        ProviderUsage(
            id=new_id(),
            target_id=target.id,
            job_id=job_id,
            operation=operation,
            status=status,
            error_type=error_type,
            latency_ms=latency_ms,
            audio_seconds=audio_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    )


class ProviderRouter(LLMProvider):
    """Try configured LLM targets in priority order, with persistent cooldowns."""

    name = "router"
    requires_key = False

    def __init__(self, settings: Settings, *, job_id: str | None = None):
        super().__init__("automatic")
        self.settings = settings
        self.job_id = job_id

    async def _complete(self, system: str, user: str, config: DetectionConfig) -> str:
        raise NotImplementedError

    async def health_check(self) -> ProviderStatus:
        usable = [
            target.id for target in store.list_provider_targets("llm") if target_available(target)
        ]
        return ProviderStatus(name=self.name, available=bool(usable), detail=", ".join(usable))

    def _provider(self, target: ProviderTarget) -> LLMProvider | None:
        cls = PROVIDERS.get(target.provider)
        if cls is None:
            return None
        api_key = get_secret(target.provider, self.settings) if cls.requires_key else None
        if cls.requires_key and not api_key:
            return None
        provider_settings = self.settings.provider(target.provider)
        return cls(target.model, api_key=api_key, base_url=provider_settings.base_url)

    async def detect_highlights(
        self, window: TranscriptWindow, config: DetectionConfig
    ) -> ClipCandidates:
        errors: list[str] = []
        for target in store.list_provider_targets("llm"):
            if not target_available(target):
                continue
            provider = self._provider(target)
            if provider is None:
                continue
            started = time.monotonic()
            result = None
            for attempt in range(2):
                try:
                    result = await asyncio.wait_for(
                        provider.detect_highlights(window, config), timeout=90.0
                    )
                    break
                except Exception as exc:
                    error_type, _ = classify_error(exc)
                    if attempt == 0 and error_type == "transient":
                        await asyncio.sleep(0.5 + random.random() * 0.5)
                        continue
                    record_result(
                        target,
                        job_id=self.job_id,
                        operation="highlight",
                        started=started,
                        error=exc,
                    )
                    errors.append(f"{target.id}: {exc}")
                    break
            if result is None:
                continue
            input_tokens, output_tokens = provider.last_usage
            record_result(
                target,
                job_id=self.job_id,
                operation="highlight",
                started=started,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )
            for candidate in result.clips:
                candidate.provider_target = target.id
            return result
        raise ProviderError(
            "All configured LLM targets failed.",
            provider="router",
            hint="; ".join(errors[-3:]) or "No target has a usable API key.",
        )
