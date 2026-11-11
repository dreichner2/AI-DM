import { activePlayerHealth } from '../gameSelectors'
import type { InspectorPanelProps } from '../InspectorPanel'
import type { SessionBoardProps } from '../SessionBoard'
import type { ActivePlayer } from '../types'

/** Health from the sheet's "current / max" HP; a sheet without a maximum gives none. */
function sheetHealth(hp: string) {
  const [current, max] = hp.split('/').map((part) => Number.parseFloat(part))
  return max === undefined ? null : activePlayerHealth(current, max)
}

/**
 * The viewer's hero as the party HUD shows them: the live roster's entry, or —
 * before the first chapter begins or the realtime join lists them — the hero
 * as their character sheet describes them.
 */
export function viewerHero(
  board: Pick<SessionBoardProps, 'activePlayers' | 'selectedPlayer'>,
  inspector: Pick<InspectorPanelProps, 'characterAvatarSrc' | 'statBlock'>,
  viewerPlayerId: number | null,
): ActivePlayer | null {
  if (viewerPlayerId === null) return null
  const live = board.activePlayers.find((player) => player.id === viewerPlayerId)
  if (live) return live
  const player = board.selectedPlayer
  if (!player || player.player_id !== viewerPlayerId) return null
  return {
    id: player.player_id,
    character_name: player.character_name,
    name: player.name,
    race: player.race,
    sex: player.sex,
    profile_image: inspector.characterAvatarSrc,
    health: sheetHealth(inspector.statBlock.hp),
  }
}

/** The party for the HUD: the live roster, led by the viewer's hero even when the roster does not list them yet. */
export function partyWithViewer(activePlayers: ActivePlayer[], hero: ActivePlayer | null) {
  return hero && !activePlayers.some((player) => player.id === hero.id) ? [hero, ...activePlayers] : activePlayers
}
