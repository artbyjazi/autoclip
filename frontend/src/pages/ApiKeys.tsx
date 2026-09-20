import { useEffect, useState } from 'react'

import { v1, type V1ApiKeyCreatedOut, type V1ApiKeyOut } from '../api-v1'
import { ErrorNote } from '../components/ErrorNote'

export function ApiKeys() {
  const [keys, setKeys] = useState<V1ApiKeyOut[]>([])
  const [name, setName] = useState('')
  const [created, setCreated] = useState<V1ApiKeyCreatedOut | null>(null)
  const [copied, setCopied] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  const refresh = () => {
    v1.listApiKeys().then(setKeys).catch((err) => setError(err as Error))
  }

  useEffect(refresh, [])

  const create = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setCopied(false)
    try {
      const record = await v1.createApiKey(name.trim() || 'API key')
      setCreated(record)
      setName('')
      refresh()
    } catch (err) {
      setError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  const revoke = async (id: string, keyName: string) => {
    if (!window.confirm(`Revoke "${keyName}"? Clients using it will get 401 immediately.`)) return
    try {
      await v1.deleteApiKey(id)
      setKeys((current) => current.filter((key) => key.id !== id))
    } catch (err) {
      setError(err as Error)
    }
  }

  const copy = async () => {
    if (!created) return
    try {
      await navigator.clipboard.writeText(created.token)
      setCopied(true)
    } catch {
      /* Clipboard unavailable; the token is visible above. */
    }
  }

  return (
    <div className="max-w-3xl pt-14">
      <div className="rise border-b border-line pb-5">
        <h1 className="font-display text-[clamp(2rem,4vw,3rem)] leading-none text-ink-900">
          API keys
        </h1>
        <p className="mt-2 text-xs text-ink-500">
          Bearer keys for <span className="numeric">/api/v1/*</span>. Only hashes are
          stored — the value below is shown once and never again.
        </p>
      </div>

      {error && (
        <div className="mt-6">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      {created && (
        <div className="rise mt-6 border border-iris-600 p-4">
          <p className="eyebrow">New key — copy it now</p>
          <p className="numeric mt-2 break-all text-sm text-ink-900">{created.token}</p>
          <div className="mt-3 flex gap-3">
            <button onClick={() => void copy()} className="btn btn-ghost text-sm">
              {copied ? 'Copied' : 'Copy'}
            </button>
            <button onClick={() => setCreated(null)} className="btn btn-quiet text-sm">
              Dismiss
            </button>
          </div>
        </div>
      )}

      <form onSubmit={create} className="rise mt-8" style={{ animationDelay: '90ms' }}>
        <label htmlFor="key-name" className="eyebrow">
          New key
        </label>
        <div className="mt-3 flex items-end gap-4">
          <input
            id="key-name"
            className="field text-sm"
            placeholder="laptop"
            value={name}
            onChange={(e) => setName(e.target.value)}
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
            maxLength={100}
          />
          <button type="submit" className="btn btn-primary shrink-0" disabled={busy}>
            {busy ? 'Creating…' : 'Create'}
          </button>
        </div>
      </form>

      <section className="rise mt-12" style={{ animationDelay: '160ms' }}>
        <div className="flex items-baseline justify-between border-b border-line pb-3">
          <h2 className="eyebrow">Active keys</h2>
          <span className="numeric text-xs text-ink-400">{keys.length}</span>
        </div>
        {keys.length === 0 ? (
          <p className="py-8 text-sm text-ink-500">No keys yet.</p>
        ) : (
          <ul>
            {keys.map((key) => (
              <li
                key={key.id}
                className="grid grid-cols-[1fr_auto] items-baseline gap-4 border-b border-line py-4 sm:grid-cols-[1fr_8rem_9rem_5rem]"
              >
                <span className="truncate text-[0.9375rem] text-ink-700">
                  {key.name}
                  <span className="numeric ml-3 text-xs text-ink-400">{key.prefix}…</span>
                </span>
                <span className="numeric hidden text-xs text-ink-500 sm:block">
                  {new Date(key.created_at).toLocaleDateString()}
                </span>
                <span className="numeric hidden text-xs text-ink-500 sm:block">
                  {key.last_used_at ? `used ${new Date(key.last_used_at).toLocaleDateString()}` : 'never used'}
                </span>
                <span className="justify-self-end">
                  <button onClick={() => void revoke(key.id, key.name)} className="btn btn-ghost text-xs">
                    Revoke
                  </button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  )
}
