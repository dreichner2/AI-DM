import type { Dispatch, SetStateAction } from 'react'
import { Archive, Globe2, Pencil, Plus, Trash2, Upload, X } from 'lucide-react'
import { NavItem, StatusDot, ThinIcon, Thumbnail } from './AppChrome'
import type { InspectorTab } from './InspectorPanel'

type MainTab = 'turns' | 'dm' | 'notes'

export const CAMPAIGN_RAIL_ID = 'campaign-rail-drawer'

export type CampaignCard = {
  id: number
  title: string
  meta: string
  avatar: string
}

export type SessionCard = {
  id: number
  title: string
  meta: string
}

export type RailError = {
  id: string
  category: string
  message: string
  createdAt: number
}

type CampaignRailProps = {
  inert?: boolean
  modal?: boolean
  onRequestClose?: () => void
  backendStatus: string | null
  campaignTitle: string | null
  campaignCards: CampaignCard[]
  sessionCards: SessionCard[]
  campaignFilter: string
  setCampaignFilter: Dispatch<SetStateAction<string>>
  selectedCampaignId: number | null
  selectedSessionId: number | null
  loadingCampaignId: number | null
  sessionLoading: boolean
  workspaceLoading: boolean
  mainTab: MainTab
  setMainTab: Dispatch<SetStateAction<MainTab>>
  inspectorTab: InspectorTab
  setInspectorTab: Dispatch<SetStateAction<InspectorTab>>
  canUseOperatorTools: boolean
  canManageCampaign: boolean
  canManageSession: boolean
  canOpenCampaignArchive: boolean
  canOpenSessionArchive: boolean
  selectionLocked: boolean
  onRenameCampaign: () => void
  onArchiveCampaign: () => void
  onDeleteCampaign: () => void
  onCreateCampaign: () => void
  onImportCampaignPack: () => void
  onManageWorlds: () => void
  onRenameSession: () => void
  onArchiveSession: () => void
  onDeleteSession: () => void
  onStartSession: () => void
  onSelectCampaign: (campaignId: number) => void
  onSelectSession: (sessionId: number) => void
  lastSyncLabel: string
  onRefreshWorkspace: () => void
  errors: RailError[]
}

function formatErrorClock(value: number) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

export function CampaignRail({
  inert,
  modal = false,
  onRequestClose,
  backendStatus,
  campaignTitle,
  campaignCards,
  sessionCards,
  campaignFilter,
  setCampaignFilter,
  selectedCampaignId,
  selectedSessionId,
  loadingCampaignId,
  sessionLoading,
  workspaceLoading,
  mainTab,
  setMainTab,
  inspectorTab,
  setInspectorTab,
  canUseOperatorTools,
  canManageCampaign,
  canManageSession,
  canOpenCampaignArchive,
  canOpenSessionArchive,
  selectionLocked,
  onRenameCampaign,
  onArchiveCampaign,
  onDeleteCampaign,
  onCreateCampaign,
  onImportCampaignPack,
  onManageWorlds,
  onRenameSession,
  onArchiveSession,
  onDeleteSession,
  onStartSession,
  onSelectCampaign,
  onSelectSession,
  lastSyncLabel,
  onRefreshWorkspace,
  errors,
}: CampaignRailProps) {
  const backendReady = backendStatus === 'ok'
  const backendChecking = backendStatus === null

  return (
    <aside
      id={CAMPAIGN_RAIL_ID}
      className="campaign-rail"
      role={modal ? 'dialog' : undefined}
      aria-label="Campaign and session navigation"
      aria-modal={modal ? true : undefined}
      inert={inert ? true : undefined}
    >
      {modal && onRequestClose ? (
        <button
          type="button"
          className="drawer-close-button"
          aria-label="Close campaign menu"
          onClick={onRequestClose}
        >
          <X size={18} />
        </button>
      ) : null}
      <section className="rail-section">
        <div className="rail-heading campaign-heading">
          <span>Campaigns</span>
          {canUseOperatorTools ? (
            <div className="rail-heading-actions">
              <button
                type="button"
                aria-label="Rename selected campaign"
                title="Rename campaign"
                onClick={onRenameCampaign}
                disabled={!canManageCampaign}
              >
                <Pencil size={14} />
              </button>
              <button
                type="button"
                aria-label="Open campaign archive"
                title="Archive and restore campaigns"
                onClick={onArchiveCampaign}
                disabled={!canOpenCampaignArchive}
              >
                <Archive size={14} />
              </button>
              <button
                type="button"
                aria-label="Delete selected campaign"
                title="Delete campaign"
                onClick={onDeleteCampaign}
                disabled={!canManageCampaign}
              >
                <Trash2 size={14} />
              </button>
              <button type="button" aria-label="Manage worlds" title="Manage worlds" onClick={onManageWorlds}>
                <Globe2 size={15} />
              </button>
              <button
                type="button"
                aria-label="Import campaign pack"
                title="Import campaign pack"
                onClick={onImportCampaignPack}
              >
                <Upload size={15} />
              </button>
              <button type="button" aria-label="Add campaign" title="Add campaign" onClick={onCreateCampaign}>
                <Plus size={16} />
              </button>
            </div>
          ) : null}
        </div>
        <div className="search-field">
          <ThinIcon name="spark" size={14} />
          <input
            value={campaignFilter}
            onChange={(event) => setCampaignFilter(event.target.value)}
            placeholder="Search campaigns..."
            aria-label="Search campaigns"
          />
        </div>
        <div className="campaign-list">
          {campaignCards.length ? (
            campaignCards.map((item, index) => (
              <button
                type="button"
                key={item.id}
                className={`campaign-card ${item.id === selectedCampaignId ? 'active' : ''} ${
                  item.id === loadingCampaignId ? 'loading' : ''
                }`}
                aria-current={item.id === selectedCampaignId ? 'true' : undefined}
                aria-busy={item.id === loadingCampaignId}
                disabled={selectionLocked && item.id !== selectedCampaignId}
                title={selectionLocked && item.id !== selectedCampaignId ? 'Wait for the active turn to finish.' : undefined}
                onClick={() => onSelectCampaign(item.id)}
              >
                <Thumbnail
                  index={index}
                  selected={item.id === selectedCampaignId}
                  src={item.avatar}
                  title={item.title}
                />
                <span>
                  <strong>{item.title}</strong>
                  <small>{item.meta}</small>
                </span>
              </button>
            ))
          ) : backendChecking ? (
            <div className="rail-skeleton-list" aria-label="Loading campaigns">
              <span />
              <span />
              <span />
            </div>
          ) : (
            <div className="empty-rail">
              {campaignFilter.trim() ? 'No campaigns match your search.' : 'No campaigns yet.'}
            </div>
          )}
        </div>
      </section>

      <section className="rail-section session-section">
        <div className="rail-heading">
          <span>Sessions ({campaignTitle ?? 'None'})</span>
          {canUseOperatorTools ? (
            <div className="rail-heading-actions">
              <button
                type="button"
                onClick={onRenameSession}
                aria-label="Rename selected session"
                title="Rename session"
                disabled={!canManageSession}
              >
                <Pencil size={14} />
              </button>
              <button
                type="button"
                onClick={onArchiveSession}
                aria-label="Open session archive"
                title="Archive and restore sessions"
                disabled={!canOpenSessionArchive}
              >
                <Archive size={14} />
              </button>
              <button
                type="button"
                onClick={onDeleteSession}
                aria-label="Delete selected session"
                title="Delete session permanently"
                disabled={!canManageSession}
              >
                <Trash2 size={14} />
              </button>
              <button type="button" onClick={onStartSession} aria-label="Start session" title="Start session">
                <Plus size={16} />
              </button>
            </div>
          ) : null}
        </div>
        <div className="session-list">
          {sessionCards.length ? (
            sessionCards.map((session) => (
              <button
                type="button"
                key={session.id}
                className={`session-card ${session.id === selectedSessionId ? 'active' : ''} ${
                  session.id === selectedSessionId && sessionLoading ? 'loading' : ''
                }`}
                aria-current={session.id === selectedSessionId ? 'true' : undefined}
                aria-busy={session.id === selectedSessionId && sessionLoading}
                disabled={selectionLocked && session.id !== selectedSessionId}
                title={selectionLocked && session.id !== selectedSessionId ? 'Wait for the active turn to finish.' : undefined}
                onClick={() => onSelectSession(session.id)}
              >
                <strong>{session.title}</strong>
                <small>{session.meta}</small>
              </button>
            ))
          ) : workspaceLoading ? (
            <div className="rail-skeleton-list" aria-label="Loading sessions">
              <span />
              <span />
              <span />
            </div>
          ) : (
            <div className="empty-rail empty-action-card">
              <span>No sessions yet.</span>
              {canUseOperatorTools ? (
                <button type="button" onClick={onStartSession} disabled={!selectedCampaignId}>
                  Start session
                </button>
              ) : null}
            </div>
          )}
        </div>
      </section>

      <nav className="rail-nav">
        <NavItem
          icon={<ThinIcon name="turns" size={18} />}
          label="Adventure"
          selected={mainTab === 'turns' && inspectorTab === 'party'}
          onClick={() => {
            setMainTab('turns')
            setInspectorTab('party')
          }}
        />
        <NavItem
          icon={<ThinIcon name="map" size={18} />}
          label="Map"
          selected={inspectorTab === 'map'}
          onClick={() => setInspectorTab('map')}
        />
        <NavItem
          icon={<ThinIcon name="book" size={18} />}
          label="Memory"
          selected={inspectorTab === 'canon'}
          onClick={() => setInspectorTab('canon')}
        />
        <NavItem
          icon={<ThinIcon name="briefcase" size={18} />}
          label="Inventory"
          selected={inspectorTab === 'inventory'}
          onClick={() => setInspectorTab('inventory')}
        />
      </nav>

      <footer className="rail-footer">
        <StatusDot
          label={backendReady ? 'All Systems Operational' : backendChecking ? 'Checking Backend' : 'Backend Offline'}
          tone={backendReady ? 'good' : backendChecking ? 'neutral' : 'warn'}
        />
        <span>
          Last sync: {lastSyncLabel}
          <button
            type="button"
            className="rail-sync-button"
            aria-label="Refresh workspace"
            onClick={onRefreshWorkspace}
          >
            <ThinIcon name="refresh" size={13} />
          </button>
        </span>
        {errors[0] ? (
          <details className="rail-error-history">
            <summary>
              <span>{errors[0].category}</span>
              {errors[0].message}
            </summary>
            <ul>
              {errors.map((item) => (
                <li key={item.id}>
                  <strong>{item.category}</strong>
                  <span>{formatErrorClock(item.createdAt)}</span>
                  <p>{item.message}</p>
                </li>
              ))}
            </ul>
          </details>
        ) : null}
      </footer>
    </aside>
  )
}
