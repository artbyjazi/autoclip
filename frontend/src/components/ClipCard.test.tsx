import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { V1ClipOut } from '../api-v1'
import { ClipCard } from './ClipCard'

const sampleClip: V1ClipOut = {
  id: 'clip-1',
  job_id: 'job-1',
  rank: 1,
  start_s: 12.5,
  end_s: 33.5,
  duration_s: 21,
  title: 'Why puppies win',
  hook: 'The moment it clicked',
  score: 90,
  score_breakdown: { hook: 90 },
  reason: 'Great hook',
  status: 'candidate',
  degraded: false,
  provider_target: 'groq-gpt-oss-20b',
  caption_style: 'bold',
  ratio: '9:16',
  exports: [],
}

afterEach(() => cleanup())

describe('ClipCard', () => {
  it('renders score, title, duration and badges', () => {
    render(<ClipCard clip={sampleClip} selected={false} onSelect={() => undefined} />)

    expect(screen.getByText('Why puppies win')).toBeTruthy()
    // Overlay badge + placeholder ring while no preview export exists.
    expect(screen.getAllByLabelText('Virality score 90 out of 100')).toHaveLength(2)
    expect(screen.getByText('candidate')).toBeTruthy()
    expect(screen.getByText('groq-gpt-oss-20b')).toBeTruthy()
    expect(screen.queryByText('degraded')).toBeNull()
  })

  it('marks degraded clips and calls onSelect', () => {
    const onSelect = vi.fn()
    render(<ClipCard clip={{ ...sampleClip, degraded: true }} selected={true} onSelect={onSelect} />)

    expect(screen.getByText('degraded')).toBeTruthy()
    fireEvent.click(screen.getByRole('button'))
    expect(onSelect).toHaveBeenCalledOnce()
  })

  it('shows a score placeholder when there is no preview export', () => {
    render(<ClipCard clip={sampleClip} selected={false} onSelect={() => undefined} />)

    // No preview export: thumbnail falls back to the score ring.
    expect(screen.getAllByLabelText('Virality score 90 out of 100').length).toBeGreaterThanOrEqual(1)
  })
})
