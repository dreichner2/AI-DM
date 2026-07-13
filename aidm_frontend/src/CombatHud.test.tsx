// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CombatHud } from './CombatHud'
import type { ActionIntent } from './gameActions'
import type { CombatParticipantSummary, CombatStatePanel } from './gameSelectors'

function participant(overrides: Partial<CombatParticipantSummary>): CombatParticipantSummary {
  return {
    id: 'combatant',
    name: 'Combatant',
    team: 'enemy',
    kind: 'creature',
    source: '',
    health: 'Unhurt',
    healthTone: 'healthy',
    conditions: [],
    morale: '—',
    moraleEvents: [],
    intent: '',
    telegraph: '',
    tacticSource: '',
    brainSource: '',
    position: '',
    selectionScore: '',
    selectionMethod: '',
    ...overrides,
  }
}

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
    enemies: [
      participant({
        id: 'enemy_goblin_1',
        name: 'Goblin Sentry',
        health: 'Wounded',
        healthTone: 'hurt',
        conditions: ['frightened'],
        intent: 'retreat',
        telegraph: 'The goblin glances toward the tunnel mouth.',
      }),
      participant({
        id: 'enemy_archer_1',
        name: 'Distant Archer',
        health: 'Hurt',
        healthTone: 'healthy',
      }),
    ],
    allies: [
      participant({
        id: 'player_30',
        name: 'Ember',
        team: 'player',
        kind: 'player_character',
        health: 'Unhurt',
      }),
    ],
    telegraphs: ['The goblin glances toward the tunnel mouth.'],
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
  it('submits only the authoritative action and target IDs', () => {
    const submitAction = vi.fn<(message?: string, intent?: ActionIntent) => boolean>(() => true)
    render(<CombatHud combat={combatState()} playerId={30} disabled={false} submitAction={submitAction} />)

    expect(screen.getByRole('status')).toHaveTextContent('Your turn')
    expect(screen.getByRole('heading', { name: 'Combat · Round 2' })).toBeInTheDocument()
    expect(screen.getByText(/Choose an action and, when needed, a target/)).toBeInTheDocument()
    expect(screen.queryByText(/server-issued|server-rolled|sub-turn counters/i)).not.toBeInTheDocument()
    const allies = screen.getByRole('region', { name: 'Allies in combat' })
    expect(allies).toHaveTextContent('Ember')
    expect(allies).toHaveTextContent('HP: Unhurt')
    expect(allies).toHaveTextContent('No conditions')
    const enemies = screen.getByRole('region', { name: 'Enemies in combat' })
    expect(enemies).toHaveTextContent('Goblin Sentry')
    expect(enemies).toHaveTextContent('HP: Wounded')
    expect(enemies).toHaveTextContent('Conditions: frightened')
    const intentions = screen.getByRole('region', { name: 'Visible enemy intentions' })
    expect(intentions).toHaveTextContent('The goblin glances toward the tunnel mouth.')
    expect(intentions).toHaveTextContent('Intent: retreat')
    expect(screen.getByText(/near · melee range · 1 action · movement optional · ends turn/)).toBeInTheDocument()
    expect(screen.getAllByText(
      'Make one weapon attack against an available target. The roll is handled automatically.',
    )).toHaveLength(2)

    const unavailableTarget = screen.getByRole('button', {
      name: 'Attack with Longsword, target Distant Archer',
    })
    expect(unavailableTarget).toBeDisabled()
    expect(unavailableTarget).toHaveAccessibleDescription(/Target is at far range\./)
    expect(screen.getByText('Unavailable: Target is at far range.')).toBeVisible()
    fireEvent.click(unavailableTarget)
    expect(submitAction).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Attack with Longsword, target Goblin Sentry' }))

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

    expect(screen.getByRole('status')).toHaveTextContent('Goblin Sentry is acting')
    expect(screen.getByText('Wait for Goblin Sentry to finish. Your choices remain visible below.')).toBeVisible()
    const action = screen.getByRole('button', {
      name: 'Attack with Longsword, target Goblin Sentry',
    })
    expect(action).toBeDisabled()
    expect(action).toHaveAccessibleDescription(/Goblin Sentry is acting now\./)
    expect(screen.getAllByText(/Unavailable: Goblin Sentry is acting now\./)).not.toHaveLength(0)
  })

  it('marks a resolving combat panel so compact layouts prioritize its waiting guidance', () => {
    render(<CombatHud combat={combatState()} playerId={30} disabled submitAction={vi.fn(() => true)} />)

    const combat = screen.getByRole('region', { name: 'Combat · Round 2' })
    expect(combat).toHaveClass('is-disabled')
    expect(combat).toHaveTextContent('Your combat choices are paused while the current turn finishes.')
  })

  it('keeps long combat choices and rosters discoverable in a mobile-friendly scroll structure', () => {
    const combat = combatState()
    const bundle = combat.legalActionBundles[0]
    const baseAction = bundle.actions[0]
    bundle.actions = Array.from({ length: 5 }, (_, index) => ({
      ...baseAction,
      id: `combat.action.${index}`,
      label: `Combat choice ${index + 1}`,
      message: `Use combat choice ${index + 1}.`,
      requiresTarget: false,
      targets: [],
    }))
    combat.enemies = Array.from({ length: 8 }, (_, index) => participant({
      id: `enemy-${index}`,
      name: `Enemy ${index + 1}`,
      health: index > 5 ? 'Critical' : 'Hurt',
      healthTone: index > 5 ? 'critical' : 'healthy',
    }))

    render(<CombatHud combat={combat} playerId={30} disabled={false} submitAction={vi.fn(() => true)} />)

    const choices = screen.getByRole('group', { name: 'Combat action choices' })
    expect(choices).toHaveAttribute('tabindex', '0')
    expect(screen.getByText('Scroll to see all 5 choices')).toBeVisible()
    expect(within(choices).getAllByRole('button')).toHaveLength(5)
    expect(within(screen.getByRole('region', { name: 'Enemies in combat' })).getAllByRole('listitem'))
      .toHaveLength(8)
  })

  it('does not expose another player bundle', () => {
    const submitAction = vi.fn(() => true)
    const { container } = render(
      <CombatHud combat={combatState()} playerId={99} disabled={false} submitAction={submitAction} />,
    )

    expect(container).toBeEmptyDOMElement()
  })
})
