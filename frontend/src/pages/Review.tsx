import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { v1, type V1ClipOut, type V1SourceOut } from '../api-v1'
import { ClipCard } from '../components/ClipCard'
import { ErrorNote } from '../components/ErrorNote'
import { ScoreRing } from '../components/ScoreRing'
import { formatDuration } from '../format'
import { useV1JobStream } from '../useV1JobStream'

const STYLES = ['bold', 'karaoke', 'clean', 'hormozi', 'mrbeast'] as const

const STYLE_BLURB: Record<string, string> = {
  bold: 'Chunky white, heavy outline',
  karaoke: 'Fills with colour as spoken',
  clean: 'Minimal lower-third',
  hormozi: 'Emphatic phrase-by-phrase',
  mrbeast: 'High-energy outlined',
}

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

type Filter = 'all' | 'kept' | 'discarded'

export function Review() {
  const { videoId } = useParams()
  const [video, setVideo] = useState<V1SourceOut | null>(null)
  const [clips, setClips] = useState<V1ClipOut[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [filter, setFilter] = useState<Filter>('all')
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

  useEffect(() => () => {
    if (mediaUrl) URL.revokeObjectURL(mediaUrl)
  }, [mediaUrl])

  const selected = useMemo(
    () => clips.find((clip) => clip.id === selectedId) ?? null,
    [clips, selectedId],
  )

  const visible = useMemo(() => {
    if (filter === 'all') return clips
    return clips.filter((clip) => clip.status === filter)
  }, [clips, filter])

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

  const select = (id: string) => {
    setSelectedId(id)
    setPreviewJobId(null)
    setRenderJobId(null)
  }

  if (error && clips.length === 0) {
    return (
      <div className="pt-24">
        <ErrorNote error={error} onDismiss={() => setError(null)} />
        <p className="mt-4 text-sm text-ink-500">
          <Link to="/" className="text-iris-600 underline underline-offset-4">
            Back to videos
          </Link>
        </p>
      </div>
    )
  }

  return (
    <div className="pt-10">
      <p className="eyebrow">
        <Link to="/" className="hover:text-iris-600">
          Videos
        </Link>{' '}
        / Review
      </p>
      <div className="mt-2 flex flex-wrap items-end justify-between gap-4">
        <h1 className="max-w-3xl truncate font-display text-[clamp(1.75rem,4vw,2.75rem)] leading-tight text-ink-900">
          {video?.title || 'Loading…'}
        </h1>
        {video && (
          <p className="numeric text-xs text-ink-500">
            {formatDuration(video.duration_s)} · {video.channel ?? ''} · {clips.length} clips
          </p>
        )}
      </div>

      {error && (
        <div className="mt-6 max-w-3xl">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {/* Filter */}
      <div className="mt-8 flex gap-2">
        {(['all', 'kept', 'discarded'] as const).map((option) => (
          <button
            key={option}
            onClick={() => setFilter(option)}
            className={[
              'btn text-sm capitalize',
              filter === option ? 'btn-primary' : 'btn-ghost',
            ].join(' ')}
          >
            {option}
          </button>
        ))}
      </div>

      {/* Grid */}
      {visible.length === 0 ? (
        <div className="card mt-6 p-8 text-center">
          <p className="text-sm text-ink-500">
            {clips.length === 0
              ? 'No clips yet — the analysis may still be running.'
              : 'No clips with this status.'}
          </p>
        </div>
      ) : (
        <div className="mt-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5">
          {visible.map((clip, index) => (
            <div key={clip.id} className="rise" style={{ animationDelay: `${Math.min(index, 8) * 55}ms` }}>
              <ClipCard clip={clip} selected={clip.id === selectedId} onSelect={() => select(clip.id)} />
            </div>
          ))}
        </div>
      )}

      {/* Detail */}
      {selected && (
        <section className="card mt-8 p-5 sm:p-8">
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
    <div className="grid gap-8 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
      {/* Player column */}
      <div>
        {mediaUrl ? (
          <video key={mediaUrl} controls playsInline src={mediaUrl} className="aspect-[9/16] max-h-[70vh] w-full rounded-xl bg-ink-900 object-contain" />
        ) : (
          <div className="thumb flex min-h-72 items-center justify-center">
            <div className="text-center">
              <ScoreRing score={clip.score} size={72} />
              <p className="mt-3 px-6 text-xs text-ink-500">
                {mediaBusy ? 'Loading video…' : 'Load the final render to watch it here.'}
              </p>
            </div>
          </div>
        )}
        <div className="mt-4 flex flex-wrap gap-3">
          <button onClick={onLoadMedia} disabled={mediaBusy} className="btn btn-ghost text-sm">
            {mediaBusy ? 'Loading…' : mediaUrl ? 'Reload video' : 'Load video'}
          </button>
          {mediaUrl && (
            <a href={mediaUrl} download={`${clip.title || 'clip'}.mp4`} className="btn btn-primary text-sm">
              Download MP4
            </a>
          )}
        </div>
      </div>

      {/* Data column */}
      <div>
        <div className="flex flex-wrap items-center gap-3">
          <ScoreRing score={clip.score} size={56} />
          <div className="flex flex-wrap gap-2">
            {clip.degraded && (
              <span className="badge badge-warn" title="All LLM targets failed; heuristic fallback">
                degraded
              </span>
            )}
            {clip.provider_target && (
              <span className="badge numeric">{clip.provider_target}</span>
            )}
            <span className="badge">{clip.status}</span>
            <span className="badge numeric">
              {formatDuration(clip.start_s)}–{formatDuration(clip.end_s)}
            </span>
          </div>
        </div>

        <p className="mt-4 font-display text-xl italic leading-snug text-ink-900">“{clip.hook}”</p>
        <p className="mt-2 text-sm leading-relaxed text-ink-500">{clip.reason}</p>

        {breakdown.length > 0 && (
          <div className="mt-6 grid gap-x-6 gap-y-3 sm:grid-cols-2">
            {breakdown.map(([key, value]) => (
              <div key={key}>
                <div className="flex items-baseline justify-between gap-2 text-xs">
                  <span className="text-ink-500">{BREAKDOWN_LABELS[key] ?? key}</span>
                  <span className="numeric font-semibold text-ink-900">{value}</span>
                </div>
                <div className="meter mt-1.5">
                  <span style={{ width: `${Math.max(0, Math.min(100, value))}%` }} />
                </div>
              </div>
            ))}
          </div>
        )}

        {/* Title + trim */}
        <div className="mt-6 grid gap-3 sm:grid-cols-[1fr_6.5rem_6.5rem_auto]">
          <label className="block">
            <span className="eyebrow">Title</span>
            <input className="field mt-1 text-sm" value={title} onChange={(e) => setTitle(e.target.value)} />
          </label>
          <label className="block">
            <span className="eyebrow">Start (s)</span>
            <input type="number" step="0.1" min={0} className="field numeric mt-1 text-sm" value={start} onChange={(e) => setStart(e.target.value)} />
          </label>
          <label className="block">
            <span className="eyebrow">End (s)</span>
            <input type="number" step="0.1" min={0} className="field numeric mt-1 text-sm" value={end} onChange={(e) => setEnd(e.target.value)} />
          </label>
          <div className="flex items-end">
            <button onClick={() => void saveMeta()} disabled={busy !== null} className="btn btn-ghost text-sm">
              {busy === 'meta' ? 'Saving…' : 'Save'}
            </button>
          </div>
        </div>

        <div className="mt-3 flex gap-2">
          <button onClick={() => void setStatus('kept')} disabled={busy !== null} className="btn btn-ghost text-sm">
            Keep
          </button>
          <button onClick={() => void setStatus('discarded')} disabled={busy !== null} className="btn btn-quiet text-sm">
            Discard
          </button>
        </div>

        {/* Styles */}
        <div className="mt-6">
          <span className="eyebrow">Caption style</span>
          <div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-3">
            {STYLES.map((key) => (
              <button
                key={key}
                onClick={() => setStyle(key)}
                className={[
                  'rounded-xl border p-3 text-left transition-shadow',
                  style === key ? 'border-iris-600 shadow-pop' : 'border-line bg-card hover:shadow-card',
                ].join(' ')}
                aria-pressed={style === key}
              >
                <span className="block aspect-[9/16] overflow-hidden rounded-lg bg-ink-900 p-2">
                  <span className="mt-6 block text-center font-display text-sm font-bold uppercase leading-tight text-white">
                    {key === 'karaoke' ? <span className="text-iris-500">Keep</span> : 'Keep'} watching
                  </span>
                </span>
                <span className="mt-2 block text-xs font-semibold capitalize text-ink-900">{key}</span>
                <span className="block text-[0.6875rem] leading-snug text-ink-500">{STYLE_BLURB[key]}</span>
              </button>
            ))}
          </div>
        </div>

        {/* Preview / render */}
        <div className="mt-6 flex flex-wrap gap-3">
          <button onClick={() => void preview()} disabled={busy !== null} className="btn btn-ghost text-sm" title="540×960 lightweight H.264">
            {busy === 'preview' ? 'Queueing…' : 'Preview'}
          </button>
          <button onClick={() => void render()} disabled={busy !== null} className="btn btn-primary text-sm" title="1080×1920 final">
            {busy === 'render' ? 'Queueing…' : 'Render final'}
          </button>
        </div>
        <p className="mt-2 text-xs text-ink-400">Preview is 540×960 ultrafast; final is 1080×1920, always 9:16.</p>

        {previewJobId && <JobFollow jobId={previewJobId} label="Preview" onDone={onPreviewDone} />}
        {renderJobId && <JobFollow jobId={renderJobId} label="Render" onDone={onRenderDone} />}
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
    <p className="numeric mt-3 text-xs text-ink-500">
      {label}: {job?.status ?? 'queued'} · {percent}%{job?.error ? ` · ${job.error}` : ''}
    </p>
  )
}
