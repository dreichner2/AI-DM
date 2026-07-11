// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { useState } from 'react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type {
  Campaign,
  CampaignWorkspace,
  SessionLogEntry,
  SessionLogResponse,
  SessionState,
} from './types'
import { ApiClientError } from './api'
import { useWorkspaceQueries } from './useWorkspaceQueries'

const apiFetchMock = vi.hoisted(() => vi.fn())
const storedRuntimeAccessSnapshotMock = vi.hoisted(() => vi.fn((auth: string) => auth))

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api')
  return {
    ...actual,
    apiFetch: apiFetchMock,
    storedRuntimeAccessSnapshot: storedRuntimeAccessSnapshotMock,
  }
})

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise
    reject = rejectPromise
  })
  return { promise, reject, resolve }
}

function campaign(campaignId: number): Campaign {
  return {
    campaign_id: campaignId,
    title: `Campaign ${campaignId}`,
    description: null,
    world_id: campaignId,
    world_name: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: null,
    status: 'active',
    is_archived: false,
    current_quest: null,
    location: null,
    session_count: 0,
    latest_session_id: null,
    latest_activity_at: null,
  }
}

function workspace(campaignId: number): CampaignWorkspace {
  return {
    campaign: campaign(campaignId),
    sessions: [],
    players: [],
    maps: [],
    segments: [],
    summary: {
      session_count: 0,
      player_count: 0,
      map_count: 0,
      segment_count: 0,
      latest_session_id: null,
      latest_activity_at: null,
    },
    has_more: { sessions: false, players: false, maps: false, segments: false },
    next_cursor: { sessions: null, players: null, maps: null, segments: null },
    limits: { sessions: null, players: null, maps: null, segments: null },
  }
}

function sessionState(sessionId: number): SessionState {
  return {
    session_id: sessionId,
    campaign_id: sessionId,
    current_location: null,
    current_quest: null,
    rolling_summary: `Session ${sessionId}`,
    active_segments: [],
    memory_snippets: [],
    state_snapshot: {},
    updated_at: '2026-01-01T00:00:00Z',
  }
}

function logEntry(id: number): SessionLogEntry {
  return {
    id,
    message: `Entry ${id}`,
    entry_type: 'dm',
    metadata: {},
    timestamp: '2026-01-01T00:00:00Z',
  }
}

function logResponse(sessionId: number, entries: SessionLogEntry[]): SessionLogResponse {
  return {
    session_id: sessionId,
    entries,
    has_more: false,
    next_cursor: null,
  }
}

type ScopeProps = {
  auth: string
  baseUrl: string
  operatorDataEnabled: boolean
  selectedCampaignId: number | null
  selectedSessionId: number | null
  sessionLogCursor: number | null
  sessionLogHasMore: boolean
}

function createCallbacks() {
  return {
    campaignWorkspaceLoaded: vi.fn(),
    onUnauthorized: vi.fn(),
    pushError: vi.fn(),
    rootCampaignsLoaded: vi.fn(),
    setCampaignSessionMeta: vi.fn(),
    setHealth: vi.fn(),
    setLlmConfig: vi.fn(),
    setLogEntries: vi.fn(),
    setMetrics: vi.fn(),
    setOptimisticEntries: vi.fn(),
    setSelectedCampaignId: vi.fn(),
    setSelectedPlayerId: vi.fn(),
    setSelectedSessionId: vi.fn(),
    setSendPending: vi.fn(),
    setSessionLogCursor: vi.fn(),
    setSessionLogHasMore: vi.fn(),
    setSessionState: vi.fn(),
    setStreamingTurn: vi.fn(),
    setTtsConfig: vi.fn(),
    setTtsConfigLoadFailed: vi.fn(),
    setWorlds: vi.fn(),
  }
}

function useQueryHarness(scope: ScopeProps, callbacks: ReturnType<typeof createCallbacks>) {
  const [workspaceLoading, setWorkspaceLoading] = useState(false)
  const [loadingCampaignId, setLoadingCampaignId] = useState<number | null>(null)
  const [sessionLoading, setSessionLoading] = useState(false)
  const queries = useWorkspaceQueries({
    ...scope,
    sessions: [],
    setWorkspaceLoading,
    setLoadingCampaignId,
    setSessionLoading,
    ...callbacks,
  } as unknown as Parameters<typeof useWorkspaceQueries>[0])
  return { loadingCampaignId, queries, sessionLoading, workspaceLoading }
}

const defaultScope: ScopeProps = {
  auth: 'token-a',
  baseUrl: 'https://old.example.test',
  operatorDataEnabled: false,
  selectedCampaignId: 1,
  selectedSessionId: 1,
  sessionLogCursor: null,
  sessionLogHasMore: false,
}

describe('useWorkspaceQueries request ownership', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    storedRuntimeAccessSnapshotMock.mockClear()
  })

  afterEach(() => cleanup())

  it('ignores an old root response after the backend scope changes', async () => {
    const oldHealth = deferred<unknown>()
    const oldCampaign = campaign(1)
    const newCampaign = campaign(2)
    apiFetchMock.mockImplementation((requestBaseUrl: string, path: string) => {
      if (requestBaseUrl === defaultScope.baseUrl && path === '/api/health') return oldHealth.promise
      if (path === '/api/health') {
        return Promise.resolve({ status: 'ok', service: 'new', env: 'test', auth_required: false, rules_engine_enabled: true, segment_evaluator_enabled: true })
      }
      if (path === '/api/campaigns') {
        return Promise.resolve(requestBaseUrl === defaultScope.baseUrl ? [oldCampaign] : [newCampaign])
      }
      if (path === '/api/beta/summary') {
        return Promise.resolve({ turn_latency_ms_avg: null, ai_failure_rate: 0, session_completion_rate: 1, coherence_feedback_avg: null, coherence_feedback_count: 0, total_turns: 0, total_sessions: 0 })
      }
      if (path === '/api/worlds?limit=200') return Promise.resolve([])
      if (path === '/api/llm/config') {
        return Promise.resolve({ current: { provider: 'test', model: 'test', fallback_models: [], latest_turn: null }, providers: [], persisted: false })
      }
      if (path === '/api/tts/config') {
        return Promise.resolve({ provider: 'deepgram', configured: false, model: 'aura-2-draco-en' })
      }
      throw new Error(`Unexpected request: ${requestBaseUrl}${path}`)
    })
    const callbacks = createCallbacks()
    const { result, rerender } = renderHook(
      (scope: ScopeProps) => useQueryHarness(scope, callbacks),
      { initialProps: defaultScope },
    )

    let oldRequest!: Promise<void>
    act(() => {
      oldRequest = result.current.queries.refreshRoot()
    })
    const newScope = { ...defaultScope, baseUrl: 'https://new.example.test' }
    rerender(newScope)
    await act(async () => {
      await result.current.queries.refreshRoot()
    })

    expect(callbacks.rootCampaignsLoaded).toHaveBeenCalledTimes(1)
    expect(callbacks.rootCampaignsLoaded).toHaveBeenCalledWith([newCampaign])
    expect(result.current.workspaceLoading).toBe(false)

    oldHealth.resolve({ status: 'ok', service: 'old', env: 'test', auth_required: false, rules_engine_enabled: true, segment_evaluator_enabled: true })
    await act(async () => oldRequest)

    expect(callbacks.rootCampaignsLoaded).toHaveBeenCalledTimes(1)
    expect(result.current.workspaceLoading).toBe(false)
  })

  it('loads campaigns and worlds without requesting operator-only root data', async () => {
    const visibleCampaign = campaign(1)
    const visibleWorld = {
      world_id: 1,
      name: 'Shared World',
      description: null,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: null,
    }
    const forbidden = new ApiClientError('Forbidden', 403, { error_code: 'forbidden' })
    apiFetchMock.mockImplementation((_baseUrl: string, path: string) => {
      if (path === '/api/health') {
        return Promise.resolve({ status: 'ok', service: 'test', env: 'test', auth_required: true, rules_engine_enabled: true, segment_evaluator_enabled: true })
      }
      if (path === '/api/campaigns') return Promise.resolve([visibleCampaign])
      if (path === '/api/worlds?limit=200') return Promise.resolve([visibleWorld])
      if (path === '/api/tts/config') {
        return Promise.reject(forbidden)
      }
      throw new Error(`Unexpected request: ${path}`)
    })
    const callbacks = createCallbacks()
    const { result } = renderHook(() => useQueryHarness(defaultScope, callbacks))

    await act(async () => {
      await result.current.queries.refreshRoot()
    })

    expect(callbacks.rootCampaignsLoaded).toHaveBeenCalledWith([visibleCampaign])
    expect(callbacks.setWorlds).toHaveBeenCalledWith([visibleWorld])
    expect(callbacks.setMetrics).toHaveBeenCalledWith(null)
    expect(apiFetchMock).not.toHaveBeenCalledWith(
      expect.any(String),
      '/api/beta/summary',
      expect.any(String),
    )
    expect(apiFetchMock).not.toHaveBeenCalledWith(
      expect.any(String),
      '/api/llm/config',
      expect.any(String),
      expect.anything(),
    )
    expect(callbacks.pushError).not.toHaveBeenCalled()
    expect(callbacks.onUnauthorized).not.toHaveBeenCalled()
    expect(result.current.workspaceLoading).toBe(false)
  })

  it('loads enabled operator data independently from essential workspace data', async () => {
    const metricsResult = deferred<{
      turn_latency_ms_avg: number | null
      ai_failure_rate: number
      session_completion_rate: number
      coherence_feedback_avg: number | null
      coherence_feedback_count: number
      total_turns: number
      total_sessions: number
    }>()
    const llmResult = deferred<{
      current: { provider: string; model: string; fallback_models: string[]; latest_turn: null }
      providers: never[]
      persisted: boolean
    }>()
    apiFetchMock.mockImplementation((_baseUrl: string, path: string) => {
      if (path === '/api/health') {
        return Promise.resolve({ status: 'ok', service: 'test', env: 'test', auth_required: true, rules_engine_enabled: true, segment_evaluator_enabled: true })
      }
      if (path === '/api/campaigns') return Promise.resolve([campaign(1)])
      if (path === '/api/worlds?limit=200') return Promise.resolve([])
      if (path === '/api/tts/config') {
        return Promise.resolve({ provider: 'deepgram', configured: true, model: 'aura-2-draco-en' })
      }
      if (path === '/api/beta/summary') return metricsResult.promise
      if (path === '/api/llm/config') return llmResult.promise
      throw new Error(`Unexpected request: ${path}`)
    })
    const callbacks = createCallbacks()
    const { result, rerender } = renderHook(
      (scope: ScopeProps) => useQueryHarness(scope, callbacks),
      { initialProps: defaultScope },
    )

    await act(async () => {
      await result.current.queries.refreshRoot()
    })

    expect(callbacks.rootCampaignsLoaded).toHaveBeenCalledWith([campaign(1)])
    expect(apiFetchMock.mock.calls.some(([, path]) => path === '/api/beta/summary')).toBe(false)
    expect(apiFetchMock.mock.calls.some(([, path]) => path === '/api/llm/config')).toBe(false)

    rerender({ ...defaultScope, operatorDataEnabled: true })
    await waitFor(() => {
      expect(apiFetchMock.mock.calls.some(([, path]) => path === '/api/beta/summary')).toBe(true)
      expect(apiFetchMock.mock.calls.some(([, path]) => path === '/api/llm/config')).toBe(true)
    })

    const llmConfig = {
      current: { provider: 'test', model: 'test', fallback_models: [], latest_turn: null },
      providers: [],
      persisted: false,
    }
    llmResult.resolve(llmConfig)
    await waitFor(() => expect(callbacks.setLlmConfig).toHaveBeenCalledWith(llmConfig))
    expect(callbacks.setMetrics).not.toHaveBeenCalledWith(expect.objectContaining({ total_turns: 7 }))

    const metrics = {
      turn_latency_ms_avg: null,
      ai_failure_rate: 0,
      session_completion_rate: 1,
      coherence_feedback_avg: null,
      coherence_feedback_count: 0,
      total_turns: 7,
      total_sessions: 2,
    }
    metricsResult.resolve(metrics)
    await waitFor(() => expect(callbacks.setMetrics).toHaveBeenCalledWith(metrics))
  })

  it('lets only the newest campaign workspace response update the selection', async () => {
    const firstWorkspace = deferred<CampaignWorkspace>()
    apiFetchMock.mockImplementation((_baseUrl: string, path: string) => {
      if (path === '/api/campaigns/1/workspace') return firstWorkspace.promise
      if (path === '/api/campaigns/2/workspace') return Promise.resolve(workspace(2))
      throw new Error(`Unexpected request: ${path}`)
    })
    const callbacks = createCallbacks()
    const { result } = renderHook(() => useQueryHarness(defaultScope, callbacks))

    let firstRequest!: Promise<void>
    act(() => {
      firstRequest = result.current.queries.refreshCampaignWorkspace(1)
    })
    await act(async () => {
      await result.current.queries.refreshCampaignWorkspace(2)
    })

    expect(callbacks.campaignWorkspaceLoaded).toHaveBeenCalledTimes(1)
    expect(callbacks.campaignWorkspaceLoaded).toHaveBeenCalledWith(workspace(2))
    expect(result.current.workspaceLoading).toBe(false)
    expect(result.current.loadingCampaignId).toBeNull()

    firstWorkspace.resolve(workspace(1))
    await act(async () => firstRequest)

    expect(callbacks.campaignWorkspaceLoaded).toHaveBeenCalledTimes(1)
    expect(result.current.workspaceLoading).toBe(false)
  })

  it('keeps shared workspace loading active until root and campaign requests both settle', async () => {
    const rootHealth = deferred<unknown>()
    const campaignWorkspace = deferred<CampaignWorkspace>()
    apiFetchMock.mockImplementation((_baseUrl: string, path: string) => {
      if (path === '/api/health') return rootHealth.promise
      if (path === '/api/campaigns') return Promise.resolve([campaign(1)])
      if (path === '/api/beta/summary') {
        return Promise.resolve({ turn_latency_ms_avg: null, ai_failure_rate: 0, session_completion_rate: 1, coherence_feedback_avg: null, coherence_feedback_count: 0, total_turns: 0, total_sessions: 0 })
      }
      if (path === '/api/worlds?limit=200') return Promise.resolve([])
      if (path === '/api/llm/config') {
        return Promise.resolve({ current: { provider: 'test', model: 'test', fallback_models: [], latest_turn: null }, providers: [], persisted: false })
      }
      if (path === '/api/tts/config') {
        return Promise.resolve({ provider: 'deepgram', configured: false, model: 'aura-2-draco-en' })
      }
      if (path === '/api/campaigns/1/workspace') return campaignWorkspace.promise
      throw new Error(`Unexpected request: ${path}`)
    })
    const callbacks = createCallbacks()
    const { result } = renderHook(() => useQueryHarness(defaultScope, callbacks))

    let rootRequest!: Promise<void>
    let campaignRequest!: Promise<void>
    act(() => {
      rootRequest = result.current.queries.refreshRoot()
      campaignRequest = result.current.queries.refreshCampaignWorkspace(1)
    })

    rootHealth.resolve({ status: 'ok', service: 'test', env: 'test', auth_required: false, rules_engine_enabled: true, segment_evaluator_enabled: true })
    await act(async () => rootRequest)

    expect(result.current.workspaceLoading).toBe(true)
    expect(result.current.loadingCampaignId).toBe(1)

    campaignWorkspace.resolve(workspace(1))
    await act(async () => campaignRequest)

    expect(result.current.workspaceLoading).toBe(false)
    expect(result.current.loadingCampaignId).toBeNull()
  })

  it('invalidates a campaign request and clears its loading state when the campaign is deselected', async () => {
    const pendingWorkspace = deferred<CampaignWorkspace>()
    apiFetchMock.mockImplementation((_baseUrl: string, path: string) => {
      if (path === '/api/campaigns/1/workspace') return pendingWorkspace.promise
      throw new Error(`Unexpected request: ${path}`)
    })
    const callbacks = createCallbacks()
    const { result, rerender } = renderHook(
      (scope: ScopeProps) => useQueryHarness(scope, callbacks),
      { initialProps: defaultScope },
    )

    let request!: Promise<void>
    act(() => {
      request = result.current.queries.refreshCampaignWorkspace(1)
    })
    expect(result.current.workspaceLoading).toBe(true)

    await act(async () => {
      rerender({ ...defaultScope, selectedCampaignId: null })
      await Promise.resolve()
    })

    expect(result.current.workspaceLoading).toBe(false)
    expect(result.current.loadingCampaignId).toBeNull()
    pendingWorkspace.resolve(workspace(1))
    await act(async () => request)

    expect(callbacks.campaignWorkspaceLoaded).not.toHaveBeenCalled()
  })

  it('lets only the newest session response replace log and state data', async () => {
    const firstLog = deferred<SessionLogResponse>()
    apiFetchMock.mockImplementation((_baseUrl: string, path: string) => {
      if (path === '/api/sessions/1/log?limit=200') return firstLog.promise
      if (path === '/api/sessions/1/state') return Promise.resolve(sessionState(1))
      if (path === '/api/sessions/2/log?limit=200') return Promise.resolve(logResponse(2, [logEntry(2)]))
      if (path === '/api/sessions/2/state') return Promise.resolve(sessionState(2))
      throw new Error(`Unexpected request: ${path}`)
    })
    const callbacks = createCallbacks()
    const { result } = renderHook(() => useQueryHarness(defaultScope, callbacks))

    let firstRequest!: Promise<void>
    act(() => {
      firstRequest = result.current.queries.loadSessionData(1)
    })
    await act(async () => {
      await result.current.queries.loadSessionData(2)
    })

    expect(callbacks.setLogEntries).toHaveBeenCalledTimes(1)
    expect(callbacks.setLogEntries).toHaveBeenCalledWith([logEntry(2)])
    expect(callbacks.setSessionState).toHaveBeenCalledTimes(1)
    expect(callbacks.setSessionState).toHaveBeenCalledWith(sessionState(2))
    expect(result.current.sessionLoading).toBe(false)

    firstLog.resolve(logResponse(1, [logEntry(1)]))
    await act(async () => firstRequest)

    expect(callbacks.setLogEntries).toHaveBeenCalledTimes(1)
    expect(callbacks.setSessionState).toHaveBeenCalledTimes(1)
    expect(result.current.sessionLoading).toBe(false)
  })

  it('invalidates a session request and clears its loading state when the session is deselected', async () => {
    const pendingLog = deferred<SessionLogResponse>()
    apiFetchMock.mockImplementation((_baseUrl: string, path: string) => {
      if (path === '/api/sessions/1/log?limit=200') return pendingLog.promise
      if (path === '/api/sessions/1/state') return Promise.resolve(sessionState(1))
      throw new Error(`Unexpected request: ${path}`)
    })
    const callbacks = createCallbacks()
    const { result, rerender } = renderHook(
      (scope: ScopeProps) => useQueryHarness(scope, callbacks),
      { initialProps: defaultScope },
    )

    let request!: Promise<void>
    act(() => {
      request = result.current.queries.loadSessionData(1)
    })
    expect(result.current.sessionLoading).toBe(true)

    await act(async () => {
      rerender({ ...defaultScope, selectedSessionId: null })
      await Promise.resolve()
    })

    expect(result.current.sessionLoading).toBe(false)
    pendingLog.resolve(logResponse(1, [logEntry(1)]))
    await act(async () => request)

    expect(callbacks.setLogEntries).not.toHaveBeenCalled()
    expect(callbacks.setSessionState).not.toHaveBeenCalled()
  })

  it('single-flights older-log loads and discards a response for a deselected session', async () => {
    const olderLog = deferred<SessionLogResponse>()
    apiFetchMock.mockImplementation((_baseUrl: string, path: string) => {
      if (path === '/api/sessions/1/log?limit=200&before_id=10') return olderLog.promise
      throw new Error(`Unexpected request: ${path}`)
    })
    const callbacks = createCallbacks()
    const scope = { ...defaultScope, sessionLogCursor: 10, sessionLogHasMore: true }
    const { result, rerender } = renderHook(
      (props: ScopeProps) => useQueryHarness(props, callbacks),
      { initialProps: scope },
    )

    let firstRequest!: Promise<void>
    let secondRequest!: Promise<void>
    act(() => {
      firstRequest = result.current.queries.loadOlderSessionLog()
      secondRequest = result.current.queries.loadOlderSessionLog()
    })

    expect(apiFetchMock).toHaveBeenCalledTimes(1)
    expect(result.current.queries.olderLogLoading).toBe(true)

    rerender({ ...scope, selectedSessionId: 2 })
    expect(result.current.queries.olderLogLoading).toBe(false)
    olderLog.resolve(logResponse(1, [logEntry(1)]))
    await act(async () => Promise.all([firstRequest, secondRequest]))

    expect(callbacks.setLogEntries).not.toHaveBeenCalled()
    expect(result.current.queries.olderLogLoading).toBe(false)
  })
})
