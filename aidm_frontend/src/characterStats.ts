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

const D12_CLASSES = new Set(['barbarian'])
const D10_CLASSES = new Set([
  'fighter',
  'ranger',
  'paladin',
  'gunslinger',
  'swashbuckler',
  'cavalier',
  'guardian',
  'marshal',
  'inquisitor',
  'warpriest',
  'warden',
  'beastmaster',
  'shapeshifter',
  'blood hunter',
  'rune knight',
])
const D6_CLASSES = new Set([
  'wizard',
  'sorcerer',
  'elementalist',
  'necromancer',
  'scholar',
  'mystic theurge',
  'business professional',
  'entertainer',
  'legal professional',
  'media professional',
  'educator',
  'service worker',
])

function baseClassName(value: string) {
  return value
    .split('-', 1)[0]
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

export function hitDieForClass(className: string) {
  const key = baseClassName(className)
  if (D12_CLASSES.has(key)) return 12
  if (D10_CLASSES.has(key)) return 10
  if (D6_CLASSES.has(key)) return 6
  return 8
}

export function proficiencyBonusForLevel(level: number) {
  const boundedLevel = Math.max(1, Math.min(20, Math.trunc(level) || 1))
  return 2 + Math.floor((boundedLevel - 1) / 4)
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

export function maxHpForScores(scores: PointBuyScores, level: number, className = '') {
  const conModifier = Math.floor((clampPointBuyScore(scores.constitution) - 10) / 2)
  const hitDie = hitDieForClass(className)
  const boundedLevel = Number.isFinite(level)
    ? Math.max(1, Math.min(20, Math.trunc(level)))
    : 1
  const perLevel = Math.max(1, Math.floor(hitDie / 2) + 1 + conModifier)
  return Math.max(1, hitDie + conModifier + (boundedLevel - 1) * perLevel)
}

export function pointBuyStatsPayload(scores: PointBuyScores, level: number, className = '') {
  const normalizedScores = POINT_BUY_ABILITIES.reduce((next, ability) => {
    next[ability.key] = clampPointBuyScore(scores[ability.key])
    return next
  }, {} as PointBuyScores)
  const spent = pointBuySpent(normalizedScores)
  const hitDie = hitDieForClass(className)
  const maxHp = maxHpForScores(normalizedScores, level, className)
  return {
    ability_scores: normalizedScores,
    point_buy: {
      budget: POINT_BUY_BUDGET,
      spent,
      remaining: POINT_BUY_BUDGET - spent,
    },
    current_hp: maxHp,
    max_hp: maxHp,
    hit_die: hitDie,
    proficiency_bonus: proficiencyBonusForLevel(level),
    gold: 0,
    xp: 0,
  }
}
