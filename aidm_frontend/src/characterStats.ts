export const POINT_BUY_BUDGET = 27

export const POINT_BUY_COSTS: Record<number, number> = {
  8: 0,
  9: 1,
  10: 2,
  11: 3,
  12: 4,
  13: 5,
  14: 7,
  15: 9,
}

export const POINT_BUY_ABILITIES = [
  { key: 'strength', label: 'STR', name: 'Strength' },
  { key: 'dexterity', label: 'DEX', name: 'Dexterity' },
  { key: 'constitution', label: 'CON', name: 'Constitution' },
  { key: 'intelligence', label: 'INT', name: 'Intelligence' },
  { key: 'wisdom', label: 'WIS', name: 'Wisdom' },
  { key: 'charisma', label: 'CHA', name: 'Charisma' },
] as const

export type PointBuyAbilityKey = (typeof POINT_BUY_ABILITIES)[number]['key']
export type PointBuyScores = Record<PointBuyAbilityKey, number>

export const DEFAULT_POINT_BUY_SCORES: PointBuyScores = {
  strength: 8,
  dexterity: 8,
  constitution: 8,
  intelligence: 8,
  wisdom: 8,
  charisma: 8,
}

export function clampPointBuyScore(value: number) {
  if (!Number.isFinite(value)) return 8
  return Math.max(8, Math.min(15, Math.trunc(value)))
}

export function pointBuySpent(scores: PointBuyScores) {
  return POINT_BUY_ABILITIES.reduce((total, ability) => {
    const score = clampPointBuyScore(scores[ability.key])
    return total + (POINT_BUY_COSTS[score] ?? POINT_BUY_BUDGET + 1)
  }, 0)
}

export function abilityModifier(score: number) {
  const modifier = Math.floor((score - 10) / 2)
  return modifier >= 0 ? `+${modifier}` : String(modifier)
}

export function maxHpForScores(scores: PointBuyScores, level: number) {
  const conModifier = Math.floor((clampPointBuyScore(scores.constitution) - 10) / 2)
  return Math.max(1, 8 + conModifier + Math.max(0, level - 1) * Math.max(1, 5 + conModifier))
}

export function pointBuyStatsPayload(scores: PointBuyScores, level: number) {
  const normalizedScores = POINT_BUY_ABILITIES.reduce((next, ability) => {
    next[ability.key] = clampPointBuyScore(scores[ability.key])
    return next
  }, {} as PointBuyScores)
  const spent = pointBuySpent(normalizedScores)
  const maxHp = maxHpForScores(normalizedScores, level)
  return {
    ability_scores: normalizedScores,
    point_buy: {
      budget: POINT_BUY_BUDGET,
      spent,
      remaining: POINT_BUY_BUDGET - spent,
    },
    current_hp: maxHp,
    max_hp: maxHp,
    gold: 0,
    xp: 0,
  }
}
