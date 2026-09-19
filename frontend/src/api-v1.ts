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
  created_at: string
  updated_at: string
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
}

export interface V1ApiKeyOut {
  id: string
  name: string
  prefix: string
  created_at: string
  last_used_at: string | null
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

  listVideos: (limit = 25) => v1request<unknown[]>(`/api/v1/videos?limit=${limit}`),
  getVideo: (id: string) => v1request<unknown>(`/api/v1/videos/${id}`),
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

  providers: () => v1request<unknown[]>('/api/v1/providers'),
  providersStatus: () => v1request<unknown[]>('/api/v1/providers/status'),
  getSettings: () => v1request<unknown>('/api/v1/settings'),
  patchSettings: (patch: Record<string, unknown>) =>
    v1request<unknown>('/api/v1/settings', {
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
  deleteApiKey: (id: string) =>
    v1request<void>(`/api/v1/api-keys/${id}`, { method: 'DELETE' }),

  storage: () => v1request<unknown>('/api/v1/storage'),
}
