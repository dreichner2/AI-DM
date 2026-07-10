import { type FormEventHandler, type RefObject } from 'react'
import { X } from 'lucide-react'
import { ModalShell } from './ModalShell'
import type { CampaignActionDialogState } from './useCampaignActions'
import type { SessionActionDialogState } from './useSessionActions'

type CampaignActionDialogProps = {
  dialog: NonNullable<CampaignActionDialogState>
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onDescriptionChange: (description: string) => void
  onSubmit: FormEventHandler<HTMLFormElement>
  onTitleChange: (title: string) => void
}

const CAMPAIGN_ACTION_LABELS = {
  rename: {
    title: 'Rename Campaign',
    pending: 'Saving...',
    submit: 'Save Campaign',
  },
  archive: {
    title: 'Archive Campaign',
    pending: 'Archiving...',
    submit: 'Archive Campaign',
  },
  restore: {
    title: 'Restore Campaign',
    pending: 'Restoring...',
    submit: 'Restore Campaign',
  },
  delete: {
    title: 'Delete Campaign',
    pending: 'Deleting...',
    submit: 'Delete Campaign',
  },
} as const

export function CampaignActionDialog({
  dialog,
  dialogRef,
  onClose,
  onDescriptionChange,
  onSubmit,
  onTitleChange,
}: CampaignActionDialogProps) {
  const labels = CAMPAIGN_ACTION_LABELS[dialog.mode]
  return (
    <ModalShell
      className="campaign-dialog campaign-action-dialog"
      closeDisabled={dialog.pending}
      dialogRef={dialogRef}
      labelledBy="campaign-action-title"
      onClose={onClose}
    >
      <header>
        <div>
          <span>Campaign</span>
          <h2 id="campaign-action-title">{labels.title}</h2>
        </div>
        <button
          type="button"
          aria-label="Close campaign action"
          onClick={onClose}
          disabled={dialog.pending}
        >
          <X size={18} />
        </button>
      </header>
      <form onSubmit={onSubmit}>
        {dialog.mode === 'rename' ? (
          <>
            <label>
              Campaign Name
              <input
                autoFocus
                data-autofocus
                value={dialog.title}
                onChange={(event) => onTitleChange(event.target.value)}
                disabled={dialog.pending}
              />
            </label>
            <label>
              Description
              <textarea
                value={dialog.description}
                onChange={(event) => onDescriptionChange(event.target.value)}
                disabled={dialog.pending}
              />
            </label>
          </>
        ) : (
          <div className="dialog-warning">
            <strong>{dialog.title}</strong>
            <span>
              {dialog.mode === 'archive'
                ? 'Archiving hides this campaign and its sessions from the normal workspace list without destroying saved history.'
                : dialog.mode === 'restore'
                  ? 'Restoring makes this campaign and sessions archived with it available for normal play again.'
                  : 'This permanently deletes the campaign, its sessions, maps, and campaign notes from this workspace. Characters stay in the workspace but are detached from it.'}
            </span>
          </div>
        )}
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
            type="submit"
            className={dialog.mode === 'archive' || dialog.mode === 'delete' ? 'danger' : undefined}
            disabled={dialog.pending}
          >
            {dialog.pending ? labels.pending : labels.submit}
          </button>
        </footer>
      </form>
    </ModalShell>
  )
}

type SessionActionDialogProps = {
  dialog: NonNullable<SessionActionDialogState>
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onNameChange: (name: string) => void
  onSubmit: FormEventHandler<HTMLFormElement>
}

export function SessionActionDialog({
  dialog,
  dialogRef,
  onClose,
  onNameChange,
  onSubmit,
}: SessionActionDialogProps) {
  const rename = dialog.mode === 'rename'
  return (
    <ModalShell
      className="campaign-dialog session-action-dialog"
      closeDisabled={dialog.pending}
      dialogRef={dialogRef}
      labelledBy="session-action-title"
      onClose={onClose}
    >
      <header>
        <div>
          <span>Session</span>
          <h2 id="session-action-title">{rename ? 'Rename Session' : 'Delete Session'}</h2>
        </div>
        <button
          type="button"
          aria-label="Close session action"
          onClick={onClose}
          disabled={dialog.pending}
        >
          <X size={18} />
        </button>
      </header>
      <form onSubmit={onSubmit}>
        {rename ? (
          <label>
            Session Name
            <input
              autoFocus
              data-autofocus
              value={dialog.name}
              onChange={(event) => onNameChange(event.target.value)}
              disabled={dialog.pending}
            />
          </label>
        ) : (
          <div className="dialog-warning">
            <strong>{dialog.name}</strong>
            <span>
              This permanently deletes this session and its saved turn history. Use the archive
              button if you only want to hide it.
            </span>
          </div>
        )}
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
            type="submit"
            className={rename ? undefined : 'danger'}
            disabled={dialog.pending}
          >
            {dialog.pending
              ? rename
                ? 'Renaming...'
                : 'Deleting...'
              : rename
                ? 'Rename Session'
                : 'Delete Session'}
          </button>
        </footer>
      </form>
    </ModalShell>
  )
}
