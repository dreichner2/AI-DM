import { type FormEvent, type RefObject } from 'react'
import { ModalShell } from './ModalShell'
import type { Campaign, SessionImportMode, SessionSummary } from './types'
import type { SaveImportDialogState } from './useSessionActions'

export type CampaignArchiveDialogState = {
  items: Campaign[]
  loading: boolean
  error: string
  pendingId: number | null
} | null

export type SessionArchiveDialogState = {
  items: SessionSummary[]
  loading: boolean
  error: string
  pendingId: number | null
} | null

type CampaignArchiveDialogProps = {
  dialog: NonNullable<CampaignArchiveDialogState>
  dialogRef: RefObject<HTMLElement | null>
  campaign: Campaign | null
  worldNameById: ReadonlyMap<number, string>
  onArchiveSelected: () => void
  onClose: () => void
  onRestore: (campaignId: number) => void
}

type SessionArchiveDialogProps = {
  activeSession: SessionSummary | null
  campaign: Campaign | null
  dialog: NonNullable<SessionArchiveDialogState>
  dialogRef: RefObject<HTMLElement | null>
  onArchiveSelected: () => void
  onClose: () => void
  onRestore: (sessionId: number) => void
}

type SaveImportDialogProps = {
  dialog: NonNullable<SaveImportDialogState>
  dialogRef: RefObject<HTMLElement | null>
  onClose: () => void
  onModeChange: (mode: SessionImportMode) => void
  onRestoreCharactersChange: (restoreCharacters: boolean) => void
  onSubmit: (event: FormEvent<HTMLFormElement>) => void
}

function formatShortAge(value: string | null) {
  if (!value) return 'No timestamp'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'No timestamp'
  const diffMs = Date.now() - date.getTime()
  const absMs = Math.max(0, diffMs)
  const minutes = Math.floor(absMs / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days}d ago`
  const weeks = Math.floor(days / 7)
  if (weeks < 5) return `${weeks}w ago`
  const months = Math.floor(days / 30)
  return `${Math.max(1, months)}mo ago`
}

function pluralize(value: number, singular: string, plural = `${singular}s`) {
  return `${value} ${value === 1 ? singular : plural}`
}

function ArchiveLoading({ label }: { label: string }) {
  return (
    <p className="story-dialog-empty story-dialog-loading" role="status" aria-label={label}>
      <span className="story-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
    </p>
  )
}

export function CampaignArchiveDialog({
  dialog,
  dialogRef,
  campaign,
  worldNameById,
  onArchiveSelected,
  onClose,
  onRestore,
}: CampaignArchiveDialogProps) {
  return (
    <ModalShell
      closeDisabled={dialog.pendingId !== null}
      closeLabel="Close campaign archive"
      description="Archived adventures stay saved here, out of your list, until you restore them."
      dialogRef={dialogRef}
      labelledBy="campaign-archive-title"
      onClose={onClose}
      title="Campaign Archive"
    >
      <div className="story-dialog-body">
        {dialog.loading ? (
          <ArchiveLoading label="Loading campaign archive" />
        ) : dialog.items.length ? (
          <ul className="story-dialog-list" aria-label="Archived campaigns">
            {dialog.items.map((item) => {
              const worldLabel = worldNameById.get(item.world_id) ?? `World ${item.world_id}`
              const pending = dialog.pendingId === item.campaign_id
              return (
                <li key={item.campaign_id} className="story-dialog-row">
                  <span className="story-dialog-row-copy">
                    <span className="story-dialog-row-name">{item.title}</span>
                    <span className="story-dialog-row-meta">
                      {worldLabel} · archived {formatShortAge(item.updated_at ?? item.created_at)}
                    </span>
                  </span>
                  <span className="story-dialog-row-actions">
                    <button
                      type="button"
                      className="story-link"
                      onClick={() => onRestore(item.campaign_id)}
                      disabled={dialog.pendingId !== null}
                    >
                      {pending ? 'Restoring...' : 'Restore'}
                    </button>
                  </span>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="story-dialog-empty">No archived adventures yet.</p>
        )}
        {dialog.error ? <p className="story-dialog-error" role="alert">{dialog.error}</p> : null}
        <footer className="story-dialog-footer">
          <button
            type="button"
            className="story-link"
            onClick={onArchiveSelected}
            disabled={!campaign || dialog.pendingId !== null}
          >
            {dialog.pendingId === campaign?.campaign_id ? 'Archiving...' : 'Archive Selected Campaign'}
          </button>
          <button
            type="button"
            className="story-link"
            onClick={onClose}
            disabled={dialog.pendingId !== null}
          >
            Close
          </button>
        </footer>
      </div>
    </ModalShell>
  )
}

export function SessionArchiveDialog({
  activeSession,
  campaign,
  dialog,
  dialogRef,
  onArchiveSelected,
  onClose,
  onRestore,
}: SessionArchiveDialogProps) {
  return (
    <ModalShell
      closeDisabled={dialog.pendingId !== null}
      closeLabel="Close session archive"
      description={`Archived chapters of ${campaign?.title ?? 'this adventure'} stay saved here, out of the chapter list.`}
      dialogRef={dialogRef}
      labelledBy="session-archive-title"
      onClose={onClose}
      title="Session Archive"
    >
      <div className="story-dialog-body">
        {dialog.loading ? (
          <ArchiveLoading label="Loading session archive" />
        ) : dialog.items.length ? (
          <ul className="story-dialog-list" aria-label="Archived sessions">
            {dialog.items.map((item) => {
              const pending = dialog.pendingId === item.session_id
              return (
                <li key={item.session_id} className="story-dialog-row">
                  <span className="story-dialog-row-copy">
                    <span className="story-dialog-row-name">{item.display_name}</span>
                    <span className="story-dialog-row-meta">
                      {pluralize(item.turn_count ?? 0, 'turn')} · archived{' '}
                      {formatShortAge(item.updated_at ?? item.created_at)}
                    </span>
                  </span>
                  <span className="story-dialog-row-actions">
                    <button
                      type="button"
                      className="story-link"
                      onClick={() => onRestore(item.session_id)}
                      disabled={dialog.pendingId !== null}
                    >
                      {pending ? 'Restoring...' : 'Restore'}
                    </button>
                  </span>
                </li>
              )
            })}
          </ul>
        ) : (
          <p className="story-dialog-empty">No archived chapters in this adventure.</p>
        )}
        {dialog.error ? <p className="story-dialog-error" role="alert">{dialog.error}</p> : null}
        <footer className="story-dialog-footer">
          <button
            type="button"
            className="story-link"
            onClick={onArchiveSelected}
            disabled={!activeSession || dialog.pendingId !== null}
          >
            {dialog.pendingId === activeSession?.session_id ? 'Archiving...' : 'Archive Selected Session'}
          </button>
          <button
            type="button"
            className="story-link"
            onClick={onClose}
            disabled={dialog.pendingId !== null}
          >
            Close
          </button>
        </footer>
      </div>
    </ModalShell>
  )
}

/** Offered when an operator opens a save of the open campaign. */
export function SaveImportDialog({
  dialog,
  dialogRef,
  onClose,
  onModeChange,
  onRestoreCharactersChange,
  onSubmit,
}: SaveImportDialogProps) {
  const restoring = dialog.mode === 'restore'
  return (
    <ModalShell
      size="narrow"
      closeDisabled={dialog.pending}
      closeLabel="Close import save"
      describedBy="save-import-description"
      description={`${dialog.save.campaign.title} · ${dialog.save.session.display_name} · ${dialog.fileName}`}
      dialogRef={dialogRef}
      labelledBy="save-import-title"
      onClose={onClose}
      title="Import a save"
    >
      <form className="story-dialog-body" onSubmit={onSubmit}>
        <div className="story-dialog-options" role="radiogroup" aria-label="Open the save as">
          <label className="story-dialog-option">
            <input
              type="radio"
              name="save-import-mode"
              checked={!restoring}
              disabled={dialog.pending}
              onChange={() => onModeChange('archive')}
            />
            <span>Keep as a read-only archive</span>
          </label>
          <label className="story-dialog-option">
            <input
              type="radio"
              name="save-import-mode"
              checked={restoring}
              disabled={dialog.pending}
              onChange={() => onModeChange('restore')}
            />
            <span>Restore as a new chapter</span>
          </label>
        </div>
        {restoring ? (
          <label className="story-dialog-check">
            <input
              type="checkbox"
              checked={dialog.restoreCharacters}
              disabled={dialog.pending}
              onChange={(event) => onRestoreCharactersChange(event.target.checked)}
            />
            <span>Also restore characters to their saved state</span>
          </label>
        ) : null}
        {restoring && dialog.restoreCharacters ? (
          <p className="story-dialog-note story-dialog-warning" role="note">
            This replaces the characters&apos; current hit points, inventory and resources with the saved values.
          </p>
        ) : null}
        {dialog.error ? <p className="story-dialog-error" role="alert">{dialog.error}</p> : null}
        <footer className="story-dialog-footer">
          <button type="button" className="story-link" onClick={onClose} disabled={dialog.pending}>
            Cancel
          </button>
          <button type="submit" className="story-primary" data-autofocus disabled={dialog.pending}>
            {dialog.pending ? 'Importing...' : restoring ? 'Restore chapter' : 'Keep archive'}
          </button>
        </footer>
      </form>
    </ModalShell>
  )
}
