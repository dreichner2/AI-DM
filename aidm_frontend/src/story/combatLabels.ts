import type { CombatParticipantSummary, CombatStatePanel } from '../gameSelectors'

const RANGE_BANDS = new Set(['melee', 'near', 'far', 'distant'])

/** "Wounded", or '' when the server sends nothing to judge by. */
export function healthWord(participant: CombatParticipantSummary) {
  return participant.health && participant.health !== 'Unknown' ? participant.health : ''
}

/** Exact hit points ("7/11"), only when the server sends them. */
export function exactHp(participant: CombatParticipantSummary) {
  return participant.hp ? `${participant.hp.current}/${participant.hp.max}` : ''
}

export function rangeBand(participant: CombatParticipantSummary) {
  return participant.rangeBand && RANGE_BANDS.has(participant.rangeBand) ? participant.rangeBand : ''
}

/** Beneath an enemy in the initiative strip: "near · Wounded", with "· 7/11" only when the server sends HP. */
export function enemyDetail(participant: CombatParticipantSummary) {
  return [rangeBand(participant), healthWord(participant), exactHp(participant)].filter(Boolean).join(' · ')
}

export function combatParticipant(combat: CombatStatePanel, id: string) {
  return [...combat.allies, ...combat.enemies].find((participant) => participant.id === id) ?? null
}

/** A target choice: "Goblin · 7/11" with HP numbers, else "Goblin · Wounded", else the range band. */
export function combatTargetLabel(
  combat: CombatStatePanel,
  target: { id: string; name: string; rangeBand: string },
) {
  const participant = combatParticipant(combat, target.id)
  const detail = participant ? exactHp(participant) || healthWord(participant) : ''
  return `${target.name} · ${detail || target.rangeBand}`
}
