// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CombatHud } from '../CombatHud'
import type { ActionIntent } from '../gameActions'
import type { CombatLegalAction, CombatStatePanel } from '../gameSelectors'
import type { GameplayControlState, GameplaySpellControl } from '../gameplayControlState'
import type { TimelineEntry } from '../types'
import { testCombat, testControls, VIEWER_ID } from './dockTestFixtures'
import { recentSpellNames } from './recentSpells'
import { StoryCombatRibbon, type RibbonTraySection } from './StoryCombatRibbon'

type Submit = (message?: string, intent?: ActionIntent) => boolean

function renderRibbon(
  combat: CombatStatePanel = testCombat(),
  {
    disabled = false,
    controls = testControls({ inCombat: true, spells: [], interactables: [] }),
    recent = [] as string[],
  }: { disabled?: boolean; controls?: GameplayControlState; recent?: string[] } = {},
) {
  const submitAction = vi.fn<Submit>(() => true)
  const onOpenTray = vi.fn<(section: RibbonTraySection, spellId?: string) => void>()
  render(
    <StoryCombatRibbon
      combat={combat}
      playerId={VIEWER_ID}
      activePlayers={[{ id: VIEWER_ID, character_name: 'Ysolde Marrow', name: 'Danny', profile_image: '/ysolde.png' }]}
      controls={controls}
      recentSpellNames={recent}
      disabled={disabled}
      submitAction={submitAction}
      onOpenTray={onOpenTray}
    />,
  )
  return { submitAction, onOpenTray }
}

const base = testCombat().legalActionBundles[0].actions[0]
const NO_COST = { action: 0, bonusAction: 0, movement: 0, reaction: 0 }

function action(id: string, type: string, label: string, overrides: Partial<CombatLegalAction> = {}): CombatLegalAction {
  return { ...base, id, type, label, requiresTarget: false, targets: [], cost: NO_COST, ...overrides }
}

function choiceTexts() {
  return [...screen.getByRole('group', { name: 'Combat actions' }).children].map((child) => child.textContent)
}

function spell(overrides: Partial<GameplaySpellControl>): GameplaySpellControl {
  return { ...testControls().spells[0], ...overrides }
}

afterEach(() => cleanup())

describe('StoryCombatRibbon', () => {
  it('shows the round and every combatant, allies first, with health and conditions', () => {
    renderRibbon()

    expect(screen.getByText('Round 2')).toBeInTheDocument()
    const combatants = within(screen.getByRole('list', { name: 'Combatants' })).getAllByRole('listitem')
    expect(combatants.map((item) => item.getAttribute('aria-label'))).toEqual([
      'Ysolde Marrow: Hurt',
      'Goblin Sentry: Wounded, frightened',
      'Distant Archer: Unhurt',
    ])
    expect(combatants[0]).toHaveAttribute('aria-current', 'true')
    expect(combatants[0]).toHaveAttribute('data-health', 'hurt')
    expect(combatants[0].querySelector('img')).toHaveAttribute('src', '/ysolde.png')
  })

  it('reflects the turn economy in the pips', () => {
    renderRibbon()
    const pips = within(screen.getByRole('list', { name: 'Turn resources' })).getAllByRole('listitem')
    expect(pips.map((pip) => pip.textContent)).toEqual([
      'Action ready',
      'Bonus spent',
      'Move ready',
      'Reaction ready',
    ])
    expect(pips[1]).toHaveAttribute('data-spent')
  })

  it('shows only what is legal now, nothing dimmed, and keeps the rest behind More…', () => {
    const { submitAction, onOpenTray } = renderRibbon()

    expect(screen.queryByRole('button', { name: /^Disengage/ })).not.toBeInTheDocument()
    expect(screen.getByRole('group', { name: 'Combat actions' }).querySelector('[data-blocked]')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'More…' }))
    expect(onOpenTray).toHaveBeenCalledWith('combat')
    expect(submitAction).not.toHaveBeenCalled()
  })

  it('lays out a fighter: two attacks, abilities, Advance, Fall back, the healing potion, More…', () => {
    renderRibbon(testCombat({
      bundle: {
        attacksRemaining: 2,
        actions: [
          action('combat.dodge', 'dodge', 'Dodge', { cost: { ...NO_COST, action: 1 } }),
          action('combat.advance', 'advance', 'Advance', { cost: { ...NO_COST, movement: 1 } }),
          action('item.use.potion', 'item', 'Use Potion of Healing', { cost: { ...NO_COST, bonusAction: 1 } }),
          action('class.use.second_wind', 'class_feature', 'Second Wind', { cost: { ...NO_COST, bonusAction: 1 } }),
          action('combat.attack.longsword', 'attack', 'Attack with Longsword'),
          action('combat.attack.handaxe', 'attack', 'Attack with Handaxe'),
          action('combat.attack.unarmed', 'attack', 'Unarmed strike'),
          action('class.cunning_action.dash', 'dash', 'Cunning Action: Dash', { cost: { ...NO_COST, bonusAction: 1 } }),
          action('combat.reposition', 'reposition', 'Fall back', { cost: { ...NO_COST, movement: 1 } }),
          action('combat.disengage', 'disengage', 'Disengage', { available: false, reason: 'No movement left.' }),
          action('combat.end_turn', 'end_turn', 'End turn'),
        ],
      },
    }))

    expect(within(screen.getByRole('list', { name: 'Turn resources' })).getByText(/Attacks ×2/)).toBeInTheDocument()
    expect(choiceTexts()).toEqual([
      'Attack with Longsword',
      'Attack with Handaxe',
      'Second WindB',
      'Cunning Action: DashB',
      'AdvanceM',
      'Fall backM',
      'Use Potion of HealingB',
      'More…',
    ])
    expect(screen.getByRole('button', { name: 'End turn' })).toBeInTheDocument()
  })

  it('lays out a wizard: their latest castable spells, Cast…, and never a rest-only feature', () => {
    const fireBolt = spell({
      id: 'spell_fire_bolt',
      name: 'Fire Bolt',
      level: 0,
      castLevels: [],
      minTargets: 1,
      maxTargets: 1,
      targets: [
        { id: 'goblin_1', name: 'Goblin Sentry', team: 'enemy', rangeBand: 'near', currentHp: null, maxHp: null, conditions: [], available: true, reason: '' },
      ],
    })
    const magicMissile = spell({ id: 'spell_magic_missile', name: 'Magic Missile' })
    const shield = spell({ id: 'spell_shield', name: 'Shield', available: false, reason: 'This reaction triggers automatically.' })
    const { submitAction, onOpenTray } = renderRibbon(
      testCombat({
        bundle: {
          actions: [
            action('combat.attack.quarterstaff', 'attack', 'Attack with Quarterstaff'),
            action('class.use.arcane_recovery', 'class_feature', 'Arcane Recovery', { capabilityEffect: 'restore_slots' }),
            action('combat.advance', 'advance', 'Advance'),
            action('combat.end_turn', 'end_turn', 'End turn'),
          ],
        },
      }),
      {
        controls: testControls({ inCombat: true, spells: [fireBolt, magicMissile, shield], interactables: [] }),
        recent: ['Shield', 'Magic Missile', 'Fire Bolt'],
      },
    )

    expect(choiceTexts()).toEqual([
      'Attack with Quarterstaff',
      'Magic MissileA',
      'Fire BoltA',
      'Cast…',
      'Advance',
      'More…',
    ])

    fireEvent.click(screen.getByRole('button', { name: 'Cast…' }))
    expect(onOpenTray).toHaveBeenLastCalledWith('magic')
    fireEvent.click(screen.getByRole('button', { name: 'Magic Missile' }))
    expect(onOpenTray).toHaveBeenLastCalledWith('magic', 'spell_magic_missile')

    fireEvent.click(screen.getByRole('button', { name: 'Fire Bolt' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Targets for Fire Bolt' })).getByRole('button', { name: 'Goblin Sentry · Wounded' }))
    expect(submitAction).toHaveBeenCalledWith('Ysolde casts Fire Bolt at Goblin Sentry.', expect.objectContaining({
      kind: 'spell',
      spell: expect.objectContaining({ name: 'Fire Bolt', target_ids: ['goblin_1'] }),
    }))
  })

  it('offers a story action the server marks legal in the fight, after the abilities, as a typed object action', () => {
    const controls = testControls({
      inCombat: true,
      spells: [],
      interactables: [{
        id: 'cairn_banner_stone',
        name: 'Banner-stone',
        kind: 'object',
        description: '',
        revision: 2,
        states: [],
        available: true,
        reason: '',
        offered: true,
        actions: [
          { id: 'inspect', label: 'Examine Banner-stone', available: true, reason: '', phrase: true },
          { id: 'use', label: 'Speak the names at the banner-stone', available: true, reason: '', phrase: true, authored: true },
        ],
      }],
    })
    const { submitAction } = renderRibbon(
      testCombat({
        bundle: {
          actions: [
            action('class.use.second_wind', 'class_feature', 'Second Wind'),
            action('combat.advance', 'advance', 'Advance'),
            action('combat.end_turn', 'end_turn', 'End turn'),
          ],
        },
      }),
      { controls },
    )

    expect(choiceTexts()).toEqual(['Second Wind', 'Speak the names at the banner-stone', 'Advance', 'More…'])
    fireEvent.click(screen.getByRole('button', { name: 'Speak the names at the banner-stone' }))
    expect(submitAction).toHaveBeenCalledWith('Speak the names at the banner-stone.', expect.objectContaining({
      kind: 'object',
      object: { id: 'cairn_banner_stone', action: 'use', revision: 2 },
    }))
  })

  it('submits the same message and intent as CombatHud for the same target, naming targets by health', () => {
    const { submitAction: ribbonSubmit } = renderRibbon()
    fireEvent.click(screen.getByRole('button', { name: 'Attack with Longsword' }))

    const targets = screen.getByRole('group', { name: 'Targets for Attack with Longsword' })
    const archer = within(targets).getByRole('button', { name: /^Distant Archer · Unhurt/ })
    expect(archer).toHaveAccessibleDescription('Target is at far range.')
    fireEvent.click(archer)
    expect(ribbonSubmit).not.toHaveBeenCalled()

    fireEvent.click(within(targets).getByRole('button', { name: 'Goblin Sentry · Wounded' }))
    cleanup()

    const hudSubmit = vi.fn<Submit>(() => true)
    render(<CombatHud combat={testCombat()} playerId={VIEWER_ID} disabled={false} submitAction={hudSubmit} />)
    fireEvent.click(screen.getByRole('button', { name: 'Attack with Longsword, target Goblin Sentry' }))

    const [ribbonMessage, ribbonIntent] = ribbonSubmit.mock.calls[0]
    const [hudMessage, hudIntent] = hudSubmit.mock.calls[0]
    expect(ribbonMessage).toBe(hudMessage)
    expect(ribbonIntent).toEqual({ ...hudIntent, client_message_id: expect.stringMatching(/^combat-hud-/) })
    expect(ribbonIntent?.combat).toEqual({ action_id: 'combat.attack.longsword', target_id: 'goblin_1' })
  })

  it('names a target by exact hit points only when the server sends them', () => {
    const combat = testCombat()
    combat.enemies[0] = { ...combat.enemies[0], hp: { current: 7, max: 11 } }
    renderRibbon(combat)
    fireEvent.click(screen.getByRole('button', { name: 'Attack with Longsword' }))
    expect(screen.getByRole('button', { name: 'Goblin Sentry · 7/11' })).toBeInTheDocument()
  })

  it('names a targeted ability that is not an attack by its label', () => {
    const { submitAction } = renderRibbon(testCombat({
      bundle: {
        actions: [action('class.use.lay_on_hands', 'class_feature', 'Lay on Hands', {
          requiresTarget: true,
          targets: [{ id: 'player_31', name: 'Brannoc', rangeBand: 'near', available: true, reason: '' }],
        })],
      },
    }))
    fireEvent.click(screen.getByRole('button', { name: 'Lay on Hands' }))
    fireEvent.click(screen.getByRole('button', { name: 'Brannoc · near' }))
    expect(submitAction).toHaveBeenCalledWith('Ysolde Marrow: Lay on Hands on Brannoc.', expect.objectContaining({
      combat: { action_id: 'class.use.lay_on_hands', target_id: 'player_31' },
    }))
  })

  it('goes back from targets to actions', () => {
    renderRibbon()
    fireEvent.click(screen.getByRole('button', { name: 'Attack with Longsword' }))
    fireEvent.click(screen.getByRole('button', { name: 'Back' }))
    expect(screen.getByRole('group', { name: 'Combat actions' })).toBeInTheDocument()
  })

  it('ends the turn as a quiet choice, or as the primary action once the action is spent', () => {
    const { submitAction } = renderRibbon()
    const endTurn = screen.getByRole('button', { name: 'End turn' })
    expect(endTurn).toHaveClass('story-chip')

    fireEvent.click(endTurn)
    expect(submitAction).toHaveBeenCalledWith('Ysolde Marrow ends their turn.', expect.objectContaining({
      kind: 'combat',
      source: 'combat_hud',
      combat: { action_id: 'combat.end_turn' },
    }))
    cleanup()

    renderRibbon(testCombat({ bundle: { actionRemaining: 0 } }))
    expect(screen.getByRole('button', { name: 'End turn' })).toHaveClass('story-primary')
  })

  it('blocks every choice while the current turn resolves', () => {
    const { submitAction } = renderRibbon(testCombat(), { disabled: true })
    fireEvent.click(screen.getByRole('button', { name: 'End turn' }))
    fireEvent.click(screen.getByRole('button', { name: 'Attack with Longsword' }))
    expect(submitAction).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('Actions are unavailable while the current turn resolves.')
  })

  it('says who is acting when it is not the viewer’s turn, and shows at most two telegraphs', () => {
    renderRibbon(testCombat({ current: false }))

    expect(screen.getByText('Goblin Sentry is acting')).toBeInTheDocument()
    expect(screen.queryByRole('group', { name: 'Combat actions' })).not.toBeInTheDocument()
    expect(screen.getByText(/^Watch for:/)).toHaveTextContent(
      'Watch for: The goblin glances toward the tunnel mouth. · The archer nocks an arrow.',
    )
  })

  it('orders the combatants by initiative, with range and health under each enemy, and links the viewer to its melee foes', () => {
    const combat = testCombat()
    combat.allies[0] = { ...combat.allies[0], rangeBand: 'melee', engagedWith: ['goblin_1'] }
    combat.enemies[0] = { ...combat.enemies[0], rangeBand: 'melee', engagedWith: ['player_30'], hp: { current: 4, max: 7 } }
    combat.enemies[1] = { ...combat.enemies[1], rangeBand: 'distant' }
    combat.turnOrder = [
      { id: 'goblin_1', name: 'Goblin Sentry', team: 'enemy', isCurrent: false },
      { id: 'player_30', name: 'Ysolde Marrow', team: 'player', isCurrent: true },
    ]
    renderRibbon(combat)

    const combatants = within(screen.getByRole('list', { name: 'Combatants' })).getAllByRole('listitem')
    expect(combatants.map((item) => item.getAttribute('data-combatant-id'))).toEqual(['goblin_1', 'player_30', 'archer_1'])
    expect(combatants.map((item) => item.querySelector('.story-combatant-range')?.textContent)).toEqual([
      'melee · Wounded · 4/7',
      'melee',
      'distant · Unhurt',
    ])
    expect(document.querySelector('.story-combatant-divider')).toBeNull()
    expect([...document.querySelectorAll('.story-engagements path')].map((path) => path.getAttribute('data-target'))).toEqual(['goblin_1'])
  })

  it('shows a dying ally without colour, with its death-save pips and the word Dying', () => {
    const combat = testCombat()
    combat.allies.push({
      ...combat.allies[0],
      id: 'player_31',
      name: 'Brannoc',
      health: 'Down',
      healthTone: 'down',
      conditions: ['unconscious', 'dying'],
      deathSaves: { successes: 1, failures: 2 },
    })
    renderRibbon(combat)
    const brannoc = within(screen.getByRole('list', { name: 'Combatants' })).getAllByRole('listitem')[1]
    expect(brannoc).toHaveAttribute('data-vitality', 'dying')
    expect(within(brannoc).getByRole('img', { name: 'Dying: 1 success, 2 failures' })).toBeInTheDocument()
    expect(brannoc.querySelectorAll('.story-vitality-pips[data-outcome="success"] i[data-filled]')).toHaveLength(1)
    expect(brannoc.querySelectorAll('.story-vitality-pips[data-outcome="failure"] i[data-filled]')).toHaveLength(2)
    expect(brannoc.querySelector('.story-combatant-conditions')).toBeNull()
  })

  it('renders nothing without a bundle for the viewer', () => {
    const { container } = render(
      <StoryCombatRibbon
        combat={testCombat()}
        playerId={99}
        activePlayers={[]}
        controls={testControls()}
        recentSpellNames={[]}
        disabled={false}
        submitAction={vi.fn()}
        onOpenTray={vi.fn()}
      />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})

describe('recentSpellNames', () => {
  const cast = (id: string, turnId: number | null, name: string, speaker = 'Ysolde'): TimelineEntry => ({
    id,
    role: 'player',
    speaker,
    text: `${speaker} casts ${name}.`,
    timestamp: null,
    metadata: { ...(turnId === null ? {} : { turn_id: turnId }), action_intent: { kind: 'spell', spell: { name } } },
  })
  const fightStarts = (turnId: number): TimelineEntry => ({
    id: `state-${turnId}`,
    role: 'system',
    speaker: 'System',
    text: '',
    timestamp: null,
    metadata: { turn_id: turnId, state_log: { lines: [{ changeType: 'combat.start', message: 'Combat begins.' }] } },
  })

  it('lists the hero’s casts in this fight, newest first, including one still on its way', () => {
    const entries = [
      cast('a', 2, 'Sleep'),
      fightStarts(4),
      cast('b', 4, 'Fire Bolt'),
      cast('c', 5, 'Magic Missile', 'Brannoc'),
      cast('d', 6, 'Magic Missile'),
      cast('e', 7, 'Fire Bolt'),
      cast('f', null, 'Ray of Frost'),
    ]
    expect(recentSpellNames(entries, 'Ysolde')).toEqual(['Ray of Frost', 'Fire Bolt', 'Magic Missile'])
  })

  it('counts every loaded turn when the fight’s start is not loaded', () => {
    expect(recentSpellNames([cast('a', 2, 'Sleep'), cast('b', 3, 'Fire Bolt')], 'Ysolde')).toEqual(['Fire Bolt', 'Sleep'])
  })
})
