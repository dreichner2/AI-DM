import { describe, expect, it } from 'vitest'
import type { CombatStatePanel, WorldStatePanel } from '../gameSelectors'
import type { GameplayControlState, GameplayInteractableControl, GameplayObjectAction } from '../gameplayControlState'
import { STORY_SUGGESTION_LIMIT, TAKE_ALL_LABEL, storySuggestions } from './storySuggestions'

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
// Both of its entries are legal and authored, sent out of the contract's order.
const openBench = object('bench', "Driver's Bench", [
  offer('inspect', "Look over the driver's bench", { authored: true }),
  offer('search', "Search under the driver's bench", { authored: true }),
])
const bell = object('bell', 'Wagon Bell', [offer('use', 'Ring the wagon bell', { authored: true })])
const wagon = object('wagon', 'Lead Wagon', [offer('search', 'Search the lead wagon', { authored: true })])
// The Last Road-Stone's four fates are choices, sent in the contract's order; Approach is its one other entry.
const roadStone = object('road-stone', 'The Last Road-Stone', [
  offer('use', 'Renew the ward', { authored: true, choice: true }),
  offer('trigger', 'Claim the ward', { authored: true, choice: true }),
  offer('inspect', 'Approach the Last Road-Stone', { authored: true }),
  offer('lock', 'Hide the road-stone', { authored: true, choice: true }),
  offer('break', 'Break the ward', { authored: true, choice: true }),
])
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
    name: 'one chip per object: its first legal entry in the contract’s order, authored objects first',
    world: {},
    objects: [strongbox, openBench],
    labels: ["Search under the driver's bench", 'Examine Wagon Strongbox', 'More…'],
  },
  {
    name: 'an object’s choices are shown together, then one chip for its other entries',
    world: {},
    objects: [roadStone],
    labels: ['Renew the ward', 'Claim the ward', 'Hide the road-stone', 'Break the ward', 'Approach the Last Road-Stone'],
  },
  {
    name: 'choices come before every other suggestion',
    world: { sceneItems: [key], availableExits: [place('Ridge and Culvert')] },
    objects: [ledger, roadStone],
    labels: [
      'Renew the ward',
      'Claim the ward',
      'Hide the road-stone',
      'Break the ward',
      'Take the Caravan Strongbox Key',
      'Read the caravan ledger',
      'More…',
    ],
  },
  {
    name: 'at most four choices, only legal ones not yet taken; the rest wait in the tray',
    world: {},
    objects: [
      object('road-stone', 'The Last Road-Stone', [
        offer('use', 'Renew the ward', { authored: true, choice: true }),
        offer('trigger', 'Claim the ward', { authored: true, choice: true, available: false, reason: 'The ward is already renewed.' }),
        offer('lock', 'Hide the road-stone', { authored: true, choice: true, available: false, done: true }),
        offer('break', 'Break the ward', { authored: true, choice: true }),
      ]),
      object('altar', 'Cinder Altar', [
        offer('use', 'Light the altar', { choice: true }),
        offer('trigger', 'Wake the altar', { choice: true }),
        offer('break', 'Topple the altar', { choice: true }),
      ]),
    ],
    labels: ['Renew the ward', 'Break the ward', 'Light the altar', 'Wake the altar', 'More…'],
  },
  {
    name: 'a failed check that may be retried says it takes time',
    world: {},
    objects: [object('bench', "Driver's Bench", [offer('search', "Search under the driver's bench", { authored: true, retry: true })])],
    labels: ["Search under the driver's bench — take your time"],
  },
  {
    name: 'at most three objects, then nothing more for other objects',
    world: {},
    objects: [ledger, bench, bell, wagon],
    labels: ['Read the caravan ledger', "Search under the driver's bench", 'Ring the wagon bell', 'More…'],
  },
  {
    name: 'with several loose things, taking everything leads, then the first of them',
    world: { sceneItems: [key, coins, pebble] },
    objects: [],
    labels: ['Take everything here', 'Take the Caravan Strongbox Key', 'More…'],
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

  it('offers one chip that takes everything when several things lie loose', () => {
    const coin = pebble
    const one = storySuggestions(world({ sceneItems: [key] }), controls([]))
    expect(one.map((suggestion) => suggestion.kind)).toEqual(['take'])
    const [all, first, ...rest] = storySuggestions(world({ sceneItems: [key, coin] }), controls([]))
    expect(all).toMatchObject({ kind: 'take_all', label: TAKE_ALL_LABEL })
    expect(first).toMatchObject({ kind: 'take', item: { id: 'key' } })
    expect(rest.filter((suggestion) => suggestion.kind === 'take')).toEqual([])
  })

  it('suggests nothing in combat', () => {
    expect(storySuggestions(world({ combat: combat(true), sceneItems: [key] }), controls([bell]))).toEqual([])
    expect(storySuggestions(world({ sceneItems: [key] }), controls([bell], { inCombat: true }))).toEqual([])
  })
})

describe('storySuggestions in a place with rooms', () => {
  const yard = { id: 'inn_yard', name: 'The Yard', current: true, adjacent: false, exit: true }
  const commonRoom = { id: 'inn_common_room', name: 'The Common Room', current: false, adjacent: true, exit: false }
  const stable = { id: 'inn_stable', name: 'The Stable', current: false, adjacent: true, exit: false }
  const cellar = { id: 'inn_cellar', name: 'The Cellar', current: false, adjacent: false, exit: false }
  const zones = [
    { ...yard, here: [{ id: 'player_30', name: 'Liora', kind: 'hero' }, { id: 'tovin-bell', name: 'Tovin Bell', kind: 'person' }] },
    { ...commonRoom, moveLabel: 'Go into the common room', here: [{ id: 'mara-reedwake', name: 'Mara Reedwake', kind: 'person' }] },
    { ...stable, moveLabel: 'Go to the stable', here: [] },
    { ...cellar, here: [{ id: 'old-hob', name: 'Old Hob', kind: 'person' }] },
  ]
  const mare = { ...object('mare', 'The Off-Leader Mare', [offer('inspect', 'Look over the off-leader', { authored: true })]), zoneId: 'inn_stable' }
  const barrel = { ...object('barrel', 'Barrel', [offer('search', 'Search the barrel', { authored: true })]), zoneId: 'inn_cellar' }

  it('offers what is here first, then what is next door marked with its room, then the rooms next door', () => {
    const labels = storySuggestions(
      world({
        zones,
        presentNpcs: [npc('Mara Reedwake'), npc('Tovin Bell'), npc('Old Hob')],
        merchants: [{ id: 'mara_counter', name: "Mara's Counter", npcId: 'mara-reedwake', buyRate: 0.5, stock: [], zoneId: 'inn_common_room' }],
      }),
      controls([mare, barrel, { ...bell, zoneId: 'inn_yard' }]),
    ).map((suggestion) => suggestion.label)

    expect(labels).toEqual([
      'Ring the wagon bell',
      'Look over the off-leader · stable',
      'Talk to Tovin Bell',
      'Talk to Mara Reedwake · common room',
      "Trade with Mara's Counter · common room",
      'Go into the common room',
      'More…',
    ])
  })

  it('offers a room next door as a move, and the ways onward from whichever room the party is in', () => {
    const fromYard = storySuggestions(world({ zones, availableExits: [place('The Old North Road')] }), controls([]))
    expect(fromYard.map((suggestion) => suggestion.label)).toEqual(['Go into the common room', 'Go to the stable', 'Go to The Old North Road'])
    expect(fromYard[0]).toMatchObject({ kind: 'zone_move', zone: { id: 'inn_common_room' } })

    const inside = zones.map((zone) => ({ ...zone, current: zone.id === 'inn_common_room', adjacent: zone.id === 'inn_yard', ...(zone.id === 'inn_yard' ? { moveLabel: 'Step out to the yard' } : {}) }))
    const fromCommonRoom = storySuggestions(world({ zones: inside, availableExits: [place('The Old North Road')] }), controls([]))
    expect(fromCommonRoom.map((suggestion) => suggestion.label)).toEqual(['Step out to the yard', 'Go to The Old North Road'])
  })
})

describe('Take heart', () => {
  it('is offered last while the hero holds Inspiration, never in a fight', () => {
    const chips = (overrides: Partial<GameplayControlState>) =>
      storySuggestions(world({ availableExits: [place('The Old North Road')] }), controls([bell], overrides))
        .map((suggestion) => [suggestion.kind, suggestion.label])
    expect(chips({ takeHeart: true })).toEqual([
      ['object', 'Ring the wagon bell'],
      ['travel', 'Go to The Old North Road'],
      ['take_heart', 'Take heart (Inspiration)'],
    ])
    expect(chips({}).map(([kind]) => kind)).toEqual(['object', 'travel'])
    expect(chips({ takeHeart: true, inCombat: true })).toEqual([])
  })

  it('waits behind More… when the row is full', () => {
    const suggestions = storySuggestions(
      world({
        sceneItems: [key, coins],
        presentNpcs: [npc('Mara Reedwake'), npc('Tovin Bell')],
        availableExits: [place('The Old North Road')],
      }),
      controls([ledger, bench, bell], { takeHeart: true }),
    )
    expect(suggestions.map((suggestion) => suggestion.kind)).not.toContain('take_heart')
    expect(suggestions.at(-1)?.kind).toBe('more')
  })
})

describe('storySuggestions in a conversation', () => {
  it("offers the conversation's own actions in the server's order instead of the scene's", () => {
    const suggestions = storySuggestions(
      world({
        presentNpcs: [npc('Mara Reedwake'), npc('Tovin Bell')],
        sceneItems: [{ id: 'key', name: 'Key', quantity: 1, type: 'key', takeLabel: 'Take the Key' }],
        conversation: {
          npcId: 'npc_mara',
          name: 'Mara Reedwake',
          summary: 'The innkeeper.',
          actions: [
            { kind: 'topic', id: 'mara_caravan', label: 'Ask about the caravan' },
            { kind: 'topic', id: 'mara_promise', label: 'Promise to bring word of the drivers' },
            { kind: 'say_free', id: '', label: 'Say something else' },
            { kind: 'leave_conversation', id: '', label: 'Leave it there' },
          ],
        },
      }),
      controls(),
    )

    expect(suggestions).toEqual([
      { kind: 'topic', key: 'topic:mara_caravan', label: 'Ask about the caravan', npcId: 'npc_mara', topicId: 'mara_caravan' },
      { kind: 'topic', key: 'topic:mara_promise', label: 'Promise to bring word of the drivers', npcId: 'npc_mara', topicId: 'mara_promise' },
      { kind: 'say_free', key: 'say_free', label: 'Say something else' },
      { kind: 'leave_conversation', key: 'leave', label: 'Leave it there', npcId: 'npc_mara' },
    ])
  })
})
