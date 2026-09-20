import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { v1 } from './api-v1'
import { TokenProvider, useToken } from './auth'

/**
 * Application shell.
 *
 * A masthead rather than a sidebar: this is a small set of screens, and a
 * persistent nav rail would spend a fifth of the width restating that.
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
    <div className="min-h-screen bg-ink-900">
      <header className="border-b border-ink-800">
        <div className="mx-auto flex max-w-[1600px] items-baseline gap-8 px-6 py-4 lg:px-10">
          <NavLink to="/" className="group flex items-baseline gap-2.5">
            <span className="font-display text-2xl leading-none text-ink-100">
              Clippy
            </span>
          </NavLink>

          <nav className="flex items-baseline gap-6">
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
              className="text-sm font-medium text-ink-400 transition-colors duration-200 hover:text-ink-200"
            >
              API
            </a>
          </nav>

          <div className="ml-auto flex items-baseline gap-5">
            {health && (
              <span className="numeric hidden text-xs text-ink-400 md:block">
                {health.status} · {health.queued} queued
              </span>
            )}
            {token && (
              <button onClick={clear} className="text-xs text-ink-500 hover:text-ink-200" title="Forget the stored Bearer key">
                Sign out
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-6 pb-24 lg:px-10">
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
          'text-sm font-medium transition-colors duration-200',
          isActive ? 'text-sodium-500' : 'text-ink-400 hover:text-ink-200',
        ].join(' ')
      }
    >
      {children}
    </NavLink>
  )
}
