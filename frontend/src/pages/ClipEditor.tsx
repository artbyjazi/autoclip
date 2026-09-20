import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { v1, type V1ClipOut, type V1ClipTranscriptOut, type V1JobOut } from '../api-v1'
import { ClipMedia } from '../components/ClipMedia'
import { ErrorNote } from '../components/ErrorNote'
import { ScoreRing } from '../components/ScoreRing'

const STYLES = ['bold', 'karaoke', 'clean', 'hormozi', 'mrbeast'] as const
const LABELS: Record<string, string> = { hook: 'Hook', retention: 'Retention', clarity: 'Clarity', insight: 'Insight', emotion: 'Emotion', surprise: 'Surprise', energy: 'Energy', standalone_context: 'Standalone', complete_thought: 'Complete thought', payoff: 'Payoff' }

export function ClipEditor() {
  const { clipId } = useParams()
  const [clip, setClip] = useState<V1ClipOut | null>(null)
  const [transcript, setTranscript] = useState<V1ClipTranscriptOut | null>(null)
  const [analysis, setAnalysis] = useState<V1JobOut | null>(null)
  const [title, setTitle] = useState('')
  const [start, setStart] = useState('0')
  const [end, setEnd] = useState('0')
  const [style, setStyle] = useState('bold')
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [error, setError] = useState<Error | null>(null)

  const load = async () => {
    if (!clipId) return
    const [loadedClip, loadedTranscript] = await Promise.all([v1.getClip(clipId), v1.clipTranscript(clipId)])
    setClip(loadedClip); setTranscript(loadedTranscript)
    setTitle(loadedClip.title); setStart(String(loadedClip.start_s)); setEnd(String(loadedClip.end_s)); setStyle(normalizeStyle(loadedClip.caption_style))
    setAnalysis(await v1.getJob(loadedClip.job_id))
  }
  useEffect(() => { void load().catch((err) => setError(err as Error)) }, [clipId]) // eslint-disable-line react-hooks/exhaustive-deps

  const selectedText = useMemo(() => {
    if (!transcript) return ''
    const from = Number(start); const to = Number(end)
    return transcript.words.filter((word) => word.end >= from && word.start <= to).map((word) => word.text).join(' ')
  }, [transcript, start, end])

  const run = async (kind: string, action: () => Promise<void>) => {
    setBusy(kind); setError(null); setMessage('')
    try { await action() } catch (err) { setError(err as Error) } finally { setBusy(null) }
  }

  const save = () => run('save', async () => {
    if (!clip) return
    const from = Number(start); const to = Number(end)
    if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) throw new Error('End must be after start.')
    await saveInline(); setMessage('Changes saved. Generate a fresh preview before downloading.')
  })

  const preview = () => run('preview', async () => {
    if (!clip) return
    await saveInline()
    const job = await v1.previewClip(clip.id, style); await wait(job.id); await load()
    setMessage('Preview updated.')
  })

  const render = () => run('render', async () => {
    if (!clip) return
    await saveInline()
    const job = await v1.renderClip(clip.id, style); await wait(job.id); await load()
    setMessage('Final render ready.')
  })

  const saveInline = async (): Promise<V1ClipOut | null> => {
    if (!clip) return null
    const from = Number(start); const to = Number(end)
    if (!Number.isFinite(from) || !Number.isFinite(to) || to <= from) throw new Error('End must be after start.')
    const patch: { title?: string; start_s?: number; end_s?: number; caption_style?: string } = {}
    if (title !== clip.title) patch.title = title
    if (from !== clip.start_s) patch.start_s = from
    if (to !== clip.end_s) patch.end_s = to
    if (style !== normalizeStyle(clip.caption_style)) patch.caption_style = style
    if (Object.keys(patch).length === 0) return clip
    const updated = await v1.patchClip(clip.id, patch)
    setClip(updated)
    return updated
  }

  const download = () => run('download', async () => {
    if (!clip) return
    const updated = await saveInline()
    if (!updated || updated.final.status !== 'ready') { const job = await v1.renderClip(clip.id, style); await wait(job.id) }
    const url = await v1.downloadBlob(clip.id); const anchor = document.createElement('a')
    anchor.href = url; anchor.download = `${title || 'clip'}.mp4`; anchor.click(); window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    await load()
  })

  if (!clip || !transcript) return <div className="card mx-auto mt-16 max-w-xl p-10 text-center text-sm text-ink-500">Loading editor…</div>
  const first = transcript.words[0]?.start ?? clip.start_s
  const last = transcript.words.at(-1)?.end ?? clip.end_s

  return (
    <div className="mx-auto max-w-6xl pt-10">
      <p className="eyebrow"><Link to={`/videos/${analysis?.source_id}/clips`} className="hover:text-iris-600">Results</Link> / Edit clip</p>
      <div className="mt-3 flex flex-wrap items-end justify-between gap-3"><h1 className="font-display text-3xl text-ink-900">{clip.title}</h1>{analysis && <a className="btn btn-ghost text-sm" target="_blank" rel="noreferrer" href={v1.sourceMediaUrl(analysis.source_id)}>View original</a>}</div>
      {error && <div className="mt-5"><ErrorNote error={error} onDismiss={() => setError(null)} /></div>}
      {message && <p className="mt-5 rounded-xl bg-signal-good-bg p-3 text-sm text-signal-good">{message}</p>}

      <div className="mt-7 grid gap-7 lg:grid-cols-[minmax(18rem,4fr)_minmax(0,7fr)]">
        <div className="lg:sticky lg:top-24 lg:self-start"><ClipMedia artifact={clip.preview} title={clip.title} /><div className="mt-3 flex flex-wrap gap-2"><button className="btn btn-ghost text-sm" disabled={busy !== null} onClick={() => void preview()}>{busy === 'preview' ? 'Rendering preview…' : 'Update preview'}</button><button className="btn btn-primary text-sm" disabled={busy !== null} onClick={() => void download()}>{busy === 'download' ? 'Rendering final…' : 'Download final'}</button></div>{clip.preview.status === 'stale' && <p className="mt-2 text-xs text-signal-warn">This preview predates your latest edit.</p>}{clip.final.status === 'stale' && <p className="mt-2 text-xs text-signal-warn">The final file predates your latest edit — the next download renders a fresh 1080×1920 file.</p>}{clip.final.status === 'ready' && clip.preview.status !== 'stale' && <p className="numeric mt-2 text-xs text-ink-500">Final ready · {clip.final.export?.style} · {Math.round((clip.final.export?.size_bytes ?? 0) / 1048576)} MB</p>}</div>

        <div className="space-y-6">
          <section className="card p-5"><div className="flex items-center gap-4"><ScoreRing score={clip.score} size={64} /><div><p className="font-display text-lg italic">“{clip.hook}”</p><p className="mt-1 text-sm text-ink-500">{clip.reason}</p></div></div><div className="mt-5 grid gap-3 sm:grid-cols-2">{Object.entries(clip.score_breakdown).map(([key, value]) => <div key={key}><div className="flex justify-between text-xs"><span className="text-ink-500">{LABELS[key] ?? key}</span><b>{value}</b></div><div className="meter mt-1"><span style={{ width: `${value}%` }} /></div></div>)}</div></section>

          <section className="card p-5"><h2 className="text-sm font-bold">Trim from transcript</h2><p className="mt-1 text-xs text-ink-500">Move either edge; captions snap to word timestamps when saved.</p><div className="mt-5 space-y-4"><label className="block"><span className="eyebrow">Start</span><input className="mt-2 w-full accent-iris-600" type="range" min={first} max={last} step="0.05" value={start} onChange={(event) => setStart(String(Math.min(Number(event.target.value), Number(end) - 0.1)))} /></label><label className="block"><span className="eyebrow">End</span><input className="mt-2 w-full accent-iris-600" type="range" min={first} max={last} step="0.05" value={end} onChange={(event) => setEnd(String(Math.max(Number(event.target.value), Number(start) + 0.1)))} /></label></div><div className="mt-4 grid grid-cols-2 gap-3"><label><span className="eyebrow">Start (s)</span><input className="field numeric mt-1 text-sm" type="number" step="0.1" value={start} onChange={(event) => setStart(event.target.value)} /></label><label><span className="eyebrow">End (s)</span><input className="field numeric mt-1 text-sm" type="number" step="0.1" value={end} onChange={(event) => setEnd(event.target.value)} /></label></div><div className="mt-4 max-h-52 overflow-auto rounded-xl bg-paper p-4 text-sm leading-relaxed text-ink-700">{selectedText}</div></section>

          <section className="card p-5"><label><span className="eyebrow">Title</span><input className="field mt-2" value={title} onChange={(event) => setTitle(event.target.value)} /></label><h2 className="eyebrow mt-5">Caption style</h2><div className="mt-2 grid grid-cols-2 gap-2 sm:grid-cols-5">{STYLES.map((item) => <button key={item} aria-pressed={style === item} onClick={() => setStyle(item)} className={`rounded-xl border p-2 text-left ${style === item ? 'border-iris-600 bg-iris-50' : 'border-line'}`}><span className="block aspect-[9/16] rounded-lg bg-ink-900 pt-8 text-center font-display text-xs font-bold uppercase text-white">{item === 'karaoke' ? <><i className="text-iris-500">Keep</i> watching</> : 'Keep watching'}</span><span className="mt-2 block text-xs font-semibold capitalize">{item}</span></button>)}</div><div className="mt-5 flex flex-wrap gap-2"><button className="btn btn-ghost" disabled={busy !== null} onClick={() => void save()}>{busy === 'save' ? 'Saving…' : 'Save changes'}</button><button className="btn btn-primary" disabled={busy !== null} onClick={() => void render()}>{busy === 'render' ? 'Rendering…' : 'Render final'}</button></div></section>
        </div>
      </div>
    </div>
  )
}

function normalizeStyle(style: string): string { return style === 'bold_pop' ? 'bold' : style === 'karaoke_fill' ? 'karaoke' : STYLES.includes(style as (typeof STYLES)[number]) ? style : 'bold' }
async function wait(jobId: string) { for (let i = 0; i < 240; i += 1) { const job = await v1.getJob(jobId); if (job.status === 'completed') return; if (['failed', 'cancelled'].includes(job.status)) throw new Error(job.error || `Job ${job.status}.`); await new Promise((resolve) => window.setTimeout(resolve, 1000)) } throw new Error('Render timed out.') }
