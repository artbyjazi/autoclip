"""Settings persistence and secret storage.

Non-secret settings live in ``<root>/config.json``. Provider credentials live
in a separate owner-only ``secrets.json`` file on headless installs, or in the
native OS keyring when one is available. Secret values are never returned by
the API or mixed into ordinary settings.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import stat
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, PrivateAttr

from . import paths

log = logging.getLogger(__name__)

KEYRING_SERVICE = "clippy"

#: Keyring service used before the rename. Read from as a fallback so keys
#: stored under the old name aren't silently lost; writes always use the new one.
LEGACY_KEYRING_SERVICES = ("autoclip", "clipforge")

ProviderName = Literal["anthropic", "openai", "gemini", "groq", "openrouter", "ollama"]

#: Providers that authenticate with an API key. Ollama runs locally and needs none.
KEYED_PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "gemini", "groq", "openrouter")

#: Extra secrets that aren't tied to a provider.
HF_TOKEN_KEY = "huggingface_token"


class ProviderSettings(BaseModel):
    """Per-provider configuration. The API key itself is never stored here."""

    model: str = ""
    #: Only meaningful for the OpenAI-compatible provider. Pointing this at
    #: OpenRouter, Groq, DeepSeek, or a local LM Studio server is the supported
    #: way to use any other vendor.
    base_url: str | None = None


class WhisperSettings(BaseModel):
    #: tiny | base | small | medium | large-v3 — or a local path.
    model: str = "small"
    #: Force a CTranslate2 compute type. Empty means "let hardware detection pick".
    compute_type: str = ""
    #: ISO 639-1 code, or empty for auto-detection.
    language: str = ""
    #: Run WhisperX diarization to label words by speaker. Needs the
    #: ``diarization`` extra and a HuggingFace token.
    diarization: bool = False


class ClipSettings(BaseModel):
    min_duration_s: float = 20.0
    max_duration_s: float = 90.0
    max_clips: int = 10


class IngestSettings(BaseModel):
    #: yt-dlp format selector. Default caps at 1080p to keep downloads sane.
    ytdlp_format: str = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]"
    #: Browser to pull YouTube cookies from ("chrome", "firefox", "edge", ...).
    #: As of 2026 most anonymous YouTube downloads hit a bot check that only
    #: browser cookies reliably clear, so this is a first-class setting.
    cookies_from_browser: str = ""
    #: Netscape cookie file for headless servers. The API only exposes whether
    #: it is configured; it never returns this filesystem path.
    cookies_file: str = ""
    #: Offer YouTube's own auto-captions as a fast path, skipping Whisper.
    prefer_youtube_captions: bool = False


class ExportSettings(BaseModel):
    ratio: Literal["9:16", "1:1", "16:9"] = "9:16"
    caption_style: str = "bold_pop"
    #: Integrated loudness target in LUFS. -14 is the de-facto platform standard.
    loudness_lufs: float = -14.0
    #: Use h264_nvenc when the hardware supports it. Falls back to libx264.
    prefer_hardware_encoder: bool = True
    crf: int = 21
    preset: str = "veryfast"
    #: Also write a .srt sidecar next to each exported clip.
    write_srt: bool = False


class StorageSettings(BaseModel):
    source_retention_days: int = 7
    preview_retention_hours: int = 24
    reject_new_jobs_below_gb: float = 2.0


class Settings(BaseModel):
    """Top-level persisted settings."""

    active_provider: ProviderName = "gemini"
    providers: dict[str, ProviderSettings] = Field(
        default_factory=lambda: {
            "anthropic": ProviderSettings(model="claude-sonnet-5"),
            "openai": ProviderSettings(model="gpt-4o"),
            "gemini": ProviderSettings(model="gemini-3.8-flash"),
            "groq": ProviderSettings(
                model="openai/gpt-oss-120b", base_url="https://api.groq.com/openai/v1"
            ),
            "openrouter": ProviderSettings(
                model="openrouter/free", base_url="https://openrouter.ai/api/v1"
            ),
            "ollama": ProviderSettings(model="", base_url="http://localhost:11434"),
        }
    )
    whisper: WhisperSettings = Field(default_factory=WhisperSettings)
    clips: ClipSettings = Field(default_factory=ClipSettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)

    #: Retained on the public contract for compatibility. Clippy's file-backed
    #: store is chmod 0600 and therefore is not considered insecure.
    insecure_secret_storage: bool = False
    _legacy_fallback_secrets: dict[str, str] = PrivateAttr(default_factory=dict)

    model_config = {"extra": "ignore"}

    def provider(self, name: str | None = None) -> ProviderSettings:
        """Return settings for ``name``, defaulting to the active provider."""
        key = name or self.active_provider
        return self.providers.setdefault(key, ProviderSettings())


def load() -> Settings:
    """Read settings from disk, falling back to defaults on a missing or bad file."""
    path = paths.config_path()
    if not path.exists():
        return _runtime_overrides(Settings())
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read %s (%s); falling back to defaults.", path, exc)
        return _runtime_overrides(Settings())

    fallback = raw.pop("_fallback_secrets", {}) or {}
    settings = Settings.model_validate(raw)
    settings._legacy_fallback_secrets = fallback
    if fallback:
        existing = _read_secret_file()
        existing.update({str(key): str(value) for key, value in fallback.items()})
        _write_secret_file(existing)
        save(settings)
    return _runtime_overrides(settings)


def _runtime_overrides(settings: Settings) -> Settings:
    """Apply operator-only paths that must never be part of the HTTP contract."""
    if cookie_file := os.environ.get("CLIPPY_YOUTUBE_COOKIES_FILE"):
        settings.ingest.cookies_file = cookie_file
    return settings


def save(settings: Settings) -> None:
    """Write settings to disk atomically, with owner-only permissions."""
    paths.ensure_layout()
    path = paths.config_path()

    payload = settings.model_dump(mode="json")
    # Write to a sibling temp file then replace, so an interrupted write can
    # never leave a truncated config behind.
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    _restrict_permissions(tmp)
    os.replace(tmp, path)


def _restrict_permissions(path: Path) -> None:
    """Best-effort chmod 600. A no-op where the platform doesn't support it."""
    with contextlib.suppress(OSError):  # platform dependent
        path.chmod(stat.S_IRUSR | stat.S_IWUSR)


# --------------------------------------------------------------------------
# Secrets
# --------------------------------------------------------------------------


def _keyring():
    """Return a usable keyring module, or None if no backend works.

    Importing ``keyring`` succeeds even with no backend; the failure only shows
    up on first use, so we probe explicitly.
    """
    try:
        import keyring
        from keyring.backends.fail import Keyring as FailKeyring
    except ImportError:  # pragma: no cover - keyring is a hard dependency
        return None

    try:
        if isinstance(keyring.get_keyring(), FailKeyring):
            return None
    except Exception:  # pragma: no cover - backend probing can raise anything
        return None
    return keyring


def get_secret(key: str, settings: Settings | None = None) -> str | None:
    """Return a stored secret, or None if unset.

    ``key`` is a provider name for API keys, or :data:`HF_TOKEN_KEY`.
    """
    env_override = os.environ.get(f"CLIPPY_{key.upper()}_KEY") or os.environ.get(
        f"AUTOCLIP_{key.upper()}_KEY"
    )
    if env_override:
        return env_override

    kr = _keyring()
    if kr is not None:
        for service in (KEYRING_SERVICE, *LEGACY_KEYRING_SERVICES):
            try:
                value = kr.get_password(service, key)
                if value:
                    return value
            except Exception as exc:  # pragma: no cover - backend dependent
                log.warning("Keyring read failed for %s: %s", key, exc)

    return _read_secret_file().get(key) or None


def set_secret(key: str, value: str, settings: Settings | None = None) -> bool:
    """Store a secret. Returns True if it went to the keyring, False if to disk.

    A False return is not an error — it means the caller should surface the
    insecure-storage warning to the user.
    """
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(KEYRING_SERVICE, key, value)
            _reset_auth_failure(key)
            return True
        except Exception as exc:  # pragma: no cover - backend dependent
            log.warning("Keyring write failed for %s: %s", key, exc)

    secrets = _read_secret_file()
    secrets[key] = value
    _write_secret_file(secrets)
    _reset_auth_failure(key)
    return False


def _reset_auth_failure(key: str) -> None:
    if key not in KEYED_PROVIDERS:
        return
    # Local import avoids making the configuration model depend on an open DB
    # during module import and keeps first-run config usable before migration.
    try:
        from .db import store

        store.reset_provider_auth(key)
    except Exception:
        log.debug("Provider runtime state is not initialised yet.")


def delete_secret(key: str, settings: Settings | None = None) -> None:
    """Remove a secret from both the keyring and the plaintext fallback."""
    kr = _keyring()
    if kr is not None:
        # Backends raise when the entry is absent; deleting a missing secret is
        # not an error from the caller's point of view. Both services are cleared
        # so a delete can't leave a pre-rename copy behind to resurface.
        for service in (KEYRING_SERVICE, *LEGACY_KEYRING_SERVICES):
            with contextlib.suppress(Exception):
                kr.delete_password(service, key)

    secrets = _read_secret_file()
    if secrets.pop(key, None) is not None:
        _write_secret_file(secrets)


def _read_secret_file() -> dict[str, str]:
    path = paths.secrets_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read secret store %s: %s", path, exc)
        return {}
    return {str(key): str(value) for key, value in payload.items() if value}


def _write_secret_file(secrets: dict[str, str]) -> None:
    paths.ensure_layout()
    path = paths.secrets_path()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(secrets, indent=2), encoding="utf-8")
    _restrict_permissions(tmp)
    os.replace(tmp, path)
    _restrict_permissions(path)
