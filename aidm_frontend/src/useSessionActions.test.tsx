// @vitest-environment jsdom
import type { FormEvent } from 'react'
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiClientError } from './api'
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

  describe('the next-chapter dialog', () => {
    const submitEvent = () => ({ preventDefault: vi.fn() }) as unknown as FormEvent<HTMLFormElement>
    const startBody = (call: number) => JSON.parse(apiFetchMock.mock.calls[call][3].body)
    const unsettledError = () =>
      new ApiClientError('Finish the fight or roll first.', 409, {
        error: 'Finish the fight or roll first.',
        error_code: 'chapter_unsettled',
        details: { session_id: 11, reason: 'active_combat' },
      })

    it('continues the story by default and closes once the chapter starts', async () => {
      apiFetchMock.mockResolvedValueOnce({ session_id: 12 })
      const options = createOptions()
      const { result } = renderHook(() => useSessionActions(options))

      act(() => result.current.openStartChapterDialog())
      expect(result.current.startChapterDialog).toMatchObject({ fresh: false, force: false, unsettled: false })
      await act(async () => result.current.submitStartChapterDialog(submitEvent()))

      expect(startBody(0)).toMatchObject({ campaign_id: 7, continuity: 'continue' })
      expect(startBody(0)).not.toHaveProperty('force')
      expect(result.current.startChapterDialog).toBeNull()
      expect(options.setSelectedSessionId).toHaveBeenCalledWith(12)
    })

    it('starts over from the beginning when that choice is made', async () => {
      apiFetchMock.mockResolvedValueOnce({ session_id: 12 })
      const { result } = renderHook(() => useSessionActions(createOptions()))

      act(() => result.current.openStartChapterDialog())
      act(() => result.current.updateStartChapterDialog({ fresh: true }))
      await act(async () => result.current.submitStartChapterDialog(submitEvent()))

      expect(startBody(0)).toMatchObject({ continuity: 'fresh' })
    })

    it('shows an unsettled refusal in the dialog and forces only after it is acknowledged', async () => {
      apiFetchMock.mockRejectedValueOnce(unsettledError()).mockResolvedValueOnce({ session_id: 12 })
      const options = createOptions()
      const { result } = renderHook(() => useSessionActions(options))

      act(() => result.current.openStartChapterDialog())
      act(() => result.current.updateStartChapterDialog({ force: true }))
      await act(async () => result.current.submitStartChapterDialog(submitEvent()))

      expect(startBody(0)).not.toHaveProperty('force')
      expect(result.current.startChapterDialog).toMatchObject({
        unsettled: true,
        pending: false,
        error: 'Finish the fight or roll first.',
      })
      expect(options.pushError).not.toHaveBeenCalled()

      await act(async () => result.current.submitStartChapterDialog(submitEvent()))
      expect(startBody(1)).toMatchObject({ continuity: 'continue', force: true })
      expect(result.current.startChapterDialog).toBeNull()
    })

    it('keeps other failures in the dialog and reports them', async () => {
      apiFetchMock.mockRejectedValueOnce(new Error('Network down'))
      const options = createOptions()
      const { result } = renderHook(() => useSessionActions(options))

      act(() => result.current.openStartChapterDialog())
      await act(async () => result.current.submitStartChapterDialog(submitEvent()))

      expect(result.current.startChapterDialog).toMatchObject({ error: 'Network down', unsettled: false, pending: false })
      expect(options.pushError).toHaveBeenCalledWith('persistence', 'Could not start the next chapter: Network down')
    })
  })
})
