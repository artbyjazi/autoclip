import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'

import { v1, type V1VideoSummaryOut } from '../api-v1'
import { ErrorNote } from '../components/ErrorNote'
import { formatDuration } from '../format'

type StatusFilter = 'all' | 'processing' | 'ready' | 'failed'
type Sort = 'newest' | 'oldest' | 'title'

const CAPTION_STYLES = ['bold', 'karaoke', 'clean', 'hormozi', 'mrbeast']

export function Videos() {
  const navigate = useNavigate()
  const [url, setUrl] = useState('')
  const [minDuration, setMinDuration] = useState('30')
  const [maxDuration, setMaxDuration] = useState('60')
  const [maxClips, setMaxClips] = useState('10')
  const [language, setLanguage] = useState('')
  const [style, setStyle] = useState('bold')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [videos, setVideos] = useState<V1VideoSummaryOut[]>([])
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [sort, setSort] = useState<Sort>('newest')

  const refresh = useCallback(() => {
    v1.listVideos().then(setVideos).catch((err) => setError(err as Error))
  }, [])

  useEffect(() => {
    refresh()
    const timer = window.setInterval(() => {
      setVideos((current) => {
        if (current.some((video) => ['queued', 'running'].includes(video.latest_analysis_job?.status ?? ''))) refresh()
        return current
      })
    }, 4000)
    return () => window.clearInterval(timer)
  }, [refresh])

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    if (!url.trim() || busy) return
    setBusy(true)
    setError(null)
    try {
      const created = await v1.createVideo(url.trim(), {
        min_duration_s: Number(minDuration), max_duration_s: Number(maxDuration),
        max_clips: Number(maxClips), language: language || null,
        caption_style: style, auto_preview: true,
      })
      navigate(`/jobs/${created.job_id}`)
    } catch (err) { setError(err as Error) } finally { setBusy(false) }
  }

  const remove = async (id: string) => {
    if (!window.confirm('Delete this video and its clips? Final exports follow the retention policy.')) return
    try {
      await v1.deleteVideo(id)
      setVideos((current) => current.filter((video) => video.id !== id))
    } catch (err) { setError(err as Error) }
  }

  const reanalyze = async (video: V1VideoSummaryOut) => {
    try {
      const created = await v1.reanalyzeVideo(video.id, { auto_preview: true })
      navigate(`/jobs/${created.job_id}`)
    } catch (err) { setError(err as Error) }
  }

  const retry = async (video: V1VideoSummaryOut) => {
    if (!video.latest_analysis_job) return
    try {
      const job = await v1.retryJob(video.latest_analysis_job.id)
      navigate(`/jobs/${job.id}`)
    } catch (err) { setError(err as Error) }
  }

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase()
    const matches = videos.filter((video) => {
      const status = video.latest_analysis_job?.status ?? 'queued'
      const statusMatch = statusFilter === 'all' ||
        (statusFilter === 'processing' && ['queued', 'running'].includes(status)) ||
        (statusFilter === 'ready' && status === 'ready_for_review') ||
        (statusFilter === 'failed' && ['failed', 'cancelled'].includes(status))
      return statusMatch && (!needle || `${video.title} ${video.channel ?? ''}`.toLowerCase().includes(needle))
    })
    return [...matches].sort((a, b) => {
      if (sort === 'title') return (a.title || '').localeCompare(b.title || '')
      return a.created_at.localeCompare(b.created_at) * (sort === 'newest' ? -1 : 1)
    })
  }, [videos, search, statusFilter, sort])

  return (
    <div className="mx-auto max-w-5xl pt-12">
      <section className="rise mx-auto max-w-2xl text-center">
        <p className="eyebrow">AI clipping</p>
        <h1 className="mt-3 font-display text-[clamp(2.3rem,6vw,4.25rem)] leading-none text-ink-900">
          Turn long videos into <span className="italic text-iris-600">shorts.</span>
        </h1>
      </section>

      <section className="rise card mx-auto mt-8 max-w-2xl p-5 sm:p-6" style={{ animationDelay: '70ms' }}>
        <form onSubmit={submit}>
          <label htmlFor="url" className="eyebrow">YouTube URL</label>
          <div className="mt-2 flex flex-col gap-2 sm:flex-row">
            <input id="url" className="field flex-1" placeholder="Paste a YouTube link" value={url} onChange={(event) => setUrl(event.target.value)} disabled={busy} />
            <button type="submit" className="btn btn-primary shrink-0" disabled={busy || !url.trim()}>{busy ? 'Queueing…' : 'Get Shorts'}</button>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-5">
            <Option label="Min seconds" value={minDuration} onChange={setMinDuration} />
            <Option label="Max seconds" value={maxDuration} onChange={setMaxDuration} />
            <Option label="Max clips" value={maxClips} onChange={setMaxClips} />
            <label className="block"><span className="eyebrow">Language</span><select className="field mt-1 text-sm" value={language} onChange={(event) => setLanguage(event.target.value)}><option value="">Auto</option><option value="en">English</option><option value="es">Spanish</option></select></label>
            <label className="block"><span className="eyebrow">Captions</span><select className="field mt-1 text-sm capitalize" value={style} onChange={(event) => setStyle(event.target.value)}>{CAPTION_STYLES.map((item) => <option key={item}>{item}</option>)}</select></label>
          </div>
        </form>
        <p className="mt-3 text-xs text-ink-400">Previews are prepared automatically. Final 1080×1920 files render only when downloaded.</p>
        {error && <div className="mt-4"><ErrorNote error={error} onDismiss={() => setError(null)} /></div>}
      </section>

      <section className="rise mt-12" style={{ animationDelay: '130ms' }}>
        <div className="mb-4 flex items-baseline gap-2"><h2 className="text-lg font-semibold text-ink-900">Projects</h2><span className="badge">{videos.length} total</span></div>
        <div className="card p-4">
          <div className="grid gap-3 md:grid-cols-[1fr_13rem_13rem]">
            <label><span className="sr-only">Search projects by title or channel</span><input className="field text-sm" placeholder="Search by title or channel…" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
            <select aria-label="Filter projects by status" className="field text-sm" value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}><option value="all">Status: All videos</option><option value="processing">Processing</option><option value="ready">Ready</option><option value="failed">Failed</option></select>
            <select aria-label="Sort projects" className="field text-sm" value={sort} onChange={(event) => setSort(event.target.value as Sort)}><option value="newest">Sort: Newest first</option><option value="oldest">Oldest first</option><option value="title">Title</option></select>
          </div>
          <div className="mt-4 divide-y divide-line">
            {filtered.length === 0 ? <p className="py-10 text-center text-sm text-ink-500">No matching projects.</p> : filtered.map((video) => <ProjectRow key={video.id} video={video} onDelete={() => void remove(video.id)} onRetry={() => void retry(video)} onReanalyze={() => void reanalyze(video)} />)}
          </div>
        </div>
      </section>
    </div>
  )
}

function Option({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <label className="block"><span className="eyebrow">{label}</span><input className="field numeric mt-1 text-sm" type="number" min={1} value={value} onChange={(event) => onChange(event.target.value)} /></label>
}

function ProjectRow({ video, onDelete, onRetry, onReanalyze }: { video: V1VideoSummaryOut; onDelete: () => void; onRetry: () => void; onReanalyze: () => void }) {
  const job = video.latest_analysis_job
  const status = job?.status ?? 'queued'
  const active = ['queued', 'running'].includes(status)
  const failed = ['failed', 'cancelled'].includes(status)
  const progress = Math.round((job?.progress ?? 0) * 100)
  return (
    <div className="flex flex-col gap-3 py-4 first:pt-1 last:pb-1 sm:flex-row sm:items-center">
      <Link to={job && active ? `/jobs/${job.id}` : `/videos/${video.id}/clips`} className="min-w-0 flex-1">
        <span className="block truncate text-sm font-semibold text-ink-900 hover:text-iris-600">{video.title || video.url || 'Untitled'}</span>
        <span className="numeric mt-1 block text-xs text-ink-500">{formatDuration(video.duration_s)}{video.channel ? ` · ${video.channel}` : ''} · {video.clip_count} clips · {video.preview_ready_count}/{video.clip_count} previews</span>
      </Link>
      <div className="flex flex-wrap items-center gap-2">
        <span className={`badge ${status === 'ready_for_review' ? 'badge-good' : failed ? 'badge-bad' : active ? 'badge-iris' : ''}`}>{active ? `${status} ${progress}%` : status.replaceAll('_', ' ')}</span>
        {failed && <button type="button" className="btn btn-ghost px-3 py-2 text-xs" onClick={onRetry}>Retry</button>}
        {!active && <button type="button" className="btn btn-quiet text-xs" onClick={onReanalyze}>Re-analyze</button>}
        <button type="button" className="btn btn-quiet text-xs" onClick={onDelete}>Delete</button>
      </div>
    </div>
  )
}
