import { useEffect } from 'react'
import { useNavigate, useParams } from 'react-router-dom'

import { v1 } from '../api-v1'
import { ErrorNote } from '../components/ErrorNote'
import { formatDuration } from '../format'
import { useV1JobStream, type V1StageProgress } from '../useV1JobStream'
import type { V1JobOut } from '../api-v1'

/** v1 current_stage values in pipeline order, per docs/HANDOFF_OPENCODE.md. */
const STAGES = [
  { key: 'download', label: 'Download', note: 'Fetching the source video' },
  { key: 'prepare', label: 'Prepare audio', note: 'Extracting mono 16 kHz audio' },
  { key: 'transcribe', label: 'Transcribe', note: 'Word-level timing, cloud first' },
  { key: 'highlights', label: 'Find highlights', note: 'Candidates, AI analysis and ranking' },
  { key: 'reframe', label: 'Reframe', note: 'Tracking the speaker into vertical' },
  { key: 'captions', label: 'Captions', note: 'Validating the selected caption style' },
  { key: 'ready_for_review', label: 'Ready', note: 'Clips ranked, awaiting review' },
] as const

const TERMINAL_GOOD = new Set(['ready_for_review', 'completed'])

export function JobProgress() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const { job, progress, connected, setJob } = useV1JobStream(jobId)

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

  const cancel = async () => { if (jobId) setJob(await v1.cancelJob(jobId)) }
  const retry = async () => { if (jobId) setJob(await v1.retryJob(jobId)) }

  return <JobProgressContent job={job} progress={progress} connected={connected} onCancel={cancel} onRetry={retry} onReview={() => navigate(`/videos/${job.source_id}/clips`)} />
}

function JobProgressContent({ job, progress, connected, onCancel, onRetry, onReview }: {
  job: V1JobOut
  progress: V1StageProgress | null
  connected: boolean
  onCancel: () => Promise<void>
  onRetry: () => Promise<void>
  onReview: () => void
}) {
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
          <button type="button" onClick={onReview} className="btn btn-primary mt-6">
            Review clips
          </button>
        )}
        {!TERMINAL_GOOD.has(job.status) && !failed && <button type="button" onClick={() => void onCancel()} className="btn btn-ghost mt-6">Cancel</button>}
        {failed && <button type="button" onClick={() => void onRetry()} className="btn btn-primary mt-6">Retry from {job.current_stage || 'last stage'}</button>}
      </div>

      {job.error && (
        <div className="card mt-6 border-signal-bad p-5">
          <ErrorNote error={new Error(job.error)} />
          <p className="mt-3 text-xs text-ink-500">
            Retrying resumes from the stage that failed — finished stages are not redone.
          </p>
        </div>
      )}

      <StageList job={job} progress={progress} />
    </div>
  )
}

function StageList({ job, progress }: { job: V1JobOut; progress: V1StageProgress | null }) {
  const activeIndex = STAGES.findIndex((stage) => stage.key === job.current_stage)
  const failed = job.status === 'failed' || job.status === 'cancelled'
  return <ol className="card mt-6 p-2">{STAGES.map((stage, index) => {
    const state = stageState(job.status, activeIndex, index, failed)
    return <li key={stage.key} className={`flex items-center gap-4 rounded-xl px-4 py-3 ${state === 'active' ? 'bg-iris-50' : ''}`}>
      <span className={`numeric flex size-7 shrink-0 items-center justify-center rounded-full text-xs font-bold ${stageBadgeClass(state)}`}>
        {state === 'done' ? '✓' : state === 'stopped' ? '✕' : index + 1}
      </span>
      <div className="min-w-0 flex-1">
        <p className={`text-sm font-semibold ${state === 'pending' ? 'text-ink-400' : 'text-ink-900'}`}>{stage.label}</p>
        <p className="truncate text-xs text-ink-500">{state === 'active' ? (progress?.message ?? stage.note) : stage.note}</p>
      </div>
    </li>
  })}</ol>
}

type StageState = 'done' | 'stopped' | 'active' | 'pending'

function stageState(status: string, activeIndex: number, index: number, failed: boolean): StageState {
  if (TERMINAL_GOOD.has(status) || (activeIndex >= 0 && index < activeIndex)) return 'done'
  if (failed && index === activeIndex) return 'stopped'
  if (index === activeIndex && status === 'running') return 'active'
  return 'pending'
}

function stageBadgeClass(state: StageState): string {
  if (state === 'done') return 'bg-signal-good-bg text-signal-good'
  if (state === 'stopped') return 'bg-signal-bad-bg text-signal-bad'
  if (state === 'active') return 'bg-iris-600 text-white'
  return 'bg-paper text-ink-400'
}
