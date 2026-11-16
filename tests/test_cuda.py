"""Tests for CUDA shared-library registration."""

import os
from pathlib import Path

import autoclip.cuda as cuda


def test_windows_dll_directory_handles_are_retained_and_registration_is_cached(
    monkeypatch,
) -> None:
    directories = [Path("fake-cuda/cublas/bin"), Path("fake-cuda/cudnn/bin")]
    handles = [object(), object()]
    calls: list[str] = []

    def fake_add_dll_directory(path: str) -> object:
        calls.append(path)
        return handles[len(calls) - 1]

    monkeypatch.setattr(cuda, "library_directories", lambda: directories)
    monkeypatch.setattr(cuda.sys, "platform", "win32")
    monkeypatch.setattr(cuda.os, "add_dll_directory", fake_add_dll_directory, raising=False)
    monkeypatch.setattr(cuda, "_DLL_DIRECTORY_HANDLES", [])
    monkeypatch.setenv("PATH", "existing-path")

    cuda.ensure_cuda_libraries.cache_clear()

    try:
        registered = cuda.ensure_cuda_libraries()

        assert registered == directories
        assert handles == cuda._DLL_DIRECTORY_HANDLES
        assert calls == [str(directory) for directory in directories]

        # A second call must use the cache, not register duplicate directories.
        assert cuda.ensure_cuda_libraries() == directories
        assert len(calls) == len(directories)

        path_entries = os.environ["PATH"].split(os.pathsep)
        assert all(str(directory) in path_entries for directory in directories)
        assert "existing-path" in path_entries
    finally:
        cuda.ensure_cuda_libraries.cache_clear()
