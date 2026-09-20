import type { V1ClipOut } from '../api-v1'
import { formatDuration } from '../format'
import { ClipThumb } from './ClipThumb'
import { ScoreRing } from './ScoreRing'

/** Product clip card: thumbnail, score ring, title, badges. */
export function ClipCard({
  clip,
  selected,
  onSelect,
}: {
  clip: V1ClipOut
  selected: boolean
  onSelect: () => void
}) {
  return (
    <button
      onClick={onSelect}
      className={[
        'card group overflow-hidden p-0 text-left transition-shadow duration-200',
        selected ? 'ring-2 ring-iris-600' : 'hover:shadow-pop',
      ].join(' ')}
      aria-pressed={selected}
    >
      <div className="relative">
        <ClipThumb clip={clip} />
        <span className="absolute left-2 top-2 rounded-full bg-white/95 px-2 py-0.5 shadow-card">
          <ScoreRing score={clip.score} size={40} />
        </span>
        {clip.degraded && (
          <span className="badge badge-warn absolute right-2 top-2">degraded</span>
        )}
      </div>
      <div className="p-3">
        <span className="block truncate text-sm font-semibold text-ink-900 group-hover:text-iris-600">
          {clip.title}
        </span>
        <span className="numeric mt-1 flex items-center justify-between text-xs text-ink-500">
          <span>
            {formatDuration(clip.start_s)}–{formatDuration(clip.end_s)}
          </span>
          <span>#{clip.rank}</span>
        </span>
        <span className="mt-2 flex flex-wrap gap-1.5">
          <span className="badge">{clip.status}</span>
          {clip.provider_target && (
            <span className="badge numeric max-w-full truncate">{clip.provider_target}</span>
          )}
        </span>
      </div>
    </button>
  )
}
