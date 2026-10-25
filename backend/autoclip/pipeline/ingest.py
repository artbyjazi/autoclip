"""Ingestion — YouTube and Twitch URLs and local file uploads become source records.

Both paths converge on a validated :class:`~autoclip.db.models.Source` with a
file on disk and probed metadata.

A note on YouTube: as of 2026 most anonymous downloads hit a bot check, and
proof-of-origin tokens no longer clear it reliably — browser cookies do. So
:class:`IngestError` distinguishes that specific failure and tells the user how
to fix it, rather than surfacing a raw yt-dlp traceback.
"""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from .. import paths
from ..config import IngestSettings
from ..db.models import Source, new_id
from . import ffmpeg

log = logging.getLogger(__name__)

#: Container and audio formats we accept for upload.
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
ACCEPTED_SUFFIXES = VIDEO_SUFFIXES | AUDIO_SUFFIXES

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com")
#: VODs and highlights live on twitch.tv; clips also on clips.twitch.tv.
_TWITCH_HOSTS = ("twitch.tv", "www.twitch.tv", "m.twitch.tv", "clips.twitch.tv")

#: yt-dlp error fragments that mean "YouTube wants proof you're a human".
_BOT_CHECK_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "this content isn't available",
    "player response",
)


class IngestError(RuntimeError):
    """Ingestion failed in a way worth explaining to the user."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base}\n\n{self.hint}" if self.hint else base


def _hostname(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_youtube_url(url: str) -> bool:
    return _hostname(url) in _YOUTUBE_HOSTS


def is_twitch_url(url: str) -> bool:
    return _hostname(url) in _TWITCH_HOSTS


def is_supported_url(url: str) -> bool:
    """True for any URL :func:`ingest_url` can download."""
    return is_youtube_url(url) or is_twitch_url(url)


def ingest_url(
    url: str,
    settings: IngestSettings | None = None,
    *,
    on_progress: Callable[[float], None] | None = None,
) -> Source:
    """Download a YouTube or Twitch URL and return a validated source record."""
    if is_twitch_url(url):
        return ingest_twitch(url, settings, on_progress=on_progress)
    if is_youtube_url(url):
        return ingest_youtube(url, settings, on_progress=on_progress)
    raise IngestError(
        "That is not a YouTube or Twitch URL.",
        hint="Paste a youtube.com, youtu.be, twitch.tv or clips.twitch.tv link, or upload a file.",
    )


def slugify(text: str, *, max_length: int = 60) -> str:
    """Turn a title into a filesystem- and URL-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug[:max_length] or "clip"


# --------------------------------------------------------------------------
# YouTube
# --------------------------------------------------------------------------


def ingest_youtube(
    url: str,
    settings: IngestSettings | None = None,
    *,
    on_progress: Callable[[float], None] | None = None,
) -> Source:
    """Download a YouTube video and return a validated source record.

    Preconditions:
        url points at content the user owns or has the rights to process.
    """
    return _ingest_with_ytdlp(url, settings, source_type="youtube", on_progress=on_progress)


# --------------------------------------------------------------------------
# Twitch
# --------------------------------------------------------------------------


def ingest_twitch(
    url: str,
    settings: IngestSettings | None = None,
    *,
    on_progress: Callable[[float], None] | None = None,
) -> Source:
    """Download a Twitch VOD, highlight or clip and return a validated source record.

    Twitch serves HLS with audio and video already muxed, so the configured
    format's ``best[height<=1080]`` fallback is what matches. Live channels are
    refused — a stream with no end can't be clipped.

    Preconditions:
        url points at content the user owns or has the rights to process.
    """
    return _ingest_with_ytdlp(url, settings, source_type="twitch", on_progress=on_progress)


def _ingest_with_ytdlp(
    url: str,
    settings: IngestSettings | None,
    *,
    source_type: Literal["youtube", "twitch"],
    on_progress: Callable[[float], None] | None,
) -> Source:
    import yt_dlp

    settings = settings or IngestSettings()
    source_id = new_id()
    target_dir = paths.source_media_dir(source_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    def hook(status: dict) -> None:
        if not on_progress or status.get("status") != "downloading":
            return
        total = status.get("total_bytes") or status.get("total_bytes_estimate")
        done = status.get("downloaded_bytes")
        if total and done:
            on_progress(min(1.0, done / total))
            return
        # HLS (all of Twitch) often has no byte total, but always counts fragments.
        index, count = status.get("fragment_index"), status.get("fragment_count")
        if index and count:
            on_progress(min(1.0, index / count))

    options: dict = {
        "format": settings.ytdlp_format,
        "outtmpl": str(target_dir / "source.%(ext)s"),
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "progress_hooks": [hook],
        "retries": 3,
        "fragment_retries": 3,
    }
    if settings.cookies_from_browser:
        # yt-dlp expects a tuple; only the browser name is required.
        options["cookiesfrombrowser"] = (settings.cookies_from_browser,)
    if source_type == "youtube" and settings.prefer_youtube_captions:
        options["writeautomaticsub"] = True
        options["subtitleslangs"] = ["en.*"]
        options["subtitlesformat"] = "json3"

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            if source_type == "twitch":
                # Resolve first so a live channel is refused before yt-dlp
                # starts recording a stream that never finishes.
                resolved = ydl.extract_info(url, download=False)
                if (resolved or {}).get("is_live"):
                    raise IngestError(
                        "This Twitch channel is live right now.",
                        hint=(
                            "AutoClip clips finished videos. Wait for the stream to end and "
                            "paste the VOD link (twitch.tv/videos/…) instead."
                        ),
                    )
                metadata = ydl.process_ie_result(resolved, download=True)
            else:
                metadata = ydl.extract_info(url, download=True)
    except IngestError:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise
    except yt_dlp.utils.DownloadError as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        if source_type == "twitch":
            raise _translate_twitch_error(exc, settings) from exc
        raise _translate_ytdlp_error(exc, settings) from exc
    except Exception as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise IngestError(f"Could not download {url}: {exc}") from exc

    downloaded = _find_downloaded_file(target_dir)
    if downloaded is None:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise IngestError("yt-dlp reported success but produced no media file.")

    info = _probe_and_validate(downloaded)

    return Source(
        id=source_id,
        type=source_type,
        url=url,
        path=str(downloaded),
        filename=downloaded.name,
        title=(metadata or {}).get("title") or downloaded.stem,
        channel=(metadata or {}).get("uploader") or (metadata or {}).get("channel"),
        duration_s=info.duration_s or float((metadata or {}).get("duration") or 0.0),
        width=info.width,
        height=info.height,
        fps=info.fps,
        has_audio=info.has_audio,
        has_video=info.has_video,
    )


def _translate_ytdlp_error(exc: Exception, settings: IngestSettings) -> IngestError:
    """Turn a yt-dlp failure into something the user can act on."""
    message = str(exc).lower()

    if any(marker in message for marker in _BOT_CHECK_MARKERS):
        if settings.cookies_from_browser:
            hint = (
                f"Cookies are already being read from {settings.cookies_from_browser}, but "
                "YouTube still refused. Make sure you are signed in to YouTube in that "
                "browser and that the browser is fully closed — it locks its cookie "
                "database while running."
            )
        else:
            hint = (
                "YouTube is asking for proof you're not a bot. Set a browser to pull "
                "cookies from — in Settings, or via config.json's "
                '`ingest.cookies_from_browser` (e.g. "chrome", "firefox", "edge"). '
                "You must be signed in to YouTube in that browser, and it must be closed "
                "while AutoClip downloads."
            )
        return IngestError("YouTube blocked this download with a bot check.", hint=hint)

    if "private video" in message or "members-only" in message:
        return IngestError(
            "This video is private or members-only.",
            hint="AutoClip only downloads content you can access and have the rights to use.",
        )
    if "unavailable" in message or "removed" in message:
        return IngestError("This video is unavailable or has been removed.")
    if "drm" in message:
        return IngestError(
            "This content is DRM-protected.",
            hint="AutoClip does not and will not circumvent DRM.",
        )

    return IngestError(
        "yt-dlp could not download this video.",
        hint=(
            "YouTube changes frequently and yt-dlp is updated often. Try "
            "`autoclip update-ytdlp` to pull the latest version.\n\n"
            f"Original error: {exc}"
        ),
    )


def _translate_twitch_error(exc: Exception, settings: IngestSettings) -> IngestError:
    """Turn a yt-dlp failure on a Twitch URL into something the user can act on."""
    message = str(exc).lower()

    if "not currently live" in message:
        return IngestError(
            "That is a Twitch channel link, and the channel isn't live.",
            hint="Paste a VOD (twitch.tv/videos/…) or clip (clips.twitch.tv/…) link instead.",
        )
    if "subscriber" in message or "sub-only" in message or "login" in message:
        if settings.cookies_from_browser:
            hint = (
                f"Cookies are already being read from {settings.cookies_from_browser}. Make "
                "sure you are signed in to a Twitch account subscribed to this channel, and "
                "that the browser is fully closed while AutoClip downloads."
            )
        else:
            hint = (
                "Set a browser to pull cookies from — in Settings, or via config.json's "
                '`ingest.cookies_from_browser` (e.g. "chrome", "firefox"). You must be '
                "signed in to Twitch there, and the browser must be closed while AutoClip "
                "downloads."
            )
        return IngestError("This Twitch video is subscriber-only.", hint=hint)
    if "does not exist" in message or "unavailable" in message or "404" in message:
        return IngestError(
            "This Twitch video is unavailable.",
            hint="Twitch VODs expire after a while, and channels can delete them.",
        )

    return IngestError(
        "yt-dlp could not download this Twitch video.",
        hint=(f"Try `autoclip update-ytdlp` to pull the latest version.\n\nOriginal error: {exc}"),
    )


def _find_downloaded_file(directory: Path) -> Path | None:
    """Return the largest media file in a directory, ignoring sidecars."""
    candidates = [
        p for p in directory.iterdir() if p.is_file() and p.suffix.lower() in ACCEPTED_SUFFIXES
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_size)


# --------------------------------------------------------------------------
# Upload
# --------------------------------------------------------------------------


def ingest_file(path: Path, *, move: bool = False, title: str | None = None) -> Source:
    """Register a local file as a source, copying it into AutoClip's media store.

    Copying rather than referencing in place means a job stays reproducible even
    if the user moves or deletes the original.

    Preconditions:
        path exists and is a readable media file.
    """
    path = Path(path)
    if not path.exists():
        raise IngestError(f"{path} does not exist.")
    if not path.is_file():
        raise IngestError(f"{path} is not a file.")

    suffix = path.suffix.lower()
    if suffix not in ACCEPTED_SUFFIXES:
        accepted = ", ".join(sorted(ACCEPTED_SUFFIXES))
        raise IngestError(
            f"{suffix or 'This file'} is not a supported format.",
            hint=f"Accepted formats: {accepted}",
        )

    source_id = new_id()
    target_dir = paths.source_media_dir(source_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"source{suffix}"

    try:
        if move:
            shutil.move(str(path), target)
        else:
            shutil.copy2(path, target)
    except OSError as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise IngestError(f"Could not store {path.name}: {exc}") from exc

    try:
        info = _probe_and_validate(target)
    except Exception:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise

    return Source(
        id=source_id,
        type="upload",
        path=str(target),
        filename=path.name,
        title=title or info.title or path.stem,
        duration_s=info.duration_s,
        width=info.width,
        height=info.height,
        fps=info.fps,
        has_audio=info.has_audio,
        has_video=info.has_video,
    )


def _probe_and_validate(path: Path) -> ffmpeg.MediaInfo:
    """Probe a media file and reject anything the pipeline can't process."""
    try:
        info = ffmpeg.probe(path)
    except ffmpeg.FFmpegError as exc:
        raise IngestError(f"{path.name} could not be read as media.", hint=str(exc)) from exc

    if not info.has_audio:
        raise IngestError(
            f"{path.name} has no audio track.",
            hint=(
                "AutoClip finds clips by transcribing speech, so a file with no audio "
                "has nothing to work from."
            ),
        )
    if info.duration_s <= 0:
        raise IngestError(
            f"{path.name} reports zero duration.",
            hint="The file may be corrupt or still being written.",
        )

    return info
