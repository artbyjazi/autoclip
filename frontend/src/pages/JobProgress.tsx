import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { formatDuration } from '../api'
import { ErrorNote } from '../components/ErrorNote'
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
    return <p className="pt-24 text-sm text-ink-500">Loading…</p>
  }

  const activeIndex = STAGES.findIndex((s) => s.key === job.current_stage)
  const percent = Math.round((progress?.overall ?? job.progress) * 100)
  const failed = job.status === 'failed' || job.status === 'cancelled'

  return (
    <div className="pt-14">
      <div className="rise flex flex-wrap items-baseline justify-between gap-6 border-b border-ink-800 pb-6">
        <div className="min-w-0">
          <p className="eyebrow">
            {job.kind} · {job.status}
            {!connected && !TERMINAL_GOOD.has(job.status) && !failed ? ' · reconnecting…' : ''}
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
          {TERMINAL_GOOD.has(job.status) && (
            <button
              onClick={() => navigate(`/videos/${job.source_id}/clips`)}
              className="btn btn-primary"
            >
              Review clips
            </button>
          )}
        </div>
      </div>

      {job.error && (
        <div className="mt-8 max-w-3xl">
          <ErrorNote error={new Error(job.error)} />
          <p className="mt-3 text-xs text-ink-500">
            Retrying resumes from the stage that failed — finished stages are not redone.
          </p>
        </div>
      )}

      <ol className="mt-12 max-w-3xl">
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

          const fraction =
            state === 'done' ? 1 : state === 'active' ? (progress?.stageProgress ?? 0) : 0

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
                    {stage.label}
                  </span>
                  <span
                    className={`text-xs ${state === 'stopped' ? 'text-signal-bad' : 'text-ink-500'}`}
                  >
                    {state === 'stopped'
                      ? job.status === 'cancelled'
                        ? 'cancelled here'
                        : 'failed here'
                      : state === 'active'
                        ? (progress?.message ?? stage.note)
                        : stage.note}
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
