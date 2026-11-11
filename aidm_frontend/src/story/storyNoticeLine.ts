import { createContext, useCallback, useContext, useEffect, useState } from 'react'

/** How long a notice that is not an error stays on the line. */
export const NOTICE_FADE_MS = 6_000

export type StoryNotice = {
  /** Identifies one occurrence: a new roll, error or redirect gets a new key, so it counts as newest. */
  key: string
  text: string
  /** An error: announced as an alert, and it stays until dismissed or replaced. */
  tone?: 'fail'
  action?: { label: string; onSelect: () => void }
  /** Leaves the line by itself after `NOTICE_FADE_MS`; errors never do. */
  fades?: boolean
}

/** The one notice the line shows now, and how to dismiss it. */
export type StoryNoticeLineState = {
  notice: StoryNotice | null
  dismiss: () => void
}

/**
 * Picks the line's notice from every notice that currently applies (in any
 * order): the most recently arrived one the player has not dismissed. A
 * notice that fades is dismissed by itself after `NOTICE_FADE_MS`.
 */
export function useStoryNoticeLine(notices: StoryNotice[]): StoryNoticeLineState {
  const [arrivalOrder, setArrivalOrder] = useState<string[]>([])
  const [dismissed, setDismissed] = useState<string[]>([])
  const activeKeys = notices.map((notice) => notice.key)
  const stillActive = arrivalOrder.filter((key) => activeKeys.includes(key))
  const arrived = activeKeys.filter((key) => !arrivalOrder.includes(key))
  if (arrived.length || stillActive.length !== arrivalOrder.length) {
    setArrivalOrder([...stillActive, ...arrived])
  }
  const stillDismissed = dismissed.filter((key) => activeKeys.includes(key))
  if (stillDismissed.length !== dismissed.length) setDismissed(stillDismissed)
  const newestKey = [...stillActive, ...arrived].filter((key) => !stillDismissed.includes(key)).at(-1)
  const notice = notices.find((candidate) => candidate.key === newestKey) ?? null
  const noticeKey = notice?.key ?? null
  const fades = Boolean(notice?.fades) && notice?.tone !== 'fail'

  const dismiss = useCallback(() => {
    if (noticeKey === null) return
    setDismissed((current) => (current.includes(noticeKey) ? current : [...current, noticeKey]))
  }, [noticeKey])

  useEffect(() => {
    if (!fades) return undefined
    const timer = window.setTimeout(dismiss, NOTICE_FADE_MS)
    return () => window.clearTimeout(timer)
  }, [dismiss, fades])

  return { notice, dismiss }
}

/**
 * The notice line as App shares it with open sheets, which show it at their
 * foot. `null` outside the Story view.
 */
export const StoryNoticeContext = createContext<StoryNoticeLineState | null>(null)

/** The shared notice line, for a sheet's foot. */
export function useSharedStoryNoticeLine() {
  return useContext(StoryNoticeContext)
}
