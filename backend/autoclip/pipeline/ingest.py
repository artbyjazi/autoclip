"""Ingestion — remote video URLs and local file uploads become source records.

Both paths converge on a validated :class:`~autoclip.db.models.Source` with a
file on disk and probed metadata.

Remote fetching goes through yt-dlp, so anything yt-dlp has an extractor for
works: YouTube, Twitch VODs and clips, Kick, Vimeo, X, and several hundred
others. We only *label* the two we tune error messages for.

Two site-specific notes, both about being blocked rather than about the
download itself:

* **YouTube** — as of 2026 most anonymous downloads hit a bot check, and
  proof-of-origin tokens no longer clear it reliably. Browser cookies do.
* **Twitch** — sub-only VODs and some region-locked content need the cookies of
  an account that can watch them.

So :class:`IngestError` distinguishes those failures and tells the user how to
fix them, rather than surfacing a raw yt-dlp traceback.

Twitch VODs routinely run eight hours. Downloading one in full to clip ninety
seconds out of the middle is a waste of bandwidth and, worse, of transcription
time — so :func:`ingest_url` accepts a time range and hands it to yt-dlp, which
fetches only the overlapping segments. A ``?t=`` timestamp in the URL seeds the
start automatically.
"""

from __future__ import annotations

import logging
import re
import shutil
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import paths
from ..config import IngestSettings
from ..db.models import Source, new_id
from . import ffmpeg

log = logging.getLogger(__name__)

#: Container and audio formats we accept for upload.
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
ACCEPTED_SUFFIXES = VIDEO_SUFFIXES | AUDIO_SUFFIXES

_YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com")
_TWITCH_HOSTS = ("twitch.tv", "clips.twitch.tv")

#: yt-dlp error fragments that mean "the site wants proof you're a human", or
#: that the account we presented can't see this content.
_BOT_CHECK_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "this content isn't available",
    "player response",
)

_SUBSCRIBER_MARKERS = (
    "subscriber-only",
    "subscriber only",
    "this video is only available to",
)

#: Timestamp fragments Twitch and YouTube put in share links: ``1h2m3s``,
#: ``90m``, or a bare seconds count.
_TIMESTAMP = re.compile(
    r"^(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s?)?$", re.IGNORECASE
)


class IngestError(RuntimeError):
    """Ingestion failed in a way worth explaining to the user."""

    def __init__(self, message: str, *, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint

    def __str__(self) -> str:
        base = super().__str__()
        return f"{base}\n\n{self.hint}" if self.hint else base


# --------------------------------------------------------------------------
# URL classification
# --------------------------------------------------------------------------


def _hostname(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host.removeprefix("www.").removeprefix("m.")


def _host_matches(url: str, domains: tuple[str, ...]) -> bool:
    host = _hostname(url)
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def is_youtube_url(url: str) -> bool:
    return _host_matches(url, _YOUTUBE_HOSTS)


def is_twitch_url(url: str) -> bool:
    return _host_matches(url, _TWITCH_HOSTS)


def is_remote_url(url: str) -> bool:
    """True if this looks like an http(s) URL we should hand to yt-dlp.

    Deliberately permissive: yt-dlp supports hundreds of sites and keeps adding
    more, so gatekeeping on a host allowlist would reject working links. A site
    yt-dlp can't handle fails at download time with its own clear message.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def source_type_for(url: str) -> str:
    """The stored ``Source.type`` for a remote URL."""
    if is_youtube_url(url):
        return "youtube"
    if is_twitch_url(url):
        return "twitch"
    return "url"


def slugify(text: str, *, max_length: int = 60) -> str:
    """Turn a title into a filesystem- and URL-safe slug."""
    slug = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE).strip().lower()
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug[:max_length] or "clip"


def parse_timestamp(value: str) -> float | None:
    """Parse ``1h2m3s``, ``90m``, ``3600`` or ``01:02:03`` into seconds.

    Returns None for anything unparseable — callers treat that as "no
    timestamp given" rather than an error, since these come from URLs users
    pasted and a malformed one shouldn't block the download.
    """
    value = value.strip()
    if not value:
        return None

    if ":" in value:
        parts = value.split(":")
        if len(parts) > 3 or not all(p.strip().isdigit() for p in parts if p.strip()):
            return None
        total = 0.0
        for part in parts:
            total = total * 60 + float(part or 0)
        return total

    match = _TIMESTAMP.fullmatch(value)
    if not match or not any(match.groupdict().values()):
        return None
    hours = float(match["h"] or 0)
    minutes = float(match["m"] or 0)
    seconds = float(match["s"] or 0)
    return hours * 3600 + minutes * 60 + seconds


def timestamp_from_url(url: str) -> float | None:
    """Pull a start offset out of a share link's ``?t=`` parameter."""
    try:
        query = parse_qs(urlparse(url).query)
    except ValueError:
        return None
    values = query.get("t") or query.get("start")
    if not values:
        return None
    return parse_timestamp(values[0])


# --------------------------------------------------------------------------
# Remote download
# --------------------------------------------------------------------------


def ingest_url(
    url: str,
    settings: IngestSettings | None = None,
    *,
    start_s: float | None = None,
    end_s: float | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> Source:
    """Download a remote video and return a validated source record.

    ``start_s``/``end_s`` restrict the download to a section of the video —
    essential for multi-hour Twitch VODs. When ``start_s`` is omitted, a ``?t=``
    timestamp in the URL is used instead. Both are in seconds from the start of
    the video, and the resulting media file is *re-based to zero*: a range
    starting at 2h produces a file whose first frame is t=0, so every downstream
    stage keeps working in file-relative time.

    Preconditions:
        url points at content the user owns or has the rights to process.
    """
    settings = settings or IngestSettings()

    # Validate before importing yt-dlp: a bad URL or a backwards range is the
    # caller's mistake, and it should surface as an IngestError rather than as
    # an import of a heavy optional dependency we're about to not use.
    if not is_remote_url(url):
        raise IngestError(f"{url!r} is not a URL AutoClip can fetch.")

    if start_s is None:
        start_s = timestamp_from_url(url)
    start_s, end_s = _validate_range(start_s, end_s)

    import yt_dlp

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
    if settings.prefer_youtube_captions and is_youtube_url(url):
        options["writeautomaticsub"] = True
        options["subtitleslangs"] = ["en.*"]
        options["subtitlesformat"] = "json3"

    if start_s is not None or end_s is not None:
        options["download_ranges"] = _range_func(start_s, end_s)
        # Deliberately NOT setting force_keyframes_at_cuts. It re-encodes the
        # whole requested section — an hour of 1080p to make one boundary frame
        # exact — and buys this pipeline nothing. A keyframe-aligned cut just
        # starts a few seconds early, and every later stage reads word timings
        # measured from the delivered file, so a little extra lead-in is
        # invisible by the time clips are chosen.
        #
        # A partial download can't carry the site's own captions in sync, and
        # yt-dlp won't trim them, so don't ask for them.
        options.pop("writeautomaticsub", None)

    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            metadata = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise _translate_ytdlp_error(exc, settings, url) from exc
    except Exception as exc:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise IngestError(f"Could not download {url}: {exc}") from exc

    downloaded = _find_downloaded_file(target_dir)
    if downloaded is None:
        shutil.rmtree(target_dir, ignore_errors=True)
        raise IngestError("yt-dlp reported success but produced no media file.")

    info = _probe_and_validate(downloaded)
    metadata = metadata or {}

    # With a range, the probed duration is the excerpt's — which is what the
    # rest of the pipeline should see. Only fall back to the site's reported
    # duration when we downloaded the whole thing.
    duration = info.duration_s
    if duration <= 0 and start_s is None and end_s is None:
        duration = float(metadata.get("duration") or 0.0)

    title = metadata.get("title") or downloaded.stem
    if start_s is not None or end_s is not None:
        title = f"{title} [{_format_range(start_s, end_s)}]"

    channel = extract_channel(metadata)
    if channel is None:
        # Said out loud rather than swallowed: every clip title from this job is
        # built to lead with the creator's name, so losing it here silently
        # degrades all of them.
        log.warning(
            "No creator name in metadata for %s — clip titles will not lead with one.",
            url,
        )

    return Source(
        id=source_id,
        type=source_type_for(url),  # type: ignore[arg-type]
        url=url,
        path=str(downloaded),
        filename=downloaded.name,
        title=title,
        channel=channel,
        duration_s=duration,
        width=info.width,
        height=info.height,
        fps=info.fps,
        has_audio=info.has_audio,
        has_video=info.has_video,
    )


#: Retained so existing callers and any downstream code keep working.
ingest_youtube = ingest_url


def _validate_range(
    start_s: float | None, end_s: float | None
) -> tuple[float | None, float | None]:
    if start_s is not None and start_s < 0:
        raise IngestError("Start time cannot be negative.")
    if end_s is not None and end_s <= 0:
        raise IngestError("End time must be greater than zero.")
    if start_s is not None and end_s is not None and end_s <= start_s:
        raise IngestError(
            "End time must be after start time.",
            hint=f"Got start={_format_clock(start_s)}, end={_format_clock(end_s)}.",
        )
    # A start of exactly 0 is the same as no range and costs a needless re-encode.
    if start_s == 0 and end_s is None:
        return None, None
    return start_s, end_s


def _range_func(start_s: float | None, end_s: float | None) -> Callable:
    """Build the callback yt-dlp uses to decide which section to fetch."""

    def ranges(_info: dict, _ydl: object) -> list[dict]:
        return [
            {
                "start_time": start_s if start_s is not None else 0.0,
                "end_time": end_s if end_s is not None else float("inf"),
            }
        ]

    return ranges


def _format_clock(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _format_range(start_s: float | None, end_s: float | None) -> str:
    if start_s is not None and end_s is not None:
        return f"{_format_clock(start_s)}–{_format_clock(end_s)}"
    if start_s is not None:
        return f"from {_format_clock(start_s)}"
    return f"to {_format_clock(end_s)}"


def _translate_ytdlp_error(exc: Exception, settings: IngestSettings, url: str) -> IngestError:
    """Turn a yt-dlp failure into something the user can act on."""
    message = str(exc).lower()
    site = "Twitch" if is_twitch_url(url) else "YouTube" if is_youtube_url(url) else "This site"

    if any(marker in message for marker in _SUBSCRIBER_MARKERS):
        return IngestError(
            "This VOD is for subscribers only.",
            hint=(
                "Set a browser to pull cookies from — in Settings, or via "
                'config.json\'s `ingest.cookies_from_browser` (e.g. "chrome") — and '
                "be signed in there with an account that subscribes to the channel."
            ),
        )

    if any(marker in message for marker in _BOT_CHECK_MARKERS):
        if settings.cookies_from_browser:
            hint = (
                f"Cookies are already being read from {settings.cookies_from_browser}, but "
                f"{site} still refused. Make sure you are signed in there and that the "
                "browser is fully closed — it locks its cookie database while running."
            )
        else:
            hint = (
                f"{site} is asking for proof you're not a bot. Set a browser to pull "
                "cookies from — in Settings, or via config.json's "
                '`ingest.cookies_from_browser` (e.g. "chrome", "firefox", "edge"). '
                "You must be signed in in that browser, and it must be closed while "
                "AutoClip downloads."
            )
        return IngestError(f"{site} blocked this download with a bot check.", hint=hint)

    if "is offline" in message or ("live" in message and "not available" in message):
        return IngestError(
            "That channel is live, not a recording.",
            hint=(
                "AutoClip clips finished video. Wait for the stream to end and paste "
                "the VOD link, or paste a Twitch clip link."
            ),
        )
    if "private video" in message or "members-only" in message:
        return IngestError(
            "This video is private or members-only.",
            hint="AutoClip only downloads content you can access and have the rights to use.",
        )
    if "unavailable" in message or "removed" in message:
        return IngestError("This video is unavailable or has been removed.")
    if "unsupported url" in message or "no suitable extractor" in message:
        return IngestError(
            "yt-dlp has no extractor for that link.",
            hint=(
                "Check the URL points at a video page. If the site is newly supported, "
                "`autoclip update-ytdlp` may pick it up."
            ),
        )
    if "drm" in message:
        return IngestError(
            "This content is DRM-protected.",
            hint="AutoClip does not and will not circumvent DRM.",
        )

    return IngestError(
        "yt-dlp could not download this video.",
        hint=(
            "Sites change frequently and yt-dlp is updated often. Try "
            "`autoclip update-ytdlp` to pull the latest version.\n\n"
            f"Original error: {exc}"
        ),
    )


#: Metadata keys that can carry "who made this", best first.
#:
#: Order is from the sites themselves, not guesswork. A Twitch VOD page renders
#: the streamer into its own metadata — ``og:title`` ends "- jynxzi on Twitch",
#: ``og:description`` opens "jynxzi went live on Twitch" — and yt-dlp's Twitch
#: extractor reads that into ``uploader``. YouTube fills ``uploader`` and
#: ``channel`` from its page metadata the same way, so ``uploader`` first covers
#: both sites with one path.
#:
#: ``creator`` is a late fallback rather than a first choice: on Twitch *clips*
#: it can name the person who cut the clip rather than the streamer in it.
#: The ID fields are last — they are a login handle, not a display name, but a
#: correct handle beats no name at all.
_CHANNEL_KEYS = ("uploader", "channel", "creator", "uploader_id", "channel_id")


def extract_channel(metadata: dict) -> str | None:
    """Pull the creator's display name out of yt-dlp metadata.

    Returns None when the extractor gave us nothing usable. Callers must treat
    that as "no name" rather than substituting a placeholder — a clip titled
    "Unknown flips out at chat" is worse than one with no name at all.
    """
    for key in _CHANNEL_KEYS:
        value = metadata.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


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
