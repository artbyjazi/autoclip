import { ApiError } from '../format'

/**
 * A failure and, where the server offered one, the fix.
 *
 * The hint carries most of the value: "YouTube blocked this download" is a
 * dead end, while "set a browser to pull cookies from" is something the user
 * can act on without leaving the app.
 */
export function ErrorNote({
  error,
  onDismiss,
}: {
  error: Error
  onDismiss?: () => void
}) {
  const hint = error instanceof ApiError ? error.hint : ''

  return (
    <div className="border-l-2 border-signal-bad pl-4">
      <div className="flex items-start justify-between gap-4">
        <p className="text-sm font-medium text-ink-100">{error.message}</p>
        {onDismiss && (
          <button onClick={onDismiss} className="btn btn-quiet shrink-0" aria-label="Dismiss">
            ✕
          </button>
        )}
      </div>
      {hint && (
        <p className="mt-2 max-w-prose whitespace-pre-line text-sm leading-relaxed text-ink-400">
          {hint}
        </p>
      )}
    </div>
  )
}
