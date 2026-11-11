import { useEffect } from 'react'
import type { RollResolvedPayload } from '../gameActions'
import type { DiceRollState } from '../useComposerActions'
import { RollCard } from './RollCard'
import { D20_CYCLE_MS } from './rollPlayback'
import { useReducedMotion } from './useReducedMotion'
import type { RollCardRecord } from './storyRolls'

const ABILITY_NAMES: Record<string, string> = {
  str: 'Strength',
  dex: 'Dexterity',
  con: 'Constitution',
  int: 'Intelligence',
  wis: 'Wisdom',
  cha: 'Charisma',
}

type PressedRollCardProps = {
  roll: DiceRollState
  /** The viewer's hero, who rolls. */
  heroName: string | null
  onPlayed: () => void
  onRetry: () => void
  onDismiss: () => void
}

function abilityName(key: string | undefined) {
  return ABILITY_NAMES[(key ?? '').trim().slice(0, 3).toLowerCase()] ?? ''
}

function titleWords(text: string) {
  return text
    .replace(/_/g, ' ')
    .trim()
    .split(/\s+/)
    .map((word, index) => (index > 0 && ['of', 'check', 'save'].includes(word.toLowerCase()) ? word.toLowerCase() : word.charAt(0).toUpperCase() + word.slice(1)))
    .join(' ')
}

/** `Athletics check`, `Wisdom save`, `Attack`; a roll of the player's own reads its ability or die. */
function rollLabel(result: RollResolvedPayload | null, roll: DiceRollState) {
  if (!result) return titleWords(roll.actionIntent.roll?.reason ?? '') || roll.die
  const ruleType = result.rule_type.toLowerCase()
  if (ruleType === 'attack') return 'Attack'
  if (ruleType.endsWith('_saving_throw')) return `${abilityName(ruleType)} save`
  if (!result.outcome) return abilityName(result.ability?.key) || result.die
  if (ruleType && !['check', 'ability', 'ability_check'].includes(ruleType)) return `${titleWords(ruleType)} check`
  return `${abilityName(result.ability?.key) || 'Ability'} check`
}

/** The pressed roll as a roll card: the server's result once it has arrived, its label before. */
function pressedRollRecord(roll: DiceRollState, heroName: string | null): RollCardRecord {
  const result = roll.result
  const actor = { id: '', name: heroName ?? '', side: 'hero' as const }
  const base = { id: `pressed-${roll.rollKey}`, actor, label: rollLabel(result, roll) }
  if (!result) return { ...base, kind: 'check', d20: null, parts: [], total: null, against: null, outcome: null, natural: null }
  const ruleType = result.rule_type.toLowerCase()
  const attack = ruleType === 'attack'
  const breakdown = result.modifier_breakdown
  const parts = breakdown
    ? [
        { label: abilityName(result.ability?.key) || 'ability', value: breakdown.ability_modifier },
        { label: breakdown.proficiency_multiplier === 2 ? 'expertise' : 'proficiency', value: breakdown.proficiency_bonus },
        { label: 'Wounded', value: -breakdown.wound_penalty },
      ]
    : [{ label: 'bonus', value: result.modifier }]
  const dice = (result.bonus_dice ?? []).map((rolled) => ({
    label: rolled.dice.startsWith('-') ? 'penalty die' : 'bonus die',
    dice: rolled.dice.replace(/^[+-]/, ''),
    faces: rolled.rolls,
    value: rolled.total,
  }))
  const inspiration = result.inspiration
    ? [{ label: 'Bardic Inspiration', dice: `1${result.inspiration.die}`, faces: [result.inspiration.roll], value: result.inspiration.roll }]
    : []
  const shown = [...parts, ...dice, ...inspiration].filter((part) => part.value)
  const remainder = result.total - result.kept - shown.reduce((sum, part) => sum + part.value, 0)
  const target = attack ? result.target_ac ?? result.dc : result.dc
  const outcome = result.outcome ? (attack ? (result.outcome === 'success' ? 'hit' : 'miss') : result.outcome) : null
  return {
    ...base,
    kind: attack ? 'attack' : ruleType.endsWith('_saving_throw') ? 'save' : 'check',
    d20: { faces: result.rolls, kept: result.kept, mode: result.mode },
    ...(result.die !== 'd20' ? { die: result.die } : {}),
    parts: remainder ? [...shown, { label: 'bonus', value: remainder }] : shown,
    total: result.total,
    against: outcome && target !== null && target !== undefined ? { kind: attack ? 'AC' : 'DC', value: target } : null,
    outcome,
    natural: result.die === 'd20' && result.kept === 20 ? 'twenty' : result.die === 'd20' && result.kept === 1 ? 'one' : null,
  }
}

/**
 * The roll the player just pressed, where they pressed it: its d20 starts
 * cycling at once and lands on the server's result when that arrives, no
 * sooner than half a second after the press.
 */
export function PressedRollCard({ roll, heroName, onPlayed, onRetry, onDismiss }: PressedRollCardProps) {
  const reduceMotion = useReducedMotion()
  const landed = Boolean(roll.result)

  useEffect(() => {
    // Without motion nothing plays: the result is shown landed as it arrives.
    if (reduceMotion && landed && roll.status === 'rolling') onPlayed()
  }, [landed, onPlayed, reduceMotion, roll.status])

  if (roll.status === 'failed') {
    return (
      <section className="roll-card-pressed" aria-label="Your roll">
        <div className="roll-card-failed" role="alert">{roll.error ?? 'The roll could not be sent.'}</div>
        <div className="roll-card-failed-actions">
          <button type="button" className="story-link" onClick={onRetry}>Retry</button>
          <button type="button" className="story-link" onClick={onDismiss}>Dismiss</button>
        </div>
      </section>
    )
  }
  const playback = reduceMotion
    ? null
    : { startAt: roll.landAt !== null ? roll.landAt - D20_CYCLE_MS : roll.startedAt, instant: false }
  return (
    <section className="roll-card-pressed" aria-label="Your roll" aria-live="polite">
      <RollCard
        roll={pressedRollRecord(roll, heroName)}
        playback={playback}
        awaiting={!reduceMotion && !landed}
        onPlayed={onPlayed}
      />
    </section>
  )
}
