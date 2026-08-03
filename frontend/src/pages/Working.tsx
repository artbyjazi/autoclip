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
import { useJobStream } from '../useJobStream'

const RATIOS = ['9:16', '1:1', '16:9'] as const

/** Human-labelled stage order, mirroring autoclip.pipeline.Stage without the
 * implementation words. */
const STAGES = [
  { key: 'prepare', label: 'Getting the audio' },
  { key: 'transcribe', label: 'Writing down what’s said' },
  { key: 'highlights', label: 'Choosing the moments', manualLabel: 'Cutting your sections' },
  { key: 'reframe', label: 'Framing for vertical' },
  { key: 'captions', label: 'Adding captions' },
  { key: 'export', label: 'Rendering' },
] as const

/**
 * One continuous screen for "start a run" through "here are your clips" — the
 * old split between a progress screen and a review screen was the single
 * biggest thing that made this feel like a developer tool.
 */
export function Working() {
  const { jobId } = useParams()
  const { job: streamedJob, progress } = useJobStream(jobId)

  const [clips, setClips] = useState<Clip[]>([])
  const [styles, setStyles] = useState<CaptionStyle[]>([])
  const [words, setWords] = useState<Word[]>([])
  const [cropPath, setCropPath] = useState<CropPath | null>(null)
  const [wordsDirty, setWordsDirty] = useState(false)
  const [savingWords, setSavingWords] = useState(false)
  const [finetuneId, setFinetuneId] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const job: Job | null = streamedJob

  const done = job?.status === 'done'

  useEffect(() => {
    if (!jobId || !done) return
    void Promise.all([api.listClips(jobId), api.captionStyles()])
      .then(([loadedClips, loadedStyles]) => {
        setClips(loadedClips)
        setStyles(loadedStyles)
      })
      .catch((err) => setError(err as Error))
  }, [jobId, done])

  const selected = useMemo(
    () => clips.find((clip) => clip.id === finetuneId) ?? null,
    [clips, finetuneId],
  )

  useEffect(() => {
    if (!selected) return
    setWordsDirty(false)
    api
      .getClipWords(selected.id)
      .then(setWords)
      .catch(() => setWords([]))
    api
      .getCropPath(selected.id)
      .then(setCropPath)
      .catch(() => setCropPath(null))
  }, [selected?.id])

  const patchClip = useCallback((updated: Clip) => {
    setClips((current) => current.map((clip) => (clip.id === updated.id ? updated : clip)))
  }, [])

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

  const retitle = async (clip: Clip, title: string) => {
    try {
      patchClip(await api.patchClip(clip.id, { title }))
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

  const cancel = async () => {
    if (!job) return
    setCancelling(true)
    try {
      await api.cancelJob(job.id)
    } catch (err) {
      setError(err as Error)
    } finally {
      setCancelling(false)
    }
  }

  const retry = async () => {
    if (!job) return
    try {
      await api.retryJob(job.id)
      window.location.reload()
    } catch (err) {
      setError(err as Error)
    }
  }

  if (!job) {
    return <p className="pt-24 text-sm text-ink-500">Loading…</p>
  }

  if (job.status === 'running' || job.status === 'queued' || job.status === 'failed' || job.status === 'cancelled') {
    return (
      <RunningOrFailed
        job={job}
        progress={progress}
        cancelling={cancelling}
        onCancel={cancel}
        onRetry={retry}
        error={error}
        onDismissError={() => setError(null)}
      />
    )
  }

  // job.status === 'done'
  const activeStyle = styles.find((style) => style.key === selected?.caption_style)

  return (
    <div className="pt-10">
      <div className="flex flex-wrap items-baseline justify-between gap-4 border-b border-ink-800 pb-5">
        <div className="min-w-0">
          <Link to="/" className="eyebrow transition-colors hover:text-sodium-500">
            ← Make Clips
          </Link>
          <h1 className="mt-2 max-w-2xl truncate font-display text-[clamp(1.5rem,3vw,2.25rem)] leading-tight text-ink-100">
            {job.source?.title || 'Untitled'}
          </h1>
        </div>
        <span className="numeric text-xs text-ink-500">{clips.length} clips ready</span>
      </div>

      {error && (
        <div className="mt-6 max-w-3xl">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {clips.length === 0 ? (
        <EmptyState />
      ) : (
        <>
          <div className="mt-8 grid gap-8 sm:grid-cols-2 lg:grid-cols-3">
            {clips.map((clip) => (
              <ClipCard
                key={clip.id}
                clip={clip}
                jobId={job.id}
                style={styles.find((s) => s.key === clip.caption_style)}
                onRetitle={(title) => retitle(clip, title)}
                finetuneOpen={finetuneId === clip.id}
                onToggleFinetune={() =>
                  setFinetuneId((current) => (current === clip.id ? null : clip.id))
                }
              />
            ))}
          </div>

          {finetuneId && selected && (
            <section className="mt-14 grid gap-x-10 gap-y-8 border-t border-ink-800 pt-8 lg:grid-cols-[minmax(0,26rem)_minmax(0,1fr)]">
              <div className="space-y-8">
                <ClipPlayer
                  src={api.mediaUrl(job.id)}
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
              </div>

              <div className="space-y-8">
                <div>
                  <p className="eyebrow">Why this clip</p>
                  <p className="mt-2 max-w-prose text-[0.9375rem] leading-relaxed text-ink-300">
                    {selected.reason || 'No rationale returned for this clip.'}
                  </p>
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
                  <p className="eyebrow border-b border-ink-800 pb-2">Shape</p>
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
              </div>
            </section>
          )}
        </>
      )}

      <div className="mt-14 border-t border-ink-800 pt-6 text-xs text-ink-500">
        Saved to <span className="numeric select-all text-ink-300">{job.output_dir}</span>
      </div>
    </div>
  )
}

function RunningOrFailed({
  job,
  progress,
  cancelling,
  onCancel,
  onRetry,
  error,
  onDismissError,
}: {
  job: Job
  progress: { stage: string; stageProgress: number; overall: number; message: string } | null
  cancelling: boolean
  onCancel: () => void
  onRetry: () => void
  error: Error | null
  onDismissError: () => void
}) {
  const activeIndex = STAGES.findIndex((s) => s.key === job.current_stage)
  const percent = Math.round((progress?.overall ?? job.progress) * 100)

  return (
    <div className="pt-14">
      <div className="rise flex flex-wrap items-baseline justify-between gap-6 border-b border-ink-800 pb-6">
        <div className="min-w-0">
          <p className="eyebrow">
            {job.status === 'failed'
              ? "Didn't finish"
              : job.status === 'cancelled'
                ? 'Cancelled'
                : 'Working'}
          </p>
          <h1 className="mt-2 max-w-3xl truncate font-display text-[clamp(1.75rem,4vw,3rem)] leading-tight text-ink-100">
            {job.source?.title || 'Untitled'}
          </h1>
          {job.source && (
            <p className="numeric mt-2 text-xs text-ink-500">
              {formatDuration(job.source.duration_s)}
              {job.source.width ? ` · ${job.source.width}×${job.source.height}` : ''}
            </p>
          )}
        </div>

        <div className="flex items-baseline gap-8">
          <span className="numeric font-display text-[clamp(2.5rem,6vw,4rem)] leading-none text-sodium-500">
            {percent}
            <span className="text-2xl text-ink-600">%</span>
          </span>
          {(job.status === 'running' || job.status === 'queued') && (
            <button onClick={onCancel} disabled={cancelling} className="btn btn-ghost">
              {cancelling ? 'Cancelling…' : 'Cancel'}
            </button>
          )}
          {(job.status === 'failed' || job.status === 'cancelled') && (
            <button onClick={onRetry} className="btn btn-primary">
              Retry
            </button>
          )}
        </div>
      </div>

      {job.error && (
        <div className="mt-8 max-w-3xl">
          <ErrorNote error={new Error(job.error)} />
          <p className="mt-3 text-xs text-ink-500">
            Retrying picks up where this left off — finished steps aren&rsquo;t redone.
          </p>
        </div>
      )}

      {error && (
        <div className="mt-8 max-w-3xl">
          <ErrorNote error={error} onDismiss={onDismissError} />
        </div>
      )}

      <ol className="mt-12 max-w-3xl">
        {STAGES.map((stage, index) => {
          const failedHere =
            (job.status === 'failed' || job.status === 'cancelled') && index === activeIndex

          const state =
            (job.status as string) === 'done' || (activeIndex >= 0 && index < activeIndex)
              ? 'done'
              : failedHere
                ? 'stopped'
                : index === activeIndex && job.status === 'running'
                  ? 'active'
                  : 'pending'

          const fraction =
            state === 'done' ? 1 : state === 'active' ? (progress?.stageProgress ?? 0) : 0

          const label =
            job.selection_mode === 'manual' && 'manualLabel' in stage
              ? stage.manualLabel
              : stage.label

          return (
            <li
              key={stage.key}
              className="rise grid grid-cols-[2.5rem_1fr] items-start gap-x-5 border-b border-ink-850 py-5"
              style={{ animationDelay: `${index * 55}ms` }}
            >
              <span
                className={[
                  'numeric pt-0.5 text-xs',
                  state === 'done'
                    ? 'text-signal-good'
                    : state === 'stopped'
                      ? 'text-signal-bad'
                      : state === 'active'
                        ? 'text-sodium-500'
                        : 'text-ink-600',
                ].join(' ')}
              >
                {state === 'done' ? '✓' : state === 'stopped' ? '✕' : String(index + 1).padStart(2, '0')}
              </span>

              <div>
                <div className="flex flex-wrap items-baseline justify-between gap-x-4">
                  <span
                    className={[
                      'text-[0.9375rem]',
                      state === 'pending' ? 'text-ink-600' : 'text-ink-100',
                    ].join(' ')}
                  >
                    {label}
                  </span>
                  <span
                    className={`text-xs ${state === 'stopped' ? 'text-signal-bad' : 'text-ink-500'}`}
                  >
                    {state === 'stopped'
                      ? job.status === 'cancelled'
                        ? 'cancelled here'
                        : 'failed here'
                      : state === 'active'
                        ? progress?.message || ''
                        : ''}
                  </span>
                </div>

                <div className="mt-3 h-px w-full bg-ink-800">
                  <div
                    className="h-px origin-left bg-sodium-500 transition-transform duration-500 ease-[cubic-bezier(0.16,1,0.3,1)]"
                    style={{ transform: `scaleX(${fraction})` }}
                  />
                </div>
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}

function ClipCard({
  clip,
  jobId,
  style,
  onRetitle,
  finetuneOpen,
  onToggleFinetune,
}: {
  clip: Clip
  jobId: string
  style: CaptionStyle | undefined
  onRetitle: (title: string) => void
  finetuneOpen: boolean
  onToggleFinetune: () => void
}) {
  const [title, setTitle] = useState(clip.title)
  useEffect(() => setTitle(clip.title), [clip.title])

  const primaryExport = clip.exports[0]

  return (
    <div className="rise space-y-3 border-t border-ink-800 pt-4">
      <ClipPlayer
        src={api.mediaUrl(jobId)}
        startS={clip.start_s}
        endS={clip.end_s}
        words={[]}
        style={style}
        ratio={clip.ratio}
      />

      <input
        className="field text-sm"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onBlur={() => title !== clip.title && onRetitle(title)}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
      />

      <div className="flex items-center justify-between text-xs text-ink-500">
        <span className="numeric">{formatDuration(clip.duration_s)}</span>
        {primaryExport && (
          <a
            href={primaryExport.download_url}
            download
            className="text-sodium-500 underline underline-offset-4"
          >
            Download ({formatBytes(primaryExport.size_bytes)})
          </a>
        )}
      </div>

      <button onClick={onToggleFinetune} className="btn btn-quiet -ml-1">
        {finetuneOpen ? 'Hide fine-tune' : 'Fine-tune'}
      </button>
    </div>
  )
}

function EmptyState() {
  return (
    <div className="max-w-xl pt-20">
      <h2 className="font-display text-3xl text-ink-200">No clips came back.</h2>
      <p className="mt-4 text-sm leading-relaxed text-ink-400">
        Nothing here stood alone well enough to cut — which is a real answer for some
        source material, not necessarily a mistake.
      </p>
      <p className="mt-3 text-sm leading-relaxed text-ink-400">
        Try picking your own sections instead, or widen the length range so shorter
        moments qualify.
      </p>
    </div>
  )
}

