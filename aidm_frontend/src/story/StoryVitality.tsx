import type { CharacterVitality } from '../gameSelectors'
import { vitalityLabel } from './vitalityLabels'

const DEATH_SAVE_LIMIT = 3

function counted(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`
}

function Pips({ filled, outcome }: { filled: number; outcome: 'success' | 'failure' }) {
  return (
    <span className="story-vitality-pips" data-outcome={outcome}>
      {Array.from({ length: DEATH_SAVE_LIMIT }, (_, index) => (
        <i key={index} data-filled={index < filled || undefined} />
      ))}
    </span>
  )
}

/**
 * A downed character's state: `Dying` with three success and three failure
 * pips filled as death saves land; `Down — stable` and `Dead` as words alone.
 */
export function StoryVitality({ vitality, className = '' }: { vitality: CharacterVitality; className?: string }) {
  const label = vitalityLabel(vitality.state)
  const description = vitality.state === 'dying'
    ? `${label}: ${counted(vitality.successes, 'success', 'successes')}, ${counted(vitality.failures, 'failure', 'failures')}`
    : label
  return (
    <span
      className={`story-vitality ${className}`.trim()}
      data-state={vitality.state}
      role="img"
      aria-label={description}
      title={description}
    >
      <span className="story-label story-vitality-label" aria-hidden="true">{label}</span>
      {vitality.state === 'dying' ? (
        <span className="story-vitality-saves" aria-hidden="true">
          <Pips filled={vitality.successes} outcome="success" />
          <Pips filled={vitality.failures} outcome="failure" />
        </span>
      ) : null}
    </span>
  )
}
