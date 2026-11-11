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
    spellbook.knownSpells[1].scaling = { upcast: { darts: 1 } }
    const resources = actor.spellResources as { slots: Record<string, { current: number; max: number }> }
    resources.slots['2'] = { current: 2, max: 2 }
    state.combat.flags.turnEconomy.actionRemaining = 0
    let missile = gameplayControlsFromSnapshot(state, 30).spells.find((spell) => spell.name === 'Magic Missile')
    expect(missile).toMatchObject({ available: true, castingTime: 'bonus_action', castLevels: [1, 2], upcastTargetsPerLevel: 1 })
    state.combat.flags.turnEconomy.bonusActionRemaining = 0
    missile = gameplayControlsFromSnapshot(state, 30).spells.find((spell) => spell.name === 'Magic Missile')
    expect(missile).toMatchObject({ available: false, reason: 'The bonus action for this turn is already spent.' })
  })

  it('passes the server summary through and lists narrated spells apart from rules-backed ones', () => {
    const state = snapshot()
    const actor = state.playerCharacters[0] as Record<string, unknown>
    const spellbook = actor.spellbook as { preparedSpells: string[]; knownSpells: Array<Record<string, unknown>> }
    Object.assign(spellbook.knownSpells[2], {
      rulesBacked: true,
      outOfCombat: true,
      effectSummary: 'heals 1d8 + modifier',
    })
    spellbook.preparedSpells.push('Detect Magic')
    spellbook.knownSpells.push(
      {
        id: 'spell_detect_magic',
        name: 'Detect Magic',
        level: 1,
        rulesBacked: false,
        authoritativeEffect: false,
        narrated: true,
        castingTime: 'action',
        concentration: true,
        effectSummary: 'Sense magic within 30 feet.',
      },
      { id: 'spell_mage_hand', name: 'Mage Hand', level: 0, rulesBacked: false, description: 'A spectral hand.' },
      { id: 'spell_shield', name: 'Shield', level: 1, rulesBacked: false, castingTime: 'reaction' },
    )
    const controls = gameplayControlsFromSnapshot(state, 30)

    expect(controls.spells.map((spell) => spell.name)).toEqual(['Fire Bolt', 'Cure Wounds', 'Magic Missile'])
    expect(controls.spells.find((spell) => spell.name === 'Cure Wounds')).toMatchObject({
      effectSummary: 'heals 1d8 + modifier',
      effectLabel: 'heals 1d8 + modifier',
      rulesBacked: true,
      outOfCombat: true,
    })
    expect(controls.spells.find((spell) => spell.name === 'Fire Bolt')).toMatchObject({ effectSummary: '', outOfCombat: false })
    expect(controls.narratedSpells).toEqual([
      {
        id: 'spell_mage_hand',
        name: 'Mage Hand',
        level: 0,
        castingTime: 'action',
        effectSummary: 'A spectral hand.',
        concentration: false,
        rulesBacked: false,
        resourceLabel: 'Cantrip',
        available: true,
        reason: '',
      },
      {
        id: 'spell_detect_magic',
        name: 'Detect Magic',
        level: 1,
        castingTime: 'action',
        effectSummary: 'Sense magic within 30 feet.',
        concentration: true,
        rulesBacked: false,
        resourceLabel: 'Level 1 slot · 1 left',
        available: true,
        reason: '',
      },
      expect.objectContaining({ name: 'Shield', available: false, reason: 'Known but not prepared.' }),
    ])
  })

  it('allows a cantrip but blocks another levelled spell after a levelled cast', () => {
    const state = snapshot()
    const economy: Record<string, unknown> = state.combat.flags.turnEconomy
    economy.leveledSpellCast = true
    const controls = gameplayControlsFromSnapshot(state, 30)
    expect(controls.spells.find((spell) => spell.name === 'Fire Bolt')?.available).toBe(true)
    expect(controls.spells.find((spell) => spell.name === 'Magic Missile')?.reason).toBe('Only one levelled spell can be cast per turn.')
  })

  it('turns an active Spiritual Weapon into a slot-free bonus-action attack', () => {
    const state = snapshot()
    const actor = state.playerCharacters[0] as Record<string, unknown>
    const spellbook = actor.spellbook as { preparedSpells: string[]; knownSpells: Array<Record<string, unknown>> }
    spellbook.preparedSpells.push('Spiritual Weapon')
    spellbook.knownSpells.push({
      id: 'spell_spiritual_weapon',
      name: 'Spiritual Weapon',
      level: 2,
      authoritativeEffect: true,
      castingTime: 'bonus_action',
      delivery: { type: 'attack', ranged: false },
      target: { relation: 'enemy', rangeBands: ['melee', 'near', 'far'], maxTargets: 1 },
      effects: [{ kind: 'damage', dice: '1d8', damageType: 'force' }, { kind: 'repeat_attack' }],
    })
    const economy: Record<string, unknown> = state.combat.flags.turnEconomy
    economy.leveledSpellCast = true
    let weapon = gameplayControlsFromSnapshot(state, 30).spells.find((spell) => spell.name === 'Spiritual Weapon')
    expect(weapon).toMatchObject({ available: false, repeatAttack: false })

    const caster: Record<string, unknown> = state.combat.participants[0]
    caster.activeEffects = [{
      id: 'sfx_weapon', sourceId: 'spell_spiritual_weapon', casterId: 'player_30', modifiers: {}, repeatAttack: { castLevel: 2 },
    }]
    weapon = gameplayControlsFromSnapshot(state, 30).spells.find((spell) => spell.name === 'Spiritual Weapon')
    expect(weapon).toMatchObject({ available: true, repeatAttack: true, castLevels: [], resourceLabel: 'Active · no slot needed' })
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

  it('offers two Eldritch Blast targets at level five', () => {
    const state = snapshot()
    const actor = state.playerCharacters[0] as Record<string, unknown>
    actor.level = 5
    const spellbook = actor.spellbook as { knownSpells: Array<Record<string, unknown>> }
    spellbook.knownSpells[0] = {
      ...spellbook.knownSpells[0], id: 'spell_eldritch_blast', name: 'Eldritch Blast',
      delivery: { type: 'attack', attacks: 1 }, scaling: { cantrip: true },
    }
    expect(gameplayControlsFromSnapshot(state, 30).spells.find((spell) => spell.name === 'Eldritch Blast')?.maxTargets).toBe(2)
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

  it('offers Spare the Dying only for a dying ally', () => {
    const state = snapshot()
    const actor = state.playerCharacters[0] as Record<string, unknown>
    actor.spellbook = {
      knownSpells: [{
        id: 'spell_spare_the_dying', name: 'Spare the Dying', level: 0, authoritativeEffect: true,
        target: { relation: 'ally', rangeBands: ['melee'], maxTargets: 1, requiresDying: true },
        effects: [{ kind: 'stabilize' }],
      }],
    }
    const ally = state.combat.participants[1] as Record<string, unknown>
    Object.assign(ally, { kind: 'player_character', hp: { current: 0, max: 10 },
      conditions: ['dying', 'unconscious'], isAlive: true, position: { rangeBand: 'melee' } })
    let spare = gameplayControlsFromSnapshot(state, 30).spells[0]
    expect(spare.targets.find((target) => target.id === 'player_31')).toMatchObject({ available: true })
    Object.assign(ally, { conditions: ['stable', 'unconscious'] })
    spare = gameplayControlsFromSnapshot(state, 30).spells[0]
    expect(spare.targets.find((target) => target.id === 'player_31')).toMatchObject({ available: false, reason: 'Must be dying.' })
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

describe('gameplayControlsFromSnapshot offered actions', () => {
  function offeredSnapshot(combatStatus: string, offeredActions?: unknown[]) {
    return {
      playerCharacters: [{ id: 'player_30', playerId: 30, name: 'Ilyra' }],
      combat: { status: combatStatus },
      currentScene: {
        interactables: [{
          id: 'strongbox',
          name: 'Wagon Strongbox',
          kind: 'container',
          open: false,
          locked: true,
          revision: 2,
          ...(offeredActions ? { offeredActions } : {}),
        }],
      },
    }
  }

  const offers = [
    { action: 'search', label: 'Search the strongbox', legal: false, done: false, authored: true, reason: 'It is locked.' },
    { action: 'unlock', label: 'Unlock the strongbox', legal: true, done: false, authored: true, check: { skill: 'thieves_tools', ability: 'dexterity', dc: 12 } },
    { action: 'inspect', label: 'Examine Wagon Strongbox', legal: true, done: true },
    { action: 'teleport', label: 'Teleport the strongbox', legal: true, done: false },
  ]

  it('takes the server’s offered actions in its order instead of guessing verbs', () => {
    const [strongbox] = gameplayControlsFromSnapshot(offeredSnapshot('none', offers), 30).interactables
    expect(strongbox.offered).toBe(true)
    expect(strongbox.actions).toEqual([
      { id: 'search', label: 'Search the strongbox', available: false, reason: 'It is locked.', phrase: true, authored: true, done: false },
      {
        id: 'unlock',
        label: 'Unlock the strongbox',
        available: true,
        reason: '',
        phrase: true,
        authored: true,
        done: false,
        check: { skill: 'thieves_tools', ability: 'dexterity', dc: 12 },
      },
      { id: 'inspect', label: 'Examine Wagon Strongbox', available: false, reason: '', phrase: true, authored: false, done: true },
    ])
  })

  it('leaves combat legality to the server when it offers actions, and blocks everything during combat when it does not', () => {
    const [offered] = gameplayControlsFromSnapshot(offeredSnapshot('active', offers), 30).interactables
    expect(offered).toMatchObject({ available: true, reason: '' })
    expect(offered.actions.find((action) => action.id === 'unlock')?.available).toBe(true)

    const [guessed] = gameplayControlsFromSnapshot(offeredSnapshot('active'), 30).interactables
    expect(guessed.offered).toBeUndefined()
    expect(guessed).toMatchObject({
      available: false,
      reason: 'Scene interactions are unavailable during combat until they consume turn economy.',
    })
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
    expect(state.items?.[0]).toMatchObject({
      id: 'potion_1',
      cost: 'bonus action',
      available: true,
      actionId: 'item.use.potion_1',
    })
    expect(state.items?.[0].targets.map((target) => [target.id, target.reason])).toEqual([
      ['player_30', ''],
      ['player_31', 'Too far away.'],
    ])
    expect(state.rest).toMatchObject({
      shortAvailable: false,
      shortReason: 'Rest is unavailable while combat is active.',
      longAvailable: false,
      longReason: 'Rest is unavailable while combat is active.',
    })
  })

  it('mirrors the rest limits, the scene rest policy and hit dice, with a reason for each kind of rest', () => {
    expect(gameplayControlsFromSnapshot(itemSnapshot(), 30).rest).toEqual({
      shortAvailable: true,
      shortReason: '',
      longAvailable: true,
      longReason: '',
      hitDice: { current: 2, max: 3, die: 'd8' },
    })

    const limited = itemSnapshot()
    limited.clock = { day: 2, phase: 'evening', shortRestsSinceLong: 2, lastLongRestDay: 2 }
    expect(gameplayControlsFromSnapshot(limited, 30).rest).toMatchObject({
      shortAvailable: false,
      shortReason: 'You need a long rest before another short rest helps.',
      longAvailable: false,
      longReason: 'You have already rested today.',
    })

    const risky = itemSnapshot()
    risky.currentScene = { name: 'Moor', dangerLevel: 4 }
    expect(gameplayControlsFromSnapshot(risky, 30).rest).toMatchObject({
      shortAvailable: true,
      shortReason: '',
      longAvailable: false,
      longReason: "This place is too dangerous for a full night's rest.",
    })

    const unsafe = itemSnapshot()
    unsafe.currentScene = { name: 'Lair', restPolicy: 'none' }
    expect(gameplayControlsFromSnapshot(unsafe, 30).rest).toMatchObject({
      shortAvailable: false,
      shortReason: 'It is too dangerous to rest here.',
      longAvailable: false,
      longReason: 'It is too dangerous to rest here.',
    })
  })

  it('reads the purse and the unequipped goods a merchant could buy', () => {
    const snapshot = itemSnapshot()
    const actor = (snapshot.playerCharacters as Array<Record<string, unknown>>)[0]
    actor.inventory = {
      currency: { gp: 12, sp: 5, cp: 0, pp: 'x' },
      items: [
        { id: 'potion_1', name: 'Potion of Healing', quantity: 2, price: { gp: 50 } },
        { id: 'sword_1', name: 'Longsword', quantity: 1, equipped: true, price: { gp: 15 } },
        { id: 'rope_1', name: 'Rope' },
        { name: 'No id' },
      ],
    }
    const state = gameplayControlsFromSnapshot(snapshot, 30)

    expect(state.currency).toEqual({ gp: 12, sp: 5 })
    expect(state.tradeGoods).toEqual([
      { id: 'potion_1', name: 'Potion of Healing', quantity: 2, price: { gp: 50 } },
      { id: 'rope_1', name: 'Rope', quantity: 1, price: null },
    ])
  })
})

describe('gameplayControlsFromSnapshot class capabilities', () => {
  function monk(state: ReturnType<typeof snapshot>) {
    const actor = state.playerCharacters[0] as Record<string, unknown>
    actor.classFeatures = [
      {
        id: 'flurry_of_blows', baseId: 'flurry_of_blows', name: 'Flurry of Blows', actionEconomy: 'bonus_action',
        targetPolicy: 'self', pool: 'ki', poolName: 'Ki', cost: { pool: 'ki', amount: 1 }, combatOnly: true, role: 'panel',
        effect: { type: 'grant_action' },
      },
      {
        id: 'step_of_the_wind:dash', baseId: 'step_of_the_wind', name: 'Step of the Wind: Dash', actionEconomy: 'bonus_action',
        targetPolicy: 'self', pool: 'ki', poolName: 'Ki', cost: { pool: 'ki', amount: 1 }, combatOnly: true, role: 'panel',
        effect: { type: 'grant_action' },
      },
      {
        id: 'martial_arts_strike', baseId: 'martial_arts_strike', name: 'Martial Arts Strike', actionEconomy: 'bonus_action',
        targetPolicy: 'self', cost: {}, role: 'combat_action', reason: 'Choose it from the combat actions on your turn.',
        effect: { type: 'grant_action' },
      },
    ]
    actor.classFeatureState = { ki: { current: 2, max: 3, refreshesOn: 'short_rest' } }
    return actor
  }

  it('reads shared pools and explains combat-only and combat-action capabilities', () => {
    const state = snapshot()
    monk(state)
    const inCombat = gameplayControlsFromSnapshot(state, 30).capabilities
    expect(inCombat.find((feature) => feature.id === 'flurry_of_blows')).toMatchObject({ current: 2, max: 3, available: true })
    expect(inCombat.find((feature) => feature.id === 'step_of_the_wind:dash')).toMatchObject({ current: 2, refreshesOn: 'short rest' })
    expect(inCombat.find((feature) => feature.id === 'martial_arts_strike')).toMatchObject({
      available: false,
      reason: 'Choose it from the combat actions on your turn.',
    })

    const outside = snapshot() as Record<string, unknown>
    monk(outside as ReturnType<typeof snapshot>)
    outside.combat = { status: 'ended' }
    expect(gameplayControlsFromSnapshot(outside, 30).capabilities.find((feature) => feature.id === 'flurry_of_blows')).toMatchObject({
      available: false,
      reason: 'Available only during active combat.',
    })

    const broke = snapshot()
    monk(broke).classFeatureState = { ki: { current: 0, max: 3 } }
    expect(gameplayControlsFromSnapshot(broke, 30).capabilities.find((feature) => feature.id === 'flurry_of_blows')).toMatchObject({
      available: false,
      reason: 'No uses remain.',
    })
  })

  it('names the pool each capability spends by the server pool name, and what one use costs', () => {
    const state = snapshot()
    const actor = monk(state)
    ;(actor.classFeatures as Array<Record<string, unknown>>).push(
      {
        id: 'lay_on_hands', baseId: 'lay_on_hands', name: 'Lay on Hands', actionEconomy: 'action', targetPolicy: 'self_or_ally',
        pool: 'lay_on_hands', poolName: 'Lay on Hands', cost: { pool: 'lay_on_hands', amount: 'requested' }, role: 'panel',
        effect: { type: 'healing_pool' },
      },
      {
        id: 'rage', baseId: 'rage', name: 'Rage', actionEconomy: 'bonus_action', targetPolicy: 'self', pool: 'rage',
        poolName: 'Rage', cost: { pool: 'rage', amount: 1 }, role: 'panel', effect: { type: 'apply_modifier' },
      },
      {
        // Without the server's display name the pool is not named from its id.
        id: 'flexible_casting', baseId: 'flexible_casting', name: 'Flexible Casting', actionEconomy: 'bonus_action',
        targetPolicy: 'self', pool: 'sorcery_points', cost: { pool: 'sorcery_points', amount: 'by_option' }, role: 'panel',
        effect: { type: 'convert_resource' },
      },
    )
    const capabilities = gameplayControlsFromSnapshot(state, 30).capabilities
    const pools = capabilities.map((feature) => [feature.id, feature.poolId, feature.poolName, feature.poolCost])

    expect(pools).toEqual([
      ['flurry_of_blows', 'ki', 'Ki', 1],
      ['step_of_the_wind:dash', 'ki', 'Ki', 1],
      ['martial_arts_strike', '', '', null],
      ['lay_on_hands', 'lay_on_hands', 'Lay on Hands', null],
      ['rage', 'rage', 'Rage', 1],
      ['flexible_casting', 'sorcery_points', '', null],
    ])
  })

  it('prefers the server combat action and keeps an ally capability off the user', () => {
    const state = snapshot() as ReturnType<typeof snapshot> & { combat: Record<string, unknown> }
    const actor = state.playerCharacters[0] as Record<string, unknown>
    actor.classFeatures = [
      {
        id: 'bardic_inspiration', baseId: 'bardic_inspiration', name: 'Bardic Inspiration', actionEconomy: 'bonus_action',
        targetPolicy: 'ally', pool: 'bardic_inspiration', cost: { pool: 'bardic_inspiration', amount: 1 }, role: 'panel',
        effect: { type: 'apply_modifier' },
      },
    ]
    actor.classFeatureState = { bardic_inspiration: { current: 3, max: 3, refreshesOn: 'long_rest' } }
    const outside = { ...state, combat: { status: 'ended' } }
    const offline = gameplayControlsFromSnapshot(outside, 30).capabilities[0]
    expect(offline.targets.map((target) => target.id)).toEqual(['player_31'])
    expect(offline.available).toBe(true)

    state.combat.legalActions = [
      {
        actorId: 'player_30',
        actions: [
          {
            id: 'class.use.bardic_inspiration',
            available: false,
            reason: 'Too far away; the ally must be in your range band or the next one.',
            capability: { id: 'bardic_inspiration' },
            targets: [{ id: 'player_31', name: 'Borin', available: false, reason: 'Too far away; the ally must be in your range band or the next one.' }],
          },
        ],
      },
    ]
    const control = gameplayControlsFromSnapshot(state, 30).capabilities[0]
    expect(control).toMatchObject({ available: false, reason: 'Too far away; the ally must be in your range band or the next one.' })
    expect(control.targets).toEqual([
      expect.objectContaining({ id: 'player_31', currentHp: 10, available: false }),
    ])
  })
})
