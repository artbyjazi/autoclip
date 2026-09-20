# Golden footage manifest

Three 20 s dialogue segments from **Tears of Steel** (Blender Foundation,
CC-BY 3.0 — attribution: “Tears of Steel, Blender Foundation, CC-BY 3.0”),
720p H.264, real actors with cuts and camera movement.

| file | source | range | duration | sha256 |
|---|---|---|---|---|
| `tos_dialogue_1.mp4` | https://www.youtube.com/watch?v=OHOpb2fS-cM | 03:40–04:00 | 20.0 s | `9d1b8515978b33e69fcce2f1475ee536a70be24491fa6860bc5e2a4be573fc2c` |
| `tos_dialogue_2.mp4` | https://www.youtube.com/watch?v=OHOpb2fS-cM | 04:00–04:20 | 20.0 s | `160bb63638f2483aa8082259bd45b36c21be6ebd6093ed1c7265c539a751e0da` |
| `tos_dialogue_3.mp4` | https://www.youtube.com/watch?v=OHOpb2fS-cM | 06:20–06:40 | 20.0 s | `c40a27e1b29c5b66501f5429eda19e7106daf4ddcaff735ed163951ae7ba8a10` |

Calibrated 2026-09-19 (9/9 green on Debian LXC, 45 s): follow 0.70–0.95,
eyes 1.00, smooth ratio ≤3.38, max jump 59 px. Thresholds in
`tests/test_golden.py` sit just above the measured worst case.

Regenerate with (needs fresh YouTube cookies — they rotate):

```bash
yt-dlp --cookies ~/Downloads/cookies-youtube.txt \
  --remote-components ejs:github \
  --download-sections "*MM:SS-MM:SS" --force-keyframes-at-cuts \
  -f "bv[height<=720][vcodec^=avc]+ba/b[height<=720]" --merge-output-format mp4 \
  -o "tests/data/golden/tos_dialogue_N.mp4" \
  "https://www.youtube.com/watch?v=OHOpb2fS-cM"
```

Notes:

- Constrain to H.264 (`vcodec^=avc`): the LXC OpenCV build cannot decode AV1.
- Golden runs on Linux (CI, LXC). MediaPipe's Metal delegate crashes on
  macOS, so `pytest -m golden` is not runnable on a Mac.
