"""Settings persistence and secret storage.

Non-secret settings live in ``<root>/config.json`` as plain JSON so users can
hand-edit them. Secrets (LLM API keys, the HuggingFace token) are kept out of
that file and stored in the OS keyring — Credential Manager on Windows, Keychain
on macOS, Secret Service on Linux.

If no keyring backend is available (headless Linux, some Docker images), we fall
back to writing secrets into ``config.json`` with restrictive permissions and
set :attr:`Settings.insecure_secret_storage` so the UI and ``autoclip doctor``
can warn about it. Silently downgrading security without telling the user is the
thing we're trying to avoid.
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

KEYRING_SERVICE = "autoclip"

#: Keyring service used before the rename. Read from as a fallback so keys
#: stored under the old name aren't silently lost; writes always use the new one.
LEGACY_KEYRING_SERVICE = "clipforge"

ProviderName = Literal["anthropic", "openai", "gemini", "ollama"]

#: Providers that authenticate with an API key. Ollama runs locally and needs none.
KEYED_PROVIDERS: tuple[str, ...] = ("anthropic", "openai", "gemini")

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


class Segment(BaseModel):
    """One user-picked section of the source video, in manual selection mode."""

    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    title: str = ""


class SelectionSettings(BaseModel):
    """How clips are chosen. Per-job, not a meaningful default to persist —
    it rides along in `job.settings_json` because it lives on `Settings`, which
    is what lets the runner read it through the same path as everything else."""

    #: "auto" = LLM picks highlights. "manual" = use `segments` verbatim.
    mode: Literal["auto", "manual"] = "auto"
    segments: list[Segment] = Field(default_factory=list)


class IngestSettings(BaseModel):
    #: yt-dlp format selector. Default caps at 1080p to keep downloads sane.
    ytdlp_format: str = "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080]"
    #: Browser to pull site cookies from ("chrome", "firefox", "edge", ...).
    #: As of 2026 most anonymous YouTube downloads hit a bot check that only
    #: browser cookies reliably clear, and Twitch sub-only VODs need the cookies
    #: of a subscribed account — so this is a first-class setting.
    cookies_from_browser: str = ""
    #: Offer YouTube's own auto-captions as a fast path, skipping Whisper.
    #: YouTube-only, and skipped when a time range is requested, since the
    #: caption track covers the whole video and yt-dlp won't trim it.
    prefer_youtube_captions: bool = False


class ExportSettings(BaseModel):
    ratio: Literal["9:16", "1:1", "16:9"] = "9:16"
    caption_style: str = "bold_pop"
    #: Integrated loudness target in LUFS. -14 is the de-facto platform standard.
    loudness_lufs: float = -14.0
    #: Use h264_nvenc when the hardware supports it. Falls back to libx264.
    prefer_hardware_encoder: bool = True
    crf: int = 18
    #: Also write a .srt sidecar next to each exported clip.
    write_srt: bool = False
    #: Where finished clips are written. Empty = <root>/exports. Set from the
    #: Make tab's folder picker; resolved through
    #: `pipeline.export.resolve_output_dir` so a one-off re-export from the
    #: clip review page lands in the same place as the job's own run.
    output_dir: str = ""


class Settings(BaseModel):
    """Top-level persisted settings."""

    active_provider: ProviderName = "anthropic"
    providers: dict[str, ProviderSettings] = Field(
        default_factory=lambda: {
            "anthropic": ProviderSettings(model="claude-sonnet-5"),
            "openai": ProviderSettings(model="gpt-4o"),
            "gemini": ProviderSettings(model="gemini-2.0-flash"),
            "ollama": ProviderSettings(model="", base_url="http://localhost:11434"),
        }
    )
    whisper: WhisperSettings = Field(default_factory=WhisperSettings)
    clips: ClipSettings = Field(default_factory=ClipSettings)
    ingest: IngestSettings = Field(default_factory=IngestSettings)
    export: ExportSettings = Field(default_factory=ExportSettings)
    selection: SelectionSettings = Field(default_factory=SelectionSettings)

    #: Set when secrets had to be written to config.json because no keyring
    #: backend was usable. Surfaced as a warning in the UI and in `doctor`.
    insecure_secret_storage: bool = False
    #: Plaintext secret fallback. Empty whenever the keyring is working.
    #: A private attribute so it never round-trips through the public schema —
    #: `save()` writes it back explicitly.
    _fallback_secrets: dict[str, str] = PrivateAttr(default_factory=dict)

    model_config = {"extra": "ignore"}

    def provider(self, name: str | None = None) -> ProviderSettings:
        """Return settings for ``name``, defaulting to the active provider."""
        key = name or self.active_provider
        return self.providers.setdefault(key, ProviderSettings())


def load() -> Settings:
    """Read settings from disk, falling back to defaults on a missing or bad file."""
    path = paths.config_path()
    if not path.exists():
        return Settings()
    try:
        raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Could not read %s (%s); falling back to defaults.", path, exc)
        return Settings()

    fallback = raw.pop("_fallback_secrets", {}) or {}
    settings = Settings.model_validate(raw)
    settings._fallback_secrets = fallback
    return settings


def save(settings: Settings) -> None:
    """Write settings to disk atomically, with owner-only permissions."""
    paths.ensure_layout()
    path = paths.config_path()

    payload = settings.model_dump(mode="json")
    if settings._fallback_secrets:
        payload["_fallback_secrets"] = settings._fallback_secrets

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
    env_override = os.environ.get(f"AUTOCLIP_{key.upper()}_KEY")
    if env_override:
        return env_override

    kr = _keyring()
    if kr is not None:
        for service in (KEYRING_SERVICE, LEGACY_KEYRING_SERVICE):
            try:
                value = kr.get_password(service, key)
                if value:
                    return value
            except Exception as exc:  # pragma: no cover - backend dependent
                log.warning("Keyring read failed for %s: %s", key, exc)

    settings = settings if settings is not None else load()
    return settings._fallback_secrets.get(key) or None


def set_secret(key: str, value: str, settings: Settings | None = None) -> bool:
    """Store a secret. Returns True if it went to the keyring, False if to disk.

    A False return is not an error — it means the caller should surface the
    insecure-storage warning to the user.
    """
    kr = _keyring()
    if kr is not None:
        try:
            kr.set_password(KEYRING_SERVICE, key, value)
            return True
        except Exception as exc:  # pragma: no cover - backend dependent
            log.warning("Keyring write failed for %s: %s", key, exc)

    settings = settings if settings is not None else load()
    settings._fallback_secrets[key] = value
    settings.insecure_secret_storage = True
    save(settings)
    return False


def delete_secret(key: str, settings: Settings | None = None) -> None:
    """Remove a secret from both the keyring and the plaintext fallback."""
    kr = _keyring()
    if kr is not None:
        # Backends raise when the entry is absent; deleting a missing secret is
        # not an error from the caller's point of view. Both services are cleared
        # so a delete can't leave a pre-rename copy behind to resurface.
        for service in (KEYRING_SERVICE, LEGACY_KEYRING_SERVICE):
            with contextlib.suppress(Exception):
                kr.delete_password(service, key)

    settings = settings if settings is not None else load()
    if settings._fallback_secrets.pop(key, None) is not None:
        settings.insecure_secret_storage = bool(settings._fallback_secrets)
        save(settings)
