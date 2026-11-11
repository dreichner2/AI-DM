import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiFetch } from '../api'
import type { ComingBackResponse, SessionStartResponse } from '../apiContract.generated'
import type { ActionIntent } from '../gameActions'
import { isRecord } from '../gameSelectors'
import { useLatest } from '../useLatest'

/**
 * What the Story view needs to bring a party back: the players' plans, the block that greets a returning party, the
 * next chapter begun by the next action after a chapter break, the question asked of a player leaving with no plan
 * noted this sitting, and a new adventure from the same ready-made pack.
 */
export type StoryReturnActions = {
  notePlan: (text: string) => Promise<boolean>
  removePlan: (planId: string) => Promise<void>
  loadComingBack: () => Promise<ComingBackResponse['comingBack']>
  /** After a chapter break: begin the next chapter, then send this action in it. */
  beginNextChapter: (message: string, intent?: ActionIntent) => void
  playAgain: (examplePackId: string) => void
  /** Set while the leaving question is asked. */
  leaving: { note: (text: string) => void; skip: () => void } | null
}

type CarriedAction = { sessionId: number; message: string; intent?: ActionIntent }

type Options = {
  baseUrl: string
  auth: string
  sessionId: number | null
  playerId: number | null
  /** The open chapter is joined and loaded: an action can be sent in it. */
  ready: boolean
  ended: boolean
  /** The session's plans (`state_snapshot.journal.plans`), to tell whether this player noted one this sitting. */
  plans: unknown
  reload: (sessionId: number) => Promise<void>
  follow: (sessionId: number) => void
  submit: (message: string, intent?: ActionIntent) => boolean
  exitToTitle: () => void
  playNow: (options: { fresh: boolean; examplePackId?: string }) => void
}

export function useStoryReturn(options: Options) {
  const latest = useLatest(options)
  const carried = useRef<CarriedAction | null>(null)
  const [leavingAsked, setLeavingAsked] = useState(false)
  // A sitting, for the leaving question, is this visit to the story.
  const [sittingStart] = useState(Date.now)
  const notedHere = useRef(false)

  const request = useCallback(
    <T,>(path: string, init?: RequestInit) => {
      const { baseUrl, auth, sessionId } = latest.current
      return apiFetch<T>(baseUrl, `/api/sessions/${sessionId}${path}`, auth, init)
    },
    [latest],
  )

  const notePlan = useCallback(
    async (text: string) => {
      const { sessionId, playerId, reload } = latest.current
      if (!sessionId || !playerId) return false
      try {
        await request('/plans', { method: 'POST', body: JSON.stringify({ text, player_id: playerId }) })
      } catch {
        return false
      }
      notedHere.current = true
      void reload(sessionId)
      return true
    },
    [latest, request],
  )

  const leave = useCallback(() => {
    setLeavingAsked(false)
    latest.current.exitToTitle()
  }, [latest])

  const askBeforeLeaving = useCallback(() => {
    const { ended, playerId, plans } = latest.current
    const noted =
      notedHere.current ||
      (Array.isArray(plans) &&
        plans.some((plan) => isRecord(plan) && plan.playerId === playerId && Date.parse(String(plan.createdAt)) >= sittingStart))
    if (ended || !playerId || noted) leave()
    else setLeavingAsked(true)
  }, [latest, leave, sittingStart])

  const actions = useMemo<StoryReturnActions>(
    () => ({
      notePlan,
      removePlan: async (planId) => {
        await request(`/plans/${encodeURIComponent(planId)}`, { method: 'DELETE' })
        const { sessionId, reload } = latest.current
        if (sessionId) void reload(sessionId)
      },
      loadComingBack: async () => {
        const { playerId } = latest.current
        return (await request<ComingBackResponse>(`/coming-back?view=player${playerId ? `&player_id=${playerId}` : ''}`)).comingBack
      },
      beginNextChapter: (message, intent) => {
        request<SessionStartResponse>('/next-chapter', { method: 'POST' }).then(
          (started) => {
            carried.current = { sessionId: started.session_id, message, intent }
            latest.current.follow(started.session_id)
          },
          // Not yet (a fight or a roll still open): the action belongs to this chapter.
          () => latest.current.submit(message, intent),
        )
      },
      playAgain: (examplePackId) => latest.current.playNow({ fresh: true, examplePackId }),
      leaving: leavingAsked ? { note: (text) => void notePlan(text).then(leave), skip: leave } : null,
    }),
    [latest, leave, leavingAsked, notePlan, request],
  )

  // Once the new chapter is joined and loaded, the action that began it goes there.
  const { sessionId, ready } = options
  useEffect(() => {
    const action = carried.current
    if (!action || action.sessionId !== sessionId || !ready) return undefined
    const timer = window.setTimeout(() => {
      if (carried.current === action && latest.current.submit(action.message, action.intent)) carried.current = null
    }, 0)
    return () => window.clearTimeout(timer)
  }, [latest, ready, sessionId])

  return { storyReturn: actions, askBeforeLeaving }
}
