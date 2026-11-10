import { type RefObject } from 'react'
import { ModalShell } from './ModalShell'
import type { PlayerDeleteDialogState } from './usePlayerProfileActions'

type PlayerDeleteDialogProps = {
  dialog: NonNullable<PlayerDeleteDialogState>
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onConfirm: () => void
}

export function PlayerDeleteDialog({
  dialog,
  dialogRef,
  onClose,
  onConfirm,
}: PlayerDeleteDialogProps) {
  return (
    <ModalShell
      size="narrow"
      closeDisabled={dialog.pending}
      closeLabel="Close character delete"
      dialogRef={dialogRef}
      labelledBy="player-delete-title"
      describedBy="player-delete-description"
      onClose={onClose}
      title="Delete Character"
    >
      <div className="story-dialog-body">
        <p className="story-dialog-subject">{dialog.player.character_name || dialog.player.name}</p>
        <p id="player-delete-description" className="story-dialog-note">
          This permanently removes the character from this table. Past chapters stay readable, but
          they will no longer link to this character.
        </p>
        {dialog.error ? <p className="story-dialog-error" role="alert">{dialog.error}</p> : null}
        <footer className="story-dialog-footer">
          <button
            type="button"
            className="story-link"
            data-autofocus
            onClick={onClose}
            disabled={dialog.pending}
          >
            Cancel
          </button>
          <button
            type="button"
            className="story-link story-link-danger"
            onClick={onConfirm}
            disabled={dialog.pending}
          >
            {dialog.pending ? 'Deleting...' : 'Delete Character'}
          </button>
        </footer>
      </div>
    </ModalShell>
  )
}
