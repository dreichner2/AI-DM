// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { useRef, useState } from 'react'
import type { Socket } from 'socket.io-client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StreamingTurn, TimelineEntry } from './types'
import { SEND_PENDING_RECOVERY_MS, useComposerActions } from './useComposerActions'

function useComposerHarness(socket: Socket, pushError: ReturnType<typeof vi.fn>) {
  const socketRef = useRef<Socket | null>(socket)
  const [sendPending, setSendPending] = useState(false)
  const [optimisticEntries, setOptimisticEntries] = useState<TimelineEntry[]>([])
  const [streamingTurn, setStreamingTurn] = useState<StreamingTurn | null>(null)
  const actions = useComposerActions({
    activePlayers: [],
    abilityOptions: [],
    campaign: { campaign_id: 3, world_id: 2, title: 'Test campaign' },
    itemOptions: [],
    pendingRollOptions: [],
    proficiencyBonus: '0',
    sessionState: null,
    selectedCampaignId: 3,
    selectedPlayer: { player_id: 4, character_name: 'Ari' },
    selectedPlayerId: 4,
    selectedSessionId: 5,
    sendPending,
    dmResponseBlocking: Boolean(streamingTurn),
    streamingTurn,
    setOptimisticEntries,
    setSendPending,
    setStreamingTurn,
    socketRef,
    stopTtsAudio: vi.fn(),
    turnControl: { mode: 'free', activePlayerId: null, activePlayerName: null },
    pushError,
  } as unknown as Parameters<typeof useComposerActions>[0])
  return {
    actions,
    optimisticEntries,
    sendPending,
    complete: () => setSendPending(false),
  }
}

function socketWithConnection(connected: boolean | undefined) {
  return {
    connected,
    emit: vi.fn(),
  } as unknown as Socket
}

describe('useComposerActions realtime delivery recovery', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    sessionStorage.clear()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it.each([false, undefined])('does not emit or enter pending state when socket.connected is %s', (connected) => {
    const socket = socketWithConnection(connected)
    const pushError = vi.fn()
    const { result } = renderHook(() => useComposerHarness(socket, pushError))

    act(() => result.current.actions.submitAction('Open the door'))

    expect(socket.emit).not.toHaveBeenCalled()
    expect(result.current.sendPending).toBe(false)
    expect(result.current.optimisticEntries).toEqual([])
    expect(pushError).toHaveBeenCalledWith(
      'validation',
      'Realtime is reconnecting. Try again in a moment.',
    )
  })

  it('unlocks, marks the local entry failed, and restores text when no terminal event arrives', async () => {
    const socket = socketWithConnection(true)
    const pushError = vi.fn()
    const { result } = renderHook(() => useComposerHarness(socket, pushError))

    act(() => result.current.actions.submitAction('Search the chamber'))

    expect(socket.emit).toHaveBeenCalledWith('send_message', expect.objectContaining({ message: 'Search the chamber' }))
    expect(result.current.sendPending).toBe(true)
    expect(result.current.optimisticEntries[0]?.metadata.persistence_status).toBe('pending')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEND_PENDING_RECOVERY_MS)
    })

    expect(result.current.sendPending).toBe(false)
    expect(result.current.optimisticEntries[0]?.metadata.persistence_status).toBe('failed')
    expect(result.current.actions.actionText).toBe('Search the chamber')
    expect(result.current.actions.queuedActionText).toBe('Search the chamber')
    expect(pushError).toHaveBeenCalledWith('validation', expect.stringContaining('timed out'))
  })

  it('cancels recovery after a terminal state clears sendPending', async () => {
    const socket = socketWithConnection(true)
    const pushError = vi.fn()
    const { result } = renderHook(() => useComposerHarness(socket, pushError))

    act(() => result.current.actions.submitAction('Listen at the door'))
    act(() => result.current.complete())
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEND_PENDING_RECOVERY_MS)
    })

    expect(result.current.sendPending).toBe(false)
    expect(result.current.actions.actionText).toBe('')
    expect(pushError).not.toHaveBeenCalledWith('validation', expect.stringContaining('timed out'))
  })
})
