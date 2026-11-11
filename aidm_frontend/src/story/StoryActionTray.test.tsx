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
import type { GameplayItemControl, GameplayNarratedSpell } from '../gameplayControlState'
import type { WorldMerchantSummary } from '../gameSelectors'
import type { JsonRecord, TimelineEntry } from '../types'
import { spellCastSubmission } from './actionIntents'
import { testBoard, testCombat, testComposer, testControls, testWorld, VIEWER_ID } from './dockTestFixtures'
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

    expect(within(tray).getByText('Action · Level 1 slot · 2 left')).toBeInTheDocument()
    expect(within(tray).getByText('three darts of force, 1d4+1 each')).toBeInTheDocument()
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

  it('words a spell cast only on its caster as cast on themself, never at their own name', () => {
    const [spell] = testControls().spells
    expect(spellCastSubmission('Ysolde', spell, ['player_30']).message).toBe('Ysolde casts Magic Missile on themself.')
    expect(spellCastSubmission('Ysolde', spell, ['player_30', 'goblin_1']).message).toBe('Ysolde casts Magic Missile at Goblin, Ysolde.')
  })

  it('picks a cast level before casting and sends it with the request', () => {
    const [missile] = testControls().spells
    const board = testBoard({
      gameplayControls: testControls({
        spells: [{ ...missile, castLevels: [1, 2, 3], upcastTargetsPerLevel: 1, maxTargets: 3, concentration: true }],
      }),
    })
    renderDock(board)
    const tray = openSection('Magic')
    expect(within(tray).getByText('conc.')).toBeInTheDocument()

    const levels = within(tray).getByRole('radiogroup', { name: 'Cast level' })
    expect(within(levels).getAllByRole('radio').map((radio) => radio.textContent)).toEqual(['1st', '2nd', '3rd'])
    expect(within(levels).getByRole('radio', { name: '1st' })).toHaveAttribute('aria-checked', 'true')
    fireEvent.click(within(levels).getByRole('radio', { name: '3rd' }))
    expect(within(tray).getByText('Targets · 0 of 5')).toBeInTheDocument()
    fireEvent.click(within(tray).getByRole('button', { name: 'Goblin · 10/10' }))
    fireEvent.click(within(tray).getByRole('button', { name: 'Cast Magic Missile' }))

    expect(vi.mocked(board.actionComposerProps.submitAction).mock.calls[0][1]?.spell).toMatchObject({
      name: 'Magic Missile',
      target_ids: ['goblin_1'],
      cast_level: 3,
    })
  })

  it('offers only spells castable here outside combat, and narrated magic last for the Dungeon Master', () => {
    const [missile] = testControls().spells
    const board = testBoard({
      gameplayControls: testControls({
        spells: [
          { ...missile, outOfCombat: false },
          { ...missile, id: 'spell_cure_wounds', name: 'Cure Wounds', castingTime: 'bonus_action', effectSummary: 'heals 1d8' },
        ],
        narratedSpells: [
          {
            id: 'spell_detect_magic',
            name: 'Detect Magic',
            level: 1,
            castingTime: 'action',
            effectSummary: 'Sense magic within 30 feet.',
            concentration: true,
            rulesBacked: false,
            resourceLabel: 'Level 1 slot · 2 left',
            available: true,
            reason: '',
          },
        ],
      }),
    })
    renderDock(board)
    const tray = openSection('Magic')

    const spells = within(tray).getByRole('list', { name: 'Spells' })
    expect(within(spells).queryByText('Magic Missile')).not.toBeInTheDocument()
    expect(within(spells).getByText('Bonus · Level 1 slot · 2 left')).toBeInTheDocument()
    const narrated = within(tray).getByRole('group', { name: 'Narrated magic' })
    expect(narrated).toHaveTextContent('The Dungeon Master adjudicates these.')
    expect(narrated.compareDocumentPosition(spells) & Node.DOCUMENT_POSITION_PRECEDING).toBeTruthy()
    fireEvent.click(within(narrated).getByRole('button', { name: /Detect Magic/ }))

    expect(board.actionComposerProps.applyComposerMode).toHaveBeenCalledWith('spell')
    expect(board.actionComposerProps.updateSpellName).toHaveBeenCalledWith('Detect Magic')
    expect(within(tray).getByText('The Dungeon Master will adjudicate this one.')).toBeInTheDocument()
  })

  it('keeps narrated magic behind one line in a fight, opened on request', () => {
    const detectMagic: GameplayNarratedSpell = {
      id: 'spell_detect_magic',
      name: 'Detect Magic',
      level: 1,
      castingTime: 'action',
      effectSummary: 'Sense magic within 30 feet.',
      concentration: true,
      rulesBacked: false,
      resourceLabel: 'Level 1 slot · 2 left',
      available: true,
      reason: '',
    }
    const board = testBoard({
      worldState: testWorld({ combat: testCombat() }),
      gameplayControls: testControls({ inCombat: true, narratedSpells: [detectMagic, { ...detectMagic, id: 'spell_light', name: 'Light' }] }),
    })
    renderDock(board)
    const tray = openSection('Magic')

    expect(within(tray).getByRole('list', { name: 'Spells' })).toHaveTextContent('Magic Missile')
    expect(within(tray).queryByRole('group', { name: 'Narrated magic' })).not.toBeInTheDocument()
    expect(within(tray).queryByText('Detect Magic')).not.toBeInTheDocument()
    fireEvent.click(within(tray).getByRole('button', { name: 'Narrated magic (2)' }))
    const narrated = within(tray).getByRole('group', { name: 'Narrated magic' })
    expect(within(narrated).getAllByRole('listitem').map((item) => item.querySelector('.story-tray-row-name')?.textContent))
      .toEqual(['Detect Magic', 'Light'])
    expect(within(tray).queryByRole('button', { name: 'Narrated magic (2)' })).not.toBeInTheDocument()
  })

  it('never offers a defeated enemy or a hero out of the fight in a target row', () => {
    const combat = testCombat()
    combat.enemies.push({ ...combat.enemies[1], id: 'goblin_2', name: 'Goblin archer', health: 'Down', healthTone: 'down' })
    combat.allies.push({ ...combat.allies[0], id: 'player_31', name: 'Brannoc', health: 'Down', healthTone: 'down', conditions: ['unconscious', 'stable', 'out_of_fight'] })
    const [missile] = testControls().spells
    const [layOnHands] = testControls().capabilities
    const brannoc = { ...layOnHands.targets[1], currentHp: 0, conditions: ['unconscious', 'stable', 'out_of_fight'] }
    const potion: GameplayItemControl = {
      id: 'potion',
      name: 'Potion of Healing',
      quantity: 2,
      effectSummary: 'Restores 2d4+2 hit points.',
      cost: 'bonus action; action for an ally',
      available: true,
      reason: '',
      targets: [layOnHands.targets[0], brannoc],
    }
    const board = testBoard({
      worldState: testWorld({ combat }),
      gameplayControls: testControls({
        inCombat: true,
        spells: [{
          ...missile,
          targets: [...missile.targets.slice(0, 2), { ...missile.targets[0], id: 'archer_1', name: 'Distant Archer' }, missile.targets[2]],
        }],
        capabilities: [{ ...layOnHands, targets: [layOnHands.targets[0], brannoc] }],
        items: [potion],
      }),
    })
    renderDock(board)

    const magic = openSection('Magic')
    expect(within(within(magic).getByRole('group', { name: 'Magic Missile targets' })).getAllByRole('button').map((button) => button.textContent))
      .toEqual(['Goblin · 10/10', 'Distant Archer · 10/10', 'Ysolde · 10/10'])

    // Brannoc is out of the fight: Lay on Hands and the potion have only Ysolde, so neither asks.
    fireEvent.click(within(magic).getByRole('button', { name: 'Abilities' }))
    expect(within(magic).queryByRole('group', { name: 'Lay on Hands target' })).not.toBeInTheDocument()
    fireEvent.click(within(magic).getByRole('button', { name: 'Use Lay on Hands' }))
    expect(board.actionComposerProps.submitAction).toHaveBeenLastCalledWith(
      'Ysolde uses Lay on Hands on Ysolde for up to 1 hit points.',
      expect.anything(),
    )

    const items = openSection('Items')
    fireEvent.click(within(items).getByRole('button', { name: /^Potion of Healing/ }))
    expect(within(items).queryByRole('group', { name: 'Potion of Healing target' })).not.toBeInTheDocument()
    fireEvent.click(within(items).getByRole('button', { name: 'Use Potion of Healing' }))
    expect(board.actionComposerProps.submitAction).toHaveBeenLastCalledWith('Ysolde uses Potion of Healing.', expect.anything())
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

    expect(within(tray).getByRole('img', { name: 'Lay on Hands: 5 of 10 left' })).toBeInTheDocument()
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

  it('shows each class pool once and what every ability costs and does, with the server reason when it cannot be used', () => {
    const base = testControls().capabilities[0]
    const ability = (id: string, name: string, extra: Partial<typeof base>) => ({
      ...base,
      id,
      name,
      targetPolicy: 'self',
      effectType: 'grant_action',
      actionEconomy: 'bonus action',
      poolId: 'ki',
      poolName: 'Ki',
      poolCost: 1,
      current: 2,
      max: 3,
      ...extra,
    })
    renderDock(testBoard({
      gameplayControls: testControls({
        capabilities: [
          ability('flurry_of_blows', 'Flurry of Blows', { description: 'Two unarmed strikes as a bonus action.' }),
          ability('patient_defense', 'Patient Defense', {
            description: 'Dodge as a bonus action.',
            available: false,
            reason: 'Available only during active combat.',
          }),
          ability('rage', 'Rage', { poolId: 'rage', poolName: 'Rage', current: 1, max: 2 }),
        ],
      }),
    }))
    const tray = openSection('Abilities')

    const pools = within(tray).getByRole('group', { name: 'Class pools' })
    expect(within(pools).getAllByRole('img').map((pool) => pool.getAttribute('aria-label'))).toEqual([
      'Ki: 2 of 3 left',
      'Rage: 1 of 2 left',
    ])
    expect(pools).toHaveTextContent('Ki2/3Rage1/2')
    const flurry = within(tray).getByRole('button', { name: /^Flurry of Blows/ })
    expect(flurry).toHaveTextContent('Bonus action · 1 Ki')
    expect(flurry).toHaveTextContent('Two unarmed strikes as a bonus action.')
    expect(within(tray).getByRole('button', { name: /^Patient Defense/ })).toHaveTextContent('Available only during active combat.')
  })

  it('lists rules-backed consumables first and uses one on the chosen target', () => {
    const potion: GameplayItemControl = {
      id: 'potion',
      name: 'Potion of Healing',
      quantity: 2,
      effectSummary: 'Restores 2d4+2 hit points.',
      cost: 'bonus action; action for an ally',
      available: true,
      reason: '',
      targets: [
        { id: 'player_30', name: 'Ysolde', team: 'player', rangeBand: 'near', currentHp: 9, maxHp: 14, conditions: [], available: true, reason: '' },
        { id: 'player_31', name: 'Brannoc', team: 'player', rangeBand: 'near', currentHp: 3, maxHp: 20, conditions: [], available: true, reason: '' },
      ],
    }
    const board = testBoard({ gameplayControls: testControls({ items: [potion] }) })
    renderDock(board)
    const tray = openSection('Items')

    const consumables = within(tray).getByRole('list', { name: 'Consumables' })
    const others = within(tray).getByRole('list', { name: 'Other items' })
    expect(consumables.compareDocumentPosition(others) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(others).queryByText('Potion of Healing')).not.toBeInTheDocument()
    expect(within(others).getByText('Longbow')).toBeInTheDocument()
    const row = within(consumables).getByRole('button', { name: /Potion of Healing/ })
    expect(row).toHaveTextContent('Bonus action; action for an ally')
    expect(row).toHaveTextContent('Restores 2d4+2 hit points.')
    fireEvent.click(row)
    fireEvent.click(within(tray).getByRole('button', { name: 'Brannoc · 3/20' }))
    fireEvent.click(within(tray).getByRole('button', { name: 'Use Potion of Healing' }))

    expect(board.actionComposerProps.submitAction).toHaveBeenCalledWith('Ysolde uses Potion of Healing on Brannoc.', {
      kind: 'item',
      source: 'scene_panel',
      text: 'Ysolde uses Potion of Healing on Brannoc.',
      client_message_id: expect.any(String),
      inventory_action: 'use',
      item: { id: 'potion', name: 'Potion of Healing', quantity: 1, target_id: 'player_31' },
    })
  })

  it('uses a consumable in combat through the server item action, and explains a blocked one', () => {
    const potion: GameplayItemControl = {
      id: 'potion',
      name: 'Potion of Healing',
      quantity: 1,
      effectSummary: 'Restores 2d4+2 hit points.',
      cost: 'bonus action',
      available: true,
      reason: '',
      actionId: 'item.use.potion',
      targets: [
        { id: 'player_30', name: 'Ysolde', team: 'player', rangeBand: 'near', currentHp: 9, maxHp: 14, conditions: [], available: true, reason: '' },
      ],
    }
    const acid: GameplayItemControl = { ...potion, id: 'acid', name: 'Acid', actionId: 'item.use.acid', available: false, reason: 'No legal target is available.', targets: [] }
    const board = testBoard({ gameplayControls: testControls({ inCombat: true, items: [potion, acid] }) })
    renderDock(board)
    const tray = openSection('Items')
    fireEvent.click(within(tray).getByRole('button', { name: /^Acid/ }))
    expect(within(tray).getByRole('button', { name: 'Use Acid' })).toBeDisabled()
    fireEvent.click(within(tray).getByRole('button', { name: /^Potion of Healing/ }))
    fireEvent.click(within(tray).getByRole('button', { name: 'Use Potion of Healing' }))

    expect(board.actionComposerProps.submitAction).toHaveBeenCalledWith('Ysolde uses Potion of Healing.', expect.objectContaining({
      kind: 'combat',
      combat: { action_id: 'item.use.potion', target_id: 'player_30' },
    }))
  })

  describe('trade', () => {
    const merchant: WorldMerchantSummary = {
      id: 'mara_shop',
      name: "Mara's Sundries",
      npcId: 'mara',
      buyRate: 0.5,
      stock: [
        { stockId: 'mara_0', name: 'Potion of Healing', type: 'potion', price: { gp: 50 }, priceLabel: '50 gp', quantity: 2 },
        { stockId: 'mara_1', name: 'Lantern', type: 'gear', price: { gp: 5, sp: 5 }, priceLabel: '5 gp, 5 sp', quantity: null },
        { stockId: 'mara_2', name: 'Rope', type: 'gear', price: { gp: 1 }, priceLabel: '1 gp', quantity: 0 },
        { stockId: 'mara_3', name: 'Spyglass', type: 'gear', price: { gp: 1000 }, priceLabel: '1000 gp', quantity: 1 },
      ],
    }

    function logEntry(id: string, role: TimelineEntry['role'], text: string, metadata: JsonRecord): TimelineEntry {
      return { id, role, speaker: role === 'player' ? 'Ysolde' : 'System', text, timestamp: null, metadata }
    }

    function tradeBoard(turnRows: TimelineEntry[] = []) {
      return testBoard({
        turnRows,
        currentResponseEntry: null,
        worldState: testWorld({ merchants: [merchant] }),
        gameplayControls: testControls({
          currency: { gp: 60, sp: 3 },
          tradeGoods: [
            { id: 'dagger_1', name: 'Dagger', quantity: 2, price: { gp: 2 } },
            { id: 'lantern_1', name: 'Lantern', quantity: 1, price: { gp: 5, sp: 5 } },
            { id: 'trinket_1', name: 'Odd trinket', quantity: 1, price: null },
            // Mara stocks rope, but only the item's own price makes an offer.
            { id: 'rope_1', name: 'Rope', quantity: 1, price: null },
            // Half of a copper is nothing: a line that would sell for 0 is not listed.
            { id: 'torch_1', name: 'Torch', quantity: 3, price: { cp: 1 } },
          ],
        }),
      })
    }

    it('opens from the trade suggestion with the purse, the stock and the offers', () => {
      const board = tradeBoard()
      renderDock(board)
      const suggestions = within(screen.getByRole('group', { name: 'Suggestions' })).getAllByRole('button')
      expect(suggestions.map((chip) => chip.textContent)).toEqual([
        'Examine Lead wagon',
        'Talk to Marta Vell',
        "Trade with Mara's Sundries",
        'Go to North causeway',
      ])
      fireEvent.click(suggestions[2])

      const tray = screen.getByRole('dialog', { name: 'Actions' })
      expect(within(tray).getByRole('button', { name: 'Trade' })).toHaveAttribute('aria-pressed', 'true')
      expect(within(tray).getByText('Your coin').parentElement).toHaveTextContent('Your coin60 gp 3 sp')
      const buy = within(tray).getByRole('region', { name: 'Buy' })
      const lines = within(buy).getAllByRole('listitem')
      expect(lines.map((line) => line.textContent)).toEqual([
        'Potion of Healing50 gp2 leftBuy Potion of Healing',
        'Lantern5 gp 5 spBuy Lantern',
        'Rope1 gp0 leftBuy RopeSold out.',
        'Spyglass1000 gp1 leftBuy SpyglassYou cannot afford this.',
      ])
      expect(within(buy).getByRole('button', { name: 'Buy Rope' })).toBeDisabled()
      expect(within(buy).getByRole('button', { name: 'Buy Spyglass' })).toBeDisabled()

      const sell = within(tray).getByRole('region', { name: 'Sell' })
      expect(within(sell).getAllByRole('listitem').map((line) => line.textContent)).toEqual([
        'Dagger ×21 gpSell Dagger',
        'Lantern2 gp 7 sp 5 cpSell Lantern',
      ])
    })

    it('buys and sells with the server shop intents and keeps the counter open', () => {
      const board = tradeBoard()
      renderDock(board)
      const tray = openSection('Trade')
      fireEvent.click(within(tray).getByRole('button', { name: 'Buy Potion of Healing' }))
      fireEvent.click(within(tray).getByRole('button', { name: 'Sell Dagger' }))

      const calls = vi.mocked(board.actionComposerProps.submitAction).mock.calls
      expect(calls[0]).toEqual(["Ysolde buys Potion of Healing from Mara's Sundries.", {
        kind: 'shop',
        source: 'scene_panel',
        text: "Ysolde buys Potion of Healing from Mara's Sundries.",
        client_message_id: expect.any(String),
        shop: { action: 'buy', merchant_id: 'mara_shop', stock_id: 'mara_0', quantity: 1 },
      }])
      expect(calls[1]).toEqual(["Ysolde sells Dagger to Mara's Sundries.", {
        kind: 'shop',
        source: 'scene_panel',
        text: "Ysolde sells Dagger to Mara's Sundries.",
        client_message_id: expect.any(String),
        shop: { action: 'sell', merchant_id: 'mara_shop', item_id: 'dagger_1', quantity: 1 },
      }])
      expect(screen.getByRole('dialog', { name: 'Actions' })).toBeInTheDocument()
    })

    it('shows a server refusal under the line that caused it until a later attempt succeeds', () => {
      const attempt = (id: string, turnId: number, shop: JsonRecord) =>
        logEntry(id, 'player', 'Ysolde buys.', { turn_id: turnId, action_intent: { kind: 'shop', shop } })
      const refused = (id: string, turnId: number, reason: string) =>
        logEntry(id, 'system', 'State updated', {
          turn_id: turnId,
          state_log: { lines: [{ status: 'rejected', changeType: 'shop.buy', message: `Ysolde buys Lantern rejected: ${reason}`, visibleToPlayer: true }] },
        })
      const lantern = { action: 'buy', merchant_id: 'mara_shop', stock_id: 'mara_1', quantity: 1 }
      const potion = { action: 'buy', merchant_id: 'mara_shop', stock_id: 'mara_0', quantity: 1 }
      renderDock(tradeBoard([
        attempt('log-1', 4, potion),
        refused('log-2', 4, 'Ysolde cannot afford Potion of Healing.'),
        attempt('log-3', 5, potion),
        attempt('log-4', 6, lantern),
        refused('log-5', 6, "Mara's Sundries is not open for trade right now."),
      ]))
      const buy = within(openSection('Trade')).getByRole('region', { name: 'Buy' })
      const [potionLine, lanternLine] = within(buy).getAllByRole('listitem')

      expect(potionLine).not.toHaveTextContent('cannot afford')
      expect(lanternLine.querySelector('[data-refused]')).toHaveTextContent("Mara's Sundries is not open for trade right now.")
    })
  })

  it('lists what can be taken and each object’s offered actions: legal ones as links, others dimmed with the reason, done ones omitted', () => {
    const board = testBoard({
      worldState: testWorld({
        sceneItems: [
          { id: 'key', name: 'Caravan Strongbox Key', quantity: 1, type: 'key', takeLabel: 'Take the Caravan Strongbox Key' },
          { id: 'mud', name: 'Road mud', quantity: 1, type: 'object' },
        ],
      }),
      gameplayControls: testControls({
        interactables: [{
          ...testControls().interactables[0],
          id: 'strongbox',
          name: 'Wagon Strongbox',
          states: ['Closed', 'Locked'],
          offered: true,
          actions: [
            { id: 'search', label: 'Search the strongbox', available: false, reason: 'It is locked.', phrase: true, authored: true },
            { id: 'unlock', label: 'Unlock the strongbox', available: true, reason: '', phrase: true, authored: true },
            { id: 'inspect', label: 'Examine Wagon Strongbox', available: false, reason: '', phrase: true, done: true },
          ],
        }],
      }),
    })
    renderDock(board)
    const tray = openSection('Scene')

    const loose = within(tray).getByRole('group', { name: 'Lying here' })
    expect(within(loose).getAllByRole('button').map((link) => link.textContent)).toEqual(['Take the Caravan Strongbox Key'])
    fireEvent.click(within(loose).getByRole('button', { name: 'Take the Caravan Strongbox Key' }))
    expect(board.actionComposerProps.submitAction).toHaveBeenLastCalledWith('Take the Caravan Strongbox Key.', expect.objectContaining({
      kind: 'item',
      inventory_action: 'pick_up',
      item: { id: 'key', name: 'Caravan Strongbox Key', quantity: 1 },
    }))
    cleanup()
    vi.mocked(board.actionComposerProps.submitAction).mockClear()

    renderDock(board)
    const actions = within(openSection('Scene')).getByRole('group', { name: 'Wagon Strongbox actions' })
    expect(within(actions).getAllByRole('button').map((link) => link.textContent)).toEqual([
      'Search the strongbox',
      'Unlock the strongbox',
    ])
    const search = within(actions).getByRole('button', { name: 'Search the strongbox' })
    expect(search).toHaveAttribute('aria-disabled', 'true')
    expect(search).toHaveAttribute('aria-description', 'It is locked.')
    expect(search.closest('.story-tray-offer')).toHaveTextContent('Search the strongboxIt is locked.')
    fireEvent.click(search)
    expect(board.actionComposerProps.submitAction).not.toHaveBeenCalled()

    fireEvent.click(within(actions).getByRole('button', { name: 'Unlock the strongbox' }))
    const storySubmit = vi.mocked(board.actionComposerProps.submitAction)
    expect(storySubmit).toHaveBeenCalledWith('Unlock the strongbox.', expect.objectContaining({
      kind: 'object',
      object: { id: 'strongbox', action: 'unlock', revision: 4 },
    }))
    cleanup()

    // Table tools offers the same server actions and sends them the same way.
    const classicSubmit = vi.fn<(message: string, intent: ActionIntent) => boolean>(() => true)
    render(<GameplayControls state={board.gameplayControls} disabled={false} onSubmit={classicSubmit} />)
    fireEvent.click(within(screen.getByRole('group', { name: 'Scene interactions' })).getByRole('button', { name: 'Unlock the strongbox' }))
    expect(storySubmit.mock.calls[0][0]).toBe(classicSubmit.mock.calls[0][0])
    expect(withoutClientId(storySubmit.mock.calls[0][1])).toEqual(withoutClientId(classicSubmit.mock.calls[0][1]))
  })

  it('keeps an object’s choices in the Scene list, one that cannot be made dimmed with its reason', () => {
    const board = testBoard({
      gameplayControls: testControls({
        interactables: [{
          ...testControls().interactables[0],
          id: 'road-stone',
          name: 'The Last Road-Stone',
          states: [],
          offered: true,
          actions: [
            { id: 'use', label: 'Renew the ward', available: true, reason: '', phrase: true, authored: true, choice: true },
            { id: 'break', label: 'Break the ward', available: false, reason: 'The ward holds.', phrase: true, authored: true, choice: true },
          ],
        }],
      }),
    })
    renderDock(board)
    const suggestions = screen.getByRole('group', { name: 'Suggestions' })
    expect(within(suggestions).getByRole('button', { name: 'Renew the ward' })).toBeInTheDocument()
    expect(within(suggestions).queryByRole('button', { name: 'Break the ward' })).not.toBeInTheDocument()

    const actions = within(openSection('Scene')).getByRole('group', { name: 'The Last Road-Stone actions' })
    const breakWard = within(actions).getByRole('button', { name: 'Break the ward' })
    expect(breakWard).toHaveAttribute('aria-disabled', 'true')
    expect(breakWard.closest('.story-tray-offer')).toHaveTextContent('Break the wardThe ward holds.')
  })

  it('offers a failed check again as taking your time, which costs time and is sent with take_time', () => {
    const board = testBoard({
      gameplayControls: testControls({
        interactables: [{
          ...testControls().interactables[0],
          id: 'bench',
          name: "Driver's Bench",
          states: [],
          offered: true,
          actions: [{ id: 'search', label: "Search under the driver's bench", available: true, reason: '', phrase: true, authored: true, retry: true }],
        }],
      }),
    })
    renderDock(board)
    const actions = within(openSection('Scene')).getByRole('group', { name: "Driver's Bench actions" })
    const retry = within(actions).getByRole('button', { name: "Search under the driver's bench — take your time" })
    expect(retry.closest('.story-tray-offer')).toHaveTextContent("Search under the driver's bench — take your timecosts time")
    fireEvent.click(retry)
    expect(board.actionComposerProps.submitAction).toHaveBeenLastCalledWith("Search under the driver's bench — take your time.", {
      kind: 'object',
      source: 'scene_panel',
      text: "Search under the driver's bench — take your time.",
      client_message_id: expect.any(String),
      object: { id: 'bench', action: 'search', revision: 4 },
      take_time: true,
    })
  })

  it('offers only Examine for an object from an older server', () => {
    renderDock(testBoard())
    const actions = within(openSection('Scene')).getByRole('group', { name: 'Lead wagon actions' })
    expect(within(actions).getAllByRole('button').map((link) => link.textContent)).toEqual(['Examine Lead wagon'])
  })

  it('ends the Scene list with every way onward, as the Map tab lists them, and none in a fight', () => {
    const board = testBoard()
    renderDock(board)
    const scene = openSection('Scene')
    const onward = within(scene).getByRole('group', { name: 'Ways onward' })
    expect(scene.querySelector('.story-tray-content')?.lastElementChild).toBe(onward)
    fireEvent.click(within(onward).getByRole('button', { name: 'Go to North causeway' }))
    expect(board.actionComposerProps.submitAction).toHaveBeenLastCalledWith(
      'The party travels to North causeway.',
      expect.objectContaining({ kind: 'travel', location: { id: 'causeway', name: 'North causeway' } }),
    )
    cleanup()

    renderDock(testBoard({ streamingTurnActive: true }))
    const blocked = within(openSection('Scene')).getByRole('button', { name: 'Go to North causeway' })
    expect(blocked).toHaveAttribute('aria-disabled', 'true')
    expect(blocked).toHaveAccessibleDescription('Wait for the Dungeon Master to finish.')
    cleanup()

    renderDock(testBoard({ worldState: testWorld({ combat: testCombat() }) }))
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Actions' })).getByRole('button', { name: 'Scene' }))
    expect(screen.queryByRole('group', { name: 'Ways onward' })).not.toBeInTheDocument()
  })

  it('leaves the finishing blow out of the Combat list: it is told from the dock', () => {
    const [attack] = testCombat().legalActionBundles[0].actions
    const finisher = { ...attack, id: 'finisher.describe', type: 'finisher', label: 'Describe how you end it', requiresTarget: false, targets: [] }
    renderDock(testBoard({ worldState: testWorld({ combat: testCombat({ bundle: { actions: [finisher, attack] } }) }) }))
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
    const list = within(screen.getByRole('region', { name: 'Combat' })).getByRole('list', { name: 'Combat actions' })
    expect(within(list).queryByRole('button', { name: 'Describe how you end it' })).not.toBeInTheDocument()
    expect(within(list).getByRole('button', { name: 'Attack with Longsword' })).toBeInTheDocument()
  })

  it('opens on Scene outside combat, and holds every legal combat action in a Combat list during a fight', () => {
    renderDock(testBoard())
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
    expect(screen.getByRole('region', { name: 'Scene' })).toBeInTheDocument()
    cleanup()

    const combat = testCombat({
      bundle: {
        actions: [
          ...testCombat().legalActionBundles[0].actions,
          { ...testCombat().legalActionBundles[0].actions[1], id: 'combat.dodge', type: 'dodge', label: 'Dodge', available: true, message: 'Ysolde Marrow dodges.' },
        ],
      },
    })
    const board = testBoard({ worldState: testWorld({ combat }) })
    renderDock(board)
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
    const list = within(screen.getByRole('region', { name: 'Combat' })).getByRole('list', { name: 'Combat actions' })
    expect(within(list).getAllByRole('listitem').map((item) => item.querySelector('.story-tray-row-name')?.textContent)).toEqual([
      'Attack with Longsword',
      'Dodge',
    ])
    // The longsword has one legal target, so it is used on it at once.
    fireEvent.click(within(list).getByRole('button', { name: 'Attack with Longsword' }))
    expect(board.actionComposerProps.submitAction).toHaveBeenLastCalledWith(
      'Ysolde Marrow attacks Goblin Sentry with Longsword.',
      expect.objectContaining({ combat: { action_id: 'combat.attack.longsword', target_id: 'goblin_1' } }),
    )
  })

  it('asks for a target in the Combat list only among two or more, and names an attack that closes in', () => {
    const [attack, ...rest] = testCombat().legalActionBundles[0].actions
    const combat = testCombat({
      bundle: {
        actions: [
          {
            ...attack,
            requiresMove: true,
            targets: [
              ...attack.targets,
              { id: 'goblin_2', name: 'Goblin Scout', rangeBand: 'near', available: true, reason: '' },
            ],
          },
          ...rest,
        ],
      },
    })
    const board = testBoard({ worldState: testWorld({ combat }) })
    renderDock(board)
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
    const list = within(screen.getByRole('region', { name: 'Combat' })).getByRole('list', { name: 'Combat actions' })
    fireEvent.click(within(list).getByRole('button', { name: 'Close in and attack with Longsword' }))
    expect(board.actionComposerProps.submitAction).not.toHaveBeenCalled()
    const targets = within(list).getByRole('group', { name: 'Close in and attack with Longsword target' })
    expect(within(targets).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Goblin Sentry · Wounded',
      'Goblin Scout · near',
    ])
    fireEvent.click(within(targets).getByRole('button', { name: 'Goblin Scout · near' }))
    expect(board.actionComposerProps.submitAction).toHaveBeenLastCalledWith(
      'Ysolde Marrow closes in and attacks Goblin Scout with Longsword.',
      expect.objectContaining({ combat: { action_id: 'combat.attack.longsword', target_id: 'goblin_2' } }),
    )
  })

  it('casts at the one legal target without a picker, and uses an ability on its only target the same way', () => {
    const [missile] = testControls().spells
    const [layOnHands] = testControls().capabilities
    const board = testBoard({
      gameplayControls: testControls({
        spells: [{ ...missile, targets: missile.targets.map((target) => ({ ...target, available: target.id === 'goblin_1' })) }],
        capabilities: [{ ...layOnHands, targets: layOnHands.targets.slice(1) }],
      }),
    })
    renderDock(board)
    const tray = openSection('Magic')
    expect(within(tray).queryByRole('group', { name: 'Magic Missile targets' })).not.toBeInTheDocument()
    fireEvent.click(within(tray).getByRole('button', { name: 'Cast Magic Missile' }))
    expect(vi.mocked(board.actionComposerProps.submitAction).mock.calls[0]).toEqual([
      'Ysolde casts Magic Missile at Goblin.',
      expect.objectContaining({ spell: expect.objectContaining({ target_ids: ['goblin_1'] }) }),
    ])

    const abilities = openSection('Abilities')
    expect(within(abilities).queryByRole('group', { name: 'Lay on Hands target' })).not.toBeInTheDocument()
    fireEvent.click(within(abilities).getByRole('button', { name: 'Use Lay on Hands' }))
    expect(vi.mocked(board.actionComposerProps.submitAction).mock.calls[1][0]).toBe('Ysolde uses Lay on Hands on Brannoc for up to 1 hit points.')
  })

  it('takes a short rest spending the chosen hit dice, otherwise with the classic rest intent', async () => {
    const board = testBoard()
    renderDock(board)
    const tray = openSection('Scene')
    const rest = within(tray).getByRole('group', { name: 'Rest' })
    const stepper = within(rest).getByRole('group', { name: 'Hit dice to spend' })
    expect(stepper).toHaveTextContent('Spend0of 3 hit dice (d10)')
    expect(within(stepper).getByRole('button', { name: 'Spend one fewer hit die' })).toBeDisabled()
    fireEvent.click(within(stepper).getByRole('button', { name: 'Spend one more hit die' }))
    fireEvent.click(within(stepper).getByRole('button', { name: 'Spend one more hit die' }))
    expect(stepper).toHaveTextContent('Spend2of 3 hit dice (d10)')
    fireEvent.click(within(rest).getByRole('button', { name: 'Short rest' }))
    const storySubmit = vi.mocked(board.actionComposerProps.submitAction)
    cleanup()

    const classicBoard = testBoard()
    render(<SessionBoard {...classicBoard} />)
    fireEvent.click(screen.getByRole('button', { name: 'Show Current Situation details' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Take a short rest' }))
    const classicSubmit = vi.mocked(classicBoard.actionComposerProps.submitAction)

    expect(storySubmit.mock.calls[0][0]).toBe('Ysolde completes a short rest.')
    expect(storySubmit.mock.calls[0][0]).toBe(classicSubmit.mock.calls[0][0])
    expect(withoutClientId(storySubmit.mock.calls[0][1])).toEqual({
      ...withoutClientId(classicSubmit.mock.calls[0][1]),
      hit_dice: 2,
    })
  })

  it('suggests enough hit dice for the missing hit points and sends a long rest without any', () => {
    const board = testBoard({
      activePlayers: [{ id: VIEWER_ID, character_name: 'Ysolde', name: 'Danny', health: { tone: 'badly-wounded', label: 'Badly wounded', currentHp: 3, maxHp: 20 } }],
    })
    renderDock(board)
    const tray = openSection('Scene')
    const rest = within(tray).getByRole('group', { name: 'Rest' })
    expect(within(rest).getByRole('group', { name: 'Hit dice to spend' })).toHaveTextContent('Spend3of 3 hit dice (d10)')
    fireEvent.click(within(rest).getByRole('button', { name: 'Long rest' }))
    expect(board.actionComposerProps.submitAction).toHaveBeenCalledWith('Ysolde completes a long rest.', expect.objectContaining({
      kind: 'rest',
      rest_type: 'long_rest',
    }))
    expect(vi.mocked(board.actionComposerProps.submitAction).mock.calls[0][1]).not.toHaveProperty('hit_dice')
  })

  it('disables a rest the server would refuse and says why beneath it', () => {
    const board = testBoard({
      gameplayControls: testControls({
        rest: {
          shortAvailable: true,
          shortReason: '',
          longAvailable: false,
          longReason: "This place is too dangerous for a full night's rest.",
          hitDice: null,
        },
      }),
    })
    renderDock(board)
    const rest = within(openSection('Scene')).getByRole('group', { name: 'Rest' })

    expect(within(rest).queryByRole('group', { name: 'Hit dice to spend' })).not.toBeInTheDocument()
    expect(within(rest).getByRole('button', { name: 'Short rest' })).toBeEnabled()
    const longRest = within(rest).getByRole('button', { name: 'Long rest' })
    expect(longRest).toBeDisabled()
    expect(longRest.nextElementSibling).toHaveTextContent("This place is too dangerous for a full night's rest.")
    fireEvent.click(within(rest).getByRole('button', { name: 'Short rest' }))
    expect(vi.mocked(board.actionComposerProps.submitAction).mock.calls[0][1]).not.toHaveProperty('hit_dice')
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

  it.each([
    {
      name: 'a spellcaster with abilities and company',
      board: {},
      composer: {},
      tabs: ['Speak / act', 'Scene', 'Magic', 'Abilities', 'Items', 'Roll', 'Interact', 'Emote', 'Out of character'],
    },
    {
      name: 'a hero alone, without spells or abilities',
      board: {
        activePlayers: [{ id: VIEWER_ID, character_name: 'Ysolde', name: 'Danny' }],
        gameplayControls: testControls({ spells: [], narratedSpells: [], capabilities: [] }),
      },
      composer: { itemOptions: [], interactionTargets: [] },
      tabs: ['Speak / act', 'Scene', 'Items', 'Roll', 'Interact'],
    },
    {
      name: 'a fight, with a merchant open',
      board: { worldState: testWorld({ combat: testCombat(), merchants: [{ id: 'm', name: 'Mara', npcId: 'mara', buyRate: 0.5, stock: [] }] }) },
      composer: {},
      tabs: ['Speak / act', 'Combat', 'Scene', 'Magic', 'Abilities', 'Items', 'Trade', 'Roll', 'Interact', 'Emote', 'Out of character'],
    },
  ])('shows the tabs that fit: $name, never Director', ({ board, composer, tabs }) => {
    renderDock(testBoard(board, { canUseOperatorTools: true, ...composer }), true)
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
    const nav = within(screen.getByRole('dialog', { name: 'Actions' })).getByRole('navigation', { name: 'Kinds of action' })
    expect(within(nav).getAllByRole('button').map((tab) => tab.textContent)).toEqual(tabs)
  })

  it('says so when there is nothing to use or no one to turn to', () => {
    renderDock(testBoard({ gameplayControls: testControls({ items: [] }) }, { itemOptions: [], interactionTargets: [] }))
    expect(within(openSection('Items')).getByText('You carry nothing.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Interact' }))
    expect(screen.getByText('There is no one here to turn to.')).toBeInTheDocument()
  })

  it('closes on an outside click and returns focus to the actions button', () => {
    renderDock(testBoard())
    fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('dialog', { name: 'Actions' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Actions' })).toHaveFocus()
  })
})
