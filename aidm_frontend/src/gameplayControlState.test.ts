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
  it('uses bonus-action economy and lists the available upcast levels', () => {
    const state = snapshot()
    const actor = state.playerCharacters[0] as Record<string, unknown>
    const spellbook = actor.spellbook as { knownSpells: Array<Record<string, unknown>> }
    spellbook.knownSpells[1].castingTime = 'bonus_action'
    const resources = actor.spellResources as { slots: Record<string, { current: number; max: number }> }
    resources.slots['2'] = { current: 2, max: 2 }
    state.combat.flags.turnEconomy.actionRemaining = 0
    let missile = gameplayControlsFromSnapshot(state, 30).spells.find((spell) => spell.name === 'Magic Missile')
    expect(missile).toMatchObject({ available: true, castingTime: 'bonus_action', castLevels: [1, 2] })
    state.combat.flags.turnEconomy.bonusActionRemaining = 0
    missile = gameplayControlsFromSnapshot(state, 30).spells.find((spell) => spell.name === 'Magic Missile')
    expect(missile).toMatchObject({ available: false, reason: 'The bonus action for this turn is already spent.' })
  })

  it('allows a cantrip but blocks another levelled spell after a levelled cast', () => {
    const state = snapshot()
    const economy: Record<string, unknown> = state.combat.flags.turnEconomy
    economy.leveledSpellCast = true
    const controls = gameplayControlsFromSnapshot(state, 30)
    expect(controls.spells.find((spell) => spell.name === 'Fire Bolt')?.available).toBe(true)
    expect(controls.spells.find((spell) => spell.name === 'Magic Missile')?.reason).toBe('Only one levelled spell can be cast per turn.')
  })

  it('offers out-of-combat healing to the active party without a range-band check', () => {
    const state = snapshot()
    state.combat.status = 'none'
    const root: Record<string, unknown> = state
    root.activePlayerIds = [30, 31]
    const actor = state.playerCharacters[0] as Record<string, unknown>
    const spellbook = actor.spellbook as { preparedSpells: string[]; knownSpells: Array<Record<string, unknown>> }
    spellbook.preparedSpells = ['Cure Wounds']
    spellbook.knownSpells[2].outOfCombat = true
    const ally = state.playerCharacters[1] as Record<string, unknown>
    ally.health = { currentHp: 0, maxHp: 12, conditions: ['dying'] }
    const healing = gameplayControlsFromSnapshot(state, 30).spells.find((spell) => spell.name === 'Cure Wounds')
    expect(healing?.available).toBe(true)
    expect(healing?.targets.find((target) => target.id === 'player_31')?.available).toBe(true)
  })

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

function itemSnapshot(): Record<string, unknown> {
  return {
    clock: { day: 2, phase: 'evening', shortRestsSinceLong: 0 },
    currentScene: { name: 'Camp', dangerLevel: 0 },
    combat: { status: 'none' },
    playerCharacters: [
      {
        id: 'player_30',
        playerId: 30,
        name: 'Ilyra',
        health: { currentHp: 5, maxHp: 14, conditions: [] },
        hitDice: { current: 2, max: 3, die: 'd8' },
        inventory: {
          items: [
            {
              id: 'potion_1',
              name: 'Potion of Healing',
              quantity: 2,
              itemEffect: {
                id: 'potion_healing',
                summary: 'Restores 2d4+2 hit points.',
                cost: 'bonus_action',
                allyCost: 'action',
                target: 'self_or_ally',
              },
            },
            {
              id: 'acid_1',
              name: 'Acid',
              quantity: 1,
              itemEffect: { id: 'acid_vial', summary: 'Thrown: 2d6 acid damage.', cost: 'action', target: 'enemy' },
            },
            { id: 'rope_1', name: 'Rope', quantity: 1 },
          ],
        },
      },
      { id: 'player_31', playerId: 31, name: 'Bram', health: { conditions: ['dead'] } },
    ],
  }
}

describe('gameplayControlsFromSnapshot items and rest', () => {
  it('offers items with server effects out of combat and checks their targets', () => {
    const state = gameplayControlsFromSnapshot(itemSnapshot(), 30)

    expect(state.items?.map((item) => item.id)).toEqual(['potion_1', 'acid_1'])
    const potion = state.items?.[0]
    expect(potion).toMatchObject({
      name: 'Potion of Healing',
      quantity: 2,
      effectSummary: 'Restores 2d4+2 hit points.',
      cost: 'bonus action; action for an ally',
      available: true,
      reason: '',
    })
    expect(potion?.targets.map((target) => [target.id, target.available, target.reason])).toEqual([
      ['player_30', true, ''],
      ['player_31', false, 'Healing cannot restore the dead.'],
    ])
    expect(state.items?.[1]).toMatchObject({ available: false, reason: 'Requires an active encounter.' })
  })

  it('uses the server combat bundle for items during combat', () => {
    const snapshot = itemSnapshot()
    snapshot.combat = {
      status: 'active',
      legalActions: [
        {
          actorId: 'player_30',
          actions: [
            { id: 'combat.end_turn', type: 'end_turn', available: true },
            {
              id: 'item.use.potion_1',
              type: 'item',
              description: 'Restores 2d4+2 hit points.',
              available: true,
              reason: '',
              economy: { action: 0, bonusAction: 1 },
              item: { id: 'potion_1', name: 'Potion of Healing', quantity: 2 },
              targets: [
                { id: 'player_30', name: 'Ilyra', rangeBand: 'near', available: true, reason: '' },
                { id: 'player_31', name: 'Bram', rangeBand: 'distant', available: false, reason: 'Too far away.' },
              ],
            },
          ],
        },
      ],
    }
    const state = gameplayControlsFromSnapshot(snapshot, 30)

    expect(state.items).toHaveLength(1)
    expect(state.items?.[0]).toMatchObject({ id: 'potion_1', cost: 'bonus action', available: true })
    expect(state.items?.[0].targets.map((target) => [target.id, target.reason])).toEqual([
      ['player_30', ''],
      ['player_31', 'Too far away.'],
    ])
    expect(state.rest).toMatchObject({
      shortAvailable: false,
      longAvailable: false,
      reason: 'Rest is unavailable while combat is active.',
    })
  })

  it('mirrors the rest limits, the scene rest policy and hit dice', () => {
    expect(gameplayControlsFromSnapshot(itemSnapshot(), 30).rest).toEqual({
      shortAvailable: true,
      longAvailable: true,
      reason: '',
      hitDice: { current: 2, max: 3, die: 'd8' },
    })

    const limited = itemSnapshot()
    limited.clock = { day: 2, phase: 'evening', shortRestsSinceLong: 2, lastLongRestDay: 2 }
    expect(gameplayControlsFromSnapshot(limited, 30).rest).toMatchObject({
      shortAvailable: false,
      longAvailable: false,
      reason: 'You have already rested today.',
    })

    const risky = itemSnapshot()
    risky.currentScene = { name: 'Moor', dangerLevel: 4 }
    expect(gameplayControlsFromSnapshot(risky, 30).rest).toMatchObject({
      shortAvailable: true,
      longAvailable: false,
      reason: "This place is too dangerous for a full night's rest.",
    })

    const unsafe = itemSnapshot()
    unsafe.currentScene = { name: 'Lair', restPolicy: 'none' }
    expect(gameplayControlsFromSnapshot(unsafe, 30).rest).toMatchObject({
      shortAvailable: false,
      longAvailable: false,
      reason: 'It is too dangerous to rest here.',
    })
  })
})
