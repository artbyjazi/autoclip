/**
 * Typed client for the frozen Clippy API v1 (`docs/openapi-v1.json`).
 *
 * Same-origin throughout: the server hands out the built bundle, so there is
 * no base URL to configure and no CORS to negotiate.
 *
 * - `GET /api/v1/health` is public.
 * - Every other `/api/v1/*` route requires `Authorization: Bearer <clp_...>`.
 * - No calls to deprecated `/api/*` routes from new code.
 */

export type V1JobStatus =
  | 'queued'
  | 'running'
  | 'ready_for_review'
  | 'completed'
  | 'failed'
  | 'cancelled'

export interface V1VideoCreateOut {
  video_id: string
  job_id: string
  status: string
}

export interface V1SourceOut {
  id: string
  type: string
  title: string
  url: string | null
  filename: string | null
  channel: string | null
  duration_s: number
  width: number | null
  height: number | null
  fps: number | null
  has_audio: boolean
  has_video: boolean
  created_at: string
}

export interface V1JobOut {
  id: string
  source_id: string
  status: string
  kind: string
  clip_id: string | null
  parent_job_id: string | null
  current_stage: string
  progress: number
  error: string | null
  provider: string
  created_at: string
  updated_at: string
  started_at: string | null
  finished_at: string | null
  source: V1SourceOut | null
}

export interface V1ExportOut {
  id: string
  clip_id: string
  ratio: string
  style: string
  size_bytes: number
  kind: string
  created_at: string
}

export interface V1ClipOut {
  id: string
  job_id: string
  rank: number
  start_s: number
  end_s: number
  duration_s: number
  title: string
  hook: string
  score: number
  score_breakdown: Record<string, number>
  reason: string
  status: string
  degraded: boolean
  provider_target: string
  caption_style: string
  ratio: string
  exports: V1ExportOut[]
}

export interface V1ApiKeyOut {
  id: string
  name: string
  prefix: string
  created_at: string
  last_used_at: string | null
}

export interface V1ApiKeyCreatedOut extends V1ApiKeyOut {
  token: string
}

export interface V1ProviderTarget {
  id: string
  purpose: string
  provider: string
  model: string
  priority: number
  enabled: boolean
  health: string
  cooldown_until: string
  consecutive_errors: number
  last_error: string
  last_success: string
  updated_at: string
}

export interface V1TargetUsage {
  target_id: string
  requests: number
  successes: number
  errors: number
  average_latency_ms: number
  input_tokens: number
  output_tokens: number
  audio_seconds: number
}

export interface V1ProviderStatus {
  name: string
  available: boolean
  detail: string
  models: string[]
  requires_key: boolean
  has_key: boolean
}

export interface V1SettingsOut {
  active_provider: string
  providers: Record<string, { model: string; base_url: string | null }>
  whisper: Record<string, unknown>
  clips: Record<string, unknown>
  ingest: Record<string, unknown>
  export: Record<string, unknown>
  storage: Record<string, unknown>
  insecure_secret_storage: boolean
  keys_present: Record<string, boolean>
}

export interface V1StorageStatus {
  root: string
  used_bytes: number
  disk_total_bytes: number
  disk_free_bytes: number
}

export interface V1CleanupReport {
  previews: number
  sources: number
  chunks: number
  bytes: number
}

const TOKEN_KEY = 'clippy.v1.token'

export function getToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY)
  } catch {
    return null
  }
}

export function setToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    /* Storage unavailable; token stays in memory only for this page load. */
  }
}

export class V1ApiError extends Error {
  status: number
  constructor(message: string, status: number) {
    super(message)
    this.name = 'V1ApiError'
    this.status = status
  }
}

async function v1request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken()
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  })

  if (response.status === 401) {
    setToken(null)
    window.dispatchEvent(new Event('clippy:unauthorized'))
    throw new V1ApiError('Missing or invalid API key. Create one with `clippy api-key create` and paste it.', 401)
  }

  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      const detail = (body as { detail?: unknown }).detail
      if (typeof detail === 'string') message = detail
      else if (detail && typeof detail === 'object') {
        message =
          (detail as { message?: string }).message ??
          JSON.stringify(detail).slice(0, 300)
      }
    } catch {
      /* Non-JSON error; keep the status line. */
    }
    throw new V1ApiError(message, response.status)
  }

  if (response.status === 204) return undefined as T
  return response.json() as Promise<T>
}

export const v1 = {
  health: () => v1request<{ status: string; queued: number }>('/api/v1/health'),

  createVideo: (youtube_url: string, options: Record<string, unknown> = {}) =>
    v1request<V1VideoCreateOut>('/api/v1/videos', {
      method: 'POST',
      body: JSON.stringify({ youtube_url, options }),
    }),

  listVideos: (limit = 25) => v1request<V1SourceOut[]>(`/api/v1/videos?limit=${limit}`),
  getVideo: (id: string) => v1request<V1SourceOut>(`/api/v1/videos/${id}`),
  deleteVideo: (id: string) =>
    v1request<void>(`/api/v1/videos/${id}`, { method: 'DELETE' }),
  listClips: (videoId: string) =>
    v1request<V1ClipOut[]>(`/api/v1/videos/${videoId}/clips`),

  getJob: (id: string) => v1request<V1JobOut>(`/api/v1/jobs/${id}`),

  getClip: (id: string) => v1request<V1ClipOut>(`/api/v1/clips/${id}`),
  patchClip: (id: string, patch: { start_s?: number; end_s?: number; title?: string; status?: string }) =>
    v1request<V1ClipOut>(`/api/v1/clips/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  previewClip: (id: string, style = 'bold') =>
    v1request<V1JobOut>(`/api/v1/clips/${id}/preview`, {
      method: 'POST',
      body: JSON.stringify({ style }),
    }),
  renderClip: (id: string) =>
    v1request<V1JobOut>(`/api/v1/clips/${id}/render`, { method: 'POST' }),
  downloadUrl: (id: string) => `/api/v1/clips/${id}/download`,

  /** Fetch the final MP4 with Bearer auth and return an object URL. */
  downloadBlob: async (id: string): Promise<string> => {
    const token = getToken()
    const response = await fetch(`/api/v1/clips/${id}/download`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
    if (response.status === 401) {
      setToken(null)
      window.dispatchEvent(new Event('clippy:unauthorized'))
      throw new V1ApiError('Missing or invalid API key.', 401)
    }
    if (!response.ok) throw new V1ApiError(`${response.status} ${response.statusText}`, response.status)
    return URL.createObjectURL(await response.blob())
  },

  providers: () => v1request<{ targets: V1ProviderTarget[]; usage: V1TargetUsage[] }>('/api/v1/providers'),
  providersStatus: () => v1request<V1ProviderStatus[]>('/api/v1/providers/status'),
  getSettings: () => v1request<V1SettingsOut>('/api/v1/settings'),
  patchSettings: (patch: Record<string, unknown>) =>
    v1request<V1SettingsOut>('/api/v1/settings', {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),
  putSecret: (key: string, value: string) =>
    v1request<void>('/api/v1/settings/secrets', {
      method: 'PUT',
      body: JSON.stringify({ key, value }),
    }),
  deleteSecret: (key: string) =>
    v1request<void>(`/api/v1/settings/secrets/${key}`, { method: 'DELETE' }),

  listApiKeys: () => v1request<V1ApiKeyOut[]>('/api/v1/api-keys'),
  createApiKey: (name: string) =>
    v1request<V1ApiKeyCreatedOut>('/api/v1/api-keys', {
      method: 'POST',
      body: JSON.stringify({ name }),
    }),
  deleteApiKey: (id: string) =>
    v1request<void>(`/api/v1/api-keys/${id}`, { method: 'DELETE' }),

  storage: () => v1request<V1StorageStatus>('/api/v1/storage'),
  runCleanup: () => v1request<V1CleanupReport>('/api/v1/storage/cleanup', { method: 'POST' }),
}
