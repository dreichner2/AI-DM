import { useCallback, useLayoutEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { apiFetch } from './api'
import { normalizeContentRating, type ContentRating, type ContentSettings } from './contentSettings'
import type {
  SessionContentSettingsUpdateResponse,
  SessionState,
  SessionSummary,
} from './types'

type UseSessionContentSettingsOptions = {
  activeSessionId: number | null
  auth: string
  baseUrl: string
  canEditContentSettings: boolean
  contentSettings: ContentSettings
  clearAuthTokenErrors: () => void
  pushError: (category: 'validation' | 'persistence', message: string) => void
  sessionUpserted: (session: SessionSummary) => void
  setSessionState: Dispatch<SetStateAction<SessionState | null>>
}

type ContentSettingsPatch = {
  content_rating?: ContentRating
  tone_tags?: string[]
}

function normalizeToneTags(toneTags: string[]) {
  return toneTags
    .map((tag) => tag.trim().toLowerCase())
    .filter((tag, index, tags) => Boolean(tag) && tags.indexOf(tag) === index)
    .slice(0, 4)
}

export function useSessionContentSettings({
  activeSessionId,
  auth,
  baseUrl,
  canEditContentSettings,
  contentSettings,
  clearAuthTokenErrors,
  pushError,
  sessionUpserted,
  setSessionState,
}: UseSessionContentSettingsOptions) {
  const currentScopeKey = `${activeSessionId ?? ''}\u0000${auth}\u0000${baseUrl}`
  const [pendingScopeKey, setPendingScopeKey] = useState<string | null>(null)
  const requestIdRef = useRef(0)
  const pendingRef = useRef(false)
  const requestScopeRef = useRef({ activeSessionId, auth, baseUrl })

  useLayoutEffect(() => {
    requestScopeRef.current = { activeSessionId, auth, baseUrl }
    requestIdRef.current += 1
    pendingRef.current = false
    return () => {
      requestIdRef.current += 1
      pendingRef.current = false
    }
  }, [activeSessionId, auth, baseUrl])

  const patchContentSettings = useCallback(
    async (patch: ContentSettingsPatch, failureLabel: string) => {
      if (!activeSessionId || pendingRef.current) return

      pendingRef.current = true
      setPendingScopeKey(currentScopeKey)
      const requestId = ++requestIdRef.current
      const requestScope = { activeSessionId, auth, baseUrl }
      try {
        const response = await apiFetch<SessionContentSettingsUpdateResponse>(
          baseUrl,
          `/api/sessions/${activeSessionId}/content-settings`,
          auth,
          {
            method: 'PATCH',
            body: JSON.stringify(patch),
          },
        )
        if (
          requestIdRef.current !== requestId ||
          requestScopeRef.current.activeSessionId !== requestScope.activeSessionId ||
          requestScopeRef.current.auth !== requestScope.auth ||
          requestScopeRef.current.baseUrl !== requestScope.baseUrl
        ) {
          return
        }
        sessionUpserted(response.session)
        setSessionState(response.state)
        clearAuthTokenErrors()
      } catch (error) {
        if (requestIdRef.current !== requestId) return
        const message = error instanceof Error ? error.message : String(error)
        pushError('persistence', `${failureLabel} update failed: ${message}`)
      } finally {
        if (requestIdRef.current === requestId) {
          pendingRef.current = false
          setPendingScopeKey(null)
        }
      }
    },
    [
      activeSessionId,
      auth,
      baseUrl,
      clearAuthTokenErrors,
      currentScopeKey,
      pushError,
      sessionUpserted,
      setSessionState,
    ],
  )

  const updateContentRating = useCallback(
    async (rating: ContentRating) => {
      if (!activeSessionId) {
        pushError('validation', 'Choose a session before changing content rating.')
        return
      }
      if (!canEditContentSettings) {
        pushError('validation', 'Only table operators can change content rating.')
        return
      }
      const nextRating = normalizeContentRating(rating)
      if (nextRating === contentSettings.contentRating) return
      await patchContentSettings({ content_rating: nextRating }, 'Content rating')
    },
    [
      activeSessionId,
      canEditContentSettings,
      contentSettings.contentRating,
      patchContentSettings,
      pushError,
    ],
  )

  const updateContentToneTags = useCallback(
    async (toneTags: string[]) => {
      if (!activeSessionId) {
        pushError('validation', 'Choose a session before changing tone tags.')
        return
      }
      if (!canEditContentSettings) {
        pushError('validation', 'Only table operators can change tone tags.')
        return
      }
      const nextToneTags = normalizeToneTags(toneTags)
      if (
        nextToneTags.length === contentSettings.toneTags.length &&
        nextToneTags.every((tag, index) => tag === contentSettings.toneTags[index])
      ) {
        return
      }
      await patchContentSettings({ tone_tags: nextToneTags }, 'Tone tag')
    },
    [
      activeSessionId,
      canEditContentSettings,
      contentSettings.toneTags,
      patchContentSettings,
      pushError,
    ],
  )

  return {
    contentSettingsPending: pendingScopeKey === currentScopeKey,
    updateContentRating,
    updateContentToneTags,
  }
}
