import { describe, expect, it } from 'vitest'
import type { CombatStatePanel, WorldStatePanel } from '../gameSelectors'
import type { GameplayControlState, GameplayInteractableControl } from '../gameplayControlState'
import { STORY_SUGGESTION_LIMIT, storySuggestions } from './storySuggestions'

function combat(active = false): CombatStatePanel {
  return {
    active,
    status: active ? 'active' : 'none',
    round: '1',
    battlefield: '',
    goal: '',
    creatureSource: '',
    resolverMethod: '',
    tacticalLevel: 'normal',
    endReason: '',
    combatStartedBy: '',
    enemyGroupSummary: '',
    initiativeRequired: false,
    debugEnabled: false,
    enemies: [],
    allies: [],
    telegraphs: [],
    legalActionBundles: [],
  }
}

function world(overrides: Partial<WorldStatePanel> = {}): WorldStatePanel {
  return {
    sceneName: 'The Drowned Lantern',
    sceneDescription: '',
    sceneType: 'social',
    mood: 'uneasy',
    dangerLevel: '1',
    activeQuests: [],
    presentNpcs: [
      { id: 'marta', name: 'Marta Vell', race: 'Human', role: 'Innkeeper', disposition: 'wary', status: 'present' },
    ],
    sceneItems: [{ id: 'ledger', name: 'caravan ledger', quantity: 1, type: 'object' }],
    availableExits: [{ id: 'causeway', name: 'North causeway', status: 'known', type: 'road' }],
    knownLocations: [],
    knownNpcs: [],
    combat: combat(),
    ...overrides,
  }
}

function wagon(overrides: Partial<GameplayInteractableControl> = {}): GameplayInteractableControl {
  return {
    id: 'lead-wagon',
    name: 'Lead wagon',
    kind: 'container',
    description: '',
    revision: 3,
    states: [],
    available: true,
    reason: '',
    actions: [
      { id: 'inspect', label: 'Inspect', available: true, reason: '' },
      { id: 'open', label: 'Open', available: false, reason: 'Already open.' },
      { id: 'search', label: 'Search', available: true, reason: '' },
    ],
    ...overrides,
  }
}

function controls(overrides: Partial<GameplayControlState> = {}): GameplayControlState {
  return {
    actorId: 'player_30',
    actorName: 'Ysolde',
    inCombat: false,
    activeActorId: '',
    isActorTurn: true,
    actionRemaining: null,
    bonusActionRemaining: null,
    concentration: '',
    spells: [],
    capabilities: [],
    interactables: [wagon()],
    ...overrides,
  }
}

describe('storySuggestions', () => {
  it('orders people, scene objects, items and exits', () => {
    const suggestions = storySuggestions(world(), controls())

    expect(suggestions.map((suggestion) => suggestion.label)).toEqual([
      'Talk to Marta Vell',
      'Search Lead wagon',
      'Examine caravan ledger',
      'Go to North causeway',
    ])
  })

  it('fills the composer for talk and examine, and types object and travel actions', () => {
    const [talk, object, examine, travel] = storySuggestions(world(), controls())

    expect(talk).toMatchObject({ kind: 'talk', text: 'I talk to Marta Vell.' })
    expect(examine).toMatchObject({ kind: 'examine', text: 'I examine the caravan ledger.' })
    expect(object).toMatchObject({ kind: 'object', object: { id: 'lead-wagon' }, action: { id: 'search' } })
    expect(travel).toMatchObject({ kind: 'travel', exit: { id: 'causeway', name: 'North causeway' } })
  })

  it('caps the row at five', () => {
    const presentNpcs = ['Marta Vell', 'Old Teodor', 'Brother Ansel', 'Pell', 'Corin', 'Wren'].map((name) => ({
      id: name.toLowerCase().replace(/\s+/g, '-'),
      name,
      race: '',
      role: '',
      disposition: '',
      status: 'present',
    }))

    const suggestions = storySuggestions(world({ presentNpcs }), controls())

    expect(suggestions).toHaveLength(STORY_SUGGESTION_LIMIT)
    expect(suggestions.every((suggestion) => suggestion.kind === 'talk')).toBe(true)
  })

  it('skips objects with nothing but inspect, unavailable objects, dead people and unnamed exits', () => {
    const suggestions = storySuggestions(
      world({
        presentNpcs: [
          { id: 'body', name: 'The drowned driver', race: '', role: '', disposition: '', status: 'dead' },
        ],
        availableExits: [{ id: '', name: 'Somewhere', status: 'known', type: 'road' }],
        sceneItems: [],
      }),
      controls({
        interactables: [
          wagon({ id: 'crate', name: 'Crate', actions: [{ id: 'inspect', label: 'Inspect', available: true, reason: '' }] }),
          wagon({ id: 'gate', name: 'Gate', available: false, reason: 'Sealed.' }),
        ],
      }),
    )

    expect(suggestions).toEqual([])
  })

  it('leads with trading at each open merchant', () => {
    const merchant = { id: 'marta_larder', name: 'Marta Vell', npcId: 'marta', buyRate: 0.5, stock: [] }
    const suggestions = storySuggestions(world({ merchants: [merchant] }), controls())
    expect(suggestions[0]).toEqual({ kind: 'trade', key: 'trade:marta_larder', label: 'Trade with Marta Vell', merchant })
    expect(suggestions[1].label).toBe('Talk to Marta Vell')
  })

  it('suggests nothing in combat', () => {
    expect(storySuggestions(world({ combat: combat(true) }), controls())).toEqual([])
    expect(storySuggestions(world(), controls({ inCombat: true }))).toEqual([])
  })
})
