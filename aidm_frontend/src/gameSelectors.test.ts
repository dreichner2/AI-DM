import { describe, expect, it } from 'vitest'
import {
  abilityOptionsFromStatBlock,
  buildMapMeta,
  buildTimeline,
  recentMemoryFromSnippets,
  inventoryCapacity,
  inventoryGoldLabel,
  inventoryWeightLabel,
  itemOptionsFromInventory,
  memorySnippetRecords,
  normalizeCharacterTraits,
  normalizeInventory,
  normalizeSpellbook,
  normalizeSpellResources,
  normalizeStats,
  normalizeXp,
  partyVitalityFromSnapshot,
  pendingRollNoticeFromTimeline,
  pendingRollOptionsFromTimeline,
  speakerDetail,
  timelineFromLog,
  truncateText,
  turnNumber,
  turnPersistenceLabel,
  worldMapFromSnapshot,
  worldStateFromSnapshot,
} from './gameSelectors'
import type { CampaignSegment, MapItem, Player, SessionLogEntry, TimelineEntry } from './types'

describe('game selector helpers', () => {
  it('projects exhausted spell resources and concentration for player feedback', () => {
    expect(normalizeSpellResources({
      spellResources: {
        castingMode: 'hybrid',
        slots: { '1': { current: 0, max: 2 }, '2': { current: 1, max: 1 } },
        pactSlots: { current: 1, max: 2, slotLevel: 2 },
        mysticArcanum: { '6': { current: 0, max: 1 } },
        concentration: { spellName: 'Hex' },
      },
    })).toEqual({
      castingMode: 'hybrid',
      slots: [
        { level: 1, current: 0, max: 2 },
        { level: 2, current: 1, max: 1 },
      ],
      pactSlot: { level: 2, current: 1, max: 2 },
      arcanum: [{ level: 6, current: 0, max: 1 }],
      concentration: 'Hex',
    })
  })

  it('labels every persisted system log entry as System without requiring markdown', () => {
    expect(timelineFromLog({
      id: 91,
      entry_type: 'system',
      message: 'State updated: Torch removed.',
      timestamp: '2026-06-06T01:00:03Z',
      metadata: {},
    })).toMatchObject({
      role: 'system',
      speaker: 'System',
      text: 'State updated: Torch removed.',
    })
  })

  it('normalizes inventory, weight labels, stat blocks, and picker options', () => {
    const inventory = normalizeInventory({
      items: [
        'Torch',
        { name: 'Healing Potion', quantity: 2, weight: 0.5 },
        { item: 'Chain Mail', count: '1', weight: '55' },
        { name: 'Greataxe', quantity: 1 },
        { name: 'Handaxe', quantity: 1, type: 'misc' },
      ],
    })

    expect(inventory).toEqual([
      { id: '', item: 'Torch', count: '1', weight: '—', icon: 'sword', weightValue: null, type: '', subtype: '', equipped: false, slot: '', equippable: false },
      { id: '', item: 'Healing Potion', count: '2', weight: '1 lb', icon: 'potion', weightValue: 1, type: '', subtype: '', equipped: false, slot: '', equippable: false },
      { id: '', item: 'Chain Mail', count: '1', weight: '55 lb', icon: 'armor', weightValue: 55, type: '', subtype: '', equipped: false, slot: 'body_armor', equippable: true },
      { id: '', item: 'Greataxe', count: '1', weight: '—', icon: 'sword', weightValue: null, type: '', subtype: '', equipped: false, slot: 'two_hands', equippable: true },
      { id: '', item: 'Handaxe', count: '1', weight: '—', icon: 'sword', weightValue: null, type: 'misc', subtype: '', equipped: false, slot: 'main_hand', equippable: true },
    ])
    expect(itemOptionsFromInventory(inventory)).toEqual([
      { id: '', name: 'Torch', quantity: '1', equipped: false, slot: '' },
      { id: '', name: 'Healing Potion', quantity: '2', equipped: false, slot: '' },
      { id: '', name: 'Chain Mail', quantity: '1', equipped: false, slot: 'body_armor' },
      { id: '', name: 'Greataxe', quantity: '1', equipped: false, slot: 'two_hands' },
      { id: '', name: 'Handaxe', quantity: '1', equipped: false, slot: 'main_hand' },
    ])
    expect(inventoryCapacity({ carrying_capacity: 120 })).toBe(120)
    expect(inventoryWeightLabel(inventory, 120)).toBe('Weight 56 / 120 lb')
    expect(inventoryGoldLabel({ gold: 12 })).toBe('12 gp')
    expect(inventoryGoldLabel({ stats: { gold_pieces: 1250 } })).toBe('1.3K gp')
    expect(inventoryGoldLabel({ gold: 0, copper: 10 })).toBe('0 gp · 10 cp')

    const statBlock = normalizeStats(
      { strength: 16, dexterity: 12, constitution: 9 },
      { current_hp: 8, max_hp: 10, ac: 14, speed: '30 ft', xp: 150, xp_to_next: 300 },
      3,
    )

    expect(statBlock.hp).toBe('8 / 10')
    expect(statBlock.ac).toBe('14')
    expect(statBlock.proficiency).toBe('+2')
    expect(abilityOptionsFromStatBlock(statBlock).slice(0, 3)).toEqual([
      { key: 'strength', label: 'STR', score: '16', modifier: '+3' },
      { key: 'dexterity', label: 'DEX', score: '12', modifier: '+1' },
      { key: 'constitution', label: 'CON', score: '9', modifier: '-1' },
    ])
    expect(normalizeXp({ xp: 150, xp_to_next: 300 }, 3)).toMatchObject({
      current: 150,
      max: 300,
      percent: 50,
    })
    expect(normalizeXp({}, '—')).toEqual({
      current: 0,
      max: 300,
      percent: 0,
      label: '0 / 300 XP',
    })
  })

  it('prefers derived armor class over stale legacy AC fields', () => {
    const statBlock = normalizeStats(
      { dexterity: 14, ac: 12, armor_class: 12 },
      { ac: 11 },
      2,
      { armorClass: 17, armorClassBreakdown: { armorName: 'Scale Mail', shieldBonus: 2 } },
    )

    expect(statBlock.ac).toBe('17')
  })

  it('keeps HP against its maximum and reads saves, death saves, hit dice and conditions', () => {
    const statBlock = normalizeStats(
      {
        strength: 18,
        dexterity: 12,
        constitution: 16,
        intelligence: 8,
        wisdom: 10,
        charisma: 9,
        max_hp: 31,
        hit_dice: 1,
        conditions: ['poisoned', ' '],
        death_saves: { successes: 2, failures: 7 },
        saving_throw_proficiencies: ['strength', 'constitution_saving_throw'],
      },
      {},
      5,
      { hitDie: 10, proficiencyBonus: 3 },
    )

    expect(statBlock.hp).toBe('31 / 31')
    expect(statBlock.savingThrows).toEqual([
      { ability: 'STR', modifier: '+7', proficient: true },
      { ability: 'DEX', modifier: '+1', proficient: false },
      { ability: 'CON', modifier: '+6', proficient: true },
      { ability: 'INT', modifier: '-1', proficient: false },
      { ability: 'WIS', modifier: '+0', proficient: false },
      { ability: 'CHA', modifier: '-1', proficient: false },
    ])
    expect(statBlock.deathSaves).toEqual({ successes: 2, failures: 3 })
    expect(statBlock.hitDice).toEqual({ current: 1, max: 5, die: 'd10' })
    expect(statBlock.conditions).toEqual(['poisoned'])

    const fresh = normalizeStats({ current_hp: 9, max_hp: 9, savingThrows: { dex: true, int: { proficient: true }, wis: false } }, {}, 2, { hitDie: 6 })
    expect(fresh.hp).toBe('9 / 9')
    expect(fresh.savingThrows).toEqual([])
    expect(fresh.hitDice).toEqual({ current: 2, max: 2, die: 'd6' })
    expect(fresh.deathSaves).toBeNull()

    const proficient = normalizeStats({ dexterity: 14, intelligence: 10, wisdom: 10, savingThrows: { dex: true, int: { proficient: true }, wis: false } }, {}, 1)
    expect(proficient.savingThrows?.filter((save) => save.proficient).map((save) => [save.ability, save.modifier])).toEqual([
      ['DEX', '+4'],
      ['INT', '+2'],
    ])
    expect(proficient.savingThrows?.[0]).toEqual({ ability: 'STR', modifier: '—', proficient: false })
    expect(proficient.hitDice).toBeNull()

    // The server's derived list (class saves plus the sheet's) wins over the stored stats.
    const derived = normalizeStats(
      { dexterity: 14, wisdom: 12, saving_throw_proficiencies: ['strength'] },
      {},
      1,
      { savingThrowProficiencies: ['dexterity', 'wisdom'] },
    )
    expect(derived.savingThrows?.filter((save) => save.proficient).map((save) => [save.ability, save.modifier])).toEqual([
      ['DEX', '+4'],
      ['WIS', '+3'],
    ])
    const noneProficient = normalizeStats({ dexterity: 14, saving_throw_proficiencies: ['dexterity'] }, {}, 1, { savingThrowProficiencies: [] })
    expect(noneProficient.savingThrows?.some((save) => save.proficient)).toBe(false)
  })

  it('reads each downed party member from combat first, then from the viewer’s own sheet', () => {
    expect(
      partyVitalityFromSnapshot({
        combat: {
          status: 'active',
          participants: [
            { id: 'player_30', team: 'player', conditions: ['unconscious', 'dying'], deathSaves: { successes: 1, failures: 2 } },
            { id: 'player_31', team: 'player', conditions: ['unconscious', 'stable'], deathSaves: { successes: 3, failures: 0 } },
            { id: 'player_32', team: 'player', conditions: [] },
            { id: 'goblin_1', team: 'enemy', conditions: ['dead'] },
          ],
        },
        playerCharacters: [
          { id: 'player_32', playerId: 32, health: { conditions: ['dead'] } },
          { id: 'player_33', playerId: 33, health: { conditions: ['dead'] } },
        ],
      }),
    ).toEqual({
      30: { state: 'dying', successes: 1, failures: 2 },
      31: { state: 'stable', successes: 0, failures: 0 },
      33: { state: 'dead', successes: 0, failures: 0 },
    })
    expect(
      partyVitalityFromSnapshot({
        combat: { status: 'ended', participants: [{ id: 'player_30', team: 'player', conditions: ['dying'] }] },
        playerCharacters: [{ playerId: 30, health: { conditions: ['unconscious', 'stable'] } }],
      }),
    ).toEqual({ 30: { state: 'stable', successes: 0, failures: 0 } })
  })

  it('normalizes spellbooks from character sheets and dedupes known spells', () => {
    const spellbook = normalizeSpellbook(
      {
        spellbook: {
          known_spells: [
            'Minor Illusion',
            {
              spellName: 'Cobalt Charm',
              spellLevel: '1',
              sourceType: 'class_catalog',
              sourceDetail: 'wizard',
              description: 'Tint a social moment with blue sparks.',
              tags: ['social', 'Original'],
              catalog: 'aidm-original',
            },
          ],
          prepared_spells: ['Cobalt Charm'],
        },
      },
      {
        spells: [
          { name: 'River Ward', level: 1, source: 'race:riverborn' },
          { name: 'Minor Illusion', level: 0 },
        ],
      },
    )

    expect(spellbook.knownSpells.map((spell) => spell.name)).toEqual([
      'Minor Illusion',
      'Cobalt Charm',
      'River Ward',
    ])
    expect(spellbook.knownSpells[1]).toMatchObject({
      level: 1,
      levelLabel: 'Lv 1',
      source: 'Class / wizard',
      prepared: true,
      catalog: 'aidm-original',
    })
    expect(spellbook.preparedSpellNames).toEqual(['Cobalt Charm'])
    expect(spellbook.sources).toEqual(expect.arrayContaining(['Class / wizard', 'aidm-original', 'social']))
  })

  it('keeps spell descriptions when character sheets also include a plain spell name list', () => {
    const spellbook = normalizeSpellbook({
      spells: ['Ki Blast', 'Ki Sense'],
      spellbook: {
        knownSpells: [
          {
            name: 'Ki Blast',
            level: 0,
            sourceType: 'race',
            sourceDetail: 'saiyan',
            description: 'Project a focused ranged burst of life energy.',
          },
          {
            name: 'Ki Sense',
            level: 0,
            sourceType: 'race',
            sourceDetail: 'saiyan',
            description: 'Feel strong nearby life force, battle pressure, or hidden power.',
          },
        ],
      },
    })

    expect(spellbook.knownSpells).toEqual([
      expect.objectContaining({
        name: 'Ki Blast',
        source: 'Race / saiyan',
        description: 'Project a focused ranged burst of life energy.',
      }),
      expect.objectContaining({
        name: 'Ki Sense',
        description: 'Feel strong nearby life force, battle pressure, or hidden power.',
      }),
    ])
  })

  it('normalizes custom race active abilities and passive traits for the inspector', () => {
    const traits = normalizeCharacterTraits({
      raceName: 'Himeros',
      customRaceDefinition: {
        traits: [
          {
            id: 'himeros_divine_beauty',
            name: 'Divine Beauty',
            category: 'skill',
            description: 'You have proficiency in the Persuasion skill.',
            mechanics: {
              skillProficiency: { skill: 'Persuasion', expertiseIfProficient: true },
            },
          },
          {
            id: 'himeros_aura_of_desire',
            name: 'Aura of Desire',
            category: 'active_ability',
            description: 'Creatures of your choice within 30 feet must make a Wisdom saving throw.',
            mechanics: {
              activeAbility: {
                actionType: 'action',
                cooldown: 'longRest',
                effectType: 'charm',
              },
            },
          },
        ],
      },
    })

    expect(traits.map((trait) => trait.name)).toEqual(['Aura of Desire', 'Divine Beauty'])
    expect(traits[0]).toMatchObject({
      active: true,
      actionType: 'Action',
      cooldown: 'Long Rest',
      source: 'Race / Himeros',
      typeLabel: 'Active',
    })
    expect(traits[1]).toMatchObject({
      active: false,
      source: 'Race / Himeros',
      typeLabel: 'Skill',
      description: 'You have proficiency in the Persuasion skill.',
    })
  })

  it('builds timeline rows from logs, optimistic entries, streaming state, and statuses', () => {
    const logEntries: SessionLogEntry[] = [
      {
        id: 1,
        entry_type: 'player',
        message: 'Ember: I inspect the gate.',
        timestamp: '2026-06-06T01:00:00Z',
        metadata: { turn_id: 11, turn_number: 1 },
      },
      {
        id: 2,
        entry_type: 'dm',
        message: 'DM: The gate hums.',
        timestamp: '2026-06-06T01:00:02Z',
        metadata: { turn_id: 11, turn_number: 1 },
      },
      {
        id: 3,
        entry_type: 'system',
        message: '**Welcome to the table.**',
        timestamp: '2026-06-06T01:00:03Z',
        metadata: {},
      },
    ]
    const optimisticEntries: TimelineEntry[] = [
      {
        id: 'optimistic-local',
        role: 'player',
        speaker: 'Ember',
        text: 'I listen.',
        timestamp: null,
        metadata: { client_message_id: 'local-1' },
      },
    ]

    const timeline = buildTimeline({
      logEntries,
      optimisticEntries,
      streamingTurn: {
        turnId: 12,
        turnNumber: 2,
        text: 'A whisper answers.',
        requiresRoll: true,
        rulesHint: { roll_type: 'perception' },
      },
      turnStatuses: { 11: 'persisted' },
    })

    expect(timeline.map((entry) => entry.id)).toEqual([
      'log-1',
      'log-2',
      'log-3',
      'optimistic-local',
      'stream-12',
    ])
    expect(timeline[0]).toMatchObject({
      role: 'player',
      speaker: 'Ember',
      text: 'I inspect the gate.',
      metadata: { turn_id: 11, turn_number: 1, persistence_status: 'persisted' },
    })
    expect(timeline[2]).toMatchObject({ role: 'system', speaker: 'System', text: 'Welcome to the table.' })
    expect(turnNumber(timeline[0], 0)).toBe(1)
    expect(turnPersistenceLabel(timeline.at(-1) as TimelineEntry)).toBe('streaming')
  })

  it('derives subtle combat panel state from the session snapshot', () => {
    const panel = worldStateFromSnapshot({
      currentScene: { name: 'Ash Road', sceneType: 'combat', mood: 'dangerous', dangerLevel: 8 },
      combat: {
        status: 'active',
        round: 2,
        battlefield: { lighting: 'dim', environmentType: 'forest', visibility: 'smoke' },
        encounterGoal: { description: 'Drive off the ambushers.' },
        flags: {
          resolverMethod: 'generated_variant',
          creatureSource: 'generated_variant',
          debugCombat: true,
          combatStartedBy: 'post_dm_adjudicator',
          initiativeRequired: true,
          enemyGroups: [{ count: 2, name: 'Ash Goblin' }],
          combatDifficultyAI: { tacticalLevel: 'smart' },
        },
        participants: [
          {
            id: 'player_1',
            name: 'Ember',
            team: 'player',
            kind: 'player_character',
            hp: { current: 12, max: 20 },
          },
          {
            id: 'enemy_goblin_1',
            name: 'Ash Goblin',
            team: 'enemy',
            kind: 'creature',
            hp: { current: 2, max: 7 },
            conditions: ['frightened'],
            morale: 18,
            moraleEvents: ['leader_died'],
            source: 'generated_variant',
            position: { rangeBand: 'near', zoneId: 'ash_road' },
            currentIntent: {
              intentType: 'retreat',
              tacticSource: 'deterministic',
              brainSource: 'deepseek-v4-pro',
              selectionScore: 91,
              selectionMethod: 'deterministic_scoring',
              visibleTelegraph: 'The goblin looks toward the treeline.',
            },
          },
        ],
      },
    })

    expect(panel.combat.active).toBe(true)
    expect(panel.combat.round).toBe('2')
    expect(panel.combat.battlefield).toBe('dim / forest / smoke')
    expect(panel.combat.enemies[0]).toMatchObject({
      name: 'Ash Goblin',
      health: 'Wounded',
      healthTone: 'hurt',
      intent: 'retreat',
      morale: '18',
      source: 'generated_variant',
      moraleEvents: ['leader died'],
      tacticSource: 'deterministic',
      brainSource: 'deepseek-v4-pro',
      position: 'near / ash road',
      selectionScore: '91',
      selectionMethod: 'deterministic scoring',
    })
    expect(panel.combat.debugEnabled).toBe(true)
    expect(panel.combat.resolverMethod).toBe('generated variant')
    expect(panel.combat.creatureSource).toBe('generated variant')
    expect(panel.combat.tacticalLevel).toBe('smart')
    expect(panel.combat.combatStartedBy).toBe('post dm adjudicator')
    expect(panel.combat.enemyGroupSummary).toBe('2 x Ash Goblin')
    expect(panel.combat.initiativeRequired).toBe(true)
    expect(panel.combat.telegraphs).toEqual(['The goblin looks toward the treeline.'])
  })

  it('places a non-blocking streamed response before newer optimistic player rows', () => {
    const optimisticEntries: TimelineEntry[] = [
      {
        id: 'optimistic-local',
        role: 'player',
        speaker: 'Ember',
        text: 'I keep moving.',
        timestamp: null,
        metadata: { client_message_id: 'local-1', persistence_status: 'pending' },
      },
    ]

    const timeline = buildTimeline({
      logEntries: [],
      optimisticEntries,
      streamingTurn: {
        turnId: 12,
        turnNumber: 2,
        text: 'The previous answer is waiting on canon.',
        requiresRoll: false,
        rulesHint: {},
      },
      turnStatuses: { 12: 'canon_pending' },
    })

    expect(timeline.map((entry) => entry.id)).toEqual(['stream-12', 'optimistic-local'])
  })

  it('derives unresolved pending roll target options from timeline metadata', () => {
    const timeline: TimelineEntry[] = [
      {
        id: 'dm-10',
        role: 'dm',
        speaker: 'DM',
        text: 'The lock resists your tools.',
        timestamp: null,
        metadata: {
          turn_id: 10,
          turn_number: 1,
          requires_roll: true,
          outcome_status: 'deferred',
          rule_type: 'thieves_tools',
          dc_hint: 'DC 14',
          roll_gate: {
            roll_spec: {
              die: 'd20',
              mode: 'advantage',
              rule_type: 'thieves_tools',
              reason: 'Pick the warded lock',
              result_visibility: 'hidden_until_landed',
              ability: { key: 'dexterity', label: 'DEX', score: 16, modifier: 3 },
            },
          },
        },
      },
      {
        id: 'dm-11',
        role: 'dm',
        speaker: 'DM',
        text: 'The guard studies your expression.',
        timestamp: null,
        metadata: {
          turn_id: 11,
          requires_roll: true,
          outcome_status: 'deferred',
          rule_type: 'social',
        },
      },
      {
        id: 'roll-11',
        role: 'system',
        speaker: 'System',
        text: 'Check resolved.',
        timestamp: null,
        metadata: { resolved_turn_id: 11 },
      },
    ]

    expect(pendingRollOptionsFromTimeline(timeline)).toEqual([
      {
        turnId: 10,
        label: 'Turn 1: thieves tools',
        detail: 'The lock resists your tools.',
        guidance: {
          pendingTurnId: 10,
          ruleType: 'thieves tools',
          dcHint: 'DC 14',
          prompt: 'The lock resists your tools.',
          remainingPlayerIds: [],
          rollSpec: {
            die: 'd20',
            mode: 'advantage',
            ruleType: 'thieves_tools',
            reason: 'Pick the warded lock',
            resultVisibility: 'hidden_until_landed',
            ability: { key: 'dexterity', label: 'DEX' },
          },
        },
      },
    ])
  })

  it('derives a party-visible pending roll notice from remaining player metadata', () => {
    const players: Player[] = [
      {
        player_id: 1,
        workspace_id: 'owner',
        account_id: null,
        username: null,
        campaign_id: 1,
        name: 'Danny',
        character_name: 'Ember',
        race: 'Human',
        sex: 'female',
        profile_image: '',
        class_: 'Wizard',
        char_class: 'Wizard',
        level: 2,
        created_at: null,
        updated_at: null,
      },
      {
        player_id: 2,
        workspace_id: 'owner',
        account_id: null,
        username: null,
        campaign_id: 1,
        name: 'Maya',
        character_name: 'Borin',
        race: 'Dwarf',
        sex: 'male',
        profile_image: '',
        class_: 'Fighter',
        char_class: 'Fighter',
        level: 2,
        created_at: null,
        updated_at: null,
      },
    ]
    const timeline: TimelineEntry[] = [
      {
        id: 'dm-20',
        role: 'dm',
        speaker: 'DM',
        text: 'Everyone roll initiative before the blast lands.',
        timestamp: null,
        metadata: {
          turn_id: 20,
          turn_number: 3,
          requires_roll: true,
          outcome_status: 'deferred',
          rule_type: 'initiative',
          remaining_player_ids: [1, 2],
        },
      },
      {
        id: 'roll-20-partial',
        role: 'system',
        speaker: 'System',
        text: 'Check resolved.',
        timestamp: null,
        metadata: {
          turn_id: 21,
          resolved_turn_id: 20,
          remaining_player_ids: [2],
        },
      },
    ]

    expect(pendingRollNoticeFromTimeline(timeline, players, 1)).toMatchObject({
      turnId: 20,
      waitingOnLabel: 'Borin',
      waitingPlayerIds: [2],
      waitingPlayerNames: ['Borin'],
      turnLabel: 'Turn 3',
      ruleLabel: 'initiative',
      detail: 'Everyone roll initiative before the blast lands.',
      pendingCount: 1,
      isWaitingOnSelectedPlayer: false,
      guidance: {
        pendingTurnId: 20,
        ruleType: 'initiative',
        remainingPlayerIds: [2],
        rollSpec: {
          die: 'd20',
          mode: 'normal',
          ruleType: 'initiative',
          reason: 'initiative',
          resultVisibility: 'hidden_until_landed',
          ability: null,
        },
      },
    })

    expect(
      pendingRollNoticeFromTimeline(
        [
          ...timeline,
          {
            id: 'roll-20-final',
            role: 'system',
            speaker: 'System',
            text: 'Check resolved.',
            timestamp: null,
            metadata: {
              turn_id: 22,
              resolved_turn_id: 20,
              remaining_player_ids: [],
            },
          },
        ],
        players,
        2,
      ),
    ).toBeNull()
  })

  it('derives speaker detail, recent memory, truncation, and map meta', () => {
    const player: Player = {
      player_id: 1,
      workspace_id: 'owner',
      account_id: null,
      username: null,
      campaign_id: 1,
      name: 'Danny',
      character_name: 'Ember',
      race: 'Elf',
      sex: 'female',
      profile_image: '/profile-icons/elf_female.png',
      class_: 'Wizard',
      char_class: 'Wizard',
      level: 2,
      created_at: null,
      updated_at: null,
    }
    const playerEntry: TimelineEntry = {
      id: 'log-1',
      role: 'player',
      speaker: 'Ember',
      text: 'I search.',
      timestamp: null,
      metadata: {},
    }
    expect(speakerDetail(playerEntry, player)).toBe('Elf Wizard')
    expect(truncateText('  This   text   should be compacted before it is shortened. ', 24)).toBe(
      'This text should be com…',
    )

    const snippets = memorySnippetRecords([
      { turn_id: 4, dm_output: '**The gate remembers Ember.**' },
      { turn_id: 5, player_input: 'Ember asks about the ash crown.' },
      'bad value',
    ])
    expect(recentMemoryFromSnippets(snippets, 9)).toEqual([
      ['Ember asks about the ash crown.', 'S9E5'],
      ['The gate remembers Ember.', 'S9E4'],
    ])

    const map: MapItem = {
      map_id: 1,
      world_id: 1,
      campaign_id: 1,
      title: 'Ash Gate',
      description: 'A locked gate.',
      map_data: { explored_percent: 72.4, threat: 'High', weather: 'Cold rain' },
      visibility: 'player',
      created_at: null,
      updated_at: null,
    }
    const segment: CampaignSegment = {
      segment_id: 1,
      campaign_id: 1,
      title: 'Ambush',
      description: '',
      trigger_condition: '',
      tags: 'high',
      external_id: null,
      source: 'manual',
      source_pack_id: null,
      metadata: {},
      is_triggered: true,
      created_at: null,
      updated_at: null,
    }
    expect(buildMapMeta(map, segment)).toEqual({
      explored: '72%',
      threat: 'High',
      threatTone: 'high',
      weather: 'Cold rain',
    })
  })

  it('builds world scene panel data from a live state snapshot', () => {
    expect(
      worldStateFromSnapshot({
        currentScene: {
          name: 'Blackwake Tavern',
          sceneType: 'social',
          mood: 'tense',
          dangerLevel: 2,
          activeQuestIds: ['find_missing_sailor'],
        },
        quests: [
          {
            id: 'find_missing_sailor',
            title: 'Find the Missing Sailor',
            status: 'active',
            stage: 'Investigate the docks',
          },
        ],
        locations: [
          { id: 'blackwake_tavern', name: 'Blackwake Tavern', status: 'visited', type: 'tavern' },
        ],
        knownNpcs: [
          {
            id: 'captain_velra',
            name: 'Captain Velra',
            race: 'Human',
            role: 'dock captain',
            disposition: 'friendly',
            status: 'met',
          },
        ],
      }),
    ).toEqual({
      sceneName: 'Blackwake Tavern',
      sceneDescription: '',
      sceneType: 'social',
      mood: 'tense',
      dangerLevel: '2',
      activeQuests: [
        {
          id: 'find_missing_sailor',
          title: 'Find the Missing Sailor',
          status: 'active',
          stage: 'Investigate the docks',
          objective: '',
          objectives: [],
        },
      ],
      presentNpcs: [],
      sceneItems: [],
      availableExits: [],
      knownLocations: [
        {
          id: 'blackwake_tavern',
          name: 'Blackwake Tavern',
          status: 'visited',
          type: 'tavern',
        },
      ],
      knownNpcs: [
        {
          id: 'captain_velra',
          name: 'Captain Velra',
          race: 'Human',
          role: 'dock captain',
          disposition: 'friendly',
          status: 'met',
        },
      ],
      combat: {
        active: false,
        status: 'none',
        round: '1',
        battlefield: 'No battlefield recorded',
        goal: 'Resolve the threat',
        creatureSource: '',
        resolverMethod: '',
        tacticalLevel: 'normal',
        endReason: '',
        combatStartedBy: '',
        initiativeRequired: false,
        debugEnabled: false,
        enemyGroupSummary: '',
        enemies: [],
        allies: [],
        telegraphs: [],
        legalActionBundles: [],
        turnOrder: [],
      },
      clock: { day: 1, phase: 'morning', label: 'Day 1 · Morning' },
      merchants: [],
      finishedQuests: [],
      clues: [],
      handouts: [],
      lore: [],
      ending: null,
    })
  })

  it('carries every objective, the finished quests, what the party has discovered and the ending', () => {
    const panel = worldStateFromSnapshot({
      currentScene: { name: 'Waystation', activeQuestIds: ['road', 'bell'] },
      quests: [
        {
          id: 'road',
          title: 'The Road of Unremembered Kings',
          status: 'active',
          stage: 'Follow the road',
          objectives: [
            { id: 'stone', description: 'Recover the road-stone fragment', status: 'completed' },
            { id: 'names', description: 'Learn why the names were erased', status: 'open' },
            { id: 'cairns', title: 'Pass the cairns', status: 'blocked' },
            { id: 'ward', description: 'Spare the wardens', status: 'failed' },
            { id: 'shrine', description: 'Light the roadside shrine', status: 'open', optional: true },
            { id: 'toll', description: 'Pay the ferry toll', status: 'optional' },
            { id: 'empty', status: 'open' },
          ],
        },
        { id: 'bell', title: 'The Bell at Third Mile', status: 'completed', stage: 'Silenced' },
        { id: 'escort', title: 'Escort the Drivers', status: 'failed', objectives: [{ id: 'drivers', description: 'Bring them home', status: 'failed' }] },
      ],
      clues: [
        { id: 'clue_stone', name: 'Pale Road-Stone Fragment', description: 'Half a name-rune.' },
        { id: 'clue_ledger', title: 'Ledger', summary: 'Every name scratched away.', status: 'discovered' },
        { id: 'clue_secret', title: 'Hidden mark', status: 'undiscovered' },
        { title: 'No id' },
      ],
      handouts: [{ id: 'handout_page', title: 'Page from the Ledger', summary: 'A ledger page.', text: 'Route: Lantern Post by dusk.' }],
      lore: [{ id: 'lore_oath', title: 'The Roadward Oath', summary: 'Protect travellers without price.' }],
      campaignPack: {
        title: 'The Road',
        activeCheckpointId: 'cp_07',
        checkpoints: [
          { id: 'cp_06', title: 'The Last Road-Stone', status: 'completed', terminal: false },
          { id: 'cp_07', title: 'The Road Remembers', summary: 'The names return to the road.', status: 'reached', terminal: true },
        ],
        ending: { checkpointId: 'cp_07', title: 'The Road Remembers', summary: 'The names return to the road.' },
      },
    })

    expect(panel.activeQuests).toHaveLength(1)
    expect(panel.activeQuests[0]).toMatchObject({ id: 'road', objective: 'Learn why the names were erased' })
    expect(panel.activeQuests[0].objectives).toEqual([
      { id: 'stone', description: 'Recover the road-stone fragment', status: 'completed', optional: false },
      { id: 'names', description: 'Learn why the names were erased', status: 'open', optional: false },
      { id: 'cairns', description: 'Pass the cairns', status: 'blocked', optional: false },
      { id: 'ward', description: 'Spare the wardens', status: 'failed', optional: false },
      { id: 'shrine', description: 'Light the roadside shrine', status: 'open', optional: true },
      { id: 'toll', description: 'Pay the ferry toll', status: 'open', optional: false },
    ])
    expect(panel.finishedQuests?.map((quest) => [quest.id, quest.status])).toEqual([
      ['bell', 'completed'],
      ['escort', 'failed'],
    ])
    expect(panel.clues).toEqual([
      { id: 'clue_stone', title: 'Pale Road-Stone Fragment', summary: 'Half a name-rune.' },
      { id: 'clue_ledger', title: 'Ledger', summary: 'Every name scratched away.' },
    ])
    expect(panel.handouts).toEqual([
      { id: 'handout_page', title: 'Page from the Ledger', summary: 'A ledger page.', text: 'Route: Lantern Post by dusk.' },
    ])
    expect(panel.lore).toEqual([{ id: 'lore_oath', title: 'The Roadward Oath', summary: 'Protect travellers without price.' }])
    expect(panel.ending).toEqual({ title: 'The Road Remembers', summary: 'The names return to the road.' })
  })

  it('has no ending until the party reaches a terminal checkpoint', () => {
    const pack = (status: string, extra: Record<string, unknown> = {}) => ({
      campaignPack: { title: 'The Road', checkpoints: [{ id: 'cp_end', title: 'Journey’s End', status, ...extra }] },
    })
    // Active is the goal the party is still working towards, not the ending.
    expect(worldStateFromSnapshot(pack('open', { terminal: true })).ending).toBeNull()
    expect(worldStateFromSnapshot(pack('active', { terminal: true })).ending).toBeNull()
    expect(worldStateFromSnapshot(pack('completed', { terminal: false })).ending).toBeNull()
    expect(worldStateFromSnapshot(pack('completed', { type: 'finale' })).ending).toBeNull()
    expect(worldStateFromSnapshot(pack('reached', { terminal: true })).ending).toEqual({ title: 'Journey’s End', summary: '' })
    expect(worldStateFromSnapshot(pack('completed', { terminal: true })).ending).toEqual({ title: 'Journey’s End', summary: '' })
    expect(
      worldStateFromSnapshot({ campaignPack: { title: 'The Road', ending: { checkpointId: 'cp_end', summary: 'Home at last.' } } }).ending,
    ).toEqual({ title: 'The Road', summary: 'Home at last.' })
  })

  it('reads the day clock and the merchants open in the scene', () => {
    const panel = worldStateFromSnapshot({
      clock: { day: 3, phase: 'evening', shortRestsSinceLong: 1, label: 'Day 3 · Evening' },
      currentScene: {
        name: 'Market',
        merchants: [
          {
            id: 'mara_shop',
            name: "Mara's Sundries",
            npcId: 'mara',
            buyRate: 0.5,
            stock: [
              { stockId: 'mara_0', name: 'Potion of Healing', type: 'potion', price: { gp: 50 }, quantity: 2 },
              { stockId: 'mara_1', name: 'Lantern', type: 'gear', price: { gp: 5, sp: 5 }, quantity: null },
            ],
          },
          { name: 'No id' },
        ],
      },
    })

    expect(panel.clock).toEqual({ day: 3, phase: 'evening', label: 'Day 3 · Evening' })
    expect(panel.merchants).toEqual([
      {
        id: 'mara_shop',
        name: "Mara's Sundries",
        npcId: 'mara',
        buyRate: 0.5,
        stock: [
          { stockId: 'mara_0', name: 'Potion of Healing', type: 'potion', price: { gp: 50 }, priceLabel: '50 gp', quantity: 2 },
          { stockId: 'mara_1', name: 'Lantern', type: 'gear', price: { gp: 5, sp: 5 }, priceLabel: '5 gp, 5 sp', quantity: null },
        ],
      },
    ])
    expect(worldStateFromSnapshot({ clock: { day: 0, phase: 'dusk' } }).clock).toEqual({
      day: 1,
      phase: 'morning',
      label: 'Day 1 · Morning',
    })
  })

  it('parses viewer-scoped server combat actions without inventing client mechanics', () => {
    const panel = worldStateFromSnapshot({
      combat: {
        status: 'active',
        round: 2,
        participants: [
          { id: 'player_30', name: 'Ember', team: 'player', hp: { current: 20, max: 20 } },
          { id: 'enemy_goblin_1', name: 'Goblin', team: 'enemy', hp: { current: 7, max: 7 } },
        ],
        legalActions: [
          {
            schemaVersion: 1,
            playerId: 30,
            actorId: 'player_30',
            actorName: 'Ember',
            round: 2,
            currentActorId: 'player_30',
            currentActorName: 'Ember',
            isCurrentActor: true,
            economy: {
              tracking: 'persisted_turn_economy',
              subTurnCountersTracked: true,
              actionRemaining: 1,
              bonusActionRemaining: 0,
              movementRemaining: 1,
              reactionRemaining: 1,
            },
            actions: [
              {
                id: 'combat.attack.blade',
                type: 'attack',
                label: 'Attack with Longsword',
                available: true,
                authoritative: true,
                requiresTarget: true,
                economy: {
                  action: 1,
                  movement: 'optional',
                  endsTurn: true,
                  tracking: 'turn_order_derived',
                  reactionTracked: false,
                  subTurnCountersTracked: false,
                },
                weapon: { name: 'Longsword' },
                range: { classification: 'melee', allowedBands: ['melee', 'near'] },
                targets: [
                  {
                    id: 'enemy_goblin_1',
                    name: 'Goblin',
                    rangeBand: 'near',
                    available: true,
                  },
                ],
              },
            ],
          },
        ],
      },
    })

    expect(panel.combat.legalActionBundles[0]).toMatchObject({
      playerId: 30,
      actorName: 'Ember',
      isCurrentActor: true,
      economyTracking: 'persisted_turn_economy',
      subTurnCountersTracked: true,
      actionRemaining: 1,
      bonusActionRemaining: 0,
      movementRemaining: 1,
      reactionRemaining: 1,
      actions: [
        {
          id: 'combat.attack.blade',
          authoritative: true,
          weaponName: 'Longsword',
          rangeClassification: 'melee',
          allowedRangeBands: ['melee', 'near'],
          targets: [{ id: 'enemy_goblin_1', available: true }],
        },
      ],
    })
  })

  it('normalises engagement, death saves, action costs, extra attacks and the initiative ribbon', () => {
    const panel = worldStateFromSnapshot({
      combat: {
        status: 'active',
        round: 3,
        participants: [
          {
            id: 'player_30',
            name: 'Ember',
            team: 'player',
            hp: { current: 0, max: 20 },
            conditions: ['unconscious', 'dying'],
            deathSaves: { successes: 1, failures: 5 },
            position: { rangeBand: 'melee', engagedWith: ['enemy_goblin_1', ''] },
          },
          {
            id: 'enemy_goblin_1',
            name: 'Goblin',
            team: 'enemy',
            hp: { current: 7, max: 7 },
            position: { rangeBand: 'melee', engagedWith: ['player_30'] },
          },
          { id: 'enemy_archer_1', name: 'Archer', team: 'enemy', hp: { current: 5, max: 5 }, position: { rangeBand: 'far' } },
          // Another player's character: the server shows its position to every player.
          {
            id: 'player_31',
            playerId: 31,
            name: 'Brannoc',
            team: 'player',
            conditions: [],
            position: { rangeBand: 'near', engagedWith: [] },
          },
        ],
        turnOrder: [
          { id: 'player_30', name: 'Ember', team: 'player', isCurrent: true },
          { id: 'enemy_goblin_1', name: 'Goblin', team: 'enemy', isCurrent: false },
          { name: 'Nameless' },
        ],
        legalActions: [
          {
            playerId: 30,
            actorId: 'player_30',
            attacksRemaining: 1,
            reactionRemaining: 0,
            economy: { actionRemaining: 0, reactionRemaining: 1 },
            actions: [
              {
                id: 'combat.attack.blade',
                type: 'attack',
                economy: { action: 0, movement: 'optional' },
                cost: { action: 0, bonusAction: 0, movement: 0, reaction: 0 },
              },
              {
                id: 'combat.offhand_attack',
                type: 'attack',
                economy: { action: 0, bonusAction: 1, movement: 'optional' },
              },
              { id: 'combat.advance', type: 'advance', cost: { action: 0, bonusAction: 0, movement: 1, reaction: 0 } },
            ],
          },
        ],
      },
    })

    const [ember, brannoc, goblin, archer] = [...panel.combat.allies, ...panel.combat.enemies]
    expect(ember).toMatchObject({ rangeBand: 'melee', engagedWith: ['enemy_goblin_1'], deathSaves: { successes: 1, failures: 3 } })
    expect(brannoc).toMatchObject({ name: 'Brannoc', rangeBand: 'near', engagedWith: [], position: 'near' })
    expect(goblin).toMatchObject({ rangeBand: 'melee', engagedWith: ['player_30'], deathSaves: null })
    expect(archer).toMatchObject({ rangeBand: 'far', engagedWith: [] })
    const bundle = panel.combat.legalActionBundles[0]
    expect(bundle.attacksRemaining).toBe(1)
    expect(bundle.reactionRemaining).toBe(0)
    expect(bundle.actions.map((action) => action.cost)).toEqual([
      { action: 0, bonusAction: 0, movement: 0, reaction: 0 },
      { action: 0, bonusAction: 1, movement: 0, reaction: 0 },
      { action: 0, bonusAction: 0, movement: 1, reaction: 0 },
    ])
    expect(panel.combat.turnOrder).toEqual([
      { id: 'player_30', name: 'Ember', team: 'player', isCurrent: true },
      { id: 'enemy_goblin_1', name: 'Goblin', team: 'enemy', isCurrent: false },
    ])
  })

  it('prioritizes active and recent NPCs while hiding player-character duplicates', () => {
    const panel = worldStateFromSnapshot({
      currentScene: {
        name: 'Blackwake Tavern',
        activeNpcIds: ['marta_fenwick', 'captain_velra'],
      },
      playerCharacters: [{ id: 'player_1', playerId: 1, name: 'Kozuki' }],
      knownNpcs: [
        { id: 'oden', name: 'Oden', role: 'old leak', lastSeenTurn: 270 },
        { id: 'kozuki', name: 'Kozuki', role: 'mistaken PC', lastSeenTurn: 999 },
        { id: 'captain_velra', name: 'Captain Velra', role: 'dock captain', lastSeenTurn: 12 },
        { id: 'marta_fenwick', name: 'Marta Fenwick', role: 'shopkeeper', lastSeenTurn: 8 },
        { id: 'new_sentry', name: 'New Sentry', role: 'guard', lastSeenTurn: 300 },
      ],
    })

    expect(panel.knownNpcs.map((npc) => npc.id)).toEqual([
      'marta_fenwick',
      'captain_velra',
      'new_sentry',
      'oden',
    ])
    expect(panel.presentNpcs.map((npc) => npc.id)).toEqual([
      'marta_fenwick',
      'captain_velra',
    ])
  })

  it('keeps the current decision context together for the play surface', () => {
    const panel = worldStateFromSnapshot({
      currentScene: {
        locationId: 'lantern_post',
        name: 'The Lantern Post Inn',
        description: 'Rain taps the shutters while an abandoned caravan waits outside.',
        sceneType: 'exploration',
        activeNpcIds: ['mara'],
        activeQuestIds: ['missing_caravan'],
        items: [{ id: 'muddy_track', name: 'Muddy track', quantity: 1, type: 'clue' }],
      },
      knownNpcs: [{ id: 'mara', name: 'Mara Reedwake', role: 'innkeeper' }],
      quests: [
        {
          id: 'missing_caravan',
          title: 'The Empty Caravan',
          status: 'active',
          stage: 'Investigate outside',
          objectives: [
            { id: 'ask', description: 'Question the witnesses', status: 'completed' },
            { id: 'tracks', description: 'Follow the tracks', status: 'active' },
          ],
        },
      ],
      locations: [
        {
          id: 'lantern_post',
          name: 'The Lantern Post Inn',
          status: 'visited',
          connectedLocationIds: ['old_road'],
        },
        { id: 'old_road', name: 'The Old North Road', status: 'known' },
      ],
    })

    expect(panel).toMatchObject({
      sceneName: 'The Lantern Post Inn',
      sceneDescription: 'Rain taps the shutters while an abandoned caravan waits outside.',
      presentNpcs: [{ id: 'mara', name: 'Mara Reedwake' }],
      sceneItems: [{ id: 'muddy_track', name: 'Muddy track', quantity: 1, type: 'clue' }],
      availableExits: [{ id: 'old_road', name: 'The Old North Road' }],
      activeQuests: [{ id: 'missing_caravan', objective: 'Follow the tracks' }],
    })
  })

  it('shows every known NPC after active and recent entries', () => {
    const panel = worldStateFromSnapshot({
      knownNpcs: Array.from({ length: 10 }, (_, index) => ({
        id: `npc_${index}`,
        name: `NPC ${index}`,
        role: 'traveler',
        lastSeenTurn: index,
      })),
    })

    expect(panel.knownNpcs).toHaveLength(10)
    expect(panel.knownNpcs.map((npc) => npc.id)).toEqual([
      'npc_9',
      'npc_8',
      'npc_7',
      'npc_6',
      'npc_5',
      'npc_4',
      'npc_3',
      'npc_2',
      'npc_1',
      'npc_0',
    ])
  })

  it('shows every known place with the current and newest visited locations first', () => {
    const panel = worldStateFromSnapshot({
      currentScene: {
        locationId: 'moon_market',
        name: 'Moon Market',
      },
      locations: [
        { id: 'old_ruins', name: 'Old Ruins', status: 'visited', type: 'ruins', lastVisitedTurn: 3 },
        { id: 'moon_market', name: 'Moon Market', status: 'visited', type: 'town', lastVisitedTurn: 4 },
        { id: 'new_docks', name: 'New Docks', status: 'visited', type: 'road', lastVisitedTurn: 8 },
        { id: 'watchtower', name: 'Watchtower', status: 'discovered', type: 'castle', firstDiscoveredTurn: 9 },
        { id: 'ash_gate', name: 'Ash Gate', status: 'visited', type: 'ruins', lastVisitedTurn: 6 },
        { id: 'far_road', name: 'Far Road', status: 'known', type: 'road', updatedAtTurn: 2 },
        { id: 'sealed_vault', name: 'Sealed Vault', status: 'hidden', type: 'dungeon', lastVisitedTurn: 10 },
      ],
    })

    expect(panel.knownLocations.map((location) => location.id)).toEqual([
      'moon_market',
      'new_docks',
      'ash_gate',
      'old_ruins',
      'watchtower',
      'far_road',
    ])
  })
})

describe('opening request visibility', () => {
  const openingText =
    'Please narrate the opening scene for this campaign. Campaign: Smoke Campaign. Session: Alpha.'

  it('hides the opening request flagged by message kind or by its legacy text', () => {
    const logEntries: SessionLogEntry[] = [
      {
        id: 1,
        entry_type: 'player',
        message: 'Ember: Inspect the door.',
        metadata: { turn_id: 1, message_kind: 'opening' },
        timestamp: null,
      },
      {
        id: 2,
        entry_type: 'player',
        message: `Ember: ${openingText}`,
        metadata: { turn_id: 2 },
        timestamp: null,
      },
      {
        id: 3,
        entry_type: 'player',
        message: openingText,
        metadata: { turn_id: 3 },
        timestamp: null,
      },
      {
        id: 4,
        entry_type: 'dm',
        message: 'DM: Rain hammers the watchtower roof.',
        metadata: { turn_id: 3 },
        timestamp: null,
      },
      {
        id: 5,
        entry_type: 'player',
        message: 'Ember: I climb the ladder.',
        metadata: { turn_id: 4 },
        timestamp: null,
      },
    ]
    const pendingOpening: TimelineEntry = {
      id: 'local-1',
      role: 'player',
      speaker: 'Ember',
      text: openingText,
      timestamp: null,
      metadata: { client_message_id: 'opening-1', message_kind: 'opening', persistence_status: 'pending' },
    }

    const timeline = buildTimeline({
      logEntries,
      optimisticEntries: [pendingOpening],
      streamingTurn: null,
      turnStatuses: {},
    })

    expect(timeline.map((entry) => entry.id)).toEqual(['log-4', 'log-5'])
    expect(timeline.some((entry) => entry.text.includes('Please narrate'))).toBe(false)
  })
})

describe('worldMapFromSnapshot', () => {
  it('returns the projected map typed, dropping malformed nodes and dangling routes', () => {
    const map = worldMapFromSnapshot({
      worldMap: {
        title: 'The Western Roadlands',
        terrain: 'moor',
        seed: 7,
        nodes: [
          { id: 'inn', name: 'Lantern Post Inn', icon: 'inn', x: 20, y: 30, state: 'current' },
          { id: 'road', name: 'Old North Road', icon: 'road', x: '55', y: 140, state: 'visited' },
          { id: 'ridge', name: '?', icon: 'unknown', x: 80, y: 70, state: 'rumoured' },
          { id: 'bad_state', name: 'Nowhere', x: 10, y: 10, state: 'secret' },
          { id: '', name: 'No id', x: 10, y: 10, state: 'known' },
        ],
        routes: [
          { from: 'inn', to: 'road', style: 'road', travelable: true },
          { from: 'road', to: 'ridge', style: 'trail', travelable: false },
          { from: 'road', to: 'bad_state', style: 'road', travelable: true },
          { from: 'inn', to: 'ridge', style: 'teleport' },
        ],
      },
    })

    expect(map).toEqual({
      title: 'The Western Roadlands',
      terrain: 'moor',
      seed: 7,
      nodes: [
        { id: 'inn', name: 'Lantern Post Inn', icon: 'inn', x: 20, y: 30, state: 'current' },
        { id: 'road', name: 'Old North Road', icon: 'road', x: 55, y: 100, state: 'visited' },
        { id: 'ridge', name: '?', icon: 'unknown', x: 80, y: 70, state: 'rumoured' },
      ],
      routes: [
        { from: 'inn', to: 'road', style: 'road', travelable: true },
        { from: 'road', to: 'ridge', style: 'trail', travelable: false },
      ],
    })
  })

  it('defaults the route style to road and is null without a drawable map', () => {
    const map = worldMapFromSnapshot({
      worldMap: {
        nodes: [
          { id: 'a', name: 'A', x: 8, y: 8, state: 'known' },
          { id: 'b', name: 'B', x: 92, y: 92, state: 'known' },
        ],
        routes: [{ from: 'a', to: 'b' }],
      },
    })

    expect(map?.terrain).toBe('plains')
    expect(map?.nodes[0].icon).toBe('unknown')
    expect(map?.routes).toEqual([{ from: 'a', to: 'b', style: 'road', travelable: false }])
    expect(worldMapFromSnapshot({})).toBeNull()
    expect(worldMapFromSnapshot({ worldMap: { nodes: [] } })).toBeNull()
    expect(worldMapFromSnapshot(null)).toBeNull()
  })
})
