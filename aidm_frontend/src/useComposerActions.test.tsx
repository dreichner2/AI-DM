// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { useRef, useState } from 'react'
import type { Socket } from 'socket.io-client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StreamingTurn, TimelineEntry } from './types'
import type { RollRequiredPayload, RollResolvedPayload } from './gameActions'
import { SEND_PENDING_RECOVERY_MS, useComposerActions } from './useComposerActions'

function useComposerHarness(
  socket: Socket,
  pushError: ReturnType<typeof vi.fn>,
  overrides: Partial<Parameters<typeof useComposerActions>[0]> = {},
) {
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
    ...overrides,
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

  it('keeps the admin passcode in memory only and clears legacy storage on lock', () => {
    sessionStorage.setItem('aidm:adminPasscode', 'legacy-secret')
    const socket = socketWithConnection(true)
    const { result } = renderHook(() => useComposerHarness(socket, vi.fn()))

    expect(result.current.actions.adminPasscode).toBe('')
    expect(sessionStorage.getItem('aidm:adminPasscode')).toBeNull()

    act(() => result.current.actions.setAdminPasscode('current-secret'))
    expect(result.current.actions.adminPasscode).toBe('current-secret')
    expect(sessionStorage.getItem('aidm:adminPasscode')).toBeNull()

    act(() => result.current.actions.toggleAdminTools())
    expect(result.current.actions.adminToolsUnlocked).toBe(true)
    act(() => result.current.actions.toggleAdminTools())
    expect(result.current.actions.adminPasscode).toBe('')
  })

  it('unlocks, marks the local entry failed, and restores text when no terminal event arrives', async () => {
    const socket = socketWithConnection(true)
    const pushError = vi.fn()
    const { result } = renderHook(() => useComposerHarness(socket, pushError))

    act(() => result.current.actions.submitAction('Search the chamber'))

    expect(socket.emit).toHaveBeenCalledWith('send_message', expect.objectContaining({ message: 'Search the chamber' }))
    const originalPayload = vi.mocked(socket.emit).mock.calls[0]?.[1]
    expect(result.current.sendPending).toBe(true)
    expect(result.current.optimisticEntries[0]?.metadata.persistence_status).toBe('pending')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEND_PENDING_RECOVERY_MS)
    })

    expect(result.current.sendPending).toBe(false)
    expect(result.current.optimisticEntries[0]?.metadata.persistence_status).toBe('failed')
    expect(result.current.actions.actionText).toBe('Search the chamber')
    expect(result.current.actions.queuedActionText).toBe('Search the chamber')
    expect(result.current.actions.queuedActionRetryable).toBe(true)
    expect(pushError).toHaveBeenCalledWith('validation', expect.stringContaining('timed out'))

    act(() => result.current.actions.submitAction())

    expect(vi.mocked(socket.emit).mock.calls[1]).toEqual(['send_message', originalPayload])
    expect(result.current.optimisticEntries).toHaveLength(1)
    expect(result.current.optimisticEntries[0]?.metadata.persistence_status).toBe('pending')
    expect(result.current.actions.queuedActionRetryable).toBe(false)

    const clientMessageId = (originalPayload as { client_message_id: string }).client_message_id
    act(() => result.current.actions.handleTurnDuplicate({
      session_id: 5,
      turn_id: 44,
      client_message_id: clientMessageId,
    }))
    expect(result.current.sendPending).toBe(false)
    expect(result.current.optimisticEntries[0]?.metadata).toMatchObject({
      client_message_id: clientMessageId,
      persistence_status: 'received',
      turn_id: 44,
    })
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

  it('reuses the exact client id and payload after a disconnect before confirmation', () => {
    const socket = socketWithConnection(true)
    const { result } = renderHook(() => useComposerHarness(socket, vi.fn()))

    act(() => result.current.actions.submitAction('Cross the bridge'))
    const originalPayload = vi.mocked(socket.emit).mock.calls[0]?.[1]
    act(() => result.current.actions.handleConnectionInterrupted())

    expect(result.current.sendPending).toBe(false)
    expect(result.current.actions.queuedActionRetryable).toBe(true)
    act(() => result.current.actions.submitAction())

    expect(vi.mocked(socket.emit).mock.calls[1]).toEqual(['send_message', originalPayload])
    expect(result.current.optimisticEntries).toHaveLength(1)
  })

  it('does not restore or retry a roll after its authoritative result confirmed persistence', () => {
    const socket = socketWithConnection(true)
    const { result } = renderHook(() => useComposerHarness(socket, vi.fn()))

    act(() => result.current.actions.startDiceRoll())
    const request = vi.mocked(socket.emit).mock.calls[0]?.[1] as { client_message_id: string }
    const resolved: RollResolvedPayload = {
      session_id: 5,
      turn_id: 45,
      player_id: 4,
      client_message_id: request.client_message_id,
      pending_turn_id: null,
      rule_type: 'check',
      die: 'd20',
      mode: 'normal',
      rolls: [11],
      kept: 11,
      modifier: 0,
      total: 11,
      reason: 'check',
      result_visibility: 'hidden_until_landed',
      ability: null,
      proficiency: { bonus: 0, skills: [] },
      modifier_breakdown: { ability_modifier: 0, proficiency_bonus: 0, wound_penalty: 0, total: 0 },
      authoritative: true,
    }
    act(() => {
      result.current.actions.handleRollResolved(resolved)
      result.current.actions.handleConnectionInterrupted()
    })

    expect(result.current.actions.diceRoll?.status).toBe('rolling')
    expect(result.current.actions.actionText).toBe('')
    expect(result.current.actions.queuedActionText).toBe('')
    expect(result.current.actions.queuedActionRetryable).toBe(false)
  })

  it('preserves a blocked action while preparing the authoritative pending roll', () => {
    const socket = socketWithConnection(true)
    const { result } = renderHook(() =>
      useComposerHarness(socket, vi.fn(), {
        abilityOptions: [
          { key: 'dexterity', label: 'DEX', score: '16', modifier: '+3' },
        ],
      }),
    )

    act(() => result.current.actions.submitAction('Open the chest and move on.'))

    const guidance: RollRequiredPayload = {
      sessionId: 5,
      pendingTurnId: 41,
      ruleType: 'ability_check',
      dcHint: null,
      prompt: 'Roll Dexterity to resolve the trap.',
      remainingPlayerIds: [4],
      rollSpec: {
        die: 'd20',
        mode: 'advantage',
        ruleType: 'ability_check',
        reason: 'Dexterity check',
        resultVisibility: 'hidden_until_landed',
        ability: { key: 'dexterity', label: 'DEX' },
      },
    }
    act(() => result.current.actions.handleRollRequired(guidance))

    expect(result.current.sendPending).toBe(false)
    expect(result.current.optimisticEntries).toEqual([])
    expect(result.current.actions.actionText).toBe('Open the chest and move on.')
    expect(result.current.actions.queuedActionText).toBe('Open the chest and move on.')
    expect(result.current.actions.queuedActionRetryable).toBe(false)
    expect(result.current.actions.composerMode).toBe('roll')
    expect(result.current.actions.rollTargetPendingTurnId).toBe('41')
    expect(result.current.actions.selectedDie).toBe('d20')
    expect(result.current.actions.rollMode).toBe('advantage')
    expect(result.current.actions.rollReason).toBe('Dexterity check')
    expect(result.current.actions.selectedAbilityKey).toBe('dexterity')

    act(() => result.current.actions.startDiceRoll())

    const rollPayload = vi.mocked(socket.emit).mock.calls[1]?.[1] as {
      message: string
      action_intent: {
        kind: string
        roll?: { target_pending_turn_id?: number | null; mode?: string; reason?: string }
      }
    }
    expect(rollPayload.message).not.toContain('Open the chest and move on.')
    expect(rollPayload.action_intent).toMatchObject({
      kind: 'roll',
      roll: {
        target_pending_turn_id: 41,
        mode: 'advantage',
        reason: 'Dexterity check',
      },
    })
    expect(result.current.actions.actionText).toBe('Open the chest and move on.')
    expect(result.current.actions.queuedActionText).toBe('Open the chest and move on.')
  })

  it('keeps composer drafts isolated by campaign, session, and character', () => {
    const socket = socketWithConnection(true)
    const { result, rerender } = renderHook(
      ({ sessionId }) =>
        useComposerHarness(socket, vi.fn(), {
          selectedSessionId: sessionId,
      }),
      { initialProps: { sessionId: 5 } },
    )
    act(() => vi.runOnlyPendingTimers())

    act(() => result.current.actions.updateActionText('Listen at the sealed door.'))
    act(() => result.current.actions.preparePendingRoll({
      pendingTurnId: 99,
      ruleType: 'saving_throw',
      dcHint: null,
      prompt: 'Make a saving throw.',
      remainingPlayerIds: [4],
      rollSpec: {
        die: 'd20',
        mode: 'disadvantage',
        ruleType: 'saving_throw',
        reason: 'Wisdom saving throw',
        resultVisibility: 'hidden_until_landed',
        ability: null,
      },
    }))
    expect(result.current.actions.composerMode).toBe('roll')
    expect(result.current.actions.rollTargetPendingTurnId).toBe('99')
    act(() => rerender({ sessionId: 6 }))

    expect(sessionStorage.getItem('aidm:composerDraft:3:5:4')).toBe(
      'Listen at the sealed door.',
    )
    act(() => vi.runOnlyPendingTimers())
    expect(result.current.actions.actionText).toBe('')
    expect(result.current.actions.composerMode).toBe('action')
    expect(result.current.actions.rollTargetPendingTurnId).toBe('')
    expect(result.current.actions.queuedActionText).toBe('')
    expect(result.current.actions.diceRoll).toBeNull()

    act(() => result.current.actions.updateActionText('Search the lower vault.'))
    act(() => rerender({ sessionId: 5 }))

    expect(sessionStorage.getItem('aidm:composerDraft:3:6:4')).toBe('Search the lower vault.')
    act(() => vi.runOnlyPendingTimers())
    expect(result.current.actions.actionText).toBe('Listen at the sealed door.')
  })
})
