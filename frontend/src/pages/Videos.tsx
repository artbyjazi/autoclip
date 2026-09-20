import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { v1, type V1SourceOut } from '../api-v1'
import { ErrorNote } from '../components/ErrorNote'
import { formatDuration } from '../format'

const STEPS = [
  { n: '1', title: 'Paste a link', note: 'Any public YouTube video — podcasts, interviews, talks.' },
  { n: '2', title: 'AI finds the moments', note: 'Transcription, virality scoring, face-tracked vertical reframe.' },
  { n: '3', title: 'Preview & download', note: 'Lightweight previews first, 1080×1920 finals on demand.' },
]

export function Videos() {
  const navigate = useNavigate()
  const [url, setUrl] = useState('')
  const [maxClips, setMaxClips] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [videos, setVideos] = useState<V1SourceOut[]>([])

  const refresh = useCallback(() => {
    v1.listVideos(25).then(setVideos).catch((err) => setError(err as Error))
  }, [])

  useEffect(() => {
    refresh()
  }, [refresh])

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!url.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      const options: Record<string, unknown> = {}
      const parsed = Number(maxClips)
      if (maxClips.trim() && Number.isFinite(parsed)) options.max_clips = Math.max(1, Math.min(50, Math.round(parsed)))
      const created = await v1.createVideo(url.trim(), options)
      navigate(`/jobs/${created.job_id}`)
    } catch (err) {
      setError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (id: string) => {
    if (!window.confirm('Delete this video and its clips? Final exports are kept per retention policy.')) return
    try {
      await v1.deleteVideo(id)
      setVideos((current) => current.filter((video) => video.id !== id))
    } catch (err) {
      setError(err as Error)
    }
  }

  return (
    <div className="pt-10">
      {/* Hero */}
      <div className="rise mx-auto max-w-2xl text-center">
        <p className="eyebrow">Local · No accounts · No watermarks</p>
        <h1 className="mt-4 font-display text-[clamp(2.5rem,6vw,4.5rem)] leading-[1.02] text-ink-900">
          Long video in. <span className="italic text-iris-600">Shorts</span> out.
        </h1>
        <p className="mt-4 text-[0.9375rem] leading-relaxed text-ink-500">
          AI transcribes, scores the viral moments, tracks the speaker into vertical,
          and burns in captions. You review, preview, and download.
        </p>
      </div>

      {/* Paste card */}
      <div className="rise card mx-auto mt-8 max-w-2xl p-5 sm:p-6" style={{ animationDelay: '90ms' }}>
        <form onSubmit={submit}>
          <label htmlFor="url" className="eyebrow">
            Paste a YouTube link
          </label>
          <div className="mt-3 flex flex-col gap-3 sm:flex-row">
            <input
              id="url"
              className="field flex-1 text-[0.9375rem]"
              placeholder="https://youtube.com/watch?v=…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
            />
            <button type="submit" className="btn btn-primary shrink-0" disabled={busy || !url.trim()}>
              {busy ? 'Queueing…' : 'Get Clips'}
            </button>
          </div>
          <div className="mt-3 flex max-w-[10rem] items-end">
            <label className="block flex-1">
              <span className="eyebrow">Max clips</span>
              <input
                type="number"
                className="field numeric mt-1 text-sm"
                placeholder="10"
                value={maxClips}
                min={1}
                max={50}
                onChange={(e) => setMaxClips(e.target.value)}
                disabled={busy}
              />
            </label>
          </div>
        </form>
        <p className="mt-3 text-xs leading-relaxed text-ink-400">
          Only download video you own or have the rights to process.
        </p>
        {error && (
          <div className="mt-4">
            <ErrorNote error={error} onDismiss={() => setError(null)} />
          </div>
        )}
      </div>

      {/* Steps */}
      <div className="rise mx-auto mt-10 grid max-w-3xl gap-4 sm:grid-cols-3" style={{ animationDelay: '160ms' }}>
        {STEPS.map((step) => (
          <div key={step.n} className="card p-5">
            <span className="numeric font-display text-3xl text-iris-600">{step.n}</span>
            <h2 className="mt-2 text-[0.9375rem] font-semibold text-ink-900">{step.title}</h2>
            <p className="mt-1 text-xs leading-relaxed text-ink-500">{step.note}</p>
          </div>
        ))}
      </div>

      {/* History */}
      <section className="rise mx-auto mt-12 max-w-3xl" style={{ animationDelay: '220ms' }}>
        <div className="flex items-baseline justify-between pb-3">
          <h2 className="eyebrow">Recent videos</h2>
          <span className="numeric text-xs text-ink-400">{videos.length}</span>
        </div>
        {videos.length === 0 ? (
          <div className="card p-8 text-center">
            <p className="text-sm text-ink-500">No videos yet — paste a link above to start.</p>
          </div>
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2">
            {videos.map((video) => (
              <li key={video.id} className="card group flex items-center gap-4 p-4">
                <Link to={`/videos/${video.id}/clips`} className="min-w-0 flex-1">
                  <span className="block truncate text-[0.9375rem] font-semibold text-ink-900 group-hover:text-iris-600">
                    {video.title || video.url || 'Untitled'}
                  </span>
                  <span className="numeric mt-1 block text-xs text-ink-500">
                    {formatDuration(video.duration_s)}
                    {video.channel ? ` · ${video.channel}` : ''}
                  </span>
                </Link>
                <button
                  onClick={() => void remove(video.id)}
                  className="btn btn-quiet shrink-0 text-xs"
                  title="Delete video"
                >
                  Delete
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
