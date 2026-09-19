# Clippy v0.1 frontend handoff

Backend contract freeze: `clippy-backend-v0.1.0-rc1`.

OpenCode owns frontend identity, UX, responsive behavior, and client-side tests.
It must consume `docs/openapi-v1.json` and must not change SQLite migrations,
pipeline stages, provider routers, storage retention, authentication, or the API
schemas. A missing frontend need becomes a separate backend request rather than
an opportunistic schema edit.

## Runtime contract

- Base URL is the same origin as the UI.
- Swagger is `/api/docs`; the frozen document is `/api/openapi.json`.
- `/api/v1/health` is public. Every other `/api/v1/*` route requires
  `Authorization: Bearer <clp_...>`.
- A new analysis finishes as `ready_for_review`. Preview and final-render jobs
  finish as `completed`.
- Public states are `queued`, `running`, `ready_for_review`, `completed`,
  `failed`, and `cancelled`.
- `current_stage` is one of `download`, `audio`, `transcription`, `candidates`,
  `analysis`, `ranking`, `reframe`, `preview`, `render`, or `ready_for_review`
  depending on the active path. Clients must display unknown future values.
- SSE is at `GET /api/v1/jobs/{id}/events`. Reconnect after a network break and
  always re-fetch the job because SQLite is authoritative.
- Analysis never renders every clip. Use the preview and render endpoints only
  after the user selects a clip.
- v0.1 exports are always 9:16. Preview is 540x960; final is 1080x1920.

## Required frontend flow

1. `POST /api/v1/videos` with a YouTube URL.
2. Subscribe to job events and poll `GET /api/v1/jobs/{id}` on reconnect.
3. When ready, load `GET /api/v1/videos/{video_id}/clips`.
4. Show overall score and `score_breakdown`; visibly mark `degraded=true`.
5. Persist title/trim changes with `PATCH /api/v1/clips/{id}`.
6. Request preview, follow its job, and load the preview export from the clip.
7. Request final render, follow its job, then use the authenticated download
   endpoint.
8. Show provider targets, health, cooldowns, and aggregate usage without ever
   asking the API to return secret values.

## Security boundaries

- Provider secrets are write-only. The settings response exposes presence only.
- The YouTube cookie path and contents are server-only; settings expose only
  `cookies_file_configured`.
- API-key creation returns the bearer once. Store it in memory or an explicitly
  user-approved secure browser mechanism; never log it.
- Legacy `/api/*` remains only for the old UI and is deprecated. New work uses
  `/api/v1` exclusively.

## Backend-owned behavior

- FIFO heavy-job execution and restart recovery.
- YouTube ingest, cloud/local ASR failover, 15-minute chunk persistence, and
  timestamp overlap deduplication.
- Heuristic shortlist, LLM evaluation, JSON repair, provider cooldown/circuit
  state, and heuristic degradation.
- Crop paths, captions, ffmpeg settings, cleanup retention, filesystem layout,
  migrations, and API-key hashing.

## Acceptance for OpenCode

- No remaining AutoClip product text in the UI.
- Dashboard, history, SSE progress, clip review, preview, render, download,
  provider/settings/API-key screens work against the frozen schema.
- Responsive and frontend tests pass.
- `frontend/src/api.ts` has no new calls to deprecated `/api/*` routes.
