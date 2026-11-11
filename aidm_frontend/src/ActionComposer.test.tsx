// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef } from 'react'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ActionComposer, type ActionComposerProps } from './ActionComposer'

afterEach(cleanup)

function composerProps(overrides: Partial<ActionComposerProps> = {}): ActionComposerProps {
  return {
    actionInputRef: createRef<HTMLTextAreaElement>(),
    actionText: 'I open the gate.',
    adminPasscode: '',
    adminToolsUnlocked: false,
    canUseOperatorTools: true,
    setActionText: vi.fn(),
    updateActionText: vi.fn(),
    setAdminPasscode: vi.fn(),
    selectedCharacterName: 'Ember',
    selectedPlayerId: 30,
    activePlayers: [],
    composerMode: 'action',
    selectedDie: 'd20',
    sendPending: false,
    turnControl: { mode: 'free', source: 'auto', activePlayerId: null, activePlayerName: null },
    turnControlStatusLabel: 'Free play',
    selectedPlayerHasTurn: true,
    queuedActionText: '',
    retryRecoverableSubmission: vi.fn(() => true),
    clearQueuedAction: vi.fn(),
    updateTurnControl: vi.fn(),
    ttsEnabled: false,
    ttsStatusClassName: 'idle',
    ttsStatusLabel: 'Off',
    ttsLatencyLabel: '',
    canStopTts: false,
    stopTtsAudio: vi.fn(),
    submitAction: vi.fn(() => true),
    toggleAdminTools: vi.fn(),
    startDiceRoll: vi.fn(),
    diceRoll: null,
    markDiceRollPlayed: vi.fn(),
    closeDiceRoll: vi.fn(),
    retryDiceRoll: vi.fn(() => true),
    applyComposerMode: vi.fn(),
    updateSelectedDie: vi.fn(),
    rollMode: 'normal',
    setRollMode: vi.fn(),
    rollReason: '',
    setRollReason: vi.fn(),
    pendingRollOptions: [],
    rollTargetPendingTurnId: '',
    setRollTargetPendingTurnId: vi.fn(),
    spellName: '',
    selectedAbility: null,
    selectedAbilityKey: '',
    abilityOptions: [],
    updateRollAbilityKey: vi.fn(),
    updateSpellName: vi.fn(),
    interactionTargets: [],
    selectedInteractionTarget: null,
    selectedInteractionTargetId: '',
    selectedInteractionType: 'speak_to',
    setSelectedInteractionTargetId: vi.fn(),
    setSelectedInteractionType: vi.fn(),
    selectedInventoryAction: 'use',
    selectedItem: null,
    itemDraftName: '',
    itemQuantity: '1',
    itemCostGold: '',
    itemOptions: [],
    setSelectedItemId: vi.fn(),
    setItemQuantity: vi.fn(),
    updateSelectedInventoryAction: vi.fn(),
    updateItemDraftName: vi.fn(),
    updateItemCostGold: vi.fn(),
    ...overrides,
  }
}

describe('ActionComposer status and controls', () => {
  it('disables Send while the selected session is still loading', () => {
    render(<ActionComposer {...composerProps({ sessionHydrated: false })} />)

    const send = screen.getByRole('button', { name: 'Send' })
    expect(send).toBeDisabled()
    expect(send).toHaveAccessibleDescription('Loading this session…')
  })

  it('shows a quiet reconnecting status instead of an uncertain delivery strip', () => {
    render(<ActionComposer {...composerProps({ submissionReconnecting: true, sendPending: true })} />)

    expect(screen.getByText('Reconnecting…').closest('[role="status"]')).toBeInTheDocument()
    expect(screen.queryByText('Delivery uncertain')).not.toBeInTheDocument()
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('offers only a retry for a delayed opening', () => {
    const retryRecoverableSubmission = vi.fn(() => true)
    render(<ActionComposer {...composerProps({ openingDelayed: true, retryRecoverableSubmission })} />)

    expect(screen.getByText('The opening is taking longer than expected')).toBeInTheDocument()
    expect(screen.queryByText('Delivery uncertain')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Try again' }))
    expect(retryRecoverableSubmission).toHaveBeenCalledOnce()
  })

  it('shows the server reason for a rejected out-of-turn action next to the composer', () => {
    render(
      <ActionComposer
        {...composerProps({ turnRejectionMessage: 'Mira is still choosing. Their turn is skipped in 20 seconds.' })}
      />,
    )

    expect(screen.getByText('Mira is still choosing. Their turn is skipped in 20 seconds.')).toBeInTheDocument()
  })

  it('toggles director controls from an explicit operator button and from the label gesture', () => {
    const toggleAdminTools = vi.fn()
    const { rerender } = render(<ActionComposer {...composerProps({ toggleAdminTools })} />)

    const directorControls = screen.getByRole('button', { name: 'Director controls' })
    expect(directorControls).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(directorControls)
    expect(toggleAdminTools).toHaveBeenCalledTimes(1)

    const actionLabel = screen.getByText(/Your Action/)
    for (let index = 0; index < 5; index += 1) fireEvent.click(actionLabel)
    expect(toggleAdminTools).toHaveBeenCalledTimes(2)

    rerender(<ActionComposer {...composerProps({ toggleAdminTools, adminToolsUnlocked: true })} />)
    expect(screen.getByRole('button', { name: 'Director controls' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('group', { name: 'Turn mode override' })).toBeInTheDocument()
  })

  it('hides director controls from players', () => {
    render(<ActionComposer {...composerProps({ canUseOperatorTools: false })} />)

    expect(screen.queryByRole('button', { name: 'Director controls' })).not.toBeInTheDocument()
  })
})
