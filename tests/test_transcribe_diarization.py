"""Diarization plumbing — WhisperX auth keyword and CPU fallback detection."""

from __future__ import annotations

from autoclip.pipeline.transcribe import _diarization_auth_kwargs, _is_accelerator_error


class _TokenPipeline:
    def __init__(self, token: str | None = None, device: str = "cpu") -> None:
        self.token = token
        self.device = device


class _LegacyPipeline:
    def __init__(self, use_auth_token: str | None = None, device: str = "cpu") -> None:
        self.token = use_auth_token
        self.device = device


class _UnknownPipeline:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class TestAuthKeyword:
    def test_uses_token_for_whisperx_3_8_and_pyannote_4(self) -> None:
        assert _diarization_auth_kwargs(_TokenPipeline, "hf_test") == {"token": "hf_test"}

    def test_uses_use_auth_token_for_older_whisperx(self) -> None:
        assert _diarization_auth_kwargs(_LegacyPipeline, "hf_test") == {"use_auth_token": "hf_test"}

    def test_falls_back_to_token_when_signature_has_no_auth_keyword(self) -> None:
        assert _diarization_auth_kwargs(_UnknownPipeline, "hf_test") == {"token": "hf_test"}


class TestAcceleratorError:
    def test_cuda_out_of_memory_is_retryable(self) -> None:
        assert _is_accelerator_error(RuntimeError("CUDA out of memory. Tried to allocate 2 GiB"))

    def test_cudnn_failure_is_retryable(self) -> None:
        assert _is_accelerator_error(RuntimeError("cuDNN error: CUDNN_STATUS_NOT_SUPPORTED"))

    def test_missing_token_is_not_retried_on_cpu(self) -> None:
        assert not _is_accelerator_error(RuntimeError("401 Invalid user token"))
