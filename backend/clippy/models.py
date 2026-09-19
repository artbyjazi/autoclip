"""On-demand download and caching of ML model bundles.

MediaPipe's task bundles are a few megabytes each. Fetching them on first use
into ``~/.clippy/models/`` keeps them out of the repository and out of the
wheel, which is the same thing Whisper does with its weights — users expect it,
and it keeps ``pip install clippy`` small.
"""

from __future__ import annotations

import logging
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelSpec:
    key: str
    filename: str
    url: str
    description: str
    approx_bytes: int


MODELS: dict[str, ModelSpec] = {
    "face_landmarker": ModelSpec(
        key="face_landmarker",
        filename="face_landmarker.task",
        url=(
            "https://storage.googleapis.com/mediapipe-models/face_landmarker/"
            "face_landmarker/float16/1/face_landmarker.task"
        ),
        description="MediaPipe face landmarks — face tracking and mouth movement",
        approx_bytes=3_758_596,
    ),
}


class ModelDownloadError(RuntimeError):
    """A model bundle could not be fetched."""


def models_dir() -> Path:
    return paths.root() / "models"


def model_path(key: str) -> Path:
    spec = _spec(key)
    return models_dir() / spec.filename


def is_available(key: str) -> bool:
    path = model_path(key)
    return path.exists() and path.stat().st_size > 0


def ensure(key: str, *, timeout: float = 120.0) -> Path:
    """Return a local path to the model, downloading it if absent.

    Preconditions:
        The process can write to the Clippy home directory.
    """
    spec = _spec(key)
    destination = model_path(key)
    if destination.exists() and destination.stat().st_size > 0:
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    log.info("Downloading %s (~%.1f MB)...", spec.filename, spec.approx_bytes / 1_048_576)

    # Download to a temp file and move into place, so an interrupted fetch can
    # never leave a truncated model that then fails cryptically at inference.
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        with (
            urllib.request.urlopen(spec.url, timeout=timeout) as response,  # noqa: S310
            temporary.open("wb") as handle,
        ):
            shutil.copyfileobj(response, handle)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        temporary.unlink(missing_ok=True)
        raise ModelDownloadError(
            f"Could not download the {spec.key} model.\n"
            f"URL: {spec.url}\n"
            f"Error: {exc}\n\n"
            f"If this machine has no internet access, download the file manually "
            f"and place it at {destination}."
        ) from exc

    if temporary.stat().st_size == 0:
        temporary.unlink(missing_ok=True)
        raise ModelDownloadError(f"The downloaded {spec.key} model was empty.")

    temporary.replace(destination)
    log.info("Saved %s", destination)
    return destination


def _spec(key: str) -> ModelSpec:
    spec = MODELS.get(key)
    if spec is None:
        raise ModelDownloadError(f"Unknown model {key!r}. Available: {', '.join(MODELS)}")
    return spec
