import { describe, expect, it } from 'vitest'
import {
  hitDieForClass,
  maxHpForScores,
  pointBuyStatsPayload,
  proficiencyBonusForLevel,
  type PointBuyScores,
} from './characterStats'

const scores: PointBuyScores = {
  strength: 15,
  dexterity: 12,
  constitution: 14,
  intelligence: 10,
  wisdom: 10,
  charisma: 10,
}

describe('character class durability', () => {
  it('uses the selected class hit die for level one and later HP', () => {
    expect(hitDieForClass('Barbarian - Berserker')).toBe(12)
    expect(hitDieForClass('Fighter - Champion')).toBe(10)
    expect(hitDieForClass('Cleric - Life')).toBe(8)
    expect(hitDieForClass('Wizard - Evoker')).toBe(6)
    expect(hitDieForClass('Unknown Homebrew')).toBe(8)

    expect(maxHpForScores(scores, 1, 'Fighter')).toBe(12)
    expect(maxHpForScores(scores, 1, 'Wizard')).toBe(8)
    expect(maxHpForScores(scores, 5, 'Fighter')).toBe(44)
    expect(maxHpForScores(scores, 5, 'Wizard')).toBe(32)
  })

  it('includes the class durability preview in the submitted stats payload', () => {
    const payload = pointBuyStatsPayload(scores, 5, 'Fighter - Champion')

    expect(payload).toMatchObject({
      current_hp: 44,
      max_hp: 44,
      hit_die: 10,
      proficiency_bonus: 3,
    })
    expect(proficiencyBonusForLevel(17)).toBe(6)
  })
})
