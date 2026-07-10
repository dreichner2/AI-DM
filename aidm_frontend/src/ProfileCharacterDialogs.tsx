import { type RefObject } from 'react'
import { X } from 'lucide-react'
import { ModalShell } from './ModalShell'
import type { Player } from './types'

export type ProfileSettingsSummary = {
  account: string
  backend: string
  campaign: string
  character: string
  narration: string
  session: string
  table: string
}

type ProfileSettingsDialogProps = {
  canEditCharacter: boolean
  canSwitchCharacter: boolean
  dialogRef: RefObject<HTMLElement | null>
  onBackendSettings: () => void
  onClose: () => void
  onEditCharacter: () => void
  onReconnectRealtime: () => void
  onRefreshWorkspace: () => void
  onSignOut: () => void
  onSwitchCharacter: () => void
  open: boolean
  signedIn: boolean
  summary: ProfileSettingsSummary
}

export function ProfileSettingsDialog({
  canEditCharacter,
  canSwitchCharacter,
  dialogRef,
  onBackendSettings,
  onClose,
  onEditCharacter,
  onReconnectRealtime,
  onRefreshWorkspace,
  onSignOut,
  onSwitchCharacter,
  open,
  signedIn,
  summary,
}: ProfileSettingsDialogProps) {
  if (!open) return null

  return (
    <ModalShell
      className="campaign-dialog profile-dialog"
      dialogRef={dialogRef}
      labelledBy="profile-settings-title"
      onClose={onClose}
    >
      <header>
        <div>
          <span>Profile</span>
          <h2 id="profile-settings-title">Profile Settings</h2>
        </div>
        <button type="button" aria-label="Close profile settings" onClick={onClose}>
          <X size={18} />
        </button>
      </header>
      <div className="profile-dialog-body">
        <dl className="profile-summary-grid">
          <div>
            <dt>Account</dt>
            <dd>{summary.account}</dd>
          </div>
          <div>
            <dt>Table</dt>
            <dd>{summary.table}</dd>
          </div>
          <div>
            <dt>Character</dt>
            <dd>{summary.character}</dd>
          </div>
          <div>
            <dt>Campaign</dt>
            <dd>{summary.campaign}</dd>
          </div>
          <div>
            <dt>Session</dt>
            <dd>{summary.session}</dd>
          </div>
          <div>
            <dt>Backend</dt>
            <dd>{summary.backend}</dd>
          </div>
          <div>
            <dt>Narration</dt>
            <dd>{summary.narration}</dd>
          </div>
        </dl>
        <div className="profile-action-list">
          <button type="button" onClick={onEditCharacter} disabled={!canEditCharacter}>
            Edit character
          </button>
          <button type="button" onClick={onSwitchCharacter} disabled={!canSwitchCharacter}>
            Switch character
          </button>
          <button type="button" onClick={onRefreshWorkspace}>
            Refresh workspace
          </button>
          <button type="button" onClick={onReconnectRealtime}>
            Reconnect realtime
          </button>
          <button type="button" onClick={onBackendSettings}>
            Backend settings
          </button>
          {signedIn ? (
            <button type="button" onClick={onSignOut}>
              Sign out
            </button>
          ) : null}
        </div>
      </div>
    </ModalShell>
  )
}

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
      className="campaign-dialog character-join-dialog"
      dialogRef={dialogRef}
      labelledBy="character-join-title"
      onClose={onClose}
    >
      <header>
        <div>
          <span>Character</span>
          <h2 id="character-join-title">Join Campaign</h2>
        </div>
        <button type="button" aria-label="Close character chooser" onClick={onClose}>
          <X size={18} />
        </button>
      </header>
      <div className="character-join-body">
        <p>
          {campaignTitle
            ? `Choose who you are playing in ${campaignTitle}.`
            : 'Choose who you are playing.'}
        </p>
        {players.length ? (
          <div className="character-choice-list" aria-label="Existing characters">
            {players.map((player) => {
              const characterName =
                player.character_name || player.name || `Player ${player.player_id}`
              const playerName = player.name || 'Unknown player'
              const characterClass = player.char_class || player.class_ || 'Adventurer'
              return (
                <button
                  key={player.player_id}
                  type="button"
                  className="character-choice-card"
                  aria-label={`Join as ${characterName}`}
                  onClick={() => onJoinPlayer(player)}
                >
                  <img
                    className="character-choice-portrait"
                    src={portraitSrcForPlayer(player)}
                    alt=""
                    aria-hidden="true"
                  />
                  <span>
                    <strong>{characterName}</strong>
                    <small>
                      {playerName} / Level {player.level} {characterClass}
                    </small>
                  </span>
                  <em>Join</em>
                </button>
              )
            })}
          </div>
        ) : (
          <div className="dialog-warning">
            <strong>No characters yet.</strong>
            <span>Create the first character for this campaign.</span>
          </div>
        )}
        <footer>
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="button" onClick={onCreateCharacter}>
            Create Character
          </button>
        </footer>
      </div>
    </ModalShell>
  )
}
