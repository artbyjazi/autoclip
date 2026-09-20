import { describe, expect, it } from 'vitest'

import { readSse } from './useV1JobStream'

function sseResponse(body: string): Response {
  const encoder = new TextEncoder()
  const stream = new ReadableStream({
    start(controller) {
      // Split into awkward chunks to also cover boundary-spanning frames.
      const bytes = encoder.encode(body)
      controller.enqueue(bytes.slice(0, 7))
      controller.enqueue(bytes.slice(7))
      controller.close()
    },
  })
  return new Response(stream)
}

describe('readSse', () => {
  it('parses CRLF-framed events (sse-starlette / Tailscale Serve)', async () => {
    const response = sseResponse(
      'event: snapshot\r\ndata: {"status":"running"}\r\n\r\nevent: progress\r\ndata: {"overall":0.62}\r\n\r\n',
    )
    const events = []
    for await (const event of readSse(response)) events.push(event)
    expect(events).toEqual([
      { type: 'snapshot', data: '{"status":"running"}' },
      { type: 'progress', data: '{"overall":0.62}' },
    ])
  })

  it('still parses LF-only events', async () => {
    const response = sseResponse('event: done\ndata: {}\n\n')
    const events = []
    for await (const event of readSse(response)) events.push(event)
    expect(events).toEqual([{ type: 'done', data: '{}' }])
  })
})
