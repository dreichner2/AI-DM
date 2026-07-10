import { profileIconSrcForCharacter } from './profileIcons'
import type { ActivePlayer } from './types'

export type SessionPresenceStripProps = {
  activePlayers: ActivePlayer[]
  selectedPlayerId: number | null
  selectedPlayerHasTurn: boolean
  turnControlStatusLabel: string
}

function activePlayerAvatarSrc(player: ActivePlayer) {
  return (
    player.profile_image ||
    profileIconSrcForCharacter({ race: player.race, sex: player.sex }) ||
    '/profile-icons/human_male.png'
  )
}

function activePlayerInitial(player: ActivePlayer) {
  return (player.character_name || player.name || '?').slice(0, 1).toUpperCase()
}

export function SessionPresenceStrip({
  activePlayers,
  selectedPlayerId,
  selectedPlayerHasTurn,
  turnControlStatusLabel,
}: SessionPresenceStripProps) {
  const typingPlayers = activePlayers.filter(
    (player) => player.id !== selectedPlayerId && player.is_typing,
  )
  const typingLabel = typingPlayers.length
    ? `${typingPlayers.slice(0, 2).map((player) => player.character_name).join(', ')}${typingPlayers.length > 2 ? ` +${typingPlayers.length - 2}` : ''} typing`
    : activePlayers.length ? 'Watching table' : 'No friends online'

  return (
    <section className="mobile-presence-strip" aria-label="Mobile active players">
      <div className={`mobile-presence-summary ${selectedPlayerHasTurn ? 'open' : 'locked'}`}>
        <span>{activePlayers.length ? `${activePlayers.length} online` : 'Solo'}</span>
        <strong>{typingLabel}</strong>
      </div>
      {activePlayers.length ? (
        <ul className="mobile-presence-list" aria-label="Active players on mobile">
          {activePlayers.map((player) => {
            const isSelectedPlayer = player.id === selectedPlayerId
            const isOtherPlayerTyping = !isSelectedPlayer && player.is_typing
            const health = player.health
            return (
              <li
                key={player.id}
                className={`${isSelectedPlayer ? 'selected' : ''} ${isOtherPlayerTyping ? 'typing' : ''}`}
              >
                <span className="mobile-presence-avatar" aria-hidden="true">
                  <img src={activePlayerAvatarSrc(player)} alt="" />
                  <span>{activePlayerInitial(player)}</span>
                  {health ? <span className={`mobile-health-dot mobile-health-dot-${health.tone}`} /> : null}
                </span>
                <span className="mobile-presence-copy">
                  <strong>{player.character_name}</strong>
                  <small>{isSelectedPlayer ? 'You' : player.name}</small>
                </span>
                {health ? (
                  <span
                    className="mobile-health-label"
                    aria-label={`${player.character_name} health: ${health.label}`}
                    title={`${health.label}: ${health.currentHp}/${health.maxHp} HP`}
                  >
                    {health.label}
                  </span>
                ) : null}
                {isOtherPlayerTyping ? (
                  <span className="mobile-typing-badge" aria-label={`${player.character_name} is typing`}>
                    Typing
                  </span>
                ) : null}
              </li>
            )
          })}
        </ul>
      ) : (
        <div className="mobile-presence-empty">{turnControlStatusLabel}</div>
      )}
    </section>
  )
}
