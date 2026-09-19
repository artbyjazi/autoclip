import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'

import { api, type SystemStatus } from './api'

/**
 * Application shell.
 *
 * A masthead rather than a sidebar: this is a four-screen tool, and a
 * persistent nav rail would spend a fifth of the width restating that.
 */
export function App() {
  const [system, setSystem] = useState<SystemStatus | null>(null)

  useEffect(() => {
    api.system().then(setSystem).catch(() => setSystem(null))
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
              New
            </TopLink>
            <TopLink to="/settings">Settings</TopLink>
          </nav>

          <div className="ml-auto flex items-baseline gap-5">
            {system && <SystemBadge system={system} />}
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

/**
 * Compact machine status.
 *
 * Surfaced permanently rather than hidden in settings because the two things it
 * reports — whether ffmpeg can render, and whether the GPU is being used — are
 * the two that change how long everything takes.
 */
function SystemBadge({ system }: { system: SystemStatus }) {
  const accel = system.accel.toUpperCase()
  return (
    <div className="hidden items-baseline gap-4 text-xs md:flex">
      <span className="numeric text-ink-400">
        {accel}
        {system.gpu_name && accel === 'CUDA' && (
          <span className="text-ink-600"> · {system.gpu_name.replace('NVIDIA GeForce ', '')}</span>
        )}
      </span>
      <span
        className={system.ready ? 'text-ink-400' : 'text-signal-bad'}
        title={system.ready ? 'All required components present' : 'Run clippy doctor'}
      >
        {system.ready ? 'ready' : 'not ready'}
      </span>
    </div>
  )
}
