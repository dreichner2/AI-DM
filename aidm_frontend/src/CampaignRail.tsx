import type { Dispatch, SetStateAction } from 'react'
import { Archive, Globe2, Pencil, Plus, Trash2 } from 'lucide-react'
import { NavItem, StatusDot, ThinIcon, Thumbnail } from './AppChrome'
import type { InspectorTab } from './InspectorPanel'

type MainTab = 'turns' | 'dm' | 'notes'

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
  canManageCampaign: boolean
  canManageSession: boolean
  canOpenCampaignArchive: boolean
  canOpenSessionArchive: boolean
  onRenameCampaign: () => void
  onArchiveCampaign: () => void
  onDeleteCampaign: () => void
  onCreateCampaign: () => void
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
  canManageCampaign,
  canManageSession,
  canOpenCampaignArchive,
  canOpenSessionArchive,
  onRenameCampaign,
  onArchiveCampaign,
  onDeleteCampaign,
  onCreateCampaign,
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
    <aside className="campaign-rail">
      <section className="rail-section">
        <div className="rail-heading">
          <span>Campaigns</span>
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
            <button type="button" aria-label="Add campaign" title="Add campaign" onClick={onCreateCampaign}>
              <Plus size={16} />
            </button>
          </div>
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
            <div className="empty-rail">No campaigns match.</div>
          )}
        </div>
      </section>

      <section className="rail-section session-section">
        <div className="rail-heading">
          <span>Sessions ({campaignTitle ?? 'None'})</span>
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
              <button type="button" onClick={onStartSession} disabled={!selectedCampaignId}>
                Start session
              </button>
            </div>
          )}
        </div>
      </section>

      <nav className="rail-nav">
        <NavItem
          icon={<ThinIcon name="archive" size={18} />}
          label="Campaigns"
          selected={mainTab === 'turns' && inspectorTab === 'party'}
          onClick={() => {
            setMainTab('turns')
            setInspectorTab('party')
          }}
        />
        <NavItem
          icon={<ThinIcon name="turns" size={18} />}
          label="Turns"
          selected={mainTab === 'turns'}
          onClick={() => setMainTab('turns')}
        />
        <NavItem
          icon={<ThinIcon name="map" size={18} />}
          label="Map"
          selected={inspectorTab === 'map'}
          onClick={() => setInspectorTab('map')}
        />
        <NavItem
          icon={<ThinIcon name="book" size={18} />}
          label="Canon"
          selected={inspectorTab === 'canon'}
          onClick={() => setInspectorTab('canon')}
        />
        <NavItem
          icon={<ThinIcon name="briefcase" size={18} />}
          label="Inventory"
          selected={inspectorTab === 'inventory'}
          onClick={() => setInspectorTab('inventory')}
        />
        <NavItem
          icon={<ThinIcon name="settings" size={18} />}
          label="Settings"
          selected={mainTab === 'notes'}
          onClick={() => setMainTab('notes')}
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
