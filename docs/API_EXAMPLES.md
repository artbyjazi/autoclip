# Clippy API v1 examples

Create a bearer once on the server. The complete value is shown only by the
create command:

```bash
clippy api-key create --name laptop
export CLIPPY_TOKEN='clp_value-shown-once'
export CLIPPY_URL='https://clippy.example-tailnet.ts.net'
```

Health is intentionally public:

```bash
curl --fail "$CLIPPY_URL/api/v1/health"
```

Queue a YouTube analysis:

```bash
curl --fail-with-body \
  -H "Authorization: Bearer $CLIPPY_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"youtube_url":"https://www.youtube.com/watch?v=VIDEO_ID"}' \
  "$CLIPPY_URL/api/v1/videos"
```

Inspect a job and stream events:

```bash
curl --fail -H "Authorization: Bearer $CLIPPY_TOKEN" \
  "$CLIPPY_URL/api/v1/jobs/JOB_ID"
curl -N -H "Authorization: Bearer $CLIPPY_TOKEN" \
  "$CLIPPY_URL/api/v1/jobs/JOB_ID/events"
```

List candidates and request renders:

```bash
curl --fail -H "Authorization: Bearer $CLIPPY_TOKEN" \
  "$CLIPPY_URL/api/v1/videos/VIDEO_ID/clips"
curl --fail-with-body -X POST \
  -H "Authorization: Bearer $CLIPPY_TOKEN" \
  -H 'Content-Type: application/json' -d '{"style":"bold"}' \
  "$CLIPPY_URL/api/v1/clips/CLIP_ID/preview"
curl --fail-with-body -X POST \
  -H "Authorization: Bearer $CLIPPY_TOKEN" \
  -H 'Content-Type: application/json' -d '{"style":"bold"}' \
  "$CLIPPY_URL/api/v1/clips/CLIP_ID/render"
```

Download the latest completed final render:

```bash
curl --fail -L -H "Authorization: Bearer $CLIPPY_TOKEN" \
  -o clip.mp4 "$CLIPPY_URL/api/v1/clips/CLIP_ID/download"
```

Never put tokens in committed scripts, URLs, browser console output, or issue
reports.
