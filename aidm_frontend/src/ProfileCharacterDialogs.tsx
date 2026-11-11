import { type RefObject } from 'react'
import { ModalShell } from './ModalShell'
import type { JoinableHero, Player, PlayerDetail } from './types'
import { useJoinableHeroes } from './useJoinableHeroes'

type CharacterJoinDialogProps = {
  campaignTitle: string | null
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onCreateCharacter: () => void
  onJoinPlayer: (player: Player) => void
  open: boolean
  players: Player[]
  portraitSrcForPlayer: (player: Player) => string
  auth: string
  baseUrl: string
  /** The adventure a hero is chosen for: the player's heroes from their other adventures (`Your heroes`) may join it as copies. */
  campaignId: number | null
  /** A hero brought in from another adventure, to select as a created one is. */
  onBrought: (detail: PlayerDetail) => void
}

/** `Level 3 · Human Fighter · last in The Road of Unremembered Kings`. */
function joinableMeta(hero: JoinableHero) {
  const kind = [hero.race, hero.class].filter(Boolean).join(' ')
  return [`Level ${hero.level}`, kind, hero.adventure_title ? `last in ${hero.adventure_title}` : ''].filter(Boolean).join(' · ')
}

export function CharacterJoinDialog({
  campaignTitle,
  dialogRef,
  onClose,
  onCreateCharacter,
  onJoinPlayer,
  open,
  players,
  portraitSrcForPlayer,
  auth,
  baseUrl,
  campaignId,
  onBrought,
}: CharacterJoinDialogProps) {
  const { joinableHeroes, bringNote, bringingId, bringHero } = useJoinableHeroes({ auth, baseUrl, campaignId, choosing: open, onBrought })
  if (!open) return null

  return (
    <ModalShell
      closeLabel="Close character chooser"
      description={
        campaignTitle ? `Choose who you are playing in ${campaignTitle}.` : 'Choose who you are playing.'
      }
      dialogRef={dialogRef}
      labelledBy="character-join-title"
      onClose={onClose}
      title="Choose your character"
    >
      <div className="story-dialog-body">
        {players.length ? (
          <ul className="story-dialog-list" aria-label="Existing characters">
            {players.map((player) => {
              const characterName =
                player.character_name || player.name || `Player ${player.player_id}`
              const playerName = player.name || 'Unknown player'
              const characterClass = player.char_class || player.class_ || 'Adventurer'
              return (
                <li key={player.player_id} className="story-dialog-row story-dialog-row-portrait">
                  <img
                    className="story-dialog-portrait"
                    src={portraitSrcForPlayer(player)}
                    alt=""
                    aria-hidden="true"
                  />
                  <span className="story-dialog-row-copy">
                    <span className="story-dialog-row-name">{characterName}</span>
                    <span className="story-dialog-row-meta">
                      Level {player.level} {characterClass} · {playerName}
                    </span>
                  </span>
                  <span className="story-dialog-row-actions">
                    <button
                      type="button"
                      className="story-link"
                      aria-label={`Join as ${characterName}`}
                      onClick={() => onJoinPlayer(player)}
                    >
                      Join
                    </button>
                  </span>
                </li>
              )
            })}
          </ul>
        ) : joinableHeroes.length ? null : (
          <p className="story-dialog-empty">No characters yet. Create the first one for this adventure.</p>
        )}
        {joinableHeroes.length ? (
          <section className="story-dialog-section" aria-labelledby="character-join-yours">
            <h3 id="character-join-yours" className="story-label">Your heroes</h3>
            <ul className="story-dialog-list" aria-labelledby="character-join-yours">
              {joinableHeroes.map((hero) => {
                const firstName = hero.character_name.trim().split(/\s+/)[0] || hero.character_name
                return (
                  <li key={hero.player_id} className="story-dialog-row story-dialog-row-portrait">
                    <img className="story-dialog-portrait" src={hero.portrait} alt="" aria-hidden="true" />
                    <span className="story-dialog-row-copy">
                      <span className="story-dialog-row-name">{hero.character_name}</span>
                      <span className="story-dialog-row-meta">{joinableMeta(hero)}</span>
                    </span>
                    <span className="story-dialog-row-actions">
                      <button
                        type="button"
                        className="story-link"
                        disabled={bringingId !== null}
                        aria-busy={bringingId === hero.player_id || undefined}
                        onClick={() => void bringHero(hero)}
                      >
                        {bringingId === hero.player_id ? 'Bringing…' : `Play as ${firstName}`}
                      </button>
                    </span>
                  </li>
                )
              })}
            </ul>
            {bringNote ? (
              <p className="story-dialog-line" role="status">
                {bringNote}
              </p>
            ) : null}
          </section>
        ) : null}
        <footer className="story-dialog-footer">
          <button type="button" className="story-link" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="story-primary" onClick={onCreateCharacter}>
            New character
          </button>
        </footer>
      </div>
    </ModalShell>
  )
}
