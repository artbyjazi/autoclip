import { useCallback, useEffect, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { v1, type V1SourceOut } from '../api-v1'
import { ErrorNote } from '../components/ErrorNote'
import { formatDuration } from '../format'

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
    <div className="pt-14">
      <div className="rise max-w-3xl">
        <p className="eyebrow">Local · No accounts · No watermarks</p>
        <h1 className="mt-5 font-display text-[clamp(2.75rem,7vw,5.5rem)] leading-[0.95] text-ink-100">
          Long video in.
          <br />
          <span className="italic text-sodium-500">Shorts</span> out.
        </h1>
      </div>

      <section className="rise mt-16 max-w-3xl" style={{ animationDelay: '90ms' }}>
        <form onSubmit={submit}>
          <label htmlFor="url" className="eyebrow">
            Paste a YouTube link
          </label>
          <div className="mt-3 flex items-end gap-4">
            <input
              id="url"
              className="field font-display text-xl md:text-2xl"
              placeholder="https://youtube.com/watch?v=…"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
            />
            <button type="submit" className="btn btn-primary shrink-0" disabled={busy || !url.trim()}>
              {busy ? 'Queueing…' : 'Start'}
            </button>
          </div>
          <div className="mt-4 flex max-w-xs items-end gap-4">
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
        <p className="mt-3 text-xs leading-relaxed text-ink-500">
          Only download video you own or have the rights to process. Analysis ends ready for
          review — previews and final 9:16 renders are on demand.
        </p>
        {error && (
          <div className="mt-6">
            <ErrorNote error={error} onDismiss={() => setError(null)} />
          </div>
        )}
      </section>

      <section className="rise mt-24" style={{ animationDelay: '160ms' }}>
        <div className="flex items-baseline justify-between border-b border-ink-800 pb-3">
          <h2 className="eyebrow">Videos</h2>
          <span className="numeric text-xs text-ink-600">{videos.length}</span>
        </div>
        {videos.length === 0 ? (
          <p className="py-8 text-sm text-ink-500">No videos yet — paste a link above.</p>
        ) : (
          <ul>
            {videos.map((video) => (
              <li
                key={video.id}
                className="group grid grid-cols-[1fr_auto] items-baseline gap-4 border-b border-ink-850 py-4 sm:grid-cols-[1fr_7rem_10rem_5rem]"
              >
                <Link
                  to={`/videos/${video.id}/clips`}
                  className="truncate text-[0.9375rem] text-ink-200 group-hover:text-ink-100"
                >
                  {video.title || video.url || 'Untitled'}
                </Link>
                <span className="numeric hidden text-xs text-ink-500 sm:block">
                  {formatDuration(video.duration_s)}
                </span>
                <span className="hidden truncate text-xs text-ink-500 sm:block">
                  {video.channel ?? '—'}
                </span>
                <span className="justify-self-end">
                  <button
                    onClick={() => void remove(video.id)}
                    className="btn btn-ghost text-xs"
                    title="Delete video"
                  >
                    Delete
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
