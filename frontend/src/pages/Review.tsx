import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import {
  api,
  formatBytes,
  formatDuration,
  type CaptionStyle,
  type Clip,
  type CropPath,
  type Job,
  type Word,
} from '../api'
import { CaptionEditor } from '../components/CaptionEditor'
import { ClipPlayer } from '../components/ClipPlayer'
import { ErrorNote } from '../components/ErrorNote'
import { TrimBar } from '../components/TrimBar'

const RATIOS = ['9:16', '1:1', '16:9'] as const

export function Review() {
  const { jobId } = useParams()
  const [job, setJob] = useState<Job | null>(null)
  const [clips, setClips] = useState<Clip[]>([])
  const [styles, setStyles] = useState<CaptionStyle[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [words, setWords] = useState<Word[]>([])
  const [cropPath, setCropPath] = useState<CropPath | null>(null)
  const [wordsDirty, setWordsDirty] = useState(false)
  const [savingWords, setSavingWords] = useState(false)
  const [exporting, setExporting] = useState<Set<string>>(new Set())
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    if (!jobId) return
    void Promise.all([api.getJob(jobId), api.listClips(jobId), api.captionStyles()])
      .then(([loadedJob, loadedClips, loadedStyles]) => {
        setJob(loadedJob)
        setClips(loadedClips)
        setStyles(loadedStyles)
        setSelectedId((current) => current ?? loadedClips[0]?.id ?? null)
      })
      .catch((err) => setError(err as Error))
  }, [jobId])

  const selected = useMemo(
    () => clips.find((clip) => clip.id === selectedId) ?? null,
    [clips, selectedId],
  )

  useEffect(() => {
    if (!selected) return
    setWordsDirty(false)
    api
      .getClipWords(selected.id)
      .then(setWords)
      .catch(() => setWords([]))
    // 404 is expected for audio-only sources and jobs that never reframed; the
    // player falls back to a centre crop, matching what the renderer does.
    api
      .getCropPath(selected.id)
      .then(setCropPath)
      .catch(() => setCropPath(null))
  }, [selected?.id])

  const patchClip = useCallback((updated: Clip) => {
    setClips((current) => current.map((clip) => (clip.id === updated.id ? updated : clip)))
  }, [])

  const setStatus = async (clip: Clip, status: Clip['status']) => {
    try {
      patchClip(await api.patchClip(clip.id, { status }))
    } catch (err) {
      setError(err as Error)
    }
  }

  const commitTrim = async (start: number, end: number) => {
    if (!selected) return
    try {
      patchClip(await api.patchClip(selected.id, { start_s: start, end_s: end }))
      setWords(await api.getClipWords(selected.id))
    } catch (err) {
      setError(err as Error)
    }
  }

  const saveWords = async () => {
    if (!selected) return
    setSavingWords(true)
    try {
      patchClip(await api.patchCaptions(selected.id, { words }))
      setWordsDirty(false)
    } catch (err) {
      setError(err as Error)
    } finally {
      setSavingWords(false)
    }
  }

  const setStyle = async (clip: Clip, styleKey: string) => {
    try {
      patchClip(await api.patchCaptions(clip.id, { caption_style: styleKey }))
    } catch (err) {
      setError(err as Error)
    }
  }

  const setRatio = async (clip: Clip, ratio: string) => {
    try {
      patchClip(await api.patchCaptions(clip.id, { ratio }))
    } catch (err) {
      setError(err as Error)
    }
  }

  const exportClip = async (clip: Clip) => {
    setExporting((current) => new Set(current).add(clip.id))
    setError(null)
    try {
      // Auto-save edited caption words so users don't accidentally export stale text.
      if (clip.id === selectedId && wordsDirty) {
        patchClip(await api.patchCaptions(clip.id, { words }))
        setWordsDirty(false)
      }
      await api.exportClip(clip.id, clip.ratio, clip.caption_style)
      patchClip(await api.getClip(clip.id))
    } catch (err) {
      setError(err as Error)
    } finally {
      setExporting((current) => {
        const next = new Set(current)
        next.delete(clip.id)
        return next
      })
    }
  }

  const exportKept = async () => {
    const targets = clips.filter((clip) => clip.status === 'kept')
    for (const clip of targets) await exportClip(clip)
  }

  const keptCount = clips.filter((clip) => clip.status === 'kept').length
  const activeStyle = styles.find((style) => style.key === selected?.caption_style)

  if (error && clips.length === 0) {
    return (
      <div className="max-w-3xl pt-24">
        <ErrorNote error={error} />
      </div>
    )
  }

  if (!job) return <p className="pt-24 text-sm text-ink-500">Loading…</p>

  return (
    <div className="pt-10">
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-ink-800 pb-5">
        <div className="min-w-0">
          <Link to="/" className="eyebrow transition-colors hover:text-sodium-500">
            ← All jobs
          </Link>
          <h1 className="mt-2 max-w-2xl truncate font-display text-[clamp(1.5rem,3vw,2.25rem)] leading-tight text-ink-100">
            {job.source?.title || 'Untitled'}
          </h1>
        </div>

        <div className="flex items-baseline gap-6">
          <span className="numeric text-xs text-ink-500">
            {clips.length} clips · {keptCount} kept
          </span>
          <button onClick={exportKept} disabled={keptCount === 0} className="btn btn-primary">
            Export kept
          </button>
        </div>
      </div>

      {error && (
        <div className="mt-6 max-w-3xl">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {clips.length === 0 ? (
        <EmptyState />
      ) : (
        <div className="mt-8 grid gap-x-10 gap-y-10 lg:grid-cols-[minmax(0,20rem)_minmax(0,26rem)_minmax(0,1fr)]">
          {/* Ranked list */}
          <section className="lg:max-h-[76vh] lg:overflow-y-auto lg:pr-1">
            <p className="eyebrow border-b border-ink-800 pb-2">Ranked</p>
            <ul>
              {clips.map((clip, index) => (
                <li key={clip.id}>
                  <ClipRow
                    clip={clip}
                    index={index}
                    selected={clip.id === selectedId}
                    onSelect={() => setSelectedId(clip.id)}
                    onKeep={() =>
                      setStatus(clip, clip.status === 'kept' ? 'candidate' : 'kept')
                    }
                    onDiscard={() =>
                      setStatus(clip, clip.status === 'discarded' ? 'candidate' : 'discarded')
                    }
                  />
                </li>
              ))}
            </ul>
          </section>

          {/* Player + trim */}
          <section className="space-y-8">
            {selected && jobId && (
              <>
                <ClipPlayer
                  src={api.mediaUrl(jobId)}
                  startS={selected.start_s}
                  endS={selected.end_s}
                  words={words}
                  style={activeStyle}
                  ratio={selected.ratio}
                  cropPath={cropPath}
                />
                <TrimBar
                  words={words}
                  startS={selected.start_s}
                  endS={selected.end_s}
                  originalStart={selected.start_s}
                  originalEnd={selected.end_s}
                  onCommit={commitTrim}
                />
              </>
            )}
          </section>

          {/* Editor + style */}
          <section className="space-y-10">
            {selected && (
              <>
                <div>
                  <p className="eyebrow">Why this clip</p>
                  <p className="mt-2 max-w-prose text-[0.9375rem] leading-relaxed text-ink-300">
                    {selected.reason || 'No rationale returned for this clip.'}
                  </p>
                  {selected.hook && (
                    <p className="mt-3 border-l-2 border-ink-700 pl-3 font-display text-lg italic text-ink-200">
                      “{selected.hook}”
                    </p>
                  )}
                </div>

                <CaptionEditor
                  words={words}
                  onChange={(next) => {
                    setWords(next)
                    setWordsDirty(true)
                  }}
                  onSave={saveWords}
                  saving={savingWords}
                  dirty={wordsDirty}
                />

                <div>
                  <p className="eyebrow border-b border-ink-800 pb-2">Caption style</p>
                  <div className="mt-3 space-y-1">
                    {styles.map((style) => (
                      <button
                        key={style.key}
                        onClick={() => setStyle(selected, style.key)}
                        className={[
                          'block w-full border-l-2 py-2 pl-3 text-left transition-colors duration-200',
                          style.key === selected.caption_style
                            ? 'border-sodium-500 bg-ink-850/60'
                            : 'border-transparent hover:border-ink-700 hover:bg-ink-850/30',
                        ].join(' ')}
                      >
                        <span className="text-sm text-ink-100">{style.label}</span>
                        <span className="mt-0.5 block text-xs leading-snug text-ink-500">
                          {style.description}
                        </span>
                      </button>
                    ))}
                  </div>
                </div>

                <div>
                  <p className="eyebrow border-b border-ink-800 pb-2">Aspect ratio</p>
                  <div className="mt-3 flex gap-2">
                    {RATIOS.map((ratio) => (
                      <button
                        key={ratio}
                        onClick={() => setRatio(selected, ratio)}
                        className={[
                          'numeric btn',
                          ratio === selected.ratio ? 'btn-primary' : 'btn-ghost',
                        ].join(' ')}
                      >
                        {ratio}
                      </button>
                    ))}
                  </div>
                </div>

                <div className="border-t border-ink-800 pt-6">
                  <button
                    onClick={() => exportClip(selected)}
                    disabled={exporting.has(selected.id)}
                    className="btn btn-primary w-full"
                  >
                    {exporting.has(selected.id) ? 'Rendering…' : 'Export this clip'}
                  </button>

                  {selected.exports.length > 0 && (
                    <ul className="mt-4 space-y-2">
                      {selected.exports.map((record) => (
                        <li
                          key={record.id}
                          className="flex items-baseline justify-between gap-3 text-xs"
                        >
                          <a
                            href={record.download_url}
                            download
                            className="truncate text-sodium-500 underline underline-offset-4"
                          >
                            {record.ratio} · {record.style}
                          </a>
                          <span className="numeric shrink-0 text-ink-600">
                            {formatBytes(record.size_bytes)}
                          </span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      )}
    </div>
  )
}

function ClipRow({
  clip,
  index,
  selected,
  onSelect,
  onKeep,
  onDiscard,
}: {
  clip: Clip
  index: number
  selected: boolean
  onSelect: () => void
  onKeep: () => void
  onDiscard: () => void
}) {
  return (
    <div
      className={[
        'rise group grid cursor-pointer grid-cols-[2.25rem_1fr] gap-x-3 border-b border-ink-850 py-3 pl-2 transition-colors duration-200',
        selected ? 'bg-ink-850/70' : 'hover:bg-ink-850/35',
        clip.status === 'discarded' ? 'opacity-40' : '',
      ].join(' ')}
      style={{ animationDelay: `${Math.min(index, 10) * 35}ms` }}
      onClick={onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter') onSelect()
      }}
    >
      {/* The score is the thing you scan by, so it gets the display face. */}
      <span
        className={[
          'numeric self-start font-display text-2xl leading-none',
          clip.score >= 85 ? 'text-sodium-500' : selected ? 'text-ink-200' : 'text-ink-500',
        ].join(' ')}
      >
        {clip.score}
      </span>

      <div className="min-w-0">
        <p className="truncate text-[0.9375rem] leading-snug text-ink-100">
          {clip.title || 'Untitled clip'}
        </p>
        <div className="mt-1 flex items-baseline gap-3">
          <span className="numeric text-xs text-ink-500">{formatDuration(clip.duration_s)}</span>
          {clip.user_trimmed && <span className="text-xs text-ink-600">trimmed</span>}
          {clip.exports.length > 0 && (
            <span className="text-xs text-signal-good">exported</span>
          )}

          {/* Secondary actions appear on hover or when the row is current —
              progressive disclosure keeps the scan column clean. */}
          <span
            className={[
              'ml-auto flex gap-3 pr-2 transition-opacity duration-200',
              selected ? 'opacity-100' : 'opacity-0 group-hover:opacity-100',
            ].join(' ')}
          >
            <button
              onClick={(e) => {
                e.stopPropagation()
                onKeep()
              }}
              className={`text-xs ${clip.status === 'kept' ? 'text-signal-good' : 'text-ink-400 hover:text-ink-100'}`}
            >
              {clip.status === 'kept' ? 'kept' : 'keep'}
            </button>
            <button
              onClick={(e) => {
                e.stopPropagation()
                onDiscard()
              }}
              className="text-xs text-ink-400 hover:text-signal-bad"
            >
              {clip.status === 'discarded' ? 'undo' : 'drop'}
            </button>
          </span>
        </div>
      </div>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="max-w-xl pt-20">
      <h2 className="font-display text-3xl text-ink-200">No clips came back.</h2>
      <p className="mt-4 text-sm leading-relaxed text-ink-400">
        The model found nothing self-contained enough to stand alone — which is a real
        answer for some source material, not necessarily a failure.
      </p>
      <p className="mt-3 text-sm leading-relaxed text-ink-400">
        If you expected clips, try a larger provider model, or widen the length range so
        shorter moments qualify.
      </p>
    </div>
  )
}
