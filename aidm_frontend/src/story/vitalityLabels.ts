import type { CharacterVitalityState } from '../gameSelectors'

const VITALITY_LABELS: Record<CharacterVitalityState, string> = {
  dying: 'Dying',
  stable: 'Down — stable',
  dead: 'Dead',
}

/** How a downed hero's state reads: `Dying`, `Down — stable` or `Dead`. */
export function vitalityLabel(state: CharacterVitalityState) {
  return VITALITY_LABELS[state]
}

/** The share of maximum hit points, in percent, at which each word starts; below the last, `Near death`. */
const HEALTH_WORDS: Array<[number, string]> = [
  [100, 'Uninjured'],
  [70, 'Lightly hurt'],
  [40, 'Wounded'],
  [15, 'Badly wounded'],
]

/**
 * How a standing hero's health reads beside their portrait: `Uninjured` at
 * full hit points, `Lightly hurt` from 70%, `Wounded` from 40%, `Badly
 * wounded` from 15%, `Near death` below.
 */
export function healthWord(currentHp: number, maxHp: number) {
  return HEALTH_WORDS.find(([percent]) => currentHp * 100 >= maxHp * percent)?.[1] ?? 'Near death'
}
