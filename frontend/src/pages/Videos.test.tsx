import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes, useParams } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setToken } from '../api-v1'
import { TokenProvider } from '../auth'
import { Videos } from './Videos'

function JobCatcher() {
  const { jobId } = useParams()
  return <p>navigated to job {jobId}</p>
}

function renderVideos() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <TokenProvider>
        <Routes>
          <Route path="/" element={<Videos />} />
          <Route path="/jobs/:jobId" element={<JobCatcher />} />
        </Routes>
      </TokenProvider>
    </MemoryRouter>,
  )
}

const sampleVideos = [
  {
    id: 'vid-1',
    type: 'youtube',
    title: 'Test Video',
    url: 'https://youtube.com/watch?v=x',
    filename: null,
    channel: 'Test Channel',
    duration_s: 125,
    width: 1920,
    height: 1080,
    fps: 30,
    has_audio: true,
    has_video: true,
    created_at: '2026-09-19T00:00:00+00:00',
    latest_analysis_job: null,
    clip_count: 0,
    preview_ready_count: 0,
    final_ready_count: 0,
  },
]

beforeEach(() => {
  sessionStorage.clear()
  setToken('clp_test')
  vi.unstubAllGlobals()
})

afterEach(() => cleanup())

describe('Videos', () => {
  it('lists videos from v1', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify(sampleVideos), { status: 200 })),
    )

    renderVideos()
    await waitFor(() => expect(screen.getByText('Test Video')).toBeTruthy())
    expect(screen.getAllByText((_, el) => el?.textContent?.includes('Test Channel') ?? false).length).toBeGreaterThanOrEqual(1)
  })

  it('creates a video and navigates to its job', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
        const url = String(input)
        if (url.includes('/api/v1/videos') && init?.method === 'POST') {
          return new Response(
            JSON.stringify({ video_id: 'vid-9', job_id: 'job-9', status: 'queued' }),
            { status: 202 },
          )
        }
        return new Response(JSON.stringify([]), { status: 200 })
      }),
    )

    renderVideos()
    fireEvent.change(screen.getByPlaceholderText(/Paste a YouTube link/i), {
      target: { value: 'https://youtube.com/watch?v=abc' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Get Shorts' }))

    await waitFor(() => expect(screen.getByText('navigated to job job-9')).toBeTruthy())
  })
})
