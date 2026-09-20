# Golden footage manifest

Three ~20 s dialogue segments from **Tears of Steel** (Blender Foundation,
CC-BY 3.0 — attribution: “Tears of Steel, Blender Foundation, CC-BY 3.0”),
720p, real actors with cuts and camera movement.

| file | source | range | sha256 |
|---|---|---|---|
| `tos_dialogue_1.mp4` | https://www.youtube.com/watch?v=OHOpb2fS-cM | TBD | TBD |
| `tos_dialogue_2.mp4` | https://www.youtube.com/watch?v=OHOpb2fS-cM | TBD | TBD |
| `tos_dialogue_3.mp4` | https://www.youtube.com/watch?v=OHOpb2fS-cM | TBD | TBD |

Regenerate with (needs fresh YouTube cookies — they rotate):

```bash
yt-dlp --cookies ~/Downloads/cookies-youtube.txt \
  --remote-components ejs:github \
  --download-sections "*MM:SS-MM:SS" --force-keyframes-at-cuts \
  -f "bv[height<=720]+ba/b[height<=720]" --merge-output-format mp4 \
  -o "tests/data/golden/tos_dialogue_N.mp4" \
  "https://www.youtube.com/watch?v=OHOpb2fS-cM"
```
