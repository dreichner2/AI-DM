import { type FormEventHandler, type RefObject } from 'react'
import { ModalShell } from './ModalShell'
import type { CampaignActionDialogState } from './useCampaignActions'
import type { SessionActionDialogState, StartChapterDialogState } from './useSessionActions'

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
    title: 'Rename the adventure',
    pending: 'Saving...',
    submit: 'Save',
    consequence: '',
  },
  archive: {
    title: 'Archive this adventure',
    pending: 'Archiving...',
    submit: 'Archive this adventure',
    consequence:
      'Archiving hides this adventure and its chapters from your list. Nothing is lost; you can restore it later.',
  },
  restore: {
    title: 'Restore this adventure',
    pending: 'Restoring...',
    submit: 'Restore this adventure',
    consequence: 'Restoring brings this adventure, and the chapters archived with it, back into play.',
  },
  delete: {
    title: 'Delete this adventure',
    pending: 'Deleting...',
    submit: 'Delete this adventure',
    consequence:
      'This permanently deletes the adventure with its chapters, maps and notes. Characters stay at this table but leave the adventure.',
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
  const destructive = dialog.mode === 'archive' || dialog.mode === 'delete'
  return (
    <ModalShell
      size={dialog.mode === 'rename' ? 'standard' : 'narrow'}
      closeDisabled={dialog.pending}
      closeLabel="Close adventure action"
      dialogRef={dialogRef}
      labelledBy="campaign-action-title"
      onClose={onClose}
      title={labels.title}
    >
      <form className="story-dialog-body" onSubmit={onSubmit}>
        {dialog.mode === 'rename' ? (
          <>
            <label className="story-dialog-field">
              <span className="story-label">Adventure name</span>
              <input
                autoFocus
                data-autofocus
                value={dialog.title}
                onChange={(event) => onTitleChange(event.target.value)}
                disabled={dialog.pending}
              />
            </label>
            <label className="story-dialog-field">
              <span className="story-label">Description</span>
              <textarea
                value={dialog.description}
                onChange={(event) => onDescriptionChange(event.target.value)}
                disabled={dialog.pending}
              />
            </label>
          </>
        ) : (
          <>
            <p className="story-dialog-subject">{dialog.title}</p>
            <p className="story-dialog-note">{labels.consequence}</p>
          </>
        )}
        {dialog.error ? <p className="story-dialog-error" role="alert">{dialog.error}</p> : null}
        <footer className="story-dialog-footer">
          <button type="button" className="story-link" onClick={onClose} disabled={dialog.pending}>
            Cancel
          </button>
          <button
            type="submit"
            className={destructive ? 'story-link story-link-danger' : 'story-primary'}
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
      size={rename ? 'standard' : 'narrow'}
      closeDisabled={dialog.pending}
      closeLabel="Close chapter action"
      dialogRef={dialogRef}
      labelledBy="session-action-title"
      onClose={onClose}
      title={rename ? 'Rename the chapter' : 'Delete this chapter'}
    >
      <form className="story-dialog-body" onSubmit={onSubmit}>
        {rename ? (
          <label className="story-dialog-field">
            <span className="story-label">Chapter name</span>
            <input
              autoFocus
              data-autofocus
              value={dialog.name}
              onChange={(event) => onNameChange(event.target.value)}
              disabled={dialog.pending}
            />
          </label>
        ) : (
          <>
            <p className="story-dialog-subject">{dialog.name}</p>
            <p className="story-dialog-note">
              This permanently deletes this chapter and everything said in it. Archive the chapter
              instead if you only want to hide it.
            </p>
          </>
        )}
        {dialog.error ? <p className="story-dialog-error" role="alert">{dialog.error}</p> : null}
        <footer className="story-dialog-footer">
          <button type="button" className="story-link" onClick={onClose} disabled={dialog.pending}>
            Cancel
          </button>
          <button
            type="submit"
            className={rename ? 'story-primary' : 'story-link story-link-danger'}
            disabled={dialog.pending}
          >
            {dialog.pending
              ? rename
                ? 'Renaming...'
                : 'Deleting...'
              : rename
                ? 'Save'
                : 'Delete this chapter'}
          </button>
        </footer>
      </form>
    </ModalShell>
  )
}

type StartChapterDialogProps = {
  dialog: NonNullable<StartChapterDialogState>
  dialogRef: RefObject<HTMLElement | null>
  /** Operators may drop an unfinished fight or roll when the server refuses to close the chapter. */
  canForce: boolean
  onClose: () => void
  onFreshChange: (fresh: boolean) => void
  onForceChange: (force: boolean) => void
  onSubmit: FormEventHandler<HTMLFormElement>
}

export function StartChapterDialog({
  dialog,
  dialogRef,
  canForce,
  onClose,
  onFreshChange,
  onForceChange,
  onSubmit,
}: StartChapterDialogProps) {
  return (
    <ModalShell
      size="narrow"
      closeDisabled={dialog.pending}
      closeLabel="Close start chapter"
      describedBy="chapter-start-description"
      description="The story continues: the world, your quests and your progress carry over. The current chapter is closed and kept for reading."
      dialogRef={dialogRef}
      labelledBy="chapter-start-title"
      onClose={onClose}
      title="Start the next chapter"
    >
      <form className="story-dialog-body" onSubmit={onSubmit}>
        <label className="story-dialog-check">
          <input
            type="checkbox"
            checked={dialog.fresh}
            disabled={dialog.pending}
            onChange={(event) => onFreshChange(event.target.checked)}
          />
          <span>Start over from the beginning instead</span>
        </label>
        {dialog.fresh ? (
          <p className="story-dialog-note story-dialog-warning" role="note">
            The world resets to the adventure&apos;s opening. Your characters keep their progress.
          </p>
        ) : null}
        {dialog.error ? <p className="story-dialog-error" role="alert">{dialog.error}</p> : null}
        {dialog.unsettled && canForce ? (
          <label className="story-dialog-check">
            <input
              type="checkbox"
              checked={dialog.force}
              disabled={dialog.pending}
              onChange={(event) => onForceChange(event.target.checked)}
            />
            <span>Start anyway and drop the unfinished fight or roll</span>
          </label>
        ) : null}
        <footer className="story-dialog-footer">
          <button type="button" className="story-link" onClick={onClose} disabled={dialog.pending}>
            Cancel
          </button>
          <button type="submit" className="story-primary" data-autofocus disabled={dialog.pending}>
            {dialog.pending ? 'Starting...' : 'Start chapter'}
          </button>
        </footer>
      </form>
    </ModalShell>
  )
}
