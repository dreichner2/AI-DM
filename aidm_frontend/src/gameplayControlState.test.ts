import { describe, expect, it } from 'vitest'
import { gameplayControlsFromSnapshot } from './gameplayControlState'

function snapshot() {
  return {
    playerCharacters: [
      {
        id: 'player_30',
        playerId: 30,
        name: 'Ilyra',
        class: 'Wizard',
        level: 3,
        health: { currentHp: 5, maxHp: 14 },
        spellbook: {
          preparationPolicy: { requiresPreparation: true },
          preparedSpells: ['Magic Missile'],
          knownSpells: [
            {
              id: 'spell_fire_bolt',
              name: 'Fire Bolt',
              level: 0,
              authoritativeEffect: true,
              delivery: { type: 'attack' },
              target: { relation: 'enemy', rangeBands: ['near', 'far'], maxTargets: 1 },
              effects: [{ kind: 'damage', dice: '1d10', damageType: 'fire' }],
            },
            {
              id: 'spell_magic_missile',
              name: 'Magic Missile',
              level: 1,
              authoritativeEffect: true,
              delivery: { type: 'automatic' },
              target: {
                relation: 'enemy',
                rangeBands: ['near', 'far', 'distant'],
                maxTargets: 1,
                ignoreCover: true,
              },
              effects: [{ kind: 'damage', dice: '3d4+3', damageType: 'force' }],
            },
            {
              id: 'spell_cure_wounds',
              name: 'Cure Wounds',
              level: 1,
              authoritativeEffect: true,
              delivery: { type: 'automatic' },
              target: { relation: 'ally', rangeBands: ['melee'], maxTargets: 1, allowSelf: true },
              effects: [{ kind: 'healing', dice: '1d8' }],
            },
          ],
        },
        spellResources: {
          slots: { '1': { current: 1, max: 4 } },
          pactSlots: { current: 0, max: 0, slotLevel: 0 },
          mysticArcanum: {},
          concentration: { active: true, spellName: 'Entangle' },
        },
        classFeatures: [
          {
            id: 'second_wind',
            name: 'Second Wind',
            actionEconomy: 'bonus_action',
            targetPolicy: 'self',
            refreshesOn: 'short_rest',
            effect: { type: 'heal' },
          },
          {
            id: 'action_surge',
            name: 'Action Surge',
            actionEconomy: 'free',
            targetPolicy: 'self',
            refreshesOn: 'short_rest',
            effect: { type: 'restore_action' },
          },
        ],
        classFeatureState: {
          second_wind: { current: 1, max: 1, refreshesOn: 'short_rest' },
          action_surge: { current: 1, max: 1, refreshesOn: 'short_rest' },
        },
      },
      { id: 'player_31', playerId: 31, name: 'Borin' },
    ],
    currentScene: {
      locationId: 'moon_crypt',
      interactables: [
        {
          id: 'moon_shrine',
          name: 'Moon Shrine',
          kind: 'object',
          description: 'A pale stone shrine.',
          used: false,
          depleted: false,
          usesRemaining: 1,
          revision: 4,
        },
        {
          id: 'iron_gate',
          name: 'Iron Gate',
          kind: 'door',
          open: false,
          locked: false,
          broken: false,
          revision: 2,
        },
      ],
      hazards: [
        {
          id: 'moon_ward',
          name: 'Moon Ward',
          kind: 'hazard',
          active: true,
          triggered: false,
          disarmed: false,
          revision: 1,
        },
      ],
    },
    combat: {
      status: 'active',
      round: 2,
      participants: [
        {
          id: 'player_30',
          playerId: 30,
          name: 'Ilyra',
          team: 'player',
          hp: { current: 5, max: 14 },
          conditions: [],
          position: { rangeBand: 'near', zoneId: 'crypt' },
          isAlive: true,
          isConscious: true,
        },
        {
          id: 'player_31',
          playerId: 31,
          name: 'Borin',
          team: 'player',
          hp: { current: 10, max: 10 },
          position: { rangeBand: 'near', zoneId: 'crypt' },
        },
        {
          id: 'enemy_goblin',
          name: 'Goblin',
          team: 'enemy',
          hp: { current: 8, max: 8 },
          position: { rangeBand: 'near', zoneId: 'crypt' },
        },
        {
          id: 'enemy_archer',
          name: 'Archer',
          team: 'enemy',
          hp: { current: 9, max: 9 },
          position: { rangeBand: 'distant', zoneId: 'gallery' },
        },
      ],
      flags: {
        activeActorId: 'player_30',
        turnEconomy: {
          actorId: 'player_30',
          actionRemaining: 1,
          bonusActionRemaining: 1,
        },
      },
    },
  }
}

describe('gameplayControlsFromSnapshot', () => {
  it('projects authoritative spells with exact visible target and resource legality', () => {
    const state = gameplayControlsFromSnapshot(snapshot(), 30)

    expect(state.actorId).toBe('player_30')
    expect(state.isActorTurn).toBe(true)
    expect(state.concentration).toBe('Entangle')
    expect(state.spells.map((spell) => spell.name)).toEqual([
      'Fire Bolt',
      'Cure Wounds',
      'Magic Missile',
    ])

    const missile = state.spells.find((spell) => spell.name === 'Magic Missile')
    expect(missile).toMatchObject({
      available: true,
      prepared: true,
      resourceLabel: 'Level 1 slot · 1 left',
      minTargets: 1,
      maxTargets: 1,
    })
    expect(missile?.targets.filter((target) => target.available).map((target) => target.id)).toEqual([
      'enemy_goblin',
      'enemy_archer',
    ])

    const fireBolt = state.spells.find((spell) => spell.name === 'Fire Bolt')
    expect(fireBolt?.targets.find((target) => target.id === 'enemy_archer')).toMatchObject({
      available: false,
      reason: 'Out of range (distant).',
    })
    expect(state.spells.find((spell) => spell.name === 'Cure Wounds')).toMatchObject({
      available: false,
      prepared: false,
      reason: 'Known but not prepared.',
    })
  })

  it('reflects class-resource and action-economy state without inventing uses', () => {
    const active = gameplayControlsFromSnapshot(snapshot(), 30)
    expect(active.capabilities.find((feature) => feature.id === 'second_wind')).toMatchObject({
      current: 1,
      max: 1,
      available: true,
    })
    expect(active.capabilities.find((feature) => feature.id === 'action_surge')).toMatchObject({
      available: false,
      reason: 'The action for this turn is still available.',
    })
    expect(active.interactables[0]).toMatchObject({
      available: false,
      reason: 'Scene interactions are unavailable during combat until they consume turn economy.',
    })

    const spentAction = snapshot()
    spentAction.combat.flags.turnEconomy.actionRemaining = 0
    const afterAction = gameplayControlsFromSnapshot(spentAction, 30)
    expect(afterAction.spells.every((spell) => !spell.available)).toBe(true)
    expect(afterAction.capabilities.find((feature) => feature.id === 'action_surge')).toMatchObject({
      available: true,
      current: 1,
    })

    const missingEconomy = snapshot()
    delete (missingEconomy.combat.flags.turnEconomy as { actionRemaining?: number }).actionRemaining
    expect(gameplayControlsFromSnapshot(missingEconomy, 30).spells[0]).toMatchObject({
      available: false,
      reason: 'Turn economy is unavailable; refresh the session.',
    })
  })

  it('lets healing reach a downed ally but never the dead', () => {
    const state = snapshot()
    const actor = state.playerCharacters[0] as Record<string, unknown>
    actor.spellbook = { ...(actor.spellbook as Record<string, unknown>), preparedSpells: ['Cure Wounds'] }
    actor.classFeatures = [
      {
        id: 'lay_on_hands',
        name: 'Lay on Hands',
        actionEconomy: 'action',
        targetPolicy: 'touch',
        refreshesOn: 'long_rest',
        effect: { type: 'healing_pool' },
      },
    ]
    actor.classFeatureState = { lay_on_hands: { current: 5, max: 5, refreshesOn: 'long_rest' } }
    const downed = state.combat.participants[1] as Record<string, unknown>
    Object.assign(downed, {
      kind: 'player_character',
      hp: { current: 0, max: 10 },
      conditions: ['unconscious', 'dying'],
      isAlive: true,
      isConscious: false,
      position: { rangeBand: 'melee', zoneId: 'crypt' },
    })
    state.combat.participants[2].hp.current = 0

    const controls = gameplayControlsFromSnapshot(state, 30)
    const cure = controls.spells.find((spell) => spell.name === 'Cure Wounds')
    expect(cure?.targets.find((target) => target.id === 'player_31')).toMatchObject({ available: true, reason: '' })
    expect(cure).toMatchObject({ available: true })
    expect(
      controls.spells.find((spell) => spell.name === 'Fire Bolt')?.targets.find((target) => target.id === 'enemy_goblin'),
    ).toMatchObject({ available: false, reason: 'Already defeated.' })
    const layOnHands = controls.capabilities.find((feature) => feature.id === 'lay_on_hands')
    expect(layOnHands?.targets.find((target) => target.id === 'player_31')).toMatchObject({ available: true })

    Object.assign(downed, { conditions: ['dead'], isAlive: false })
    const afterDeath = gameplayControlsFromSnapshot(state, 30)
    expect(
      afterDeath.spells.find((spell) => spell.name === 'Cure Wounds')?.targets.find((target) => target.id === 'player_31'),
    ).toMatchObject({ available: false, reason: 'Healing cannot restore the dead.' })
    expect(
      afterDeath.capabilities.find((feature) => feature.id === 'lay_on_hands')?.targets.find((target) => target.id === 'player_31'),
    ).toMatchObject({ available: false, reason: 'Healing cannot restore the dead.' })
  })

  it('treats a petrified caster as unable to act', () => {
    const state = snapshot()
    Object.assign(state.combat.participants[0], { conditions: ['petrified'] })

    const controls = gameplayControlsFromSnapshot(state, 30)

    expect(controls.spells.map((spell) => spell.reason)).toEqual([
      'Caster cannot act.',
      'Caster cannot act.',
      'Caster cannot act.',
    ])
  })

  it('keeps projected object IDs, state, candidate actions, and revisions together', () => {
    const outOfCombat = snapshot()
    outOfCombat.combat.status = 'none'
    const state = gameplayControlsFromSnapshot(outOfCombat, 30)

    const shrine = state.interactables.find((object) => object.id === 'moon_shrine')
    expect(shrine).toMatchObject({ revision: 4, available: true })
    expect(shrine?.states).toContain('1 uses left')
    expect(shrine?.actions.find((action) => action.id === 'use')).toMatchObject({ available: true })

    const gate = state.interactables.find((object) => object.id === 'iron_gate')
    expect(gate?.actions.find((action) => action.id === 'open')).toMatchObject({ available: true })
    expect(gate?.actions.find((action) => action.id === 'close')).toMatchObject({
      available: false,
      reason: 'Already closed.',
    })

    const ward = state.interactables.find((object) => object.id === 'moon_ward')
    expect(ward?.actions.map((action) => action.id)).toEqual(
      expect.arrayContaining(['inspect', 'trigger', 'disarm']),
    )
  })
})
