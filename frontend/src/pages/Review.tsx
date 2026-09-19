import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { formatDuration } from '../api'
import { v1, type V1ClipOut, type V1SourceOut } from '../api-v1'
import { ErrorNote } from '../components/ErrorNote'
import { useV1JobStream } from '../useV1JobStream'

const STYLES = ['bold', 'karaoke', 'clean', 'hormozi', 'mrbeast'] as const

const BREAKDOWN_LABELS: Record<string, string> = {
  hook: 'Hook',
  retention: 'Retention',
  clarity: 'Clarity',
  insight: 'Insight',
  emotion: 'Emotion',
  surprise: 'Surprise',
  energy: 'Energy',
  standalone_context: 'Standalone',
  complete_thought: 'Complete thought',
  payoff: 'Payoff',
}

export function Review() {
  const { videoId } = useParams()
  const [video, setVideo] = useState<V1SourceOut | null>(null)
  const [clips, setClips] = useState<V1ClipOut[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [previewJobId, setPreviewJobId] = useState<string | null>(null)
  const [renderJobId, setRenderJobId] = useState<string | null>(null)
  const [mediaUrl, setMediaUrl] = useState<string | null>(null)
  const [mediaBusy, setMediaBusy] = useState(false)

  useEffect(() => {
    if (!videoId) return
    void Promise.all([v1.getVideo(videoId), v1.listClips(videoId)])
      .then(([loadedVideo, loadedClips]) => {
        setVideo(loadedVideo)
        const ranked = [...loadedClips].sort((a, b) => b.score - a.score)
        setClips(ranked)
        setSelectedId((current) => current ?? ranked[0]?.id ?? null)
      })
      .catch((err) => setError(err as Error))
  }, [videoId])

  // Revoke blob URLs when replaced or unmounted.
  useEffect(() => () => {
    if (mediaUrl) URL.revokeObjectURL(mediaUrl)
  }, [mediaUrl])

  const selected = useMemo(
    () => clips.find((clip) => clip.id === selectedId) ?? null,
    [clips, selectedId],
  )

  const patchClip = useCallback((updated: V1ClipOut) => {
    setClips((current) => current.map((clip) => (clip.id === updated.id ? updated : clip)))
  }, [])

  const refreshClip = useCallback(
    async (id: string) => {
      patchClip(await v1.getClip(id))
    },
    [patchClip],
  )

  const loadMedia = useCallback(async () => {
    if (!selected) return
    setMediaBusy(true)
    setError(null)
    try {
      const url = await v1.downloadBlob(selected.id)
      setMediaUrl((current) => {
        if (current) URL.revokeObjectURL(current)
        return url
      })
    } catch (err) {
      setError(err as Error)
    } finally {
      setMediaBusy(false)
    }
  }, [selected])

  if (error && clips.length === 0) {
    return (
      <div className="pt-24">
        <ErrorNote error={error} onDismiss={() => setError(null)} />
        <p className="mt-4 text-sm text-ink-500">
          <Link to="/" className="text-sodium-500 underline underline-offset-4">
            Back to videos
          </Link>
        </p>
      </div>
    )
  }

  return (
    <div className="pt-14">
      <p className="eyebrow">
        <Link to="/" className="hover:text-ink-200">
          Videos
        </Link>{' '}
        / Review
      </p>
      <h1 className="mt-2 max-w-3xl truncate font-display text-[clamp(1.75rem,4vw,3rem)] leading-tight text-ink-100">
        {video?.title || 'Loading…'}
      </h1>
      {video && (
        <p className="numeric mt-2 text-xs text-ink-500">
          {formatDuration(video.duration_s)} · {video.channel ?? ''} · {clips.length} clips
        </p>
      )}

      {error && (
        <div className="mt-6 max-w-3xl">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      <div className="mt-10 grid gap-x-12 gap-y-10 lg:grid-cols-[1fr_1.4fr]">
        {/* Ranked list */}
        <section>
          <h2 className="eyebrow">Clips</h2>
          <ul className="mt-3">
            {clips.map((clip) => (
              <li key={clip.id}>
                <button
                  onClick={() => {
                    setSelectedId(clip.id)
                    setPreviewJobId(null)
                    setRenderJobId(null)
                  }}
                  className={[
                    'grid w-full grid-cols-[3rem_1fr_auto] items-baseline gap-3 border-b border-ink-850 py-3 text-left transition-colors duration-200',
                    clip.id === selectedId ? 'bg-ink-850/40' : 'hover:bg-ink-850/40',
                  ].join(' ')}
                >
                  <span className="numeric pl-2 text-2xl text-sodium-500">{clip.score}</span>
                  <span className="min-w-0">
                    <span className="block truncate text-[0.9375rem] text-ink-100">
                      {clip.title}
                    </span>
                    <span className="numeric block text-xs text-ink-500">
                      {formatDuration(clip.start_s)}–{formatDuration(clip.end_s)} · {clip.status}
                      {clip.degraded ? ' · degraded' : ''}
                    </span>
                  </span>
                  <span className="numeric pr-2 text-xs text-ink-500">#{clip.rank}</span>
                </button>
              </li>
            ))}
          </ul>
          {clips.length === 0 && (
            <p className="py-8 text-sm text-ink-500">
              No clips yet — the analysis may still be running.
            </p>
          )}
        </section>

        {/* Detail */}
        {selected && (
          <section>
            <ClipDetail
              clip={selected}
              onChanged={patchClip}
              onError={setError}
              previewJobId={previewJobId}
              renderJobId={renderJobId}
              onPreview={(jobId) => {
                setPreviewJobId(jobId)
                setRenderJobId(null)
              }}
              onRender={(jobId) => {
                setRenderJobId(jobId)
                setPreviewJobId(null)
              }}
              onPreviewDone={() => void refreshClip(selected.id)}
              onRenderDone={() => void refreshClip(selected.id)}
              mediaUrl={mediaUrl}
              mediaBusy={mediaBusy}
              onLoadMedia={() => void loadMedia()}
            />
          </section>
        )}
      </div>
    </div>
  )
}

function ClipDetail({
  clip,
  onChanged,
  onError,
  previewJobId,
  renderJobId,
  onPreview,
  onRender,
  onPreviewDone,
  onRenderDone,
  mediaUrl,
  mediaBusy,
  onLoadMedia,
}: {
  clip: V1ClipOut
  onChanged: (clip: V1ClipOut) => void
  onError: (err: Error) => void
  previewJobId: string | null
  renderJobId: string | null
  onPreview: (jobId: string) => void
  onRender: (jobId: string) => void
  onPreviewDone: () => void
  onRenderDone: () => void
  mediaUrl: string | null
  mediaBusy: boolean
  onLoadMedia: () => void
}) {
  const [title, setTitle] = useState(clip.title)
  const [start, setStart] = useState(String(Math.round(clip.start_s * 100) / 100))
  const [end, setEnd] = useState(String(Math.round(clip.end_s * 100) / 100))
  const [style, setStyle] = useState(clip.caption_style || 'bold')
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => {
    setTitle(clip.title)
    setStart(String(Math.round(clip.start_s * 100) / 100))
    setEnd(String(Math.round(clip.end_s * 100) / 100))
    setStyle(clip.caption_style || 'bold')
  }, [clip.id]) // eslint-disable-line react-hooks/exhaustive-deps

  const run = async (kind: string, fn: () => Promise<unknown>) => {
    setBusy(kind)
    try {
      await fn()
    } catch (err) {
      onError(err as Error)
    } finally {
      setBusy(null)
    }
  }

  const saveMeta = () =>
    run('meta', async () => {
      const start_s = Number(start)
      const end_s = Number(end)
      if (!Number.isFinite(start_s) || !Number.isFinite(end_s) || end_s <= start_s) {
        throw new Error('Trim range is invalid: end must be after start.')
      }
      onChanged(await v1.patchClip(clip.id, { title, start_s, end_s }))
    })

  const setStatus = (status: string) =>
    run('status', async () => {
      onChanged(await v1.patchClip(clip.id, { status }))
    })

  const preview = () =>
    run('preview', async () => {
      const job = await v1.previewClip(clip.id, STYLES.includes(style as (typeof STYLES)[number]) ? style : 'bold')
      onPreview(job.id)
    })

  const render = () =>
    run('render', async () => {
      const job = await v1.renderClip(clip.id)
      onRender(job.id)
    })

  const breakdown = Object.entries(clip.score_breakdown ?? {})

  return (
    <div>
      <div className="flex flex-wrap items-baseline gap-3">
        <span className="numeric font-display text-5xl leading-none text-sodium-500">
          {clip.score}
        </span>
        <div className="flex flex-wrap gap-2 text-xs">
          {clip.degraded && (
            <span className="border border-sodium-600 px-2 py-0.5 text-sodium-500" title="All LLM targets failed; heuristic fallback">
              degraded
            </span>
          )}
          {clip.provider_target && (
            <span className="numeric border border-ink-700 px-2 py-0.5 text-ink-400">
              {clip.provider_target}
            </span>
          )}
          <span className="border border-ink-700 px-2 py-0.5 text-ink-400">{clip.status}</span>
          <span className="numeric border border-ink-700 px-2 py-0.5 text-ink-400">
            {formatDuration(clip.start_s)}–{formatDuration(clip.end_s)}
          </span>
        </div>
      </div>

      <p className="mt-4 text-[0.9375rem] italic text-ink-200">“{clip.hook}”</p>
      <p className="mt-2 text-sm leading-relaxed text-ink-400">{clip.reason}</p>

      {breakdown.length > 0 && (
        <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-2 sm:grid-cols-3">
          {breakdown.map(([key, value]) => (
            <div key={key} className="flex items-baseline justify-between gap-2 border-b border-ink-850 py-1.5">
              <dt className="text-xs text-ink-500">{BREAKDOWN_LABELS[key] ?? key}</dt>
              <dd className="numeric text-sm text-ink-200">{value}</dd>
            </div>
          ))}
        </dl>
      )}

      {/* Title + trim */}
      <div className="mt-8 grid gap-4 sm:grid-cols-[1fr_7rem_7rem_auto]">
        <label className="block">
          <span className="eyebrow">Title</span>
          <input
            className="field mt-1 text-sm"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="eyebrow">Start (s)</span>
          <input
            type="number"
            step="0.1"
            min={0}
            className="field numeric mt-1 text-sm"
            value={start}
            onChange={(e) => setStart(e.target.value)}
          />
        </label>
        <label className="block">
          <span className="eyebrow">End (s)</span>
          <input
            type="number"
            step="0.1"
            min={0}
            className="field numeric mt-1 text-sm"
            value={end}
            onChange={(e) => setEnd(e.target.value)}
          />
        </label>
        <div className="flex items-end">
          <button onClick={() => void saveMeta()} disabled={busy !== null} className="btn btn-ghost text-sm">
            {busy === 'meta' ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>

      {/* Keep / discard */}
      <div className="mt-4 flex gap-3">
        <button
          onClick={() => void setStatus('kept')}
          disabled={busy !== null}
          className="btn btn-ghost text-sm"
        >
          Keep
        </button>
        <button
          onClick={() => void setStatus('discarded')}
          disabled={busy !== null}
          className="btn btn-ghost text-sm"
        >
          Discard
        </button>
      </div>

      {/* Preview / render */}
      <div className="mt-8 border-t border-ink-800 pt-6">
        <div className="flex flex-wrap items-end gap-4">
          <label className="block">
            <span className="eyebrow">Caption style</span>
            <select value={style} onChange={(e) => setStyle(e.target.value)} className="field mt-1 cursor-pointer text-sm">
              {STYLES.map((s) => (
                <option key={s} value={s} className="bg-ink-850">
                  {s}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={() => void preview()}
            disabled={busy !== null}
            className="btn btn-ghost text-sm"
            title="540×960 lightweight H.264"
          >
            {busy === 'preview' ? 'Queueing…' : 'Preview'}
          </button>
          <button
            onClick={() => void render()}
            disabled={busy !== null}
            className="btn btn-primary text-sm"
            title="1080×1920 final"
          >
            {busy === 'render' ? 'Queueing…' : 'Render final'}
          </button>
          <button
            onClick={onLoadMedia}
            disabled={mediaBusy}
            className="btn btn-ghost text-sm"
            title="Fetch the final MP4 with your Bearer key"
          >
            {mediaBusy ? 'Loading…' : mediaUrl ? 'Reload video' : 'Load video'}
          </button>
        </div>
        <p className="mt-2 text-xs text-ink-500">
          Preview is 540×960 ultrafast; final is 1080×1920, always 9:16.
        </p>

        {previewJobId && (
          <JobFollow jobId={previewJobId} label="Preview" onDone={onPreviewDone} />
        )}
        {renderJobId && (
          <JobFollow jobId={renderJobId} label="Render" onDone={onRenderDone} />
        )}

        {mediaUrl && (
          <video key={mediaUrl} controls playsInline src={mediaUrl} className="mt-6 max-h-[70vh] w-full max-w-sm bg-black" />
        )}
      </div>
    </div>
  )
}

/** Follows one preview/render job inline until it reaches a terminal state. */
function JobFollow({ jobId, label, onDone }: { jobId: string; label: string; onDone: () => void }) {
  const { job, progress } = useV1JobStream(jobId)
  const firedRef = useRef(false)

  useEffect(() => {
    if (job && ['completed', 'failed', 'cancelled'].includes(job.status) && !firedRef.current) {
      firedRef.current = true
      if (job.status === 'completed') onDone()
    }
  }, [job?.status]) // eslint-disable-line react-hooks/exhaustive-deps

  const percent = Math.round(((progress?.overall ?? job?.progress) ?? 0) * 100)
  return (
    <p className="numeric mt-3 text-xs text-ink-400">
      {label}: {job?.status ?? 'queued'} · {percent}%{job?.error ? ` · ${job.error}` : ''}
    </p>
  )
}
