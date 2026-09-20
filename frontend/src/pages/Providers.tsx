import { useEffect, useState } from 'react'

import { v1, type V1ProviderStatus, type V1ProviderTarget, type V1TargetUsage } from '../api-v1'
import { ErrorNote } from '../components/ErrorNote'

function formatTokens(n: number) {
  if (n < 1000) return `${n}`
  return `${(n / 1000).toFixed(1)}k`
}

export function Providers() {
  const [targets, setTargets] = useState<V1ProviderTarget[]>([])
  const [usage, setUsage] = useState<V1TargetUsage[]>([])
  const [live, setLive] = useState<V1ProviderStatus[]>([])
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    v1.providers()
      .then((data) => {
        setTargets([...data.targets].sort((a, b) => b.priority - a.priority))
        setUsage(data.usage)
      })
      .catch((err) => setError(err as Error))
    v1.providersStatus().then(setLive).catch(() => undefined)
  }, [])

  const usageByTarget = new Map(usage.map((u) => [u.target_id, u]))

  return (
    <div className="max-w-5xl pt-14">
      <div className="rise border-b border-ink-800 pb-5">
        <h1 className="font-display text-[clamp(2rem,4vw,3rem)] leading-none text-ink-100">
          Providers
        </h1>
        <p className="mt-2 text-xs text-ink-500">
          Tried top-down by priority. Failures cool down; auth failures stay off until the
          key is updated. All LLM targets are free-tier models.
        </p>
      </div>

      {error && (
        <div className="mt-6">
          <ErrorNote error={error} onDismiss={() => setError(null)} />
        </div>
      )}

      <section className="rise mt-10" style={{ animationDelay: '90ms' }}>
        <h2 className="eyebrow">Routing targets</h2>
        <ul className="mt-3">
          {targets.map((target) => {
            const stats = usageByTarget.get(target.id)
            const cooled = target.cooldown_until ? `cooldown until ${target.cooldown_until}` : null
            return (
              <li key={target.id} className="border-b border-ink-850 py-4">
                <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
                  <span className="numeric text-sm text-ink-100">
                    <span className="mr-3 text-xs text-ink-600">{target.priority}</span>
                    {target.id}
                  </span>
                  <span className="flex gap-2 text-xs">
                    <span className="border border-ink-700 px-2 py-0.5 text-ink-400">
                      {target.purpose}
                    </span>
                    <span
                      className={
                        target.enabled
                          ? 'border border-ink-700 px-2 py-0.5 text-ink-400'
                          : 'border border-signal-bad px-2 py-0.5 text-signal-bad'
                      }
                    >
                      {target.enabled ? target.health : 'disabled'}
                    </span>
                  </span>
                </div>
                <p className="numeric mt-1 truncate pl-8 text-xs text-ink-500">
                  {target.provider} · {target.model}
                </p>
                {cooled && <p className="mt-1 pl-8 text-xs text-sodium-500">{cooled}</p>}
                {target.consecutive_errors > 0 && (
                  <p className="mt-1 pl-8 text-xs text-signal-bad">
                    {target.consecutive_errors} consecutive errors
                    {target.last_error ? ` — ${target.last_error.slice(0, 160)}` : ''}
                  </p>
                )}
                {stats && (stats.requests > 0) && (
                  <p className="numeric mt-1 pl-8 text-xs text-ink-600">
                    {stats.requests} calls · {stats.successes} ok · {stats.errors} err ·{' '}
                    {Math.round(stats.average_latency_ms)} ms avg ·{' '}
                    {formatTokens(stats.input_tokens)}+{formatTokens(stats.output_tokens)} tokens
                    {stats.audio_seconds > 0 ? ` · ${Math.round(stats.audio_seconds)}s audio` : ''}
                  </p>
                )}
              </li>
            )
          })}
        </ul>
        {targets.length === 0 && !error && (
          <p className="py-8 text-sm text-ink-500">Loading…</p>
        )}
      </section>

      <section className="rise mt-14" style={{ animationDelay: '160ms' }}>
        <h2 className="eyebrow">Live health</h2>
        <ul className="mt-3">
          {live.map((provider) => (
            <li
              key={provider.name}
              className="flex flex-wrap items-baseline justify-between gap-x-4 border-b border-ink-850 py-3"
            >
              <span className="text-sm text-ink-100">
                {provider.name}
                <span className="numeric ml-3 text-xs text-ink-600">
                  {provider.requires_key ? (provider.has_key ? 'key set' : 'no key') : 'no key needed'}
                </span>
              </span>
              <span className={`text-xs ${provider.available ? 'text-signal-good' : 'text-ink-500'}`}>
                {provider.available ? 'reachable' : provider.detail || 'unavailable'}
              </span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}
