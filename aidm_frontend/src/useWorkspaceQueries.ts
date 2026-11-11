import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { ApiClientError, apiFetch, storedRuntimeAccessSnapshot } from './api'
import type {
  BetaSummary,
  Campaign,
  CampaignWorkspace,
  Health,
  LlmRuntimeConfig,
  SessionListResponse,
  SessionLogResponse,
  SessionLogEntry,
  SessionState,
  SessionSummary,
  StreamingTurn,
  TimelineEntry,
  TtsRuntimeConfig,
  World,
} from './types'
import { useLatest } from './useLatest'
import type { SessionPage } from './useWorkspaceStore'

/** Sessions per page in a campaign's chapter list; the workspace loads the first page. */
export const SESSION_PAGE_SIZE = 30

export type CampaignSessionMeta = {
  count: number
  updatedAt: string | null
  latestSessionId: number | null
}

type ValueUpdater<T> = T | ((current: T) => T)

type WorkspaceQueryErrorCategory = 'connection' | 'workspace'

/**
 * The query the Story view adds to every state, log and workspace read: the
 * player-safe projection, with legality and private data for one hero. Table
 * tools reads without it ('').
 */
export function playerViewQuery(storyView: boolean, playerId: number | null) {
  if (!storyView) return ''
  return playerId === null ? 'view=player' : `view=player&player_id=${playerId}`
}

function withQuery(path: string, query: string) {
  return query ? `${path}${path.includes('?') ? '&' : '?'}${query}` : path
}

type OlderLogLoadingScope = {
  auth: string
  baseUrl: string
  requestId: number
  sessionId: number
}

type OlderSessionsLoadingScope = {
  auth: string
  baseUrl: string
  campaignId: number
}

type UseWorkspaceQueriesOptions = {
  auth: string
  baseUrl: string
  /** `playerViewQuery(...)` for the current play view and hero. */
  playerView?: string
  operatorDataEnabled: boolean
  runtimeConfigHeaders?: HeadersInit
  sessions: SessionSummary[]
  /** Cursor for the selected campaign's next older page of sessions. */
  sessionsCursor: string | null
  selectedCampaignId: number | null
  selectedSessionId: number | null
  sessionLogCursor: number | null
  sessionLogHasMore: boolean
  setHealth: Dispatch<SetStateAction<Health | null>>
  setMetrics: Dispatch<SetStateAction<BetaSummary | null>>
  setLlmConfig: Dispatch<SetStateAction<LlmRuntimeConfig | null>>
  setTtsConfig: Dispatch<SetStateAction<TtsRuntimeConfig | null>>
  setTtsConfigLoadFailed: Dispatch<SetStateAction<boolean>>
  setWorlds: Dispatch<SetStateAction<World[]>>
  setCampaignSessionMeta: Dispatch<SetStateAction<Record<number, CampaignSessionMeta>>>
  setSelectedCampaignId: (value: ValueUpdater<number | null>) => void
  setSelectedSessionId: (value: ValueUpdater<number | null>) => void
  setSelectedPlayerId: (value: ValueUpdater<number | null>) => void
  setSessionState: (value: ValueUpdater<SessionState | null>) => void
  setLogEntries: (value: ValueUpdater<SessionLogEntry[]>) => void
  setSessionLogCursor: (value: ValueUpdater<number | null>) => void
  setSessionLogHasMore: (value: ValueUpdater<boolean>) => void
  setWorkspaceLoading: (value: ValueUpdater<boolean>) => void
  setLoadingCampaignId: (value: ValueUpdater<number | null>) => void
  setSessionLoading: (value: ValueUpdater<boolean>) => void
  sessionPageLoaded: (page: SessionPage) => void
  onSessionLogLoaded: (entries: SessionLogEntry[]) => void
  pendingSubmissionCampaignId: () => number | null
  rootCampaignsLoaded: (campaigns: Campaign[]) => void
  campaignWorkspaceLoaded: (workspace: CampaignWorkspace) => void
  olderSessionsLoaded: (page: SessionListResponse) => void
  setOptimisticEntries: Dispatch<SetStateAction<TimelineEntry[]>>
  setStreamingTurn: Dispatch<SetStateAction<StreamingTurn | null>>
  setSendPending: Dispatch<SetStateAction<boolean>>
  pushError: (category: WorkspaceQueryErrorCategory, message: string) => void
  onUnauthorized: () => void
}

function isUnauthorizedError(error: unknown) {
  return error instanceof ApiClientError && error.status === 401
}

function isNotFoundError(error: unknown) {
  return error instanceof ApiClientError && error.status === 404
}

function isForbiddenError(error: unknown) {
  return error instanceof ApiClientError && error.status === 403
}

/** The hero a read was made as (`player_id` of its player view), or null. */
function viewedHeroId(view: string) {
  const match = /(?:^|&)player_id=(\d+)/.exec(view)
  return match ? Number(match[1]) : null
}

/** The same view without its hero: the player view with nothing private, or the full view as it is. */
function withoutHero(view: string) {
  return view.replace(/&?player_id=\d+/, '')
}

function latestTimestamp(values: Array<string | null | undefined>): string | null {
  let latest: string | null = null
  let latestMs = 0
  values.forEach((value) => {
    if (!value) return
    const time = new Date(value).getTime()
    if (!Number.isNaN(time) && time >= latestMs) {
      latestMs = time
      latest = value
    }
  })
  return latest
}

function sessionMetaFromCampaign(campaign: Campaign): CampaignSessionMeta {
  return {
    count: campaign.session_count ?? 0,
    updatedAt: campaign.latest_activity_at ?? campaign.created_at,
    latestSessionId: campaign.latest_session_id ?? null,
  }
}

export function useWorkspaceQueries({
  auth,
  baseUrl,
  playerView = '',
  operatorDataEnabled,
  runtimeConfigHeaders,
  sessions,
  sessionsCursor,
  selectedCampaignId,
  selectedSessionId,
  sessionLogCursor,
  sessionLogHasMore,
  setHealth,
  setMetrics,
  setLlmConfig,
  setTtsConfig,
  setTtsConfigLoadFailed,
  setWorlds,
  setCampaignSessionMeta,
  setSelectedCampaignId,
  setSelectedSessionId,
  setSelectedPlayerId,
  setSessionState,
  setLogEntries,
  setSessionLogCursor,
  setSessionLogHasMore,
  setWorkspaceLoading,
  setLoadingCampaignId,
  setSessionLoading,
  sessionPageLoaded,
  onSessionLogLoaded,
  pendingSubmissionCampaignId,
  rootCampaignsLoaded,
  campaignWorkspaceLoaded,
  olderSessionsLoaded,
  setOptimisticEntries,
  setStreamingTurn,
  setSendPending,
  pushError,
  onUnauthorized,
}: UseWorkspaceQueriesOptions) {
  // Read at call time so a workspace cache update or an auth-dialog change
  // never gives loadSessionData a new identity (that would re-run every
  // effect keyed on it and reload the session).
  const latest = useLatest({ sessions, onSessionLogLoaded, onUnauthorized, pendingSubmissionCampaignId, playerView })
  const rootRequestRef = useRef(0)
  const operatorRequestRef = useRef(0)
  const llmConfigRequestRef = useRef(0)
  const operatorDataEnabledRef = useRef(operatorDataEnabled)
  const workspaceRequestRef = useRef(0)
  const sessionRequestRef = useRef(0)
  const olderLogRequestRef = useRef(0)
  const olderLogLoadingRef = useRef(false)
  const olderSessionsRequestRef = useRef(0)
  const olderSessionsLoadingRef = useRef(false)
  const runtimeScopeRef = useRef({ auth, baseUrl })
  const selectedCampaignIdRef = useRef(selectedCampaignId)
  const selectedSessionIdRef = useRef(selectedSessionId)
  const rootLoadingRequestRef = useRef<number | null>(null)
  const campaignLoadingRequestRef = useRef<{ campaignId: number; requestId: number } | null>(null)
  const [olderLogLoadingScope, setOlderLogLoadingScope] = useState<OlderLogLoadingScope | null>(null)
  const olderLogLoading = Boolean(
    olderLogLoadingScope &&
    olderLogLoadingScope.auth === auth &&
    olderLogLoadingScope.baseUrl === baseUrl &&
    olderLogLoadingScope.sessionId === selectedSessionId,
  )
  const [olderSessionsLoadingScope, setOlderSessionsLoadingScope] = useState<OlderSessionsLoadingScope | null>(null)
  const olderSessionsLoading = Boolean(
    olderSessionsLoadingScope &&
    olderSessionsLoadingScope.auth === auth &&
    olderSessionsLoadingScope.baseUrl === baseUrl &&
    olderSessionsLoadingScope.campaignId === selectedCampaignId,
  )

  useEffect(() => {
    runtimeScopeRef.current = { auth, baseUrl }
    rootRequestRef.current += 1
    operatorRequestRef.current += 1
    llmConfigRequestRef.current += 1
    workspaceRequestRef.current += 1
    const invalidatedSessionRequestId = ++sessionRequestRef.current
    olderLogRequestRef.current += 1
    olderLogLoadingRef.current = false
    olderSessionsRequestRef.current += 1
    olderSessionsLoadingRef.current = false
    rootLoadingRequestRef.current = null
    campaignLoadingRequestRef.current = null
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      const currentScope = runtimeScopeRef.current
      if (currentScope.auth !== auth || currentScope.baseUrl !== baseUrl) return
      if (rootLoadingRequestRef.current === null && campaignLoadingRequestRef.current === null) {
        setWorkspaceLoading(false)
        setLoadingCampaignId(null)
      }
      if (sessionRequestRef.current === invalidatedSessionRequestId) {
        setSessionLoading(false)
      }
    })
    return () => {
      cancelled = true
    }
  }, [auth, baseUrl, setLoadingCampaignId, setSessionLoading, setWorkspaceLoading])

  useEffect(() => {
    selectedCampaignIdRef.current = selectedCampaignId
    workspaceRequestRef.current += 1
    campaignLoadingRequestRef.current = null
    olderSessionsRequestRef.current += 1
    olderSessionsLoadingRef.current = false
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
      setOlderSessionsLoadingScope(null)
      const campaignRequest = campaignLoadingRequestRef.current
      setWorkspaceLoading(rootLoadingRequestRef.current !== null || campaignRequest !== null)
      setLoadingCampaignId(campaignRequest?.campaignId ?? null)
    })
    return () => {
      cancelled = true
    }
  }, [selectedCampaignId, setLoadingCampaignId, setWorkspaceLoading])

  useEffect(() => {
    selectedSessionIdRef.current = selectedSessionId
    const invalidatedSessionRequestId = ++sessionRequestRef.current
    olderLogRequestRef.current += 1
    olderLogLoadingRef.current = false
    let cancelled = false
    queueMicrotask(() => {
      if (!cancelled && sessionRequestRef.current === invalidatedSessionRequestId) {
        setSessionLoading(false)
      }
    })
    return () => {
      cancelled = true
    }
  }, [selectedSessionId, setSessionLoading])

  const isCurrentRequest = useCallback(
    (
      requestRef: { current: number },
      requestId: number,
      requestAuth: string,
      requestBaseUrl: string,
      requestAccessSnapshot: string,
    ) => {
      const currentScope = runtimeScopeRef.current
      return (
        requestRef.current === requestId &&
        currentScope.auth === requestAuth &&
        currentScope.baseUrl === requestBaseUrl &&
        requestAccessSnapshot === storedRuntimeAccessSnapshot(requestAuth)
      )
    },
    [],
  )

  const clearSessionData = useCallback(() => {
    sessionRequestRef.current += 1
    olderLogRequestRef.current += 1
    olderLogLoadingRef.current = false
    setLogEntries([])
    setSessionLogCursor(null)
    setSessionLogHasMore(false)
    setSessionState(null)
    setSessionLoading(false)
    setOlderLogLoadingScope(null)
  }, [
    setLogEntries,
    setSessionLoading,
    setSessionLogCursor,
    setSessionLogHasMore,
    setSessionState,
  ])

  const loadSessionData = useCallback(
    async (sessionId: number) => {
      const requestId = ++sessionRequestRef.current
      olderLogRequestRef.current += 1
      olderLogLoadingRef.current = false
      setOlderLogLoadingScope(null)
      const requestAuth = auth
      const requestBaseUrl = baseUrl
      const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
      const view = latest.current.playerView
      setSessionLoading(true)
      try {
        const [logData, stateData] = await Promise.all([
          apiFetch<SessionLogResponse>(
            baseUrl,
            withQuery(`/api/sessions/${sessionId}/log?limit=200`, view),
            requestAuth,
          ),
          apiFetch<SessionState>(baseUrl, withQuery(`/api/sessions/${sessionId}/state`, view), requestAuth),
        ])
        if (!isCurrentRequest(
          sessionRequestRef,
          requestId,
          requestAuth,
          requestBaseUrl,
          requestAccessSnapshot,
        )) return
        sessionPageLoaded({
          sessionId,
          entries: logData.entries,
          nextCursor: logData.next_cursor ?? null,
          hasMore: Boolean(logData.has_more),
          sessionState: stateData,
        })
        latest.current.onSessionLogLoaded(logData.entries)
        const { sessions } = latest.current
        setCampaignSessionMeta((current) => {
          const existing = current[stateData.campaign_id]
          return {
            ...current,
            [stateData.campaign_id]: {
              count: existing?.count ?? sessions.length,
              latestSessionId: existing?.latestSessionId ?? sessionId,
              updatedAt: latestTimestamp([
                existing?.updatedAt,
                stateData.updated_at,
                sessions.find((session) => session.session_id === sessionId)?.created_at,
              ]),
            },
          }
        })
      } catch (error) {
        if (!isCurrentRequest(
          sessionRequestRef,
          requestId,
          requestAuth,
          requestBaseUrl,
          requestAccessSnapshot,
        )) return
        const heroId = viewedHeroId(view)
        if (isUnauthorizedError(error)) {
          latest.current.onUnauthorized()
        } else if (isForbiddenError(error) && heroId !== null) {
          // A remembered hero this chapter will not be read as: forget the hero, and the chapter is read again.
          setSelectedPlayerId((current) => (current === heroId ? null : current))
          return
        } else if (isNotFoundError(error) || isForbiddenError(error)) {
          // A remembered chapter the server no longer has, or will not show: forget it.
          setSelectedSessionId((current) => (current === sessionId ? null : current))
          setLogEntries([])
          setSessionLogCursor(null)
          setSessionLogHasMore(false)
          setSessionState(null)
          return
        }
        throw error
      } finally {
        if (isCurrentRequest(
          sessionRequestRef,
          requestId,
          requestAuth,
          requestBaseUrl,
          requestAccessSnapshot,
        )) {
          setSessionLoading(false)
        }
      }
    },
    [
      auth,
      baseUrl,
      isCurrentRequest,
      latest,
      sessionPageLoaded,
      setCampaignSessionMeta,
      setLogEntries,
      setSessionLoading,
      setSessionLogCursor,
      setSessionLogHasMore,
      setSelectedPlayerId,
      setSelectedSessionId,
      setSessionState,
    ],
  )

  const loadOlderSessionLog = useCallback(async () => {
    if (!selectedSessionId || !sessionLogHasMore || olderLogLoadingRef.current || sessionLogCursor === null) return
    const requestId = ++olderLogRequestRef.current
    const requestSessionId = selectedSessionId
    const requestAuth = auth
    const requestBaseUrl = baseUrl
    const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
    olderLogLoadingRef.current = true
    setOlderLogLoadingScope({
      auth: requestAuth,
      baseUrl: requestBaseUrl,
      requestId,
      sessionId: requestSessionId,
    })
    try {
      const data = await apiFetch<SessionLogResponse>(
        baseUrl,
        withQuery(`/api/sessions/${requestSessionId}/log?limit=200&before_id=${sessionLogCursor}`, latest.current.playerView),
        requestAuth,
      )
      if (
        !isCurrentRequest(
          olderLogRequestRef,
          requestId,
          requestAuth,
          requestBaseUrl,
          requestAccessSnapshot,
        ) || selectedSessionIdRef.current !== requestSessionId
      ) return
      setLogEntries((current) => [...data.entries, ...current])
      setSessionLogCursor(data.next_cursor ?? null)
      setSessionLogHasMore(Boolean(data.has_more))
    } catch (error) {
      if (
        !isCurrentRequest(
          olderLogRequestRef,
          requestId,
          requestAuth,
          requestBaseUrl,
          requestAccessSnapshot,
        ) || selectedSessionIdRef.current !== requestSessionId
      ) return
      if (isUnauthorizedError(error)) {
        onUnauthorized()
      }
      pushError('workspace', `Older history load failed: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      if (olderLogRequestRef.current === requestId) {
        olderLogLoadingRef.current = false
        setOlderLogLoadingScope(null)
      }
    }
  }, [
    auth,
    baseUrl,
    isCurrentRequest,
    latest,
    onUnauthorized,
    pushError,
    selectedSessionId,
    sessionLogCursor,
    sessionLogHasMore,
    setLogEntries,
    setSessionLogCursor,
    setSessionLogHasMore,
  ])

  const loadOlderSessions = useCallback(async () => {
    const campaignId = selectedCampaignId
    if (!campaignId || !sessionsCursor || olderSessionsLoadingRef.current) return
    const requestId = ++olderSessionsRequestRef.current
    const requestAuth = auth
    const requestBaseUrl = baseUrl
    const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
    olderSessionsLoadingRef.current = true
    setOlderSessionsLoadingScope({ auth: requestAuth, baseUrl: requestBaseUrl, campaignId })
    try {
      const page = await apiFetch<SessionListResponse>(
        requestBaseUrl,
        `/api/sessions/campaigns/${campaignId}/sessions?limit=${SESSION_PAGE_SIZE}&cursor=${encodeURIComponent(sessionsCursor)}`,
        requestAuth,
      )
      if (!isCurrentRequest(
        olderSessionsRequestRef,
        requestId,
        requestAuth,
        requestBaseUrl,
        requestAccessSnapshot,
      )) return
      olderSessionsLoaded(page)
    } catch (error) {
      if (!isCurrentRequest(
        olderSessionsRequestRef,
        requestId,
        requestAuth,
        requestBaseUrl,
        requestAccessSnapshot,
      )) return
      if (isUnauthorizedError(error)) {
        onUnauthorized()
      }
      pushError('workspace', `Older chapters failed to load: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      if (olderSessionsRequestRef.current === requestId) {
        olderSessionsLoadingRef.current = false
        setOlderSessionsLoadingScope(null)
      }
    }
  }, [
    auth,
    baseUrl,
    isCurrentRequest,
    olderSessionsLoaded,
    onUnauthorized,
    pushError,
    selectedCampaignId,
    sessionsCursor,
  ])

  // The narrator configuration: Table tools reads it with the operator data, the Settings sheet whenever it opens.
  const refreshLlmConfig = useCallback(async () => {
    const requestId = ++llmConfigRequestRef.current
    const requestAuth = auth
    const requestBaseUrl = baseUrl
    const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
    const requestIsCurrent = () => isCurrentRequest(
      llmConfigRequestRef,
      requestId,
      requestAuth,
      requestBaseUrl,
      requestAccessSnapshot,
    )
    try {
      const config = await apiFetch<LlmRuntimeConfig>(
        requestBaseUrl,
        '/api/llm/config',
        requestAuth,
        { headers: runtimeConfigHeaders },
      )
      if (requestIsCurrent()) setLlmConfig(config)
    } catch {
      if (requestIsCurrent()) setLlmConfig(null)
    }
  }, [auth, baseUrl, isCurrentRequest, runtimeConfigHeaders, setLlmConfig])

  const refreshOperatorData = useCallback(async () => {
    if (!operatorDataEnabledRef.current) return

    const requestId = ++operatorRequestRef.current
    const requestAuth = auth
    const requestBaseUrl = baseUrl
    const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
    const requestIsCurrent = () => (
      operatorDataEnabledRef.current &&
      isCurrentRequest(
        operatorRequestRef,
        requestId,
        requestAuth,
        requestBaseUrl,
        requestAccessSnapshot,
      )
    )

    const metricsRequest = apiFetch<BetaSummary>(
      requestBaseUrl,
      '/api/beta/summary',
      requestAuth,
    ).then((metrics) => {
      if (requestIsCurrent()) setMetrics(metrics)
    }).catch(() => {
      if (requestIsCurrent()) setMetrics(null)
    })
    await Promise.allSettled([metricsRequest, refreshLlmConfig()])
  }, [
    auth,
    baseUrl,
    isCurrentRequest,
    refreshLlmConfig,
    setMetrics,
  ])

  useEffect(() => {
    operatorDataEnabledRef.current = operatorDataEnabled
    if (!operatorDataEnabled) {
      operatorRequestRef.current += 1
      llmConfigRequestRef.current += 1
      setMetrics(null)
      setLlmConfig(null)
      return
    }
    void refreshOperatorData()
  }, [
    operatorDataEnabled,
    refreshOperatorData,
    setLlmConfig,
    setMetrics,
  ])

  const refreshRoot = useCallback(async () => {
    const requestId = ++rootRequestRef.current
    const requestAuth = auth
    const requestBaseUrl = baseUrl
    const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
    rootLoadingRequestRef.current = requestId
    setWorkspaceLoading(true)
    try {
      const [healthResult, workspaceResult] = await Promise.allSettled([
        apiFetch<Health>(requestBaseUrl, '/api/health', requestAuth),
        Promise.all([
          apiFetch<Campaign[]>(requestBaseUrl, '/api/campaigns', requestAuth),
          apiFetch<World[]>(requestBaseUrl, '/api/worlds?limit=200', requestAuth),
        ]),
      ])

      if (!isCurrentRequest(
        rootRequestRef,
        requestId,
        requestAuth,
        requestBaseUrl,
        requestAccessSnapshot,
      )) return

      if (healthResult.status === 'fulfilled') {
        const healthData = healthResult.value
        setHealth(healthData)
        if (operatorDataEnabledRef.current) void refreshOperatorData()
      } else {
        setHealth(null)
        pushError(
          'connection',
          `Connection failed: ${healthResult.reason instanceof Error ? healthResult.reason.message : String(healthResult.reason)}`,
        )
        return
      }

      if (workspaceResult.status === 'fulfilled') {
        const [campaignData, worldData] = workspaceResult.value
        rootCampaignsLoaded(campaignData)
        setWorlds(worldData)
        setCampaignSessionMeta(
          Object.fromEntries(
            campaignData.map((item) => [item.campaign_id, sessionMetaFromCampaign(item)]),
          ),
        )
        setTtsConfig(null)
        setTtsConfigLoadFailed(false)
        void apiFetch<TtsRuntimeConfig>(requestBaseUrl, '/api/tts/config', requestAuth)
          .then((ttsConfig) => {
            if (isCurrentRequest(
              rootRequestRef,
              requestId,
              requestAuth,
              requestBaseUrl,
              requestAccessSnapshot,
            )) {
              setTtsConfig(ttsConfig)
              setTtsConfigLoadFailed(false)
            }
          })
          .catch(() => {
            if (isCurrentRequest(
              rootRequestRef,
              requestId,
              requestAuth,
              requestBaseUrl,
              requestAccessSnapshot,
            )) {
              setTtsConfig(null)
              setTtsConfigLoadFailed(true)
            }
          })
        setSelectedCampaignId((current) => {
          if (campaignData.some((item) => item.campaign_id === current)) {
            return current
          }
          return null
        })
        if (!campaignData.length) {
          setSelectedSessionId(null)
        }
      } else {
        const error = workspaceResult.reason
        if (isUnauthorizedError(error)) {
          onUnauthorized()
          pushError('connection', 'Table token required. Enter the table token to connect.')
          return
        }
        pushError('connection', `Connection failed: ${error instanceof Error ? error.message : String(error)}`)
      }
    } finally {
      if (rootLoadingRequestRef.current === requestId) {
        rootLoadingRequestRef.current = null
        setWorkspaceLoading(campaignLoadingRequestRef.current !== null)
      }
    }
  }, [
    auth,
    baseUrl,
    isCurrentRequest,
    onUnauthorized,
    pushError,
    refreshOperatorData,
    rootCampaignsLoaded,
    setCampaignSessionMeta,
    setHealth,
    setSelectedCampaignId,
    setSelectedSessionId,
    setTtsConfig,
    setTtsConfigLoadFailed,
    setWorkspaceLoading,
    setWorlds,
  ])

  const refreshCampaignWorkspace = useCallback(
    async (campaignId: number) => {
      const requestId = ++workspaceRequestRef.current
      const requestAuth = auth
      const requestBaseUrl = baseUrl
      const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
      // An adventure being opened is read without the selected hero, who plays in the one open before it.
      const view = campaignId === selectedCampaignIdRef.current
        ? latest.current.playerView
        : withoutHero(latest.current.playerView)
      campaignLoadingRequestRef.current = { campaignId, requestId }
      setWorkspaceLoading(true)
      setLoadingCampaignId(campaignId)
      try {
        const workspace = await apiFetch<CampaignWorkspace>(
          requestBaseUrl,
          withQuery(`/api/campaigns/${campaignId}/workspace`, view),
          requestAuth,
        )
        if (!isCurrentRequest(
          workspaceRequestRef,
          requestId,
          requestAuth,
          requestBaseUrl,
          requestAccessSnapshot,
        )) return
        const campaignData = workspace.campaign
        const sessionData = workspace.sessions
        const playerData = workspace.players
        // Sessions listed past the first page stay listed while more pages exist.
        const retainedSessionIds = workspace.has_more.sessions
          ? latest.current.sessions
              .filter((item) => item.campaign_id === campaignId)
              .map((item) => item.session_id)
          : []
        campaignWorkspaceLoaded(workspace)
        setCampaignSessionMeta((current) => ({
          ...current,
          [campaignId]: {
            count: workspace.summary.session_count,
            updatedAt: workspace.summary.latest_activity_at ?? campaignData.created_at,
            latestSessionId: workspace.summary.latest_session_id,
          },
        }))
        setSelectedSessionId((current) => {
          if (
            sessionData.some((item) => item.session_id === current) ||
            (current !== null && retainedSessionIds.includes(current))
          ) {
            return current
          }
          return (sessionData.find((item) => item.is_current) ?? sessionData[0])?.session_id ?? null
        })
        setSelectedPlayerId((current) => {
          if (playerData.some((item) => item.player_id === current)) {
            return current
          }
          return null
        })
        // Never wipe an in-flight send for this campaign: the socket owns its
        // lifecycle and a refresh says nothing about its delivery.
        const pendingCampaignId = latest.current.pendingSubmissionCampaignId()
        if (pendingCampaignId === null || pendingCampaignId !== campaignId) {
          setOptimisticEntries([])
          setStreamingTurn(null)
          setSendPending(false)
        }
      } catch (error) {
        if (isCurrentRequest(
          workspaceRequestRef,
          requestId,
          requestAuth,
          requestBaseUrl,
          requestAccessSnapshot,
        )) {
          const heroId = viewedHeroId(view)
          if (isUnauthorizedError(error)) {
            onUnauthorized()
          } else if (isForbiddenError(error) && heroId !== null) {
            // A remembered hero this adventure will not be read as: forget the hero, and it is read again.
            setSelectedPlayerId((current) => (current === heroId ? null : current))
            return
          } else if (isNotFoundError(error)) {
            setSelectedCampaignId((current) => (current === campaignId ? null : current))
            setSelectedSessionId(null)
            setOptimisticEntries([])
            setStreamingTurn(null)
            setSendPending(false)
            return
          }
          pushError('workspace', `Workspace load failed: ${error instanceof Error ? error.message : String(error)}`)
        }
      } finally {
        if (campaignLoadingRequestRef.current?.requestId === requestId) {
          campaignLoadingRequestRef.current = null
          setLoadingCampaignId(null)
          setWorkspaceLoading(rootLoadingRequestRef.current !== null)
        }
      }
    },
    [
      auth,
      baseUrl,
      campaignWorkspaceLoaded,
      isCurrentRequest,
      latest,
      onUnauthorized,
      pushError,
      setCampaignSessionMeta,
      setOptimisticEntries,
      setSelectedCampaignId,
      setLoadingCampaignId,
      setSelectedPlayerId,
      setSelectedSessionId,
      setSendPending,
      setStreamingTurn,
      setWorkspaceLoading,
    ],
  )

  const refreshCurrentWorkspace = useCallback(async () => {
    await refreshRoot()
    const currentCampaignId = selectedCampaignIdRef.current
    if (currentCampaignId) {
      await refreshCampaignWorkspace(currentCampaignId)
    }
    const currentSessionId = selectedSessionIdRef.current
    if (currentSessionId) {
      await loadSessionData(currentSessionId)
    }
  }, [
    loadSessionData,
    refreshCampaignWorkspace,
    refreshRoot,
  ])

  return {
    clearSessionData,
    loadOlderSessionLog,
    loadOlderSessions,
    loadSessionData,
    olderLogLoading,
    olderSessionsLoading,
    refreshCampaignWorkspace,
    refreshCurrentWorkspace,
    refreshLlmConfig,
    refreshRoot,
  }
}
