import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { v1, type V1ClipOut, type V1ClipTranscriptOut, type V1VideoSummaryOut } from '../api-v1'
import { ClipMedia } from '../components/ClipMedia'
import { ErrorNote } from '../components/ErrorNote'
import { ScoreRing } from '../components/ScoreRing'
import { formatDuration } from '../format'

type Filter = 'all' | 'kept' | 'discarded'
type DurationFilter = 'all' | 'under30' | '30to60' | 'over60'
type Sort = 'score' | 'time'

export function Review() {
  const { videoId } = useParams()
  const [video, setVideo] = useState<V1VideoSummaryOut | null>(null)
  const [clips, setClips] = useState<V1ClipOut[]>([])
  const [filter, setFilter] = useState<Filter>('all')
  const [duration, setDuration] = useState<DurationFilter>('all')
  const [sort, setSort] = useState<Sort>('score')
  const [error, setError] = useState<Error | null>(null)

  const refresh = useCallback(async () => {
    if (!videoId) return
    const [loadedVideo, loadedClips] = await Promise.all([v1.getVideo(videoId), v1.listClips(videoId)])
    setVideo(loadedVideo)
    setClips(loadedClips)
  }, [videoId])

  useEffect(() => { void refresh().catch((err) => setError(err as Error)) }, [refresh])
  useEffect(() => {
    if (!clips.some((clip) => ['missing', 'queued', 'running'].includes(clip.preview.status))) return
    const timer = window.setInterval(() => void refresh().catch(() => undefined), 2500)
    return () => window.clearInterval(timer)
  }, [clips, refresh])

  const visible = useMemo(() => clips.filter((clip) => {
    if (filter !== 'all' && clip.status !== filter) return false
    if (duration === 'under30' && clip.duration_s >= 30) return false
    if (duration === '30to60' && (clip.duration_s < 30 || clip.duration_s > 60)) return false
    if (duration === 'over60' && clip.duration_s <= 60) return false
    return true
  }).sort((a, b) => sort === 'score' ? b.score - a.score : a.start_s - b.start_s), [clips, filter, duration, sort])

  const changed = (updated: V1ClipOut) => setClips((current) => current.map((clip) => clip.id === updated.id ? updated : clip))
  const previewsReady = clips.filter((clip) => clip.preview.status === 'ready').length

  if (error && clips.length === 0) return <div className="pt-20"><ErrorNote error={error} /><Link className="mt-4 inline-block text-iris-600" to="/">Back to projects</Link></div>

  return (
    <div className="mx-auto max-w-5xl pt-10">
      <p className="eyebrow"><Link to="/" className="hover:text-iris-600">Projects</Link> / Results</p>
      <div className="mt-3 flex flex-col justify-between gap-4 sm:flex-row sm:items-end">
        <div><h1 className="max-w-3xl font-display text-[clamp(1.8rem,4vw,2.8rem)] leading-tight text-ink-900">{video?.title || 'Loading…'}</h1>{video && <p className="numeric mt-2 text-xs text-ink-500">{formatDuration(video.duration_s)} · {video.channel || 'YouTube'} · {clips.length} clips</p>}</div>
        {video && <a href={v1.sourceMediaUrl(video.id)} target="_blank" rel="noreferrer" className="btn btn-ghost text-sm">View original video</a>}
      </div>

      {clips.length > 0 && previewsReady < clips.length && <div className="card mt-6 flex items-center gap-4 p-4"><span className="size-2 animate-pulse rounded-full bg-iris-600" /><div className="flex-1"><p className="text-sm font-semibold">Preparing previews {previewsReady}/{clips.length}</p><div className="meter mt-2"><span style={{ width: `${clips.length ? previewsReady / clips.length * 100 : 0}%` }} /></div></div></div>}
      {error && <div className="mt-5"><ErrorNote error={error} onDismiss={() => setError(null)} /></div>}

      <div className="mt-7 flex flex-wrap items-center gap-2">
        {(['all', 'kept', 'discarded'] as const).map((item) => <button key={item} onClick={() => setFilter(item)} className={`btn text-sm capitalize ${filter === item ? 'btn-primary' : 'btn-ghost'}`}>{item}</button>)}
        <select aria-label="Filter clips by duration" className="field ml-auto w-auto min-w-40 text-sm" value={duration} onChange={(event) => setDuration(event.target.value as DurationFilter)}><option value="all">Any length</option><option value="under30">Under 30 sec</option><option value="30to60">30–60 sec</option><option value="over60">Over 60 sec</option></select>
        <select aria-label="Sort clips" className="field w-auto min-w-36 text-sm" value={sort} onChange={(event) => setSort(event.target.value as Sort)}><option value="score">Sort by score</option><option value="time">Sort by time</option></select>
      </div>

      <div className="mt-7 space-y-8">
        {visible.map((clip) => <ClipResultRow key={clip.id} clip={clip} onChanged={changed} onRefresh={() => void refresh()} onError={setError} />)}
        {visible.length === 0 && <div className="card p-10 text-center text-sm text-ink-500">No clips match these filters.</div>}
      </div>
    </div>
  )
}

function ClipResultRow({ clip, onChanged, onRefresh, onError }: { clip: V1ClipOut; onChanged: (clip: V1ClipOut) => void; onRefresh: () => void; onError: (error: Error) => void }) {
  const [transcript, setTranscript] = useState<V1ClipTranscriptOut | null>(null)
  const [expanded, setExpanded] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)

  useEffect(() => { void v1.clipTranscript(clip.id).then(setTranscript).catch(() => undefined) }, [clip.id, clip.start_s, clip.end_s])

  const setStatus = async (status: 'kept' | 'discarded') => {
    setBusy(status)
    try { onChanged(await v1.patchClip(clip.id, { status })) } catch (err) { onError(err as Error) } finally { setBusy(null) }
  }

  const download = async () => {
    setBusy('download')
    try {
      if (clip.final.status !== 'ready') {
        const job = await v1.renderClip(clip.id, clip.caption_style)
        await waitForJob(job.id)
      }
      const url = await v1.downloadBlob(clip.id)
      const anchor = document.createElement('a')
      anchor.href = url; anchor.download = `${clip.title || 'clip'}.mp4`; anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
      onRefresh()
    } catch (err) { onError(err as Error) } finally { setBusy(null) }
  }

  return (
    <article className="card p-4 sm:p-6">
      <div className="grid gap-6 md:grid-cols-[13rem_minmax(0,1fr)]">
        <ClipMedia artifact={clip.preview} title={clip.title} />
        <div className="min-w-0">
          <div className="flex flex-wrap items-start gap-3"><div><p className="eyebrow">#{clip.rank}</p><h2 className="mt-1 text-lg font-bold text-ink-900">{clip.title}</h2></div><div className="ml-auto"><ScoreRing score={clip.score} size={54} /></div></div>
          <div className="mt-2 flex flex-wrap gap-2"><span className="badge numeric">{formatDuration(clip.start_s)}–{formatDuration(clip.end_s)}</span><span className="badge">{clip.status}</span>{clip.degraded && <span className="badge badge-warn">heuristic</span>}<span className={`badge numeric ${clip.preview.status === 'ready' ? 'badge-good' : clip.preview.status === 'failed' ? 'badge-bad' : 'badge-iris'}`}>preview: {clip.preview.status}</span><span className={`badge numeric ${clip.final.status === 'ready' ? 'badge-good' : clip.final.status === 'failed' ? 'badge-bad' : ''}`}>final: {clip.final.status}</span></div>
          <p className="mt-4 font-display text-lg italic text-ink-900">“{clip.hook}”</p>
          <p className="mt-2 text-sm leading-relaxed text-ink-500">{clip.reason}</p>
          {transcript && <div className="mt-4 rounded-xl bg-paper p-4"><p className={`text-sm leading-relaxed text-ink-700 ${expanded ? '' : 'line-clamp-4'}`}>{transcript.text}</p>{transcript.text.length > 220 && <button className="mt-2 text-xs font-semibold text-iris-600" onClick={() => setExpanded((value) => !value)}>{expanded ? 'Show less' : 'Read transcript'}</button>}</div>}
          <div className="mt-5 flex flex-wrap gap-2">
            <button className="btn btn-ghost text-sm" disabled={busy !== null} onClick={() => void setStatus('kept')}>Keep</button>
            <button className="btn btn-quiet text-sm" disabled={busy !== null} onClick={() => void setStatus('discarded')}>Discard</button>
            <Link className="btn btn-ghost text-sm" to={`/clips/${clip.id}/edit`}>Edit</Link>
            <button className="btn btn-primary text-sm" disabled={busy !== null || ['queued', 'running'].includes(clip.preview.status)} onClick={() => void download()}>{busy === 'download' ? 'Rendering…' : clip.final.status === 'ready' ? 'Download MP4' : 'Render & download'}</button>
          </div>
          {clip.preview.status === 'failed' && <p className="mt-3 text-xs text-signal-bad">Preview failed: {clip.preview.error} — you can still render the final file.</p>}
          {clip.preview.status === 'stale' && <p className="mt-3 text-xs text-signal-warn">This preview predates your latest trim or caption change.</p>}
          {clip.final.status === 'stale' && <p className="mt-3 text-xs text-signal-warn">A final file exists but predates your latest edit — downloading will render a fresh one.</p>}
        </div>
      </div>
    </article>
  )
}

async function waitForJob(jobId: string): Promise<void> {
  for (let attempt = 0; attempt < 240; attempt += 1) {
    const job = await v1.getJob(jobId)
    if (job.status === 'completed') return
    if (job.status === 'failed' || job.status === 'cancelled') throw new Error(job.error || `Render ${job.status}.`)
    await new Promise((resolve) => window.setTimeout(resolve, 1000))
  }
  throw new Error('Render timed out.')
}
