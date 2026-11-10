import { useLayoutEffect, useRef } from 'react'

/**
 * Keeps a stable ref whose `current` is the value from the latest committed
 * render. Effects and event handlers read through it so that changing
 * callback identities never re-run long-lived effects such as the session
 * socket.
 */
export function useLatest<T>(value: T): { readonly current: T } {
  const ref = useRef(value)
  useLayoutEffect(() => {
    ref.current = value
  })
  return ref
}
