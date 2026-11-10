import { type FormEvent, type RefObject } from 'react'
import { X } from 'lucide-react'
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
      className="campaign-dialog archive-dialog"
      dialogRef={dialogRef}
      labelledBy="campaign-archive-title"
      onClose={onClose}
    >
        <header>
          <div>
            <span>Archive</span>
            <h2 id="campaign-archive-title">Campaign Archive</h2>
          </div>
          <button
            type="button"
            aria-label="Close campaign archive"
            onClick={onClose}
            disabled={dialog.pendingId !== null}
          >
            <X size={18} />
          </button>
        </header>
        <div className="dialog-body">
          <div className="dialog-warning">
            <strong>{campaign?.title ?? 'No campaign selected'}</strong>
            <span>Archived campaigns stay saved here, hidden from the active campaign rail.</span>
          </div>
          <div className="world-manager-list" aria-label="Archived campaigns">
            {dialog.loading ? (
              <div className="rail-skeleton-list" aria-label="Loading campaign archive">
                <span />
                <span />
                <span />
              </div>
            ) : dialog.items.length ? (
              dialog.items.map((item) => {
                const worldLabel = worldNameById.get(item.world_id) ?? `World ${item.world_id}`
                const pending = dialog.pendingId === item.campaign_id
                return (
                  <div key={item.campaign_id} className="world-manager-row">
                    <span>
                      <strong>{item.title}</strong>
                      <small>
                        {worldLabel} / Updated {formatShortAge(item.updated_at ?? item.created_at)}
                      </small>
                    </span>
                    <div>
                      <button
                        type="button"
                        onClick={() => onRestore(item.campaign_id)}
                        disabled={dialog.pendingId !== null}
                      >
                        {pending ? 'Restoring...' : 'Restore'}
                      </button>
                    </div>
                  </div>
                )
              })
            ) : (
              <div className="dialog-warning">
                <strong>No archived campaigns.</strong>
                <span>Archive an active campaign and it will appear here.</span>
              </div>
            )}
          </div>
          {dialog.error ? <div className="dialog-error" role="alert">{dialog.error}</div> : null}
          <footer>
            <button
              type="button"
              className="secondary"
              onClick={onClose}
              disabled={dialog.pendingId !== null}
            >
              Close
            </button>
            <button
              type="button"
              onClick={onArchiveSelected}
              disabled={!campaign || dialog.pendingId !== null}
            >
              {dialog.pendingId === campaign?.campaign_id ? 'Archiving...' : 'Archive Selected Campaign'}
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
      className="campaign-dialog archive-dialog"
      dialogRef={dialogRef}
      labelledBy="session-archive-title"
      onClose={onClose}
    >
        <header>
          <div>
            <span>Archive</span>
            <h2 id="session-archive-title">Session Archive</h2>
          </div>
          <button
            type="button"
            aria-label="Close session archive"
            onClick={onClose}
            disabled={dialog.pendingId !== null}
          >
            <X size={18} />
          </button>
        </header>
        <div className="dialog-body">
          <div className="dialog-warning">
            <strong>{campaign?.title ?? 'No campaign selected'}</strong>
            <span>Archived sessions stay saved here, hidden from the active session rail.</span>
          </div>
          <div className="world-manager-list" aria-label="Archived sessions">
            {dialog.loading ? (
              <div className="rail-skeleton-list" aria-label="Loading session archive">
                <span />
                <span />
                <span />
              </div>
            ) : dialog.items.length ? (
              dialog.items.map((item) => {
                const title = item.display_name
                const pending = dialog.pendingId === item.session_id
                return (
                  <div key={item.session_id} className="world-manager-row">
                    <span>
                      <strong>{title}</strong>
                      <small>
                        {pluralize(item.turn_count ?? 0, 'turn')} / Updated{' '}
                        {formatShortAge(item.updated_at ?? item.created_at)}
                      </small>
                    </span>
                    <div>
                      <button
                        type="button"
                        onClick={() => onRestore(item.session_id)}
                        disabled={dialog.pendingId !== null}
                      >
                        {pending ? 'Restoring...' : 'Restore'}
                      </button>
                    </div>
                  </div>
                )
              })
            ) : (
              <div className="dialog-warning">
                <strong>No archived sessions.</strong>
                <span>Archive a session in this campaign and it will appear here.</span>
              </div>
            )}
          </div>
          {dialog.error ? <div className="dialog-error" role="alert">{dialog.error}</div> : null}
          <footer>
            <button
              type="button"
              className="secondary"
              onClick={onClose}
              disabled={dialog.pendingId !== null}
            >
              Close
            </button>
            <button
              type="button"
              onClick={onArchiveSelected}
              disabled={!activeSession || dialog.pendingId !== null}
            >
              {dialog.pendingId === activeSession?.session_id ? 'Archiving...' : 'Archive Selected Session'}
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
      className="campaign-dialog chapter-start-dialog"
      closeDisabled={dialog.pending}
      describedBy="save-import-description"
      dialogRef={dialogRef}
      labelledBy="save-import-title"
      onClose={onClose}
    >
      <header>
        <div>
          <span>Import</span>
          <h2 id="save-import-title">Import a save</h2>
        </div>
        <button type="button" aria-label="Close import save" onClick={onClose} disabled={dialog.pending}>
          <X size={18} />
        </button>
      </header>
      <form onSubmit={onSubmit}>
        <p id="save-import-description" className="chapter-start-copy">
          {dialog.save.campaign.title} · {dialog.save.session.display_name} · {dialog.fileName}
        </p>
        <label className="chapter-start-option">
          <input
            type="radio"
            name="save-import-mode"
            checked={!restoring}
            disabled={dialog.pending}
            onChange={() => onModeChange('archive')}
          />
          <span>Keep as a read-only archive</span>
        </label>
        <label className="chapter-start-option">
          <input
            type="radio"
            name="save-import-mode"
            checked={restoring}
            disabled={dialog.pending}
            onChange={() => onModeChange('restore')}
          />
          <span>Restore as a new chapter</span>
        </label>
        {restoring ? (
          <label className="chapter-start-option">
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
          <div className="dialog-warning" role="note">
            <span>
              This replaces the characters&apos; current hit points, inventory and resources with the saved values.
            </span>
          </div>
        ) : null}
        {dialog.error ? <div className="dialog-error" role="alert">{dialog.error}</div> : null}
        <footer>
          <button type="button" className="secondary" onClick={onClose} disabled={dialog.pending}>
            Cancel
          </button>
          <button type="submit" data-autofocus disabled={dialog.pending}>
            {dialog.pending ? 'Importing...' : restoring ? 'Restore chapter' : 'Keep archive'}
          </button>
        </footer>
      </form>
    </ModalShell>
  )
}
