import type { CombatLegalAction, CombatParticipantSummary, CombatStatePanel } from '../gameSelectors'
import { outOfFight } from './vitalityLabels'

const RANGE_BANDS = new Set(['melee', 'near', 'far', 'distant'])

/** A combat action's name; an attack that spends the move first reads "Close in and attack with Longsword". */
export function combatActionLabel(action: CombatLegalAction) {
  return action.type === 'attack' && action.requiresMove
    ? `Close in and attack with ${action.weaponName || 'your weapon'}`
    : action.label
}

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

/** The strip's short form of each reach: the server's own label runs to a sentence, and the strip has a few words. */
const STRIP_REACH: Record<string, string> = {
  in_reach: 'in reach',
  one_move: 'a move away · 30 ft',
  out_of_reach: 'out of reach · 60 ft+',
  far_off: 'far off',
}

/** How far a participant is from the viewing hero ("a move away · 30 ft"); its band when the server does not say. */
export function distance(participant: CombatParticipantSummary) {
  return (participant.reach && STRIP_REACH[participant.reach]) || participant.reachLabel || rangeBand(participant)
}

/**
 * Beneath an enemy in the initiative strip: "a move away · 30 ft · Wounded", with "· 7/11" only when the
 * server sends HP.
 */
export function enemyDetail(participant: CombatParticipantSummary) {
  return [distance(participant), healthWord(participant), exactHp(participant)].filter(Boolean).join(' · ')
}

export function combatParticipant(combat: CombatStatePanel, id: string) {
  return [...combat.allies, ...combat.enemies].find((participant) => participant.id === id) ?? null
}

/** The targets a picker lists: never a defeated enemy, nor a hero out of the fight. */
export function offeredTargets<Target extends { id: string }>(combat: CombatStatePanel, targets: Target[]) {
  return targets.filter((target) => {
    const participant = combatParticipant(combat, target.id)
    return participant?.team === 'enemy' ? participant.healthTone !== 'down' : !outOfFight(participant?.conditions)
  })
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
