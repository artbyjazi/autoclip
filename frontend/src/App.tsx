import { useEffect, useState } from 'react'
import { NavLink, Outlet, useLocation } from 'react-router-dom'

import { api, type SystemStatus } from './api'

/**
 * Application shell.
 *
 * One tab, not a sidebar: this is a one-job tool (cut clips), and a nav rail
 * would spend a lot of width restating that. Settings is secondary, so it
 * drops to an icon button rather than a second tab.
 */
export function App() {
  const [system, setSystem] = useState<SystemStatus | null>(null)
  const location = useLocation()

  useEffect(() => {
    api.system().then(setSystem).catch(() => setSystem(null))
  }, [])

  // "Make Clips" owns /jobs/* too — a running or finished job is still part of
  // making clips, not a separate destination.
  const onMakeClips = location.pathname === '/' || location.pathname.startsWith('/jobs')

  return (
    <div className="min-h-screen bg-ink-900">
      <header className="border-b border-ink-800">
        <div className="mx-auto flex max-w-[1600px] items-baseline gap-8 px-6 py-4 lg:px-10">
          <NavLink to="/" className="group flex items-baseline gap-2.5">
            <span className="font-display text-2xl leading-none text-ink-100">
              Auto<span className="italic text-sodium-500">Clip</span>
            </span>
          </NavLink>

          <nav className="flex items-baseline gap-2">
            <TabLink to="/" active={onMakeClips}>
              Make Clips
            </TabLink>
          </nav>

          <div className="ml-auto flex items-center gap-4">
            {system && <StatusDot system={system} />}
            <NavLink
              to="/settings"
              className={({ isActive }) =>
                [
                  'rounded-full p-2 transition-colors duration-200',
                  isActive ? 'text-sodium-500' : 'text-ink-400 hover:text-ink-100',
                ].join(' ')
              }
              aria-label="Settings"
              title="Settings"
            >
              <GearIcon />
            </NavLink>
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1600px] px-6 pb-24 lg:px-10">
        <Outlet />
      </main>
    </div>
  )
}

function TabLink({
  to,
  active,
  children,
}: {
  to: string
  active: boolean
  children: React.ReactNode
}) {
  return (
    <NavLink
      to={to}
      className={[
        'px-3 py-2 text-sm font-semibold transition-colors duration-200',
        active ? 'text-sodium-500' : 'text-ink-400 hover:text-ink-200',
      ].join(' ')}
    >
      {children}
    </NavLink>
  )
}

/**
 * A single status dot rather than the old text badge — "CUDA / RTX 4070"
 * means nothing to the target user. The detail moves into the tooltip and
 * Settings' "This machine" section for anyone who does want it.
 */
function StatusDot({ system }: { system: SystemStatus }) {
  const label = system.ready
    ? `Ready${system.gpu_name ? ` · using ${system.gpu_name.replace('NVIDIA GeForce ', '')}` : ''}`
    : 'Something needed is missing — see Settings'

  return (
    <span
      className={`inline-block size-2 rounded-full ${system.ready ? 'bg-signal-good' : 'bg-signal-bad'}`}
      title={label}
      aria-label={label}
    />
  )
}

function GearIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
      <path
        d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"
        stroke="currentColor"
        strokeWidth="1.6"
      />
      <path
        d="M19.4 13.5a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V19.5a2 2 0 1 1-4 0v-.09a1.65 1.65 0 0 0-1.08-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H4.5a2 2 0 1 1 0-4h.09a1.65 1.65 0 0 0 1.51-1.08 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H10a1.65 1.65 0 0 0 1-1.51V4.5a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V10a1.65 1.65 0 0 0 1.51 1H19.5a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"
        stroke="currentColor"
        strokeWidth="1.3"
        strokeLinejoin="round"
      />
    </svg>
  )
}
