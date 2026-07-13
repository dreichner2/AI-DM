import { type RefObject } from 'react'
import { X } from 'lucide-react'
import { ModalShell } from './ModalShell'
import type { Campaign, JsonRecord, SessionSummary } from './types'

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
  selectedCampaignId: number | null
}

function isRecord(value: unknown): value is JsonRecord {
  return Boolean(value && typeof value === 'object' && !Array.isArray(value))
}

function stringValue(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : ''
}

function snapshotRecord(session: SessionSummary | null | undefined) {
  return isRecord(session?.state_snapshot) ? session.state_snapshot : {}
}

function sessionDisplayName(session: SessionSummary, fallbackPrefix: string | number | null) {
  const snapshot = snapshotRecord(session)
  return (
    stringValue(session.display_name) ||
    stringValue(snapshot.name) ||
    stringValue(snapshot.title) ||
    `S${fallbackPrefix ?? '-'}E${session.session_id}`
  )
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
  selectedCampaignId,
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
                const title = sessionDisplayName(item, campaign?.world_id ?? selectedCampaignId)
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
