import { lazy, type ComponentType } from 'react'

/** Where the browser cannot say when it is idle, the wait instead. */
const IDLE_FALLBACK_MS = 1_000

/** A thenable that calls back at once, so React.lazy renders code that has already arrived in the same pass. */
function settled<T>(value: T) {
  return { then: (resolve: (value: T) => void) => resolve(value) } as unknown as Promise<T>
}

/**
 * A module fetched on demand that can also be fetched ahead, when the player
 * reaches for what it shows. Once its code has arrived, its lazy components
 * render at once: React.lazy over a bare import() always shows the fallback
 * first and reveals the component no sooner than 300 ms later.
 */
export function lazyModule<Module>(load: () => Promise<Module>) {
  let loaded: Module | null = null
  let pending: Promise<Module> | null = null
  const fetchModule = () =>
    (pending ??= load().then(
      (module) => (loaded = module),
      (error: unknown) => {
        // A fetch that failed (offline for a moment) is tried again next time.
        pending = null
        throw error
      },
    ))
  return {
    /** Starts fetching the code; calling it again, or after the code has arrived, does nothing. */
    preload() {
      fetchModule().catch(() => undefined)
    },
    isLoaded: () => loaded !== null,
    component<Props>(pick: (module: Module) => ComponentType<Props>) {
      return lazy(() => {
        const toComponent = (module: Module) => ({ default: pick(module) })
        return loaded ? settled(toComponent(loaded)) : fetchModule().then(toComponent)
      })
    },
  }
}

/** Runs `task` once the browser is idle; returns a function that cancels it. */
export function whenIdle(task: () => void) {
  if (typeof window.requestIdleCallback === 'function') {
    const handle = window.requestIdleCallback(task)
    return () => window.cancelIdleCallback(handle)
  }
  const timer = window.setTimeout(task, IDLE_FALLBACK_MS)
  return () => window.clearTimeout(timer)
}
