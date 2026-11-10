import { type RefObject } from 'react'
import { ModalShell } from './ModalShell'
import type { Player } from './types'

type CharacterJoinDialogProps = {
  campaignTitle: string | null
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onCreateCharacter: () => void
  onJoinPlayer: (player: Player) => void
  open: boolean
  players: Player[]
  portraitSrcForPlayer: (player: Player) => string
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
}: CharacterJoinDialogProps) {
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
        ) : (
          <p className="story-dialog-empty">No characters yet. Create the first one for this adventure.</p>
        )}
        <footer className="story-dialog-footer">
          <button type="button" className="story-link" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="story-primary" onClick={onCreateCharacter}>
            Create Character
          </button>
        </footer>
      </div>
    </ModalShell>
  )
}
