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
