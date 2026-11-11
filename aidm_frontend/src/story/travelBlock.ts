import type { SessionBoardProps } from '../SessionBoard'

/**
 * Why the party cannot travel on right now ('' when it can). The Map tab's and
 * the action tray's ways onward give the same reason.
 */
export function travelBlockedReason(board: SessionBoardProps) {
  const composer = board.actionComposerProps
  if (board.worldState.combat.active) return 'Not while combat is underway.'
  if (board.sendPending || board.streamingTurnActive) return 'Wait for the Dungeon Master to finish.'
  if (!composer.selectedPlayerId || !composer.selectedCharacterName) return 'Choose a character first.'
  return ''
}
