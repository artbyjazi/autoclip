/** Circular virality score: 0–100 rendered as an SVG ring. */
export function ScoreRing({ score, size = 56 }: { score: number; size?: number }) {
  const radius = 26
  const circumference = 2 * Math.PI * radius
  const clamped = Math.max(0, Math.min(100, Math.round(score)))
  const filled = (clamped / 100) * circumference

  return (
    <span
      className="relative inline-flex items-center justify-center"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Virality score ${clamped} out of 100`}
    >
      <svg width={size} height={size} viewBox="0 0 64 64" className="score-ring">
        <circle cx="32" cy="32" r={radius} fill="none" strokeWidth="7" className="score-ring-track" />
        <circle
          cx="32"
          cy="32"
          r={radius}
          fill="none"
          strokeWidth="7"
          className="score-ring-value"
          strokeDasharray={`${filled} ${circumference}`}
        />
      </svg>
      <span className="numeric absolute text-sm font-bold text-ink-900">{clamped}</span>
    </span>
  )
}
