// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Campaign, CampaignWorkspace, PlayNowResponse, SessionSummary } from './types'
import { usePlayNowOnboarding } from './usePlayNowOnboarding'

const apiFetchMock = vi.hoisted(() => vi.fn())
const HEROES_PATH = '/api/onboarding/pregenerated-characters'

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

/** Answers apiFetch by path; any other path fails like an unreachable route. */
function routeApi(routes: Record<string, () => unknown>) {
  apiFetchMock.mockImplementation((_baseUrl: string, path: string) => {
    const route = routes[path]
    return route ? Promise.resolve(route()) : Promise.reject(new Error(`Unhandled ${path}`))
  })
}

function callsTo(path: string) {
  return apiFetchMock.mock.calls.filter(([, calledPath]) => calledPath === path)
}

function requestBody(path: string) {
  const [, , , init] = callsTo(path).at(-1) ?? []
  return JSON.parse((init as RequestInit).body as string) as unknown
}

function heroLibrary() {
  const hero = (id: string, name: string, race: string, charClass: string, portrait: string) => ({
    id,
    character_id: id,
    character_name: name,
    name: 'Local Player',
    race,
    char_class: charClass,
    tagline: `${name} walks the road.`,
    portrait,
    profile_image: portrait,
  })
  return {
    characters: [
      hero('arden-vale', 'Arden Vale', 'Human', 'Fighter', '/profile-icons/human_male.png'),
      hero('liora-quill', 'Liora Quill', 'Elf', 'Wizard', '/profile-icons/elf_female.png'),
      hero('mara-fen', 'Mara Fen', 'Halfling', 'Rogue', '/profile-icons/halfling_female.png'),
    ],
    count: 3,
    default_character_id: 'arden-vale',
  }
}

function campaign(overrides: Partial<Campaign>): Campaign {
  return {
    campaign_id: 10,
    title: 'Road of Unremembered Kings',
    description: null,
    world_id: 5,
    world_name: 'The Old Road',
    created_at: '2026-10-01T10:00:00Z',
    updated_at: '2026-10-01T10:00:00Z',
    status: 'active',
    is_archived: false,
    current_quest: null,
    location: 'The Lantern Post Inn',
    session_count: 1,
    latest_session_id: 20,
    latest_activity_at: '2026-10-01T10:00:00Z',
    ...overrides,
  }
}

function chapter(overrides: Partial<SessionSummary>): SessionSummary {
  return {
    session_id: 20,
    campaign_id: 11,
    name: null,
    display_name: 'Chapter',
    status: 'active',
    is_current: false,
    is_archived: false,
    chapter_number: 1,
    chapter_recap: '',
    ended_at: null,
    created_at: null,
    updated_at: null,
    latest_activity_at: null,
    turn_count: 0,
    scene_title: null,
    summary_excerpt: '',
    imported: false,
    play_now: false,
    ...overrides,
  }
}

function workspaceFor(campaignRow: Campaign, sessions: SessionSummary[]) {
  return { campaign: campaignRow, sessions, players: [] } as unknown as CampaignWorkspace
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
    session: chapter({ campaign_id: 10, display_name: 'Play Now', is_current: true, play_now: true }),
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
    sessionLoading: false,
    loadedSessionId: null,
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
    routeApi({})
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

  it('shows the title screen in the Story view whenever no adventure is open', () => {
    localStorage.setItem('aidm:hasPlayed', 'true')
    const options = createOptions({ campaignCount: 2 })
    const { result, rerender } = renderHook(
      ({ values }) => usePlayNowOnboarding(values),
      { initialProps: { values: options } },
    )
    expect(result.current.showTitleScreen).toBe(false)

    rerender({ values: createOptions({ campaignCount: 2, storyView: true }) })
    expect(result.current.showTitleScreen).toBe(true)
    // A stale hero or chapter choice without an adventure still leaves nothing to play.
    rerender({ values: createOptions({ campaignCount: 2, storyView: true, selectedPlayerId: 30, selectedSessionId: 20 }) })
    expect(result.current.showTitleScreen).toBe(true)
    rerender({ values: createOptions({ campaignCount: 2, storyView: true, selectedCampaignId: 11 }) })
    expect(result.current.showTitleScreen).toBe(false)
    // A sheet opened from the title screen covers it.
    rerender({ values: createOptions({ campaignCount: 2, storyView: true, modalOpen: true }) })
    expect(result.current.showTitleScreen).toBe(false)
  })

  it('reads an adventure as not yet begun until its first turn', async () => {
    const blankPage = campaign({ campaign_id: 11, title: 'The Salt Road', session_count: 0, latest_activity_at: '2026-11-10T09:00:00Z' })
    const sessions: SessionSummary[] = []
    routeApi({ '/api/campaigns/11/workspace': () => workspaceFor(blankPage, sessions) })
    const options = createOptions({ campaignCount: 1, campaigns: [blankPage] })
    const { result, rerender } = renderHook(
      ({ values }) => usePlayNowOnboarding(values),
      { initialProps: { values: options } },
    )
    // From the campaign list: no chapter at all.
    expect(result.current.titleScreenProps.resume?.begun).toBe(false)
    await waitFor(() => expect(callsTo('/api/campaigns/11/workspace')).toHaveLength(1))
    expect(result.current.titleScreenProps.resume?.begun).toBe(false)

    // Its first chapter exists but nothing has been played in it.
    sessions.push(chapter({ session_id: 301, chapter_number: 1, is_current: true, turn_count: 0 }))
    rerender({ values: { ...options, campaigns: [{ ...blankPage, session_count: 1 }], modalOpen: true } })
    rerender({ values: { ...options, campaigns: [{ ...blankPage, session_count: 1 }] } })
    await waitFor(() => expect(result.current.titleScreenProps.resume?.chapterNumber).toBe(1))
    expect(result.current.titleScreenProps.resume?.begun).toBe(false)

    sessions[0] = { ...sessions[0], turn_count: 2 }
    rerender({ values: { ...options, modalOpen: true } })
    rerender({ values: options })
    await waitFor(() => expect(callsTo('/api/campaigns/11/workspace')).toHaveLength(3))
    await waitFor(() => expect(result.current.titleScreenProps.resume?.begun).toBe(true))
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
    routeApi({ '/api/onboarding/play-now': () => request.promise })
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
    expect(callsTo('/api/onboarding/play-now')).toHaveLength(1)
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
        loadedSessionId: 20,
      },
    })
    expect(options.startAdventure).toHaveBeenCalledOnce()
  })

  it('waits for the whole refresh chain and a hydrated session before auto-starting', async () => {
    apiFetchMock.mockResolvedValue(playNowResponse())
    const playerDetail = deferred<void>()
    const options = createOptions({
      loadPlayerDetail: vi.fn(() => playerDetail.promise),
    })
    const joined = {
      ...options,
      activeSessionId: 20,
      selectedCampaignId: 10,
      selectedSessionId: 20,
      selectedPlayerId: 30,
      selectedPlayerDetailId: 30,
      socketStatus: 'joined',
      loadedSessionId: 20,
    }
    const { result, rerender } = renderHook(
      ({ values }) => usePlayNowOnboarding(values),
      { initialProps: { values: options } },
    )

    let request!: Promise<void>
    act(() => {
      request = result.current.playNowFromTitleScreen()
    })
    await act(async () => {
      await Promise.resolve()
    })
    rerender({ values: joined })
    expect(result.current.playNowPending).toBe(true)
    expect(options.startAdventure).not.toHaveBeenCalled()

    playerDetail.resolve()
    rerender({ values: { ...joined, sessionLoading: true } })
    await act(async () => request)
    expect(result.current.playNowPending).toBe(false)
    expect(options.startAdventure).not.toHaveBeenCalled()

    rerender({ values: { ...joined, workspaceLoading: true } })
    rerender({ values: { ...joined, loadedSessionId: 19 } })
    expect(options.startAdventure).not.toHaveBeenCalled()

    rerender({ values: joined })
    rerender({ values: { ...joined, sendPending: true } })
    rerender({ values: joined })
    expect(options.startAdventure).toHaveBeenCalledOnce()
  })

  it('loads the ready-made heroes once when the title shows and remembers the chosen hero', async () => {
    localStorage.setItem('aidm:titleHero', 'mara-fen')
    routeApi({ [HEROES_PATH]: heroLibrary })
    const hidden = createOptions({ backendReady: false })
    const { result, rerender } = renderHook(
      ({ values }) => usePlayNowOnboarding(values),
      { initialProps: { values: hidden } },
    )
    expect(result.current.showTitleScreen).toBe(false)
    expect(apiFetchMock).not.toHaveBeenCalled()
    expect(result.current.titleScreenProps.heroesStatus).toBe('loading')

    const shown = { ...hidden, backendReady: true }
    rerender({ values: shown })
    await waitFor(() => expect(result.current.titleScreenProps.heroesStatus).toBe('ready'))

    expect(callsTo(HEROES_PATH)).toHaveLength(1)
    expect(result.current.titleScreenProps.heroes.map((hero) => hero.id)).toEqual([
      'arden-vale',
      'liora-quill',
      'mara-fen',
    ])
    expect(result.current.titleScreenProps.heroes[2]).toEqual({
      id: 'mara-fen',
      name: 'Mara Fen',
      race: 'Halfling',
      charClass: 'Rogue',
      tagline: 'Mara Fen walks the road.',
      portrait: '/profile-icons/halfling_female.png',
    })
    expect(result.current.titleScreenProps.selectedHeroId).toBe('mara-fen')

    act(() => result.current.titleScreenProps.onSelectHero('liora-quill'))
    expect(result.current.titleScreenProps.selectedHeroId).toBe('liora-quill')
    expect(localStorage.getItem('aidm:titleHero')).toBe('liora-quill')

    rerender({ values: { ...shown, modalOpen: true } })
    rerender({ values: shown })
    expect(result.current.showTitleScreen).toBe(true)
    expect(callsTo(HEROES_PATH)).toHaveLength(1)
  })

  it('chooses the server default hero when the remembered one is no longer offered', async () => {
    localStorage.setItem('aidm:titleHero', 'retired-hero')
    routeApi({ [HEROES_PATH]: heroLibrary })
    const { result } = renderHook(() => usePlayNowOnboarding(createOptions()))

    await waitFor(() => expect(result.current.titleScreenProps.selectedHeroId).toBe('arden-vale'))
  })

  it('sends the chosen hero as character_id to local Play Now', async () => {
    routeApi({ [HEROES_PATH]: heroLibrary, '/api/onboarding/play-now': playNowResponse })
    const options = createOptions()
    const { result } = renderHook(() => usePlayNowOnboarding(options))
    await waitFor(() => expect(result.current.titleScreenProps.heroesStatus).toBe('ready'))

    act(() => result.current.titleScreenProps.onSelectHero('mara-fen'))
    act(() => result.current.titleScreenProps.onPlayNow({ fresh: false }))
    await waitFor(() => expect(options.setSelectedSessionId).toHaveBeenCalledWith(20))

    expect(callsTo('/api/onboarding/play-now')).toHaveLength(1)
    expect(requestBody('/api/onboarding/play-now')).toEqual({ character_id: 'mara-fen' })
  })

  it('asks for a fresh adventure when a returning player plays a ready-made one', async () => {
    routeApi({ [HEROES_PATH]: heroLibrary, '/api/onboarding/play-now': playNowResponse })
    const options = createOptions()
    const { result } = renderHook(() => usePlayNowOnboarding(options))
    await waitFor(() => expect(result.current.titleScreenProps.heroesStatus).toBe('ready'))

    act(() => result.current.titleScreenProps.onPlayNow({ fresh: true }))
    await waitFor(() => expect(options.setSelectedSessionId).toHaveBeenCalledWith(20))
    expect(requestBody('/api/onboarding/play-now')).toEqual({ character_id: 'arden-vale', fresh: true })
  })

  it('sends the chosen hero as character_id to hosted guest Play Now', async () => {
    const payload = { ...playNowResponse(), account_session: guestAccountSession(), guest_account: true }
    routeApi({ [HEROES_PATH]: heroLibrary, '/api/accounts/play-now': () => payload })
    const options = createOptions({
      authRequired: true,
      hostedAccessReady: false,
      adoptAccountSession: vi.fn(),
    })
    const { result } = renderHook(() => usePlayNowOnboarding(options))
    await waitFor(() => expect(result.current.titleScreenProps.heroesStatus).toBe('ready'))

    act(() => result.current.titleScreenProps.onSelectHero('liora-quill'))
    await act(async () => result.current.playNowFromTitleScreen())

    expect(callsTo('/api/onboarding/play-now')).toHaveLength(0)
    expect(requestBody('/api/accounts/play-now')).toEqual({ character_id: 'liora-quill' })
    expect(options.campaignUpserted).toHaveBeenCalledWith(payload.campaign)
  })

  it('keeps Play Now on the default hero when the hero list cannot be loaded', async () => {
    routeApi({ '/api/onboarding/play-now': playNowResponse })
    const options = createOptions()
    const { result, rerender } = renderHook(
      ({ values }) => usePlayNowOnboarding(values),
      { initialProps: { values: options } },
    )
    await waitFor(() => expect(result.current.titleScreenProps.heroesStatus).toBe('failed'))
    expect(result.current.titleScreenProps.heroes).toEqual([])
    expect(result.current.titleScreenProps.selectedHeroId).toBeNull()

    await act(async () => result.current.playNowFromTitleScreen())
    expect(requestBody('/api/onboarding/play-now')).toEqual({})
    expect(options.pushError).not.toHaveBeenCalled()

    // The same credentials are not retried; new ones are.
    rerender({ values: { ...options, selectedCampaignId: null } })
    expect(callsTo(HEROES_PATH)).toHaveLength(1)
    routeApi({ [HEROES_PATH]: heroLibrary })
    rerender({ values: { ...options, auth: 'account-token' } })
    await waitFor(() => expect(result.current.titleScreenProps.heroesStatus).toBe('ready'))
    expect(callsTo(HEROES_PATH)).toHaveLength(2)
    expect(callsTo(HEROES_PATH)[1]?.[2]).toBe('account-token')
  })

  it('offers the most recently played campaign and continues into its current chapter', async () => {
    const older = campaign({ campaign_id: 10, title: 'Shadow over the Greenway', latest_activity_at: '2026-11-01T10:00:00Z' })
    const latest = campaign({
      campaign_id: 11,
      title: 'The Road of Unremembered Kings',
      location: 'The Lantern Post Inn',
      latest_activity_at: '2026-11-09T10:00:00Z',
    })
    const archived = campaign({ campaign_id: 12, title: 'Archived', is_archived: true, latest_activity_at: '2026-11-10T09:00:00Z' })
    const workspace = workspaceFor(latest, [
      chapter({ session_id: 201, chapter_number: 1, status: 'ended', turn_count: 40, summary_excerpt: 'Old news.' }),
      chapter({
        session_id: 202,
        chapter_number: 2,
        is_current: true,
        turn_count: 6,
        summary_excerpt: 'The party left the inn at dawn.',
        chapter_recap: 'The ledger named every traveller but one.',
        latest_activity_at: '2026-11-09T12:00:00Z',
        scene_title: 'The Old North Road',
        continue_line: 'The Old North Road — Mist lies on the milestones.',
      }),
    ])
    routeApi({ [HEROES_PATH]: heroLibrary, '/api/campaigns/11/workspace': () => workspace })
    const options = createOptions({ campaignCount: 3, campaigns: [older, latest, archived] })
    const { result, rerender } = renderHook(
      ({ values }) => usePlayNowOnboarding(values),
      { initialProps: { values: options } },
    )

    expect(result.current.titleScreenProps.resume).toEqual({
      title: 'The Road of Unremembered Kings',
      chapterNumber: null,
      scene: 'The Lantern Post Inn',
      lastPlayedAt: '2026-11-09T10:00:00Z',
      continueLine: null,
      begun: true,
    })
    await waitFor(() => expect(result.current.titleScreenProps.resume?.chapterNumber).toBe(2))
    expect(result.current.titleScreenProps.resume).toEqual({
      title: 'The Road of Unremembered Kings',
      chapterNumber: 2,
      scene: 'The Old North Road',
      lastPlayedAt: '2026-11-09T12:00:00Z',
      continueLine: 'The Old North Road — Mist lies on the milestones.',
      begun: true,
    })
    rerender({ values: { ...options } })
    expect(callsTo('/api/campaigns/11/workspace')).toHaveLength(1)
    expect(callsTo('/api/campaigns/10/workspace')).toHaveLength(0)

    act(() => result.current.titleScreenProps.onContinue())

    expect(options.setSelectedCampaignId).toHaveBeenCalledWith(11)
    expect(options.setSelectedSessionId).toHaveBeenCalledWith(202)
    expect(options.setMainTab).toHaveBeenCalledWith('turns')
    expect(localStorage.getItem('aidm:hasPlayed')).toBe('true')
    expect(result.current.showTitleScreen).toBe(false)
  })

  it('never shows the rolling summary, and reads the continue line again each time the title opens', async () => {
    const latest = campaign({ campaign_id: 11 })
    const sessions = [chapter({ session_id: 202, is_current: true, turn_count: 3, summary_excerpt: 'The caravan was searched.' })]
    routeApi({ '/api/campaigns/11/workspace': () => workspaceFor(latest, sessions) })
    const options = createOptions({ campaignCount: 1, campaigns: [latest] })
    const { result, rerender } = renderHook(
      ({ values }) => usePlayNowOnboarding(values),
      { initialProps: { values: options } },
    )
    await waitFor(() => expect(result.current.titleScreenProps.resume?.chapterNumber).toBe(1))
    expect(result.current.titleScreenProps.resume?.continueLine).toBeNull()

    sessions[0] = { ...sessions[0], continue_line: 'The Lantern Post — The bell rings three times.' }
    rerender({ values: { ...options, modalOpen: true } })
    rerender({ values: options })
    await waitFor(() =>
      expect(result.current.titleScreenProps.resume?.continueLine).toBe('The Lantern Post — The bell rings three times.'),
    )
    expect(callsTo('/api/campaigns/11/workspace')).toHaveLength(2)
  })

  it('returns to the title without changing the selection', () => {
    const latest = campaign({ campaign_id: 11 })
    routeApi({ [HEROES_PATH]: heroLibrary, '/api/campaigns/11/workspace': () => workspaceFor(latest, []) })
    const options = createOptions({
      authRequired: true,
      hostedAccessReady: true,
      campaignCount: 1,
      campaigns: [latest],
      selectedCampaignId: 11,
      selectedSessionId: 20,
      selectedPlayerId: 30,
      openCampaignChooser: vi.fn(),
    })
    const { result } = renderHook(() => usePlayNowOnboarding(options))
    expect(result.current.showTitleScreen).toBe(false)

    act(() => result.current.returnToTitle())

    expect(result.current.showTitleScreen).toBe(true)
    expect(options.closeMobilePanels).toHaveBeenCalledOnce()
    expect(options.setSelectedCampaignId).not.toHaveBeenCalled()
    expect(options.setSelectedSessionId).not.toHaveBeenCalled()
    expect(options.setSelectedPlayerId).not.toHaveBeenCalled()
    expect(result.current.titleScreenProps.resume?.title).toBe('Road of Unremembered Kings')

    act(() => result.current.titleScreenProps.onOpenAdventures())
    expect(options.openCampaignChooser).toHaveBeenCalledOnce()
    expect(result.current.showTitleScreen).toBe(false)

    act(() => result.current.returnToTitle())
    act(() => result.current.titleScreenProps.onContinue())
    expect(options.setSelectedCampaignId).toHaveBeenCalledWith(11)
    expect(result.current.showTitleScreen).toBe(false)
  })

  it('keeps every field it returned before and hands TitleScreen all of its props', () => {
    const { result } = renderHook(() => usePlayNowOnboarding(createOptions({ safeMode: true })))

    expect(Object.keys(result.current)).toEqual(
      expect.arrayContaining([
        'createAccountFromTitleScreen',
        'createCampaignFromTitleScreen',
        'continueFromTitleScreen',
        'logInFromTitleScreen',
        'playNowFromTitleScreen',
        'playNowPending',
        'showTitleScreen',
        'titleScreenAccountReady',
        'titleScreenCanContinue',
        'returnToTitle',
        'titleScreenProps',
      ]),
    )
    expect(Object.keys(result.current.titleScreenProps).sort()).toEqual([
      'accountReady',
      'heroes',
      'heroesStatus',
      'onContinue',
      'onCreateAccount',
      'onCreateCampaign',
      'onLogIn',
      'onOpenAdventures',
      'onPlayNow',
      'onSelectHero',
      'pending',
      'resume',
      'safeMode',
      'selectedHeroId',
    ])
    expect(result.current.titleScreenProps.safeMode).toBe(true)
    expect(result.current.titleScreenProps.resume).toBeNull()
    expect(result.current.titleScreenProps.accountReady).toBe(true)
  })
})
