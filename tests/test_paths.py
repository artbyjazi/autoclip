"""Filesystem layout resolution."""

from __future__ import annotations

from pathlib import Path

import pytest
from clippy import paths


def test_home_override_is_honoured(clippy_home: Path) -> None:
    assert paths.root() == clippy_home.resolve()


def test_override_is_read_per_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Resolving lazily rather than caching at import time is what lets tests —
    # and a user changing the env var — take effect without a reload.
    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "first"))
    first = paths.root()

    monkeypatch.setenv(paths.ENV_HOME, str(tmp_path / "second"))

    assert paths.root() != first


def test_defaults_under_the_user_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Path.home() is patched, not just the env var: without it this test reads
    # the developer's real home directory and passes or fails depending on
    # whether they happen to have a pre-rename install.
    monkeypatch.delenv(paths.ENV_HOME, raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    assert paths.root() == (tmp_path / ".clippy").resolve()


class TestLegacyHomeMigration:
    """Pre-rename installs migrate without deleting or copying large media."""

    @pytest.fixture(autouse=True)
    def _isolated_home(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(paths.ENV_HOME, raising=False)
        monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))

    def test_legacy_directory_is_migrated_when_the_new_one_is_absent(
        self, tmp_path: Path
    ) -> None:
        legacy = tmp_path / paths.LEGACY_DIR_NAMES[0]
        legacy.mkdir()
        (legacy / paths.LEGACY_DB_NAMES[0]).write_bytes(b"SQLite format 3\x00")
        (legacy / "config.json").write_text("{}", encoding="utf-8")
        (legacy / "media").mkdir()
        (legacy / "media" / "keep.mp4").write_bytes(b"video")

        paths.ensure_layout()

        destination = tmp_path / paths.DEFAULT_DIR_NAME
        assert paths.root() == destination.resolve()
        assert (destination / paths.DB_NAME).read_bytes() == b"SQLite format 3\x00"
        assert (destination / "config.json").read_text(encoding="utf-8") == "{}"
        assert (destination / "media").is_symlink()
        assert (destination / "media" / "keep.mp4").read_bytes() == b"video"
        assert (destination / ".migrated-from").read_text(encoding="utf-8").strip() == str(
            legacy.resolve()
        )
        assert legacy.exists()

    def test_new_directory_wins_when_both_exist(self, tmp_path: Path) -> None:
        (tmp_path / paths.LEGACY_DIR_NAMES[0]).mkdir()
        (tmp_path / paths.DEFAULT_DIR_NAME).mkdir()

        assert paths.root() == (tmp_path / paths.DEFAULT_DIR_NAME).resolve()

    def test_new_directory_is_used_on_a_clean_machine(self, tmp_path: Path) -> None:
        assert paths.root() == (tmp_path / paths.DEFAULT_DIR_NAME).resolve()

    def test_explicit_override_beats_both(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        (tmp_path / paths.LEGACY_DIR_NAMES[0]).mkdir()
        elsewhere = tmp_path / "somewhere-else"
        monkeypatch.setenv(paths.ENV_HOME, str(elsewhere))

        assert paths.root() == elsewhere.resolve()

    def test_explicit_legacy_home_migrates_to_explicit_destination(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        legacy_home = tmp_path / "old-home"
        destination = tmp_path / "new-home"
        legacy_home.mkdir()
        (legacy_home / paths.LEGACY_DB_NAMES[0]).write_bytes(b"old")
        monkeypatch.setenv(paths.ENV_HOME, str(destination))
        monkeypatch.setenv(paths.LEGACY_ENV_HOME, str(legacy_home))

        paths.ensure_layout()

        assert paths.db_path() == destination / paths.DB_NAME
        assert paths.db_path().read_bytes() == b"old"
        assert (legacy_home / paths.LEGACY_DB_NAMES[0]).exists()

    def test_new_database_wins_when_both_exist(self, tmp_path: Path) -> None:
        home = tmp_path / paths.DEFAULT_DIR_NAME
        home.mkdir()
        (home / paths.LEGACY_DB_NAMES[0]).write_bytes(b"old")
        (home / paths.DB_NAME).write_bytes(b"new")

        assert paths.db_path().name == paths.DB_NAME

    def test_clean_install_uses_the_new_database_name(self, tmp_path: Path) -> None:
        (tmp_path / paths.DEFAULT_DIR_NAME).mkdir()

        assert paths.db_path().name == paths.DB_NAME


def test_ensure_layout_creates_the_tree(clippy_home: Path) -> None:
    paths.ensure_layout()

    for directory in (paths.media_dir(), paths.work_dir(), paths.exports_dir()):
        assert directory.is_dir()


def test_ensure_layout_is_idempotent(clippy_home: Path) -> None:
    paths.ensure_layout()
    marker = paths.media_dir() / "keep.txt"
    marker.write_text("x", encoding="utf-8")

    paths.ensure_layout()

    assert marker.exists()


def test_all_artifacts_live_under_the_root(clippy_home: Path) -> None:
    root = paths.root()
    derived = [
        paths.media_dir(),
        paths.work_dir(),
        paths.exports_dir(),
        paths.db_path(),
        paths.config_path(),
        paths.job_work_dir("job123"),
        paths.source_media_dir("src456"),
    ]

    for path in derived:
        assert root in path.parents


def test_per_job_and_per_source_directories_are_namespaced(clippy_home: Path) -> None:
    assert paths.job_work_dir("job123").name == "job123"
    assert paths.job_work_dir("job123").parent == paths.work_dir()
    assert paths.source_media_dir("src456").parent == paths.media_dir()
