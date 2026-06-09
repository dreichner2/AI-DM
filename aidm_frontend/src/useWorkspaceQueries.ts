import { useCallback, useRef, useState, type Dispatch, type SetStateAction } from 'react'
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

type UseWorkspaceQueriesOptions = {
  auth: string
  baseUrl: string
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
  const workspaceRequestRef = useRef(0)
  const sessionRequestRef = useRef(0)
  const [olderLogLoading, setOlderLogLoading] = useState(false)

  const clearSessionData = useCallback(() => {
    sessionRequestRef.current += 1
    setLogEntries([])
    setSessionLogCursor(null)
    setSessionLogHasMore(false)
    setSessionState(null)
    setSessionLoading(false)
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
      const requestAuth = auth
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
        if (sessionRequestRef.current !== requestId) return
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
        if (isUnauthorizedError(error)) {
          if (requestAccessSnapshot !== storedRuntimeAccessSnapshot()) return
          onUnauthorized()
        } else if (isNotFoundError(error) && sessionRequestRef.current === requestId) {
          setSelectedSessionId((current) => (current === sessionId ? null : current))
          setLogEntries([])
          setSessionLogCursor(null)
          setSessionLogHasMore(false)
          setSessionState(null)
          return
        }
        throw error
      } finally {
        if (sessionRequestRef.current === requestId) {
          setSessionLoading(false)
        }
      }
    },
    [
      auth,
      baseUrl,
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
    if (!selectedSessionId || !sessionLogHasMore || olderLogLoading || sessionLogCursor === null) return
    const requestAuth = auth
    const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
    setOlderLogLoading(true)
    try {
      const data = await apiFetch<SessionLogResponse>(
        baseUrl,
        `/api/sessions/${selectedSessionId}/log?limit=200&before_id=${sessionLogCursor}`,
        requestAuth,
      )
      setLogEntries((current) => [...data.entries, ...current])
      setSessionLogCursor(data.next_cursor ?? null)
      setSessionLogHasMore(Boolean(data.has_more))
    } catch (error) {
      if (isUnauthorizedError(error)) {
        if (requestAccessSnapshot !== storedRuntimeAccessSnapshot()) return
        onUnauthorized()
      }
      pushError('workspace', `Older history load failed: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setOlderLogLoading(false)
    }
  }, [
    auth,
    baseUrl,
    olderLogLoading,
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
    const requestAuth = auth
    const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
    try {
      const [healthData, campaignData, metricData, llmData, worldData] = await Promise.all([
        apiFetch<Health>(baseUrl, '/api/health', requestAuth),
        apiFetch<Campaign[]>(baseUrl, '/api/campaigns', requestAuth),
        apiFetch<BetaSummary>(baseUrl, '/api/beta/summary', requestAuth),
        apiFetch<LlmRuntimeConfig>(baseUrl, '/api/llm/config', requestAuth),
        apiFetch<World[]>(baseUrl, '/api/worlds?limit=200', requestAuth),
      ])
      setHealth(healthData)
      rootCampaignsLoaded(campaignData)
      setMetrics(metricData)
      setLlmConfig(llmData)
      setWorlds(worldData)
      setCampaignSessionMeta(
        Object.fromEntries(
          campaignData.map((item) => [item.campaign_id, sessionMetaFromCampaign(item)]),
        ),
      )
      void apiFetch<TtsRuntimeConfig>(baseUrl, '/api/tts/config', requestAuth)
        .then(setTtsConfig)
        .catch(() => {
          setTtsConfig({ provider: 'deepgram', configured: false, model: 'aura-2-draco-en' })
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
    } catch (error) {
      setHealth(null)
      if (isUnauthorizedError(error)) {
        if (requestAccessSnapshot !== storedRuntimeAccessSnapshot()) return
        onUnauthorized()
        pushError('connection', 'Workspace token required. Enter the workspace token to connect.')
        return
      }
      pushError('connection', `Connection failed: ${error instanceof Error ? error.message : String(error)}`)
    }
  }, [
    auth,
    baseUrl,
    onUnauthorized,
    pushError,
    rootCampaignsLoaded,
    setCampaignSessionMeta,
    setHealth,
    setLlmConfig,
    setMetrics,
    setSelectedCampaignId,
    setSelectedSessionId,
    setTtsConfig,
    setWorlds,
  ])

  const refreshCampaignWorkspace = useCallback(
    async (campaignId: number) => {
      const requestId = ++workspaceRequestRef.current
      const requestAuth = auth
      const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
      setWorkspaceLoading(true)
      setLoadingCampaignId(campaignId)
      try {
        const workspace = await apiFetch<CampaignWorkspace>(
          baseUrl,
          `/api/campaigns/${campaignId}/workspace`,
          requestAuth,
        )
        if (workspaceRequestRef.current !== requestId) return
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
        if (workspaceRequestRef.current === requestId) {
          if (isUnauthorizedError(error)) {
            if (requestAccessSnapshot !== storedRuntimeAccessSnapshot()) return
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
        if (workspaceRequestRef.current === requestId) {
          setWorkspaceLoading(false)
          setLoadingCampaignId(null)
        }
      }
    },
    [
      auth,
      baseUrl,
      campaignWorkspaceLoaded,
      onUnauthorized,
      pushError,
      setCampaignSessionMeta,
      setLoadingCampaignId,
      setOptimisticEntries,
      setSelectedCampaignId,
      setSelectedPlayerId,
      setSelectedSessionId,
      setSendPending,
      setStreamingTurn,
      setWorkspaceLoading,
    ],
  )

  const refreshCurrentWorkspace = useCallback(async () => {
    await refreshRoot()
    if (selectedCampaignId) {
      await refreshCampaignWorkspace(selectedCampaignId)
    }
    if (selectedSessionId) {
      await loadSessionData(selectedSessionId)
    }
  }, [
    loadSessionData,
    refreshCampaignWorkspace,
    refreshRoot,
    selectedCampaignId,
    selectedSessionId,
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
