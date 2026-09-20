import { useEffect, useRef, useState } from 'react'

import { getToken, v1, type V1JobOut } from './api-v1'

export interface V1StageProgress {
  stage: string
  stageProgress: number
  overall: number
  message: string
}

/** Terminal v1 states. Analysis ends at ready_for_review; renders at completed. */
const TERMINAL = new Set(['ready_for_review', 'completed', 'failed', 'cancelled'])

interface SseEvent {
  type: string
  data: string
}

/** Minimal SSE parser over a fetch stream (EventSource can't send Bearer). */
export async function* readSse(response: Response): AsyncGenerator<SseEvent> {
  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      // sse-starlette (and proxies like Tailscale Serve) frame events with
      // CRLF; normalize so block boundaries are found regardless.
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const block = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        let type = ''
        let data = ''
        for (const line of block.split('\n')) {
          if (line.startsWith('event:')) type = line.slice(6).trim()
          else if (line.startsWith('data:')) data += line.slice(5).trim()
        }
        if (type) yield { type, data }
        boundary = buffer.indexOf('\n\n')
      }
    }
  } finally {
    reader.releaseLock()
  }
}

/**
 * Subscribe to a v1 job's event stream with Bearer auth.
 *
 * fetch() has no auto-reconnect, so on any break while the job is
 * non-terminal we re-fetch the job (SQLite is authoritative) and reconnect
 * after a short backoff.
 */
export function useV1JobStream(jobId: string | undefined) {
  const [job, setJob] = useState<V1JobOut | null>(null)
  const [progress, setProgress] = useState<V1StageProgress | null>(null)
  const [connected, setConnected] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  useEffect(() => {
    if (!jobId) return
    let cancelled = false
    const abort = new AbortController()
    abortRef.current = abort

    v1.getJob(jobId)
      .then((initial) => {
        if (!cancelled) setJob(initial)
      })
      .catch(() => undefined)

    const terminal = (status: string, error: string | null) =>
      setJob((current) =>
        current ? { ...current, status, error, progress: status === 'failed' ? current.progress : 1 } : current,
      )

    const run = async () => {
      let backoff = 2000
      for (;;) {
        if (cancelled || abort.signal.aborted) return
        try {
          const response = await fetch(`/api/v1/jobs/${jobId}/events`, {
            headers: getToken() ? { Authorization: `Bearer ${getToken()}` } : {},
            signal: abort.signal,
          })
          if (!response.ok || !response.body) throw new Error(`SSE ${response.status}`)
          setConnected(true)
          backoff = 2000
          for await (const event of readSse(response)) {
            if (cancelled) return
            const data = event.data ? JSON.parse(event.data) : {}
            if (event.type === 'snapshot') {
              const snapshot = data as V1JobOut
              setJob(snapshot)
              if (TERMINAL.has(snapshot.status)) return
            } else if (event.type === 'progress') {
              setProgress({
                stage: data.stage,
                stageProgress: data.stage_progress ?? 0,
                overall: data.overall ?? 0,
                message: data.message ?? '',
              })
              setJob((current) =>
                current
                  ? { ...current, status: 'running', current_stage: data.stage, progress: data.overall ?? current.progress }
                  : current,
              )
            } else if (event.type === 'done') {
              terminal('completed', null)
              return
            } else if (TERMINAL.has(event.type)) {
              terminal(event.type, data.error ?? null)
              return
            }
          }
          // Stream ended without a terminal state: re-fetch and reconnect.
          try {
            const fresh = await v1.getJob(jobId)
            if (cancelled) return
            setJob(fresh)
            if (TERMINAL.has(fresh.status)) return
          } catch {
            /* Fall through to backoff. */
          }
        } catch {
          if (cancelled || abort.signal.aborted) return
          setConnected(false)
        }
        await new Promise((resolve) => setTimeout(resolve, backoff))
        backoff = Math.min(backoff * 2, 15000)
      }
    }

    void run()
    // Polling fallback: if the SSE connection drops silently (proxy buffering,
    // missed frames) the DB is still authoritative — sync every few seconds
    // while the job is non-terminal so the bar never looks frozen.
    const poller = window.setInterval(() => {
      void v1.getJob(jobId).then((fresh) => {
        if (cancelled) return
        setJob(fresh)
        if (TERMINAL.has(fresh.status)) window.clearInterval(poller)
      }).catch(() => undefined)
    }, 4000)
    return () => {
      cancelled = true
      window.clearInterval(poller)
      abort.abort()
      abortRef.current = null
    }
  }, [jobId])

  return { job, progress, connected, setJob }
}
