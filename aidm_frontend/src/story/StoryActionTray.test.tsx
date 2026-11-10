// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActionComposerProps } from '../ActionComposer'
import type { ActionIntent, ComposerMode, InventoryAction } from '../gameActions'
import { itemOptionSelectionKey } from '../gameActions'
import { GameplayControls } from '../GameplayControls'
import { SessionBoard, type SessionBoardProps } from '../SessionBoard'
import { testBoard, testComposer, testControls } from './dockTestFixtures'
import { StoryComposer } from './StoryComposer'

type Submit = (message?: string, intent?: ActionIntent) => boolean

function renderDock(board: SessionBoardProps, canUseOperatorTools = false) {
  render(
    <StoryComposer
      board={board}
      chrome={{ connection: 'ready', canUseOperatorTools, endedChapter: null }}
      onOpenDrawer={vi.fn()}
    />,
  )
}

function openSection(name: string) {
  fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
  const tray = screen.getByRole('dialog', { name: 'Actions' })
  fireEvent.click(within(tray).getByRole('button', { name }))
  return tray
}

function withoutClientId(intent: ActionIntent | undefined) {
  return intent ? { ...intent, client_message_id: 'id' } : intent
}

beforeEach(() => window.localStorage.setItem('aidm:storyHintsSeen', '3'))
afterEach(() => cleanup())

describe('StoryActionTray', () => {
  it('casts a spell at two targets with the same intent as the classic Mechanical actions panel', () => {
    const board = testBoard()
    renderDock(board)
    const tray = openSection('Magic')

    expect(within(tray).getByText('1st · Level 1 slot · 2 left')).toBeInTheDocument()
    const targets = within(tray).getByRole('group', { name: 'Magic Missile targets' })
    expect(within(targets).getByText('Targets · 0 of 3')).toBeInTheDocument()
    const caster = within(targets).getByRole('button', { name: 'Ysolde · 10/10' })
    expect(caster).toHaveAccessibleDescription('Requires a hostile target.')
    fireEvent.click(caster)
    fireEvent.click(within(targets).getByRole('button', { name: 'Goblin · 10/10' }))
    fireEvent.click(within(targets).getByRole('button', { name: 'Goblin archer · 10/10' }))
    expect(within(targets).getByText('Targets · 2 of 3')).toBeInTheDocument()
    fireEvent.click(within(tray).getByRole('button', { name: 'Cast Magic Missile' }))

    const storySubmit = vi.mocked(board.actionComposerProps.submitAction)
    expect(screen.queryByRole('dialog', { name: 'Actions' })).not.toBeInTheDocument()
    expect(screen.getByLabelText('Your Action')).toHaveFocus()
    cleanup()

    const classicSubmit = vi.fn<(message: string, intent: ActionIntent) => boolean>(() => true)
    render(<GameplayControls state={testControls()} disabled={false} onSubmit={classicSubmit} />)
    fireEvent.click(screen.getByRole('checkbox', { name: 'Target Goblin' }))
    fireEvent.click(screen.getByRole('checkbox', { name: 'Target Goblin archer' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cast Magic Missile' }))

    const [storyMessage, storyIntent] = storySubmit.mock.calls[0]
    const [classicMessage, classicIntent] = classicSubmit.mock.calls[0]
    expect(storyMessage).toBe('Ysolde casts Magic Missile at Goblin, Goblin archer.')
    expect(storyMessage).toBe(classicMessage)
    expect(withoutClientId(storyIntent)).toEqual(withoutClientId(classicIntent))
    expect(storyIntent?.spell?.target_ids).toEqual(['goblin_1', 'goblin_2'])
  })

  it('switches to free-form casting for spells the rules engine does not resolve', () => {
    const board = testBoard()
    renderDock(board)
    const tray = openSection('Magic')

    fireEvent.click(within(tray).getByRole('button', { name: 'Cast something else…' }))
    expect(board.actionComposerProps.applyComposerMode).toHaveBeenCalledWith('spell')
    expect(within(tray).getByText('The Dungeon Master will adjudicate this one.')).toBeInTheDocument()

    fireEvent.change(within(tray).getByLabelText('Spell'), { target: { value: 'Faerie Fire' } })
    expect(board.actionComposerProps.updateSpellName).toHaveBeenCalledWith('Faerie Fire')
    fireEvent.click(within(tray).getByRole('button', { name: 'Describe the casting' }))
    expect(screen.getByLabelText('Your Action')).toHaveFocus()
  })

  it('uses a capability with the same intent as the classic panel', () => {
    const board = testBoard()
    renderDock(board)
    const tray = openSection('Abilities')

    expect(within(tray).getByRole('img', { name: '5 of 10 uses left' })).toBeInTheDocument()
    fireEvent.click(within(tray).getByRole('button', { name: 'Brannoc · 3/20' }))
    fireEvent.change(within(tray).getByLabelText('Hit points'), { target: { value: '3' } })
    fireEvent.click(within(tray).getByRole('button', { name: 'Use Lay on Hands' }))
    const storySubmit = vi.mocked(board.actionComposerProps.submitAction)
    cleanup()

    const classicSubmit = vi.fn<(message: string, intent: ActionIntent) => boolean>(() => true)
    render(<GameplayControls state={testControls()} disabled={false} onSubmit={classicSubmit} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Lay on Hands target' }), { target: { value: 'player_31' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: 'Lay on Hands healing amount' }), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use Lay on Hands' }))

    const [storyMessage, storyIntent] = storySubmit.mock.calls[0]
    const [classicMessage, classicIntent] = classicSubmit.mock.calls[0]
    expect(storyMessage).toBe('Ysolde uses Lay on Hands on Brannoc for up to 3 hit points.')
    expect(storyMessage).toBe(classicMessage)
    expect(withoutClientId(storyIntent)).toEqual(withoutClientId(classicIntent))
  })

  it('runs a scene object action with the same intent as the classic panel, and blocks spent ones', () => {
    const board = testBoard({
      gameplayControls: testControls({
        interactables: [{
          ...testControls().interactables[0],
          actions: [
            { id: 'open', label: 'Open', available: true, reason: '' },
            { id: 'unlock', label: 'Unlock', available: false, reason: 'Already unlocked.' },
          ],
        }],
      }),
    })
    renderDock(board)
    const tray = openSection('Scene')
    const actions = within(tray).getByRole('group', { name: 'Lead wagon actions' })

    fireEvent.click(within(actions).getByRole('button', { name: 'Unlock' }))
    expect(board.actionComposerProps.submitAction).not.toHaveBeenCalled()
    expect(within(actions).getByRole('button', { name: 'Unlock' })).toHaveAccessibleDescription('Already unlocked.')

    fireEvent.click(within(actions).getByRole('button', { name: 'Open' }))
    const storySubmit = vi.mocked(board.actionComposerProps.submitAction)
    cleanup()

    const classicSubmit = vi.fn<(message: string, intent: ActionIntent) => boolean>(() => true)
    render(<GameplayControls state={board.gameplayControls} disabled={false} onSubmit={classicSubmit} />)
    fireEvent.click(within(screen.getByRole('group', { name: 'Scene interactions' })).getByRole('button', { name: 'Open' }))

    expect(storySubmit.mock.calls[0][0]).toBe(classicSubmit.mock.calls[0][0])
    expect(withoutClientId(storySubmit.mock.calls[0][1])).toEqual(withoutClientId(classicSubmit.mock.calls[0][1]))
  })

  it('takes a rest with the same intent as the classic Current Situation panel', async () => {
    const board = testBoard()
    renderDock(board)
    const tray = openSection('Scene')
    fireEvent.click(within(within(tray).getByRole('group', { name: 'Rest' })).getByRole('button', { name: 'Short rest' }))
    const storySubmit = vi.mocked(board.actionComposerProps.submitAction)
    cleanup()

    const classicBoard = testBoard()
    render(<SessionBoard {...classicBoard} />)
    fireEvent.click(screen.getByRole('button', { name: 'Show Current Situation details' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Take a short rest' }))
    const classicSubmit = vi.mocked(classicBoard.actionComposerProps.submitAction)

    expect(storySubmit.mock.calls[0][0]).toBe('Ysolde completes a short rest.')
    expect(storySubmit.mock.calls[0][0]).toBe(classicSubmit.mock.calls[0][0])
    expect(withoutClientId(storySubmit.mock.calls[0][1])).toEqual(withoutClientId(classicSubmit.mock.calls[0][1]))
  })

  it('uses an item through the classic item-mode path once the composer reflects the choice', async () => {
    const submitAction = vi.fn<Submit>(() => true)
    const calls: string[] = []

    function ItemHarness() {
      const base = testComposer()
      const [composerMode, setComposerMode] = useState<ComposerMode>('action')
      const [selectedInventoryAction, setSelectedInventoryAction] = useState<InventoryAction>('drop')
      const [selectedItemId, setSelectedItemId] = useState('')
      const [itemQuantity, setItemQuantity] = useState('1')
      const [actionText, setActionText] = useState('')
      const selectedItem =
        base.itemOptions.find((item, index) => itemOptionSelectionKey(item, index) === selectedItemId) ?? base.itemOptions[0]
      const composer: ActionComposerProps = {
        ...base,
        composerMode,
        applyComposerMode: (mode) => {
          calls.push(`mode:${mode}`)
          setComposerMode(mode)
        },
        selectedInventoryAction,
        updateSelectedInventoryAction: (action) => {
          calls.push(`action:${action}`)
          setSelectedInventoryAction(action)
        },
        selectedItem,
        setSelectedItemId: (value) => {
          calls.push(`item:${String(value)}`)
          setSelectedItemId(value)
        },
        itemQuantity,
        setItemQuantity: (value) => {
          calls.push(`quantity:${String(value)}`)
          setItemQuantity(value)
        },
        actionText,
        setActionText: (value) => {
          calls.push(`text:${String(value)}`)
          setActionText(value)
        },
        submitAction: (message, intent) => {
          calls.push('submit')
          expect({ composerMode, selectedInventoryAction, item: selectedItem?.name, actionText }).toEqual({
            composerMode: 'item',
            selectedInventoryAction: 'use',
            item: 'Potion of Healing',
            actionText: 'Ysolde uses Potion of Healing',
          })
          return submitAction(message, intent)
        },
      }
      return (
        <StoryComposer
          board={{ ...testBoard(), actionComposerProps: composer }}
          chrome={{ connection: 'ready', canUseOperatorTools: false, endedChapter: null }}
          onOpenDrawer={vi.fn()}
        />
      )
    }

    render(<ItemHarness />)
    const tray = openSection('Items')
    fireEvent.click(within(tray).getByRole('button', { name: /Potion of Healing/ }))
    fireEvent.click(within(within(tray).getByRole('group', { name: 'Potion of Healing actions' })).getByRole('button', { name: 'Use' }))

    await waitFor(() => expect(submitAction).toHaveBeenCalledTimes(1))
    expect(submitAction).toHaveBeenCalledWith(undefined, undefined)
    expect(calls).toEqual([
      'mode:item',
      'action:use',
      'item:potion',
      'quantity:1',
      'text:Ysolde uses Potion of Healing',
      'submit',
    ])
    expect(screen.queryByRole('dialog', { name: 'Actions' })).not.toBeInTheDocument()
  })

  it('asks who receives a given item', () => {
    const board = testBoard()
    renderDock(board)
    const tray = openSection('Items')
    fireEvent.click(within(tray).getByRole('button', { name: /Potion of Healing/ }))
    fireEvent.click(within(tray).getByRole('button', { name: 'Give' }))
    fireEvent.click(within(within(tray).getByRole('group', { name: 'Give Potion of Healing to' })).getByRole('button', { name: 'Brannoc' }))

    expect(board.actionComposerProps.updateSelectedInventoryAction).toHaveBeenCalledWith('give')
    expect(board.actionComposerProps.setActionText).toHaveBeenCalledWith('Ysolde gives Potion of Healing to Brannoc.')
  })

  it('prepares an interaction and returns to the input', () => {
    const board = testBoard()
    renderDock(board)
    const tray = openSection('Interact')
    fireEvent.click(within(tray).getByRole('button', { name: 'Act on' }))
    fireEvent.click(within(within(tray).getByRole('group', { name: 'Who' })).getByRole('button', { name: 'Brannoc' }))

    const composer = board.actionComposerProps
    expect(composer.applyComposerMode).toHaveBeenCalledWith('interact')
    expect(composer.setSelectedInteractionType).toHaveBeenCalledWith('act_on')
    expect(composer.setSelectedInteractionTargetId).toHaveBeenCalledWith('player:31')
    expect(composer.setActionText).toHaveBeenCalledWith('Ysolde directs an action at Brannoc:')
    expect(screen.getByLabelText('Your Action')).toHaveFocus()
  })

  it('rolls dice from the Roll section with the classic roll state', () => {
    const board = testBoard()
    renderDock(board)
    const tray = openSection('Roll')
    fireEvent.click(within(tray).getByRole('button', { name: 'D8' }))
    fireEvent.click(within(tray).getByRole('button', { name: 'DEX +3' }))
    fireEvent.click(within(tray).getByRole('button', { name: 'Advantage' }))
    fireEvent.change(within(tray).getByLabelText('Reason'), { target: { value: 'Leap the ditch' } })
    fireEvent.click(within(tray).getByRole('button', { name: 'Roll dice' }))

    const composer = board.actionComposerProps
    expect(composer.updateSelectedDie).toHaveBeenCalledWith('d8')
    expect(composer.updateRollAbilityKey).toHaveBeenCalledWith('dexterity')
    expect(composer.setRollMode).toHaveBeenCalledWith('advantage')
    expect(composer.setRollReason).toHaveBeenCalledWith('Leap the ditch')
    expect(composer.startDiceRoll).toHaveBeenCalledWith()
  })

  it('sets emote and out-of-character modes and closes', () => {
    const board = testBoard()
    renderDock(board)
    openSection('Emote')
    expect(board.actionComposerProps.applyComposerMode).toHaveBeenCalledWith('emote')
    expect(screen.queryByRole('dialog', { name: 'Actions' })).not.toBeInTheDocument()
  })

  it('hides sections with nothing in them', () => {
    renderDock(testBoard({ gameplayControls: testControls({ capabilities: [] }) }, { itemOptions: [], interactionTargets: [] }))
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
    const tray = screen.getByRole('dialog', { name: 'Actions' })
    expect(within(tray).queryByRole('button', { name: 'Abilities' })).not.toBeInTheDocument()
    expect(within(tray).queryByRole('button', { name: 'Items' })).not.toBeInTheDocument()
    expect(within(tray).queryByRole('button', { name: 'Interact' })).not.toBeInTheDocument()
  })

  it('offers the Director section only to operators', () => {
    renderDock(testBoard())
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
    expect(screen.queryByRole('button', { name: 'Director' })).not.toBeInTheDocument()
    cleanup()

    const board = testBoard({}, {
      canUseOperatorTools: true,
      turnControl: { mode: 'spotlight', source: 'manual', activePlayerId: 31, activePlayerName: 'Brannoc' },
    })
    renderDock(board, true)
    const tray = openSection('Director')
    const composer = board.actionComposerProps

    fireEvent.click(within(tray).getByRole('button', { name: 'Structured' }))
    expect(composer.updateTurnControl).toHaveBeenCalledWith('structured', 31, 'manual')
    fireEvent.click(within(tray).getByRole('button', { name: 'Auto' }))
    expect(composer.updateTurnControl).toHaveBeenCalledWith('free', null, 'auto')
    fireEvent.click(within(within(tray).getByRole('group', { name: 'Active player' })).getByRole('button', { name: 'Ysolde' }))
    expect(composer.updateTurnControl).toHaveBeenCalledWith('spotlight', 30)

    expect(within(tray).queryByLabelText('Admin passcode')).not.toBeInTheDocument()
    fireEvent.click(within(tray).getByRole('button', { name: 'Unlock admin tools' }))
    expect(composer.toggleAdminTools).toHaveBeenCalledTimes(1)
  })

  it('shows the admin passcode and Admin mode once admin tools are unlocked', () => {
    const board = testBoard({}, { canUseOperatorTools: true, adminToolsUnlocked: true })
    renderDock(board, true)
    const tray = openSection('Director')

    fireEvent.change(within(tray).getByLabelText('Admin passcode'), { target: { value: 'secret' } })
    expect(board.actionComposerProps.setAdminPasscode).toHaveBeenCalledWith('secret')
    fireEvent.click(within(tray).getByRole('button', { name: 'Admin mode' }))
    expect(board.actionComposerProps.applyComposerMode).toHaveBeenCalledWith('admin')
  })

  it('closes on an outside click and returns focus to the actions button', () => {
    renderDock(testBoard())
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('dialog', { name: 'Actions' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Actions' })).toHaveFocus()
  })
})
