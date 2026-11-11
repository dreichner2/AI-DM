import {
  characterVitality,
  partyVitalityFromSnapshot,
  stringValue,
  type CharacterVitality,
  type CombatDeathSaves,
} from '../gameSelectors'

/** A downed hero as the story reads them: dying, stable, or out of the fight until it ends. */
export type HeroVitality = Omit<CharacterVitality, 'state'> & { state: 'dying' | 'stable' | 'out' }

const VITALITY_LABELS: Record<HeroVitality['state'], string> = {
  dying: 'Dying',
  stable: 'Down — stable',
  out: 'Out of the fight',
}

/** A hero never dies: `out_of_fight`, or a `dead` from an old save, means out of the fight. */
const OUT_CONDITIONS = new Set(['out_of_fight', 'dead'])
/** Conditions the vitality mark already shows, so never listed beside it. */
export const VITALITY_CONDITIONS = new Set(['dying', 'stable', 'unconscious', ...OUT_CONDITIONS])

/** How a downed hero's state reads: `Dying`, `Down — stable` or `Out of the fight`. */
export function vitalityLabel(state: HeroVitality['state']) {
  return VITALITY_LABELS[state]
}

/** The conditions (any list) put a hero out of the fight. */
export function outOfFight(conditions: unknown) {
  return Array.isArray(conditions) && conditions.some((condition) => OUT_CONDITIONS.has(stringValue(condition).toLowerCase()))
}

/** An old save's `dead` hero reads as out of the fight. */
function heroVitality(vitality: CharacterVitality): HeroVitality {
  return { ...vitality, state: vitality.state === 'dead' ? 'out' : vitality.state }
}

/** A downed hero's vitality from their conditions (see characterVitality); null for anyone standing. */
export function heroVitalityOf(conditions: readonly string[], deathSaves?: CombatDeathSaves | null) {
  const vitality = characterVitality(conditions, deathSaves)
  return vitality && heroVitality(vitality)
}

/** Each downed hero's vitality by player id (see partyVitalityFromSnapshot). */
export function partyHeroVitality(snapshot: unknown): Record<number, HeroVitality> {
  return Object.fromEntries(
    Object.entries(partyVitalityFromSnapshot(snapshot)).map(([id, vitality]) => [id, heroVitality(vitality)]),
  )
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
