import { useEffect, useState } from 'react'

import { v1, type V1SettingsOut, type V1StorageStatus } from '../api-v1'
import { ErrorNote } from '../components/ErrorNote'

const SECRET_LABELS: Record<string, string> = {
  anthropic: 'Anthropic API key',
  openai: 'OpenAI-compatible API key',
  gemini: 'Google Gemini API key',
  groq: 'Groq API key',
  openrouter: 'OpenRouter API key',
  huggingface_token: 'HuggingFace token',
}

const PROVIDER_NAMES = ['anthropic', 'openai', 'gemini', 'groq', 'openrouter', 'ollama']

export function Settings() {
  const [settings, setSettings] = useState<V1SettingsOut | null>(null)
  const [storage, setStorage] = useState<V1StorageStatus | null>(null)
  const [error, setError] = useState<Error | null>(null)
  const [saved, setSaved] = useState(false)

  const reload = () => {
    v1.getSettings().then(setSettings).catch((e) => setError(e as Error))
    v1.storage().then(setStorage).catch(() => undefined)
  }

  useEffect(reload, [])

  const patch = async (update: Record<string, unknown>) => {
    setError(null)
    try {
      setSettings(await v1.patchSettings(update))
      setSaved(true)
      setTimeout(() => setSaved(false), 1600)
    } catch (err) {
      setError(err as Error)
    }
  }

  if (!settings) return <p className="pt-24 text-sm text-ink-500">Loading…</p>

  const str = (section: Record<string, unknown>, key: string) =>
    typeof section[key] === 'string' ? (section[key] as string) : ''
  const num = (section: Record<string, unknown>, key: string) =>
    typeof section[key] === 'number' ? (section[key] as number) : 0
  const bool = (section: Record<string, unknown>, key: string) => section[key] === true

  // The cookie path is server-only: the API exposes presence, never the path,
  // and rejects any attempt to set it. Strip both keys before PATCHing ingest.
  const patchIngest = (ingest: Record<string, unknown>) => {
    const { cookies_file: _cf, cookies_file_configured: _cc, ...rest } = ingest
    void _cf
    void _cc
    return patch({ ingest: rest })
  }

  return (
    <div className="max-w-4xl pt-14">
      <div className="rise flex items-baseline justify-between border-b border-line pb-5">
        <h1 className="font-display text-[clamp(2rem,4vw,3rem)] leading-none text-ink-900">
          Settings
        </h1>
        <span
          className="text-xs text-signal-good transition-opacity duration-300"
          style={{ opacity: saved ? 1 : 0 }}
        >
          saved
        </span>
      </div>

      {error && (
        <div className="mt-6">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      <Section title="AI provider" note="Which model picks the clips. Routing order and failover live under Providers.">
        <div className="space-y-1">
          {PROVIDER_NAMES.map((name) => (
            <button
              key={name}
              onClick={() => patch({ active_provider: name })}
              className={[
                'block w-full border-l-2 py-3 pl-3 text-left transition-colors duration-200',
                name === settings.active_provider
                  ? 'border-iris-600 bg-iris-50'
                  : 'border-transparent hover:border-line hover:bg-paper',
              ].join(' ')}
            >
              <div className="flex items-baseline justify-between gap-4">
                <span className="text-sm text-ink-900">{name}</span>
                <span className="text-xs text-ink-500">
                  {settings.keys_present[name] ? 'key set' : 'no key'}
                </span>
              </div>
              <span className="numeric mt-1 block truncate text-xs text-ink-400">
                {settings.providers[name]?.model || '—'}
              </span>
            </button>
          ))}
        </div>

        <div className="mt-6 grid gap-5 sm:grid-cols-2">
          <Field
            label="Model"
            value={settings.providers[settings.active_provider]?.model ?? ''}
            placeholder="model name"
            onCommit={(value) =>
              patch({
                providers: {
                  [settings.active_provider]: {
                    ...settings.providers[settings.active_provider],
                    model: value,
                  },
                },
              })
            }
          />
          <Field
            label="Base URL"
            hint="Point at OpenRouter, Groq, DeepSeek, or a local server."
            value={settings.providers[settings.active_provider]?.base_url ?? ''}
            placeholder="https://…"
            onCommit={(value) =>
              patch({
                providers: {
                  [settings.active_provider]: {
                    ...settings.providers[settings.active_provider],
                    base_url: value || null,
                  },
                },
              })
            }
          />
        </div>
      </Section>

      <Section title="Keys" note="Write-only. Stored in the server's owner-only secrets file, never returned by the API.">
        <div className="space-y-4">
          {Object.entries(SECRET_LABELS).map(([key, label]) => (
            <SecretField
              key={key}
              secretKey={key}
              label={label}
              present={settings.keys_present[key] ?? false}
              onChanged={reload}
              onError={setError}
            />
          ))}
        </div>
      </Section>

      <Section title="Transcription">
        <div className="grid gap-5 sm:grid-cols-2">
          <Select
            label="Whisper model"
            value={str(settings.whisper, 'model')}
            onChange={(value) => patch({ whisper: { ...settings.whisper, model: value } })}
            options={['tiny', 'base', 'small', 'medium', 'large-v3']}
          />
          <Field
            label="Language"
            hint="Leave empty to detect automatically."
            value={str(settings.whisper, 'language')}
            placeholder="auto"
            onCommit={(value) => patch({ whisper: { ...settings.whisper, language: value } })}
          />
        </div>

        <label className="mt-5 flex items-start gap-3 text-sm text-ink-700">
          <input
            type="checkbox"
            checked={bool(settings.whisper, 'diarization')}
            onChange={(e) =>
              patch({ whisper: { ...settings.whisper, diarization: e.target.checked } })
            }
            className="mt-0.5 size-4 accent-iris-600"
          />
          <span>Identify speakers — label who is talking, and cut to them</span>
        </label>
      </Section>

      <Section title="Clips">
        <div className="grid gap-5 sm:grid-cols-3">
          <NumberField
            label="Min length (s)"
            value={num(settings.clips, 'min_duration_s')}
            onCommit={(value) => patch({ clips: { ...settings.clips, min_duration_s: value } })}
          />
          <NumberField
            label="Max length (s)"
            value={num(settings.clips, 'max_duration_s')}
            onCommit={(value) => patch({ clips: { ...settings.clips, max_duration_s: value } })}
          />
          <NumberField
            label="Max clips"
            value={num(settings.clips, 'max_clips')}
            onCommit={(value) => patch({ clips: { ...settings.clips, max_clips: value } })}
          />
        </div>
      </Section>

      <Section title="Ingest">
        <div className="grid gap-5 sm:grid-cols-2">
          <Select
            label="YouTube cookies from"
            hint="YouTube blocks most anonymous downloads. Sign in in that browser and close it before downloading."
            value={str(settings.ingest, 'cookies_from_browser')}
            onChange={(value) =>
              patchIngest({ ...settings.ingest, cookies_from_browser: value })
            }
            options={['', 'chrome', 'firefox', 'edge', 'brave', 'chromium', 'safari']}
            labels={{ '': 'None' }}
          />
          <div>
            <span className="eyebrow">Server cookie file</span>
            <p className="mt-2 text-sm text-ink-700">
              {bool(settings.ingest, 'cookies_file_configured') ? (
                <span className="text-signal-good">configured</span>
              ) : (
                <span className="text-ink-500">not configured</span>
              )}
            </p>
            <p className="mt-1.5 text-xs leading-snug text-ink-500">
              Managed by the server operator; its path is never exposed through the API.
            </p>
          </div>
        </div>
      </Section>

      <Section title="Export">
        <div className="grid gap-5 sm:grid-cols-2">
          <Select
            label="Default ratio"
            value={str(settings.export, 'ratio')}
            onChange={(value) => patch({ export: { ...settings.export, ratio: value } })}
            options={['9:16', '1:1', '16:9']}
          />
          <NumberField
            label="Loudness target (LUFS)"
            value={num(settings.export, 'loudness_lufs')}
            onCommit={(value) => patch({ export: { ...settings.export, loudness_lufs: value } })}
          />
        </div>

        <label className="mt-5 flex items-start gap-3 text-sm text-ink-700">
          <input
            type="checkbox"
            checked={bool(settings.export, 'prefer_hardware_encoder')}
            onChange={(e) =>
              patch({
                export: { ...settings.export, prefer_hardware_encoder: e.target.checked },
              })
            }
            className="mt-0.5 size-4 accent-iris-600"
          />
          <span>Use GPU encoding when available</span>
        </label>

        <label className="mt-4 flex items-center gap-3 text-sm text-ink-700">
          <input
            type="checkbox"
            checked={bool(settings.export, 'write_srt')}
            onChange={(e) => patch({ export: { ...settings.export, write_srt: e.target.checked } })}
            className="size-4 accent-iris-600"
          />
          Also write an .srt sidecar
        </label>
      </Section>

      <StorageSection storage={storage} settings={settings} onPatch={patch} onError={setError} />
    </div>
  )
}

function StorageSection({
  storage,
  settings,
  onPatch,
  onError,
}: {
  storage: V1StorageStatus | null
  settings: V1SettingsOut
  onPatch: (update: Record<string, unknown>) => Promise<void>
  onError: (error: Error) => void
}) {
  const [report, setReport] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const num = (key: string) =>
    typeof settings.storage[key] === 'number' ? (settings.storage[key] as number) : 0

  const cleanup = async () => {
    setBusy(true)
    setReport(null)
    try {
      const result = await v1.runCleanup()
      const mb = (result.bytes / 1048576).toFixed(1)
      setReport(
        `Removed ${result.previews} previews, ${result.sources} sources, ${result.chunks} chunks — ${mb} MB reclaimed. Final exports are never deleted.`,
      )
    } catch (err) {
      onError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  const gb = (bytes: number) => `${(bytes / 1073741824).toFixed(1)} GB`

  return (
    <Section
      title="Storage"
      note="Previews expire after 24 hours by default, sources after 7 days. Transcripts, metadata and final exports are kept."
    >
      <dl className="grid gap-x-8 gap-y-3 text-sm sm:grid-cols-2">
        <Row label="Home" value={storage?.root ?? '…'} />
        <Row
          label="Clippy data"
          value={storage ? formatBytes(storage.used_bytes) : '…'}
        />
        <Row label="Disk free" value={storage ? gb(storage.disk_free_bytes) : '…'} />
        <Row label="Disk total" value={storage ? gb(storage.disk_total_bytes) : '…'} />
      </dl>

      <div className="mt-5 grid gap-5 sm:grid-cols-3">
        <NumberField
          label="Sources kept (days)"
          value={num('source_retention_days')}
          onCommit={(value) =>
            onPatch({ storage: { ...settings.storage, source_retention_days: value } })
          }
        />
        <NumberField
          label="Previews kept (hours)"
          value={num('preview_retention_hours')}
          onCommit={(value) =>
            onPatch({ storage: { ...settings.storage, preview_retention_hours: value } })
          }
        />
        <NumberField
          label="Refuse jobs below (GB)"
          value={num('reject_new_jobs_below_gb')}
          onCommit={(value) =>
            onPatch({ storage: { ...settings.storage, reject_new_jobs_below_gb: value } })
          }
        />
      </div>

      <div className="mt-5">
        <button onClick={() => void cleanup()} disabled={busy} className="btn btn-ghost text-sm">
          {busy ? 'Cleaning…' : 'Run cleanup now'}
        </button>
        {report && <p className="mt-3 text-sm text-signal-good">{report}</p>}
      </div>
    </Section>
  )
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  const units = ['KB', 'MB', 'GB']
  let value = bytes / 1024
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}

function Section({
  title,
  note,
  children,
}: {
  title: string
  note?: string
  children: React.ReactNode
}) {
  return (
    <section className="rise mt-14">
      <div className="border-b border-line pb-2">
        <h2 className="eyebrow">{title}</h2>
        {note && <p className="mt-1 text-xs text-ink-500">{note}</p>}
      </div>
      <div className="mt-5">{children}</div>
    </section>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-line pb-2">
      <dt className="text-ink-500">{label}</dt>
      <dd className="numeric truncate text-right text-ink-700">{value}</dd>
    </div>
  )
}

/** Commits on blur rather than per keystroke, so a PATCH isn't fired per letter. */
function Field({
  label,
  hint,
  value,
  placeholder,
  onCommit,
}: {
  label: string
  hint?: string
  value: string
  placeholder?: string
  onCommit: (value: string) => void
}) {
  const [draft, setDraft] = useState(value)
  useEffect(() => setDraft(value), [value])

  return (
    <label className="block">
      <span className="eyebrow">{label}</span>
      <input
        className="field mt-1 text-sm"
        value={draft}
        placeholder={placeholder}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft !== value && onCommit(draft)}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
        spellCheck={false}
      />
      {hint && <span className="mt-1.5 block text-xs leading-snug text-ink-500">{hint}</span>}
    </label>
  )
}

function NumberField({
  label,
  value,
  onCommit,
}: {
  label: string
  value: number
  onCommit: (value: number) => void
}) {
  const [draft, setDraft] = useState(String(value))
  useEffect(() => setDraft(String(value)), [value])

  return (
    <label className="block">
      <span className="eyebrow">{label}</span>
      <input
        type="number"
        className="field numeric mt-1 text-sm"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          const parsed = Number(draft)
          if (!Number.isNaN(parsed) && parsed !== value) onCommit(parsed)
        }}
        onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
      />
    </label>
  )
}

function Select({
  label,
  hint,
  value,
  onChange,
  options,
  labels = {},
}: {
  label: string
  hint?: string
  value: string
  onChange: (value: string) => void
  options: string[]
  labels?: Record<string, string>
}) {
  return (
    <label className="block">
      <span className="eyebrow">{label}</span>
      <select
        className="field mt-1 cursor-pointer text-sm"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      >
        {options.map((option) => (
          <option key={option} value={option} className="bg-card">
            {labels[option] ?? option}
          </option>
        ))}
      </select>
      {hint && <span className="mt-1.5 block text-xs leading-snug text-ink-500">{hint}</span>}
    </label>
  )
}

function SecretField({
  secretKey,
  label,
  present,
  onChanged,
  onError,
}: {
  secretKey: string
  label: string
  present: boolean
  onChanged: () => void
  onError: (error: Error) => void
}) {
  const [value, setValue] = useState('')
  const [busy, setBusy] = useState(false)

  const save = async () => {
    if (!value.trim()) return
    setBusy(true)
    try {
      await v1.putSecret(secretKey, value.trim())
      setValue('')
      onChanged()
    } catch (err) {
      onError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  const remove = async () => {
    setBusy(true)
    try {
      await v1.deleteSecret(secretKey)
      onChanged()
    } catch (err) {
      onError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className="min-w-56 flex-1">
        <span className="eyebrow">
          {label}
          {present && <span className="ml-2 text-signal-good">set</span>}
        </span>
        <input
          type="password"
          className="field mt-1 text-sm"
          value={value}
          placeholder={present ? '••••••••••••' : 'paste to add'}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && save()}
          autoComplete="off"
        />
      </label>
      <button onClick={save} disabled={!value.trim() || busy} className="btn btn-ghost">
        Save
      </button>
      {present && (
        <button onClick={remove} disabled={busy} className="btn btn-quiet">
          Remove
        </button>
      )}
    </div>
  )
}
