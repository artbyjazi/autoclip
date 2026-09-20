import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import { setToken, type V1ClipOut, type V1VideoSummaryOut } from '../api-v1'
import { Review } from './Review'

const video: V1VideoSummaryOut = {
  id: 'video-1', type: 'youtube', title: 'Interview', url: 'https://youtube.com/x', filename: null,
  channel: 'Channel', duration_s: 600, width: 1920, height: 1080, fps: 30,
  has_audio: true, has_video: true, created_at: '2026-01-01T00:00:00Z', latest_analysis_job: null,
  clip_count: 2, preview_ready_count: 0, final_ready_count: 0,
}
const base: V1ClipOut = {
  id: 'clip-1', job_id: 'job-1', rank: 1, start_s: 10, end_s: 50, duration_s: 40,
  title: 'Best moment', hook: 'Watch this', score: 92, score_breakdown: { hook: 92 }, reason: 'Strong hook',
  status: 'candidate', degraded: false, provider_target: 'groq', caption_style: 'bold', ratio: '9:16', exports: [],
  preview: { status: 'queued', job_id: 'preview-1', export: null, error: null },
  final: { status: 'missing', job_id: null, export: null, error: null },
}

beforeEach(() => {
  setToken('clp_test')
  vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request) => {
    const url = String(input)
    if (url.endsWith('/api/v1/videos/video-1')) return new Response(JSON.stringify(video))
    if (url.endsWith('/api/v1/videos/video-1/clips')) return new Response(JSON.stringify([base, { ...base, id: 'clip-2', rank: 2, title: 'Short moment', score: 70, start_s: 70, end_s: 90, duration_s: 20, status: 'discarded' }]))
    if (url.includes('/transcript')) return new Response(JSON.stringify({ text: 'This is the spoken transcript.', words: [] }))
    return new Response('{}')
  }))
})
afterEach(() => { cleanup(); vi.unstubAllGlobals() })

describe('Review', () => {
  it('shows Ssemble-style result rows, transcript and filters', async () => {
    render(<MemoryRouter initialEntries={['/videos/video-1/clips']}><Routes><Route path="/videos/:videoId/clips" element={<Review />} /></Routes></MemoryRouter>)
    await waitFor(() => expect(screen.getByText('Best moment')).toBeTruthy())
    await waitFor(() => expect(screen.getAllByText('This is the spoken transcript.')).toHaveLength(2))
    expect(screen.getByText('Preparing previews 0/2')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: /discarded/i }))
    expect(screen.queryByText('Best moment')).toBeNull()
    expect(screen.getByText('Short moment')).toBeTruthy()
  })
})
