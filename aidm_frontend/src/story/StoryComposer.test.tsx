// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActionComposerProps } from '../ActionComposer'
import type { SessionBoardProps } from '../SessionBoard'
import { testBoard, testCombat, testControls, testRollNotice, testWorld, VIEWER_ID } from './dockTestFixtures'
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

  it('fills the input for people, and submits typed pickups, object and travel actions as their labels', () => {
    const { props } = renderComposer({
      board: {
        worldState: testWorld({
          sceneItems: [{ id: 'key', name: 'Caravan Strongbox Key', quantity: 1, type: 'key', takeLabel: 'Take the Caravan Strongbox Key' }],
        }),
        gameplayControls: testControls({
          interactables: [{
            id: 'ledger',
            name: 'Caravan Ledger',
            kind: 'object',
            description: '',
            revision: 3,
            states: [],
            available: true,
            reason: '',
            offered: true,
            actions: [{ id: 'inspect', label: 'Read the caravan ledger', available: true, reason: '', phrase: true, authored: true }],
          }],
        }),
      },
    })
    const composer = props.board.actionComposerProps
    const suggestions = screen.getByRole('group', { name: 'Suggestions' })
    expect(within(suggestions).getAllByRole('button').map((chip) => chip.textContent)).toEqual([
      'Take the Caravan Strongbox Key',
      'Read the caravan ledger',
      'Talk to Marta Vell',
      'Go to North causeway',
    ])

    fireEvent.click(within(suggestions).getByRole('button', { name: 'Talk to Marta Vell' }))
    expect(composer.updateActionText).toHaveBeenCalledWith('I talk to Marta Vell.')
    expect(screen.getByLabelText('Your Action')).toHaveFocus()

    fireEvent.click(within(suggestions).getByRole('button', { name: 'Take the Caravan Strongbox Key' }))
    expect(composer.submitAction).toHaveBeenLastCalledWith('Take the Caravan Strongbox Key.', {
      kind: 'item',
      source: 'scene_panel',
      text: 'Take the Caravan Strongbox Key.',
      client_message_id: expect.any(String),
      inventory_action: 'pick_up',
      item: { id: 'key', name: 'Caravan Strongbox Key', quantity: 1 },
    })

    fireEvent.click(within(suggestions).getByRole('button', { name: 'Read the caravan ledger' }))
    expect(composer.submitAction).toHaveBeenLastCalledWith('Read the caravan ledger.', {
      kind: 'object',
      source: 'scene_panel',
      text: 'Read the caravan ledger.',
      client_message_id: expect.any(String),
      object: { id: 'ledger', action: 'inspect', revision: 3 },
    })

    fireEvent.click(within(suggestions).getByRole('button', { name: 'Go to North causeway' }))
    expect(composer.submitAction).toHaveBeenLastCalledWith('The party travels to North causeway.', {
      kind: 'travel',
      source: 'scene_panel',
      text: 'The party travels to North causeway.',
      client_message_id: expect.any(String),
      location: { id: 'causeway', name: 'North causeway' },
    })
  })

  it('offers only Examine for an object from an older server, sent as its label', () => {
    const { props } = renderComposer()
    fireEvent.click(screen.getByRole('button', { name: 'Examine Lead wagon' }))
    expect(props.board.actionComposerProps.submitAction).toHaveBeenLastCalledWith('Examine Lead wagon.', expect.objectContaining({
      kind: 'object',
      object: { id: 'lead-wagon', action: 'inspect', revision: 4 },
    }))
    expect(screen.queryByRole('button', { name: /Open Lead wagon|Search Lead wagon/ })).not.toBeInTheDocument()
  })

  it('keeps the suggestions on one line: chips with no room wait behind More… instead of being cut off', () => {
    let rowWidth = 400
    // Chips measure 8px a character; jsdom lays nothing out by itself.
    const layout = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const width = this.classList.contains('story-suggestions')
        ? rowWidth
        : this.classList.contains('story-chip') ? (this.textContent ?? '').length * 8 : 0
      return { x: 0, y: 0, top: 0, left: 0, right: width, bottom: 0, width, height: 0, toJSON: () => ({}) }
    })
    try {
      const { props, rerender } = renderComposer()
      const visible = () =>
        within(screen.getByRole('group', { name: 'Suggestions' })).getAllByRole('button').map((chip) => chip.textContent)
      // 144 + 144 + 160 px of chips do not fit 400: the last waits behind More… (40 px).
      expect(visible()).toEqual(['Examine Lead wagon', 'Talk to Marta Vell', 'More…'])
      fireEvent.click(screen.getByRole('button', { name: 'More…' }))
      expect(screen.getByRole('region', { name: 'Scene' })).toBeInTheDocument()
      fireEvent.keyDown(screen.getByRole('dialog', { name: 'Actions' }), { key: 'Escape' })

      rowWidth = 460
      rerender(<StoryComposer {...props} />)
      expect(visible()).toEqual(['Examine Lead wagon', 'Talk to Marta Vell', 'Go to North causeway'])

      rowWidth = 100
      rerender(<StoryComposer {...props} />)
      expect(visible()).toEqual(['More…'])
    } finally {
      layout.mockRestore()
    }
  })

  it('opens the tray on Scene from More…, and on the Combat list during a fight', () => {
    const many = ['Mara', 'Tovin', 'Osric'].map((name) => ({ id: name, name, race: '', role: '', disposition: '', status: 'present' }))
    renderComposer({ board: { worldState: testWorld({ presentNpcs: many }) } })
    fireEvent.click(screen.getByRole('button', { name: 'More…' }))
    expect(screen.getByRole('region', { name: 'Scene' })).toBeInTheDocument()
    cleanup()

    renderComposer({ board: { worldState: testWorld({ combat: testCombat() }) } })
    fireEvent.click(screen.getByRole('button', { name: 'More…' }))
    expect(screen.getByRole('region', { name: 'Combat' })).toBeInTheDocument()
    cleanup()

    renderComposer({ board: { worldState: testWorld({ combat: testCombat() }) } })
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
    expect(screen.getByRole('region', { name: 'Combat' })).toBeInTheDocument()
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

  it('closes everything but out-of-character speech to a hero who is down: dying, stable or at 0 HP', () => {
    const fixture = testBoard()
    const sessionState = fixture.sessionState
    if (!sessionState) throw new Error('fixture has a session state')
    const dying = {
      ...sessionState,
      state_snapshot: {
        combat: {
          status: 'active',
          participants: [{ id: 'player_30', team: 'player', conditions: ['unconscious', 'dying'], deathSaves: { successes: 0, failures: 1 } }],
        },
      },
    }
    const { container, props } = renderComposer({
      board: { sessionState: dying, worldState: testWorld({ combat: testCombat() }) },
      composer: { actionText: 'I crawl.' },
    })

    expect(statusLine(container)).toBe('You are down. Your companions can still reach you.Speak out of character')
    expect(screen.queryByRole('region', { name: 'Combat, round 2' })).not.toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Suggestions' })).not.toBeInTheDocument()
    const input = screen.getByLabelText('Your Action')
    expect(input).toBeDisabled()
    expect(input).toHaveAttribute('placeholder', 'You are down.')
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Actions' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: 'Speak out of character' }))
    expect(props.board.actionComposerProps.applyComposerMode).toHaveBeenCalledWith('ooc')
    cleanup()

    const stable = { ...dying, state_snapshot: { playerCharacters: [{ playerId: 30, health: { conditions: ['unconscious', 'stable'] } }] } }
    renderComposer({ board: { sessionState: stable }, composer: { actionText: 'I wait.' } })
    expect(screen.getByLabelText('Your Action')).toBeDisabled()
    expect(screen.queryByRole('group', { name: 'Suggestions' })).not.toBeInTheDocument()
    cleanup()

    renderComposer({ board: { sessionState: stable }, composer: { actionText: 'Hold on, I need a minute.', composerMode: 'ooc' } })
    expect(screen.getByLabelText('Your Action')).toBeEnabled()
    expect(screen.getByLabelText('Your Action')).toHaveAttribute('placeholder', 'Say it out of character…')
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
    cleanup()

    const alone = [{ id: VIEWER_ID, character_name: 'Ysolde', name: 'Danny', health: { tone: 'dead' as const, label: 'Dead', currentHp: 0, maxHp: 14 } }]
    const { container: aloneDock } = renderComposer({ board: { activePlayers: alone }, composer: { composerMode: 'ooc' } })
    expect(statusLine(aloneDock)).toBe('You are down. Your companions can still reach you.')
    expect(screen.getByLabelText('Your Action')).toBeDisabled()
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
