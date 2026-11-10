// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { GameplayControls } from './GameplayControls'
import type { GameplayControlState } from './gameplayControlState'

function controlState(): GameplayControlState {
  return {
    actorId: 'player_30',
    actorName: 'Ilyra',
    inCombat: true,
    activeActorId: 'player_30',
    isActorTurn: true,
    actionRemaining: 1,
    bonusActionRemaining: 1,
    concentration: '',
    spells: [
      {
        id: 'spell_magic_missile',
        name: 'Magic Missile',
        level: 1,
        description: 'Reliable force darts.',
        delivery: 'automatic',
        castingTime: 'action',
        castLevels: [1],
        effectLabel: 'damage · force',
        relation: 'enemy',
        rangeBands: ['near', 'far', 'distant'],
        minTargets: 1,
        maxTargets: 1,
        concentration: false,
        prepared: true,
        resourceLabel: 'Level 1 slot · 1 left',
        available: true,
        reason: '',
        targets: [
          {
            id: 'player_30',
            name: 'Ilyra',
            team: 'player',
            rangeBand: 'near',
            currentHp: 7,
            maxHp: 14,
            conditions: [],
            available: false,
            reason: 'Requires a hostile target.',
          },
          {
            id: 'enemy_goblin_1',
            name: 'Goblin',
            team: 'enemy',
            rangeBand: 'near',
            currentHp: 8,
            maxHp: 8,
            conditions: [],
            available: true,
            reason: '',
          },
        ],
      },
    ],
    capabilities: [
      {
        id: 'lay_on_hands',
        name: 'Lay on Hands',
        description: 'Spend a healing pool.',
        actionEconomy: 'action',
        targetPolicy: 'self_or_ally',
        effectType: 'healing_pool',
        current: 5,
        max: 10,
        refreshesOn: 'long rest',
        available: true,
        reason: '',
        targets: [
          {
            id: 'player_30',
            name: 'Ilyra',
            team: 'player',
            rangeBand: 'near',
            currentHp: 7,
            maxHp: 14,
            conditions: [],
            available: true,
            reason: '',
          },
          {
            id: 'player_31',
            name: 'Borin',
            team: 'player',
            rangeBand: 'near',
            currentHp: 3,
            maxHp: 12,
            conditions: [],
            available: true,
            reason: '',
          },
        ],
      },
    ],
    interactables: [
      {
        id: 'moon_shrine',
        name: 'Moon Shrine',
        kind: 'object',
        description: 'A pale stone shrine.',
        revision: 4,
        states: ['1 uses left'],
        available: true,
        reason: '',
        actions: [{ id: 'use', label: 'Use', available: true, reason: '' }],
      },
    ],
  }
}

afterEach(() => cleanup())

describe('GameplayControls', () => {
  it('sends the selected cast level with an exact-target spell', () => {
    const state = controlState()
    state.spells[0].castLevels = [1, 2, 3]
    const onSubmit = vi.fn(() => true)
    render(<GameplayControls state={state} disabled={false} onSubmit={onSubmit} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Cast level' }), { target: { value: '3' } })
    fireEvent.click(screen.getByRole('checkbox', { name: 'Target Goblin' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cast Magic Missile' }))
    expect(onSubmit).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      spell: expect.objectContaining({ cast_level: 3, target_ids: ['enemy_goblin_1'] }),
    }))
  })

  it('labels an active repeat attack and sends it without a cast level', () => {
    const state = controlState()
    Object.assign(state.spells[0], { name: 'Spiritual Weapon', castLevels: [], repeatAttack: true })
    const onSubmit = vi.fn(() => true)
    render(<GameplayControls state={state} disabled={false} onSubmit={onSubmit} />)
    expect(screen.getByRole('option', { name: 'Spiritual Weapon · attack again' })).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Cast level' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: 'Target Goblin' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cast Spiritual Weapon' }))
    expect(onSubmit).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      spell: expect.not.objectContaining({ cast_level: expect.anything() }),
    }))
  })

  it('permits the additional target granted by an upcast dart spell', () => {
    const state = controlState()
    const spell = state.spells[0]
    spell.maxTargets = 3
    spell.castLevels = [1, 2]
    spell.upcastTargetsPerLevel = 1
    const example = spell.targets.find((target) => target.available)!
    spell.targets = [1, 2, 3, 4].map((number) => ({ ...example, id: `enemy_${number}`, name: `Goblin ${number}` }))
    const onSubmit = vi.fn(() => true)
    render(<GameplayControls state={state} disabled={false} onSubmit={onSubmit} />)
    fireEvent.change(screen.getByRole('combobox', { name: 'Cast level' }), { target: { value: '2' } })
    for (let number = 1; number <= 4; number += 1) fireEvent.click(screen.getByRole('checkbox', { name: `Target Goblin ${number}` }))
    fireEvent.click(screen.getByRole('button', { name: 'Cast Magic Missile' }))
    expect(onSubmit).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({
      spell: expect.objectContaining({ cast_level: 2, target_ids: ['enemy_1', 'enemy_2', 'enemy_3', 'enemy_4'] }),
    }))
  })

  it('submits exact-target spell, capability, and revision-bound object intents', () => {
    const onSubmit = vi.fn(() => true)
    render(<GameplayControls state={controlState()} disabled={false} onSubmit={onSubmit} />)

    fireEvent.click(screen.getByRole('checkbox', { name: 'Target Goblin' }))
    fireEvent.click(screen.getByRole('button', { name: 'Cast Magic Missile' }))
    expect(onSubmit).toHaveBeenNthCalledWith(
      1,
      'Ilyra casts Magic Missile at Goblin.',
      expect.objectContaining({
        kind: 'spell',
        source: 'scene_panel',
        client_message_id: expect.any(String),
        spell: expect.objectContaining({
          name: 'Magic Missile',
          resource_pool: 'auto',
          target_ids: ['enemy_goblin_1'],
        }),
      }),
    )

    const capabilities = screen.getByRole('group', { name: 'Class capabilities' })
    fireEvent.change(within(capabilities).getByRole('combobox', { name: 'Lay on Hands target' }), {
      target: { value: 'player_31' },
    })
    fireEvent.change(within(capabilities).getByRole('spinbutton', { name: 'Lay on Hands healing amount' }), {
      target: { value: '3' },
    })
    fireEvent.click(within(capabilities).getByRole('button', { name: 'Use Lay on Hands' }))
    expect(onSubmit).toHaveBeenNthCalledWith(
      2,
      'Ilyra uses Lay on Hands on Borin for up to 3 hit points.',
      expect.objectContaining({
        kind: 'capability',
        capability: { id: 'lay_on_hands', target_id: 'player_31', amount: 3 },
      }),
    )

    const interactions = screen.getByRole('group', { name: 'Scene interactions' })
    fireEvent.click(within(interactions).getByRole('button', { name: 'Use' }))
    expect(onSubmit).toHaveBeenNthCalledWith(
      3,
      'Ilyra attempts to use Moon Shrine.',
      expect.objectContaining({
        kind: 'object',
        object: { id: 'moon_shrine', action: 'use', revision: 4 },
      }),
    )
  })

  it('disables exhausted or off-turn actions from the current snapshot', () => {
    const state = controlState()
    state.isActorTurn = false
    state.spells[0] = {
      ...state.spells[0],
      available: false,
      reason: 'Only the active combat actor may cast.',
      resourceLabel: 'No level 1+ resource remains',
    }
    state.capabilities[0] = {
      ...state.capabilities[0],
      current: 0,
      available: false,
      reason: 'No uses remain.',
    }

    render(<GameplayControls state={state} disabled={false} onSubmit={vi.fn(() => true)} />)

    expect(screen.getByRole('button', { name: 'Cast Magic Missile' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Use Lay on Hands' })).toBeDisabled()
    expect(screen.getByText('Only the active combat actor may cast.')).toBeInTheDocument()
    expect(screen.getByText('No uses remain.')).toBeInTheDocument()
  })
})
