import { useEffect, useState } from 'react'

import { v1, type V1ArtifactState } from '../api-v1'

export function ClipMedia({ artifact, title, className = '' }: { artifact: V1ArtifactState; title: string; className?: string }) {
  const [source, setSource] = useState<string | null>(null)

  useEffect(() => {
    const exportId = artifact.export?.id
    if (!exportId) { setSource(null); return }
    let cancelled = false
    let revoke = false
    let loaded: string | null = null
    void v1.playableExport(exportId).then((result) => {
      if (cancelled) { if (result.revoke) URL.revokeObjectURL(result.url); return }
      loaded = result.url
      revoke = result.revoke
      setSource(result.url)
    }).catch(() => setSource(null))
    return () => { cancelled = true; if (revoke && loaded) URL.revokeObjectURL(loaded) }
  }, [artifact.export?.id])

  if (!source) {
    const label = artifact.status === 'failed'
      ? artifact.error || 'Preview failed'
      : artifact.status === 'running'
        ? 'Preparing preview…'
        : artifact.status === 'queued'
          ? 'Preview queued…'
          : artifact.status === 'stale'
            ? 'Preview outdated — regenerate it after your last edit.'
            : artifact.status === 'cancelled'
              ? 'Preview cancelled'
              : 'Preview pending'
    return <div className={`thumb flex items-center justify-center ${className}`}><p className="px-4 text-center text-xs text-ink-500">{label}</p></div>
  }
  return <video controls playsInline preload="metadata" src={source} aria-label={title} className={`aspect-[9/16] w-full rounded-xl bg-ink-900 object-contain ${className}`} />
}
