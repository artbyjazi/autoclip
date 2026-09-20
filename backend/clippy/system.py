"""Runtime environment detection.

Answers "what can this machine actually do?" — which ffmpeg features are
compiled in, whether there's a usable GPU, which CTranslate2 compute type suits
it, and whether the optional extras are installed.

Every probe here is defensive: a missing tool is a reported finding, never an
exception. ``clippy doctor`` renders this report, and the pipeline reads it
to choose encoders and compute types.
"""

from __future__ import annotations

import contextlib
import logging
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Literal

log = logging.getLogger(__name__)

#: ffmpeg filters the pipeline cannot run without.
REQUIRED_FILTERS = ("crop", "scale", "ass", "concat", "loudnorm")
#: Filters we use when present but can work around.
OPTIONAL_FILTERS = ("sendcmd", "zoompan", "silencedetect")

Accel = Literal["cuda", "mps", "cpu"]
ComputeType = str

#: Below this CUDA compute capability there is no usable fp16 path at all —
#: not just no tensor cores. Pascal (GTX 10-series, 6.1) reports fp16 support in
#: CUDA but runs it at 1/64 rate, and CTranslate2 declines to use it. 7.0 is
#: Volta, the first architecture where fp16 is genuinely fast.
FP16_MIN_COMPUTE_CAPABILITY = 7.0

#: Compute types in descending order of preference, filtered against what
#: CTranslate2 actually reports for the device. float32 is the universal
#: fallback and is always supported.
CUDA_FP16_PREFERENCE = ("float16", "int8_float16", "int8_float32", "int8", "float32")
CUDA_NO_FP16_PREFERENCE = ("int8_float32", "int8", "float32")
CPU_PREFERENCE = ("int8", "int8_float32", "float32")

#: Used when CTranslate2 cannot be queried at all.
_FALLBACK_COMPUTE_TYPES = frozenset({"float32", "int8"})

_TIMEOUT_S = 20


def _run(args: list[str]) -> str:
    """Run a command and return combined output, or "" if it can't be run."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
            check=False,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (proc.stdout or "") + (proc.stderr or "")


@dataclass
class FFmpegInfo:
    found: bool = False
    path: str | None = None
    version: str | None = None
    has_libass: bool = False
    has_fontconfig: bool = False
    has_libx264: bool = False
    #: h264_nvenc is listed in this build's encoders.
    has_nvenc: bool = False
    #: h264_nvenc actually encodes on this machine. Being listed is not enough:
    #: the build's required NVENC API version can exceed what the installed
    #: driver provides, and the failure only appears at encode time.
    nvenc_works: bool = False
    #: Why nvenc is unusable, when it is listed but doesn't work.
    nvenc_error: str = ""
    filters: set[str] = field(default_factory=set)
    ffprobe_found: bool = False

    @property
    def missing_filters(self) -> list[str]:
        return [f for f in REQUIRED_FILTERS if f not in self.filters]

    @property
    def usable(self) -> bool:
        """True when ffmpeg can render a captioned clip end to end."""
        return (
            self.found
            and self.ffprobe_found
            and self.has_libass
            and self.has_libx264
            and not self.missing_filters
        )


@dataclass
class GPUInfo:
    accel: Accel = "cpu"
    name: str | None = None
    vram_mb: int | None = None
    compute_capability: float | None = None
    driver_version: str | None = None
    #: True when CTranslate2 reports a usable CUDA device — the check that
    #: actually predicts whether faster-whisper will run on GPU. nvidia-smi
    #: seeing a card is not sufficient (missing cuDNN is the usual culprit).
    ctranslate2_cuda: bool = False

    #: Compute types CTranslate2 reports for this device. Populated by the
    #: probe; empty only when CTranslate2 could not be queried.
    supported_compute_types: frozenset[str] = field(default_factory=frozenset)

    @property
    def compute_type(self) -> ComputeType:
        """Pick the best compute type this device actually supports.

        Asks CTranslate2 rather than inferring from compute capability. The two
        disagree: a GTX 1060 reports fp16 capability to CUDA, but CTranslate2
        supports only float32, int8, and int8_float32 on it — so anything fp16
        fails at model load with an unhelpful message.
        """
        supported = self.supported_compute_types or _FALLBACK_COMPUTE_TYPES

        if self.accel == "cuda":
            cc = self.compute_capability
            no_fp16 = cc is not None and cc < FP16_MIN_COMPUTE_CAPABILITY
            preference = CUDA_NO_FP16_PREFERENCE if no_fp16 else CUDA_FP16_PREFERENCE
        else:
            # CTranslate2 has no Metal backend, so Apple Silicon takes the CPU
            # path too — its NEON int8 kernels handle it well.
            preference = CPU_PREFERENCE

        for candidate in preference:
            if candidate in supported:
                return candidate
        return "float32"

    @property
    def device(self) -> str:
        """The device string to hand to faster-whisper."""
        return "cuda" if self.accel == "cuda" else "cpu"


@dataclass
class OptionalDeps:
    mediapipe: bool = False
    scenedetect: bool = False
    whisperx: bool = False
    faster_whisper: bool = False
    keyring_backend: bool = False
    ollama_running: bool = False
    ollama_models: list[str] = field(default_factory=list)


@dataclass
class SystemReport:
    python_version: str
    python_ok: bool
    platform: str
    ffmpeg: FFmpegInfo
    gpu: GPUInfo
    deps: OptionalDeps

    @property
    def ready(self) -> bool:
        """True when the core pipeline can run (GPU and extras are optional)."""
        return (
            self.python_ok
            and self.ffmpeg.usable
            and self.deps.faster_whisper
            and self.deps.mediapipe
            and self.deps.scenedetect
        )


# --------------------------------------------------------------------------
# Probes
# --------------------------------------------------------------------------


def probe_ffmpeg() -> FFmpegInfo:
    info = FFmpegInfo()
    exe = shutil.which("ffmpeg")
    info.ffprobe_found = shutil.which("ffprobe") is not None
    if not exe:
        return info

    info.found = True
    info.path = exe

    banner = _run([exe, "-hide_banner", "-version"])
    if match := re.search(r"ffmpeg version (\S+)", banner):
        info.version = match.group(1)
    info.has_libass = "--enable-libass" in banner or "libass" in banner
    info.has_fontconfig = "fontconfig" in banner
    info.has_libx264 = "libx264" in banner

    encoders = _run([exe, "-hide_banner", "-encoders"])
    info.has_nvenc = "h264_nvenc" in encoders
    if info.has_nvenc:
        info.nvenc_works, info.nvenc_error = _probe_nvenc(exe)

    filters = _run([exe, "-hide_banner", "-filters"])
    # Filter listing rows look like: " ... crop   V->V   Crop the input video."
    # Match the name column specifically so substrings don't produce false hits.
    for line in filters.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r"[A-Za-z0-9_]+", parts[1]):
            info.filters.add(parts[1])

    return info


def _probe_nvenc(exe: str) -> tuple[bool, str]:
    """Try a one-frame NVENC encode to null. Returns (works, error summary).

    The cheapest honest test. Checking the encoder list only tells us the build
    has the code, not that the driver supports the API version it was built
    against — that mismatch is common and only surfaces mid-render.
    """
    output = _run(
        [
            exe,
            "-hide_banner",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=256x256:d=0.1",
            "-c:v",
            "h264_nvenc",
            "-frames:v",
            "1",
            "-f",
            "null",
            "-",
        ]
    )
    lowered = output.lower()
    if "error" in lowered or "cannot load" in lowered or "not support" in lowered:
        for line in output.splitlines():
            if "nvenc" in line.lower() and ("driver" in line.lower() or "requir" in line.lower()):
                return False, line.strip()
        return False, "NVENC initialisation failed."
    return True, ""


def probe_gpu() -> GPUInfo:
    info = GPUInfo()

    if sys.platform == "darwin" and platform.machine() == "arm64":
        info.accel = "mps"
        info.name = platform.processor() or "Apple Silicon"
        return info

    if shutil.which("nvidia-smi"):
        out = _run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,driver_version,compute_cap",
                "--format=csv,noheader,nounits",
            ]
        )
        first = next((ln for ln in out.splitlines() if ln.strip()), "")
        fields = [f.strip() for f in first.split(",")]
        if len(fields) >= 3:
            info.name = fields[0] or None
            # Older drivers omit or blank out individual columns; each is
            # independently optional rather than all-or-nothing.
            with contextlib.suppress(ValueError, IndexError):
                info.vram_mb = int(float(fields[1]))
            info.driver_version = fields[2] or None
            if len(fields) >= 4:
                with contextlib.suppress(ValueError):
                    info.compute_capability = float(fields[3])

    # nvidia-smi seeing a card doesn't mean CTranslate2 can use it — the CUDA
    # runtime and cuDNN also have to be loadable. This is the check that counts.
    # Registering the pip-installed library directories first is what makes them
    # findable; without it the probe passes and inference later fails.
    from .cuda import ensure_cuda_libraries

    ensure_cuda_libraries()

    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            info.ctranslate2_cuda = True
            info.accel = "cuda"
    except Exception:
        pass

    info.supported_compute_types = query_compute_types(info.device)
    return info


def query_compute_types(device: str) -> frozenset[str]:
    """Ask CTranslate2 which compute types work on ``device``.

    The authoritative answer, and frequently narrower than CUDA's own
    capability reporting suggests. Returns an empty set if CTranslate2 cannot
    be queried, which callers treat as "fall back to the safe defaults".
    """
    try:
        import ctranslate2

        return frozenset(ctranslate2.get_supported_compute_types(device))
    except Exception as exc:
        log.debug("Could not query CTranslate2 compute types for %s: %s", device, exc)
        return frozenset()


def probe_optional_deps() -> OptionalDeps:
    deps = OptionalDeps()

    for attr, module in (
        ("mediapipe", "mediapipe"),
        ("scenedetect", "scenedetect"),
        ("whisperx", "whisperx"),
        ("faster_whisper", "faster_whisper"),
    ):
        setattr(deps, attr, _module_available(module))

    from .config import _keyring

    deps.keyring_backend = _keyring() is not None

    deps.ollama_running, deps.ollama_models = _probe_ollama()
    return deps


def _module_available(name: str) -> bool:
    """Check importability without paying the cost of importing."""
    import importlib.util

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _probe_ollama(base_url: str = "http://localhost:11434") -> tuple[bool, list[str]]:
    """Return (running, model names). Never raises."""
    try:
        import httpx

        resp = httpx.get(f"{base_url}/api/tags", timeout=2.0)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        return True, [m for m in models if m]
    except Exception:
        return False, []


def _platform_string() -> str:
    """Human-readable OS description.

    ``platform.release()`` still reports "10" on Windows 11, so we append the
    build number — it's the only reliable way to tell the two apart.
    """
    system_name = platform.system()
    release = platform.release()
    if system_name == "Windows":
        release = f"{release} (build {platform.version()})"
    return f"{system_name} {release} ({platform.machine()})"


@lru_cache(maxsize=1)
def report() -> SystemReport:
    """Build the full system report. Cached — call :func:`refresh` to re-probe."""
    version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    python_ok = (3, 11) <= sys.version_info[:2] < (3, 13)
    return SystemReport(
        python_version=version,
        python_ok=python_ok,
        platform=_platform_string(),
        ffmpeg=probe_ffmpeg(),
        gpu=probe_gpu(),
        deps=probe_optional_deps(),
    )


def refresh() -> SystemReport:
    """Discard the cached report and probe again."""
    report.cache_clear()
    return report()
