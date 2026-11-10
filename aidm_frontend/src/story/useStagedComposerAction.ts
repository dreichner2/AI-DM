import { useCallback, useEffect, useRef, useState } from 'react'
import type { ActionComposerProps } from '../ActionComposer'

/** How long a staged action may wait for the composer state it depends on. */
const STAGE_WINDOW_MS = 2000

type StagedAction = {
  token: number
  expiresAt: number
  ready: (composer: ActionComposerProps) => boolean
  run: (composer: ActionComposerProps) => void
}

/**
 * The composer's setters update App state, so a submit made in the same tick
 * would read the old mode, item or roll. A staged action runs once, from an
 * effect, on the first render whose composer state satisfies `ready` — and is
 * dropped if that does not happen within a couple of seconds.
 */
export function useStagedComposerAction(composer: ActionComposerProps) {
  const [staged, setStaged] = useState<StagedAction | null>(null)
  const tokenRef = useRef(0)
  const consumedRef = useRef(0)

  useEffect(() => {
    if (!staged || consumedRef.current >= staged.token) return
    if (Date.now() > staged.expiresAt) {
      consumedRef.current = staged.token
      return
    }
    if (!staged.ready(composer)) return
    consumedRef.current = staged.token
    staged.run(composer)
  }, [composer, staged])

  return useCallback(
    (ready: StagedAction['ready'], run: StagedAction['run']) => {
      tokenRef.current += 1
      setStaged({ token: tokenRef.current, expiresAt: Date.now() + STAGE_WINDOW_MS, ready, run })
    },
    [],
  )
}
