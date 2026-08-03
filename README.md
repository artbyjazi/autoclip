# AutoClip

**Open-source, local-first AI video clipper.** Long video in → ranked, caption-burned, speaker-tracked 9:16 clips out.

[![CI](https://github.com/artbyjazi/autoclip/actions/workflows/ci.yml/badge.svg)](https://github.com/artbyjazi/autoclip/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.11 | 3.12](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)

Paste a link — YouTube, Twitch, or anything else yt-dlp supports — or drop a file. AutoClip transcribes it, uses an LLM to find the moments worth clipping, reframes them to vertical while tracking whoever is speaking, burns in animated captions, and exports platform-ready MP4s.

No accounts. No uploads to anyone's servers. No watermarks. No subscription.

---

## Why this exists

Existing open-source clippers stop at "works on my machine" — no UI, janky reframing, ugly captions. AutoClip ships the whole loop: ingest → clips → review → export.

Two ways to run it:

- **Fully local** — Whisper + Ollama. Nothing leaves your machine, no API costs.
- **Bring your own key** — Anthropic, OpenAI, Gemini, or any OpenAI-compatible endpoint (OpenRouter, Groq, DeepSeek, LM Studio) for better clip selection.

Only transcript *text* is ever sent to a provider — never video or audio. With Ollama, nothing is sent at all.

## Status

Working end to end: ingest, transcription, highlight detection across four providers, speaker-tracked reframing, four caption styles, export at three aspect ratios, and the full review UI. Verified on real footage — see [what "verified" means](#what-has-and-hasnt-been-verified).

Pre-1.0. The reframe quality bar hasn't been validated against a fixed golden set yet, which is the gate for tagging v0.1.0.

## Requirements

| | |
|---|---|
| **Python** | 3.11 or 3.12 — **not 3.13.** MediaPipe publishes no 3.13 wheels and the reframe stage needs it. |
| **ffmpeg** | A *full* build with `libass` and `libx264`. Both `ffmpeg` and `ffprobe` on PATH. See below — the default package is the wrong one on macOS and Windows. |
| **Node** | 20+, to build the UI. Not needed at runtime. |
| **GPU** | Optional. NVIDIA or Apple Silicon speeds up transcription several-fold; CPU works, just slower. |

`autoclip doctor` checks all of this and tells you exactly what to fix. Run it before anything else.

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
git clone https://github.com/artbyjazi/autoclip.git
```

```bash
cd autoclip && uv venv --python 3.11 && uv pip install -e ".[dev]"
```

```bash
cd frontend && npm install && npm run build && cd ..
```

```bash
autoclip doctor
```

```bash
autoclip serve
```

That opens `http://localhost:8000`. If you don't use [uv](https://docs.astral.sh/uv/), a plain `python -m venv .venv` and `pip install -e ".[dev]"` works the same way.

### Add a provider

Clip selection needs a language model. Either paste a key in **Settings → Keys**, or:

```bash
autoclip config set-secret anthropic
```

Keys go into your OS keyring — Credential Manager, Keychain, or Secret Service — never into a config file. If no keyring backend exists, AutoClip falls back to a file **and says so**, in the UI and in `doctor`.

For a fully local setup, install [Ollama](https://ollama.com) and pull a model instead:

```bash
ollama pull llama3.1:8b
```

Clip quality tracks model quality closely. A 7B model returns valid JSON full of mediocre picks; a frontier model is noticeably better at spotting a real hook. That's the honest trade for running offline.

### Optional extras

```bash
uv pip install -e ".[gpu]"
```

CUDA runtime libraries for NVIDIA GPUs. Install these if `doctor` reports that CTranslate2 can't see your GPU — pip puts them somewhere the OS loader doesn't search, and AutoClip registers the location itself. On macOS this deliberately installs nothing: there's no CUDA there, and Apple Silicon takes the CPU path.

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
| `autoclip doctor` | check this machine and explain what's missing |
| `autoclip serve` | start the web app |
| `autoclip clip <url\|file>` | run the whole pipeline and export |
| `autoclip jobs` | list recent jobs |
| `autoclip providers` | check which providers are reachable |
| `autoclip styles` | list caption presets |
| `autoclip config show` | print settings |
| `autoclip update-ytdlp` | update yt-dlp after a YouTube change |

### Caption styles

| style | look |
|---|---|
| `bold_pop` | chunky white, heavy outline, spoken word grows and turns yellow |
| `karaoke_fill` | words fill with colour exactly as they're spoken |
| `clean_lower` | minimal lower third, no animation |
| `boxed` | high-contrast text on a solid block |

Fonts are bundled under the SIL Open Font License, so nothing is fetched at runtime.

## Troubleshooting

Everything here is a real failure hit during development, not hypothetical.

**`autoclip doctor` says Python is wrong.** You're on 3.13. MediaPipe has no wheels for it. `uv venv --python 3.11`.

**Transcription fails with "Library cublas64_12.dll is not found".** The CUDA runtime libraries aren't installed. `uv pip install -e ".[gpu]"`. AutoClip registers their location itself — pip installs them somewhere the OS loader doesn't search, which is why the error is so unhelpful.

**"Requested int8_float16 compute type, but the target device does not support..."** Clear `whisper.compute_type` in `~/.autoclip/config.json` and let AutoClip choose. It queries the backend for what's actually supported rather than guessing from your GPU model.

**Captions don't appear in exports, or ffmpeg says "No such filter: ass".** Your ffmpeg has no libass. `doctor` reports this and prints the right command for your platform. On macOS that means `brew install ffmpeg-full`, not `brew install ffmpeg`; on Windows the full Gyan build, not "essentials".

**YouTube downloads fail with a bot check.** As of 2026, YouTube blocks most anonymous downloads and proof-of-origin tokens no longer clear it. Set **Settings → Ingest → cookies from browser** to a browser you're signed into, and **close that browser** first — it locks its cookie database while running. Uploading a file always works and needs none of this.

**A Twitch VOD says it's subscriber-only.** Same fix as the bot check: point **cookies from browser** at a browser signed in with an account that subscribes to the channel, and close it. Twitch clip links (`clips.twitch.tv/…`) are public and need none of this.

**A Twitch link fails with "that channel is live".** AutoClip clips finished video, not a running stream. Wait for the stream to end and use the VOD link under the channel's Videos tab.

**A four-hour VOD takes forever.** It would — transcription scales with length. Give the ingest request a `start_s`/`end_s` range and only that section is downloaded and transcribed. Pasting a Twitch or YouTube link that already carries a `?t=` timestamp seeds the start for you.

**Exports are slower than expected.** Check `doctor` for GPU encoding. A build can list `h264_nvenc` and still be unusable if your driver is older than the NVENC API it was compiled against; AutoClip probes this and falls back to CPU encoding, which is identical quality and just slower.

**No sound in the review player.** Click **Test audio** under the player. It measures the actual signal leaving the video element and tells you whether the problem is in AutoClip or between your browser and your speakers.

## How it works

```
ingest → prepare → transcribe → highlights → reframe → captions → export
```

Each stage writes artifacts to `~/.autoclip/work/{job_id}/`, so a retry resumes at the stage that failed rather than starting over. Two decisions carry most of the design:

**Highlight detection returns word indices, not timestamps.** Models are unreliable at arithmetic and completely reliable at copying a number they can see. Timing is looked up from measured word timings afterwards.

**Crop paths never interpolate across a cut.** Shots are detected first and framed independently. Panning through an edit is the most obvious sign of an auto-reframed video.

[ARCHITECTURE.md](docs/ARCHITECTURE.md) covers the rest, including why several odd-looking choices exist.

## What has and hasn't been verified

**Has been:** the full pipeline on real talking-head footage, asserted end to end — 1080×1920 h264/yuv420p output, AAC at −14 LUFS, crop segments tiling each clip without gaps, captions burned in, framing correctly following a cut to a second speaker. Run it yourself:

```bash
AUTOCLIP_E2E_MEDIA=/path/to/clip.mp4 pytest -m e2e
```

**Hasn't been:** the §6.4 reframe acceptance bar — no visible jitter, no cut-off faces, speaker on screen ≥95% of speaking time — against a fixed three-video golden set. The mechanics have unit coverage, but "looks right on footage I picked" isn't the bar. This is the gate for v0.1.0.

Also unverified: whether the clip *picks* are good. That's a judgement call about your material and your model, and no test settles it.

## Non-goals

Deliberate, not oversights:

- **No direct posting** to TikTok/Instagram/YouTube. Their APIs are approval-gated; AutoClip exports files.
- **No cloud version, no accounts, no telemetry.**
- **No timeline editor** beyond trim handles and caption edits.
- **No DRM circumvention**, ever.

## Contributing

Prompts and caption styles are the highest-leverage places to start, and neither needs deep knowledge of the codebase — `backend/autoclip/prompts/highlight_v1.txt` is plain text and affects output quality more than most code changes. See [CONTRIBUTING.md](docs/CONTRIBUTING.md).

## Legal

AutoClip bundles [yt-dlp](https://github.com/yt-dlp/yt-dlp). **Only download content you own or have the rights to process.** AutoClip contains no workarounds for DRM or paywalled content and never will.

## License

MIT — see [LICENSE](LICENSE). Bundled fonts (Anton, Inter) are under the SIL Open Font License.
