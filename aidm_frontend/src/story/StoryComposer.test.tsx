// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActionComposerProps } from '../ActionComposer'
import type { SessionBoardProps } from '../SessionBoard'
import { testBoard, testCombat, testRollNotice, testWorld } from './dockTestFixtures'
import { StoryComposer } from './StoryComposer'
import type { StoryComposerProps } from './types'

function renderComposer({
  board = {},
  composer = {},
  chrome = {},
}: {
  board?: Partial<SessionBoardProps>
  composer?: Partial<ActionComposerProps>
  chrome?: Partial<StoryComposerProps['chrome']>
} = {}) {
  const props: StoryComposerProps = {
    board: testBoard(board, composer),
    chrome: { connection: 'ready', canUseOperatorTools: false, endedChapter: null, ...chrome },
    onOpenDrawer: vi.fn(),
  }
  return { ...render(<StoryComposer {...props} />), props }
}

function statusLine(container: HTMLElement) {
  return container.querySelector('.story-dock-status')?.textContent ?? ''
}

beforeEach(() => window.localStorage.clear())
afterEach(() => cleanup())

describe('StoryComposer', () => {
  it('names the input "Your Action" and the send button "Send"', () => {
    renderComposer({ composer: { actionText: 'I open the door.' } })

    expect(screen.getByLabelText('Your Action').tagName).toBe('TEXTAREA')
    expect(screen.getByLabelText('Your Action')).toHaveAttribute('id', 'action-input')
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
  })

  it('sends on Enter and Cmd/Ctrl+Enter through submitAction, but not on Shift+Enter or mid-composition', () => {
    const { props } = renderComposer({ composer: { actionText: 'I open the door.' } })
    const submitAction = props.board.actionComposerProps.submitAction
    const input = screen.getByLabelText('Your Action')

    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 })
    expect(submitAction).not.toHaveBeenCalled()

    fireEvent.keyDown(input, { key: 'Enter' })
    fireEvent.keyDown(input, { key: 'Enter', metaKey: true })
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true, shiftKey: true })
    expect(submitAction).toHaveBeenCalledTimes(3)
    expect(submitAction).toHaveBeenNthCalledWith(1)
  })

  it('keeps Send disabled for an empty draft or while the Dungeon Master is working', () => {
    renderComposer({ composer: { actionText: '   ' } })
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    cleanup()

    renderComposer({ board: { streamingTurnActive: true }, composer: { actionText: 'Wait.' } })
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('opens the action tray with "/" on an empty input and closes it with Escape', () => {
    renderComposer()
    const input = screen.getByLabelText('Your Action')

    fireEvent.keyDown(input, { key: '/' })
    const tray = screen.getByRole('dialog', { name: 'Actions' })
    expect(screen.getByRole('button', { name: 'Actions' })).toHaveAttribute('aria-expanded', 'true')
    expect(tray).toContainElement(document.activeElement as HTMLElement)

    fireEvent.keyDown(tray, { key: 'Escape' })
    expect(screen.queryByRole('dialog', { name: 'Actions' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Actions' })).toHaveFocus()
  })

  it('does not open the tray when "/" is typed into a draft', () => {
    renderComposer({ composer: { actionText: 'and/or' } })
    fireEvent.keyDown(screen.getByLabelText('Your Action'), { key: '/' })
    expect(screen.queryByRole('dialog', { name: 'Actions' })).not.toBeInTheDocument()
  })

  it('shows exactly one status line, in priority order', () => {
    const someoneElsesRoll = testRollNotice({
      waitingOnLabel: 'Brannoc',
      waitingPlayerIds: [31],
      isWaitingOnSelectedPlayer: false,
    })
    const recoveryGate: SessionBoardProps['turnRecoveryGate'] = {
      status: 'required',
      reason: 'post_dm_state_application_failed',
      turnId: 9,
      narrationSaved: true,
      mechanicsApplied: false,
      mechanicsStatus: 'none',
      preDmMechanicsApplied: false,
      preDmAppliedChangeCount: 0,
      postDmMechanicsApplied: false,
      createdAt: '2026-11-10T00:00:00Z',
    }
    const cases: Array<{
      board: Partial<SessionBoardProps>
      composer: Partial<ActionComposerProps>
      connection: StoryComposerProps['chrome']['connection']
      expected: string
    }> = [
      {
        board: { turnRecoveryGate: recoveryGate, operationalError: 'Lost sync', pendingRollNotice: someoneElsesRoll, sendPending: true },
        composer: { queuedActionRetryable: true, selectedPlayerHasTurn: false },
        connection: 'reconnecting',
        expected: 'The Dungeon Master is reviewing the last turn.',
      },
      {
        board: { operationalError: 'Lost sync', pendingRollNotice: someoneElsesRoll, sendPending: true },
        composer: { queuedActionRetryable: true, selectedPlayerHasTurn: false },
        connection: 'reconnecting',
        expected: 'We could not confirm that reached the table.Send againDiscard',
      },
      {
        board: { operationalError: 'Lost sync', pendingRollNotice: someoneElsesRoll, sendPending: true },
        composer: { selectedPlayerHasTurn: false },
        connection: 'reconnecting',
        expected: 'Reconnecting…',
      },
      {
        board: { operationalError: 'Lost sync', pendingRollNotice: someoneElsesRoll, sendPending: true },
        composer: { selectedPlayerHasTurn: false },
        connection: 'ready',
        expected: 'Lost syncRefresh and reconnect',
      },
      {
        board: { pendingRollNotice: someoneElsesRoll, sendPending: true },
        composer: { selectedPlayerHasTurn: false },
        connection: 'ready',
        expected: 'Waiting for Brannoc to roll',
      },
      {
        board: { sendPending: true },
        composer: { selectedPlayerHasTurn: false },
        connection: 'ready',
        expected: 'The Dungeon Master is narrating',
      },
      {
        board: {},
        composer: { selectedPlayerHasTurn: false, turnControlStatusLabel: 'Spotlight: Brannoc' },
        connection: 'ready',
        expected: 'Spotlight: Brannoc',
      },
      { board: {}, composer: {}, connection: 'ready', expected: '' },
    ]

    for (const { board, composer, connection, expected } of cases) {
      const { container } = renderComposer({ board, composer, chrome: { connection } })
      expect(container.querySelectorAll('.story-dock-status').length).toBeLessThanOrEqual(1)
      expect(statusLine(container)).toBe(expected)
      cleanup()
    }
  })

  it('wires the recovery, connection and operational-error lines to their actions', () => {
    const { props } = renderComposer({
      board: { operationalError: 'Lost sync' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Refresh and reconnect' }))
    expect(props.board.onRecoverOperationalError).toHaveBeenCalledTimes(1)
    cleanup()

    renderComposer({ chrome: { connection: 'offline' }, composer: { actionText: 'Hello' } })
    expect(screen.getByText('Offline')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
  })

  it('offers Send again and Discard when delivery is uncertain', () => {
    const { props } = renderComposer({
      composer: { queuedActionRetryable: true, queuedActionText: 'I step onto the causeway.', actionText: 'I step onto the causeway.' },
    })
    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('We could not confirm that reached the table.')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()

    fireEvent.click(within(alert).getByRole('button', { name: 'Send again' }))
    fireEvent.click(within(alert).getByRole('button', { name: 'Discard' }))
    expect(props.board.actionComposerProps.retryRecoverableSubmission).toHaveBeenCalledTimes(1)
    expect(props.board.actionComposerProps.clearQueuedAction).toHaveBeenCalledTimes(1)
  })

  it('disables the input while the turn is under review and without a character', () => {
    renderComposer({
      board: {
        turnRecoveryGate: {
          status: 'required',
          reason: 'post_dm_state_application_failed',
          turnId: 9,
          narrationSaved: true,
          mechanicsApplied: false,
          mechanicsStatus: 'none',
          preDmMechanicsApplied: false,
          preDmAppliedChangeCount: 0,
          postDmMechanicsApplied: false,
          createdAt: '2026-11-10T00:00:00Z',
        },
      },
    })
    expect(screen.getByLabelText('Your Action')).toBeDisabled()
    cleanup()

    renderComposer({ composer: { selectedPlayerId: null, selectedCharacterName: null } })
    expect(screen.getByLabelText('Your Action')).toBeDisabled()
    expect(screen.getByLabelText('Your Action')).toHaveAttribute('placeholder', 'Choose a character first')
  })

  it('renders only the continue action for an ended chapter', () => {
    const onContinue = vi.fn()
    const { container } = renderComposer({ chrome: { endedChapter: { onContinue } } })

    expect(screen.getByText('This chapter has closed.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Your Action')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Actions' })).not.toBeInTheDocument()
    expect(container.querySelectorAll('button')).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Continue the adventure' }))
    expect(onContinue).toHaveBeenCalledTimes(1)
  })

  it('uses the mode placeholder and shows the active mode on the actions button', () => {
    renderComposer({ composer: { composerMode: 'ooc' } })
    expect(screen.getByLabelText('Your Action')).toHaveAttribute('placeholder', 'Say it out of character…')
    expect(screen.getByRole('button', { name: 'Actions' })).toHaveAttribute('data-mode', 'ooc')
  })

  it('fills the input for people and items, and submits typed travel and object actions', () => {
    const { props } = renderComposer()
    const composer = props.board.actionComposerProps
    const suggestions = screen.getByRole('group', { name: 'Suggestions' })

    fireEvent.click(within(suggestions).getByRole('button', { name: 'Talk to Marta Vell' }))
    expect(composer.updateActionText).toHaveBeenCalledWith('I talk to Marta Vell.')
    expect(screen.getByLabelText('Your Action')).toHaveFocus()

    fireEvent.click(within(suggestions).getByRole('button', { name: 'Examine caravan ledger' }))
    expect(composer.updateActionText).toHaveBeenCalledWith('I examine the caravan ledger.')

    fireEvent.click(within(suggestions).getByRole('button', { name: 'Go to North causeway' }))
    expect(composer.submitAction).toHaveBeenLastCalledWith('The party travels to North causeway.', {
      kind: 'travel',
      source: 'scene_panel',
      text: 'The party travels to North causeway.',
      client_message_id: expect.any(String),
      location: { id: 'causeway', name: 'North causeway' },
    })

    fireEvent.click(within(suggestions).getByRole('button', { name: 'Open Lead wagon' }))
    expect(composer.submitAction).toHaveBeenLastCalledWith('Ysolde attempts to open Lead wagon.', {
      kind: 'object',
      source: 'scene_panel',
      text: 'Ysolde attempts to open Lead wagon.',
      client_message_id: expect.any(String),
      object: { id: 'lead-wagon', action: 'open', revision: 4 },
    })
  })

  it('returns to plain text before filling a suggestion from another mode', () => {
    const { props } = renderComposer({ composer: { composerMode: 'ooc' } })
    fireEvent.click(screen.getByRole('button', { name: 'Talk to Marta Vell' }))
    expect(props.board.actionComposerProps.applyComposerMode).toHaveBeenCalledWith('action')
  })

  it('hides suggestions when the viewer cannot act or something is pending', () => {
    renderComposer({ composer: { selectedPlayerHasTurn: false } })
    expect(screen.queryByRole('group', { name: 'Suggestions' })).not.toBeInTheDocument()
    cleanup()

    renderComposer({ board: { sendPending: true } })
    expect(screen.queryByRole('group', { name: 'Suggestions' })).not.toBeInTheDocument()
    cleanup()

    renderComposer({ board: { pendingRollNotice: testRollNotice({ isWaitingOnSelectedPlayer: false }) } })
    expect(screen.queryByRole('group', { name: 'Suggestions' })).not.toBeInTheDocument()
  })

  it('replaces suggestions with the combat ribbon, and everything below the status with the roll prompt', () => {
    const combat = testCombat()
    renderComposer({ board: { worldState: testWorld({ combat }) } })
    expect(screen.getByRole('region', { name: 'Combat, round 2' })).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Suggestions' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Your Action')).toBeInTheDocument()
    cleanup()

    renderComposer({ board: { worldState: testWorld({ combat }), pendingRollNotice: testRollNotice() } })
    expect(screen.getByRole('region', { name: 'Pending roll' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Combat, round 2' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Your Action')).not.toBeInTheDocument()
  })

  it('returns a finished roll to plain text mode', () => {
    const { props } = renderComposer({ composer: { composerMode: 'roll' } })
    expect(props.board.actionComposerProps.applyComposerMode).toHaveBeenCalledWith('action')
    cleanup()

    const prompt = renderComposer({ board: { pendingRollNotice: testRollNotice() }, composer: { composerMode: 'roll' } })
    expect(prompt.props.board.actionComposerProps.applyComposerMode).not.toHaveBeenCalled()
  })

  it('relocks admin tools for someone who is no longer an operator', () => {
    const { props } = renderComposer({ composer: { canUseOperatorTools: false, adminToolsUnlocked: true } })
    expect(props.board.actionComposerProps.toggleAdminTools).toHaveBeenCalledTimes(1)
  })

  it('tells a dying viewer they are down and keeps the input closed until they are back up', () => {
    const fixture = testBoard()
    const sessionState = fixture.sessionState
    if (!sessionState) throw new Error('fixture has a session state')
    const downed = {
      ...sessionState,
      state_snapshot: {
        combat: {
          status: 'active',
          participants: [{ id: 'player_30', team: 'player', conditions: ['unconscious', 'dying'], deathSaves: { successes: 0, failures: 1 } }],
        },
      },
    }
    const { container } = renderComposer({
      board: { sessionState: downed, worldState: testWorld({ combat: testCombat() }) },
      composer: { actionText: 'I crawl.' },
    })

    expect(statusLine(container)).toBe('You are down. Your companions can still reach you.')
    expect(screen.getByLabelText('Your Action')).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Actions' })).toBeDisabled()
    cleanup()

    const stable = { ...downed, state_snapshot: { playerCharacters: [{ playerId: 30, health: { conditions: ['unconscious', 'stable'] } }] } }
    const { container: stableDock } = renderComposer({ board: { sessionState: stable }, composer: { actionText: 'I wait.' } })
    expect(statusLine(stableDock)).not.toContain('You are down')
    expect(screen.getByLabelText('Your Action')).toBeEnabled()
  })

  it('shows the keyboard hint until three actions have been sent from this browser', () => {
    window.localStorage.setItem('aidm:storyHintsSeen', '2')
    const { props } = renderComposer({ composer: { actionText: 'I wait.' } })
    expect(screen.getByText(/Enter to send/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(props.board.actionComposerProps.submitAction).toHaveBeenCalledTimes(1)
    expect(window.localStorage.getItem('aidm:storyHintsSeen')).toBe('3')
    expect(screen.queryByText(/Enter to send/)).not.toBeInTheDocument()
  })
})
