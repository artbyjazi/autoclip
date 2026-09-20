"""Environment detection logic.

The probes themselves shell out to real binaries, so these tests target the
decision logic layered on top — which is where a wrong answer would silently
cost users performance or produce a broken render.
"""

from __future__ import annotations

import pytest
from clippy.system import REQUIRED_FILTERS, FFmpegInfo, GPUInfo

#: What CTranslate2 actually reports on a Pascal card (verified on a GTX 1060).
#: Note the complete absence of any fp16 type, despite CUDA advertising fp16
#: capability for compute 6.1.
PASCAL_SUPPORT = frozenset({"float32", "int8", "int8_float32"})

#: A modern card with tensor cores.
TURING_SUPPORT = frozenset(
    {"float32", "float16", "int8", "int8_float16", "int8_float32", "bfloat16"}
)

CPU_SUPPORT = frozenset({"float32", "int16", "int8", "int8_float32"})


class TestComputeTypeSelection:
    """Selection consults CTranslate2's reported support, not compute capability.

    Inferring from capability produced a real bug: a GTX 1060 reports fp16 to
    CUDA, so int8_float16 looked safe, and every transcription died at model
    load with an error that pointed at cuDNN instead of at the compute type.
    """

    def test_pascal_avoids_every_fp16_type(self) -> None:
        gpu = GPUInfo(accel="cuda", compute_capability=6.1, supported_compute_types=PASCAL_SUPPORT)

        assert gpu.compute_type == "int8_float32"
        assert "float16" not in gpu.compute_type

    def test_turing_prefers_float16(self) -> None:
        gpu = GPUInfo(accel="cuda", compute_capability=7.5, supported_compute_types=TURING_SUPPORT)

        assert gpu.compute_type == "float16"

    @pytest.mark.parametrize("capability", [8.6, 8.9, 12.0])
    def test_modern_cards_prefer_float16(self, capability: float) -> None:
        gpu = GPUInfo(
            accel="cuda", compute_capability=capability, supported_compute_types=TURING_SUPPORT
        )

        assert gpu.compute_type == "float16"

    def test_never_picks_an_unsupported_type(self) -> None:
        # The invariant that matters: whatever is chosen must be in the set the
        # backend said it supports.
        for capability, support in (
            (6.1, PASCAL_SUPPORT),
            (7.5, TURING_SUPPORT),
            (None, PASCAL_SUPPORT),
        ):
            gpu = GPUInfo(
                accel="cuda", compute_capability=capability, supported_compute_types=support
            )
            assert gpu.compute_type in support

    def test_capability_below_volta_is_respected_even_if_fp16_is_offered(self) -> None:
        # Belt and braces: if a driver ever did offer fp16 on Pascal, we still
        # decline it, because it runs at 1/64 rate there.
        gpu = GPUInfo(accel="cuda", compute_capability=6.1, supported_compute_types=TURING_SUPPORT)

        assert gpu.compute_type == "int8_float32"

    def test_unknown_capability_uses_what_is_supported(self) -> None:
        gpu = GPUInfo(accel="cuda", compute_capability=None, supported_compute_types=PASCAL_SUPPORT)

        assert gpu.compute_type in PASCAL_SUPPORT

    def test_falls_back_safely_when_ctranslate2_cannot_be_queried(self) -> None:
        # An empty support set means the probe failed; the choice must still be
        # something universally available.
        gpu = GPUInfo(accel="cuda", compute_capability=6.1, supported_compute_types=frozenset())

        assert gpu.compute_type in ("int8", "float32")

    def test_apple_silicon_uses_the_cpu_path(self) -> None:
        gpu = GPUInfo(accel="mps", supported_compute_types=CPU_SUPPORT)

        assert gpu.compute_type == "int8"
        # CTranslate2 has no Metal backend, so the device string must stay "cpu".
        assert gpu.device == "cpu"

    def test_cpu_prefers_int8(self) -> None:
        gpu = GPUInfo(accel="cpu", supported_compute_types=CPU_SUPPORT)

        assert gpu.compute_type == "int8"
        assert gpu.device == "cpu"

    def test_cuda_device_string(self) -> None:
        assert GPUInfo(accel="cuda").device == "cuda"


class TestComputeTypeOverride:
    """A forced compute type is honoured only when the device supports it."""

    def _settings(self, compute_type: str):
        from clippy.config import WhisperSettings

        return WhisperSettings(compute_type=compute_type)

    def test_supported_override_is_honoured(self) -> None:
        from clippy.pipeline.transcribe import resolve_compute

        gpu = GPUInfo(accel="cuda", compute_capability=6.1, supported_compute_types=PASCAL_SUPPORT)

        assert resolve_compute(self._settings("int8"), gpu) == ("cuda", "int8")

    def test_unsupported_override_is_ignored(self) -> None:
        # Honouring it would fail at model load with a message blaming cuDNN,
        # which sends the user chasing the wrong problem entirely.
        from clippy.pipeline.transcribe import resolve_compute

        gpu = GPUInfo(accel="cuda", compute_capability=6.1, supported_compute_types=PASCAL_SUPPORT)

        device, compute_type = resolve_compute(self._settings("float16"), gpu)

        assert device == "cuda"
        assert compute_type == "int8_float32"

    def test_empty_override_uses_automatic_selection(self) -> None:
        from clippy.pipeline.transcribe import resolve_compute

        gpu = GPUInfo(accel="cuda", compute_capability=7.5, supported_compute_types=TURING_SUPPORT)

        assert resolve_compute(self._settings(""), gpu) == ("cuda", "float16")

    def test_override_is_trusted_when_support_is_unknown(self) -> None:
        # No probe result means no basis to overrule the user.
        from clippy.pipeline.transcribe import resolve_compute

        gpu = GPUInfo(accel="cuda", supported_compute_types=frozenset())

        assert resolve_compute(self._settings("float16"), gpu) == ("cuda", "float16")


class TestFFmpegUsability:
    def _complete(self, **overrides) -> FFmpegInfo:
        defaults = {
            "found": True,
            "ffprobe_found": True,
            "has_libass": True,
            "has_libx264": True,
            "filters": set(REQUIRED_FILTERS),
        }
        return FFmpegInfo(**{**defaults, **overrides})

    def test_complete_build_is_usable(self) -> None:
        assert self._complete().usable is True

    def test_missing_filters_are_reported(self) -> None:
        info = self._complete(filters={"crop", "scale"})

        assert set(info.missing_filters) == {"ass", "concat", "loudnorm"}
        assert info.usable is False

    @pytest.mark.parametrize(
        "missing",
        ["found", "ffprobe_found", "has_libass", "has_libx264"],
    )
    def test_each_requirement_is_load_bearing(self, missing: str) -> None:
        assert self._complete(**{missing: False}).usable is False

    def test_nvenc_is_optional(self) -> None:
        # Software encode is a valid fallback, so a build without nvenc is fine.
        assert self._complete(has_nvenc=False).usable is True

    def test_not_found_reports_all_filters_missing(self) -> None:
        info = FFmpegInfo()

        assert set(info.missing_filters) == set(REQUIRED_FILTERS)
        assert info.usable is False
