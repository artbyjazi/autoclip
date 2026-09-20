import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { ErrorNote } from '../components/ErrorNote'
import { formatDuration } from '../format'
import { useV1JobStream } from '../useV1JobStream'

/** v1 current_stage values in pipeline order, per docs/HANDOFF_OPENCODE.md. */
const STAGES = [
  { key: 'download', label: 'Download', note: 'Fetching the source video' },
  { key: 'audio', label: 'Audio', note: 'Extracting mono 16 kHz audio' },
  { key: 'transcription', label: 'Transcribe', note: 'Word-level timing, cloud first' },
  { key: 'candidates', label: 'Candidates', note: 'Heuristic shortlist, top 20' },
  { key: 'analysis', label: 'Analysis', note: 'LLM evaluation with failover' },
  { key: 'ranking', label: 'Ranking', note: 'Virality scores and dedup' },
  { key: 'reframe', label: 'Reframe', note: 'Tracking the speaker into vertical' },
  { key: 'preview', label: 'Preview', note: '540×960 lightweight render' },
  { key: 'render', label: 'Render', note: '1080×1920 final render' },
  { key: 'ready_for_review', label: 'Ready', note: 'Clips ranked, awaiting review' },
] as const

const TERMINAL_GOOD = new Set(['ready_for_review', 'completed'])

export function JobProgress() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const { job, progress, connected } = useV1JobStream(jobId)

  useEffect(() => {
    if (job && TERMINAL_GOOD.has(job.status) && job.kind === 'analysis') {
      // A short hold so the completed state is legible rather than a flash.
      const timer = setTimeout(() => navigate(`/videos/${job.source_id}/clips`), 900)
      return () => clearTimeout(timer)
    }
  }, [job?.status, job?.source_id, navigate])

  if (!job) {
    return (
      <div className="card mx-auto mt-16 max-w-xl p-10 text-center">
        <p className="text-sm text-ink-500">Loading job…</p>
      </div>
    )
  }

  const activeIndex = STAGES.findIndex((s) => s.key === job.current_stage)
  const percent = Math.round((progress?.overall ?? job.progress) * 100)
  const failed = job.status === 'failed' || job.status === 'cancelled'

  return (
    <div className="mx-auto max-w-2xl pt-10">
      <div className="rise card p-6 text-center sm:p-10">
        <p className="eyebrow">
          {job.kind} · {job.status}
          {!connected && !TERMINAL_GOOD.has(job.status) && !failed ? ' · reconnecting…' : ''}
        </p>
        <h1 className="mx-auto mt-3 max-w-xl truncate font-display text-[clamp(1.5rem,4vw,2.5rem)] leading-tight text-ink-900">
          {job.source?.title || 'Untitled'}
        </h1>
        {job.source && (
          <p className="numeric mt-2 text-xs text-ink-500">
            {formatDuration(job.source.duration_s)}
            {job.source.width ? ` · ${job.source.width}×${job.source.height}` : ''}
          </p>
        )}

        <p className="numeric mt-6 font-display text-[clamp(3.5rem,10vw,5.5rem)] leading-none text-iris-600">
          {percent}
          <span className="text-3xl text-ink-400">%</span>
        </p>
        <div className="meter mx-auto mt-4 max-w-md">
          <span className="sweep" style={{ width: `${percent}%` }} />
        </div>

        {TERMINAL_GOOD.has(job.status) && (
          <button onClick={() => navigate(`/videos/${job.source_id}/clips`)} className="btn btn-primary mt-6">
            Review clips
          </button>
        )}
      </div>

      {job.error && (
        <div className="card mt-6 border-signal-bad p-5">
          <ErrorNote error={new Error(job.error)} />
          <p className="mt-3 text-xs text-ink-500">
            Retrying resumes from the stage that failed — finished stages are not redone.
          </p>
        </div>
      )}

      <ol className="card mt-6 p-2">
        {STAGES.map((stage, index) => {
          const failedHere = failed && index === activeIndex
          const state =
            TERMINAL_GOOD.has(job.status) || (activeIndex >= 0 && index < activeIndex)
              ? 'done'
              : failedHere
                ? 'stopped'
                : index === activeIndex && job.status === 'running'
                  ? 'active'
                  : 'pending'

          return (
            <li
              key={stage.key}
              className={[
                'flex items-center gap-4 rounded-xl px-4 py-3',
                state === 'active' ? 'bg-iris-50' : '',
              ].join(' ')}
            >
              <span
                className={[
                  'numeric flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-bold',
                  state === 'done'
                    ? 'bg-signal-good-bg text-signal-good'
                    : state === 'stopped'
                      ? 'bg-signal-bad-bg text-signal-bad'
                      : state === 'active'
                        ? 'bg-iris-600 text-white'
                        : 'bg-paper text-ink-400',
                ].join(' ')}
              >
                {state === 'done' ? '✓' : state === 'stopped' ? '✕' : index + 1}
              </span>
              <div className="min-w-0 flex-1">
                <p className={['text-sm font-semibold', state === 'pending' ? 'text-ink-400' : 'text-ink-900'].join(' ')}>
                  {stage.label}
                </p>
                <p className="truncate text-xs text-ink-500">
                  {state === 'active' ? (progress?.message ?? stage.note) : stage.note}
                </p>
              </div>
            </li>
          )
        })}
      </ol>
    </div>
  )
}
