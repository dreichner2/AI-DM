import { type RefObject } from 'react'
import { ModalShell } from './ModalShell'
import type { AccountWorkspace } from './types'
import { savedWorkspaceDisplayName } from './workspaceLabels'

export type SavedWorkspaceDeleteDialogState = {
  workspace: AccountWorkspace
  error: string
  pending: boolean
} | null

type SavedWorkspaceDeleteDialogProps = {
  deletesTable: boolean
  dialog: NonNullable<SavedWorkspaceDeleteDialogState>
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onConfirm: () => void
}

export function SavedWorkspaceDeleteDialog({
  deletesTable,
  dialog,
  dialogRef,
  onClose,
  onConfirm,
}: SavedWorkspaceDeleteDialogProps) {
  return (
    <ModalShell
      size="narrow"
      closeDisabled={dialog.pending}
      closeLabel="Close saved table delete"
      dialogRef={dialogRef}
      labelledBy="saved-workspace-delete-title"
      onClose={onClose}
      title={deletesTable ? 'Delete Table' : 'Remove Saved Table'}
    >
      <div className="story-dialog-body">
        <p className="story-dialog-subject">{savedWorkspaceDisplayName(dialog.workspace)}</p>
        <p className="story-dialog-note">
          {deletesTable
            ? 'This permanently deletes the table for everyone. This cannot be undone.'
            : 'This removes the table from your saved tables only.'}
        </p>
        {dialog.error ? <p className="story-dialog-error" role="alert">{dialog.error}</p> : null}
        <footer className="story-dialog-footer">
          <button type="button" className="story-link" onClick={onClose} disabled={dialog.pending}>
            Cancel
          </button>
          <button
            type="button"
            className={deletesTable ? 'story-link story-link-danger' : 'story-primary'}
            onClick={onConfirm}
            disabled={dialog.pending}
          >
            {dialog.pending
              ? deletesTable
                ? 'Deleting...'
                : 'Removing...'
              : deletesTable
                ? 'Delete Table'
                : 'Remove'}
          </button>
        </footer>
      </div>
    </ModalShell>
  )
}

type ShareSessionDialogProps = {
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onCopy: () => void
  url: string
}

export function ShareSessionDialog({
  dialogRef,
  onClose,
  onCopy,
  url,
}: ShareSessionDialogProps) {
  return (
    <ModalShell
      size="narrow"
      closeLabel="Close share session"
      describedBy="share-session-description"
      description="Send this link to a friend. It opens this table, where they choose or create their own character."
      dialogRef={dialogRef}
      labelledBy="share-session-title"
      onClose={onClose}
      title="Share Session"
    >
      <div className="story-dialog-body">
        <label className="story-dialog-field">
          <span className="story-label">Invite link</span>
          <input
            data-autofocus
            readOnly
            aria-label="Session share link"
            className="story-dialog-link-field"
            value={url}
            onFocus={(event) => event.currentTarget.select()}
          />
        </label>
        <footer className="story-dialog-footer">
          <button type="button" className="story-link" onClick={onClose}>
            Close
          </button>
          <button type="button" className="story-primary" onClick={onCopy}>
            Copy Link
          </button>
        </footer>
      </div>
    </ModalShell>
  )
}
