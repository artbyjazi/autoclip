# Changelog

Notable changes. Format loosely follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versions follow [semantic versioning](https://semver.org/) once 0.1.0 ships.

## Unreleased

Everything so far. The first tagged release waits on the reframe acceptance bar
being validated against a fixed golden video set — see
[the README](README.md#what-has-and-hasnt-been-verified).

### Added

- **Any yt-dlp-supported link, not just YouTube.** Twitch VODs and clips work,
  as do Kick, Vimeo, X and several hundred others. Host validation was replaced
  with "is this an http URL" — yt-dlp reports unsupported sites far more
  accurately than an allowlist we would have to maintain. Twitch's own failure
  modes (subscriber-only VODs, a channel that is live rather than recorded) get
  their own error messages.
- **Partial downloads via a time range.** `start_s`/`end_s` on the ingest
  endpoint fetch only that section of a video — the difference between minutes
  and hours on a multi-hour stream VOD. A `?t=` timestamp in a pasted link seeds
  the start automatically. The excerpt is re-based to zero, so every later
  stage keeps working in file-relative time.
- **Pipeline** — ingest (any video URL via yt-dlp, or file upload), audio preparation,
  transcription with word-level timings, LLM highlight detection, speaker-tracked
  reframing, ASS caption generation, and export at 9:16 / 1:1 / 16:9.
- **Four LLM providers** behind one interface: Anthropic, OpenAI-compatible (any
  `base_url`, covering OpenRouter, Groq, DeepSeek, LM Studio), Google Gemini, and
  Ollama. Malformed responses are retried once with the validation error attached.
- **Reframing** with shot detection, MediaPipe face tracking, active-speaker
  selection from mouth movement correlated against diarization, and a
  One Euro Filter with dead zone and velocity clamp. Shots are framed as TRACK,
  WIDE, or GENERAL; subjects too far apart to crop get a fitted frame over a
  blurred fill rather than someone being cut out.
- **Four caption styles** with bundled OFL fonts, so nothing is fetched at runtime.
- **Web UI** — ingest, live job progress over SSE, clip review with word-snapping
  trim handles and caption editing, and export.
- **Resumable stages.** Artifacts live on disk per job, so a retry restarts at the
  stage that failed.
- **`autoclip doctor`** — probes ffmpeg features, GPU acceleration, compute-type
  support, and provider reachability, and explains what to do about each.
- **End-to-end test** against real media, covering every stage with only the
  language model's answer scripted.

### Changed

- **`POST /api/sources/youtube` is now `POST /api/sources/url`**, taking optional
  `start_s` and `end_s`. The old path stays as a deprecated alias that reaches
  the same handler, so anything scripted against it keeps working.
- **Schema v2** widens `sources.type` to `youtube | twitch | url | upload`. The
  v1 CHECK constraint would have rejected a Twitch source outright. SQLite can't
  alter a CHECK in place, so the migration rebuilds the table; existing rows and
  the jobs that reference them are covered by tests.

### Notable fixes during development

Each of these was found by running the thing, not by reading it:

- **Compute-type selection asked the wrong question.** Inferring from CUDA compute
  capability chose `int8_float16` on a GTX 1060, which advertises fp16 to CUDA and
  then can't use it. Selection now queries CTranslate2 for what it actually
  supports.
- **CUDA libraries were installed and unloadable.** pip places them where the OS
  loader doesn't look, so the model loaded and died at first inference. AutoClip
  registers the directories itself.
- **`h264_nvenc` being listed is not proof it works.** A build can require a newer
  NVENC API than the driver provides. Encoder selection now runs a one-frame probe.
- **Filtergraph paths need two levels of escaping**, and quoting interacts badly
  with apostrophes. Renders now use bare relative names with ffmpeg's working
  directory set, removing the problem instead of out-escaping it.
- **The wheel shipped with no UI.** hatchling honours `.gitignore`, and the built
  frontend is gitignored; declared via `artifacts`.
- **Retry could never clear a job's error message**, because the update helper
  skipped `None` values.
- **Progress ran backwards** at every stage boundary, double-counting each
  completed stage.
- **A 9:16 preview rendered nearly square.** A max-height clamp shortened the box
  without narrowing it, silently violating the declared aspect ratio.
- **The preview showed different framing from the export**, centre-cropping while
  the renderer tracked the speaker.
