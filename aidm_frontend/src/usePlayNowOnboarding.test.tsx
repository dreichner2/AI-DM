// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { PlayNowResponse } from './types'
import { usePlayNowOnboarding } from './usePlayNowOnboarding'

const apiFetchMock = vi.hoisted(() => vi.fn())

vi.mock('./api', async () => {
  const actual = await vi.importActual<typeof import('./api')>('./api')
  return { ...actual, apiFetch: apiFetchMock }
})

function createStorageMock(): Storage {
  const values = new Map<string, string>()
  return {
    get length() {
      return values.size
    },
    clear: vi.fn(() => values.clear()),
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    key: vi.fn((index: number) => [...values.keys()][index] ?? null),
    removeItem: vi.fn((key: string) => values.delete(key)),
    setItem: vi.fn((key: string, value: string) => values.set(key, value)),
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise
  })
  return { promise, resolve }
}

function playNowResponse(): PlayNowResponse {
  return {
    mode: 'play_now',
    workspace_id: 'owner',
    campaign_id: 10,
    session_id: 20,
    player_id: 30,
    world_id: 5,
    idempotent_replay: false,
    campaign: {
      campaign_id: 10,
      title: 'Road of Unremembered Kings',
      description: null,
      world_id: 5,
      world_name: 'The Old Road',
      created_at: null,
      updated_at: null,
      status: 'active',
      is_archived: false,
      current_quest: null,
      location: null,
      session_count: 1,
      latest_session_id: 20,
      latest_activity_at: null,
    },
    session: {
      session_id: 20,
      campaign_id: 10,
      created_at: null,
      status: 'active',
      deleted_at: null,
      updated_at: null,
      latest_activity_at: null,
      display_name: 'Play Now',
      turn_count: 0,
      latest_summary: '',
      is_archived: false,
      state_snapshot: {},
    },
    player: {
      player_id: 30,
      workspace_id: 'owner',
      account_id: null,
      username: null,
      campaign_id: 10,
      name: 'Danny',
      character_name: 'Arden Vale',
      race: 'Human',
      sex: 'male',
      profile_image: '/profile-icons/human_male.png',
      class_: 'Fighter',
      char_class: 'Fighter',
      level: 1,
      created_at: null,
      updated_at: null,
      stats: {},
      inventory: [],
      character_sheet: {},
    },
    pregen: {
      character_id: 'arden-vale',
      character_name: 'Arden Vale',
      name: 'Danny',
      race: 'Human',
      sex: 'male',
      class_: 'Fighter',
      char_class: 'Fighter',
      level: 1,
      tagline: 'A road-worn guardian.',
      profile_image: '/profile-icons/human_male.png',
      stats: {},
      inventory: [],
      character_sheet: {},
    },
    example_pack: {
      example_pack_id: 'road.unremembered-kings',
      pack_id: 'road.unremembered-kings',
      source_filename: 'road.json',
      source: 'bundled_example',
    },
    join_context: {
      workspace_id: 'owner',
      campaign_id: 10,
      session_id: 20,
      player_id: 30,
      world_id: 5,
      socket: {
        event: 'join_session',
        payload: { workspace_id: 'owner', session_id: 20, player_id: 30 },
      },
      send_message: {
        event: 'send_message',
        payload: {
          workspace_id: 'owner',
          session_id: 20,
          campaign_id: 10,
          world_id: 5,
          player_id: 30,
        },
      },
    },
  }
}

function createOptions(overrides: Record<string, unknown> = {}) {
  return {
    activeSessionId: null,
    auth: '',
    authRequired: false,
    backendReady: true,
    baseUrl: 'https://backend.example.test',
    campaignCount: 0,
    closeMobilePanels: vi.fn(),
    modalOpen: false,
    runtimeSettingsOpen: false,
    selectedCampaignId: null,
    selectedPlayerDetailId: null,
    selectedPlayerId: null,
    selectedSessionId: null,
    workspaceLoading: false,
    campaignUpserted: vi.fn(),
    sessionUpserted: vi.fn(),
    playerUpserted: vi.fn(),
    clearAuthTokenErrors: vi.fn(),
    loadPlayerDetail: vi.fn(() => Promise.resolve()),
    loadSessionData: vi.fn(() => Promise.resolve()),
    openCreateCampaignDialog: vi.fn(),
    pushError: vi.fn(),
    refreshCampaignWorkspace: vi.fn(() => Promise.resolve()),
    refreshRoot: vi.fn(() => Promise.resolve()),
    setClarificationRequest: vi.fn(),
    setLogEntries: vi.fn(),
    setMainTab: vi.fn(),
    setOptimisticEntries: vi.fn(),
    setPlayerDetail: vi.fn(),
    setSelectedCampaignId: vi.fn(),
    setSelectedPlayerId: vi.fn(),
    setSelectedSessionId: vi.fn(),
    setSessionState: vi.fn(),
    setStreamingTurn: vi.fn(),
    setTurnStatuses: vi.fn(),
    currentResponsePresent: false,
    dmResponseBlocking: false,
    sendPending: false,
    socketStatus: 'idle',
    startAdventure: vi.fn(),
    turnRowCount: 0,
    ...overrides,
  } as unknown as Parameters<typeof usePlayNowOnboarding>[0]
}

describe('usePlayNowOnboarding', () => {
  beforeEach(() => {
    apiFetchMock.mockReset()
    vi.stubGlobal('localStorage', createStorageMock())
  })

  afterEach(() => cleanup())

  it('shows first-run onboarding and persists continue/new-campaign choices', () => {
    const options = createOptions({ campaignCount: 1 })
    const { result } = renderHook(() => usePlayNowOnboarding(options))

    expect(result.current.showTitleScreen).toBe(true)
    expect(result.current.titleScreenCanContinue).toBe(true)

    act(() => result.current.continueFromTitleScreen())
    expect(localStorage.getItem('aidm:hasPlayed')).toBe('true')
    expect(options.closeMobilePanels).toHaveBeenCalledOnce()
    expect(result.current.showTitleScreen).toBe(false)

    act(() => result.current.createCampaignFromTitleScreen())
    expect(options.openCreateCampaignDialog).toHaveBeenCalledOnce()
    expect(options.closeMobilePanels).toHaveBeenCalledTimes(2)
  })

  it('honors the saved first-run choice and runtime visibility guards', () => {
    localStorage.setItem('aidm:hasPlayed', 'true')
    const options = createOptions({ campaignCount: 1 })
    const { result, rerender } = renderHook(
      ({ values }) => usePlayNowOnboarding(values),
      { initialProps: { values: options } },
    )

    expect(result.current.showTitleScreen).toBe(false)

    rerender({ values: createOptions({ campaignCount: 0, workspaceLoading: true }) })
    expect(result.current.showTitleScreen).toBe(false)
  })

  it('prepares Play Now once, refreshes its workspace, and auto-starts when joined', async () => {
    const request = deferred<PlayNowResponse>()
    apiFetchMock.mockReturnValue(request.promise)
    const refreshOrder: string[] = []
    const options = createOptions({
      refreshRoot: vi.fn(async () => {
        refreshOrder.push('root')
      }),
      refreshCampaignWorkspace: vi.fn(async () => {
        refreshOrder.push('campaign')
      }),
      loadSessionData: vi.fn(async () => {
        refreshOrder.push('session')
      }),
      loadPlayerDetail: vi.fn(async () => {
        refreshOrder.push('player')
      }),
    })
    const { result, rerender } = renderHook(
      ({ values }) => usePlayNowOnboarding(values),
      { initialProps: { values: options } },
    )

    let firstRequest!: Promise<void>
    act(() => {
      firstRequest = result.current.playNowFromTitleScreen()
      void result.current.playNowFromTitleScreen()
    })
    expect(apiFetchMock).toHaveBeenCalledOnce()
    expect(result.current.playNowPending).toBe(true)

    const payload = playNowResponse()
    request.resolve(payload)
    await act(async () => firstRequest)

    expect(options.campaignUpserted).toHaveBeenCalledWith(payload.campaign)
    expect(options.sessionUpserted).toHaveBeenCalledWith(payload.session)
    expect(options.playerUpserted).toHaveBeenCalledWith(payload.player)
    expect(options.setSelectedCampaignId).toHaveBeenCalledWith(10)
    expect(options.setSelectedSessionId).toHaveBeenCalledWith(20)
    expect(options.setSelectedPlayerId).toHaveBeenCalledWith(30)
    expect(options.setLogEntries).toHaveBeenCalledWith([])
    expect(options.setSessionState).toHaveBeenCalledWith(null)
    expect(options.setMainTab).toHaveBeenCalledWith('turns')
    expect(refreshOrder).toEqual(['root', 'campaign', 'session', 'player'])
    expect(localStorage.getItem('aidm:workspaceId')).toBe('owner')
    expect(result.current.playNowPending).toBe(false)

    rerender({
      values: {
        ...options,
        activeSessionId: 20,
        selectedCampaignId: 10,
        selectedSessionId: 20,
        selectedPlayerId: 30,
        selectedPlayerDetailId: 30,
        socketStatus: 'joined',
      },
    })
    expect(options.startAdventure).toHaveBeenCalledOnce()
  })
})
