import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useLocation } from 'react-router-dom'

import { getToken, setToken, v1 } from './api-v1'

interface TokenContextValue {
  token: string | null
  save: (token: string) => Promise<void>
  clear: () => void
}

const TokenContext = createContext<TokenContextValue>({
  token: null,
  save: async () => undefined,
  clear: () => undefined,
})

export function useToken() {
  return useContext(TokenContext)
}

/** Provides the Bearer token; re-renders the gate when a 401 clears it. */
export function TokenProvider({ children }: { children: ReactNode }) {
  const [token, setTokenState] = useState<string | null>(() => getToken())

  useEffect(() => {
    const onUnauthorized = () => setTokenState(null)
    window.addEventListener('clippy:unauthorized', onUnauthorized)
    return () => window.removeEventListener('clippy:unauthorized', onUnauthorized)
  }, [])

  const save = useCallback(async (value: string) => {
    const cleaned = value.trim()
    if (!cleaned) throw new Error('Paste a token first.')
    setToken(cleaned)
    try {
      // Cheap metadata call that proves the key is valid.
      await v1.listApiKeys()
    } catch (err) {
      setToken(null)
      throw err
    }
    setTokenState(cleaned)
  }, [])

  const clear = useCallback(() => {
    setToken(null)
    setTokenState(null)
  }, [])

  return (
    <TokenContext.Provider value={{ token, save, clear }}>
      {children}
    </TokenContext.Provider>
  )
}

/**
 * Blocks v1 pages until a Bearer token is stored. The legacy settings page
 * stays reachable without a token until its own C6 migration.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const { token, save } = useToken()
  const location = useLocation()
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<Error | null>(null)

  if (token || location.pathname.startsWith('/settings')) return <>{children}</>

  const submit = async (event: React.FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await save(input)
      setInput('')
    } catch (err) {
      setError(err as Error)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto max-w-xl pt-14">
      <div className="rise card p-6 sm:p-10">
        <p className="eyebrow">Private · Bearer required</p>
        <h1 className="mt-4 font-display text-[clamp(2rem,5vw,3rem)] leading-[1.02] text-ink-900">
          Paste your <span className="italic text-iris-600">API key</span>.
        </h1>
        <p className="mt-4 text-sm leading-relaxed text-ink-500">
          Every <span className="numeric">/api/v1/*</span> route except health needs a Bearer
          key. Create one on the server with{' '}
          <code className="numeric text-ink-900">clippy api-key create --name laptop</code>{' '}
          — the value is shown once. It stays in this tab&apos;s session storage, never in a
          log.
        </p>
        <form onSubmit={submit} className="mt-8">
          <label htmlFor="token" className="eyebrow">
            API key
          </label>
          <div className="mt-3 flex items-end gap-4">
            <input
              id="token"
              type="password"
              className="field numeric text-sm"
              placeholder="clp_…"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              autoComplete="off"
              spellCheck={false}
              disabled={busy}
            />
            <button type="submit" className="btn btn-primary shrink-0" disabled={busy || !input.trim()}>
              {busy ? 'Checking…' : 'Save'}
            </button>
          </div>
        </form>
        {error && <p className="mt-4 text-sm text-signal-bad">{error.message}</p>}
      </div>
    </div>
  )
}
