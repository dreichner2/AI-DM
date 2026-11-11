import type {
  WorldConversation,
  WorldLocationSummary,
  WorldMerchantSummary,
  WorldSceneItemSummary,
  WorldStatePanel,
  WorldZoneSummary,
} from '../gameSelectors'
import type {
  GameplayControlState,
  GameplayInteractableControl,
  GameplayObjectAction,
} from '../gameplayControlState'
import { TAKE_HEART_LABEL, objectActionLabel } from './actionIntents'
import { markedLabel, zoneOfPerson, zoneReach } from './zones'

/** Chips shown before the `More…` chip. */
export const STORY_SUGGESTION_LIMIT = 6

const CHOICE_LIMIT = 4
const TAKE_LIMIT = 2
/** The words the server reads as taking every loose item in the scene. */
export const TAKE_ALL_LABEL = 'Take everything here'
const OBJECT_LIMIT = 3
const TALK_LIMIT = 2
const TRADE_LIMIT = 1
const ZONE_LIMIT = 2
const TRAVEL_LIMIT = 2

/**
 * One thing the player could do next. `talk` fills the composer so the player
 * can finish the sentence; `say` sends its label as the player's own words;
 * `take`, `object`, `zone_move`, `travel` and `take_heart` submit typed intents;
 * `trade` opens the merchant's wares; `more` opens the action tray. In a conversation, `topic` and
 * `leave_conversation` submit typed intents and `say_free` puts the player in the box to say it in their own words.
 */
export type StorySuggestion =
  | { kind: 'take'; key: string; label: string; item: WorldSceneItemSummary }
  | { kind: 'take_all'; key: string; label: string }
  | {
      kind: 'object'
      key: string
      label: string
      object: GameplayInteractableControl
      action: GameplayObjectAction
    }
  | { kind: 'talk'; key: string; label: string; text: string }
  | { kind: 'say'; key: string; label: string }
  | { kind: 'trade'; key: string; label: string; merchant: WorldMerchantSummary }
  | { kind: 'zone_move'; key: string; label: string; zone: WorldZoneSummary }
  | { kind: 'travel'; key: string; label: string; exit: WorldLocationSummary }
  | { kind: 'take_heart'; key: string; label: string }
  | { kind: 'topic'; key: string; label: string; npcId: string; topicId: string }
  | { kind: 'say_free'; key: string; label: string }
  | { kind: 'leave_conversation'; key: string; label: string; npcId: string }
  | { kind: 'more'; key: string; label: string }

type ObjectSuggestion = Extract<StorySuggestion, { kind: 'object' }>

/** Opens the action tray on whatever the row has no room for. */
export const MORE_SUGGESTION: StorySuggestion = { kind: 'more', key: 'more', label: 'More…' }

const UNREACHABLE_NPC_STATUSES = new Set(['dead', 'deceased', 'destroyed'])

/** The contract's order of object actions. */
const ACTION_ORDER: GameplayObjectAction['id'][] = [
  'search', 'use', 'trigger', 'open', 'unlock', 'inspect', 'close', 'lock', 'break', 'disarm', 'reset', 'plan',
]

/**
 * What the player may do with an object: the server's offered actions that are
 * legal and not yet done. An object from an older server, which offers none,
 * may only be examined; the client never guesses other verbs.
 */
export function objectChoices(object: GameplayInteractableControl): GameplayObjectAction[] {
  if (!object.available) return []
  if (object.offered) return object.actions.filter((action) => action.available)
  if (object.states.includes('Inspected')) return []
  return [{ id: 'inspect', label: `Examine ${object.name}`, available: true, reason: '', phrase: true }]
}

function inActionOrder(actions: GameplayObjectAction[]) {
  return [...actions].sort((left, right) => ACTION_ORDER.indexOf(left.id) - ACTION_ORDER.indexOf(right.id))
}

function objectChip(
  object: GameplayInteractableControl,
  action: GameplayObjectAction,
  zones?: WorldZoneSummary[],
): ObjectSuggestion {
  const label = markedLabel(objectActionLabel(action), zones, object.zoneId)
  return { kind: 'object', key: `object:${object.id}:${action.planId ?? action.id}`, label, object, action }
}

/**
 * The objects' choices the player may make now (offered with `choice`), each
 * its own chip: objects in scene order, each object's in the contract's
 * action order, at most four.
 */
function choiceSuggestions(objects: GameplayInteractableControl[], zones?: WorldZoneSummary[]): ObjectSuggestion[] {
  return objects
    .flatMap((object) =>
      inActionOrder(objectChoices(object).filter((action) => action.choice)).map((action) => objectChip(object, action, zones)),
    )
    .slice(0, CHOICE_LIMIT)
}

/**
 * An object's one chip among its actions that are not choices: the first in
 * the contract's action order, preferring one the pack authored; null when it
 * offers nothing else.
 */
function objectSuggestion(object: GameplayInteractableControl, zones?: WorldZoneSummary[]): ObjectSuggestion | null {
  // A plan is a chip only when the server suggests it (two at a time); the rest wait under More….
  const actions = inActionOrder(objectChoices(object).filter((action) => !action.choice && (action.id !== 'plan' || action.suggested)))
  const action = actions.find((candidate) => candidate.authored) ?? actions[0]
  return action ? objectChip(object, action, zones) : null
}

/**
 * One chip per object: in a place with rooms, those in the viewer's own zone before those next door (and none
 * further off); then authored ones first, each group in scene order; at most three.
 */
function objectSuggestions(objects: GameplayInteractableControl[], zones?: WorldZoneSummary[]): ObjectSuggestion[] {
  const chips = objects
    .filter((object) => zoneReach(zones, object.zoneId) !== 'away')
    .map((object) => objectSuggestion(object, zones))
    .filter((chip): chip is ObjectSuggestion => chip !== null)
  const near = (chip: ObjectSuggestion) => zoneReach(zones, chip.object.zoneId) === 'here'
  return [true, false]
    .flatMap((here) => [
      ...chips.filter((chip) => near(chip) === here && chip.action.authored),
      ...chips.filter((chip) => near(chip) === here && !chip.action.authored),
    ])
    .slice(0, OBJECT_LIMIT)
}

/** A conversation's offered actions, in the server's order: its topics, `Say something else`, `Leave it there`. */
function conversationSuggestions({ npcId, actions }: WorldConversation): StorySuggestion[] {
  return actions.map((action): StorySuggestion =>
    action.kind === 'topic'
      ? { kind: 'topic', key: `topic:${action.id}`, label: action.label, npcId, topicId: action.id }
      : action.kind === 'leave_conversation'
        ? { kind: 'leave_conversation', key: 'leave', label: action.label, npcId }
        : { kind: 'say_free', key: 'say_free', label: action.label })
}

/**
 * Contextual suggestions for the Story dock, in priority order: the choices
 * an object puts before the player, what may be said to the captives a fight
 * left (`Question the Ashmarked Cutthroat`), loose items to take, scene
 * objects, people present, an open merchant, the rooms next door (`Go into the
 * common room`), ways onward, then `Take heart (Inspiration)` while the hero
 * holds Inspiration — at most six, then `More…` when the tray
 * holds anything left out. In a place with rooms, what stands next door is
 * offered after what stands in the viewer's own zone and marked with its zone
 * (`Talk to Mara Reedwake · common room`), anything further off is not offered,
 * and ways onward only from a zone the party can leave by. Combat has its own
 * ribbon, so nothing is suggested while it lasts. While the viewer's hero is in a conversation, its own actions are
 * the suggestions.
 */
export function storySuggestions(
  worldState: WorldStatePanel,
  gameplayControls: GameplayControlState,
): StorySuggestion[] {
  if (worldState.combat.active || gameplayControls.inCombat) return []
  if (worldState.conversation) return conversationSuggestions(worldState.conversation)

  const take: StorySuggestion[] = worldState.sceneItems
    .filter((item) => item.id && item.takeLabel)
    .map((item) => ({ kind: 'take', key: `take:${item.id}`, label: item.takeLabel ?? '', item }))
  // With several things lying about, one chip takes them all in a single turn.
  const takeChips: StorySuggestion[] =
    take.length > 1
      ? [{ kind: 'take_all', key: 'take:all', label: TAKE_ALL_LABEL }, ...take.slice(0, TAKE_LIMIT - 1)]
      : take

  const zones = worldState.zones
  const objectCandidates = gameplayControls.interactables
    .filter((object) => zoneReach(zones, object.zoneId) !== 'away')
    .reduce((count, object) => count + objectChoices(object).length, 0)

  const captives: StorySuggestion[] = (worldState.captives ?? []).flatMap((captive) =>
    captive.offeredActions.map((action) => ({
      kind: 'say' as const,
      key: `say:${captive.id || captive.name}:${action.label}`,
      label: action.label,
    })),
  )

  const npcZone = (id: string) => zoneOfPerson(zones, id)?.id
  const talk: StorySuggestion[] = worldState.presentNpcs
    .filter((npc) => npc.name && !UNREACHABLE_NPC_STATUSES.has(npc.status.toLowerCase()))
    .filter((npc) => zoneReach(zones, npcZone(npc.id)) !== 'away')
    .sort((left, right) => Number(zoneReach(zones, npcZone(left.id)) === 'next') - Number(zoneReach(zones, npcZone(right.id)) === 'next'))
    .map((npc) => ({
      kind: 'talk',
      key: `talk:${npc.id || npc.name}`,
      label: markedLabel(`Talk to ${npc.name}`, zones, npcZone(npc.id)),
      text: `I talk to ${npc.name}.`,
    }))

  const trade: StorySuggestion[] = (worldState.merchants ?? []).map((merchant) => ({
    kind: 'trade',
    key: `trade:${merchant.id}`,
    label: markedLabel(`Trade with ${merchant.name}`, zones, merchant.zoneId),
    merchant,
  }))

  const moves: StorySuggestion[] = (zones ?? [])
    .filter((zone) => zone.adjacent && zone.moveLabel)
    .map((zone) => ({ kind: 'zone_move', key: `zone:${zone.id}`, label: zone.moveLabel ?? '', zone }))

  const travel: StorySuggestion[] = worldState.availableExits
    .filter((exit) => exit.id)
    .sort((left, right) => Number(left.status === 'visited') - Number(right.status === 'visited'))
    .map((exit) => ({
      kind: 'travel',
      key: `travel:${exit.id}`,
      label: `Go to ${exit.name}`,
      exit,
    }))

  // Inspiration the hero holds, last: leaving it alone is keeping it.
  const heart: StorySuggestion[] = gameplayControls.takeHeart
    ? [{ kind: 'take_heart', key: 'take_heart', label: TAKE_HEART_LABEL }]
    : []
  const chips = [
    ...choiceSuggestions(gameplayControls.interactables, zones),
    ...captives,
    ...takeChips,
    ...objectSuggestions(gameplayControls.interactables, zones),
    ...talk.slice(0, TALK_LIMIT),
    ...trade.slice(0, TRADE_LIMIT),
    ...moves.slice(0, ZONE_LIMIT),
    ...travel.slice(0, TRAVEL_LIMIT),
    ...heart,
  ].slice(0, STORY_SUGGESTION_LIMIT)
  const candidates =
    captives.length + take.length + objectCandidates + talk.length + trade.length + moves.length + travel.length + heart.length
  return candidates > chips.length ? [...chips, MORE_SUGGESTION] : chips
}
