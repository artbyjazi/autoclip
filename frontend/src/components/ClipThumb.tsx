import { useEffect, useRef, useState } from 'react'

import { v1, type V1ClipOut } from '../api-v1'
import { ScoreRing } from './ScoreRing'

/**
 * Lazy 9:16 thumbnail for a clip.
 *
 * Fetches the preview export blob only while the card is in the viewport
 * (IntersectionObserver) and revokes it when it leaves, so a grid of clips
 * never holds N videos in memory. Plays muted on hover, like product clip
 * grids do. Clips with no preview export render a score placeholder.
 */
export function ClipThumb({ clip, playOnHover = true }: { clip: V1ClipOut; playOnHover?: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  const videoRef = useRef<HTMLVideoElement>(null)
  const [url, setUrl] = useState<string | null>(null)

  const previewId = clip.exports.find((e) => e.kind === 'preview')?.id ?? null

  useEffect(() => {
    const node = ref.current
    if (!node || !previewId) return
    let cancelled = false
    let objectUrl: string | null = null

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.some((entry) => entry.isIntersecting)
        if (visible && !objectUrl) {
          void v1
            .exportBlob(previewId)
            .then((blobUrl) => {
              if (cancelled) {
                URL.revokeObjectURL(blobUrl)
                return
              }
              objectUrl = blobUrl
              setUrl(blobUrl)
            })
            .catch(() => undefined)
        } else if (!visible && objectUrl) {
          URL.revokeObjectURL(objectUrl)
          objectUrl = null
          setUrl(null)
        }
      },
      { rootMargin: '200px' },
    )
    observer.observe(node)
    return () => {
      cancelled = true
      observer.disconnect()
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [previewId])

  const onEnter = () => {
    if (playOnHover) void videoRef.current?.play().catch(() => undefined)
  }
  const onLeave = () => {
    const video = videoRef.current
    if (video) {
      video.pause()
      video.currentTime = 0
    }
  }

  return (
    <div ref={ref} className="thumb shimmer" onMouseEnter={onEnter} onMouseLeave={onLeave}>
      {url ? (
        <video ref={videoRef} muted loop playsInline preload="metadata" src={url} />
      ) : (
        <span className="absolute inset-0 flex items-center justify-center">
          <ScoreRing score={clip.score} size={64} />
        </span>
      )}
    </div>
  )
}
