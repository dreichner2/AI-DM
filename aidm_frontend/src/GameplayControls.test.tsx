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
