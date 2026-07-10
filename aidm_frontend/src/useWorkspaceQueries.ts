import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { ApiClientError, apiFetch, storedRuntimeAccessSnapshot } from './api'
import type {
  BetaSummary,
  Campaign,
  CampaignWorkspace,
  Health,
  LlmRuntimeConfig,
  SessionLogResponse,
  SessionLogEntry,
  SessionState,
  SessionSummary,
  StreamingTurn,
  TimelineEntry,
  TtsRuntimeConfig,
  World,
} from './types'

export type CampaignSessionMeta = {
  count: number
  updatedAt: string | null
  latestSessionId: number | null
}

type ValueUpdater<T> = T | ((current: T) => T)

type WorkspaceQueryErrorCategory = 'connection' | 'workspace'

type OlderLogLoadingScope = {
  auth: string
  baseUrl: string
  requestId: number
  sessionId: number
}

type UseWorkspaceQueriesOptions = {
  auth: string
  baseUrl: string
  runtimeConfigHeaders?: HeadersInit
  sessions: SessionSummary[]
  selectedCampaignId: number | null
  selectedSessionId: number | null
  sessionLogCursor: number | null
  sessionLogHasMore: boolean
  setHealth: Dispatch<SetStateAction<Health | null>>
  setMetrics: Dispatch<SetStateAction<BetaSummary | null>>
  setLlmConfig: Dispatch<SetStateAction<LlmRuntimeConfig | null>>
  setTtsConfig: Dispatch<SetStateAction<TtsRuntimeConfig | null>>
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
  rootCampaignsLoaded: (campaigns: Campaign[]) => void
  campaignWorkspaceLoaded: (workspace: CampaignWorkspace) => void
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
  runtimeConfigHeaders,
  sessions,
  selectedCampaignId,
  selectedSessionId,
  sessionLogCursor,
  sessionLogHasMore,
  setHealth,
  setMetrics,
  setLlmConfig,
  setTtsConfig,
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
  rootCampaignsLoaded,
  campaignWorkspaceLoaded,
  setOptimisticEntries,
  setStreamingTurn,
  setSendPending,
  pushError,
  onUnauthorized,
}: UseWorkspaceQueriesOptions) {
  const rootRequestRef = useRef(0)
  const workspaceRequestRef = useRef(0)
  const sessionRequestRef = useRef(0)
  const olderLogRequestRef = useRef(0)
  const olderLogLoadingRef = useRef(false)
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

  useEffect(() => {
    runtimeScopeRef.current = { auth, baseUrl }
    rootRequestRef.current += 1
    workspaceRequestRef.current += 1
    const invalidatedSessionRequestId = ++sessionRequestRef.current
    olderLogRequestRef.current += 1
    olderLogLoadingRef.current = false
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
    let cancelled = false
    queueMicrotask(() => {
      if (cancelled) return
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
      setSessionLoading(true)
      try {
        const [logData, stateData] = await Promise.all([
          apiFetch<SessionLogResponse>(
            baseUrl,
            `/api/sessions/${sessionId}/log?limit=200`,
            requestAuth,
          ),
          apiFetch<SessionState>(baseUrl, `/api/sessions/${sessionId}/state`, requestAuth),
        ])
        if (!isCurrentRequest(
          sessionRequestRef,
          requestId,
          requestAuth,
          requestBaseUrl,
          requestAccessSnapshot,
        )) return
        setLogEntries(logData.entries)
        setSessionLogCursor(logData.next_cursor ?? null)
        setSessionLogHasMore(Boolean(logData.has_more))
        setSessionState(stateData)
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
        if (isUnauthorizedError(error)) {
          onUnauthorized()
        } else if (isNotFoundError(error)) {
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
      onUnauthorized,
      sessions,
      setCampaignSessionMeta,
      setLogEntries,
      setSessionLoading,
      setSessionLogCursor,
      setSessionLogHasMore,
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
        `/api/sessions/${requestSessionId}/log?limit=200&before_id=${sessionLogCursor}`,
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
    onUnauthorized,
    pushError,
    selectedSessionId,
    sessionLogCursor,
    sessionLogHasMore,
    setLogEntries,
    setSessionLogCursor,
    setSessionLogHasMore,
  ])

  const refreshRoot = useCallback(async () => {
    const requestId = ++rootRequestRef.current
    const requestAuth = auth
    const requestBaseUrl = baseUrl
    const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
    rootLoadingRequestRef.current = requestId
    setWorkspaceLoading(true)
    try {
      const [healthResult, workspaceResult, llmResult] = await Promise.allSettled([
        apiFetch<Health>(requestBaseUrl, '/api/health', requestAuth),
        Promise.all([
          apiFetch<Campaign[]>(requestBaseUrl, '/api/campaigns', requestAuth),
          apiFetch<BetaSummary>(requestBaseUrl, '/api/beta/summary', requestAuth),
          apiFetch<World[]>(requestBaseUrl, '/api/worlds?limit=200', requestAuth),
        ]),
        apiFetch<LlmRuntimeConfig>(requestBaseUrl, '/api/llm/config', requestAuth, {
          headers: runtimeConfigHeaders,
        }),
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
      } else {
        setHealth(null)
        pushError(
          'connection',
          `Connection failed: ${healthResult.reason instanceof Error ? healthResult.reason.message : String(healthResult.reason)}`,
        )
        return
      }

      if (llmResult.status === 'fulfilled') {
        setLlmConfig(llmResult.value)
      } else {
        setLlmConfig(null)
      }

      if (workspaceResult.status === 'fulfilled') {
        const [campaignData, metricData, worldData] = workspaceResult.value
        rootCampaignsLoaded(campaignData)
        setMetrics(metricData)
        setWorlds(worldData)
        setCampaignSessionMeta(
          Object.fromEntries(
            campaignData.map((item) => [item.campaign_id, sessionMetaFromCampaign(item)]),
          ),
        )
        void apiFetch<TtsRuntimeConfig>(requestBaseUrl, '/api/tts/config', requestAuth)
          .then((ttsConfig) => {
            if (isCurrentRequest(
              rootRequestRef,
              requestId,
              requestAuth,
              requestBaseUrl,
              requestAccessSnapshot,
            )) setTtsConfig(ttsConfig)
          })
          .catch(() => {
            if (isCurrentRequest(
              rootRequestRef,
              requestId,
              requestAuth,
              requestBaseUrl,
              requestAccessSnapshot,
            )) setTtsConfig({ provider: 'deepgram', configured: false, model: 'aura-2-draco-en' })
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
    rootCampaignsLoaded,
    runtimeConfigHeaders,
    setCampaignSessionMeta,
    setHealth,
    setLlmConfig,
    setMetrics,
    setSelectedCampaignId,
    setSelectedSessionId,
    setTtsConfig,
    setWorkspaceLoading,
    setWorlds,
  ])

  const refreshCampaignWorkspace = useCallback(
    async (campaignId: number) => {
      const requestId = ++workspaceRequestRef.current
      const requestAuth = auth
      const requestBaseUrl = baseUrl
      const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
      campaignLoadingRequestRef.current = { campaignId, requestId }
      setWorkspaceLoading(true)
      setLoadingCampaignId(campaignId)
      try {
        const workspace = await apiFetch<CampaignWorkspace>(
          requestBaseUrl,
          `/api/campaigns/${campaignId}/workspace`,
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
          if (sessionData.some((item) => item.session_id === current)) {
            return current
          }
          return sessionData[0]?.session_id ?? null
        })
        setSelectedPlayerId((current) => {
          if (playerData.some((item) => item.player_id === current)) {
            return current
          }
          return null
        })
        setOptimisticEntries([])
        setStreamingTurn(null)
        setSendPending(false)
      } catch (error) {
        if (isCurrentRequest(
          workspaceRequestRef,
          requestId,
          requestAuth,
          requestBaseUrl,
          requestAccessSnapshot,
        )) {
          if (isUnauthorizedError(error)) {
            onUnauthorized()
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
    loadSessionData,
    olderLogLoading,
    refreshCampaignWorkspace,
    refreshCurrentWorkspace,
    refreshRoot,
  }
}
