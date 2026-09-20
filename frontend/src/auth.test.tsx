import { cleanup, render, screen } from '@testing-library/react'
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
  it('asks for a token when none is stored', () => {
    renderGate()
    expect(screen.getByText(/Paste your/i)).toBeTruthy()
    expect(screen.queryByText('protected content')).toBeNull()
  })

  it('renders children when a token is stored', () => {
    setToken('clp_test')
    renderGate()
    expect(screen.getByText('protected content')).toBeTruthy()
  })

  it('lets the legacy settings page through without a token', () => {
    renderGate('/settings')
    expect(screen.getByText('protected content')).toBeTruthy()
  })
})
