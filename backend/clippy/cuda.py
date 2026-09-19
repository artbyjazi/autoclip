"""Make pip-installed NVIDIA runtime libraries loadable.

``pip install nvidia-cublas-cu12 nvidia-cudnn-cu12`` puts its shared libraries
under ``site-packages/nvidia/<component>/bin`` (Windows) or ``.../lib`` (Linux).
Neither location is on the loader's search path, so CTranslate2 loads the model
fine and then dies at the first inference with::

    RuntimeError: Library cublas64_12.dll is not found or cannot be loaded

The libraries are installed and unusable, which is a worse failure than not
having them: nothing about the message suggests a search-path problem.

This module registers those directories at runtime — ``os.add_dll_directory``
on Windows, and a ``ctypes`` preload on POSIX, where ``LD_LIBRARY_PATH`` is read
once at process start and so cannot be fixed from inside the process.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import sysconfig
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

#: Components shipped as separate wheels. Order matters on POSIX: cuBLAS
#: depends on cuBLASLt, and a dependency must be resident before its dependant.
_COMPONENTS = ("cuda_runtime", "cublas", "cudnn", "cuda_nvrtc")

_WINDOWS_SUBDIR = "bin"
_POSIX_SUBDIR = "lib"


def _nvidia_root() -> Path | None:
    """Locate the ``nvidia`` package directory inside site-packages."""
    candidates = [sysconfig.get_paths().get("purelib"), sysconfig.get_paths().get("platlib")]
    for entry in sys.path:
        candidates.append(entry)

    for candidate in candidates:
        if not candidate:
            continue
        root = Path(candidate) / "nvidia"
        if root.is_dir():
            return root
    return None


def library_directories() -> list[Path]:
    """Return the NVIDIA library directories present in this environment."""
    root = _nvidia_root()
    if root is None:
        return []

    subdir = _WINDOWS_SUBDIR if sys.platform == "win32" else _POSIX_SUBDIR
    found: list[Path] = []

    for component in _COMPONENTS:
        path = root / component / subdir
        if path.is_dir():
            found.append(path)

    # Pick up any component not in the known list, so a new wheel doesn't need
    # a code change here to work.
    for child in sorted(root.iterdir()):
        path = child / subdir
        if path.is_dir() and path not in found:
            found.append(path)

    return found


@lru_cache(maxsize=1)
def ensure_cuda_libraries() -> list[Path]:
    """Register pip-installed CUDA libraries with the loader.

    Idempotent and safe to call when no GPU or no NVIDIA package is present —
    it simply finds nothing and returns an empty list. Never raises: a failure
    here should degrade to CPU inference, not crash the process.
    """
    directories = library_directories()
    if not directories:
        return []

    registered: list[Path] = []

    for directory in directories:
        try:
            if sys.platform == "win32":
                os.add_dll_directory(str(directory))
            else:
                _preload_shared_objects(directory)
            registered.append(directory)
        except Exception as exc:  # pragma: no cover - platform dependent
            log.debug("Could not register %s: %s", directory, exc)

    # PATH as well: some builds shell out or dlopen by bare name, which
    # add_dll_directory does not cover.
    if sys.platform == "win32" and registered:
        existing = os.environ.get("PATH", "")
        additions = [str(d) for d in registered if str(d) not in existing]
        if additions:
            os.environ["PATH"] = os.pathsep.join([*additions, existing])

    if registered:
        log.debug("Registered %d NVIDIA library director(ies).", len(registered))

    return registered


def _preload_shared_objects(directory: Path) -> None:
    """Load every ``.so`` in a directory so the dynamic linker keeps it resident.

    On POSIX the loader reads ``LD_LIBRARY_PATH`` once at process start, so the
    only way to make these resolvable from inside a running process is to load
    them explicitly and let the linker satisfy later lookups from what is
    already mapped.
    """
    for library in sorted(directory.glob("*.so*")):
        try:
            ctypes.CDLL(str(library), mode=getattr(ctypes, "RTLD_GLOBAL", 0))
        except OSError:
            # Version-suffixed symlinks and stubs routinely fail; the ones that
            # matter succeed, and a failure here is not actionable.
            continue
