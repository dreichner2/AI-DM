// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActionComposerProps } from '../ActionComposer'
import type { SessionBoardProps } from '../SessionBoard'
import { testBoard, testCombat, testControls, testRollNotice, testWorld, VIEWER_ID } from './dockTestFixtures'
import { StoryComposer } from './StoryComposer'
import { dismissEnding } from './storyEnding'
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

  it('keeps Send disabled for an empty draft, and lets a draft go while the Dungeon Master is working', () => {
    renderComposer({ composer: { actionText: '   ' } })
    expect(screen.getByRole('button', { name: 'Send' })).toBeDisabled()
    cleanup()

    const { props } = renderComposer({ board: { streamingTurnActive: true }, composer: { actionText: 'I draw my blade.' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(props.board.actionComposerProps.submitAction).toHaveBeenCalledTimes(1)
  })

  it('shows an action held for the end of the turn as one quiet line, with Cancel', () => {
    const cancelHeldAction = vi.fn()
    const { container } = renderComposer({
      board: { sendPending: true },
      composer: { heldActionText: 'I draw my blade.', cancelHeldAction },
    })

    expect(statusLine(container)).toBe('Waiting for the story to catch up — "I draw my blade."Cancel')
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(cancelHeldAction).toHaveBeenCalledTimes(1)
  })

  it('sends what may be said to a captive as the player’s own words', () => {
    const { props } = renderComposer({
      board: {
        worldState: testWorld({
          captives: [{ id: 'cutthroat', name: 'Ashmarked Cutthroat', offeredActions: [{ kind: 'talk', label: 'Question the Ashmarked Cutthroat' }] }],
        }),
      },
    })

    fireEvent.click(within(screen.getByRole('group', { name: 'Suggestions' })).getByRole('button', { name: 'Question the Ashmarked Cutthroat' }))
    expect(props.board.actionComposerProps.submitAction).toHaveBeenCalledWith('Question the Ashmarked Cutthroat')
  })

  it('shows who the hero is talking with above the conversation, and sends its topics and leaving as intents', () => {
    const { props, container } = renderComposer({
      board: {
        worldState: testWorld({
          conversation: {
            npcId: 'npc_mara',
            name: 'Mara Reedwake',
            summary: 'The innkeeper who notices everything.',
            actions: [
              { kind: 'topic', id: 'mara_caravan', label: 'Ask about the caravan' },
              { kind: 'say_free', id: '', label: 'Say something else' },
              { kind: 'leave_conversation', id: '', label: 'Leave it there' },
            ],
          },
        }),
      },
    })
    const composer = props.board.actionComposerProps
    const talking = container.querySelector('.story-talking') as HTMLElement
    expect(within(talking).getByText('Talking with Mara Reedwake')).toHaveClass('story-label')
    expect(talking).toHaveTextContent('The innkeeper who notices everything.')
    expect(talking.nextElementSibling).toHaveAttribute('aria-label', 'Suggestions')
    expect(screen.getByLabelText('Your Action')).toHaveAttribute('placeholder', 'Say it in your own words')
    const suggestions = screen.getByRole('group', { name: 'Suggestions' })
    expect(within(suggestions).getAllByRole('button').map((chip) => chip.textContent)).toEqual([
      'Ask about the caravan',
      'Say something else',
      'Leave it there',
    ])
    // The conversation's row wraps (stylesheet) so leaving it never waits behind More….
    expect(suggestions).toHaveAttribute('data-talking', 'true')

    fireEvent.click(within(suggestions).getByRole('button', { name: 'Ask about the caravan' }))
    expect(composer.submitAction).toHaveBeenLastCalledWith('Ask about the caravan.', {
      kind: 'talk',
      source: 'scene_panel',
      text: 'Ask about the caravan.',
      client_message_id: expect.any(String),
      talk: { action: 'topic', npc_id: 'npc_mara', topic_id: 'mara_caravan' },
    })
    fireEvent.click(within(suggestions).getByRole('button', { name: 'Leave it there' }))
    expect(composer.submitAction).toHaveBeenLastCalledWith('Leave it there.', {
      kind: 'talk',
      source: 'scene_panel',
      text: 'Leave it there.',
      client_message_id: expect.any(String),
      talk: { action: 'leave', npc_id: 'npc_mara' },
    })
    fireEvent.click(within(suggestions).getByRole('button', { name: 'Say something else' }))
    expect(composer.submitAction).toHaveBeenCalledTimes(2)
    expect(composer.updateActionText).not.toHaveBeenCalled()
    expect(screen.getByLabelText('Your Action')).toHaveFocus()
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

  it('says why the table refused an action instead of dropping it in silence', () => {
    const refusal = 'Arden Vale has the spotlight. Your action was not sent; your draft is kept.'
    const { container } = renderComposer({ composer: { turnRejectionMessage: refusal } })

    expect(statusLine(container)).toBe(refusal)
    expect(container.querySelector('.story-dock-status')).toHaveAttribute('role', 'alert')
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

  it('lets the suggestions take a second line where the stylesheet allows two', () => {
    let rowWidth = 300
    const layout = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const width = this.classList.contains('story-suggestions')
        ? rowWidth
        : this.classList.contains('story-chip') ? (this.textContent ?? '').length * 8 : 0
      return { x: 0, y: 0, top: 0, left: 0, right: width, bottom: 0, width, height: 0, toJSON: () => ({}) }
    })
    try {
      const { props, rerender } = renderComposer()
      const row = screen.getByRole('group', { name: 'Suggestions' })
      const visible = () => within(row).getAllByRole('button').filter((chip) => !chip.hidden).map((chip) => chip.textContent)
      // One line of 300: 144 px and More… only.
      expect(visible()).toEqual(['Examine Lead wagon', 'More…'])

      // Narrow screens (story-dock.css): 144 + 144 on the first line, 160 on the second.
      row.style.setProperty('--story-chip-rows', '2')
      rerender(<StoryComposer {...props} />)
      expect(visible()).toEqual(['Examine Lead wagon', 'Talk to Marta Vell', 'Go to North causeway'])

      rowWidth = 200
      rerender(<StoryComposer {...props} />)
      expect(visible()).toEqual(['Examine Lead wagon', 'Talk to Marta Vell', 'More…'])
    } finally {
      layout.mockRestore()
    }
  })

  it('fits the suggestions again whenever web fonts finish loading', () => {
    const fonts = new EventTarget()
    Object.defineProperty(document, 'fonts', { configurable: true, value: fonts })
    let charWidth = 6
    const layout = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
      const width = this.classList.contains('story-suggestions')
        ? 400
        : this.classList.contains('story-chip') ? (this.textContent ?? '').length * charWidth : 0
      return { x: 0, y: 0, top: 0, left: 0, right: width, bottom: 0, width, height: 0, toJSON: () => ({}) }
    })
    try {
      renderComposer()
      const visible = () =>
        within(screen.getByRole('group', { name: 'Suggestions' })).getAllByRole('button').map((chip) => chip.textContent)
      // The fallback face is narrow enough for every chip…
      expect(visible()).toEqual(['Examine Lead wagon', 'Talk to Marta Vell', 'Go to North causeway'])

      // …the story face, arriving later, is not.
      charWidth = 8
      fonts.dispatchEvent(new Event('loadingdone'))
      expect(visible()).toEqual(['Examine Lead wagon', 'Talk to Marta Vell', 'More…'])
    } finally {
      layout.mockRestore()
      Reflect.deleteProperty(document, 'fonts')
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

  it('suggests nothing once the story has ended, until the reader chooses Keep playing', () => {
    window.sessionStorage.clear()
    const ending = { title: 'The Road Remembers', summary: 'The lanterns burn steady along the western roads.' }
    const { props } = renderComposer({ board: { worldState: testWorld({ ending }) } })
    expect(screen.queryByRole('group', { name: 'Suggestions' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Your Action')).toBeEnabled()

    act(() => dismissEnding(props.board.sessionId))
    expect(within(screen.getByRole('group', { name: 'Suggestions' })).getByRole('button', { name: 'Talk to Marta Vell' }))
      .toBeInTheDocument()
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

  it('closes everything but out-of-character speech and the turn order to a hero who is down: dying, stable or at 0 HP', () => {
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
    expect(screen.getByRole('region', { name: 'Combat, round 2' })).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Combat actions' })).not.toBeInTheDocument()
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

  it('tells a hero out of the fight they come round when it ends, and keeps the turn order in view', () => {
    const fixture = testBoard()
    const sessionState = fixture.sessionState
    if (!sessionState) throw new Error('fixture has a session state')
    const out = {
      ...sessionState,
      state_snapshot: {
        combat: {
          status: 'active',
          participants: [{ id: 'player_30', team: 'player', conditions: ['unconscious', 'stable', 'out_of_fight'] }],
        },
      },
    }
    const { container, props } = renderComposer({ board: { sessionState: out, worldState: testWorld({ combat: testCombat() }) } })

    expect(statusLine(container)).toBe('You are out of this fight. You will come round when it ends.Speak out of character')
    expect(screen.getByLabelText('Your Action')).toHaveAttribute('placeholder', 'You are out of this fight.')
    expect(screen.getByLabelText('Your Action')).toBeDisabled()
    expect(screen.getByRole('region', { name: 'Combat, round 2' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Speak out of character' }))
    expect(props.board.actionComposerProps.applyComposerMode).toHaveBeenCalledWith('ooc')
  })

  it('asks the finishing hero how it ends, and shows the others who is deciding without blocking them', () => {
    const finisher = {
      heroId: 'player_30',
      heroName: 'Ysolde',
      enemyName: 'Ashmarked Cutthroat',
      waitingLine: 'Ysolde is deciding how it ends…',
      offeredActions: [
        { id: 'finisher.describe', kind: 'finisher', label: 'Describe how you end it', prompt: 'How do you end it?' },
        { id: 'finisher.story', kind: 'finisher', label: 'Let the story tell it', prompt: '' },
      ],
    }
    const { props } = renderComposer({ board: { worldState: testWorld({ combat: { ...testCombat(), pendingFinisher: finisher } }) } })
    const row = screen.getByRole('group', { name: 'The finishing blow' })
    const input = screen.getByLabelText('Your Action')
    expect(input).toHaveAttribute('placeholder', 'How do you end it?')
    expect(screen.queryByRole('group', { name: 'Combat actions' })).not.toBeInTheDocument()

    fireEvent.click(within(row).getByRole('button', { name: 'Describe how you end it' }))
    expect(input).toHaveFocus()
    expect(props.board.actionComposerProps.submitAction).not.toHaveBeenCalled()
    fireEvent.click(within(row).getByRole('button', { name: 'Let the story tell it' }))
    expect(props.board.actionComposerProps.submitAction).toHaveBeenCalledWith('Let the story tell it')
    cleanup()

    const { container } = renderComposer({
      board: { worldState: testWorld({ combat: { ...testCombat(), active: false, pendingFinisher: { ...finisher, heroId: 'player_31', heroName: 'Corin Ashby', waitingLine: 'Corin Ashby is deciding how it ends…', offeredActions: [] } } }) },
      composer: { actionText: 'I bind my wound.' },
    })
    expect(statusLine(container)).toBe('Corin Ashby is deciding how it ends…')
    expect(screen.queryByRole('group', { name: 'The finishing blow' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Your Action')).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
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

/** Let fake time pass the way a browser does: each timer fires, then the card re-renders and sets its next one. */
function advance(ms: number) {
  for (let elapsed = 0; elapsed < ms; elapsed += 1) {
    act(() => {
      vi.advanceTimersByTime(1)
    })
  }
}

describe('StoryComposer rolling in place', () => {
  const pressedAt = new Date('2026-11-11T12:00:00.000Z').getTime()
  const pressed = {
    die: 'd20',
    message: 'I roll a d20 for Stealth check',
    actionIntent: {
      kind: 'roll' as const,
      source: 'dice_roller' as const,
      text: 'I roll a d20 for Stealth check',
      client_message_id: 'client-roll-1',
      roll: { die: 'd20', mode: 'normal' as const, reason: 'Stealth check', result_visibility: 'hidden_until_landed' as const, target_pending_turn_id: 41 },
    },
    clientMessageId: 'client-roll-1',
    rollKey: 1,
    startedAt: pressedAt,
    result: null,
    landAt: null,
    status: 'requesting' as const,
  }
  const result = {
    session_id: 20,
    turn_id: 42,
    player_id: VIEWER_ID,
    client_message_id: 'client-roll-1',
    pending_turn_id: 41,
    rule_type: 'stealth',
    die: 'd20',
    mode: 'normal' as const,
    rolls: [15],
    kept: 15,
    modifier: 4,
    total: 19,
    reason: 'Stealth check',
    result_visibility: 'hidden_until_landed' as const,
    ability: { key: 'dexterity', label: 'DEX', score: 18, modifier: 4 },
    dc: 13,
    outcome: 'success' as const,
    authoritative: true as const,
  }

  afterEach(() => vi.useRealTimers())

  it('turns the roll prompt into the roll card, cycling until the result lands half a second after the press', () => {
    vi.useFakeTimers({ now: pressedAt })
    const notice = testRollNotice()
    const { rerender, props } = renderComposer({ board: { pendingRollNotice: notice } })
    expect(screen.getByRole('button', { name: 'Roll Stealth check' })).toBeInTheDocument()

    const markDiceRollPlayed = vi.fn()
    const withRoll = (diceRoll: ActionComposerProps['diceRoll']) => ({
      ...props,
      board: testBoard({ pendingRollNotice: notice }, { diceRoll, markDiceRollPlayed }),
    })
    rerender(<StoryComposer {...withRoll(pressed)} />)
    const card = screen.getByRole('region', { name: 'Your roll' })
    expect(screen.queryByRole('button', { name: 'Roll Stealth check' })).not.toBeInTheDocument()
    expect(card.querySelector('.roll-card')).toHaveAttribute('data-phase', 'cycle')
    expect(card.querySelector('.roll-card-label')).toHaveTextContent('Ysolde · Stealth check')

    // The result arrives at once; the d20 keeps cycling until 500 ms after the press.
    rerender(<StoryComposer {...withRoll({ ...pressed, result, landAt: pressedAt + 500, status: 'rolling' })} />)
    advance(499)
    expect(card.querySelector('.roll-card')).toHaveAttribute('data-phase', 'cycle')
    advance(1)
    expect(card.querySelector('.roll-card-total')).toHaveTextContent('15')
    advance(150)
    expect(card.querySelector('.roll-card-total')).toHaveTextContent('19')
    expect(card.querySelector('.roll-card-outcome')).toHaveTextContent('success vs DC 13')
    expect(markDiceRollPlayed).toHaveBeenCalledTimes(1)
  })

  it('offers a retry when the roll could not be sent', () => {
    const retryDiceRoll = vi.fn(() => true)
    const closeDiceRoll = vi.fn()
    renderComposer({ composer: { diceRoll: { ...pressed, status: 'failed', error: 'The roll request could not be sent.' }, retryDiceRoll, closeDiceRoll } })

    expect(screen.getByRole('alert')).toHaveTextContent('The roll request could not be sent.')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(retryDiceRoll).toHaveBeenCalledTimes(1)
    expect(closeDiceRoll).toHaveBeenCalledTimes(1)
  })
})
