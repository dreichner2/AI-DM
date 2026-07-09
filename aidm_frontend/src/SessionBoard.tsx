import { lazy, Suspense, useEffect, useState, type ChangeEvent, type Dispatch, type RefObject, type SetStateAction } from 'react'
import {
  ArrowDown,
  BookOpen,
  ChevronDown,
  ClipboardList,
  Download,
  MoreHorizontal,
  Share2,
  SlidersHorizontal,
  Sparkles,
  Trash2,
  Upload,
  Volume2,
  X,
} from 'lucide-react'
import { ActionComposer, type ActionComposerProps } from './ActionComposer'
import { ThinIcon, ToolbarButton } from './AppChrome'
import {
  CONTENT_RATING_OPTIONS,
  CONTENT_TONE_TAG_OPTIONS,
  type ContentRating,
  type ContentSettings,
} from './contentSettings'
import {
  type PendingRollNotice,
  speakerDetail,
  truncateText,
  turnNumber,
  turnPersistenceLabel,
} from './gameSelectors'
import { NarrativeProse } from './NarrativeProse'
import { profileIconSrcForCharacter } from './profileIcons'
import type { SceneMusicControlPayload, SceneMusicSyncState } from './SceneMusicPlayer'
import type { SceneDisplayState } from './sceneState'
import type { ActivePlayer, Campaign, ClarificationRequest, Player, SessionState, SessionSummary, TimelineEntry } from './types'

const SceneMusicPlayer = lazy(() =>
  import('./SceneMusicPlayer').then((module) => ({ default: module.SceneMusicPlayer })),
)

export type MainTab = 'turns' | 'dm' | 'notes'
export type BoardViewMode = 'theater' | 'ops'
export type TurnQualityScores = {
  coherence: number
  fun: number
  rules: number
}

export type DirectorCommentaryCheckpoint = {
  checkpointId: string
  title: string
  status?: string
  summary?: string
  edgeType?: string
  fromCheckpointId?: string
  fromTitle?: string
}

export type DirectorCommentaryRecord = {
  id: string
  title: string
  summary?: string
  hidden?: boolean
  checkpointIds?: string[]
}

export type DirectorCommentaryPayload = {
  enabled: boolean
  pack: {
    packId: string
    title: string
    version?: string
  } | null
  routeTaken: DirectorCommentaryCheckpoint[]
  roadsNotTaken: DirectorCommentaryCheckpoint[]
  alternateEndings: DirectorCommentaryCheckpoint[]
  undiscoveredRecords: Record<string, DirectorCommentaryRecord[]>
  summary: {
    routeTakenCount: number
    roadsNotTakenCount: number
    alternateEndingsCount: number
    undiscoveredRecordsCount: number
  }
  commentary: string[]
}

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
const BOARD_VIEW_MODE_STORAGE_KEY = 'aidm:boardViewMode'
const QUALITY_SCORE_OPTIONS = [1, 2, 3, 4, 5] as const
const DEFAULT_TURN_QUALITY_SCORES: TurnQualityScores = {
  coherence: 4,
  fun: 4,
  rules: 4,
}
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

function loadBoardViewMode(): BoardViewMode {
  try {
    const rawValue = localStorage.getItem(BOARD_VIEW_MODE_STORAGE_KEY)
    return rawValue === 'ops' || rawValue === 'theater' ? rawValue : 'ops'
  } catch {
    return 'ops'
  }
}

function saveBoardViewMode(mode: BoardViewMode) {
  try {
    localStorage.setItem(BOARD_VIEW_MODE_STORAGE_KEY, mode)
  } catch {
    // The current page can still switch modes if storage is unavailable.
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
  sceneState: SceneDisplayState | null
  onSceneMusicControl: (payload: SceneMusicControlPayload) => void
  contentSettings: ContentSettings
  contentSettingsPending: boolean
  canEditContentSettings: boolean
  onContentRatingChange: (rating: ContentRating) => void
  onContentToneTagsChange: (toneTags: string[]) => void
  onBoardViewModeChange?: (mode: BoardViewMode) => void
  directorCommentary: DirectorCommentaryPayload | null
  sessionRecap: string
  onSpeakSessionRecap: (text: string) => void
  workspaceLoading: boolean
  sessionLoading: boolean
  mainTab: MainTab
  setMainTab: Dispatch<SetStateAction<MainTab>>
  showMobilePresenceStrip: boolean
  activePlayers: ActivePlayer[]
  downloadCampaignChronicle: () => Promise<void>
  downloadSessionChronicle: () => Promise<void>
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
  dismissTimelineEntry: (turnId: string) => void
  reportedBadTurnIds: Set<number>
  reportingBadTurnIds: Set<number>
  reportBadTurn: (entry: TimelineEntry) => void
  ratedTurnQualityIds: Set<number>
  ratingTurnQualityIds: Set<number>
  submitTurnQuality: (entry: TimelineEntry, scores: TurnQualityScores) => void
  expandedTurnIds: Set<string>
  setExpandedTurnIds: Dispatch<SetStateAction<Set<string>>>
  selectedPlayer: Player | null
  currentResponseEntry: TimelineEntry | null
  latestDmText: string
  sendPending: boolean
  streamingTurnActive: boolean
  pendingRollNotice: PendingRollNotice | null
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
  onStartAdventure: () => void
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

function timelineMetadataString(entry: TimelineEntry, key: string) {
  const value = entry.metadata[key]
  return typeof value === 'string' ? value.trim().toLowerCase() : ''
}

function canDismissLocalTimelineEntry(entry: TimelineEntry) {
  if (entry.role !== 'player') return false
  const persistenceStatus = timelineMetadataString(entry, 'persistence_status')
  const hasClientMessageId = Boolean(timelineMetadataString(entry, 'client_message_id'))
  const localEntry = entry.id.startsWith('local-') || hasClientMessageId
  return localEntry && (persistenceStatus === 'pending' || persistenceStatus === 'failed')
}

function timelineTurnId(entry: TimelineEntry) {
  const rawValue = entry.metadata.turn_id
  const parsed = typeof rawValue === 'number' ? rawValue : Number(rawValue)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function canReportBadTurn(entry: TimelineEntry | null) {
  return Boolean(entry && entry.role === 'dm' && !entry.streaming && timelineTurnId(entry) !== null)
}

function canRateTurnQuality(entry: TimelineEntry | null) {
  return canReportBadTurn(entry)
}

function RollWaitBanner({ notice }: { notice: PendingRollNotice }) {
  return (
    <section
      className={`roll-wait-banner ${notice.isWaitingOnSelectedPlayer ? 'current-player' : ''}`}
      role="status"
      aria-label="Pending roll"
    >
      <div className="roll-wait-icon" aria-hidden="true">
        <ThinIcon name="dice" size={18} />
      </div>
      <div className="roll-wait-copy">
        <strong>Waiting on {notice.waitingOnLabel} to roll</strong>
        <span>
          {notice.turnLabel}: {notice.ruleLabel}
          {notice.isWaitingOnSelectedPlayer ? ' - your character is up' : ''}
        </span>
        <small>{notice.detail}</small>
      </div>
      <div className="roll-wait-meta">
        {notice.pendingCount > 1 ? `${notice.pendingCount} pending checks` : 'Roll needed'}
      </div>
    </section>
  )
}

function SceneHeader({ sceneState }: { sceneState: SceneDisplayState | null }) {
  if (!sceneState) return null
  return (
    <div className="scene-state-header" aria-label="Current scene">
      <span>{sceneState.locationName}</span>
      <small>
        {sceneState.sceneType}
        {sceneState.mood ? ` / ${sceneState.mood}` : ''}
        {sceneState.inCombat ? ' / combat' : ` / danger ${sceneState.dangerLevel}`}
      </small>
    </div>
  )
}

function OperatorDrawer({
  boardViewMode,
  canEditContentSettings,
  contentSettings,
  contentSettingsPending,
  dmExecutionStats,
  onBoardViewModeChange,
  onContentRatingChange,
  onContentToneTagsChange,
}: {
  boardViewMode: BoardViewMode
  canEditContentSettings: boolean
  contentSettings: ContentSettings
  contentSettingsPending: boolean
  dmExecutionStats: DmExecutionStats
  onBoardViewModeChange: (mode: BoardViewMode) => void
  onContentRatingChange: (rating: ContentRating) => void
  onContentToneTagsChange: (toneTags: string[]) => void
}) {
  const toneTagSet = new Set(contentSettings.toneTags)
  const toggleToneTag = (tag: string) => {
    const nextTags = toneTagSet.has(tag)
      ? contentSettings.toneTags.filter((item) => item !== tag)
      : [...contentSettings.toneTags, tag].slice(0, 4)
    onContentToneTagsChange(nextTags)
  }
  return (
    <details className="operator-drawer">
      <summary>
        <SlidersHorizontal size={15} />
        Operator
      </summary>
      <div className="operator-drawer-body">
        <div className="operator-mode-toggle" role="group" aria-label="Board view mode">
          <button
            type="button"
            aria-pressed={boardViewMode === 'theater'}
            onClick={() => onBoardViewModeChange('theater')}
          >
            Theater
          </button>
          <button
            type="button"
            aria-pressed={boardViewMode === 'ops'}
            onClick={() => onBoardViewModeChange('ops')}
          >
            Ops
          </button>
        </div>
        <div className="operator-rating-toggle" role="group" aria-label="Content rating">
          {CONTENT_RATING_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={contentSettings.contentRating === option.value}
              disabled={!canEditContentSettings || contentSettingsPending}
              onClick={() => onContentRatingChange(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
        <div className="operator-tone-toggle" role="group" aria-label="Tone tags">
          {CONTENT_TONE_TAG_OPTIONS.map((tag) => (
            <button
              key={tag}
              type="button"
              aria-pressed={toneTagSet.has(tag)}
              disabled={!canEditContentSettings || contentSettingsPending}
              onClick={() => toggleToneTag(tag)}
            >
              {formatDirectorStatus(tag)}
            </button>
          ))}
        </div>
        <dl>
          <div>
            <dt>Rating</dt>
            <dd>{CONTENT_RATING_OPTIONS.find((option) => option.value === contentSettings.contentRating)?.label}</dd>
          </div>
          <div>
            <dt>Tokens</dt>
            <dd>{dmExecutionStats.tokens}</dd>
          </div>
          <div>
            <dt>Time</dt>
            <dd>{dmExecutionStats.time}</dd>
          </div>
          <div>
            <dt>Model</dt>
            <dd>{dmExecutionStats.model}</dd>
          </div>
          <div>
            <dt>Temp</dt>
            <dd>{dmExecutionStats.temperature}</dd>
          </div>
          <div>
            <dt>Tone</dt>
            <dd>{contentSettings.toneTags.length ? contentSettings.toneTags.join(', ') : 'Default'}</dd>
          </div>
        </dl>
      </div>
    </details>
  )
}

function contentRatingLabel(contentSettings: ContentSettings) {
  return CONTENT_RATING_OPTIONS.find((option) => option.value === contentSettings.contentRating)?.label ?? 'Not set'
}

function PreviouslyOnCard({
  onSpeak,
  text,
  updatedAt,
}: {
  onSpeak: (text: string) => void
  text: string
  updatedAt: string | null
}) {
  const recapText = text.trim()
  if (!recapText) return null
  return (
    <aside className="previously-on-card" aria-label="Previously On">
      <div>
        <span>Previously On</span>
        {updatedAt ? <time>{formatDateTime(updatedAt)}</time> : null}
        <button type="button" aria-label="Play recap" title="Play recap" onClick={() => onSpeak(recapText)}>
          <Volume2 size={15} />
        </button>
      </div>
      <NarrativeProse text={recapText} />
    </aside>
  )
}

function formatDirectorStatus(status: string | undefined) {
  if (!status) return ''
  return status.replace(/[_-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase())
}

function DirectorCheckpointList({
  emptyText,
  items,
}: {
  emptyText: string
  items: DirectorCommentaryCheckpoint[]
}) {
  if (!items.length) return <p>{emptyText}</p>
  return (
    <ol className="director-checkpoint-list">
      {items.slice(0, 5).map((item) => (
        <li key={`${item.fromCheckpointId ?? 'route'}:${item.checkpointId}:${item.edgeType ?? item.status ?? ''}`}>
          <strong>{item.title || item.checkpointId}</strong>
          <span>
            {item.edgeType ? `${formatDirectorStatus(item.edgeType)} from ${item.fromTitle || item.fromCheckpointId}` : formatDirectorStatus(item.status)}
          </span>
        </li>
      ))}
    </ol>
  )
}

function DirectorUndiscoveredList({ records }: { records: Record<string, DirectorCommentaryRecord[]> }) {
  const groups = Object.entries(records)
    .map(([collection, items]) => ({ collection, items }))
    .filter((group) => group.items.length)
    .slice(0, 4)
  if (!groups.length) return <p>No hidden campaign-pack records remain.</p>
  return (
    <div className="director-record-groups">
      {groups.map((group) => (
        <div key={group.collection}>
          <strong>{formatDirectorStatus(group.collection)}</strong>
          <span>{group.items.slice(0, 3).map((item) => item.title || item.id).join(', ')}</span>
        </div>
      ))}
    </div>
  )
}

function DirectorCommentaryPanel({
  activeSessionTitle,
  canonFacts,
  commentary,
  contentSettings,
  currentResponseEntry,
  dmExecutionStats,
  latestDmText,
  onClose,
  questTitle,
  sceneState,
  sessionState,
  streamLabel,
}: {
  activeSessionTitle: string
  canonFacts: CanonFact[]
  commentary: DirectorCommentaryPayload | null
  contentSettings: ContentSettings
  currentResponseEntry: TimelineEntry | null
  dmExecutionStats: DmExecutionStats
  latestDmText: string
  onClose: () => void
  questTitle: string
  sceneState: SceneDisplayState | null
  sessionState: SessionState | null
  streamLabel: string
}) {
  const sceneName = sceneState?.locationName || sessionState?.current_location || 'Scene unset'
  const sceneDetail = sceneState
    ? `${sceneState.sceneType}${sceneState.mood ? ` / ${sceneState.mood}` : ''}${
        sceneState.inCombat ? ' / combat' : ` / danger ${sceneState.dangerLevel}`
      }`
    : questTitle
  const latestResponseLabel = currentResponseEntry?.streaming
    ? 'Streaming'
    : currentResponseEntry
      ? 'Latest response ready'
      : 'Awaiting response'
  const responseSummary = latestDmText.trim()
    ? truncateText(latestDmText, 156)
    : 'No DM prose recorded yet.'
  const memoryHighlights = canonFacts.slice(0, 3)
  const packTitle = commentary?.pack?.title || commentary?.pack?.packId || ''
  const commentaryNotes = commentary?.commentary.slice(0, 3) ?? []

  return (
    <section
      id="director-commentary-panel"
      className="director-commentary-panel"
      aria-labelledby="director-commentary-title"
    >
      <div className="director-commentary-heading">
        <div>
          <span>{activeSessionTitle}</span>
          <h2 id="director-commentary-title">Director Commentary</h2>
        </div>
        <button
          type="button"
          aria-label="Close Director Commentary"
          title="Close Director Commentary"
          onClick={onClose}
        >
          <X size={17} />
        </button>
      </div>
      <dl className="director-commentary-list">
        {commentary?.enabled ? (
          <div>
            <dt>Pack</dt>
            <dd>
              <strong>{packTitle}</strong>
              <span>
                {commentary.summary.routeTakenCount} reached / {commentary.summary.roadsNotTakenCount} branch
                {commentary.summary.roadsNotTakenCount === 1 ? '' : 'es'} missed /{' '}
                {commentary.summary.undiscoveredRecordsCount} hidden
              </span>
            </dd>
          </div>
        ) : null}
        <div>
          <dt>Scene</dt>
          <dd>
            <strong>{sceneName}</strong>
            <span>{sceneDetail}</span>
          </dd>
        </div>
        <div>
          <dt>Pacing</dt>
          <dd>
            <strong>{latestResponseLabel}</strong>
            <span>
              {streamLabel} / {dmExecutionStats.time} / {dmExecutionStats.tokens} tokens
            </span>
          </dd>
        </div>
        <div>
          <dt>Tone</dt>
          <dd>
            <strong>{contentRatingLabel(contentSettings)}</strong>
            <span>{dmExecutionStats.model} at temp {dmExecutionStats.temperature}</span>
          </dd>
        </div>
        <div>
          <dt>Latest Beat</dt>
          <dd>
            <span>{responseSummary}</span>
          </dd>
        </div>
      </dl>
      {commentary?.enabled ? (
        <div className="director-pack-grid" aria-label="Campaign pack director notes">
          <section>
            <span>Route Taken</span>
            <DirectorCheckpointList emptyText="No checkpoints reached yet." items={commentary.routeTaken} />
          </section>
          <section>
            <span>Roads Not Taken</span>
            <DirectorCheckpointList emptyText="No alternate branches recorded yet." items={commentary.roadsNotTaken} />
          </section>
          <section>
            <span>Undiscovered</span>
            <DirectorUndiscoveredList records={commentary.undiscoveredRecords} />
          </section>
          {commentaryNotes.length ? (
            <section>
              <span>Notes</span>
              {commentaryNotes.map((note) => (
                <p key={note}>{note}</p>
              ))}
            </section>
          ) : null}
        </div>
      ) : null}
      <div className="director-memory-strip" aria-label="Director memory">
        <span>Memory</span>
        {memoryHighlights.length ? (
          memoryHighlights.map(([fact, source]) => (
            <p key={`${fact}-${source}`}>
              {fact}
              <small>{source}</small>
            </p>
          ))
        ) : (
          <p>No memory snippets recorded yet.</p>
        )}
      </div>
    </section>
  )
}

function activePlayerAvatarSrc(player: ActivePlayer) {
  return (
    player.profile_image ||
    profileIconSrcForCharacter({ race: player.race, sex: player.sex }) ||
    '/profile-icons/human_male.png'
  )
}

function activePlayerInitial(player: ActivePlayer) {
  return (player.character_name || player.name || '?').slice(0, 1).toUpperCase()
}

function MobilePresenceStrip({
  activePlayers,
  selectedPlayerId,
  selectedPlayerHasTurn,
  turnControlStatusLabel,
}: {
  activePlayers: ActivePlayer[]
  selectedPlayerId: number | null
  selectedPlayerHasTurn: boolean
  turnControlStatusLabel: string
}) {
  const typingPlayers = activePlayers.filter(
    (player) => player.id !== selectedPlayerId && player.is_typing,
  )
  const typingLabel = typingPlayers.length
    ? `${typingPlayers.slice(0, 2).map((player) => player.character_name).join(', ')}${typingPlayers.length > 2 ? ` +${typingPlayers.length - 2}` : ''} typing`
    : activePlayers.length ? 'Watching table' : 'No friends online'

  return (
    <section className="mobile-presence-strip" aria-label="Mobile active players">
      <div className={`mobile-presence-summary ${selectedPlayerHasTurn ? 'open' : 'locked'}`}>
        <span>{activePlayers.length ? `${activePlayers.length} online` : 'Solo'}</span>
        <strong>{typingLabel}</strong>
      </div>
      {activePlayers.length ? (
        <ul className="mobile-presence-list" aria-label="Active players on mobile">
          {activePlayers.map((player) => {
            const isSelectedPlayer = player.id === selectedPlayerId
            const isOtherPlayerTyping = !isSelectedPlayer && player.is_typing
            const health = player.health
            return (
              <li
                key={player.id}
                className={`${isSelectedPlayer ? 'selected' : ''} ${isOtherPlayerTyping ? 'typing' : ''}`}
              >
                <span className="mobile-presence-avatar" aria-hidden="true">
                  <img src={activePlayerAvatarSrc(player)} alt="" />
                  <span>{activePlayerInitial(player)}</span>
                  {health ? <span className={`mobile-health-dot mobile-health-dot-${health.tone}`} /> : null}
                </span>
                <span className="mobile-presence-copy">
                  <strong>{player.character_name}</strong>
                  <small>{isSelectedPlayer ? 'You' : player.name}</small>
                </span>
                {health ? (
                  <span
                    className="mobile-health-label"
                    aria-label={`${player.character_name} health: ${health.label}`}
                    title={`${health.label}: ${health.currentHp}/${health.maxHp} HP`}
                  >
                    {health.label}
                  </span>
                ) : null}
                {isOtherPlayerTyping ? (
                  <span className="mobile-typing-badge" aria-label={`${player.character_name} is typing`}>
                    Typing
                  </span>
                ) : null}
              </li>
            )
          })}
        </ul>
      ) : (
        <div className="mobile-presence-empty">{turnControlStatusLabel}</div>
      )}
    </section>
  )
}

export function SessionBoard({
  activeSessionTitle,
  campaignTitle,
  sessionId,
  playerId,
  showSceneMusicPlayer,
  duckMusicForNarration,
  sceneMusicSyncState,
  sceneState,
  onSceneMusicControl,
  contentSettings,
  contentSettingsPending,
  canEditContentSettings,
  onContentRatingChange,
  onContentToneTagsChange,
  onBoardViewModeChange,
  directorCommentary,
  sessionRecap,
  onSpeakSessionRecap,
  workspaceLoading,
  sessionLoading,
  mainTab,
  setMainTab,
  showMobilePresenceStrip,
  activePlayers,
  downloadCampaignChronicle,
  downloadSessionChronicle,
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
  dismissTimelineEntry,
  reportedBadTurnIds,
  reportingBadTurnIds,
  reportBadTurn,
  ratedTurnQualityIds,
  ratingTurnQualityIds,
  submitTurnQuality,
  expandedTurnIds,
  setExpandedTurnIds,
  selectedPlayer,
  currentResponseEntry,
  latestDmText,
  sendPending,
  streamingTurnActive,
  pendingRollNotice,
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
  onStartAdventure,
  actionComposerProps,
}: SessionBoardProps) {
  const loading = workspaceLoading || sessionLoading
  const [chatTextSettings, setChatTextSettings] = useState(loadChatTextSettings)
  const [boardViewMode, setBoardViewMode] = useState<BoardViewMode>(loadBoardViewMode)
  const [chatTextMenuOpen, setChatTextMenuOpen] = useState(false)
  const [directorCommentaryOpen, setDirectorCommentaryOpen] = useState(false)
  const [qualityDrafts, setQualityDrafts] = useState<Record<number, TurnQualityScores>>({})
  const streamLabel =
    currentResponseEntry && turnPersistenceLabel(currentResponseEntry)
      ? turnPersistenceLabel(currentResponseEntry)
      : sendPending || streamingTurnActive ? 'Streaming...' : 'Ready'
  const chatTextClassName = `chat-text-size-${chatTextSettings.size} chat-text-font-${chatTextSettings.font}`
  const theaterMode = boardViewMode === 'theater'
  const rollWaitBanner = pendingRollNotice ? <RollWaitBanner notice={pendingRollNotice} /> : null
  const showStartAdventure =
    Boolean(activeSession) && !loading && turnRows.length === 0 && !currentResponseEntry
  const startAdventureDisabled =
    sendPending || streamingTurnActive || !sessionId || !actionComposerProps.selectedPlayerId
  const previouslyOnText =
    sessionRecap.trim() ||
    sessionState?.rolling_summary?.trim() ||
    activeSession?.latest_summary?.trim() ||
    (activeSession ? 'No recap recorded yet.' : welcomeText)
  const previouslyOnUpdatedAt =
    sessionState?.updated_at ?? activeSession?.latest_activity_at ?? activeSession?.updated_at ?? null

  const updateChatTextSettings = (nextSettings: ChatTextSettings) => {
    setChatTextSettings(nextSettings)
    saveChatTextSettings(nextSettings)
  }

  const updateBoardViewMode = (nextMode: BoardViewMode) => {
    setBoardViewMode(nextMode)
    saveBoardViewMode(nextMode)
  }

  useEffect(() => {
    onBoardViewModeChange?.(boardViewMode)
  }, [boardViewMode, onBoardViewModeChange])

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

  const renderReportButton = (entry: TimelineEntry | null) => {
    if (!entry) return null
    if (!canReportBadTurn(entry)) return null
    const turnId = timelineTurnId(entry) as number
    const reported = reportedBadTurnIds.has(turnId)
    const reporting = reportingBadTurnIds.has(turnId)
    return (
      <button
        type="button"
        className="turn-report"
        aria-label={reported ? 'Bad turn reported' : 'Report bad turn'}
        disabled={reported || reporting}
        onClick={() => reportBadTurn(entry)}
      >
        <ClipboardList size={15} />
      </button>
    )
  }

  const updateQualityDraft = (turnId: number, field: keyof TurnQualityScores, value: number) => {
    setQualityDrafts((current) => ({
      ...current,
      [turnId]: {
        ...(current[turnId] ?? DEFAULT_TURN_QUALITY_SCORES),
        [field]: value,
      },
    }))
  }

  const renderQualityScoreGroup = (
    turnId: number,
    field: keyof TurnQualityScores,
    label: string,
    draft: TurnQualityScores,
  ) => (
    <div className="turn-quality-score-group">
      <span>{label}</span>
      <div>
        {QUALITY_SCORE_OPTIONS.map((score) => (
          <button
            key={`${field}-${score}`}
            type="button"
            aria-label={`${label} ${score}`}
            aria-pressed={draft[field] === score}
            onClick={() => updateQualityDraft(turnId, field, score)}
          >
            {score}
          </button>
        ))}
      </div>
    </div>
  )

  const renderQualityPrompt = (entry: TimelineEntry | null) => {
    if (!entry || !canRateTurnQuality(entry)) return null
    const turnId = timelineTurnId(entry)
    if (turnId === null) return null
    const submitted = ratedTurnQualityIds.has(turnId)
    const submitting = ratingTurnQualityIds.has(turnId)
    const draft = qualityDrafts[turnId] ?? DEFAULT_TURN_QUALITY_SCORES

    if (submitted) {
      return (
        <div className="turn-quality-prompt submitted" role="status">
          <strong>Beta feedback</strong>
          <span>Feedback sent.</span>
        </div>
      )
    }

    return (
      <form
        className="turn-quality-prompt"
        aria-label="Beta turn feedback"
        onSubmit={(event) => {
          event.preventDefault()
          if (!submitting) submitTurnQuality(entry, draft)
        }}
      >
        <strong>Beta feedback</strong>
        {renderQualityScoreGroup(turnId, 'coherence', 'Coherence', draft)}
        {renderQualityScoreGroup(turnId, 'fun', 'Fun', draft)}
        {renderQualityScoreGroup(turnId, 'rules', 'Rules', draft)}
        <button type="submit" disabled={submitting}>
          {submitting ? 'Recording' : 'Record'}
        </button>
      </form>
    )
  }

  const renderTurnCopy = (turn: TimelineEntry, expanded: boolean) => {
    const text = theaterMode && turn.role === 'dm'
      ? turn.text
      : expanded
        ? turn.text
        : truncateText(turn.text, 180)
    if (theaterMode && turn.role === 'dm') {
      return <NarrativeProse text={text} />
    }
    return <p>{text}</p>
  }

  const renderDmResponseCopy = (text: string) => (
    theaterMode ? <NarrativeProse text={text} /> : <p>{text}</p>
  )

  return (
    <main
      className={`session-board session-board-${boardViewMode}`}
      data-scene-mood={sceneState?.musicTag ?? 'calm'}
    >
      <section className="session-header">
        <div>
          <h1>
            {activeSessionTitle}{' '}
            <span className={loading ? 'loading-badge' : ''}>
              {loading ? 'Loading' : 'Live'}
            </span>
          </h1>
          <p>{campaignTitle}</p>
          <SceneHeader sceneState={sceneState} />
        </div>
        <div className="session-actions">
          <ToolbarButton
            icon={theaterMode ? <SlidersHorizontal size={17} /> : <BookOpen size={17} />}
            onClick={() => updateBoardViewMode(theaterMode ? 'ops' : 'theater')}
            title={theaterMode ? 'Operator view' : 'Theater view'}
          >
            {theaterMode ? 'Ops' : 'Theater'}
          </ToolbarButton>
          <ToolbarButton
            icon={<ClipboardList size={17} />}
            onClick={() => setMainTab('notes')}
            title="Summary"
          >
            Summary
          </ToolbarButton>
          <ToolbarButton
            ariaControls="director-commentary-panel"
            ariaExpanded={directorCommentaryOpen}
            icon={<Sparkles size={17} />}
            onClick={() => setDirectorCommentaryOpen((current) => !current)}
            title="Director Commentary"
          >
            Director
          </ToolbarButton>
          <ToolbarButton
            icon={<Download size={17} />}
            onClick={() => void downloadSessionJson()}
            title="Export"
          >
            Export
          </ToolbarButton>
          <ToolbarButton
            disabled={!activeSession}
            icon={<BookOpen size={17} />}
            onClick={() => void downloadSessionChronicle()}
            title="Download session Chronicle"
          >
            Chronicle
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
                <button type="button" role="menuitem" disabled={!activeSession} onClick={() => void downloadSessionChronicle()}>
                  Download session Chronicle
                </button>
                <button type="button" role="menuitem" disabled={!campaign} onClick={() => void downloadCampaignChronicle()}>
                  Download campaign Chronicle
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

      {showMobilePresenceStrip ? (
        <MobilePresenceStrip
          activePlayers={activePlayers}
          selectedPlayerId={actionComposerProps.selectedPlayerId}
          selectedPlayerHasTurn={actionComposerProps.selectedPlayerHasTurn}
          turnControlStatusLabel={actionComposerProps.turnControlStatusLabel}
        />
      ) : null}

      {showSceneMusicPlayer ? (
        <Suspense fallback={null}>
          <SceneMusicPlayer
            sessionId={sessionId}
            playerId={playerId}
            duckForNarration={duckMusicForNarration}
            musicSyncState={sceneMusicSyncState}
            sceneState={sceneState}
            autoFollowScene
            onMusicControl={onSceneMusicControl}
          />
        </Suspense>
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

      <OperatorDrawer
        boardViewMode={boardViewMode}
        canEditContentSettings={canEditContentSettings}
        contentSettings={contentSettings}
        contentSettingsPending={contentSettingsPending}
        dmExecutionStats={dmExecutionStats}
        onBoardViewModeChange={updateBoardViewMode}
        onContentRatingChange={onContentRatingChange}
        onContentToneTagsChange={onContentToneTagsChange}
      />

      {directorCommentaryOpen ? (
        <DirectorCommentaryPanel
          activeSessionTitle={activeSessionTitle}
          canonFacts={canonFacts}
          commentary={directorCommentary}
          contentSettings={contentSettings}
          currentResponseEntry={currentResponseEntry}
          dmExecutionStats={dmExecutionStats}
          latestDmText={latestDmText}
          onClose={() => setDirectorCommentaryOpen(false)}
          questTitle={questTitle}
          sceneState={sceneState}
          sessionState={sessionState}
          streamLabel={streamLabel}
        />
      ) : null}

      {mainTab === 'turns' ? (
        <>
          <section
            className={`turn-feed ${chatTextClassName}`}
            ref={turnFeedRef}
            onScroll={updateJumpToLatestVisibility}
          >
            {rollWaitBanner}
            <PreviouslyOnCard
              text={previouslyOnText}
              updatedAt={previouslyOnUpdatedAt}
              onSpeak={onSpeakSessionRecap}
            />
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
                const dismissible = canDismissLocalTimelineEntry(turn)
                const reportable = canReportBadTurn(turn)
                return (
                  <article className="turn-row" key={turn.id}>
                    <div className="turn-number">{turnNumber(turn, index)}</div>
                    <div
                      className={`turn-card ${expanded ? 'expanded' : ''} ${
                        theaterMode && turn.role === 'dm' ? 'dm-theater-card' : ''
                      }`}
                    >
                      <div className="turn-speaker">
                        <strong>{turn.speaker}</strong>
                        <span>{speakerDetail(turn, selectedPlayer)}</span>
                      </div>
                      {turnPersistenceLabel(turn) ? (
                        <span className="turn-status-label">{turnPersistenceLabel(turn)}</span>
                      ) : null}
                      {renderTurnCopy(turn, expanded)}
                      <time>{formatClock(turn.timestamp)}</time>
                      <div className={`turn-actions ${dismissible ? 'has-dismiss' : ''} ${reportable ? 'has-report' : ''}`}>
                        {renderReportButton(turn)}
                        {dismissible ? (
                          <button
                            type="button"
                            className="turn-dismiss"
                            aria-label="Delete pending message"
                            title="Delete pending message"
                            onClick={() => dismissTimelineEntry(turn.id)}
                          >
                            <Trash2 size={15} />
                          </button>
                        ) : null}
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
                    </div>
                  </article>
                )
              })
            ) : (
              <div className={`empty-state ${showStartAdventure ? 'start-adventure-card' : ''}`}>
                <span>{activeSession ? welcomeText : 'No turn log entries loaded for this session.'}</span>
                {showStartAdventure ? (
                  <button
                    type="button"
                    className="start-adventure-button"
                    onClick={onStartAdventure}
                    disabled={startAdventureDisabled}
                  >
                    <Sparkles size={15} />
                    Start Adventure
                  </button>
                ) : null}
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
                  <div className="dm-response-actions">{renderReportButton(currentResponseEntry)}</div>
                  <div className="response-copy">
                    {renderDmResponseCopy(latestDmText)}
                  </div>
                  {renderQualityPrompt(currentResponseEntry)}
                  <div className={`stream-state ${sendPending || streamingTurnActive ? 'streaming' : ''}`}>
                    <span />
                    {streamLabel}
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
          {rollWaitBanner}
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
              <div className="dm-response-actions">{renderReportButton(currentResponseEntry)}</div>
              <div className="response-copy">
                {renderDmResponseCopy(latestDmText)}
              </div>
              {renderQualityPrompt(currentResponseEntry)}
              <div className={`stream-state ${sendPending || streamingTurnActive ? 'streaming' : ''}`}>
                <span />
                {streamLabel}
              </div>
            </div>
          </article>
        </section>
      ) : null}

      {mainTab === 'notes' ? (
        <section className="turn-feed notes-panel">
          {rollWaitBanner}
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
