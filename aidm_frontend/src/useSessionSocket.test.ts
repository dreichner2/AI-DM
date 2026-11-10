// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import type { Socket } from 'socket.io-client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { trustBackendOrigin, writeOriginScopedStorage } from './api'
import type { StreamingTurn } from './types'
import {
  buildSessionSocketConnection,
  isTerminalSessionSocketError,
  normalizeRollRequiredPayload,
  normalizeRollResolvedPayload,
  normalizeTurnDuplicatePayload,
  useSessionSocket,
} from './useSessionSocket'

// Each io() call returns a new fake socket that, like socket.io, fires every
// still-registered 'disconnect' listener synchronously from disconnect().
const socketFactory = vi.hoisted(() => {
  const sockets: Array<{
    connected: boolean
    active: boolean
    listeners: Map<string, Set<(...args: unknown[]) => void>>
    emit: ReturnType<typeof vi.fn>
    on: (event: string, listener: (...args: unknown[]) => void) => unknown
    removeAllListeners: () => unknown
    disconnect: () => unknown
    fire: (event: string, ...args: unknown[]) => void
  }> = []
  const io = vi.fn(() => {
    const listeners = new Map<string, Set<(...args: unknown[]) => void>>()
    const socket = {
      connected: true,
      active: true,
      listeners,
      emit: vi.fn(),
      on(event: string, listener: (...args: unknown[]) => void) {
        const registered = listeners.get(event) ?? new Set()
        registered.add(listener)
        listeners.set(event, registered)
        return socket
      },
      removeAllListeners() {
        listeners.clear()
        return socket
      },
      disconnect() {
        if (socket.connected) socket.fire('disconnect', 'io client disconnect')
        socket.connected = false
        return socket
      },
      fire(event: string, ...args: unknown[]) {
        listeners.get(event)?.forEach((listener) => listener(...args))
      },
    }
    sockets.push(socket)
    return socket
  })
  return { io, sockets }
})

vi.mock('socket.io-client', () => ({ io: socketFactory.io }))

describe('session socket backend origin credential boundary', () => {
  beforeEach(() => {
    localStorage.clear()
    sessionStorage.clear()
  })

  it('omits all Socket.IO auth and browser credentials for an untrusted backend', () => {
    const savedBackend = 'https://saved-backend.example.test'
    localStorage.setItem('aidm:baseUrl', savedBackend)
    trustBackendOrigin(savedBackend)
    writeOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', 'saved-workspace-token', savedBackend)
    writeOriginScopedStorage(localStorage, 'aidm:workspaceId', 'saved-workspace', savedBackend)

    const { socketOptions } = buildSessionSocketConnection(
      'https://untrusted-tunnel.ngrok-free.app',
      'saved-account-token',
    )

    expect(socketOptions.auth).toBeUndefined()
    expect(socketOptions.withCredentials).toBe(false)
    expect(socketOptions.extraHeaders).toEqual({ 'ngrok-skip-browser-warning': 'true' })
  })

  it('includes only credentials scoped to a trusted backend and retains ngrok support', () => {
    const backend = 'https://trusted-tunnel.ngrok-free.app'
    trustBackendOrigin(backend)
    writeOriginScopedStorage(sessionStorage, 'aidm:workspaceToken', 'trusted-workspace-token', backend)
    writeOriginScopedStorage(localStorage, 'aidm:workspaceId', 'trusted-workspace', backend)

    const { socketOptions } = buildSessionSocketConnection(backend, 'trusted-account-token')

    expect(socketOptions.auth).toEqual({
      account_token: 'trusted-account-token',
      workspace_token: 'trusted-workspace-token',
    })
    expect(socketOptions.withCredentials).toBeUndefined()
    expect(socketOptions.extraHeaders).toEqual({ 'ngrok-skip-browser-warning': 'true' })
  })
})

describe('session socket lifecycle errors', () => {
  it('treats unavailable session, campaign, and player bindings as terminal', () => {
    expect(isTerminalSessionSocketError('session_archived')).toBe(true)
    expect(isTerminalSessionSocketError('campaign_deleted')).toBe(true)
    expect(isTerminalSessionSocketError('player_identity_mismatch')).toBe(true)
    expect(isTerminalSessionSocketError('roll_required')).toBe(false)
    expect(isTerminalSessionSocketError('rate_limited')).toBe(false)
  })
})

describe('authoritative socket payload validation', () => {
  const resolvedRoll = {
    session_id: 20,
    turn_id: 81,
    player_id: 30,
    client_message_id: 'client-roll-1',
    pending_turn_id: null,
    rule_type: 'ability_check',
    die: 'd20',
    mode: 'advantage',
    rolls: [7, 18],
    kept: 18,
    modifier: 5,
    total: 23,
    reason: 'STR check',
    result_visibility: 'hidden_until_landed',
    ability: { key: 'strength', label: 'STR', score: 16, modifier: 3 },
    proficiency: { bonus: 2, skills: ['athletics'], multiplier: 2 },
    modifier_breakdown: {
      ability_modifier: 3,
      proficiency_bonus: 2,
      proficiency_multiplier: 2,
      wound_penalty: 0,
      total: 5,
    },
    authoritative: true,
  }

  it('accepts a consistent authoritative roll and duplicate receipt', () => {
    expect(normalizeRollResolvedPayload(resolvedRoll)).toMatchObject({
      client_message_id: 'client-roll-1',
      rolls: [7, 18],
      kept: 18,
      modifier: 5,
      total: 23,
      proficiency: { bonus: 2, skills: ['athletics'], multiplier: 2 },
      modifier_breakdown: expect.objectContaining({ proficiency_multiplier: 2 }),
      authoritative: true,
    })
    expect(normalizeTurnDuplicatePayload({
      session_id: 20,
      turn_id: 81,
      client_message_id: 'client-roll-1',
    })).toEqual({ session_id: 20, turn_id: 81, client_message_id: 'client-roll-1' })
  })

  it('accepts the public room payload without private character provenance', () => {
    const publicRoll: Record<string, unknown> = { ...resolvedRoll }
    delete publicRoll.ability
    delete publicRoll.proficiency
    delete publicRoll.modifier_breakdown

    const normalized = normalizeRollResolvedPayload(publicRoll)

    expect(normalized).toMatchObject({
      rolls: [7, 18],
      kept: 18,
      modifier: 5,
      total: 23,
      authoritative: true,
    })
    expect(normalized).not.toHaveProperty('ability')
    expect(normalized).not.toHaveProperty('proficiency')
    expect(normalized).not.toHaveProperty('modifier_breakdown')
  })

  it('rejects client-like, unauthoritative, or internally inconsistent results', () => {
    expect(normalizeRollResolvedPayload({ ...resolvedRoll, authoritative: false })).toBeNull()
    expect(normalizeRollResolvedPayload({ ...resolvedRoll, total: 999 })).toBeNull()
    expect(normalizeRollResolvedPayload({ ...resolvedRoll, rolls: [21] })).toBeNull()
    expect(normalizeRollResolvedPayload({ ...resolvedRoll, proficiency: { bonus: 2 } })).toBeNull()
  })

  it('normalizes server roll guidance while dropping private or malformed fields', () => {
    expect(normalizeRollRequiredPayload({
      session_id: 20,
      pending_turn_id: 80,
      rule_type: 'stealth',
      dc_hint: 'DC 14',
      prompt: 'Roll to slip past the sentry.',
      remaining_player_ids: [30, '31', -1],
      roll_spec: {
        die: 'd20',
        mode: 'disadvantage',
        rule_type: 'stealth',
        reason: 'Sneak past the sentry',
        result_visibility: 'hidden_until_landed',
        ability: { key: 'dexterity', label: 'DEX', score: 18, modifier: 4 },
        attack: { private: true },
      },
    })).toEqual({
      sessionId: 20,
      pendingTurnId: 80,
      ruleType: 'stealth',
      dcHint: 'DC 14',
      prompt: 'Roll to slip past the sentry.',
      remainingPlayerIds: [30, 31],
      rollSpec: {
        die: 'd20',
        mode: 'disadvantage',
        ruleType: 'stealth',
        reason: 'Sneak past the sentry',
        resultVisibility: 'hidden_until_landed',
        ability: { key: 'dexterity', label: 'DEX' },
      },
    })

    expect(normalizeRollRequiredPayload({ pending_turn_id: 80 })).toBeNull()
  })
})

type SocketHookOptions = Parameters<typeof useSessionSocket>[0]

function hookOptions(overrides: Partial<SocketHookOptions> = {}): SocketHookOptions {
  return {
    auth: '',
    baseUrl: '',
    selectedSessionId: 20,
    selectedPlayerId: 30,
    selectedCampaignId: 10,
    socketReconnectKey: 0,
    socketRef: { current: null as Socket | null },
    loadSessionData: vi.fn(() => Promise.resolve()),
    refreshPlayerDetail: vi.fn(() => Promise.resolve()),
    pushError: vi.fn(),
    rememberStreamedTtsTurn: vi.fn(),
    resetTtsFailureForNextResponse: vi.fn(),
    stopTtsAudio: vi.fn(),
    setActivePlayers: vi.fn(),
    setSessionState: vi.fn(),
    setSocketStatus: vi.fn(),
    setSendPending: vi.fn(),
    setOptimisticEntries: vi.fn(),
    setStreamingTurn: vi.fn(),
    setTurnStatuses: vi.fn(),
    setClarificationRequest: vi.fn(),
    setSceneMusicSyncState: vi.fn(),
    setSceneState: vi.fn(),
    spokenTextLengthRef: { current: 0 },
    speakableStreamingTextRef: { current: '' },
    queueTtsNarrationRef: { current: null },
    ttsEnabledRef: { current: false },
    ttsQueueSuppressedRef: { current: false },
    ttsFailureReportedRef: { current: false },
    ttsPartialFlushTimerRef: { current: null },
    lastSpokenDmEntryRef: { current: null },
    lastSpokenTurnIdRef: { current: null },
    lastSpokenTextRef: { current: null },
    onConnectionInterrupted: vi.fn(),
    onSessionJoined: vi.fn(),
    onPlayerMessageSaved: vi.fn(),
    onTurnRejected: vi.fn(),
    onRollRequired: vi.fn(),
    onRollResolved: vi.fn(),
    onTurnDuplicate: vi.fn(),
    ...overrides,
  }
}

function latestSocket() {
  const socket = socketFactory.sockets.at(-1)
  if (!socket) throw new Error('No socket was created')
  return socket
}

describe('useSessionSocket connection lifetime', () => {
  beforeEach(() => {
    socketFactory.io.mockClear()
    socketFactory.sockets.length = 0
  })

  afterEach(() => cleanup())

  it('keeps one connection while callback identities change', () => {
    const { rerender } = renderHook((options: SocketHookOptions) => useSessionSocket(options), {
      initialProps: hookOptions(),
    })
    const onSessionJoined = vi.fn()

    rerender(hookOptions({ loadSessionData: vi.fn(() => Promise.resolve()), pushError: vi.fn() }))
    rerender(hookOptions({ onConnectionInterrupted: vi.fn(), onSessionJoined }))

    expect(socketFactory.io).toHaveBeenCalledTimes(1)
    act(() => {
      latestSocket().fire('connect')
      latestSocket().fire('active_players', [])
    })
    expect(onSessionJoined).toHaveBeenCalledWith({ sessionId: 20, playerId: 30 })
  })

  it('rebuilds the connection only when its identity changes', () => {
    const { rerender } = renderHook((options: SocketHookOptions) => useSessionSocket(options), {
      initialProps: hookOptions(),
    })

    rerender(hookOptions({ selectedSessionId: 21 }))
    rerender(hookOptions({ selectedSessionId: 21, socketReconnectKey: 1 }))

    expect(socketFactory.io).toHaveBeenCalledTimes(3)
  })

  it('does not run the disconnect handler for its own teardown', () => {
    const options = hookOptions()
    const { rerender, unmount } = renderHook((value: SocketHookOptions) => useSessionSocket(value), {
      initialProps: options,
    })
    const firstSocket = latestSocket()
    act(() => firstSocket.fire('connect'))
    vi.mocked(options.setSocketStatus).mockClear()
    vi.mocked(options.setSendPending).mockClear()

    rerender({ ...options, selectedSessionId: 21 })
    unmount()

    expect(firstSocket.listeners.size).toBe(0)
    expect(options.setSocketStatus).not.toHaveBeenCalledWith('offline')
    expect(options.setSocketStatus).not.toHaveBeenCalledWith('reconnecting')
    expect(options.setSendPending).not.toHaveBeenCalled()
    expect(firstSocket.emit).toHaveBeenCalledWith('leave_session', { session_id: 20, player_id: 30 })
    // The composer still hears that its socket was replaced.
    expect(options.onConnectionInterrupted).toHaveBeenCalledTimes(2)
  })

  it('reports a real drop and announces the next join once per connection', () => {
    const options = hookOptions()
    renderHook(() => useSessionSocket(options))
    const socket = latestSocket()

    act(() => {
      socket.fire('connect')
      socket.fire('active_players', [])
      socket.fire('active_players', [])
    })
    expect(options.onSessionJoined).toHaveBeenCalledTimes(1)

    act(() => socket.fire('disconnect', 'transport close'))
    expect(options.onConnectionInterrupted).toHaveBeenCalledTimes(1)
    expect(options.setSocketStatus).toHaveBeenLastCalledWith('reconnecting')

    act(() => {
      socket.fire('connect')
      socket.fire('active_players', [])
    })
    expect(options.setSocketStatus).toHaveBeenCalledWith('rejoining')
    expect(options.onSessionJoined).toHaveBeenCalledTimes(2)
  })

  it('routes an out-of-turn rejection to the composer instead of the error rail', () => {
    const options = hookOptions()
    renderHook(() => useSessionSocket(options))

    act(() => latestSocket().fire('error', {
      error: 'Mira is still choosing. Their turn is skipped in 20 seconds.',
      error_code: 'turn_out_of_order',
      details: { active_player_absent: true, skip_in_seconds: 20 },
    }))

    expect(options.onTurnRejected).toHaveBeenCalledWith({
      message: 'Mira is still choosing. Their turn is skipped in 20 seconds.',
      details: { active_player_absent: true, skip_in_seconds: 20 },
    })
    expect(options.pushError).not.toHaveBeenCalled()
  })

  it('keeps start-of-response roll hints when chunks carry only text', () => {
    let streamingTurn: StreamingTurn | null = null
    const setStreamingTurn = vi.fn((update: unknown) => {
      streamingTurn = typeof update === 'function'
        ? (update as (current: StreamingTurn | null) => StreamingTurn | null)(streamingTurn)
        : update as StreamingTurn | null
    })
    renderHook(() => useSessionSocket(hookOptions({ setStreamingTurn })))

    act(() => {
      latestSocket().fire('dm_response_start', {
        turn_id: 7,
        requires_roll: true,
        rules_hint: { roll_type: 'stealth' },
      })
      latestSocket().fire('dm_chunk', { turn_id: 7, chunk: 'The guard turns.' })
    })

    expect(streamingTurn).toMatchObject({
      text: 'The guard turns.',
      requiresRoll: true,
      rulesHint: { roll_type: 'stealth' },
    })
  })

  it('tells the composer when a new_message echoes a client message id', () => {
    const options = hookOptions()
    renderHook(() => useSessionSocket(options))

    act(() => latestSocket().fire('new_message', {
      message: 'I open the gate.',
      speaker: 'Ember',
      turn_id: 4,
      client_message_id: 'client-4',
    }))

    expect(options.onPlayerMessageSaved).toHaveBeenCalledWith('client-4')
  })
})
