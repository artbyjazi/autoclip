import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  api,
  formatDuration,
  type CaptionStyle,
  type FolderListing,
  type Job,
  type ProviderStatus,
  type Segment,
  type Settings as SettingsData,
} from '../api'
import { ErrorNote } from '../components/ErrorNote'
import { formatTimecode, parseTimecode } from '../time'

type ClipLength = 'short' | 'standard' | 'long'
type Quality = 'fast' | 'balanced' | 'best'

const LENGTH_PRESETS: Record<ClipLength, { min: number; max: number; label: string }> = {
  short: { min: 15, max: 30, label: 'Short (15–30s)' },
  standard: { min: 20, max: 90, label: 'Standard (20–90s)' },
  long: { min: 60, max: 180, label: 'Long (60–180s)' },
}

const QUALITY_MODEL: Record<Quality, string> = {
  fast: 'tiny',
  balanced: 'small',
  best: 'large-v3',
}

const RATIOS: { value: '9:16' | '1:1' | '16:9'; label: string }[] = [
  { value: '9:16', label: 'Vertical (TikTok, Shorts, Reels)' },
  { value: '1:1', label: 'Square' },
  { value: '16:9', label: 'Widescreen' },
]

interface DraftSegment {
  key: number
  start: string
  end: string
  title: string
}

let segmentKeySeq = 0
const newSegment = (): DraftSegment => ({ key: segmentKeySeq++, start: '', end: '', title: '' })

export function Make() {
  const navigate = useNavigate()

  const [url, setUrl] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const [selectionMode, setSelectionMode] = useState<'auto' | 'manual'>('auto')
  const [clipCount, setClipCount] = useState(5)
  const [clipLength, setClipLength] = useState<ClipLength>('standard')
  const [segments, setSegments] = useState<DraftSegment[]>([newSegment()])

  const [useRange, setUseRange] = useState(false)
  const [rangeStart, setRangeStart] = useState('')
  const [rangeEnd, setRangeEnd] = useState('')

  const [settings, setSettings] = useState<SettingsData | null>(null)
  const [outputDir, setOutputDir] = useState('')
  const [folderModalOpen, setFolderModalOpen] = useState(false)

  const [styleOpen, setStyleOpen] = useState(false)
  const [ratio, setRatio] = useState<'9:16' | '1:1' | '16:9'>('9:16')
  const [captionStyle, setCaptionStyle] = useState('bold_pop')
  const [captionStyles, setCaptionStyles] = useState<CaptionStyle[]>([])

  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [provider, setProvider] = useState('')
  const [quality, setQuality] = useState<Quality>('balanced')
  const [multipleSpeakers, setMultipleSpeakers] = useState(false)
  const [providers, setProviders] = useState<ProviderStatus[]>([])

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)
  const [jobs, setJobs] = useState<Job[]>([])
  const [clipCounts, setClipCounts] = useState<Record<string, number>>({})

  useEffect(() => {
    api.listJobs(8).then(setJobs).catch(() => undefined)
    api.captionStyles().then(setCaptionStyles).catch(() => undefined)
    api.providerStatus().then(setProviders).catch(() => undefined)
    api
      .getSettings()
      .then((loaded) => {
        setSettings(loaded)
        setOutputDir(loaded.export.output_dir)
        setRatio((loaded.export.ratio as '9:16' | '1:1' | '16:9') || '9:16')
        setCaptionStyle(loaded.export.caption_style || 'bold_pop')
      })
      .catch(() => undefined)
  }, [])

  // Clip counts are fetched lazily, only for jobs that finished, so the Recent
  // list can say "Ready — 6 clips" instead of just "Ready".
  useEffect(() => {
    const done = jobs.filter((j) => j.status === 'done' && !(j.id in clipCounts))
    if (done.length === 0) return
    void Promise.all(
      done.map((j) => api.listClips(j.id).then((clips) => [j.id, clips.length] as const)),
    ).then((pairs) => {
      setClipCounts((current) => {
        const next = { ...current }
        for (const [id, count] of pairs) next[id] = count
        return next
      })
    })
  }, [jobs, clipCounts])

  // ---- Manual section list -------------------------------------------------

  const segmentErrors = useMemo(() => parseSegments(segments), [segments])

  const setSegmentField = (key: number, field: 'start' | 'end' | 'title', value: string) => {
    setSegments((current) => current.map((s) => (s.key === key ? { ...s, [field]: value } : s)))
  }

  const removeSegment = (key: number) => {
    setSegments((current) => current.filter((s) => s.key !== key))
  }

  const totalManualSeconds = segmentErrors.parsed.reduce(
    (sum, s) => sum + (s ? s.end_s - s.start_s : 0),
    0,
  )

  // ---- Range (download-only) ------------------------------------------------

  const rangeStartS = rangeStart.trim() ? parseTimecode(rangeStart) : null
  const rangeEndS = rangeEnd.trim() ? parseTimecode(rangeEnd) : null
  const rangeError = useRange
    ? !rangeStart.trim() || !rangeEnd.trim()
      ? 'Give both a start and an end.'
      : rangeStartS === null || rangeEndS === null
        ? 'Use a plain number of seconds, m:ss, or h:mm:ss.'
        : rangeEndS <= rangeStartS
          ? 'The end must come after the start.'
          : null
    : null

  // ---- Validation ------------------------------------------------------------

  const disabledReason = useMemo(() => {
    if (busy) return null
    if (!url.trim() && !file) return 'Add a video link or choose a file.'
    if (selectionMode === 'manual') {
      if (segments.length === 0) return 'Add at least one section to cut.'
      if (segmentErrors.errors.length > 0) return segmentErrors.errors[0]
    }
    if (rangeError) return rangeError
    return null
  }, [busy, url, file, selectionMode, segments.length, segmentErrors, rangeError])

  // ---- Submit ------------------------------------------------------------

  const submit = useCallback(async () => {
    if (disabledReason) return
    setBusy(true)
    setError(null)
    try {
      const source = file
        ? await api.uploadSource(file)
        : await api.ingestUrl(url.trim(), {
            startS: useRange && rangeStartS !== null ? rangeStartS : undefined,
            endS: useRange && rangeEndS !== null ? rangeEndS : undefined,
          })

      const overrides: Parameters<typeof api.createJob>[1] = {
        selection_mode: selectionMode,
        ratio,
        caption_style: captionStyle,
        output_dir: outputDir || undefined,
        diarization: multipleSpeakers || undefined,
        provider: provider || undefined,
        whisper_model: QUALITY_MODEL[quality],
      }

      if (selectionMode === 'manual') {
        overrides.segments = segmentErrors.parsed.filter((s): s is Segment => s !== null)
      } else {
        overrides.max_clips = clipCount
        overrides.min_duration_s = LENGTH_PRESETS[clipLength].min
        overrides.max_duration_s = LENGTH_PRESETS[clipLength].max
      }

      const job = await api.createJob(source.id, overrides)
      navigate(`/jobs/${job.id}`)
    } catch (err) {
      setError(err as Error)
    } finally {
      setBusy(false)
    }
  }, [
    disabledReason,
    file,
    url,
    useRange,
    rangeStartS,
    rangeEndS,
    selectionMode,
    ratio,
    captionStyle,
    outputDir,
    multipleSpeakers,
    provider,
    quality,
    segmentErrors,
    clipCount,
    clipLength,
    navigate,
  ])

  const saveOutputDir = async (path: string) => {
    setOutputDir(path)
    setFolderModalOpen(false)
    if (!settings) return
    try {
      await api.putSettings({ export: { ...settings.export, output_dir: path } })
    } catch {
      /* The picker already reflects the choice; a failed save just means it
         won't be remembered next time, which isn't worth blocking on. */
    }
  }

  const usableProvider = providers.find((p) => p.available)

  return (
    <div className="pt-14">
      <div className="rise max-w-3xl">
        <p className="eyebrow">Local · No accounts · No watermarks</p>
        <h1 className="mt-5 font-display text-[clamp(2.25rem,5.5vw,4rem)] leading-[0.95] text-ink-100">
          Long video in.
          <br />
          <span className="italic text-sodium-500">Shorts</span> out.
        </h1>
      </div>

      <div className="mt-14 max-w-3xl space-y-14">
        {/* 1. Your video */}
        <Step number={1} title="Your video">
          <label htmlFor="url" className="eyebrow">
            Paste a link
          </label>
          <input
            id="url"
            className="field font-display mt-2 text-xl"
            placeholder="https://youtube.com/watch?v=…"
            value={url}
            onChange={(e) => {
              setUrl(e.target.value)
              if (e.target.value) setFile(null)
            }}
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
          />

          <div className="mt-4">
            <button
              type="button"
              onClick={() => fileInput.current?.click()}
              onDragOver={(e) => {
                e.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                const dropped = e.dataTransfer.files[0]
                if (dropped) {
                  setFile(dropped)
                  setUrl('')
                }
              }}
              className={[
                'block w-full border border-dashed px-4 py-3 text-left text-sm transition-colors duration-200',
                dragging
                  ? 'border-sodium-500 bg-sodium-700/10 text-ink-100'
                  : 'border-ink-700 text-ink-400 hover:border-ink-600',
              ].join(' ')}
            >
              {file ? (
                <span className="text-ink-100">{file.name}</span>
              ) : (
                'or choose a file — mp4 · mov · mkv · webm · mp3 · wav · m4a'
              )}
            </button>
            <input
              ref={fileInput}
              type="file"
              className="hidden"
              accept="video/*,audio/*"
              onChange={(e) => {
                const picked = e.target.files?.[0]
                if (picked) {
                  setFile(picked)
                  setUrl('')
                }
                e.target.value = ''
              }}
            />
          </div>

          <label className="mt-5 flex items-center gap-3 text-sm text-ink-200">
            <input
              type="checkbox"
              checked={useRange}
              onChange={(e) => setUseRange(e.target.checked)}
              className="size-4 accent-sodium-500"
            />
            Only use part of the video
          </label>
          {useRange && (
            <div className="mt-3 grid grid-cols-2 gap-4">
              <TimecodeField label="Start" value={rangeStart} onChange={setRangeStart} />
              <TimecodeField label="End" value={rangeEnd} onChange={setRangeEnd} />
              <p className="col-span-2 text-xs leading-relaxed text-ink-500">
                Skip to the good part — we&rsquo;ll only download this stretch (much faster on
                long streams).
              </p>
            </div>
          )}

          <p className="mt-3 text-xs leading-relaxed text-ink-500">
            Only use video you own or have the rights to process.
          </p>
        </Step>

        {/* 2. What to cut */}
        <Step number={2} title="What to cut">
          <div className="flex gap-2">
            <SegmentedOption
              active={selectionMode === 'auto'}
              onClick={() => setSelectionMode('auto')}
            >
              Let AI find the best moments
            </SegmentedOption>
            <SegmentedOption
              active={selectionMode === 'manual'}
              onClick={() => setSelectionMode('manual')}
            >
              I&rsquo;ll pick the moments
            </SegmentedOption>
          </div>

          {selectionMode === 'auto' ? (
            <div className="mt-6 grid gap-6 sm:grid-cols-2">
              <div>
                <span className="eyebrow">How many clips?</span>
                <div className="mt-2 flex items-center gap-4">
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => setClipCount((n) => Math.max(1, n - 1))}
                  >
                    –
                  </button>
                  <span className="numeric w-8 text-center text-lg text-ink-100">{clipCount}</span>
                  <button
                    type="button"
                    className="btn btn-ghost"
                    onClick={() => setClipCount((n) => Math.min(50, n + 1))}
                  >
                    +
                  </button>
                </div>
              </div>
              <div>
                <span className="eyebrow">Clip length</span>
                <div className="mt-2 space-y-1">
                  {(Object.keys(LENGTH_PRESETS) as ClipLength[]).map((key) => (
                    <button
                      key={key}
                      type="button"
                      onClick={() => setClipLength(key)}
                      className={[
                        'block w-full border-l-2 py-1.5 pl-3 text-left text-sm transition-colors duration-200',
                        clipLength === key
                          ? 'border-sodium-500 bg-ink-850/60 text-ink-100'
                          : 'border-transparent text-ink-400 hover:border-ink-700',
                      ].join(' ')}
                    >
                      {LENGTH_PRESETS[key].label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <div className="mt-6">
              <ul className="space-y-3">
                {segments.map((segment, index) => (
                  <li key={segment.key} className="flex items-start gap-3">
                    <span className="numeric mt-2.5 w-5 text-xs text-ink-600">{index + 1}</span>
                    <TimecodeField
                      label="Start"
                      value={segment.start}
                      onChange={(v) => setSegmentField(segment.key, 'start', v)}
                      compact
                    />
                    <span className="mt-2.5 text-ink-600">→</span>
                    <TimecodeField
                      label="End"
                      value={segment.end}
                      onChange={(v) => setSegmentField(segment.key, 'end', v)}
                      compact
                    />
                    <input
                      className="field mt-0 flex-1 text-sm"
                      placeholder="Name (optional)"
                      value={segment.title}
                      onChange={(e) => setSegmentField(segment.key, 'title', e.target.value)}
                    />
                    <button
                      type="button"
                      onClick={() => removeSegment(segment.key)}
                      className="btn btn-quiet mt-1.5"
                      aria-label="Remove section"
                    >
                      ✕
                    </button>
                  </li>
                ))}
              </ul>

              <button
                type="button"
                onClick={() => setSegments((current) => [...current, newSegment()])}
                className="btn btn-quiet -ml-1 mt-3"
              >
                + Add section
              </button>

              <div className="mt-3 flex items-center justify-between text-xs">
                <span className="text-ink-500">
                  {segments.length} section{segments.length === 1 ? '' : 's'} ·{' '}
                  {formatTimecode(totalManualSeconds)}
                </span>
                {segmentErrors.errors.length > 0 && (
                  <span className="text-signal-bad">{segmentErrors.errors[0]}</span>
                )}
              </div>
            </div>
          )}
        </Step>

        {/* 3. Where to save */}
        <Step number={3} title="Where to save">
          <div className="flex flex-wrap items-center gap-4">
            <span className="min-w-0 flex-1 truncate text-sm text-ink-300">
              {outputDir || 'Default location (inside your AutoClip folder)'}
            </span>
            <button
              type="button"
              className="btn btn-ghost shrink-0"
              onClick={() => setFolderModalOpen(true)}
            >
              Change…
            </button>
          </div>
        </Step>

        {/* 4. Style (collapsed) */}
        <Disclosure title="Style" open={styleOpen} onToggle={() => setStyleOpen((v) => !v)}>
          <div className="grid gap-8 sm:grid-cols-2">
            <div>
              <span className="eyebrow">Shape</span>
              <div className="mt-2 space-y-1">
                {RATIOS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setRatio(option.value)}
                    className={[
                      'flex w-full items-center gap-3 border-l-2 py-1.5 pl-3 text-left text-sm transition-colors duration-200',
                      ratio === option.value
                        ? 'border-sodium-500 bg-ink-850/60 text-ink-100'
                        : 'border-transparent text-ink-400 hover:border-ink-700',
                    ].join(' ')}
                  >
                    <RatioSwatch value={option.value} active={ratio === option.value} />
                    {option.label}
                  </button>
                ))}
              </div>
            </div>
            <div>
              <span className="eyebrow">Captions</span>
              <div className="mt-2 space-y-1">
                {captionStyles.map((style) => (
                  <button
                    key={style.key}
                    type="button"
                    onClick={() => setCaptionStyle(style.key)}
                    className={[
                      'block w-full border-l-2 py-1.5 pl-3 text-left transition-colors duration-200',
                      captionStyle === style.key
                        ? 'border-sodium-500 bg-ink-850/60'
                        : 'border-transparent hover:border-ink-700',
                    ].join(' ')}
                  >
                    <span className="text-sm text-ink-100">{style.label}</span>
                    <span className="mt-0.5 block text-xs text-ink-500">{style.description}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </Disclosure>

        {/* Advanced (collapsed) */}
        <Disclosure
          title="Advanced"
          small
          open={advancedOpen}
          onToggle={() => setAdvancedOpen((v) => !v)}
        >
          <div className="grid gap-x-8 gap-y-5 sm:grid-cols-2">
            <label className="block">
              <span className="eyebrow">AI service</span>
              <select
                className="field mt-1 cursor-pointer text-sm"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
              >
                <option value="" className="bg-ink-850">
                  Pick automatically
                </option>
                {providers.map((p) => (
                  <option
                    key={p.name}
                    value={p.name}
                    disabled={!p.available}
                    className="bg-ink-850"
                  >
                    {p.available ? p.name : `${p.name} — unavailable`}
                  </option>
                ))}
              </select>
            </label>

            <label className="block">
              <span className="eyebrow">Transcription quality</span>
              <select
                className="field mt-1 cursor-pointer text-sm"
                value={quality}
                onChange={(e) => setQuality(e.target.value as Quality)}
              >
                <option value="fast" className="bg-ink-850">
                  Fast
                </option>
                <option value="balanced" className="bg-ink-850">
                  Balanced
                </option>
                <option value="best" className="bg-ink-850">
                  Best
                </option>
              </select>
            </label>

            <label className="flex items-center gap-3 text-sm text-ink-200 sm:col-span-2">
              <input
                type="checkbox"
                checked={multipleSpeakers}
                onChange={(e) => setMultipleSpeakers(e.target.checked)}
                className="size-4 accent-sodium-500"
              />
              Multiple speakers — label who is talking, and cut to them
            </label>
          </div>
        </Disclosure>
      </div>

      {error && (
        <div className="mt-10 max-w-3xl">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {providers.length > 0 && !usableProvider && selectionMode === 'auto' && (
        <p className="mt-8 max-w-3xl border-l-2 border-sodium-600 pl-4 text-sm text-ink-300">
          No AI service is reachable yet, so finding highlights will fail. Add an API key in{' '}
          <a href="/settings" className="text-sodium-500 underline underline-offset-4">
            Settings
          </a>
          , or switch to &ldquo;I&rsquo;ll pick the moments&rdquo; above.
        </p>
      )}

      <div className="mt-10 max-w-3xl">
        <button
          type="button"
          onClick={() => void submit()}
          disabled={disabledReason !== null}
          className="btn btn-primary w-full py-3 text-base"
        >
          {busy ? 'Starting…' : 'Make clips'}
        </button>
        {disabledReason && (
          <p className="mt-2 text-xs text-signal-bad">{disabledReason}</p>
        )}
      </div>

      <RecentJobs jobs={jobs} clipCounts={clipCounts} />

      {folderModalOpen && (
        <FolderModal
          initialPath={outputDir || undefined}
          onClose={() => setFolderModalOpen(false)}
          onChoose={saveOutputDir}
        />
      )}
    </div>
  )
}

// ----------------------------------------------------------------------------
// Building blocks
// ----------------------------------------------------------------------------

function Step({
  number,
  title,
  children,
}: {
  number: number
  title: string
  children: React.ReactNode
}) {
  return (
    <section className="rise" style={{ animationDelay: `${number * 60}ms` }}>
      <div className="flex items-baseline gap-3 border-b border-ink-800 pb-2">
        <span className="numeric text-xs text-sodium-500">{String(number).padStart(2, '0')}</span>
        <h2 className="font-display text-xl text-ink-100">{title}</h2>
      </div>
      <div className="mt-5">{children}</div>
    </section>
  )
}

function Disclosure({
  title,
  open,
  onToggle,
  small,
  children,
}: {
  title: string
  open: boolean
  onToggle: () => void
  small?: boolean
  children: React.ReactNode
}) {
  return (
    <section className={small ? 'border-t border-ink-800 pt-6' : 'rise'}>
      <button
        type="button"
        onClick={onToggle}
        className={
          small
            ? 'btn btn-quiet -ml-1'
            : 'flex w-full items-baseline justify-between border-b border-ink-800 pb-2 text-left'
        }
      >
        {small ? (
          open ? (
            'Hide advanced'
          ) : (
            'Advanced'
          )
        ) : (
          <>
            <h2 className="font-display text-xl text-ink-100">{title}</h2>
            <span className="eyebrow">{open ? 'Hide' : 'Show'}</span>
          </>
        )}
      </button>
      <div
        className="grid transition-[grid-template-rows] duration-400 ease-[cubic-bezier(0.16,1,0.3,1)]"
        style={{ gridTemplateRows: open ? '1fr' : '0fr' }}
      >
        <div className="overflow-hidden">
          <div className="pt-5">{children}</div>
        </div>
      </div>
    </section>
  )
}

function SegmentedOption({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={['flex-1', 'btn', active ? 'btn-primary' : 'btn-ghost'].join(' ')}
    >
      {children}
    </button>
  )
}

function TimecodeField({
  label,
  value,
  onChange,
  compact,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  compact?: boolean
}) {
  return (
    <label className={compact ? 'block w-24' : 'block'}>
      <span className="eyebrow">{label}</span>
      <input
        className="field numeric mt-1 text-sm"
        placeholder="0:00"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        spellCheck={false}
      />
    </label>
  )
}

function RatioSwatch({ value, active }: { value: '9:16' | '1:1' | '16:9'; active: boolean }) {
  const dims: Record<string, [number, number]> = { '9:16': [9, 16], '1:1': [14, 14], '16:9': [16, 9] }
  const [w, h] = dims[value]
  const scale = 16 / Math.max(w, h)
  return (
    <span
      className={`inline-block border ${active ? 'border-sodium-500' : 'border-ink-600'}`}
      style={{ width: w * scale, height: h * scale }}
    />
  )
}

function RecentJobs({
  jobs,
  clipCounts,
}: {
  jobs: Job[]
  clipCounts: Record<string, number>
}) {
  if (jobs.length === 0) return null

  return (
    <section className="rise mt-20 max-w-3xl" style={{ animationDelay: '240ms' }}>
      <div className="flex items-baseline justify-between border-b border-ink-800 pb-3">
        <h2 className="eyebrow">Recent</h2>
        <span className="numeric text-xs text-ink-600">{jobs.length}</span>
      </div>

      <ul>
        {jobs.map((job) => (
          <li key={job.id}>
            <a
              href={`/jobs/${job.id}`}
              className="group grid grid-cols-[1fr_auto] items-baseline gap-4 border-b border-ink-850 py-4 transition-colors duration-200 hover:bg-ink-850/40 sm:grid-cols-[1fr_7rem_10rem]"
            >
              <span className="truncate text-[0.9375rem] text-ink-200 group-hover:text-ink-100">
                {job.source?.title || 'Untitled'}
              </span>
              <span className="numeric hidden text-xs text-ink-500 sm:block">
                {job.source ? formatDuration(job.source.duration_s) : '—'}
              </span>
              <StatusWords job={job} count={clipCounts[job.id]} />
            </a>
          </li>
        ))}
      </ul>
    </section>
  )
}

function StatusWords({ job, count }: { job: Job; count?: number }) {
  const tone: Record<string, string> = {
    done: 'text-signal-good',
    failed: 'text-signal-bad',
    running: 'text-sodium-500',
    queued: 'text-ink-400',
    cancelled: 'text-ink-500',
  }

  let label: string
  switch (job.status) {
    case 'running':
      label = `Working… ${Math.round(job.progress * 100)}%`
      break
    case 'queued':
      label = 'Waiting to start'
      break
    case 'done':
      label = count !== undefined ? `Ready — ${count} clip${count === 1 ? '' : 's'}` : 'Ready'
      break
    case 'failed':
      label = "Didn't finish"
      break
    case 'cancelled':
      label = 'Cancelled'
      break
    default:
      label = job.status
  }

  return (
    <span className={`justify-self-end text-xs ${tone[job.status] ?? 'text-ink-400'}`}>
      {label}
    </span>
  )
}

/** Folder picker modal backed by GET /api/settings/folders. */
function FolderModal({
  initialPath,
  onClose,
  onChoose,
}: {
  initialPath?: string
  onClose: () => void
  onChoose: (path: string) => void
}) {
  const [listing, setListing] = useState<FolderListing | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback((path?: string) => {
    setLoading(true)
    setError(null)
    api
      .browseFolders(path)
      .then(setListing)
      .catch((err) => setError(err as Error))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    load(initialPath)
    // Only on mount — subsequent navigation calls `load` directly.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div
      className="fixed inset-0 z-50 grid place-items-center bg-ink-900/80 p-6"
      onClick={onClose}
      role="presentation"
    >
      <div
        className="max-h-[80vh] w-full max-w-lg overflow-hidden border border-ink-700 bg-ink-900"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-baseline justify-between border-b border-ink-800 px-5 py-4">
          <h2 className="font-display text-xl text-ink-100">Choose a folder</h2>
          <button onClick={onClose} className="btn btn-quiet" aria-label="Close">
            ✕
          </button>
        </div>

        {listing && (
          <div className="flex flex-wrap gap-2 px-5 pt-4">
            {listing.suggestions.map((s) => (
              <button
                key={s.path}
                onClick={() => load(s.path)}
                className="btn btn-ghost px-3 py-1 text-xs"
              >
                {s.label}
              </button>
            ))}
          </div>
        )}

        <div className="max-h-96 overflow-y-auto px-5 py-4">
          {error && <ErrorNote error={error} onDismiss={() => setError(null)} />}
          {loading && <p className="text-sm text-ink-500">Loading…</p>}
          {listing && !loading && (
            <>
              <p className="numeric mb-3 truncate text-xs text-ink-500">{listing.path}</p>
              <ul className="space-y-1">
                {listing.parent && (
                  <li>
                    <button
                      onClick={() => load(listing.parent!)}
                      className="block w-full py-1.5 text-left text-sm text-ink-400 hover:text-ink-100"
                    >
                      .. (up)
                    </button>
                  </li>
                )}
                {listing.entries.map((entry) => (
                  <li key={entry.path}>
                    <button
                      onClick={() => load(entry.path)}
                      className="block w-full py-1.5 text-left text-sm text-ink-200 hover:text-ink-100"
                    >
                      {entry.name}
                    </button>
                  </li>
                ))}
                {listing.entries.length === 0 && (
                  <p className="text-sm text-ink-600">No subfolders here.</p>
                )}
              </ul>
            </>
          )}
        </div>

        <div className="flex items-center justify-between border-t border-ink-800 px-5 py-4">
          <span className="text-xs text-ink-500">
            {listing && !listing.writable && "Can't be written to — pick another."}
          </span>
          <button
            onClick={() => listing && onChoose(listing.path)}
            disabled={!listing || !listing.writable}
            className="btn btn-primary"
          >
            Use this folder
          </button>
        </div>
      </div>
    </div>
  )
}

// ----------------------------------------------------------------------------
// Validation helpers
// ----------------------------------------------------------------------------

function parseSegments(segments: DraftSegment[]): {
  parsed: (Segment | null)[]
  errors: string[]
} {
  const errors: string[] = []
  const parsed: (Segment | null)[] = segments.map((segment, index) => {
    if (!segment.start.trim() || !segment.end.trim()) {
      errors.push(`Section ${index + 1} needs both a start and an end.`)
      return null
    }
    const start = parseTimecode(segment.start)
    const end = parseTimecode(segment.end)
    if (start === null || end === null) {
      errors.push(`Section ${index + 1}: use a plain number of seconds, m:ss, or h:mm:ss.`)
      return null
    }
    if (end <= start) {
      errors.push("A section's end must come after its start.")
      return null
    }
    return { start_s: start, end_s: end, title: segment.title.trim() }
  })

  const valid = parsed.filter((s): s is Segment => s !== null)
  const ordered = [...valid].sort((a, b) => a.start_s - b.start_s)
  for (let i = 1; i < ordered.length; i += 1) {
    if (ordered[i].start_s < ordered[i - 1].end_s) {
      errors.push('Two sections overlap.')
      break
    }
  }

  return { parsed, errors }
}
