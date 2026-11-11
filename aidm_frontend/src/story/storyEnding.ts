import { useSyncExternalStore } from 'react'

/**
 * Whether the reader chose `Keep playing` past a session's ending. The
 * epilogue and the dock both follow it: until then the story has ended and
 * nothing is suggested. Remembered for the browser tab, per session.
 */

/** Session storage key under which a dismissed epilogue is remembered for one session. */
const ENDING_DISMISSED_KEY = 'aidm:storyEndingDismissed'

// Holds a choice for this page when the browser refuses storage.
const dismissedInPage = new Set<string>()
const listeners = new Set<() => void>()

function storageKey(sessionId: number | null) {
  return `${ENDING_DISMISSED_KEY}:${sessionId ?? 'none'}`
}

function endingDismissed(sessionId: number | null) {
  const key = storageKey(sessionId)
  if (dismissedInPage.has(key)) return true
  try {
    return window.sessionStorage.getItem(key) === '1'
  } catch {
    return false
  }
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** `Keep playing`: the session's ending stays behind the reader for the rest of the tab's life. */
export function dismissEnding(sessionId: number | null) {
  const key = storageKey(sessionId)
  try {
    window.sessionStorage.setItem(key, '1')
  } catch {
    // Without storage the choice holds until the page reloads.
    dismissedInPage.add(key)
  }
  listeners.forEach((listener) => listener())
}

/** True once `Keep playing` was chosen for this session's ending. */
export function useEndingDismissed(sessionId: number | null) {
  return useSyncExternalStore(subscribe, () => endingDismissed(sessionId), () => false)
}
