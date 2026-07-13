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
      weapon_proficiencies: ['category:simple', 'category:martial'],
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

function guestAccountSession() {
  const workspace = {
    workspace_id: 'owner',
    workspace_name: 'Guest Table',
    table_name: 'Guest Table',
    access_mode: 'password',
    workspace_role: 'player',
    is_workspace_admin: false,
    created_at: null,
    updated_at: null,
  }
  return {
    account: {
      account_id: 41,
      username: 'guest-41',
      first_name: 'Guest',
      last_name: 'Adventurer',
      display_name: 'Guest Adventurer',
      workspace_id: 'owner',
      workspace_role: 'player',
      is_workspace_admin: false,
      requires_password_setup: false,
      workspaces: [workspace],
    },
    account_token: '',
    account_token_transport: 'http_only_cookie',
    workspace_id: 'owner',
    workspace_role: 'player',
    is_workspace_admin: false,
    claimed_player_ids: [30],
    workspaces: [workspace],
  }
}

function createOptions(overrides: Record<string, unknown> = {}) {
  return {
    activeSessionId: null,
    auth: '',
    authRequired: false,
    hostedAccessReady: false,
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
    openLogIn: vi.fn(),
    openCreateAccount: vi.fn(),
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

  it('starts hosted Play Now as a guest while keeping account choices separate', async () => {
    const payload = {
      ...playNowResponse(),
      account_session: guestAccountSession(),
      guest_account: true,
    }
    apiFetchMock.mockResolvedValue(payload)
    const adoptAccountSession = vi.fn()
    const options = createOptions({
      authRequired: true,
      hostedAccessReady: false,
      campaignCount: 0,
      selectedCampaignId: 10,
      selectedSessionId: 20,
      selectedPlayerId: 30,
      adoptAccountSession,
    })
    const { result } = renderHook(() => usePlayNowOnboarding(options))

    expect(result.current.showTitleScreen).toBe(true)
    expect(result.current.titleScreenAccountReady).toBe(false)

    await act(async () => result.current.playNowFromTitleScreen())
    expect(apiFetchMock).toHaveBeenCalledWith(
      'https://backend.example.test',
      '/api/accounts/play-now',
      '',
      expect.objectContaining({ method: 'POST' }),
    )
    expect(adoptAccountSession).toHaveBeenCalledWith(payload.account_session)
    expect(options.openLogIn).not.toHaveBeenCalled()
    expect(options.campaignUpserted).toHaveBeenCalledWith(payload.campaign)

    act(() => result.current.logInFromTitleScreen())
    act(() => result.current.createAccountFromTitleScreen())
    expect(options.openLogIn).toHaveBeenCalledOnce()
    expect(options.openCreateAccount).toHaveBeenCalledOnce()
    expect(options.closeMobilePanels).toHaveBeenCalledTimes(3)
  })

  it('preserves campaign selection for authenticated hosted accounts with existing campaigns', () => {
    const options = createOptions({
      authRequired: true,
      hostedAccessReady: true,
      campaignCount: 1,
    })
    const { result } = renderHook(() => usePlayNowOnboarding(options))

    expect(result.current.showTitleScreen).toBe(false)
    expect(result.current.titleScreenAccountReady).toBe(true)
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
