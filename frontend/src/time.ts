/**
 * Timecode parsing for the manual section editor.
 *
 * Mirrors what a user types for a "start → end" pair: plain seconds
 * ("125", "12.5"), "m:ss", or "h:mm:ss". Anything else is a mistake worth
 * surfacing inline rather than silently coercing to 0.
 */
export function parseTimecode(input: string): number | null {
  const trimmed = input.trim()
  if (!trimmed) return null

  // Plain seconds, optionally fractional.
  if (/^\d+(\.\d+)?$/.test(trimmed)) {
    return Number(trimmed)
  }

  const parts = trimmed.split(':')
  if (parts.length < 2 || parts.length > 3 || parts.some((p) => p === '')) return null
  if (!parts.every((p) => /^\d+(\.\d+)?$/.test(p))) return null

  const numbers = parts.map(Number)
  const [h, m, s] = numbers.length === 3 ? numbers : [0, ...numbers]
  if (m >= 60 || s >= 60) return null

  return h * 3600 + m * 60 + s
}

/** Format seconds as h:mm:ss past an hour, otherwise m:ss. Whole seconds only —
 * this is for reading back a section list, not for frame-accurate trimming. */
export function formatTimecode(seconds: number): string {
  const total = Math.max(0, Math.round(seconds))
  const s = total % 60
  const m = Math.floor(total / 60) % 60
  const h = Math.floor(total / 3600)
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
}
