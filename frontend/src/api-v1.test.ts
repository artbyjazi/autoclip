import { beforeEach, describe, expect, it, vi } from 'vitest'

import { getToken, setToken, v1, V1ApiError } from './api-v1'

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

beforeEach(() => {
  sessionStorage.clear()
  vi.unstubAllGlobals()
})

describe('v1 client', () => {
  it('sends the Bearer token and parses JSON', async () => {
    setToken('clp_test')
    const fetchMock = vi.fn(async () => jsonResponse({ status: 'ok', queued: 0 }))
    vi.stubGlobal('fetch', fetchMock)

    const health = await v1.health()

    expect(health).toEqual({ status: 'ok', queued: 0 })
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer clp_test')
  })

  it('clears the token and notifies on 401', async () => {
    setToken('clp_bad')
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{"detail":"nope"}', { status: 401 })))
    const events: string[] = []
    const listener = () => events.push('unauthorized')
    window.addEventListener('clippy:unauthorized', listener)

    await expect(v1.listVideos()).rejects.toBeInstanceOf(V1ApiError)
    expect(getToken()).toBeNull()
    expect(events).toEqual(['unauthorized'])

    window.removeEventListener('clippy:unauthorized', listener)
  })

  it('surfaces server detail messages', async () => {
    setToken('clp_test')
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: 'This clip has no final render yet.' }, 404)),
    )

    await expect(v1.getClip('missing')).rejects.toThrow('This clip has no final render yet.')
  })

  it('downloadBlob returns an object URL', async () => {
    setToken('clp_test')
    const blob = new Blob(['fake-mp4'], { type: 'video/mp4' })
    const response = { ok: true, status: 200, blob: async () => blob } as Response
    vi.stubGlobal('fetch', vi.fn(async () => response))
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-url')

    const url = await v1.downloadBlob('clip-1')
    expect(url).toBe('blob:mock-url')
  })
})
