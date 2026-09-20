# Contributing to Clippy

Thanks for looking. Clippy is MIT and meant to be hacked on.

## Getting set up

You need **Python 3.11 or 3.12** (not 3.13 — MediaPipe has no wheels for it),
**Node 20+**, and **ffmpeg built with libass**.

```bash
git clone https://github.com/iamyeizi/clippy
cd clippy
uv venv --python 3.11
uv pip install -e ".[dev]"
clippy doctor
```

`doctor` tells you exactly what's missing and how to fix it. Start there rather
than guessing at an error later.

For the UI:

```bash
cd frontend
npm install
npm run dev
```

`npm run dev` serves on 5173 and proxies `/api` to 8000, so run `clippy serve`
alongside it for hot reload against a live backend.

## Before you open a PR

```bash
ruff check . && ruff format --check .
pytest -m "not slow"
pytest -m slow          # real ffmpeg renders; slower, worth it
cd frontend && npm run build
```

CI runs all of this on Linux, macOS, and Windows. The Windows leg is not
decoration — filtergraph path escaping and keyring behaviour genuinely differ
there, and both have already produced bugs.

## Where things are

See [ARCHITECTURE.md](ARCHITECTURE.md) for the map and the reasoning behind the
load-bearing decisions. Skim the "Load-bearing decisions" section before changing
the pipeline; several choices that look arbitrary are working around something
specific.

## Good first contributions

**Prompts.** `backend/clippy/prompts/highlight_v1.txt` is plain text and has
more effect on output quality than most code changes. Add `highlight_v2.txt` and
compare on the same source video. No Python required.

**Caption styles.** Add a preset to `PRESETS` in `pipeline/captions.py`. Each is
a dataclass; the render endpoints validate the key through `get_style`.

**Provider adapters.** Subclass `LLMProvider`, implement `_complete` and
`health_check`, register it in `providers/__init__.py`. The retry loop, JSON
extraction, and index clamping are inherited.

**Language support.** Whisper handles many languages already, but caption
grouping assumes space-separated words and left-to-right layout.

## Things to know before changing the pipeline

**Migrations are append-only.** Once a migration ships, editing it silently
diverges existing databases from fresh installs. Add a new one.

**Never pass a user-controlled path into a filtergraph.** Use
`ffmpeg.relative_filter_workspace()`. The escaping rules are genuinely awkward
and the failure mode is a render that either dies cryptically or quietly drops
captions.

**Reframe changes need the golden set.** The §6.4 acceptance bar — no visible
jitter, no cut-off faces, speaker on screen ≥95% of speaking time — is a release
gate. If you change smoothing, tracking, or strategy selection, run the golden
tests and say what moved.

**Bias toward stillness.** In reframing, a locked frame that is slightly
off-centre beats a frame that is always correct and always moving. Several
defaults exist specifically to stop the crop from micro-correcting.

## Style

Ruff handles formatting and linting; run it and move on. Beyond that:

- Comments explain *why*, not *what*. If a constant looks arbitrary, say what
  it's working around.
- Python docstrings write preconditions as plain lines under a `Preconditions:`
  header.
- Errors should tell the user what to do. `IngestError` and `ProviderError` both
  carry a `hint` for exactly this, and the UI renders it.

## Reporting bugs

Include the output of `clippy doctor`. Most reports in this domain come down
to an ffmpeg build without libass, a GPU driver mismatch, or Python 3.13 — and
`doctor` identifies all three immediately.

## Legal

Clippy bundles yt-dlp. It contains no DRM circumvention and will not accept
contributions that add any.
