// @vitest-environment jsdom
import { act, cleanup, renderHook } from '@testing-library/react'
import { useRef, useState } from 'react'
import type { Socket } from 'socket.io-client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StreamingTurn, TimelineEntry } from './types'
import type { RollRequiredPayload, RollResolvedPayload } from './gameActions'
import { RECONNECT_RESEND_WINDOW_MS, SEND_PENDING_RECOVERY_MS, useComposerActions } from './useComposerActions'

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
    sessionHydrated: true,
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
    dismissError: vi.fn(),
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

function sendMessageCalls(socket: Socket) {
  return vi.mocked(socket.emit).mock.calls.filter(([event]) => event === 'send_message')
}

type HarnessResult = { current: ReturnType<typeof useComposerHarness> }

// A drop with no rejoin inside the resend window leaves delivery uncertain.
async function interruptWithoutRejoin(result: HarnessResult) {
  act(() => result.current.actions.handleConnectionInterrupted())
  await act(async () => {
    await vi.advanceTimersByTimeAsync(RECONNECT_RESEND_WINDOW_MS)
  })
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

  it('submits the selected second same-name inventory item by id', () => {
    const socket = socketWithConnection(true)
    const { result } = renderHook(() =>
      useComposerHarness(socket, vi.fn(), {
        itemOptions: [
          { id: 'potion-red', name: 'Healing Potion', quantity: '1' },
          { id: 'potion-blue', name: 'Healing Potion', quantity: '1' },
        ],
      }),
    )

    act(() => result.current.actions.applyComposerMode('item'))
    act(() => result.current.actions.setSelectedItemId('potion-blue'))

    expect(result.current.actions.selectedItem?.id).toBe('potion-blue')

    act(() => result.current.actions.submitAction())

    expect(socket.emit).toHaveBeenCalledWith(
      'send_message',
      expect.objectContaining({
        action_intent: expect.objectContaining({
          kind: 'item',
          item: expect.objectContaining({ id: 'potion-blue', name: 'Healing Potion' }),
        }),
      }),
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

  it('reuses the exact client id and payload after a disconnect with no rejoin before confirmation', async () => {
    const socket = socketWithConnection(true)
    const { result } = renderHook(() => useComposerHarness(socket, vi.fn()))

    act(() => result.current.actions.submitAction('Cross the bridge'))
    const originalPayload = vi.mocked(socket.emit).mock.calls[0]?.[1]
    await interruptWithoutRejoin(result)

    expect(result.current.sendPending).toBe(false)
    expect(result.current.actions.submissionReconnecting).toBe(false)
    expect(result.current.actions.queuedActionRetryable).toBe(true)
    act(() => result.current.actions.submitAction())

    expect(vi.mocked(socket.emit).mock.calls[1]).toEqual(['send_message', originalPayload])
    expect(result.current.optimisticEntries).toHaveLength(1)
  })

  it('waits silently through a same-session reconnect and re-emits the identical payload once', () => {
    const socket = socketWithConnection(true)
    const pushError = vi.fn()
    const { result } = renderHook(() => useComposerHarness(socket, pushError))

    act(() => result.current.actions.submitAction('Cross the bridge'))
    const originalPayload = vi.mocked(socket.emit).mock.calls[0]?.[1] as { client_message_id: string }
    act(() => {
      result.current.actions.handleConnectionInterrupted()
      result.current.complete()
    })

    expect(result.current.actions.submissionReconnecting).toBe(true)
    expect(result.current.actions.queuedActionRetryable).toBe(false)
    expect(result.current.actions.queuedActionText).toBe('')
    expect(result.current.actions.actionText).toBe('')
    expect(result.current.optimisticEntries[0]?.metadata.persistence_status).toBe('pending')
    expect(pushError).not.toHaveBeenCalled()

    act(() => result.current.actions.handleSessionJoined({ sessionId: 6, playerId: 4 }))
    expect(sendMessageCalls(socket)).toHaveLength(1)

    act(() => result.current.actions.handleSessionJoined({ sessionId: 5, playerId: 4 }))
    act(() => result.current.actions.handleSessionJoined({ sessionId: 5, playerId: 4 }))

    expect(sendMessageCalls(socket)).toEqual([
      ['send_message', originalPayload],
      ['send_message', originalPayload],
    ])
    expect(result.current.sendPending).toBe(true)
    expect(result.current.actions.submissionReconnecting).toBe(false)
    expect(result.current.optimisticEntries).toHaveLength(1)
    expect(result.current.optimisticEntries[0]?.metadata.persistence_status).toBe('pending')

    act(() => result.current.actions.handleTurnDuplicate({
      session_id: 5,
      turn_id: 44,
      client_message_id: originalPayload.client_message_id,
    }))
    expect(result.current.sendPending).toBe(false)
    expect(result.current.actions.queuedActionRetryable).toBe(false)
  })

  it('releases the composer when the saved log confirms a re-emitted send before its duplicate answer', () => {
    const socket = socketWithConnection(true)
    const { result } = renderHook(() => useComposerHarness(socket, vi.fn()))

    act(() => result.current.actions.submitAction('Cross the bridge'))
    const { client_message_id: clientMessageId } = vi.mocked(socket.emit).mock.calls[0]?.[1] as {
      client_message_id: string
    }
    act(() => {
      result.current.actions.handleConnectionInterrupted()
      result.current.complete()
    })
    act(() => result.current.actions.handleSessionJoined({ sessionId: 5, playerId: 4 }))
    act(() => result.current.actions.handleSavedMessages([clientMessageId]))
    expect(result.current.sendPending).toBe(true)

    act(() => result.current.actions.handleTurnDuplicate({
      session_id: 5,
      turn_id: 44,
      client_message_id: clientMessageId,
    }))

    expect(result.current.sendPending).toBe(false)
  })

  it('falls back to uncertain delivery when the automatic re-emit is itself interrupted', () => {
    const socket = socketWithConnection(true)
    const pushError = vi.fn()
    const { result } = renderHook(() => useComposerHarness(socket, pushError))

    act(() => result.current.actions.submitAction('Cross the bridge'))
    act(() => result.current.actions.handleConnectionInterrupted())
    act(() => result.current.actions.handleSessionJoined({ sessionId: 5, playerId: 4 }))
    act(() => result.current.actions.handleConnectionInterrupted())

    expect(sendMessageCalls(socket)).toHaveLength(2)
    expect(result.current.actions.submissionReconnecting).toBe(false)
    expect(result.current.actions.queuedActionRetryable).toBe(true)
    expect(result.current.actions.queuedActionText).toBe('Cross the bridge')
    expect(pushError).toHaveBeenCalledWith('validation', expect.stringContaining('Realtime disconnected'))
  })

  it('marks a pending send uncertain at once when the socket closes for another player', () => {
    const socket = socketWithConnection(true)
    const { result, rerender } = renderHook(
      ({ playerId }) => useComposerHarness(socket, vi.fn(), { selectedPlayerId: playerId }),
      { initialProps: { playerId: 4 } },
    )

    act(() => result.current.actions.submitAction('Cross the bridge'))
    rerender({ playerId: 7 })
    act(() => result.current.actions.handleConnectionInterrupted())

    expect(result.current.actions.submissionReconnecting).toBe(false)
    expect(result.current.actions.queuedActionRetryable).toBe(true)
  })

  it('clears an uncertain delivery and its error once the saved log contains its client id', async () => {
    const socket = socketWithConnection(true)
    const pushError = vi.fn()
    const dismissError = vi.fn()
    const { result } = renderHook(() => useComposerHarness(socket, pushError, { dismissError }))

    act(() => result.current.actions.submitAction('Cross the bridge'))
    const { client_message_id: clientMessageId } = vi.mocked(socket.emit).mock.calls[0]?.[1] as {
      client_message_id: string
    }
    await interruptWithoutRejoin(result)
    expect(result.current.actions.queuedActionRetryable).toBe(true)
    const uncertainError = pushError.mock.calls.find(([category]) => category === 'validation')?.[1]

    act(() => result.current.actions.handleSavedMessages(['another-id']))
    expect(result.current.actions.queuedActionRetryable).toBe(true)

    act(() => result.current.actions.handleSavedMessages([clientMessageId]))

    expect(result.current.actions.queuedActionRetryable).toBe(false)
    expect(result.current.actions.queuedActionText).toBe('')
    expect(result.current.actions.actionText).toBe('')
    expect(dismissError).toHaveBeenCalledWith('validation', uncertainError)
  })

  it('treats a pending send echoed as saved as confirmed even if the socket then drops', () => {
    const socket = socketWithConnection(true)
    const { result } = renderHook(() => useComposerHarness(socket, vi.fn()))

    act(() => result.current.actions.submitAction('Cross the bridge'))
    const { client_message_id: clientMessageId } = vi.mocked(socket.emit).mock.calls[0]?.[1] as {
      client_message_id: string
    }
    act(() => result.current.actions.handleSavedMessages([clientMessageId]))
    act(() => result.current.actions.handleConnectionInterrupted())

    expect(result.current.actions.submissionReconnecting).toBe(false)
    expect(result.current.actions.queuedActionRetryable).toBe(false)
  })

  it('sends the opening as an internal request that never becomes a player draft', async () => {
    const socket = socketWithConnection(true)
    const pushError = vi.fn()
    const { result } = renderHook(() => useComposerHarness(socket, pushError))
    act(() => vi.runOnlyPendingTimers())
    act(() => result.current.actions.updateActionText('My own half-written plan'))

    act(() => result.current.actions.startOpening('Please narrate the opening scene for this campaign.'))

    const openingPayload = vi.mocked(socket.emit).mock.calls.find(([event]) => event === 'send_message')?.[1]
    expect(openingPayload).toMatchObject({ message_kind: 'opening' })
    expect(result.current.optimisticEntries[0]?.metadata.message_kind).toBe('opening')
    expect(result.current.actions.actionText).toBe('My own half-written plan')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(SEND_PENDING_RECOVERY_MS)
    })

    expect(result.current.actions.openingDelayed).toBe(true)
    expect(result.current.actions.queuedActionRetryable).toBe(false)
    expect(result.current.actions.queuedActionText).toBe('')
    expect(result.current.actions.actionText).toBe('My own half-written plan')
    expect(pushError).not.toHaveBeenCalledWith('validation', expect.anything())

    act(() => result.current.actions.startOpening('Please narrate the opening scene for this campaign.'))

    expect(sendMessageCalls(socket)).toEqual([
      ['send_message', openingPayload],
      ['send_message', openingPayload],
    ])
    expect(result.current.actions.openingDelayed).toBe(false)
  })

  it('keeps the typed draft and shows the server reason when an out-of-turn action is rejected', () => {
    const socket = socketWithConnection(true)
    const pushError = vi.fn()
    const { result } = renderHook(() => useComposerHarness(socket, pushError))
    act(() => vi.runOnlyPendingTimers())

    act(() => result.current.actions.updateActionText('  I leap onto the cart  '))
    act(() => result.current.actions.submitAction())
    expect(result.current.actions.actionText).toBe('')

    act(() => result.current.actions.handleTurnRejected({
      message: 'Mira is still choosing. Their turn is skipped in 20 seconds.',
      details: { active_player_absent: true, skip_in_seconds: 20 },
    }))

    expect(result.current.optimisticEntries).toEqual([])
    expect(result.current.sendPending).toBe(false)
    expect(result.current.actions.actionText).toBe('  I leap onto the cart  ')
    expect(result.current.actions.turnRejectionMessage).toBe(
      'Mira is still choosing. Their turn is skipped in 20 seconds.',
    )
    expect(pushError).not.toHaveBeenCalled()
  })

  it('refuses to send until the selected session is hydrated', () => {
    const socket = socketWithConnection(true)
    const { result } = renderHook(() => useComposerHarness(socket, vi.fn(), { sessionHydrated: false }))

    act(() => result.current.actions.submitAction('Open the door'))

    expect(socket.emit).not.toHaveBeenCalled()
    expect(result.current.sendPending).toBe(false)
  })

  it('blocks a modified or new submission while delivery is uncertain and keeps safe retry available', async () => {
    const socket = socketWithConnection(true)
    const pushError = vi.fn()
    const { result } = renderHook(() => useComposerHarness(socket, pushError))

    act(() => result.current.actions.submitAction('Cross the bridge'))
    const originalPayload = vi.mocked(socket.emit).mock.calls[0]?.[1]
    await interruptWithoutRejoin(result)
    act(() => result.current.actions.updateActionText('Inspect the river instead'))
    act(() => result.current.actions.submitAction())

    expect(vi.mocked(socket.emit).mock.calls.filter(([event]) => event === 'send_message')).toHaveLength(1)
    expect(result.current.actions.actionText).toBe('Inspect the river instead')
    expect(result.current.actions.queuedActionText).toBe('Cross the bridge')
    expect(result.current.actions.queuedActionRetryable).toBe(true)
    expect(pushError).toHaveBeenCalledWith(
      'validation',
      expect.stringContaining('Delivery of your previous action is uncertain'),
    )

    act(() => result.current.actions.retryRecoverableSubmission())
    expect(vi.mocked(socket.emit).mock.calls.filter(([event]) => event === 'send_message')[1])
      .toEqual(['send_message', originalPayload])
  })

  it('preserves an edited draft when the player explicitly discards an uncertain delivery', async () => {
    const socket = socketWithConnection(true)
    const { result } = renderHook(() => useComposerHarness(socket, vi.fn()))

    act(() => result.current.actions.submitAction('Open the hatch'))
    await interruptWithoutRejoin(result)
    act(() => result.current.actions.updateActionText('Listen at the hatch'))
    act(() => result.current.actions.clearQueuedAction())

    expect(result.current.actions.actionText).toBe('Listen at the hatch')
    expect(result.current.actions.queuedActionText).toBe('')
    expect(result.current.actions.queuedActionRetryable).toBe(false)

    act(() => result.current.actions.submitAction())
    const sendMessageCalls = vi.mocked(socket.emit).mock.calls.filter(([event]) => event === 'send_message')
    expect(sendMessageCalls).toHaveLength(2)
    expect(sendMessageCalls[1]).toEqual([
      'send_message',
      expect.objectContaining({ message: 'Listen at the hatch' }),
    ])
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

  it('does not erase a roll started before deferred draft hydration completes', async () => {
    const socket = socketWithConnection(true)
    const { result } = renderHook(() => useComposerHarness(socket, vi.fn()))

    act(() => result.current.actions.startDiceRoll())
    expect(result.current.actions.diceRoll?.status).toBe('requesting')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0)
    })

    expect(result.current.actions.diceRoll?.status).toBe('requesting')
    expect(socket.emit).toHaveBeenCalledWith(
      'send_message',
      expect.objectContaining({
        action_intent: expect.objectContaining({ kind: 'roll' }),
      }),
    )
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
