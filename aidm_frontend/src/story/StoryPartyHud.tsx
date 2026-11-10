import type { ActivePlayer } from '../types'
import type { StoryDrawerTab } from './types'

type StoryPartyHudProps = {
  activePlayers: ActivePlayer[]
  viewerPlayerId: number | null
  onDrawerOpenChange: (open: boolean, tab?: StoryDrawerTab) => void
}

const MAX_PORTRAITS = 4
const RING_STROKE = 2

function displayName(player: ActivePlayer) {
  return player.character_name || player.name || 'Adventurer'
}

function Portrait({ player, size, onOpen }: { player: ActivePlayer; size: number; onOpen: () => void }) {
  const name = displayName(player)
  const health = player.health ?? null
  const ratio = health && health.maxHp > 0 ? Math.max(0, Math.min(1, health.currentHp / health.maxHp)) : 1
  const radius = size / 2 - RING_STROKE
  const circumference = 2 * Math.PI * radius
  const label = health ? `${name}: ${health.label}` : name
  return (
    <button
      type="button"
      className="story-portrait"
      style={{ width: size, height: size }}
      aria-label={label}
      title={label}
      data-health={health?.tone ?? 'uninjured'}
      data-typing={player.is_typing ? '' : undefined}
      onClick={onOpen}
    >
      {player.profile_image ? (
        <img className="story-portrait-image" src={player.profile_image} alt="" draggable={false} />
      ) : (
        <span className="story-portrait-initial" aria-hidden="true">
          {name.charAt(0).toUpperCase()}
        </span>
      )}
      <svg className="story-portrait-ring" viewBox={`0 0 ${size} ${size}`} aria-hidden="true" focusable="false">
        <circle className="story-portrait-track" cx={size / 2} cy={size / 2} r={radius} />
        <circle
          className="story-portrait-arc"
          cx={size / 2}
          cy={size / 2}
          r={radius}
          strokeDasharray={`${circumference * ratio} ${circumference}`}
          transform={`rotate(-90 ${size / 2} ${size / 2})`}
        />
      </svg>
    </button>
  )
}

/** The party at a glance: portraits with health rings, the viewer's own character first. */
export function StoryPartyHud({ activePlayers, viewerPlayerId, onDrawerOpenChange }: StoryPartyHudProps) {
  if (!activePlayers.length) return null
  const viewer = activePlayers.find((player) => player.id === viewerPlayerId) ?? null
  const others = activePlayers.filter((player) => player !== viewer)
  const ordered = viewer ? [viewer, ...others] : others
  const visible = ordered.slice(0, MAX_PORTRAITS)
  const hidden = ordered.length - visible.length
  const openCharacter = () => onDrawerOpenChange(true, 'character')

  return (
    <div className="story-party" role="group" aria-label="Party">
      <ul className="story-party-list">
        {visible.map((player) => {
          const isViewer = player === viewer
          return (
            <li key={player.id} className="story-party-member" data-viewer={isViewer ? '' : undefined}>
              <Portrait player={player} size={isViewer ? 56 : 46} onOpen={openCharacter} />
              {isViewer ? (
                <span className="story-party-viewer">
                  <span className="story-label story-party-name">{displayName(player)}</span>
                  {player.health ? (
                    <span className="story-party-hp">
                      HP {player.health.currentHp} / {player.health.maxHp}
                    </span>
                  ) : null}
                </span>
              ) : null}
            </li>
          )
        })}
        {hidden > 0 ? (
          <li className="story-party-more">
            <span aria-hidden="true">+{hidden}</span>
            <span className="story-sr-only">{hidden} more in the party</span>
          </li>
        ) : null}
      </ul>
    </div>
  )
}
