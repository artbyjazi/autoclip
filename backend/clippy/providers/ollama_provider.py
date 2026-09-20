"""Ollama provider — the fully-local path.

Talks to Ollama's HTTP API directly rather than through its SDK, keeping the
dependency surface to httpx (already required elsewhere).

Clip quality tracks model quality closely here. A 3B model will return
syntactically valid JSON full of mediocre clips; the recommendation list below
reflects what actually produces usable selections.
"""

from __future__ import annotations

import logging

import httpx

from .base import DetectionConfig, LLMProvider, ProviderError, ProviderStatus

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434"

#: Models with enough instruction-following to hold the JSON contract and enough
#: judgement to rank clips sensibly. Below roughly 7B, selection quality falls
#: off sharply even when the JSON stays valid.
RECOMMENDED_MODELS = [
    "llama3.1:8b",
    "qwen2.5:14b",
    "mistral-nemo:12b",
    "gemma2:9b",
]

#: Highlight detection over an 8-minute window is slow on consumer hardware.
_REQUEST_TIMEOUT = httpx.Timeout(600.0, connect=5.0)


class OllamaProvider(LLMProvider):
    name = "ollama"
    requires_key = False

    def __init__(self, model: str = "", *, api_key: str | None = None, base_url: str | None = None):
        super().__init__(model, api_key=api_key, base_url=base_url or DEFAULT_BASE_URL)

    @property
    def url(self) -> str:
        return (self.base_url or DEFAULT_BASE_URL).rstrip("/")

    async def _complete(self, system: str, user: str, config: DetectionConfig) -> str:
        if not self.model:
            raise ProviderError(
                "No Ollama model is selected.",
                provider=self.name,
                hint=f"Pull one first, e.g. `ollama pull {RECOMMENDED_MODELS[0]}`.",
            )

        payload = {
            "model": self.model,
            "system": system,
            "prompt": user,
            "stream": False,
            # Ollama's JSON mode constrains decoding to valid JSON, which is the
            # single biggest reliability win for small local models.
            "format": "json",
            "options": {"temperature": config.temperature},
        }

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                response = await client.post(f"{self.url}/api/generate", json=payload)
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError as exc:
            raise ProviderError(
                f"Could not reach Ollama at {self.url}.",
                provider=self.name,
                hint="Start it with `ollama serve`, or install it from ollama.com.",
            ) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError(
                "Ollama timed out.",
                provider=self.name,
                hint=(
                    "Large transcripts on a small GPU can exceed the limit. Try a smaller "
                    "model, or use a hosted provider for this video."
                ),
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise _translate_status(exc, self.name, self.model) from exc

        return data.get("response", "")

    async def health_check(self) -> ProviderStatus:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0)) as client:
                response = await client.get(f"{self.url}/api/tags")
                response.raise_for_status()
                data = response.json()
        except httpx.ConnectError:
            return ProviderStatus(
                name=self.name,
                available=False,
                detail=f"Not running at {self.url}",
            )
        except Exception as exc:
            return ProviderStatus(name=self.name, available=False, detail=str(exc)[:200])

        models = [m.get("name", "") for m in data.get("models", [])]
        models = [m for m in models if m]

        if not models:
            return ProviderStatus(
                name=self.name,
                available=False,
                detail=f"Running, but no models pulled. Try `ollama pull {RECOMMENDED_MODELS[0]}`.",
                models=[],
            )

        return ProviderStatus(
            name=self.name, available=True, detail=f"{len(models)} model(s)", models=sorted(models)
        )


def _translate_status(exc: httpx.HTTPStatusError, provider: str, model: str) -> ProviderError:
    if exc.response.status_code == 404:
        return ProviderError(
            f"Ollama has no model named '{model}'.",
            provider=provider,
            hint=f"Pull it with `ollama pull {model}`, or pick one you already have.",
        )
    return ProviderError(f"Ollama returned HTTP {exc.response.status_code}.", provider=provider)
