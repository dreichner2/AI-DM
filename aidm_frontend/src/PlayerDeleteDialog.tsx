import { type RefObject } from 'react'
import { X } from 'lucide-react'
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
      className="campaign-dialog player-delete-dialog"
      closeDisabled={dialog.pending}
      dialogRef={dialogRef}
      labelledBy="player-delete-title"
      describedBy="player-delete-description"
      onClose={onClose}
    >
        <header>
          <div>
            <span>Character</span>
            <h2 id="player-delete-title">Delete Character</h2>
          </div>
          <button
            type="button"
            aria-label="Close character delete"
            onClick={onClose}
            disabled={dialog.pending}
          >
            <X size={18} />
          </button>
        </header>
        <div className="dialog-body">
          <div id="player-delete-description" className="dialog-warning">
            <strong>{dialog.player.character_name || dialog.player.name}</strong>
            <span>
              This permanently removes the character from this workspace. Past
              turn history stays readable, but it will no longer point at this
              character record.
            </span>
          </div>
          {dialog.error ? <div className="dialog-error" role="alert">{dialog.error}</div> : null}
          <footer>
            <button
              type="button"
              className="secondary"
              data-autofocus
              onClick={onClose}
              disabled={dialog.pending}
            >
              Cancel
            </button>
            <button
              type="button"
              className="danger"
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
