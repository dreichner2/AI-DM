import { describe, expect, it } from 'vitest'
import type { CombatStatePanel, WorldStatePanel } from '../gameSelectors'
import type { GameplayControlState, GameplayInteractableControl, GameplayObjectAction } from '../gameplayControlState'
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

function npc(name: string, status = 'present') {
  return { id: name.toLowerCase().replace(/\s+/g, '-'), name, race: '', role: '', disposition: '', status }
}

function place(name: string, status = 'known') {
  return { id: name.toLowerCase().replace(/\s+/g, '-'), name, status, type: 'road' }
}

function world(overrides: Partial<WorldStatePanel> = {}): WorldStatePanel {
  return {
    sceneName: 'The Lantern Post',
    sceneDescription: '',
    sceneType: 'social',
    mood: 'uneasy',
    dangerLevel: '1',
    activeQuests: [],
    presentNpcs: [],
    sceneItems: [],
    availableExits: [],
    knownLocations: [],
    knownNpcs: [],
    combat: combat(),
    ...overrides,
  }
}

function offer(id: GameplayObjectAction['id'], label: string, overrides: Partial<GameplayObjectAction> = {}): GameplayObjectAction {
  return { id, label, available: true, reason: '', phrase: true, ...overrides }
}

function object(id: string, name: string, actions: GameplayObjectAction[] | null): GameplayInteractableControl {
  return {
    id,
    name,
    kind: 'object',
    description: '',
    revision: 1,
    states: [],
    available: true,
    reason: '',
    ...(actions ? { offered: true, actions } : { actions: [{ id: 'open', label: 'Open', available: true, reason: '' }] }),
  }
}

function controls(interactables: GameplayInteractableControl[] = [], overrides: Partial<GameplayControlState> = {}): GameplayControlState {
  return {
    actorId: 'player_30',
    actorName: 'Liora',
    inCombat: false,
    activeActorId: '',
    isActorTurn: true,
    actionRemaining: null,
    bonusActionRemaining: null,
    concentration: '',
    spells: [],
    capabilities: [],
    interactables,
    ...overrides,
  }
}

const ledger = object('ledger', 'Caravan Ledger', [
  offer('search', 'Search Caravan Ledger'),
  offer('inspect', 'Read the caravan ledger', { authored: true }),
])
const bench = object('bench', "Driver's Bench", [
  offer('search', "Search under the driver's bench", { authored: true }),
  offer('open', "Open Driver's Bench", { available: false, reason: 'It is already open.' }),
  offer('inspect', "Look over the driver's bench", { authored: true, done: true, available: false }),
])
const strongbox = object('strongbox', 'Wagon Strongbox', [
  offer('unlock', 'Unlock the strongbox', { authored: true, available: false, reason: 'It is locked, and you carry no key that fits.' }),
  offer('inspect', 'Examine Wagon Strongbox'),
])
const bell = object('bell', 'Wagon Bell', [offer('use', 'Ring the wagon bell', { authored: true })])
const wagon = object('wagon', 'Lead Wagon', [offer('search', 'Search the lead wagon', { authored: true })])
const key = { id: 'key', name: 'Caravan Strongbox Key', quantity: 1, type: 'key', takeLabel: 'Take the Caravan Strongbox Key' }
const coins = { id: 'coins', name: 'gp', quantity: 15, type: 'currency', takeLabel: 'Take 15 gp' }
const pebble = { id: 'pebble', name: 'Pebble', quantity: 1, type: 'object', takeLabel: 'Take the Pebble' }

type Case = {
  name: string
  world: Partial<WorldStatePanel>
  objects: GameplayInteractableControl[]
  labels: string[]
}

const CASES: Case[] = [
  {
    name: 'authored object actions first (scene order), then the first legal action of objects not shown',
    world: {},
    objects: [ledger, bench, strongbox],
    labels: ['Read the caravan ledger', "Search under the driver's bench", 'Examine Wagon Strongbox', 'More…'],
  },
  {
    name: 'at most three authored actions, then nothing more for other objects',
    world: {},
    objects: [ledger, bench, bell, wagon],
    labels: ['Read the caravan ledger', "Search under the driver's bench", 'Ring the wagon bell', 'More…'],
  },
  {
    name: 'take labels lead, at most two',
    world: { sceneItems: [key, coins, pebble] },
    objects: [],
    labels: ['Take the Caravan Strongbox Key', 'Take 15 gp', 'More…'],
  },
  {
    name: 'every kind in order: take, objects, talk (2), trade (1), go (2, unvisited first), capped at six',
    world: {
      sceneItems: [key],
      presentNpcs: [npc('Mara Reedwake'), npc('Tovin Bell'), npc('Osric Hale'), npc('The drowned driver', 'dead')],
      merchants: [
        { id: 'mara_larder', name: 'Mara Reedwake', npcId: 'mara', buyRate: 0.5, stock: [] },
        { id: 'tovin_cart', name: 'Tovin Bell', npcId: 'tovin', buyRate: 0.5, stock: [] },
      ],
      availableExits: [place('Lantern Post', 'visited'), place('The Old North Road'), place('Ridge and Culvert')],
    },
    objects: [bell],
    labels: [
      'Take the Caravan Strongbox Key',
      'Ring the wagon bell',
      'Talk to Mara Reedwake',
      'Talk to Tovin Bell',
      'Trade with Mara Reedwake',
      'Go to The Old North Road',
      'More…',
    ],
  },
  {
    name: 'no More… when everything fits',
    world: { presentNpcs: [npc('Mara Reedwake')], availableExits: [place('The Old North Road')] },
    objects: [bell],
    labels: ['Ring the wagon bell', 'Talk to Mara Reedwake', 'Go to The Old North Road'],
  },
  {
    name: 'an older server offers only Examine, never a guessed verb',
    world: {},
    objects: [object('crate', 'Crate', null)],
    labels: ['Examine Crate'],
  },
  {
    name: 'nothing for an object whose offered actions are all done or illegal, or a loose item without a take label',
    world: { sceneItems: [{ id: 'ledger-page', name: 'Ledger page', quantity: 1, type: 'paper' }] },
    objects: [object('gate', 'Gate', [offer('open', 'Open the gate', { available: false, reason: 'It is locked.' })])],
    labels: [],
  },
]

describe('storySuggestions', () => {
  it.each(CASES)('$name', ({ world: overrides, objects, labels }) => {
    expect(storySuggestions(world(overrides), controls(objects)).map((suggestion) => suggestion.label)).toEqual(labels)
  })

  it('never shows more than six chips before More…', () => {
    const suggestions = storySuggestions(
      world({
        sceneItems: [key, coins],
        presentNpcs: [npc('Mara Reedwake'), npc('Tovin Bell')],
        merchants: [{ id: 'mara_larder', name: 'Mara Reedwake', npcId: 'mara', buyRate: 0.5, stock: [] }],
        availableExits: [place('The Old North Road')],
      }),
      controls([ledger, bench, bell]),
    )
    expect(suggestions.filter((suggestion) => suggestion.kind !== 'more')).toHaveLength(STORY_SUGGESTION_LIMIT)
    expect(suggestions.at(-1)?.kind).toBe('more')
  })

  it('carries what each chip needs: the item to take, the object and action, the person, the place', () => {
    const [take, read, talk, go] = storySuggestions(
      world({ sceneItems: [key], presentNpcs: [npc('Mara Reedwake')], availableExits: [place('The Old North Road')] }),
      controls([object('ledger', 'Caravan Ledger', [offer('inspect', 'Read the caravan ledger', { authored: true })])]),
    )
    expect(take).toMatchObject({ kind: 'take', item: { id: 'key' } })
    expect(read).toMatchObject({ kind: 'object', object: { id: 'ledger' }, action: { id: 'inspect' } })
    expect(talk).toMatchObject({ kind: 'talk', text: 'I talk to Mara Reedwake.' })
    expect(go).toMatchObject({ kind: 'travel', exit: { id: 'the-old-north-road' } })
  })

  it('suggests nothing in combat', () => {
    expect(storySuggestions(world({ combat: combat(true), sceneItems: [key] }), controls([bell]))).toEqual([])
    expect(storySuggestions(world({ sceneItems: [key] }), controls([bell], { inCombat: true }))).toEqual([])
  })
})
