// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CombatHud } from './CombatHud'
import type { ActionIntent } from './gameActions'
import type { CombatStatePanel } from './gameSelectors'

function combatState({ current = true }: { current?: boolean } = {}): CombatStatePanel {
  return {
    active: true,
    status: 'active',
    round: '2',
    battlefield: 'dim dungeon room',
    goal: 'Stop the sentries',
    creatureSource: 'core',
    resolverMethod: 'campaign pack',
    tacticalLevel: 'normal',
    endReason: '',
    combatStartedBy: 'player',
    enemyGroupSummary: '2 x sentry',
    initiativeRequired: false,
    debugEnabled: false,
    enemies: [],
    allies: [],
    telegraphs: [],
    legalActionBundles: [
      {
        schemaVersion: 1,
        playerId: 30,
        actorId: 'player_30',
        actorName: 'Ember',
        round: 2,
        currentActorId: current ? 'player_30' : 'enemy_goblin_1',
        currentActorName: current ? 'Ember' : 'Goblin Sentry',
        isCurrentActor: current,
        economyTracking: 'turn_order_derived',
        subTurnCountersTracked: false,
        actions: [
          {
            id: 'combat.attack.blade',
            type: 'attack',
            label: 'Attack with Longsword',
            description: 'Make one server-rolled weapon attack against a legal target.',
            message: '',
            available: current,
            reason: current ? '' : 'Goblin Sentry is acting now.',
            requiresTarget: true,
            authoritative: true,
            economy: {
              action: 1,
              movement: 'optional',
              endsTurn: true,
              tracking: 'turn_order_derived',
              reactionTracked: false,
              subTurnCountersTracked: false,
            },
            targets: [
              {
                id: 'enemy_goblin_1',
                name: 'Goblin Sentry',
                rangeBand: 'near',
                available: true,
                reason: '',
              },
              {
                id: 'enemy_archer_1',
                name: 'Distant Archer',
                rangeBand: 'far',
                available: false,
                reason: 'Target is at far range.',
              },
            ],
            rangeClassification: 'melee',
            allowedRangeBands: ['melee', 'near'],
            weaponName: 'Longsword',
          },
        ],
      },
    ],
  }
}

afterEach(() => cleanup())

describe('CombatHud', () => {
  it('submits only the server-issued action and target IDs', () => {
    const submitAction = vi.fn<(message?: string, intent?: ActionIntent) => boolean>(() => true)
    render(<CombatHud combat={combatState()} playerId={30} disabled={false} submitAction={submitAction} />)

    expect(screen.getByText('Your turn')).toBeInTheDocument()
    expect(screen.getByText(/Goblin Sentry · near · melee range · 1 action · movement optional · ends turn/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Distant Archer/ })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Attack with Longsword Goblin Sentry/ }))

    expect(submitAction).toHaveBeenCalledTimes(1)
    const [message, intent] = submitAction.mock.calls[0] ?? []
    expect(message).toBe('Ember attacks Goblin Sentry with Longsword.')
    expect(intent).toMatchObject({
      kind: 'combat',
      source: 'combat_hud',
      text: 'Ember attacks Goblin Sentry with Longsword.',
      combat: {
        action_id: 'combat.attack.blade',
        target_id: 'enemy_goblin_1',
      },
    })
    expect(intent?.client_message_id).toMatch(/^combat-hud-/)
  })

  it('shows whose turn it is and blocks unavailable actions', () => {
    const submitAction = vi.fn(() => true)
    render(<CombatHud combat={combatState({ current: false })} playerId={30} disabled={false} submitAction={submitAction} />)

    expect(screen.getByText('Goblin Sentry is acting')).toBeInTheDocument()
    expect(screen.getByRole('button', {
      name: /Attack with Longsword Goblin Sentry .* Goblin Sentry is acting now\./,
    })).toBeDisabled()
    expect(screen.getByText(/Goblin Sentry is acting now\./)).toBeVisible()
  })

  it('does not expose another player bundle', () => {
    const submitAction = vi.fn(() => true)
    const { container } = render(
      <CombatHud combat={combatState()} playerId={99} disabled={false} submitAction={submitAction} />,
    )

    expect(container).toBeEmptyDOMElement()
  })
})
