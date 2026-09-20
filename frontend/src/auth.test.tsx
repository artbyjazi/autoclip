import { act, cleanup, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setToken } from './api-v1'
import { AuthGate, TokenProvider } from './auth'

function renderGate(path = '/') {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <TokenProvider>
        <AuthGate>
          <p>protected content</p>
        </AuthGate>
      </TokenProvider>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  sessionStorage.clear()
  vi.unstubAllGlobals()
})

afterEach(() => cleanup())

describe('AuthGate', () => {
  it('lets Tailscale-authenticated production requests try without a token', () => {
    renderGate()
    expect(screen.getByText('protected content')).toBeTruthy()
  })

  it('renders children when a token is stored', () => {
    setToken('clp_test')
    renderGate()
    expect(screen.getByText('protected content')).toBeTruthy()
  })

  it('shows the local API-key fallback only after a 401', () => {
    renderGate()
    act(() => window.dispatchEvent(new Event('clippy:unauthorized')))
    expect(screen.getByText(/Local development fallback/i)).toBeTruthy()
    expect(screen.queryByText('protected content')).toBeNull()
  })
})
