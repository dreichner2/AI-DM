import type { CombatStatePanel, WorldStatePanel } from '../gameSelectors'
import type { SceneDisplayState } from '../sceneState'

export type StoryMood = 'calm' | 'hearth' | 'wild' | 'tense' | 'dread' | 'combat' | 'rest'

// Keywords match at a word start so "forest" is not read as "rest" and
// "beginning" is not an inn; "inn" itself must be the whole word.
const MOOD_KEYWORDS: ReadonlyArray<[StoryMood, RegExp]> = [
  ['rest', /\b(?:rest|camp|sleep)/],
  ['hearth', /\b(?:inns?\b|tavern|hearth|town|market|shop)/],
  ['wild', /\b(?:forest|road|wild|marsh|mountain|river)/],
  ['dread', /\b(?:crypt|tomb|ruin|dungeon|haunt|dark|cave|undead)/],
]

function dangerLevel(worldState: WorldStatePanel | null, sceneState: SceneDisplayState | null) {
  if (sceneState && Number.isFinite(sceneState.dangerLevel)) return sceneState.dangerLevel
  const parsed = Number.parseFloat(worldState?.dangerLevel ?? '')
  return Number.isFinite(parsed) ? parsed : 0
}

/** The atmosphere a scene should be lit with, from what the table already knows about it. */
export function storyMood(
  worldState: WorldStatePanel | null,
  sceneState: SceneDisplayState | null,
  combatState: CombatStatePanel | null,
): StoryMood {
  if (combatState?.active || sceneState?.inCombat) return 'combat'
  const haystack = [
    sceneState?.sceneType,
    sceneState?.mood,
    sceneState?.musicTag,
    worldState?.sceneType,
    worldState?.mood,
  ]
    .filter((value): value is string => typeof value === 'string' && value.length > 0)
    .join(' ')
    .replace(/[_-]+/g, ' ')
    .toLowerCase()
  for (const [mood, pattern] of MOOD_KEYWORDS) {
    if (pattern.test(haystack)) return mood
  }
  return dangerLevel(worldState, sceneState) >= 3 ? 'tense' : 'calm'
}
