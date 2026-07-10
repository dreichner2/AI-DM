import { type RefObject } from 'react'
import { X } from 'lucide-react'
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
      className="campaign-dialog saved-workspace-delete-dialog"
      closeDisabled={dialog.pending}
      dialogRef={dialogRef}
      labelledBy="saved-workspace-delete-title"
      onClose={onClose}
    >
      <header>
        <div>
          <span>{deletesTable ? 'Delete' : 'Remove'}</span>
          <h2 id="saved-workspace-delete-title">
            {deletesTable ? 'Delete Table' : 'Remove Saved Table'}
          </h2>
        </div>
        <button
          type="button"
          aria-label="Close saved table delete"
          onClick={onClose}
          disabled={dialog.pending}
        >
          <X size={18} />
        </button>
      </header>
      <div className="dialog-body">
        <div className="dialog-warning">
          <strong>{savedWorkspaceDisplayName(dialog.workspace)}</strong>
          <span>
            {deletesTable
              ? 'This permanently deletes the table for everyone. This cannot be undone.'
              : 'This removes the table from your saved tables only.'}
          </span>
        </div>
        {dialog.error ? <div className="dialog-error">{dialog.error}</div> : null}
        <footer>
          <button
            type="button"
            className="secondary"
            onClick={onClose}
            disabled={dialog.pending}
          >
            Cancel
          </button>
          <button
            type="button"
            className={deletesTable ? 'danger' : undefined}
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
      className="campaign-dialog share-session-dialog"
      describedBy="share-session-description"
      dialogRef={dialogRef}
      labelledBy="share-session-title"
      onClose={onClose}
    >
      <header>
        <div>
          <span>Table Link</span>
          <h2 id="share-session-title">Share Session</h2>
        </div>
        <button type="button" aria-label="Close share session" onClick={onClose}>
          <X size={18} />
        </button>
      </header>
      <label>
        Session Link
        <input
          data-autofocus
          readOnly
          aria-label="Session share link"
          value={url}
          onFocus={(event) => event.currentTarget.select()}
        />
      </label>
      <p id="share-session-description">
        Send this to someone who can open this frontend and reach this backend. They can choose or
        create their own character after it opens.
      </p>
      <footer>
        <button type="button" className="secondary" onClick={onClose}>
          Close
        </button>
        <button type="button" onClick={onCopy}>
          Copy Link
        </button>
      </footer>
    </ModalShell>
  )
}
