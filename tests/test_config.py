"""Settings persistence and secret storage."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from clippy import config, paths


def test_defaults_load_without_a_config_file() -> None:
    settings = config.load()

    assert settings.active_provider == "gemini"
    assert settings.whisper.model == "small"
    assert settings.export.loudness_lufs == -14.0
    assert set(settings.providers) == {
        "anthropic",
        "openai",
        "gemini",
        "groq",
        "openrouter",
        "ollama",
    }


def test_settings_round_trip() -> None:
    settings = config.load()
    settings.active_provider = "ollama"
    settings.whisper.model = "large-v3"
    settings.clips.max_clips = 25
    config.save(settings)

    reloaded = config.load()
    assert reloaded.active_provider == "ollama"
    assert reloaded.whisper.model == "large-v3"
    assert reloaded.clips.max_clips == 25


def test_load_falls_back_to_defaults_on_corrupt_file() -> None:
    paths.ensure_layout()
    paths.config_path().write_text("{ not json", encoding="utf-8")

    settings = config.load()

    assert settings.active_provider == "gemini"


def test_provider_returns_active_by_default() -> None:
    settings = config.load()
    settings.active_provider = "gemini"

    assert settings.provider().model == settings.providers["gemini"].model
    assert settings.provider("openai").model == "gpt-4o"


def test_operator_cookie_path_can_come_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLIPPY_YOUTUBE_COOKIES_FILE", "/run/secrets/youtube-cookies.txt")

    assert config.load().ingest.cookies_file == "/run/secrets/youtube-cookies.txt"


def test_unknown_provider_key_is_created_on_demand() -> None:
    settings = config.load()

    created = settings.provider("some-future-vendor")

    assert created.model == ""
    assert "some-future-vendor" in settings.providers


def test_save_is_atomic_leaving_no_temp_file() -> None:
    config.save(config.load())

    leftovers = list(paths.root().glob("*.tmp"))
    assert leftovers == []


class TestSecrets:
    def test_secret_round_trip_via_keyring(self, fake_keyring) -> None:
        secure = config.set_secret("anthropic", "sk-ant-test")

        assert secure is True
        assert config.get_secret("anthropic") == "sk-ant-test"

    def test_keyring_storage_never_touches_config_file(self, fake_keyring) -> None:
        config.set_secret("anthropic", "sk-ant-test")
        config.save(config.load())

        raw = paths.config_path().read_text(encoding="utf-8")
        assert "sk-ant-test" not in raw

    def test_missing_secret_returns_none(self, fake_keyring) -> None:
        assert config.get_secret("openai") is None

    def test_delete_removes_the_secret(self, fake_keyring) -> None:
        config.set_secret("openai", "sk-openai-test")
        config.delete_secret("openai")

        assert config.get_secret("openai") is None

    def test_environment_variable_overrides_stored_secret(
        self, fake_keyring, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        config.set_secret("anthropic", "from-keyring")
        monkeypatch.setenv("CLIPPY_ANTHROPIC_KEY", "from-env")

        assert config.get_secret("anthropic") == "from-env"


class TestSecretsWithoutKeyring:
    """Headless installs use a separate owner-only secret file."""

    @pytest.mark.skipif(
        sys.platform == "win32", reason="Windows has no POSIX owner-only file modes"
    )
    def test_file_store_is_owner_only(self, no_keyring) -> None:
        secure = config.set_secret("anthropic", "sk-plaintext")

        assert secure is False
        assert config.load().insecure_secret_storage is False
        assert paths.secrets_path().stat().st_mode & 0o777 == 0o600

    def test_fallback_secret_is_still_readable(self, no_keyring) -> None:
        config.set_secret("anthropic", "sk-plaintext")

        assert config.get_secret("anthropic") == "sk-plaintext"

    def test_fallback_secret_is_not_in_the_public_schema(self, no_keyring) -> None:
        config.set_secret("anthropic", "sk-plaintext")

        settings = config.load()
        # The secret must survive a round trip but stay out of model_dump, so it
        # can never leak through an API response that serialises settings.
        assert "sk-plaintext" not in json.dumps(settings.model_dump(mode="json"))
        assert "sk-plaintext" in paths.secrets_path().read_text(encoding="utf-8")
        if paths.config_path().exists():
            assert "sk-plaintext" not in paths.config_path().read_text(encoding="utf-8")

    def test_deleting_last_file_secret_keeps_secure_flag(self, no_keyring) -> None:
        config.set_secret("anthropic", "sk-plaintext")
        config.delete_secret("anthropic")

        assert config.load().insecure_secret_storage is False

    def test_keyring_errors_degrade_to_the_fallback(self, failing_keyring) -> None:
        # Uses a fixture rather than importing from conftest: `tests` is not an
        # installed package, so `from tests.conftest import ...` resolves only
        # when the repo root happens to be on sys.path. It did locally and did
        # not in CI.
        secure = config.set_secret("gemini", "sk-gemini")

        assert secure is False
        assert config.get_secret("gemini") == "sk-gemini"


def test_config_file_is_written_under_clippy_home(clippy_home: Path) -> None:
    config.save(config.load())

    assert paths.config_path() == clippy_home.resolve() / "config.json"
    assert paths.config_path().exists()
