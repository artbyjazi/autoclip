import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { v1 } from './api-v1'
import { TokenProvider, useToken } from './auth'

/**
 * Application shell: white masthead, product nav, health badge.
 */
export function App() {
  return (
    <TokenProvider>
      <Shell />
    </TokenProvider>
  )
}

function Shell() {
  const [health, setHealth] = useState<{ status: string; queued: number } | null>(null)
  const { token, clear } = useToken()

  useEffect(() => {
    v1.health().then(setHealth).catch(() => setHealth(null))
  }, [])

  return (
    <div className="min-h-screen bg-paper">
      <header className="sticky top-0 z-10 border-b border-line bg-card/90 backdrop-blur">
        <div className="mx-auto flex max-w-[1400px] items-center gap-8 px-6 py-3.5 lg:px-10">
          <NavLink to="/" className="flex items-baseline gap-2">
            <span className="font-display text-2xl leading-none text-ink-900">
              Clippy
            </span>
          </NavLink>

          <nav className="flex items-center gap-1 sm:gap-2">
            <TopLink to="/" end>
              Videos
            </TopLink>
            <TopLink to="/providers">Providers</TopLink>
            <TopLink to="/api-keys">API keys</TopLink>
            <TopLink to="/settings">Settings</TopLink>
            <a
              href="/api/docs"
              target="_blank"
              rel="noreferrer"
              className="rounded-lg px-3 py-1.5 text-sm font-medium text-ink-500 transition-colors hover:text-ink-900"
            >
              API
            </a>
          </nav>

          <div className="ml-auto flex items-center gap-4">
            {health && (
              <span className="badge numeric hidden md:inline-flex">
                {health.status} · {health.queued} queued
              </span>
            )}
            {token && (
              <button onClick={clear} className="text-xs font-medium text-ink-500 hover:text-ink-900" title="Forget the stored Bearer key">
                Sign out
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1400px] px-6 pb-24 lg:px-10">
        <Outlet />
      </main>
    </div>
  )
}

function TopLink({
  to,
  end,
  children,
}: {
  to: string
  end?: boolean
  children: React.ReactNode
}) {
  return (
    <NavLink
      to={to}
      end={end}
      className={({ isActive }) =>
        [
          'rounded-lg px-3 py-1.5 text-sm font-medium transition-colors',
          isActive ? 'bg-iris-100 text-iris-700' : 'text-ink-500 hover:text-ink-900',
        ].join(' ')
      }
    >
      {children}
    </NavLink>
  )
}
