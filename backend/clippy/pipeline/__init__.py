"""Video processing pipeline stages."""

from __future__ import annotations

__all__ = ["STAGES", "Stage"]

from enum import StrEnum


class Stage(StrEnum):
    """Ordered pipeline stages.

    Stage order matters: a job that fails at ``REFRAME`` resumes from
    ``REFRAME``, reusing the artifacts every earlier stage left in
    ``work/{job_id}/``.
    """

    INGEST = "download"
    PREPARE = "prepare"
    TRANSCRIBE = "transcribe"
    HIGHLIGHTS = "highlights"
    REFRAME = "reframe"
    CAPTIONS = "captions"
    EXPORT = "export"
    READY = "ready_for_review"
    PREVIEW = "preview"
    RENDER = "render"

    @property
    def label(self) -> str:
        return {
            Stage.INGEST: "Downloading source",
            Stage.PREPARE: "Preparing media",
            Stage.TRANSCRIBE: "Transcribing",
            Stage.HIGHLIGHTS: "Finding highlights",
            Stage.REFRAME: "Reframing to vertical",
            Stage.CAPTIONS: "Generating captions",
            Stage.EXPORT: "Exporting clips",
            Stage.READY: "Ready for review",
            Stage.PREVIEW: "Rendering preview",
            Stage.RENDER: "Rendering final clip",
        }[self]


#: Canonical stage order.
STAGES: tuple[Stage, ...] = tuple(Stage)
