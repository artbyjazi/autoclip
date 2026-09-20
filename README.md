# Clippy

**Open-source, local-first AI video clipper.** Long video in → ranked, caption-burned, speaker-tracked 9:16 clips out.

[![CI](https://github.com/iamyeizi/clippy/actions/workflows/ci.yml/badge.svg)](https://github.com/iamyeizi/clippy/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)

Paste a YouTube link or drop a file. Clippy transcribes it, uses an LLM to find the moments worth clipping, reframes them to vertical while tracking whoever is speaking, burns in animated captions, and exports platform-ready MP4s.

No accounts. No uploads to anyone's servers. No watermarks. No subscription.

---

## Why this exists

Existing open-source clippers stop at "works on my machine" — no UI, janky reframing, ugly captions. Clippy ships the whole loop: ingest → clips → review → export.

Three transcription paths are tried in priority order:

- **Groq** — `whisper-large-v3-turbo` with word timestamps.
- **Gemini** — timestamped transcription when configured.
- **Fully local** — faster-whisper `small`, requiring no API key.

Audio is sent only to the selected cloud ASR target. Transcript text is sent to
the selected ranking target. With local ASR plus local ranking, nothing leaves
the server.

## Status

The v0.1 backend provides durable analysis/preview/render jobs, resumable ASR,
hybrid ranking, 9:16 CPU rendering, Bearer-authenticated API v1, provider
failover, retention cleanup, and private Tailscale deployment. The bundled UI
talks exclusively to the Bearer-authenticated API v1; see
[HANDOFF_OPENCODE.md](docs/HANDOFF_OPENCODE.md) for the frozen contract.

Pre-1.0. The reframe quality bar hasn't been validated against a fixed golden set yet, which is the gate for tagging v0.1.0.

## Requirements

| | |
|---|---|
| **Python** | 3.12 through `uv` — **not 3.13+.** MediaPipe has no compatible wheels there. |
| **ffmpeg** | A *full* build with `libass` and `libx264`. Both `ffmpeg` and `ffprobe` on PATH. See below — the default package is the wrong one on macOS and Windows. |
| **Node** | 20+, to build the UI. Not needed at runtime. |
| **GPU** | Optional. NVIDIA or Apple Silicon speeds up transcription several-fold; CPU works, just slower. |

`clippy doctor` checks all of this and tells you exactly what to fix. Run it before anything else.

### Installing ffmpeg correctly

Captions are burned in with **libass**, and on two platforms the obvious package doesn't include it. It installs cleanly, then fails to burn a single caption.

**macOS** — Homebrew split the formula; the plain one is a reduced build:

```bash
brew install ffmpeg-full
```

If you already installed the plain one: `brew unlink ffmpeg && brew link --force --overwrite ffmpeg-full`.

**Windows** — install the *full* build, not `Gyan.FFmpeg.Essentials`:

```bash
winget install Gyan.FFmpeg
```

**Linux** — the distro package is fine:

```bash
sudo apt install ffmpeg
```

## Quickstart

```bash
git clone git@github.com:iamyeizi/clippy.git
```

```bash
cd clippy && uv sync --frozen --python 3.12 --extra dev
```

```bash
cd frontend && npm install && npm run build && cd ..
```

```bash
clippy doctor
```

```bash
clippy serve
```

That opens `http://localhost:8000`. If you don't use [uv](https://docs.astral.sh/uv/), a plain `python -m venv .venv` and `pip install -e ".[dev]"` works the same way.

### Add a provider

Clip selection needs a language model. Either paste a key in **Settings → Keys**, or:

```bash
clippy config set-secret gemini
```

Keys go into your OS keyring — Credential Manager, Keychain, or Secret Service — never into a config file. If no keyring backend exists, Clippy falls back to a file **and says so**, in the UI and in `doctor`.

For a fully local setup, install [Ollama](https://ollama.com) and pull a model instead:

```bash
ollama pull llama3.1:8b
```

Clip quality tracks model quality closely. A 7B model returns valid JSON full of mediocre picks; a frontier model is noticeably better at spotting a real hook. That's the honest trade for running offline.

### Optional extras

```bash
uv pip install -e ".[gpu]"
```

CUDA runtime libraries for NVIDIA GPUs. Install these if `doctor` reports that CTranslate2 can't see your GPU — pip puts them somewhere the OS loader doesn't search, and Clippy registers the location itself. On macOS this deliberately installs nothing: there's no CUDA there, and Apple Silicon takes the CPU path.

```bash
uv pip install -e ".[diarization]"
```

Speaker diarization via WhisperX — what lets the reframe stage cut to whoever is talking in a multi-person video. Pulls PyTorch (large), and needs a HuggingFace token plus acceptance of the gated pyannote model licences.

### Docker

The guaranteed path — ffmpeg, Python version, and fonts all pinned:

```bash
docker compose -f docker/compose.yaml up --build
```

```bash
docker compose -f docker/compose.yaml --profile gpu up --build
```

## Using it

Everything the UI does is also on the CLI:

| command | does |
|---|---|
| `clippy doctor` | check this machine and explain what's missing |
| `clippy serve` | start the web app |
| `clippy clip <url\|file>` | analyze and produce review-ready candidates |
| `clippy api-key create` | create a Bearer key, displayed once |
| `clippy cleanup` | apply retention without deleting final exports |
| `clippy jobs` | list recent jobs |
| `clippy providers` | check which providers are reachable |
| `clippy styles` | list caption presets |
| `clippy config show` | print settings |
| `clippy update-ytdlp` | update yt-dlp after a YouTube change |

### Caption styles

| style | look |
|---|---|
| `bold` | chunky white, heavy outline, active word highlighted |
| `karaoke` | words fill with colour as they are spoken |
| `clean` | minimal lower-third presentation |
| `hormozi` | emphatic phrase-by-phrase social style |
| `mrbeast` | high-energy outlined style |

Fonts are bundled under the SIL Open Font License, so nothing is fetched at runtime.

## Troubleshooting

Everything here is a real failure hit during development, not hypothetical.

**`clippy doctor` says Python is wrong.** You're on 3.13. MediaPipe has no wheels for it. `uv venv --python 3.11`.

**Transcription fails with "Library cublas64_12.dll is not found".** The CUDA runtime libraries aren't installed. `uv pip install -e ".[gpu]"`. Clippy registers their location itself — pip installs them somewhere the OS loader doesn't search, which is why the error is so unhelpful.

**"Requested int8_float16 compute type, but the target device does not support..."** Clear `whisper.compute_type` in `~/.clippy/config.json` and let Clippy choose. It queries the backend for what's actually supported rather than guessing from your GPU model.

**Captions don't appear in exports, or ffmpeg says "No such filter: ass".** Your ffmpeg has no libass. `doctor` reports this and prints the right command for your platform. On macOS that means `brew install ffmpeg-full`, not `brew install ffmpeg`; on Windows the full Gyan build, not "essentials".

**YouTube downloads fail with a bot check.** As of 2026, YouTube blocks most anonymous downloads and proof-of-origin tokens no longer clear it. Set **Settings → Ingest → cookies from browser** to a browser you're signed into, and **close that browser** first — it locks its cookie database while running. Uploading a file always works and needs none of this.

**Exports are slower than expected.** Check `doctor` for GPU encoding. A build can list `h264_nvenc` and still be unusable if your driver is older than the NVENC API it was compiled against; Clippy probes this and falls back to CPU encoding, which is identical quality and just slower.

**No sound in the review player.** Click **Test audio** under the player. It measures the actual signal leaving the video element and tells you whether the problem is in Clippy or between your browser and your speakers.

## How it works

```
download → audio → transcription → candidates → analysis → ranking → reframe
                                                        ↓
                                              review → preview/render
```

Each stage writes artifacts to `~/.clippy/work/{job_id}/`, so a retry resumes at the stage that failed rather than starting over. Two decisions carry most of the design:

**Highlight detection returns word indices, not timestamps.** Models are unreliable at arithmetic and completely reliable at copying a number they can see. Timing is looked up from measured word timings afterwards.

**Crop paths never interpolate across a cut.** Shots are detected first and framed independently. Panning through an edit is the most obvious sign of an auto-reframed video.

[ARCHITECTURE.md](docs/ARCHITECTURE.md) covers the rest, including why several odd-looking choices exist.

## What has and hasn't been verified

**Has been:** the full pipeline on real talking-head footage, asserted end to end — 1080×1920 h264/yuv420p output, AAC at −14 LUFS, crop segments tiling each clip without gaps, captions burned in, framing correctly following a cut to a second speaker. Run it yourself:

```bash
CLIPPY_E2E_MEDIA=/path/to/clip.mp4 pytest -m e2e
```

**Hasn't been:** the §6.4 reframe acceptance bar — no visible jitter, no cut-off faces, speaker on screen ≥95% of speaking time — against a fixed three-video golden set. The mechanics have unit coverage, but "looks right on footage I picked" isn't the bar. This is the gate for v0.1.0.

Also unverified: whether the clip *picks* are good. That's a judgement call about your material and your model, and no test settles it.

## Non-goals

Deliberate, not oversights:

- **No direct posting** to TikTok/Instagram/YouTube. Their APIs are approval-gated; Clippy exports files.
- **No cloud version, no accounts, no telemetry.**
- **No timeline editor** beyond trim handles and caption edits.
- **No DRM circumvention**, ever.

## Contributing

Prompts and caption styles are the highest-leverage places to start, and neither needs deep knowledge of the codebase — `backend/clippy/prompts/highlight_v1.txt` is plain text and affects output quality more than most code changes. See [CONTRIBUTING.md](docs/CONTRIBUTING.md).

## Legal

Clippy bundles [yt-dlp](https://github.com/yt-dlp/yt-dlp). **Only download content you own or have the rights to process.** Clippy contains no workarounds for DRM or paywalled content and never will.

## License

MIT — see [LICENSE](LICENSE). Bundled fonts (Anton, Inter) are under the SIL Open Font License.
