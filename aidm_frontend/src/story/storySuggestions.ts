import type {
  WorldLocationSummary,
  WorldMerchantSummary,
  WorldSceneItemSummary,
  WorldStatePanel,
} from '../gameSelectors'
import type {
  GameplayControlState,
  GameplayInteractableControl,
  GameplayObjectAction,
} from '../gameplayControlState'

/** Chips shown before the `More…` chip. */
export const STORY_SUGGESTION_LIMIT = 6

const TAKE_LIMIT = 2
const AUTHORED_LIMIT = 3
const OBJECT_LIMIT = 3
const TALK_LIMIT = 2
const TRADE_LIMIT = 1
const TRAVEL_LIMIT = 2

/**
 * One thing the player could do next. `talk` fills the composer so the player
 * can finish the sentence; `take`, `object` and `travel` submit typed intents;
 * `trade` opens the merchant's wares; `more` opens the action tray.
 */
export type StorySuggestion =
  | { kind: 'take'; key: string; label: string; item: WorldSceneItemSummary }
  | {
      kind: 'object'
      key: string
      label: string
      object: GameplayInteractableControl
      action: GameplayObjectAction
    }
  | { kind: 'talk'; key: string; label: string; text: string }
  | { kind: 'trade'; key: string; label: string; merchant: WorldMerchantSummary }
  | { kind: 'travel'; key: string; label: string; exit: WorldLocationSummary }
  | { kind: 'more'; key: string; label: string }

type ObjectSuggestion = Extract<StorySuggestion, { kind: 'object' }>

const UNREACHABLE_NPC_STATUSES = new Set(['dead', 'deceased', 'destroyed'])

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

function objectSuggestion(object: GameplayInteractableControl, action: GameplayObjectAction): ObjectSuggestion {
  return { kind: 'object', key: `object:${object.id}:${action.id}`, label: action.label, object, action }
}

/** Authored actions first (scene order, the server's order within an object), then one choice per object not yet shown. */
function objectSuggestions(objects: GameplayInteractableControl[]): ObjectSuggestion[] {
  const choices = objects.map((object) => ({ object, actions: objectChoices(object) }))
  const authored = choices
    .flatMap(({ object, actions }) => actions.filter((action) => action.authored).map((action) => objectSuggestion(object, action)))
    .slice(0, AUTHORED_LIMIT)
  const shown = new Set(authored.map((suggestion) => suggestion.object.id))
  const firsts = choices.flatMap(({ object, actions }) =>
    !shown.has(object.id) && actions.length ? [objectSuggestion(object, actions[0])] : [],
  )
  return [...authored, ...firsts].slice(0, OBJECT_LIMIT)
}

/**
 * Contextual suggestions for the Story dock, in priority order: loose items to
 * take, scene objects, people present, an open merchant, then ways onward —
 * at most six, then `More…` when the tray holds anything left out. Combat has
 * its own ribbon, so nothing is suggested while it lasts.
 */
export function storySuggestions(
  worldState: WorldStatePanel,
  gameplayControls: GameplayControlState,
): StorySuggestion[] {
  if (worldState.combat.active || gameplayControls.inCombat) return []

  const take: StorySuggestion[] = worldState.sceneItems
    .filter((item) => item.id && item.takeLabel)
    .map((item) => ({ kind: 'take', key: `take:${item.id}`, label: item.takeLabel ?? '', item }))

  const objectCandidates = gameplayControls.interactables.reduce(
    (count, object) => count + objectChoices(object).length,
    0,
  )

  const talk: StorySuggestion[] = worldState.presentNpcs
    .filter((npc) => npc.name && !UNREACHABLE_NPC_STATUSES.has(npc.status.toLowerCase()))
    .map((npc) => ({
      kind: 'talk',
      key: `talk:${npc.id || npc.name}`,
      label: `Talk to ${npc.name}`,
      text: `I talk to ${npc.name}.`,
    }))

  const trade: StorySuggestion[] = (worldState.merchants ?? []).map((merchant) => ({
    kind: 'trade',
    key: `trade:${merchant.id}`,
    label: `Trade with ${merchant.name}`,
    merchant,
  }))

  const travel: StorySuggestion[] = worldState.availableExits
    .filter((exit) => exit.id)
    .sort((left, right) => Number(left.status === 'visited') - Number(right.status === 'visited'))
    .map((exit) => ({
      kind: 'travel',
      key: `travel:${exit.id}`,
      label: `Go to ${exit.name}`,
      exit,
    }))

  const chips = [
    ...take.slice(0, TAKE_LIMIT),
    ...objectSuggestions(gameplayControls.interactables),
    ...talk.slice(0, TALK_LIMIT),
    ...trade.slice(0, TRADE_LIMIT),
    ...travel.slice(0, TRAVEL_LIMIT),
  ].slice(0, STORY_SUGGESTION_LIMIT)
  const candidates = take.length + objectCandidates + talk.length + trade.length + travel.length
  return candidates > chips.length ? [...chips, { kind: 'more', key: 'more', label: 'More…' }] : chips
}
