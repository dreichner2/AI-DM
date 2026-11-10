import type { WorldLocationSummary, WorldStatePanel } from '../gameSelectors'
import type {
  GameplayControlState,
  GameplayInteractableControl,
  GameplayObjectAction,
} from '../gameplayControlState'

export const STORY_SUGGESTION_LIMIT = 5

/**
 * One thing the player could do next. `talk` and `examine` fill the composer so
 * the player can finish the sentence; `object` and `travel` submit typed intents.
 */
export type StorySuggestion =
  | { kind: 'talk'; key: string; label: string; text: string }
  | { kind: 'examine'; key: string; label: string; text: string }
  | {
      kind: 'object'
      key: string
      label: string
      object: GameplayInteractableControl
      action: GameplayObjectAction
    }
  | { kind: 'travel'; key: string; label: string; exit: WorldLocationSummary }

const UNREACHABLE_NPC_STATUSES = new Set(['dead', 'deceased', 'destroyed'])

/**
 * Contextual suggestions for the Story dock, in priority order: people present,
 * scene objects with something to do, loose items, then ways onward. Combat has
 * its own ribbon, so nothing is suggested while it lasts.
 */
export function storySuggestions(
  worldState: WorldStatePanel,
  gameplayControls: GameplayControlState,
): StorySuggestion[] {
  if (worldState.combat.active || gameplayControls.inCombat) return []

  const talk: StorySuggestion[] = worldState.presentNpcs
    .filter((npc) => npc.name && !UNREACHABLE_NPC_STATUSES.has(npc.status.toLowerCase()))
    .map((npc) => ({
      kind: 'talk',
      key: `talk:${npc.id || npc.name}`,
      label: `Talk to ${npc.name}`,
      text: `I talk to ${npc.name}.`,
    }))

  const objects: StorySuggestion[] = gameplayControls.interactables.flatMap((object) => {
    if (!object.available) return []
    const action = object.actions.find((candidate) => candidate.id !== 'inspect' && candidate.available)
    return action
      ? [{
          kind: 'object' as const,
          key: `object:${object.id}:${action.id}`,
          label: `${action.label} ${object.name}`,
          object,
          action,
        }]
      : []
  })

  const examine: StorySuggestion[] = worldState.sceneItems
    .filter((item) => item.name)
    .map((item) => ({
      kind: 'examine',
      key: `examine:${item.id || item.name}`,
      label: `Examine ${item.name}`,
      text: `I examine the ${item.name}.`,
    }))

  const travel: StorySuggestion[] = worldState.availableExits
    .filter((exit) => exit.id)
    .map((exit) => ({
      kind: 'travel',
      key: `travel:${exit.id}`,
      label: `Go to ${exit.name}`,
      exit,
    }))

  return [...talk, ...objects, ...examine, ...travel].slice(0, STORY_SUGGESTION_LIMIT)
}
