import { useState, type ChangeEvent, type Dispatch, type RefObject, type SetStateAction } from 'react'
import {
  ArrowDown,
  ChevronDown,
  ClipboardList,
  Download,
  MoreHorizontal,
  Share2,
  Upload,
} from 'lucide-react'
import { ActionComposer, type ActionComposerProps } from './ActionComposer'
import { ThinIcon, ToolbarButton } from './AppChrome'
import {
  speakerDetail,
  truncateText,
  turnNumber,
  turnPersistenceLabel,
} from './gameSelectors'
import { SceneMusicPlayer } from './SceneMusicPlayer'
import type { SceneMusicControlPayload, SceneMusicSyncState } from './SceneMusicPlayer'
import type { Campaign, ClarificationRequest, Player, SessionState, SessionSummary, TimelineEntry } from './types'

export type MainTab = 'turns' | 'dm' | 'notes'

type ChatTextSize = 'default' | 'large' | 'extra'
type ChatTextFont = 'default' | 'sans' | 'mono'

type ChatTextSettings = {
  size: ChatTextSize
  font: ChatTextFont
}

type DmExecutionStats = {
  tokens: number | string
  time: string
  model: string
  temperature: string
}

type CanonFact = [fact: string, source: string]

const CHAT_TEXT_SETTINGS_STORAGE_KEY = 'aidm:chatTextSettings'
const DEFAULT_CHAT_TEXT_SETTINGS: ChatTextSettings = {
  size: 'default',
  font: 'default',
}

function isChatTextSize(value: unknown): value is ChatTextSize {
  return value === 'default' || value === 'large' || value === 'extra'
}

function isChatTextFont(value: unknown): value is ChatTextFont {
  return value === 'default' || value === 'sans' || value === 'mono'
}

function loadChatTextSettings(): ChatTextSettings {
  try {
    const rawValue = localStorage.getItem(CHAT_TEXT_SETTINGS_STORAGE_KEY)
    if (!rawValue) return DEFAULT_CHAT_TEXT_SETTINGS
    const parsed = JSON.parse(rawValue) as Partial<ChatTextSettings>
    return {
      size: isChatTextSize(parsed.size) ? parsed.size : DEFAULT_CHAT_TEXT_SETTINGS.size,
      font: isChatTextFont(parsed.font) ? parsed.font : DEFAULT_CHAT_TEXT_SETTINGS.font,
    }
  } catch {
    return DEFAULT_CHAT_TEXT_SETTINGS
  }
}

function saveChatTextSettings(settings: ChatTextSettings) {
  try {
    localStorage.setItem(CHAT_TEXT_SETTINGS_STORAGE_KEY, JSON.stringify(settings))
  } catch {
    // Reading controls still work for the current page when storage is unavailable.
  }
}

type SessionBoardProps = {
  activeSessionTitle: string
  campaignTitle: string
  sessionId: number | null
  playerId: number | null
  showSceneMusicPlayer: boolean
  duckMusicForNarration: boolean
  sceneMusicSyncState: SceneMusicSyncState | null
  onSceneMusicControl: (payload: SceneMusicControlPayload) => void
  workspaceLoading: boolean
  sessionLoading: boolean
  mainTab: MainTab
  setMainTab: Dispatch<SetStateAction<MainTab>>
  downloadSessionJson: () => Promise<void>
  sessionImportPending: boolean
  sessionImportInputRef: RefObject<HTMLInputElement | null>
  importSessionJson: (event: ChangeEvent<HTMLInputElement>) => Promise<void>
  shareSession: () => void
  sessionMenuRef: RefObject<HTMLDivElement | null>
  sessionMenuOpen: boolean
  setSessionMenuOpen: Dispatch<SetStateAction<boolean>>
  refreshCurrentWorkspace: () => Promise<void>
  activeSession: SessionSummary | null
  openRenameSessionDialog: () => void
  openDeleteSessionDialog: () => void
  notesCount: number
  turnFeedRef: RefObject<HTMLElement | null>
  updateJumpToLatestVisibility: () => void
  sessionLogHasMore: boolean
  olderLogLoading: boolean
  loadOlderSessionLog: () => Promise<void>
  turnRows: TimelineEntry[]
  expandedTurnIds: Set<string>
  setExpandedTurnIds: Dispatch<SetStateAction<Set<string>>>
  selectedPlayer: Player | null
  currentResponseEntry: TimelineEntry | null
  latestDmText: string
  sendPending: boolean
  streamingTurnActive: boolean
  dmExecutionStats: DmExecutionStats
  welcomeText: string
  showJumpToLatest: boolean
  scrollTurnFeedToLatest: () => void
  questTitle: string
  sessionState: SessionState | null
  campaign: Campaign | null
  canonFacts: CanonFact[]
  clarificationRequest: ClarificationRequest | null
  resolveClarification: (selectedItemId: string) => void
  actionComposerProps: ActionComposerProps
}

function formatDateTime(value: string | null) {
  if (!value) return 'Not recorded'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'Not recorded'
  return date.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

function formatClock(value: string | null) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
}

export function SessionBoard({
  activeSessionTitle,
  campaignTitle,
  sessionId,
  playerId,
  showSceneMusicPlayer,
  duckMusicForNarration,
  sceneMusicSyncState,
  onSceneMusicControl,
  workspaceLoading,
  sessionLoading,
  mainTab,
  setMainTab,
  downloadSessionJson,
  sessionImportPending,
  sessionImportInputRef,
  importSessionJson,
  shareSession,
  sessionMenuRef,
  sessionMenuOpen,
  setSessionMenuOpen,
  refreshCurrentWorkspace,
  activeSession,
  openRenameSessionDialog,
  openDeleteSessionDialog,
  notesCount,
  turnFeedRef,
  updateJumpToLatestVisibility,
  sessionLogHasMore,
  olderLogLoading,
  loadOlderSessionLog,
  turnRows,
  expandedTurnIds,
  setExpandedTurnIds,
  selectedPlayer,
  currentResponseEntry,
  latestDmText,
  sendPending,
  streamingTurnActive,
  dmExecutionStats,
  welcomeText,
  showJumpToLatest,
  scrollTurnFeedToLatest,
  questTitle,
  sessionState,
  campaign,
  canonFacts,
  clarificationRequest,
  resolveClarification,
  actionComposerProps,
}: SessionBoardProps) {
  const loading = workspaceLoading || sessionLoading
  const [chatTextSettings, setChatTextSettings] = useState(loadChatTextSettings)
  const [chatTextMenuOpen, setChatTextMenuOpen] = useState(false)
  const streamLabel =
    currentResponseEntry && turnPersistenceLabel(currentResponseEntry)
      ? turnPersistenceLabel(currentResponseEntry)
      : sendPending || streamingTurnActive ? 'Streaming...' : 'Ready'
  const chatTextClassName = `chat-text-size-${chatTextSettings.size} chat-text-font-${chatTextSettings.font}`

  const updateChatTextSettings = (nextSettings: ChatTextSettings) => {
    setChatTextSettings(nextSettings)
    saveChatTextSettings(nextSettings)
  }

  const toggleTurnExpanded = (turnId: string) => {
    setExpandedTurnIds((current) => {
      const next = new Set(current)
      if (next.has(turnId)) {
        next.delete(turnId)
      } else {
        next.add(turnId)
      }
      return next
    })
  }

  return (
    <main className="session-board">
      <section className="session-header">
        <div>
          <h1>
            {activeSessionTitle}{' '}
            <span className={loading ? 'loading-badge' : ''}>
              {loading ? 'Loading' : 'Live'}
            </span>
          </h1>
          <p>{campaignTitle}</p>
        </div>
        <div className="session-actions">
          <ToolbarButton
            icon={<ClipboardList size={17} />}
            onClick={() => setMainTab('notes')}
            title="Summary"
          >
            Summary
          </ToolbarButton>
          <ToolbarButton
            icon={<Download size={17} />}
            onClick={() => void downloadSessionJson()}
            title="Export"
          >
            Export
          </ToolbarButton>
          <ToolbarButton
            disabled={sessionImportPending}
            icon={<Upload size={17} />}
            onClick={() => sessionImportInputRef.current?.click()}
            title="Import"
          >
            {sessionImportPending ? 'Importing' : 'Import'}
          </ToolbarButton>
          <input
            ref={sessionImportInputRef}
            aria-label="Import session file"
            className="file-input-hidden"
            type="file"
            accept="application/json,.json"
            onChange={(event) => void importSessionJson(event)}
            disabled={sessionImportPending}
          />
          <ToolbarButton icon={<Share2 size={17} />} onClick={shareSession} title="Share">
            Share
          </ToolbarButton>
          <div className="session-menu-wrap" ref={sessionMenuRef}>
            <ToolbarButton
              icon={<MoreHorizontal size={18} />}
              onClick={() => setSessionMenuOpen((current) => !current)}
              title="Session menu"
              id="session-menu-button"
              ariaExpanded={sessionMenuOpen}
              ariaControls="session-menu"
            />
            {sessionMenuOpen ? (
              <div
                id="session-menu"
                className="session-menu"
                role="menu"
                aria-label="Session actions"
                aria-labelledby="session-menu-button"
              >
                <button type="button" role="menuitem" onClick={() => void refreshCurrentWorkspace()}>
                  Refresh session
                </button>
                <button type="button" role="menuitem" disabled={!activeSession} onClick={openRenameSessionDialog}>
                  Rename session
                </button>
                <button type="button" role="menuitem" disabled={!activeSession} className="danger" onClick={openDeleteSessionDialog}>
                  Delete session
                </button>
              </div>
            ) : null}
          </div>
        </div>
      </section>

      <div className="content-tabs" role="tablist" aria-label="Session views">
        <button
          type="button"
          role="tab"
          aria-selected={mainTab === 'turns'}
          className={mainTab === 'turns' ? 'active' : ''}
          onClick={() => setMainTab('turns')}
        >
          Turns
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mainTab === 'dm'}
          className={mainTab === 'dm' ? 'active' : ''}
          onClick={() => setMainTab('dm')}
        >
          DM Response
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mainTab === 'notes'}
          className={mainTab === 'notes' ? 'active' : ''}
          onClick={() => setMainTab('notes')}
        >
          Notes ({notesCount})
        </button>
      </div>

      {showSceneMusicPlayer ? (
        <SceneMusicPlayer
          sessionId={sessionId}
          playerId={playerId}
          duckForNarration={duckMusicForNarration}
          musicSyncState={sceneMusicSyncState}
          onMusicControl={onSceneMusicControl}
        />
      ) : null}

      <div className="chat-reading-control">
        <button
          type="button"
          className="chat-reading-toggle"
          aria-label="Chat text options"
          aria-expanded={chatTextMenuOpen}
          aria-controls="chat-reading-menu"
          title="Chat text options"
          onClick={() => setChatTextMenuOpen((current) => !current)}
        >
          Aa
        </button>
        {chatTextMenuOpen ? (
          <div id="chat-reading-menu" className="chat-reading-menu" role="group" aria-label="Chat text display">
            <label>
              <span>Size</span>
              <select
                aria-label="Chat text size"
                value={chatTextSettings.size}
                onChange={(event) =>
                  updateChatTextSettings({
                    ...chatTextSettings,
                    size: event.target.value as ChatTextSize,
                  })
                }
              >
                <option value="default">Default</option>
                <option value="large">Large</option>
                <option value="extra">Extra</option>
              </select>
            </label>
            <label>
              <span>Font</span>
              <select
                aria-label="Chat text font"
                value={chatTextSettings.font}
                onChange={(event) =>
                  updateChatTextSettings({
                    ...chatTextSettings,
                    font: event.target.value as ChatTextFont,
                  })
                }
              >
                <option value="default">Default</option>
                <option value="sans">Sans</option>
                <option value="mono">Mono</option>
              </select>
            </label>
          </div>
        ) : null}
      </div>

      {mainTab === 'turns' ? (
        <>
          <section
            className={`turn-feed ${chatTextClassName}`}
            ref={turnFeedRef}
            onScroll={updateJumpToLatestVisibility}
          >
            {loading ? (
              <div className="panel-loading-strip" role="status">
                {sessionLoading ? 'Loading session history...' : 'Loading campaign workspace...'}
              </div>
            ) : null}
            {sessionLogHasMore ? (
              <button
                type="button"
                className="load-history-button"
                onClick={() => void loadOlderSessionLog()}
                disabled={olderLogLoading}
              >
                {olderLogLoading ? 'Loading older turns...' : 'Load older turns'}
              </button>
            ) : null}
            {turnRows.length ? (
              turnRows.map((turn, index) => {
                const expanded = expandedTurnIds.has(turn.id)
                return (
                  <article className="turn-row" key={turn.id}>
                    <div className="turn-number">{turnNumber(turn, index)}</div>
                    <div className={`turn-card ${expanded ? 'expanded' : ''}`}>
                      <div className="turn-speaker">
                        <strong>{turn.speaker}</strong>
                        <span>{speakerDetail(turn, selectedPlayer)}</span>
                      </div>
                      {turnPersistenceLabel(turn) ? (
                        <span className="turn-status-label">{turnPersistenceLabel(turn)}</span>
                      ) : null}
                      <p>{expanded ? turn.text : truncateText(turn.text, 180)}</p>
                      <time>{formatClock(turn.timestamp)}</time>
                      <button
                        type="button"
                        className="turn-expand"
                        aria-label={expanded ? 'Collapse turn' : 'Expand turn'}
                        aria-expanded={expanded}
                        onClick={() => toggleTurnExpanded(turn.id)}
                      >
                        <ChevronDown size={18} />
                      </button>
                    </div>
                  </article>
                )
              })
            ) : (
              <div className="empty-state">
                {activeSession ? welcomeText : 'No turn log entries loaded for this session.'}
              </div>
            )}

            {currentResponseEntry ? (
              <article className="turn-row current">
                <div className="turn-number">
                  {turnNumber(currentResponseEntry, turnRows.length)}
                </div>
                <div className="dm-response-card">
                  <div className="turn-speaker">
                    <strong>{currentResponseEntry.speaker}</strong>
                    <span>{currentResponseEntry.streaming ? 'Streaming' : 'Latest Response'}</span>
                  </div>
                  <div className="response-copy">
                    <p>{latestDmText}</p>
                  </div>
                  <div className={`stream-state ${sendPending || streamingTurnActive ? 'streaming' : ''}`}>
                    <span />
                    {streamLabel}
                  </div>
                  <div className="execution-footer">
                    Tokens: {dmExecutionStats.tokens} <span>|</span> Time: {dmExecutionStats.time}{' '}
                    <span>|</span> Model: {dmExecutionStats.model} <span>|</span> Temp:{' '}
                    {dmExecutionStats.temperature}
                  </div>
                </div>
              </article>
            ) : null}
          </section>
          {showJumpToLatest ? (
            <button
              type="button"
              className="jump-latest-button"
              onClick={scrollTurnFeedToLatest}
            >
              <ArrowDown size={14} />
              Latest
            </button>
          ) : null}
        </>
      ) : null}

      {mainTab === 'dm' ? (
        <section className={`turn-feed single-panel ${chatTextClassName}`}>
          {loading ? (
            <div className="panel-loading-strip" role="status">
              {sessionLoading ? 'Loading session response...' : 'Loading campaign workspace...'}
            </div>
          ) : null}
          <article className="turn-row current">
            <div className="turn-number">
              {currentResponseEntry ? turnNumber(currentResponseEntry, 0) : '—'}
            </div>
            <div className="dm-response-card expanded">
              <div className="turn-speaker">
                <strong>{currentResponseEntry?.speaker ?? 'DM'}</strong>
                <span>Full Response</span>
              </div>
              <div className="response-copy">
                <p>{latestDmText}</p>
              </div>
              <div className={`stream-state ${sendPending || streamingTurnActive ? 'streaming' : ''}`}>
                <span />
                {streamLabel}
              </div>
              <div className="execution-footer">
                Tokens: {dmExecutionStats.tokens} <span>|</span> Time: {dmExecutionStats.time}{' '}
                <span>|</span> Model: {dmExecutionStats.model} <span>|</span> Temp:{' '}
                {dmExecutionStats.temperature}
              </div>
            </div>
          </article>
        </section>
      ) : null}

      {mainTab === 'notes' ? (
        <section className="turn-feed notes-panel">
          <div className="notes-card">
            <h2>Session State</h2>
            <dl>
              <dt>Current quest</dt>
              <dd>{questTitle}</dd>
              <dt>Current location</dt>
              <dd>{sessionState?.current_location || campaign?.location || 'No location recorded'}</dd>
              <dt>Updated</dt>
              <dd>{formatDateTime(sessionState?.updated_at ?? null)}</dd>
            </dl>
            <h3>Rolling Summary</h3>
            <p>{sessionState?.rolling_summary || 'No rolling summary recorded yet.'}</p>
          </div>
          <div className="notes-card compact-notes">
            <h3>Recent Memory</h3>
            {canonFacts.length ? (
              canonFacts.slice(0, 5).map(([fact, source]) => (
                <div key={`${fact}-${source}`} className="note-line">
                  <ThinIcon name="dot" size={12} />
                  <span>{fact}</span>
                  <small>{source}</small>
                </div>
              ))
            ) : (
              <p>No memory snippets recorded yet.</p>
            )}
          </div>
        </section>
      ) : null}

      {clarificationRequest ? (
        <section className="clarification-panel" aria-live="polite">
          <div>
            <strong>{clarificationRequest.prompt}</strong>
            <span>{clarificationRequest.originalPlayerMessage}</span>
          </div>
          <div className="clarification-options">
            {clarificationRequest.options.map((option) => (
              <button
                type="button"
                key={option.itemId}
                onClick={() => resolveClarification(option.itemId)}
              >
                <span>{option.label}</span>
                {option.description ? <small>{option.description}</small> : null}
              </button>
            ))}
          </div>
        </section>
      ) : null}

      <ActionComposer {...actionComposerProps} />
    </main>
  )
}
