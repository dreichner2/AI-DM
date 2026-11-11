import type { InspectorPanelProps } from '../InspectorPanel'
import type { SessionBoardProps } from '../SessionBoard'

/** The hero knows or can cast a spell. Without one there is no Magic tab in the drawer, as in the tray. */
export function heroHasSpells(board: SessionBoardProps, inspector: InspectorPanelProps) {
  const controls = board.gameplayControls
  return Boolean(inspector.spellbook.knownSpells.length || controls.spells.length || controls.narratedSpells?.length)
}
