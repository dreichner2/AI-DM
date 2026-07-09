// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useSessionActions } from './useSessionActions'

const apiFetchMock = vi.hoisted(() => vi.fn())

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api')
  return { ...actual, apiFetch: apiFetchMock }
})

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function createOptions() {
  return {
    auth: 'token',
    baseUrl: 'https://backend.example.test',
    campaign: null,
    activeSession: null,
    sessionDisplayFallback: null,
    selectedCampaignId: 7,
    selectedSessionId: null,
    selectedPlayerId: null,
    players: [],
    selectedPlayer: null,
    playerDetail: null,
    sessionState: null,
    logEntries: [],
    maps: [],
    segments: [],
    metrics: null,
    rememberDialogTrigger: vi.fn(),
    sessionMenuButton: vi.fn(() => null),
    sessionDisplayName: vi.fn(() => 'Session'),
    loadSessionData: vi.fn(),
    refreshRoot: vi.fn(),
    refreshCampaignWorkspace: vi.fn(() => Promise.resolve()),
    sessionUpserted: vi.fn(),
    setSelectedCampaignId: vi.fn(),
    setSelectedSessionId: vi.fn(),
    setLogEntries: vi.fn(),
    setSessionState: vi.fn(),
    setOptimisticEntries: vi.fn(),
    setStreamingTurn: vi.fn(),
    setMainTab: vi.fn(),
    setSessionMenuOpen: vi.fn(),
    pushError: vi.fn(),
  } as unknown as Parameters<typeof useSessionActions>[0]
}

describe('useSessionActions', () => {
  beforeEach(() => apiFetchMock.mockReset())
  afterEach(() => cleanup())

  it('starts a session synchronously single-flight and unlocks after completion', async () => {
    const firstStart = deferred<{ session_id: number }>()
    apiFetchMock.mockReturnValueOnce(firstStart.promise).mockResolvedValueOnce({ session_id: 12 })
    const options = createOptions()
    const { result } = renderHook(() => useSessionActions(options))

    let firstRequest!: Promise<void>
    let duplicateRequest!: Promise<void>
    act(() => {
      firstRequest = result.current.startSession()
      duplicateRequest = result.current.startSession()
    })

    expect(duplicateRequest).toBe(firstRequest)
    expect(apiFetchMock).toHaveBeenCalledTimes(1)

    firstStart.resolve({ session_id: 11 })
    await act(async () => firstRequest)

    expect(options.setSelectedSessionId).toHaveBeenCalledWith(11)
    expect(options.refreshCampaignWorkspace).toHaveBeenCalledWith(7)

    await act(async () => result.current.startSession())

    expect(apiFetchMock).toHaveBeenCalledTimes(2)
    expect(options.setSelectedSessionId).toHaveBeenLastCalledWith(12)
  })

  it('scopes in-flight starts by campaign and ignores a late response after navigation', async () => {
    const firstStart = deferred<{ session_id: number }>()
    const secondStart = deferred<{ session_id: number }>()
    apiFetchMock.mockReturnValueOnce(firstStart.promise).mockReturnValueOnce(secondStart.promise)
    const options = createOptions()
    const { result, rerender } = renderHook(
      ({ selectedCampaignId }) =>
        useSessionActions({ ...options, selectedCampaignId }),
      { initialProps: { selectedCampaignId: 7 } },
    )

    let firstRequest!: Promise<void>
    act(() => {
      firstRequest = result.current.startSession()
    })
    rerender({ selectedCampaignId: 8 })
    let secondRequest!: Promise<void>
    act(() => {
      secondRequest = result.current.startSession()
    })

    expect(apiFetchMock).toHaveBeenCalledTimes(2)
    expect(JSON.parse(apiFetchMock.mock.calls[0][3].body)).toMatchObject({ campaign_id: 7 })
    expect(JSON.parse(apiFetchMock.mock.calls[1][3].body)).toMatchObject({ campaign_id: 8 })

    secondStart.resolve({ session_id: 12 })
    await act(async () => secondRequest)
    expect(options.setSelectedSessionId).toHaveBeenCalledWith(12)
    expect(options.refreshCampaignWorkspace).toHaveBeenCalledWith(8)

    firstStart.resolve({ session_id: 11 })
    await act(async () => firstRequest)
    expect(options.setSelectedSessionId).toHaveBeenCalledTimes(1)
    expect(options.refreshCampaignWorkspace).toHaveBeenCalledTimes(1)
  })
})
