import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type RefObject,
  type SetStateAction,
} from 'react'
import type { Socket } from 'socket.io-client'
import {
  ChevronDown,
  ExternalLink,
  Flame,
  Lock,
  Maximize2,
  Menu,
  Minimize2,
  PanelRightOpen,
  Radio,
  Settings,
  Sun,
  UserCircle,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react'
import {
  CampaignActionDialog,
  SessionActionDialog,
} from './CampaignSessionActionDialogs'
import type {
  CampaignArchiveDialogState,
  SessionArchiveDialogState,
} from './ArchiveDialogs'
import { StatusDot, ThinIcon } from './AppChrome'
import { BetaRuntimeNotesPanel } from './BetaRuntimeNotesPanel'
import {
  CAMPAIGN_RAIL_ID,
  CampaignRail,
  type CampaignCard,
  type SessionCard,
} from './CampaignRail'
import type { CampaignPackControlAction } from './CampaignPackPanel'
import {
  INSPECTOR_PANEL_ID,
  InspectorPanel,
  type InspectorTab,
} from './InspectorPanel'
import { PlayerDeleteDialog } from './PlayerDeleteDialog'
import {
  CharacterJoinDialog,
  ProfileSettingsDialog,
} from './ProfileCharacterDialogs'
import { useModalFocusTrap } from './useModalFocusTrap'
import { ApiClientError, WORKSPACE_ID_HEADER, apiFetch, storedRuntimeAccessSnapshot } from './api'
import { actorCapabilitiesAllowOperatorTools } from './capabilities'
import {
  contentSettingsFromSnapshot,
} from './contentSettings'
import {
  SessionBoard,
  type BoardViewMode,
  type MainTab,
  type TurnQualityScores,
} from './SessionBoard'
import {
  abilityOptionsFromStatBlock,
  buildMapMeta,
  buildTimeline,
  recentMemoryFromSnippets,
  formatCompactNumber,
  inventoryCapacity,
  inventoryGoldLabel as buildInventoryGoldLabel,
  inventoryWeightLabel as buildInventoryWeightLabel,
  type InventoryRow,
  isRecord,
  itemOptionsFromInventory,
  memorySnippetRecords,
  normalizeCharacterTraits,
  normalizeInventory,
  normalizeSpellbook,
  normalizeSpellResources,
  normalizeStats,
  normalizeXp,
  numberValue,
  pendingRollNoticeFromTimeline,
  pendingRollOptionsFromTimeline,
  stringValue,
  truncateText,
  turnStatusAllowsNextSend,
  worldStateFromSnapshot,
} from './gameSelectors'
import { diceRollMessage } from './gameActions'
import { gameplayControlsFromSnapshot } from './gameplayControlState'
import { subscribeToMediaQueryChange } from './mediaQuery'
import { profileIconSrcForCharacter } from './profileIcons'
import { BackendTrustDialog, RuntimeSettingsDialog } from './RuntimeSettingsDialog'
import type { SceneDisplayState } from './sceneState'
import type { SceneMusicControlPayload, SceneMusicSyncState } from './SceneMusicPlayer'
import { TitleScreen } from './TitleScreen'
import { turnControlFromSnapshot, turnControlWithActiveName } from './turnControl'
import {
  turnRecoveryGateFromSnapshot,
  type TurnRecoveryResolution,
  type TurnRecoveryResponse,
} from './turnRecovery'
import './App.css'
import type {
  AccountWorkspace,
  ActorCapabilitiesResponse,
  ActivePlayer,
  ActivePlayerHealth,
  ActivePlayerHealthTone,
  BadTurnFeedbackResponse,
  BetaSummary,
  Campaign,
  CampaignPackCommentaryResponse,
  ClarificationRequest,
  CoherenceFeedbackResponse,
  Health,
  LlmRuntimeConfig,
  Player,
  PlayerDetail,
  PlayerEquipmentUpdateResponse,
  SessionRecapResponse,
  SessionState,
  SessionSummary,
  StreamingTurn,
  TimelineEntry,
  TurnControlMode,
  TurnControlSource,
  TtsRuntimeConfig,
  World,
} from './types'
import {
  useCampaignActions,
  type CampaignActionDialogState,
} from './useCampaignActions'
import { useComposerActions } from './useComposerActions'
import { usePlayerProfileActions } from './usePlayerProfileActions'
import { usePlayNowOnboarding } from './usePlayNowOnboarding'
import { useSessionActions, type SessionActionDialogState } from './useSessionActions'
import { useSessionContentSettings } from './useSessionContentSettings'
import { useSessionSocket } from './useSessionSocket'
import { useRuntimeSettings, useShareBackendTrust, type RuntimeAccount } from './useRuntimeSettings'
import { useTtsNarration } from './useTtsNarration'
import { useWorldMapSegmentActions } from './useWorldMapSegmentActions'
import { useWorkspaceQueries, type CampaignSessionMeta } from './useWorkspaceQueries'
import { useWorkspaceStore } from './useWorkspaceStore'
import {
  SavedWorkspaceDeleteDialog,
  ShareSessionDialog,
  type SavedWorkspaceDeleteDialogState,
} from './WorkspaceDialogs'
import { savedWorkspaceDisplayName } from './workspaceLabels'
import {
  WorldDeleteDialog,
  WorldManagerDialog,
} from './WorldDialogs'
import {
  emptyWorldForm,
  type WorldDeleteDialogState,
  type WorldFormState,
} from './worldDialogState'

const DEFAULT_BASE_URL = import.meta.env.VITE_AIDM_API_BASE_URL ?? ''
const COMPACT_LAYOUT_MEDIA_QUERY = '(max-width: 1100px)'
const BOARD_VIEW_MODE_STORAGE_KEY = 'aidm:boardViewMode'

const loadDiceRollDialog = () => import('./DiceRollDialog')
const DiceRollDialog = lazy(loadDiceRollDialog)
const PlayerEditDialog = lazy(() =>
  import('./PlayerEditDialog').then((module) => ({ default: module.PlayerEditDialog })),
)
const CreateCampaignDialog = lazy(() =>
  import('./OperatorTools').then((module) => ({ default: module.CreateCampaignDialog })),
)
const CampaignPackImportDialog = lazy(() =>
  import('./OperatorTools').then((module) => ({ default: module.CampaignPackImportDialog })),
)
const CampaignChooserDialog = lazy(() =>
  import('./CampaignChooserDialog').then((module) => ({ default: module.CampaignChooserDialog })),
)
const CampaignArchiveDialog = lazy(() =>
  import('./OperatorTools').then((module) => ({ default: module.CampaignArchiveDialog })),
)
const SessionArchiveDialog = lazy(() =>
  import('./OperatorTools').then((module) => ({ default: module.SessionArchiveDialog })),
)
function preloadDiceRollDialog() {
  if (import.meta.env.MODE === 'test') return
  void loadDiceRollDialog()
}

function ModalLoading({
  dialogRef,
  label,
}: {
  dialogRef: RefObject<HTMLElement | null>
  label: string
}) {
  return (
    <div className="modal-backdrop" role="presentation">
      <section
        ref={dialogRef}
        className="campaign-dialog modal-loading-dialog"
        role="dialog"
        aria-label={label}
        aria-modal="true"
        aria-busy="true"
      >
        <span className="modal-loading-mark" aria-hidden="true" />
        <strong data-autofocus tabIndex={-1} role="status" aria-live="polite">{label}</strong>
        <p>The requested tools are loading. Your current table state is preserved.</p>
      </section>
    </div>
  )
}

const ACTIVE_PLAYER_HEALTH_LABELS: Record<ActivePlayerHealthTone, string> = {
  uninjured: 'Uninjured',
  wounded: 'Wounded',
  'badly-wounded': 'Badly wounded',
  dead: 'Dead',
}

function normalizedCharacterLookup(value: unknown) {
  return stringValue(value).toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim()
}

function numericSnapshotPlayerIds(record: Record<string, unknown>) {
  const values = [record.playerId, record.player_id, record.playerID, record.id]
  const ids = new Set<number>()
  values.forEach((value) => {
    if (typeof value === 'number' && Number.isInteger(value) && value > 0) {
      ids.add(value)
      return
    }
    const text = stringValue(value)
    if (!text) return
    const exactNumber = Number(text)
    if (Number.isInteger(exactNumber) && exactNumber > 0) {
      ids.add(exactNumber)
      return
    }
    const playerIdMatch = text.match(/^player[_-](\d+)$/i)
    if (playerIdMatch) {
      const parsed = Number(playerIdMatch[1])
      if (Number.isInteger(parsed) && parsed > 0) ids.add(parsed)
    }
  })
  return ids
}

function snapshotPlayerName(record: Record<string, unknown>) {
  return (
    normalizedCharacterLookup(record.character_name) ||
    normalizedCharacterLookup(record.characterName) ||
    normalizedCharacterLookup(record.name)
  )
}

function healthStatusFromSnapshotPlayer(record: Record<string, unknown>): ActivePlayerHealth | null {
  const health = isRecord(record.health) ? record.health : {}
  const stats = isRecord(record.stats) ? record.stats : {}
  const currentHp = numberValue(
    health.currentHp ??
      health.current_hp ??
      health.current ??
      health.hp ??
      health.hitPoints ??
      health.hit_points ??
      record.currentHp ??
      record.current_hp,
  )
  const maxHp = numberValue(
    health.maxHp ??
      health.max_hp ??
      health.max ??
      health.maximum ??
      health.maxHitPoints ??
      health.max_hit_points ??
      record.maxHp ??
      record.max_hp ??
      stats.maxHp ??
      stats.max_hp,
  )
  if (currentHp === null || maxHp === null || maxHp <= 0) return null
  let tone: ActivePlayerHealthTone = 'wounded'
  if (currentHp <= 0) {
    tone = 'dead'
  } else if (currentHp >= maxHp) {
    tone = 'uninjured'
  } else if (currentHp / maxHp <= 0.25) {
    tone = 'badly-wounded'
  }
  return {
    tone,
    label: ACTIVE_PLAYER_HEALTH_LABELS[tone],
    currentHp,
    maxHp,
  }
}

function activePlayersWithSnapshotHealth(activePlayers: ActivePlayer[], snapshot: unknown): ActivePlayer[] {
  if (!activePlayers.length || !isRecord(snapshot) || !Array.isArray(snapshot.playerCharacters)) {
    return activePlayers
  }
  const snapshotPlayers = snapshot.playerCharacters.filter(isRecord)
  if (!snapshotPlayers.length) return activePlayers
  return activePlayers.map((player) => {
    const playerName = normalizedCharacterLookup(player.character_name)
    const snapshotPlayer =
      snapshotPlayers.find((record) => numericSnapshotPlayerIds(record).has(player.id)) ??
      snapshotPlayers.find((record) => Boolean(playerName) && snapshotPlayerName(record) === playerName)
    const health = snapshotPlayer ? healthStatusFromSnapshotPlayer(snapshotPlayer) : null
    if (!health && player.health === undefined) return player
    return { ...player, health }
  })
}

function isCompactLayoutViewport() {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia(COMPACT_LAYOUT_MEDIA_QUERY).matches
  )
}

type ThemeMode = 'dark' | 'light'

function isEditableShortcutTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

type UiErrorCategory = 'connection' | 'tts' | 'validation' | 'persistence' | 'workspace' | 'system'

type UiError = {
  id: string
  category: UiErrorCategory
  message: string
  createdAt: number
}

function readBoardViewMode(): BoardViewMode {
  try {
    return localStorage.getItem(BOARD_VIEW_MODE_STORAGE_KEY) === 'theater' ? 'theater' : 'ops'
  } catch {
    return 'ops'
  }
}

function isUnauthorizedError(error: unknown) {
  return error instanceof ApiClientError && error.status === 401
}

function isNotFoundError(error: unknown) {
  return error instanceof ApiClientError && error.status === 404
}

function isAuthTokenWorkspaceError(error: UiError) {
  return error.category === 'workspace' && error.message.includes('Missing or invalid workspace token')
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

const OWNER_WORKSPACE_ID = 'owner'

function tableStatusDisplayName(account: RuntimeAccount, workspaceId: string) {
  const selectedWorkspaceId = account?.workspaceId || workspaceId
  const selectedWorkspace = account?.workspaces.find(
    (workspace) => workspace.workspace_id === selectedWorkspaceId,
  )
  if (selectedWorkspace) return savedWorkspaceDisplayName(selectedWorkspace)
  return selectedWorkspaceId || 'No table selected'
}

function formatDurationFrom(value: string | null, nowMs: number) {
  if (!value) return 'No session'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return 'No session'
  const seconds = Math.max(0, Math.floor((nowMs - date.getTime()) / 1000))
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const remainingSeconds = seconds % 60
  if (hours > 0) return `${hours}h ${String(minutes).padStart(2, '0')}m`
  return `${minutes}m ${String(remainingSeconds).padStart(2, '0')}s`
}

function SessionDuration({ startedAt }: { startedAt: string | null }) {
  const [nowMs, setNowMs] = useState(() => Date.now())

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  return <>{formatDurationFrom(startedAt, nowMs)}</>
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
    `S${fallbackPrefix ?? '—'}E${session.session_id}`
  )
}

function parsePositiveInt(value: string | null) {
  if (!value) return null
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

type SelectionStorageName = 'selectedCampaignId' | 'selectedSessionId' | 'selectedPlayerId'

function selectionStorageScope(auth: string) {
  const token = auth.trim()
  return token ? `auth:${hashString(token).toString(36)}` : 'open'
}

function selectionStorageKey(scope: string, name: SelectionStorageName) {
  return `aidm:${scope}:${name}`
}

function readInitialSelection(scope: string, name: SelectionStorageName, queryName?: string) {
  const queryValue = queryName ? new URLSearchParams(window.location.search).get(queryName) : null
  const scopedValue = localStorage.getItem(selectionStorageKey(scope, name))
  const legacyValue = scope === 'open' ? localStorage.getItem(`aidm:${name}`) : null
  return parsePositiveInt(queryValue ?? scopedValue ?? legacyValue)
}

function pluralize(value: number, singular: string, plural = `${singular}s`) {
  return `${value} ${value === 1 ? singular : plural}`
}

function focusableElements(container: HTMLElement) {
  const selector = [
    'button:not([disabled])',
    'input:not([disabled])',
    'textarea:not([disabled])',
    'select:not([disabled])',
    'a[href]',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',')
  return Array.from(container.querySelectorAll<HTMLElement>(selector)).filter((element) => {
    if (element.getAttribute('aria-hidden') === 'true') return false
    const style = window.getComputedStyle(element)
    return style.display !== 'none' && style.visibility !== 'hidden'
  })
}

function playerAdventureName(player: Player) {
  return player.character_name?.trim() || player.name?.trim() || `Player ${player.player_id}`
}

function buildStartAdventurePrompt({
  campaign,
  sessionName,
  players,
  sessionState,
}: {
  campaign: Campaign | null
  sessionName: string
  players: Player[]
  sessionState: SessionState | null
}) {
  const playerNames = Array.from(new Set(players.map(playerAdventureName).filter(Boolean)))
  const roster = playerNames.length
    ? `${pluralize(playerNames.length, 'player')} named: ${playerNames.join(', ')}`
    : '0 players named yet'
  const location = sessionState?.current_location || campaign?.location || ''
  const quest = sessionState?.current_quest || campaign?.current_quest || ''
  const context = [
    `Campaign: ${campaign?.title || 'Untitled campaign'}.`,
    `Session: ${sessionName}.`,
    `The table currently has ${roster}.`,
    location ? `Current location: ${location}.` : '',
    quest ? `Current quest: ${quest}.` : '',
  ].filter(Boolean)

  return [
    'Please narrate the opening scene for this campaign.',
    ...context,
    'Start the adventure by telling the players where they are, what they know, what is happening right now, and what immediate choice or prompt is in front of them.',
  ].join(' ')
}

function worldDeleteErrorMessage(error: unknown) {
  if (error instanceof ApiClientError && isRecord(error.payload)) {
    const details = isRecord(error.payload.details) ? error.payload.details : {}
    if (error.payload.error_code === 'world_in_use') {
      const campaigns = Number(details.campaign_count ?? 0)
      const maps = Number(details.map_count ?? 0)
      const npcs = Number(details.npc_count ?? 0)
      const campaignRows = Array.isArray(details.campaigns)
        ? details.campaigns.filter(isRecord)
        : []
      const campaignLabels = campaignRows
        .map((item) => {
          const title = stringValue(item.title) || `Campaign ${item.campaign_id ?? ''}`.trim()
          const status = stringValue(item.status) || 'active'
          return `${title} (${status})`
        })
        .filter(Boolean)
      const blockers = [
        campaigns > 0 ? pluralize(campaigns, 'campaign') : '',
        maps > 0 ? pluralize(maps, 'map') : '',
        npcs > 0 ? pluralize(npcs, 'NPC') : '',
      ].filter(Boolean)
      const blockerText = blockers.length ? blockers.join(', ') : 'saved records'
      const campaignText = campaignLabels.length ? ` Campaigns: ${campaignLabels.join(', ')}.` : ''
      return `World is still used by ${blockerText}.${campaignText}`
    }
    if (typeof error.payload.error === 'string') return error.payload.error
  }
  return error instanceof Error ? error.message : String(error)
}

function providerLabel(value: string) {
  const normalized = value.trim().toLowerCase()
  if (!normalized) return 'Unknown'
  if (normalized === 'nvidia') return 'NVIDIA'
  if (normalized === 'openai') return 'OpenAI'
  if (normalized === 'gemini') return 'Gemini'
  if (normalized === 'kimi') return 'Kimi'
  if (normalized === 'fallback') return 'Fallback'
  return value
}

function hashString(value: string) {
  let hash = 0
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash << 5) - hash + value.charCodeAt(index)
    hash |= 0
  }
  return Math.abs(hash)
}

function avatarDataUri(seed: string, variant: 'campaign' | 'character' = 'campaign') {
  const palettes = [
    ['#4b2d1f', '#f36b2e', '#f4d8a8'],
    ['#172a32', '#78a9d8', '#d6f0ff'],
    ['#2d2117', '#c79752', '#f0d49c'],
    ['#1e2825', '#8bb29e', '#d7e7dc'],
    ['#2b2027', '#b86d82', '#f3cbd4'],
  ]
  const hash = hashString(seed || variant)
  const [base, accent, light] = palettes[hash % palettes.length]
  const angle = 28 + (hash % 46)
  const glyph = variant === 'character' ? 'M' : 'A'
  const svg = `
    <svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">
      <defs>
        <linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stop-color="${light}" stop-opacity=".28"/>
          <stop offset=".42" stop-color="${accent}" stop-opacity=".42"/>
          <stop offset="1" stop-color="${base}"/>
        </linearGradient>
        <filter id="s"><feDropShadow dx="0" dy="4" stdDeviation="5" flood-color="#000" flood-opacity=".34"/></filter>
      </defs>
      <rect width="96" height="96" rx="8" fill="${base}"/>
      <path d="M-10 ${72 - angle} C22 14, 70 16, 106 ${angle}" fill="none" stroke="${accent}" stroke-width="18" stroke-opacity=".34"/>
      <path d="M16 76 L48 12 L80 76 Z" fill="url(#g)" filter="url(#s)"/>
      <path d="M25 68 L48 24 L71 68 Z" fill="none" stroke="${light}" stroke-width="2" stroke-opacity=".36"/>
      <text x="48" y="61" text-anchor="middle" font-family="Inter, Arial" font-size="24" font-weight="500" fill="${light}" opacity=".82">${glyph}</text>
    </svg>
  `
  return `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`
}

function characterPortraitSrc(player: Player) {
  const characterName = player.character_name || player.name || `Player ${player.player_id}`
  return (
    player.profile_image ||
    profileIconSrcForCharacter({
      race: player.race,
      sex: player.sex,
      seed: characterName,
    }) ||
    avatarDataUri(characterName, 'character')
  )
}

function AIDMApp() {
  const [health, setHealth] = useState<Health | null>(null)
  const [actorCapabilities, setActorCapabilities] = useState<ActorCapabilitiesResponse | null>(null)
  const [llmConfig, setLlmConfig] = useState<LlmRuntimeConfig | null>(null)
  const [ttsConfig, setTtsConfig] = useState<TtsRuntimeConfig | null>(null)
  const [ttsConfigLoadFailed, setTtsConfigLoadFailed] = useState(false)
  const [runtimePending, setRuntimePending] = useState(false)
  const [campaignSessionMeta, setCampaignSessionMeta] = useState<
    Record<number, CampaignSessionMeta>
  >({})
  const [metrics, setMetrics] = useState<BetaSummary | null>(null)
  const [socketStatus, setSocketStatus] = useState('idle')
  const [activePlayers, setActivePlayers] = useState<ActivePlayer[]>([])
  const [sceneMusicSyncState, setSceneMusicSyncState] = useState<SceneMusicSyncState | null>(null)
  const [sceneState, setSceneState] = useState<SceneDisplayState | null>(null)
  const [sendPending, setSendPending] = useState(false)
  const [errors, setErrors] = useState<UiError[]>([])
  const [optimisticEntries, setOptimisticEntries] = useState<TimelineEntry[]>([])
  const [streamingTurn, setStreamingTurn] = useState<StreamingTurn | null>(null)
  const [turnStatuses, setTurnStatuses] = useState<Record<number, string>>({})
  const [reportedBadTurnIds, setReportedBadTurnIds] = useState<Set<number>>(() => new Set())
  const [reportingBadTurnIds, setReportingBadTurnIds] = useState<Set<number>>(() => new Set())
  const [ratedTurnQualityIds, setRatedTurnQualityIds] = useState<Set<number>>(() => new Set())
  const [ratingTurnQualityIds, setRatingTurnQualityIds] = useState<Set<number>>(() => new Set())
  const [clarificationRequest, setClarificationRequest] = useState<ClarificationRequest | null>(null)
  const [mainTab, setMainTab] = useState<MainTab>('turns')
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('party')
  const [campaignFilter, setCampaignFilter] = useState('')
  const [expandedTurnIds, setExpandedTurnIds] = useState<Set<string>>(() => new Set())
  const [showJumpToLatest, setShowJumpToLatest] = useState(false)
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false)
  const [accountMenuOpen, setAccountMenuOpen] = useState(false)
  const [betaNotesOpen, setBetaNotesOpen] = useState(false)
  const [railCollapsed, setRailCollapsed] = useState(false)
  const [compactViewport, setCompactViewport] = useState(isCompactLayoutViewport)
  const [mobileRailOpen, setMobileRailOpen] = useState(false)
  const [mobileInspectorOpen, setMobileInspectorOpen] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [fullscreenFallback, setFullscreenFallback] = useState(false)
  const [theme, setTheme] = useState<ThemeMode>(() =>
    localStorage.getItem('aidm:theme') === 'light' ? 'light' : 'dark',
  )
  const [worlds, setWorlds] = useState<World[]>([])
  const [worldManagerOpen, setWorldManagerOpen] = useState(false)
  const [worldForm, setWorldForm] = useState<WorldFormState>(emptyWorldForm)
  const [worldDeleteDialog, setWorldDeleteDialog] = useState<WorldDeleteDialogState>(null)
  const [savedWorkspaceDeleteDialog, setSavedWorkspaceDeleteDialog] =
    useState<SavedWorkspaceDeleteDialogState>(null)
  const [profileSettingsOpen, setProfileSettingsOpen] = useState(false)
  const [campaignArchiveDialog, setCampaignArchiveDialog] =
    useState<CampaignArchiveDialogState>(null)
  const [sessionArchiveDialog, setSessionArchiveDialog] =
    useState<SessionArchiveDialogState>(null)
  const [campaignPackImportOpen, setCampaignPackImportOpen] = useState(false)
  const [campaignPackControlPending, setCampaignPackControlPending] = useState<string | null>(null)
  const [campaignChooserOpen, setCampaignChooserOpen] = useState(false)
  const [campaignChooserDismissedKey, setCampaignChooserDismissedKey] = useState('')
  const [characterJoinDialogOpen, setCharacterJoinDialogOpen] = useState(false)
  const [boardViewMode, setBoardViewMode] = useState<BoardViewMode>(readBoardViewMode)
  const [sessionRecap, setSessionRecap] = useState('')
  const [directorCommentary, setDirectorCommentary] = useState<CampaignPackCommentaryResponse | null>(null)
  const [socketReconnectKey, setSocketReconnectKey] = useState(0)
  const [equipmentPendingItemKey, setEquipmentPendingItemKey] = useState<string | null>(null)
  const [turnRecoveryPending, setTurnRecoveryPending] = useState(false)
  const [turnRecoveryError, setTurnRecoveryError] = useState('')
  const [turnRecoverySuccess, setTurnRecoverySuccess] = useState('')
  const campaignRailToggleRef = useRef<HTMLButtonElement | null>(null)
  const mobileInspectorToggleRef = useRef<HTMLButtonElement | null>(null)
  const mobilePanelReturnFocusRef = useRef<HTMLButtonElement | null>(null)
  const resetRuntimeState = useCallback(() => {
    setHealth(null)
    setActorCapabilities(null)
    setLlmConfig(null)
    setTtsConfig(null)
    setTtsConfigLoadFailed(false)
    setMetrics(null)
    setWorlds([])
  }, [])
  const reconnectSocket = useCallback(() => {
    setSocketReconnectKey((current) => current + 1)
  }, [])
  const closeMobilePanels = useCallback(() => {
    mobilePanelReturnFocusRef.current = null
    setMobileRailOpen(false)
    setMobileInspectorOpen(false)
  }, [])
  const closeMobilePanelsAndRestoreFocus = useCallback(() => {
    const returnFocusTarget = mobilePanelReturnFocusRef.current
    mobilePanelReturnFocusRef.current = null
    setMobileRailOpen(false)
    setMobileInspectorOpen(false)
    window.requestAnimationFrame(() => returnFocusTarget?.focus())
  }, [])
  const {
    adoptAccountSession,
    accountTokenTransport,
    authToken,
    baseUrl,
    clearAuthToken: clearRuntimeAuthToken,
    closeRuntimeSettings,
    openAuthTokenPrompt,
    openRuntimeSettings,
    runtimeAuthIntent,
    runtimeAuthStep,
    runtimeAccount,
    runtimeCreatedWorkspaceToken,
    runtimeWorkspaceAction,
    runtimeWorkspaceCreateAccessMode,
    runtimeWorkspaceJoinMethod,
    legacyPasswordSetupRequired,
    runtimeSettingsError,
    runtimeSettingsForm,
    runtimeSettingsMode,
    runtimeSettingsOpen,
    setRuntimeAuthIntent,
    setRuntimeAuthStep,
    setRuntimeWorkspaceAction,
    setRuntimeWorkspaceCreateAccessMode,
    setRuntimeWorkspaceJoinMethod,
    setLegacyPasswordSetupRequired,
    setRuntimeSettingsError,
    setRuntimeSettingsForm,
    deleteSavedWorkspace,
    selectSavedWorkspace,
    submitRuntimeSettings,
    workspaceId,
    workspaceToken,
  } = useRuntimeSettings({
    defaultBaseUrl: DEFAULT_BASE_URL,
    reconnectSocket,
    resetRuntimeState,
  })
  const rootRef = useRef<HTMLDivElement | null>(null)
  const accountMenuRef = useRef<HTMLDivElement | null>(null)
  const betaNotesToggleRef = useRef<HTMLButtonElement | null>(null)
  const sessionMenuRef = useRef<HTMLDivElement | null>(null)
  const sessionImportInputRef = useRef<HTMLInputElement | null>(null)
  const modalDialogRef = useRef<HTMLElement | null>(null)
  const dialogReturnFocusRef = useRef<HTMLElement | null>(null)
  const promptedCharacterCampaignIdsRef = useRef<Set<number>>(new Set())
  const selectedPlayerByCampaignRef = useRef<Record<number, number>>({})
  const lastSelectedCampaignIdRef = useRef<number | null>(null)
  const actionInputRef = useRef<HTMLTextAreaElement | null>(null)
  const turnFeedRef = useRef<HTMLElement | null>(null)
  const submitActionRef = useRef<(() => void) | null>(null)
  const toggleFullscreenRef = useRef<(() => Promise<void>) | null>(null)
  const socketRef = useRef<Socket | null>(null)
  const closeBetaNotes = useCallback(() => {
    setBetaNotesOpen(false)
    window.requestAnimationFrame(() => betaNotesToggleRef.current?.focus())
  }, [])
  const playerRequestRef = useRef(0)
  const sessionActionDialogRef = useRef<SessionActionDialogState>(null)
  const campaignActionDialogRef = useRef<CampaignActionDialogState>(null)

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return

    const mediaQuery = window.matchMedia(COMPACT_LAYOUT_MEDIA_QUERY)
    const syncCompactViewport = () => {
      const isCompact = mediaQuery.matches
      setCompactViewport(isCompact)
      if (!isCompact) {
        setMobileRailOpen(false)
        setMobileInspectorOpen(false)
      }
    }

    syncCompactViewport()
    return subscribeToMediaQueryChange(mediaQuery, syncCompactViewport)
  }, [])

  useEffect(() => {
    if (!compactViewport) return

    const drawerId = mobileRailOpen
      ? CAMPAIGN_RAIL_ID
      : mobileInspectorOpen
        ? INSPECTOR_PANEL_ID
        : null
    if (!drawerId) return

    const focusTimer = window.setTimeout(() => {
      const drawer = document.getElementById(drawerId)
      const initialControl = drawerId === CAMPAIGN_RAIL_ID
        ? drawer?.querySelector<HTMLElement>('[aria-label="Search campaigns"]')
        : drawer?.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')
      initialControl?.focus()
    }, 200)
    return () => window.clearTimeout(focusTimer)
  }, [compactViewport, mobileInspectorOpen, mobileRailOpen])

  useEffect(() => {
    if (!compactViewport || (!mobileRailOpen && !mobileInspectorOpen)) return

    const drawerId = mobileRailOpen ? CAMPAIGN_RAIL_ID : INSPECTOR_PANEL_ID
    const handleDrawerKeyDown = (event: KeyboardEvent) => {
      if (modalDialogRef.current) return
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        closeMobilePanelsAndRestoreFocus()
        return
      }
      if (event.key !== 'Tab') return
      const drawer = document.getElementById(drawerId)
      if (!drawer) return
      const focusable = focusableElements(drawer)
      if (!focusable.length) {
        event.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && (document.activeElement === first || !drawer.contains(document.activeElement))) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && (document.activeElement === last || !drawer.contains(document.activeElement))) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleDrawerKeyDown)
    return () => document.removeEventListener('keydown', handleDrawerKeyDown)
  }, [closeMobilePanelsAndRestoreFocus, compactViewport, mobileInspectorOpen, mobileRailOpen])

  const auth = runtimeAccount?.requiresPasswordSetup ? '' : authToken.trim()
  const accountSessionAvailable = Boolean(
    auth || accountTokenTransport === 'http_only_cookie',
  )
  const hostedWorkspaceAccessReady = Boolean(
    accountSessionAvailable &&
    runtimeAccount?.workspaceId &&
    workspaceId &&
    runtimeAccount.workspaceId === workspaceId,
  )
  const canUseOwnerRuntimeConfig = Boolean(
    runtimeAccount?.workspaces.some(
      (workspace) => workspace.workspace_id === OWNER_WORKSPACE_ID && workspace.is_workspace_admin,
    ),
  )
  const canQueryActorCapabilities =
    health?.auth_required === true && hostedWorkspaceAccessReady
  const activeActorCapabilities =
    actorCapabilities && actorCapabilities.workspace_id === workspaceId ? actorCapabilities : null
  const canUseOperatorTools = activeActorCapabilities
    ? actorCapabilitiesAllowOperatorTools(activeActorCapabilities.capabilities)
    : health?.auth_required === false || runtimeAccount?.isWorkspaceAdmin === true
  const currentUserIsWorkspaceAdmin = activeActorCapabilities
    ? activeActorCapabilities.is_workspace_admin
    : health?.auth_required === false || runtimeAccount?.isWorkspaceAdmin === true
  const operatorDataEnabled = health?.auth_required === false || Boolean(
    activeActorCapabilities &&
    actorCapabilitiesAllowOperatorTools(activeActorCapabilities.capabilities),
  )
  const runtimeConfigHeaders = useMemo<HeadersInit | undefined>(
    () => (canUseOwnerRuntimeConfig ? { [WORKSPACE_ID_HEADER]: OWNER_WORKSPACE_ID } : undefined),
    [canUseOwnerRuntimeConfig],
  )
  useEffect(() => {
    if (!canQueryActorCapabilities) {
      setActorCapabilities(null)
      return undefined
    }

    let cancelled = false
    setActorCapabilities(null)
    apiFetch<ActorCapabilitiesResponse>(baseUrl, '/api/capabilities', auth)
      .then((payload) => {
        if (!cancelled) setActorCapabilities(payload)
      })
      .catch(() => {
        if (!cancelled) setActorCapabilities(null)
      })
    return () => {
      cancelled = true
    }
  }, [auth, baseUrl, canQueryActorCapabilities, runtimeAccount?.workspaceId, workspaceId, workspaceToken])
  const storedSelectionScope = selectionStorageScope(auth)
  const {
    campaigns,
    campaign,
    sessions,
    players,
    maps,
    segments,
    selectedCampaignId,
    setSelectedCampaignId,
    selectedSessionId,
    setSelectedSessionId,
    selectedPlayerId,
    setSelectedPlayerId,
    playerDetail,
    setPlayerDetail,
    sessionState,
    setSessionState,
    logEntries,
    setLogEntries,
    sessionLogCursor,
    setSessionLogCursor,
    sessionLogHasMore,
    setSessionLogHasMore,
    workspaceLoading,
    setWorkspaceLoading,
    loadingCampaignId,
    setLoadingCampaignId,
    sessionLoading,
    setSessionLoading,
    rootCampaignsLoaded,
    campaignWorkspaceLoaded,
    campaignUpserted,
    campaignRemoved,
    sessionUpserted,
    playerUpserted,
  } = useWorkspaceStore({
    selectedCampaignId: readInitialSelection(storedSelectionScope, 'selectedCampaignId', 'campaign'),
    selectedSessionId: readInitialSelection(storedSelectionScope, 'selectedSessionId', 'session'),
    selectedPlayerId: readInitialSelection(storedSelectionScope, 'selectedPlayerId'),
  })
  const viewerMaps = useMemo(
    () => (canUseOperatorTools ? maps : maps.filter((map) => map.visibility === 'player')),
    [canUseOperatorTools, maps],
  )
  const pushError = useCallback((category: UiErrorCategory, message: string) => {
    const createdAt = Date.now()
    setErrors((current) => {
      const duplicate = current.find(
        (item) =>
          item.category === category &&
          item.message === message &&
          createdAt - item.createdAt < 15_000,
      )
      if (duplicate) return current
      return [
        {
          id: `${createdAt}-${Math.random().toString(36).slice(2, 8)}`,
          category,
          message,
          createdAt,
        },
        ...current.slice(0, 7),
      ]
    })
  }, [])
  const clearAuthTokenErrors = useCallback(() => {
    setErrors((current) =>
      current.filter((item) => item.category !== 'connection' && !isAuthTokenWorkspaceError(item)),
    )
  }, [])
  const loadPlayerDetail = useCallback(async (playerId: number) => {
    const requestId = ++playerRequestRef.current
    const requestAuth = auth
    const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
    await apiFetch<PlayerDetail>(baseUrl, `/api/players/${playerId}`, requestAuth)
      .then((detail) => {
        if (playerRequestRef.current === requestId) {
          setPlayerDetail(detail)
          clearAuthTokenErrors()
        }
      })
      .catch((error: unknown) => {
        if (playerRequestRef.current === requestId) {
          setPlayerDetail(null)
          if (isUnauthorizedError(error)) {
            if (requestAccessSnapshot !== storedRuntimeAccessSnapshot()) return
            openAuthTokenPrompt()
            clearAuthTokenErrors()
            return
          }
          if (isNotFoundError(error)) {
            setSelectedPlayerId((current) => (current === playerId ? null : current))
            return
          }
          pushError('workspace', `Player load failed: ${error instanceof Error ? error.message : String(error)}`)
        }
      })
  }, [
    auth,
    baseUrl,
    clearAuthTokenErrors,
    openAuthTokenPrompt,
    pushError,
    setPlayerDetail,
    setSelectedPlayerId,
  ])
  const clearResolvedOperationalErrors = useCallback(() => {
    setErrors((current) =>
      current.filter((item) => {
        if (
          item.category === 'connection' &&
          (item.message.startsWith('Socket connection failed:') ||
            item.message === 'Realtime is reconnecting. Try again in a moment.')
        ) {
          return false
        }
        if (
          item.category === 'workspace' &&
          (item.message.startsWith('Workspace load failed:') ||
            item.message.startsWith('Session refresh failed:') ||
            item.message.startsWith('Player load failed:'))
        ) {
          return false
        }
        return true
      }),
    )
  }, [])
  useEffect(() => {
    if (health?.status !== 'ok') return
    clearAuthTokenErrors()
  }, [clearAuthTokenErrors, health?.status])
  const selectedPlayer = useMemo(
    () =>
      players.find(
        (player) =>
          player.player_id === selectedPlayerId &&
          player.campaign_id === selectedCampaignId,
      ) ?? null,
    [players, selectedCampaignId, selectedPlayerId],
  )
  const selectedPlayerMatchesDetail =
    !!selectedPlayer && playerDetail?.player_id === selectedPlayer.player_id
  const selectedPlayerLevel = selectedPlayerMatchesDetail
    ? playerDetail.level
    : selectedPlayer?.level ?? null
  useEffect(() => {
    if (lastSelectedCampaignIdRef.current !== selectedCampaignId) {
      lastSelectedCampaignIdRef.current = selectedCampaignId
      if (!selectedCampaignId) return

      const rememberedPlayerId = selectedPlayerByCampaignRef.current[selectedCampaignId]
      if (!rememberedPlayerId) return

      const rememberedPlayerAvailable = players.some(
        (player) => player.player_id === rememberedPlayerId,
      )
      if (!rememberedPlayerAvailable) {
        delete selectedPlayerByCampaignRef.current[selectedCampaignId]
        return
      }
      if (rememberedPlayerId !== selectedPlayerId) {
        setSelectedPlayerId(rememberedPlayerId)
      }
      return
    }

    if (selectedCampaignId && selectedPlayerId && selectedPlayer) {
      selectedPlayerByCampaignRef.current[selectedCampaignId] = selectedPlayerId
    }
  }, [players, selectedCampaignId, selectedPlayer, selectedPlayerId, setSelectedPlayerId])
  const statBlock = useMemo(
    () => normalizeStats(
      playerDetail?.stats,
      playerDetail?.character_sheet,
      selectedPlayerLevel,
      playerDetail?.derived,
    ),
    [
      playerDetail?.character_sheet,
      playerDetail?.derived,
      playerDetail?.stats,
      selectedPlayerLevel,
    ],
  )
  const inventoryRows = useMemo(
    () => normalizeInventory(playerDetail?.inventory),
    [playerDetail?.inventory],
  )
  const spellbook = useMemo(
    () => normalizeSpellbook(playerDetail?.stats, playerDetail?.character_sheet),
    [playerDetail?.character_sheet, playerDetail?.stats],
  )
  const spellResources = useMemo(
    () => normalizeSpellResources(playerDetail?.character_sheet, playerDetail?.stats),
    [playerDetail?.character_sheet, playerDetail?.stats],
  )
  const characterTraits = useMemo(
    () => normalizeCharacterTraits(playerDetail?.race_selection, playerDetail?.character_sheet),
    [playerDetail?.character_sheet, playerDetail?.race_selection],
  )
  const abilityOptions = useMemo(() => abilityOptionsFromStatBlock(statBlock), [statBlock])
  const itemOptions = useMemo(() => itemOptionsFromInventory(inventoryRows), [inventoryRows])
  const campaignWorldId = campaign?.world_id ?? campaigns[0]?.world_id ?? null
  const worldSelectOptions = useMemo(() => {
    const options = new Map<number, World>()
    worlds.forEach((world) => options.set(world.world_id, world))
    if (campaignWorldId && !options.has(campaignWorldId)) {
      options.set(campaignWorldId, {
        world_id: campaignWorldId,
        name: `World ${campaignWorldId}`,
        description: null,
        created_at: null,
      })
    }
    return [...options.values()].sort((left, right) => left.name.localeCompare(right.name))
  }, [campaignWorldId, worlds])

  const timeline = useMemo(
    () => buildTimeline({ logEntries, optimisticEntries, streamingTurn, turnStatuses }),
    [logEntries, optimisticEntries, streamingTurn, turnStatuses],
  )
  const streamingTurnStatus = streamingTurn ? turnStatuses[streamingTurn.turnId] : ''
  const dmResponseBlocking = Boolean(streamingTurn && !turnStatusAllowsNextSend(streamingTurnStatus))
  const pendingRollOptions = useMemo(() => pendingRollOptionsFromTimeline(timeline), [timeline])
  const turnControlSnapshot = isRecord(sessionState?.state_snapshot) ? sessionState.state_snapshot : null
  const activePlayersWithHealth = useMemo(
    () => activePlayersWithSnapshotHealth(activePlayers, turnControlSnapshot),
    [activePlayers, turnControlSnapshot],
  )
  const turnControl = useMemo(
    () => turnControlWithActiveName(turnControlFromSnapshot(turnControlSnapshot), activePlayers),
    [activePlayers, turnControlSnapshot],
  )

  const activeSession =
    sessions.find(
      (session) =>
        session.session_id === selectedSessionId &&
        session.campaign_id === selectedCampaignId,
    ) ?? null
  const activeSessionId = activeSession?.session_id ?? null
  const selectedPlayerDetailId = selectedPlayer?.player_id ?? null
  const pendingRollNotice = useMemo(
    () => pendingRollNoticeFromTimeline(timeline, players, selectedPlayerDetailId),
    [players, selectedPlayerDetailId, timeline],
  )
  const sceneMusicWorkspaceReady =
    health?.auth_required === false || hostedWorkspaceAccessReady
  const showSceneMusicPlayer =
    Boolean(activeSessionId && selectedPlayerDetailId && sceneMusicWorkspaceReady) &&
    !(runtimeSettingsOpen && runtimeSettingsMode === 'auth')
  const socketCampaignId = activeSessionId && selectedPlayerDetailId ? selectedCampaignId : null
  const activeSessionName = activeSession
    ? sessionDisplayName(activeSession, campaign?.world_id ?? selectedCampaignId)
    : 'No session selected'
  const latestDmEntry =
    [...timeline].reverse().find((entry) => entry.role === 'dm') ?? null
  const latestTimelineEntry = timeline.length ? timeline[timeline.length - 1] : null
  const currentResponseEntry =
    latestTimelineEntry?.streaming || latestTimelineEntry?.role === 'dm'
      ? latestTimelineEntry
      : null
  const turnRows = currentResponseEntry
    ? timeline.filter((entry) => entry.id !== currentResponseEntry.id)
    : timeline
  const turnRowCount = turnRows.length
  const speakableDmEntry =
    currentResponseEntry?.role === 'dm' && !currentResponseEntry.streaming
      ? currentResponseEntry
      : null
  const welcomeText = activeSession
    ? `Welcome to ${activeSessionName}. Choose an opening move and the DM will begin the scene.`
    : 'Start or select a session to begin play.'

  const latestPlayError = errors.find((error) =>
    (error.category === 'connection' && (
      error.message.startsWith('Socket connection failed:') ||
      error.message === 'Realtime is reconnecting. Try again in a moment.'
    )) ||
    (error.category === 'workspace' && (
      error.message.startsWith('Workspace load failed:') ||
      error.message.startsWith('Session refresh failed:') ||
      error.message.startsWith('Player load failed:')
    )),
  ) ?? null

  const latestDmText =
    currentResponseEntry?.text ||
    latestDmEntry?.text ||
    sessionState?.rolling_summary ||
    welcomeText

  useEffect(() => {
    setReportedBadTurnIds(new Set())
    setReportingBadTurnIds(new Set())
    setRatedTurnQualityIds(new Set())
    setRatingTurnQualityIds(new Set())
  }, [activeSessionId])

  const reportBadTurn = useCallback(
    async (entry: TimelineEntry) => {
      const turnId = numberValue(entry.metadata.turn_id)
      if (!activeSessionId || turnId === null) {
        pushError('validation', 'Choose a saved DM turn before reporting it.')
        return
      }
      if (reportedBadTurnIds.has(turnId) || reportingBadTurnIds.has(turnId)) return
      setReportingBadTurnIds((current) => new Set(current).add(turnId))
      try {
        await apiFetch<BadTurnFeedbackResponse>(
          baseUrl,
          '/api/feedback/bad-turn',
          auth,
          {
            method: 'POST',
            body: JSON.stringify({
              session_id: activeSessionId,
              turn_id: turnId,
              category: 'other',
            }),
          },
        )
        setReportedBadTurnIds((current) => new Set(current).add(turnId))
      } catch (error) {
        pushError('persistence', `Could not report turn: ${error instanceof Error ? error.message : String(error)}`)
      } finally {
        setReportingBadTurnIds((current) => {
          const next = new Set(current)
          next.delete(turnId)
          return next
        })
      }
    },
    [activeSessionId, auth, baseUrl, pushError, reportedBadTurnIds, reportingBadTurnIds],
  )

  const submitTurnQuality = useCallback(
    async (entry: TimelineEntry, scores: TurnQualityScores) => {
      const turnId = numberValue(entry.metadata.turn_id)
      if (!activeSessionId || turnId === null) {
        pushError('validation', 'Choose a saved DM turn before sending beta feedback.')
        return
      }
      if (ratedTurnQualityIds.has(turnId) || ratingTurnQualityIds.has(turnId)) return
      setRatingTurnQualityIds((current) => new Set(current).add(turnId))
      try {
        await apiFetch<CoherenceFeedbackResponse>(
          baseUrl,
          '/api/feedback/coherence',
          auth,
          {
            method: 'POST',
            body: JSON.stringify({
              session_id: activeSessionId,
              turn_id: turnId,
              coherence_score: scores.coherence,
              category: 'beta_turn_prompt',
              fun_score: scores.fun,
              rules_score: scores.rules,
            }),
          },
        )
        setRatedTurnQualityIds((current) => new Set(current).add(turnId))
      } catch (error) {
        pushError('persistence', `Could not send beta feedback: ${error instanceof Error ? error.message : String(error)}`)
      } finally {
        setRatingTurnQualityIds((current) => {
          const next = new Set(current)
          next.delete(turnId)
          return next
        })
      }
    },
    [activeSessionId, auth, baseUrl, pushError, ratedTurnQualityIds, ratingTurnQualityIds],
  )

  const {
    ttsEnabled,
    ttsSpeaking,
    effectiveTtsStatus,
    ttsStatusLabel,
    ttsLatencyLabel,
    canStopTts,
    stopTtsAudio,
    toggleTts,
    resetTtsFailureForNextResponse,
    rememberStreamedTtsTurn,
    spokenTextLengthRef,
    speakableStreamingTextRef,
    queueTtsNarrationRef,
    ttsEnabledRef,
    ttsQueueSuppressedRef,
    ttsFailureReportedRef,
    ttsPartialFlushTimerRef,
    lastSpokenDmEntryRef,
    lastSpokenTurnIdRef,
    lastSpokenTextRef,
  } = useTtsNarration({
    auth,
    baseUrl,
    ttsConfig,
    ttsConfigLoadFailed,
    selectedSessionId,
    sendPending,
    streamingTurn,
    speakableDmEntry,
    pushError,
  })

  const {
    actionText,
    adminPasscode,
    adminToolsUnlocked,
    applyComposerMode,
    closeDiceRoll,
    completeDiceRoll,
    composerMode,
    diceRoll,
    handleConnectionInterrupted,
    handleRollRequired,
    handleRollResolved,
    handleTurnDuplicate,
    interactionTargets,
    rollMode,
    rollReason,
    rollTargetPendingTurnId,
    spellName,
    selectedAbility,
    selectedAbilityKey,
    selectedDie,
    selectedInteractionTarget,
    selectedInteractionTargetId,
    selectedInteractionType,
    selectedInventoryAction,
    selectedItem,
    itemDraftName,
    itemQuantity,
    itemCostGold,
    queuedActionText,
    queuedActionRetryable,
    preparePendingRoll,
    retryRecoverableSubmission,
    retryDiceRoll,
    sharedRollNotice,
    setActionText,
    updateActionText,
    setAdminPasscode,
    setSelectedInteractionTargetId,
    setSelectedInteractionType,
    setItemQuantity,
    setRollMode,
    setRollReason,
    setRollTargetPendingTurnId,
    setSelectedItemId,
    updateRollAbilityKey,
    updateSpellName,
    updateSelectedInventoryAction,
    updateItemDraftName,
    updateItemCostGold,
    startDiceRoll,
    submitAction,
    toggleAdminTools,
    clearQueuedAction,
    selectedPlayerHasTurn,
    turnControlStatusLabel,
    updateSelectedDie,
  } = useComposerActions({
    activePlayers,
    abilityOptions,
    campaign,
    itemOptions,
    pendingRollOptions,
    sessionState,
    selectedCampaignId,
    selectedPlayer,
    selectedPlayerId: selectedPlayerDetailId,
    selectedSessionId: activeSessionId,
    sendPending,
    dmResponseBlocking,
    streamingTurn,
    setOptimisticEntries,
    setSendPending,
    setStreamingTurn,
    socketRef,
    stopTtsAudio,
    turnControl,
    pushError,
  })
  const startAdventure = useCallback(() => {
    submitAction(
      buildStartAdventurePrompt({
        campaign,
        sessionName: activeSessionName,
        players,
        sessionState,
      }),
    )
  }, [activeSessionName, campaign, players, sessionState, submitAction])

  const updateTurnControl = useCallback(
    (mode: TurnControlMode, activePlayerId?: number | null, source: TurnControlSource = 'manual') => {
      if (!activeSessionId || !selectedPlayerDetailId) {
        pushError('validation', 'Choose a session and player before changing turn mode.')
        return
      }
      const socket = socketRef.current
      if (!socket || socket.connected === false) {
        pushError('connection', 'Realtime is reconnecting. Try again in a moment.')
        return
      }
      const nextActivePlayerId = mode === 'free' ? null : activePlayerId ?? turnControl.activePlayerId ?? selectedPlayerDetailId
      socket.emit('set_turn_control', {
        session_id: activeSessionId,
        player_id: selectedPlayerDetailId,
        mode,
        source,
        active_player_id: nextActivePlayerId,
      })
    },
    [activeSessionId, pushError, selectedPlayerDetailId, socketRef, turnControl.activePlayerId],
  )

  const campaignTitle = campaign?.title ?? 'No campaign selected'
  const activeSessionTitle = activeSession
    ? sessionDisplayName(activeSession, campaign?.world_id ?? selectedCampaignId)
    : selectedCampaignId
      ? 'No session selected'
      : 'Select a campaign'
  const realtimeLabel =
    socketStatus === 'joined'
      ? 'Joined'
      : socketStatus === 'connecting' || socketStatus === 'joining'
        ? 'Connecting'
        : socketStatus === 'error'
          ? 'Error'
          : socketStatus === 'offline'
            ? 'Offline'
            : health?.status === 'ok'
              ? 'Standby'
              : 'Offline'
  const realtimeTone: 'good' | 'neutral' | 'warn' =
    realtimeLabel === 'Joined'
      ? 'good'
      : realtimeLabel === 'Error' || realtimeLabel === 'Offline'
        ? 'warn'
        : 'neutral'
  const handleWorkspaceUnauthorized = useCallback(() => {
    // Hosted first-run visitors should land on the welcome screen and choose
    // Log In or Create Account themselves. Existing sessions still reopen the
    // access dialog when their saved credentials expire.
    if (!accountSessionAvailable && !runtimeAccount?.workspaceId) return
    openAuthTokenPrompt()
  }, [accountSessionAvailable, openAuthTokenPrompt, runtimeAccount?.workspaceId])

  const {
    clearSessionData,
    loadOlderSessionLog,
    loadSessionData,
    olderLogLoading,
    refreshCampaignWorkspace,
    refreshCurrentWorkspace,
    refreshRoot,
  } = useWorkspaceQueries({
    auth,
    baseUrl,
    operatorDataEnabled,
    runtimeConfigHeaders,
    sessions,
    selectedCampaignId,
    selectedSessionId,
    sessionLogCursor,
    sessionLogHasMore,
    setHealth,
    setMetrics,
    setLlmConfig,
    setTtsConfig,
    setTtsConfigLoadFailed,
    setWorlds,
    setCampaignSessionMeta,
    setSelectedCampaignId,
    setSelectedSessionId,
    setSelectedPlayerId,
    setSessionState,
    setLogEntries,
    setSessionLogCursor,
    setSessionLogHasMore,
    setWorkspaceLoading,
    setLoadingCampaignId,
    setSessionLoading,
    rootCampaignsLoaded,
    campaignWorkspaceLoaded,
    setOptimisticEntries,
    setStreamingTurn,
    setSendPending,
    pushError,
    onUnauthorized: handleWorkspaceUnauthorized,
  })

  const {
    activateSegment,
    createDefaultMap,
    createMapPending,
    createPlayerPending,
    createSegment,
    deleteSegment,
    mapManagementForm,
    mapSavePending,
    saveMapManagement,
    segmentDeletePendingId,
    segmentManagementForm,
    segmentSavePending,
    setMapManagementForm,
    setSegmentManagementForm,
  } = useWorldMapSegmentActions({
    auth,
    baseUrl,
    campaign,
    maps: viewerMaps,
    selectedCampaignId,
    refreshCampaignWorkspace,
    setSelectedPlayerId,
    setInspectorTab,
    pushError,
  })

  const updateJumpToLatestVisibility = useCallback(() => {
    const feed = turnFeedRef.current
    if (!feed) return
    const distanceFromBottom = feed.scrollHeight - feed.scrollTop - feed.clientHeight
    setShowJumpToLatest(distanceFromBottom > 96)
  }, [])

  const scrollTurnFeedToLatest = useCallback(() => {
    const feed = turnFeedRef.current
    if (!feed) return
    feed.scrollTo({ top: feed.scrollHeight, behavior: 'smooth' })
    setShowJumpToLatest(false)
  }, [])

  const dismissTimelineEntry = useCallback((turnId: string) => {
    setOptimisticEntries((current) => current.filter((entry) => entry.id !== turnId))
    setExpandedTurnIds((current) => {
      if (!current.has(turnId)) return current
      const next = new Set(current)
      next.delete(turnId)
      return next
    })
  }, [])

  useEffect(() => {
    setShowJumpToLatest(false)
  }, [mainTab, selectedSessionId])

  useEffect(() => {
    if (mainTab !== 'turns' || showJumpToLatest) return
    const frame = window.requestAnimationFrame(() => {
      const feed = turnFeedRef.current
      if (feed) {
        feed.scrollTop = feed.scrollHeight
      }
    })
    return () => window.cancelAnimationFrame(frame)
  }, [latestDmText, mainTab, showJumpToLatest, timeline.length])

  const toggleFullscreen = async () => {
    try {
      if (fullscreenFallback) {
        setFullscreenFallback(false)
        return
      }
      if (document.fullscreenElement) {
        await document.exitFullscreen()
        return
      }
      await rootRef.current?.requestFullscreen()
    } catch {
      setFullscreenFallback(true)
      pushError('system', 'Native fullscreen was blocked by this browser, so app fullscreen mode is active.')
    }
  }

  const rememberDialogTrigger = useCallback((fallback?: HTMLElement | null) => {
    if (fallback) {
      dialogReturnFocusRef.current = fallback
      return
    }
    const activeElement = document.activeElement
    dialogReturnFocusRef.current =
      activeElement instanceof HTMLElement && activeElement !== document.body
        ? activeElement
        : fallback ?? null
  }, [])

  const {
    closeShareSessionDialog,
    closeSessionActionDialog,
    copyShareSessionUrl,
    downloadCampaignChronicle,
    downloadSessionChronicle,
    downloadSessionJson,
    importSessionJson,
    openDeleteSessionDialog,
    openRenameSessionDialog,
    sessionActionDialog,
    sessionImportPending,
    setSessionActionDialog,
    shareSession,
    shareSessionUrl,
    startSession,
    submitSessionActionDialog,
  } = useSessionActions({
    auth,
    baseUrl,
    campaign,
    activeSession,
    sessionDisplayFallback: campaign?.world_id ?? selectedCampaignId,
    selectedCampaignId,
    selectedSessionId,
    selectedPlayerId,
    players,
    selectedPlayer,
    playerDetail,
    sessionState,
    logEntries,
    maps: viewerMaps,
    segments,
    metrics,
    rememberDialogTrigger,
    sessionMenuButton: () =>
      sessionMenuRef.current?.querySelector<HTMLElement>('button[aria-label="Session menu"]') ?? null,
    sessionDisplayName,
    loadSessionData,
    refreshRoot,
    refreshCampaignWorkspace,
    sessionUpserted,
    setSelectedCampaignId,
    setSelectedSessionId,
    setLogEntries,
    setSessionState,
    setOptimisticEntries,
    setStreamingTurn,
    setMainTab,
    setSessionMenuOpen,
    pushError,
  })

  const {
    campaignActionDialog,
    closeCampaignActionDialog,
    closeCreateCampaignDialog,
    createCampaignError,
    createCampaignForm,
    createCampaignPackOptions,
    createCampaignPackOptionsPending,
    createCampaignOpen,
    createCampaignPending,
    openCreateCampaignDialog,
    openDeleteCampaignDialog,
    openRenameCampaignDialog,
    setCampaignActionDialog,
    setCreateCampaignForm,
    submitCampaignActionDialog,
    submitCreateCampaign,
  } = useCampaignActions({
    auth,
    baseUrl,
    campaign,
    selectedCampaignId,
    defaultWorldId: campaignWorldId,
    rememberDialogTrigger,
    refreshRoot,
    refreshCampaignWorkspace,
    campaignUpserted,
    campaignRemoved,
    setSelectedCampaignId,
    setSelectedSessionId,
    setLogEntries,
    setSessionState,
    setOptimisticEntries,
    setStreamingTurn,
    setMainTab,
    setInspectorTab,
    pushError,
  })
  const openCampaignPackImportDialog = useCallback(() => {
    rememberDialogTrigger()
    setCampaignPackImportOpen(true)
  }, [rememberDialogTrigger])

  const closeCampaignPackImportDialog = useCallback(() => {
    setCampaignPackImportOpen(false)
  }, [])

  const handleCampaignPackImported = useCallback(
    async (campaignId: number, sessionId: number) => {
      setCampaignPackImportOpen(false)
      setSelectedSessionId(null)
      setLogEntries([])
      setSessionState(null)
      setOptimisticEntries([])
      setStreamingTurn(null)
      setMainTab('turns')
      setInspectorTab('map')
      await refreshRoot()
      setSelectedCampaignId(campaignId)
      await refreshCampaignWorkspace(campaignId)
      setSelectedSessionId(sessionId)
      await loadSessionData(sessionId)
    },
    [
      loadSessionData,
      refreshCampaignWorkspace,
      refreshRoot,
      setInspectorTab,
      setLogEntries,
      setMainTab,
      setOptimisticEntries,
      setSelectedCampaignId,
      setSelectedSessionId,
      setSessionState,
      setStreamingTurn,
    ],
  )

  const controlCampaignPackProgress = useCallback(
    async (
      action: CampaignPackControlAction,
      checkpointId?: string | null,
      reason?: string,
    ) => {
      if (!activeSessionId) {
        pushError('validation', 'Choose a campaign-pack session before changing checkpoints.')
        return
      }
      setCampaignPackControlPending(action)
      try {
        await apiFetch(
          baseUrl,
          `/api/sessions/${activeSessionId}/campaign-pack/progress`,
          auth,
          {
            method: 'POST',
            body: JSON.stringify({
              action,
              checkpointId: checkpointId || undefined,
              reason,
            }),
          },
        )
        await loadSessionData(activeSessionId)
        if (selectedCampaignId) {
          await refreshCampaignWorkspace(selectedCampaignId)
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        pushError('persistence', `Campaign pack checkpoint update failed: ${message}`)
      } finally {
        setCampaignPackControlPending(null)
      }
    },
    [
      activeSessionId,
      auth,
      baseUrl,
      loadSessionData,
      pushError,
      refreshCampaignWorkspace,
      selectedCampaignId,
    ],
  )

  const loadArchivedCampaigns = useCallback(async () => {
    setCampaignArchiveDialog((current) => ({
      items: current?.items ?? [],
      loading: true,
      error: '',
      pendingId: null,
    }))
    try {
      const allCampaigns = await apiFetch<Campaign[]>(
        baseUrl,
        '/api/campaigns?include_archived=true',
        auth,
      )
      setCampaignArchiveDialog({
        items: allCampaigns.filter((item) => item.is_archived),
        loading: false,
        error: '',
        pendingId: null,
      })
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setCampaignArchiveDialog((current) => ({
        items: current?.items ?? [],
        loading: false,
        error: message,
        pendingId: null,
      }))
      pushError('persistence', `Could not load campaign archive: ${message}`)
    }
  }, [auth, baseUrl, pushError])

  const openCampaignArchiveManager = useCallback(() => {
    rememberDialogTrigger()
    void loadArchivedCampaigns()
  }, [loadArchivedCampaigns, rememberDialogTrigger])

  const closeCampaignArchiveDialog = useCallback(() => {
    if (campaignArchiveDialog?.pendingId) return
    setCampaignArchiveDialog(null)
  }, [campaignArchiveDialog?.pendingId])

  const archiveSelectedCampaignFromManager = useCallback(async () => {
    if (!campaign || !selectedCampaignId) {
      setCampaignArchiveDialog((current) =>
        current
          ? { ...current, error: 'Select an active campaign before archiving.' }
          : current,
      )
      return
    }
    const campaignId = campaign.campaign_id
    setCampaignArchiveDialog((current) =>
      current ? { ...current, pendingId: campaignId, error: '' } : current,
    )
    try {
      await apiFetch<{ deleted: boolean; archived?: boolean }>(
        baseUrl,
        `/api/campaigns/${campaignId}`,
        auth,
        { method: 'DELETE' },
      )
      setSelectedCampaignId(null)
      setSelectedSessionId(null)
      campaignRemoved(campaignId)
      setLogEntries([])
      setSessionState(null)
      setOptimisticEntries([])
      setStreamingTurn(null)
      await refreshRoot()
      await loadArchivedCampaigns()
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setCampaignArchiveDialog((current) =>
        current ? { ...current, pendingId: null, error: message } : current,
      )
      pushError('persistence', `Could not archive campaign: ${message}`)
    }
  }, [
    auth,
    baseUrl,
    campaign,
    campaignRemoved,
    loadArchivedCampaigns,
    pushError,
    refreshRoot,
    selectedCampaignId,
    setLogEntries,
    setOptimisticEntries,
    setSelectedCampaignId,
    setSelectedSessionId,
    setSessionState,
    setStreamingTurn,
  ])

  const restoreCampaignFromArchive = useCallback(
    async (campaignId: number) => {
      setCampaignArchiveDialog((current) =>
        current ? { ...current, pendingId: campaignId, error: '' } : current,
      )
      try {
        const response = await apiFetch<{ restored: boolean; campaign: Campaign }>(
          baseUrl,
          `/api/campaigns/${campaignId}/restore`,
          auth,
          { method: 'POST' },
        )
        campaignUpserted(response.campaign)
        await refreshRoot()
        setSelectedCampaignId(response.campaign.campaign_id)
        await loadArchivedCampaigns()
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        setCampaignArchiveDialog((current) =>
          current ? { ...current, pendingId: null, error: message } : current,
        )
        pushError('persistence', `Could not restore campaign: ${message}`)
      }
    },
    [
      auth,
      baseUrl,
      campaignUpserted,
      loadArchivedCampaigns,
      pushError,
      refreshRoot,
      setSelectedCampaignId,
    ],
  )

  const loadArchivedSessions = useCallback(
    async (campaignId = selectedCampaignId) => {
      setSessionArchiveDialog((current) => ({
        items: current?.items ?? [],
        loading: true,
        error: '',
        pendingId: null,
      }))
      if (!campaignId) {
        setSessionArchiveDialog({
          items: [],
          loading: false,
          error: 'Select a campaign to view archived sessions.',
          pendingId: null,
        })
        return
      }
      try {
        const allSessions = await apiFetch<SessionSummary[]>(
          baseUrl,
          `/api/sessions/campaigns/${campaignId}/sessions?include_archived=true`,
          auth,
        )
        setSessionArchiveDialog({
          items: allSessions.filter((item) => item.is_archived),
          loading: false,
          error: '',
          pendingId: null,
        })
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        setSessionArchiveDialog((current) => ({
          items: current?.items ?? [],
          loading: false,
          error: message,
          pendingId: null,
        }))
        pushError('persistence', `Could not load session archive: ${message}`)
      }
    },
    [auth, baseUrl, pushError, selectedCampaignId],
  )

  const openSessionArchiveManager = useCallback(() => {
    rememberDialogTrigger()
    void loadArchivedSessions()
  }, [loadArchivedSessions, rememberDialogTrigger])

  const closeSessionArchiveDialog = useCallback(() => {
    if (sessionArchiveDialog?.pendingId) return
    setSessionArchiveDialog(null)
  }, [sessionArchiveDialog?.pendingId])

  const archiveSelectedSessionFromManager = useCallback(async () => {
    if (!activeSession || !selectedCampaignId) {
      setSessionArchiveDialog((current) =>
        current
          ? { ...current, error: 'Select an active session before archiving.' }
          : current,
      )
      return
    }
    const sessionId = activeSession.session_id
    setSessionArchiveDialog((current) =>
      current ? { ...current, pendingId: sessionId, error: '' } : current,
    )
    try {
      await apiFetch<{ archived: boolean; session: SessionSummary }>(
        baseUrl,
        `/api/sessions/${sessionId}/archive`,
        auth,
        { method: 'POST' },
      )
      setSelectedSessionId(null)
      setLogEntries([])
      setSessionState(null)
      setOptimisticEntries([])
      setStreamingTurn(null)
      await refreshCampaignWorkspace(selectedCampaignId)
      await loadArchivedSessions(selectedCampaignId)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setSessionArchiveDialog((current) =>
        current ? { ...current, pendingId: null, error: message } : current,
      )
      pushError('persistence', `Could not archive session: ${message}`)
    }
  }, [
    activeSession,
    auth,
    baseUrl,
    loadArchivedSessions,
    pushError,
    refreshCampaignWorkspace,
    selectedCampaignId,
    setLogEntries,
    setOptimisticEntries,
    setSelectedSessionId,
    setSessionState,
    setStreamingTurn,
  ])

  const restoreSessionFromArchive = useCallback(
    async (sessionId: number) => {
      setSessionArchiveDialog((current) =>
        current ? { ...current, pendingId: sessionId, error: '' } : current,
      )
      try {
        const response = await apiFetch<{ restored: boolean; session: SessionSummary }>(
          baseUrl,
          `/api/sessions/${sessionId}/restore`,
          auth,
          { method: 'POST' },
        )
        sessionUpserted(response.session)
        await refreshCampaignWorkspace(response.session.campaign_id)
        setSelectedSessionId(response.session.session_id)
        await loadArchivedSessions(response.session.campaign_id)
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        setSessionArchiveDialog((current) =>
          current ? { ...current, pendingId: null, error: message } : current,
        )
        pushError('persistence', `Could not restore session: ${message}`)
      }
    },
    [
      auth,
      baseUrl,
      loadArchivedSessions,
      pushError,
      refreshCampaignWorkspace,
      sessionUpserted,
      setSelectedSessionId,
    ],
  )

  const resetWorldForm = useCallback(() => {
    setWorldForm({ ...emptyWorldForm })
  }, [])

  const openWorldManagerDialog = useCallback(() => {
    rememberDialogTrigger()
    setWorldForm({ ...emptyWorldForm })
    setWorldManagerOpen(true)
  }, [rememberDialogTrigger])

  const closeWorldManagerDialog = useCallback(() => {
    if (worldForm.pending || worldDeleteDialog) return
    setWorldManagerOpen(false)
    setWorldForm({ ...emptyWorldForm })
  }, [worldDeleteDialog, worldForm.pending])

  const editWorld = useCallback((world: World) => {
    setWorldForm({
      mode: 'edit',
      worldId: world.world_id,
      name: world.name,
      description: world.description ?? '',
      error: '',
      pending: false,
    })
  }, [])

  const submitWorldForm = useCallback(
    async (event: FormEvent<HTMLFormElement>) => {
      event.preventDefault()
      const name = worldForm.name.trim()
      const description = worldForm.description.trim()
      if (!name) {
        setWorldForm((current) => ({ ...current, error: 'World name is required.' }))
        return
      }
      if (worldForm.mode === 'edit' && !worldForm.worldId) {
        setWorldForm((current) => ({ ...current, error: 'Choose a world to edit.' }))
        return
      }

      setWorldForm((current) => ({ ...current, pending: true, error: '' }))
      try {
        const path =
          worldForm.mode === 'edit' && worldForm.worldId
            ? `/api/worlds/${worldForm.worldId}`
            : '/api/worlds'
        await apiFetch<World>(baseUrl, path, auth, {
          method: worldForm.mode === 'edit' ? 'PATCH' : 'POST',
          body: JSON.stringify({ name, description }),
        })
        await refreshRoot()
        setWorldForm({ ...emptyWorldForm })
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error)
        setWorldForm((current) => ({ ...current, pending: false, error: message }))
        pushError(
          'persistence',
          `Could not ${worldForm.mode === 'edit' ? 'update' : 'create'} world: ${message}`,
        )
      }
    },
    [auth, baseUrl, pushError, refreshRoot, worldForm],
  )

  const openWorldDeleteDialog = useCallback(
    (world: World) => {
      rememberDialogTrigger()
      setWorldDeleteDialog({
        world,
        error: '',
        pending: false,
        canForce: false,
      })
    },
    [rememberDialogTrigger],
  )

  const closeWorldDeleteDialog = useCallback(() => {
    if (worldDeleteDialog?.pending) return
    setWorldDeleteDialog(null)
  }, [worldDeleteDialog?.pending])

  const submitWorldDeleteDialog = useCallback(async (force = false) => {
    if (!worldDeleteDialog) return
    const { world } = worldDeleteDialog
    setWorldDeleteDialog((current) => (current ? { ...current, pending: true, error: '' } : current))
    setWorldForm((current) => ({ ...current, error: '' }))
    try {
      await apiFetch<{ deleted: boolean }>(
        baseUrl,
        `/api/worlds/${world.world_id}${force ? '?force=true' : ''}`,
        auth,
        { method: 'DELETE' },
      )
      await refreshRoot()
      setWorldForm((current) =>
        current.worldId === world.world_id ? { ...emptyWorldForm } : current,
      )
      setWorldDeleteDialog(null)
    } catch (error) {
      const message = worldDeleteErrorMessage(error)
      const canForce =
        error instanceof ApiClientError &&
        isRecord(error.payload) &&
        error.payload.error_code === 'world_in_use'
      setWorldDeleteDialog((current) =>
        current ? { ...current, pending: false, error: message, canForce } : current,
      )
      setWorldForm((current) => ({ ...current, error: message }))
      pushError('persistence', `Could not delete world: ${message}`)
    }
  }, [auth, baseUrl, pushError, refreshRoot, worldDeleteDialog])

  const openRuntimeSettingsDialog = () => {
    rememberDialogTrigger()
    setAccountMenuOpen(false)
    openRuntimeSettings()
  }

  const openWorkspaceAuthDialog = () => {
    rememberDialogTrigger()
    setAccountMenuOpen(false)
    openRuntimeSettings('auth')
  }

  const openSavedWorkspaceDeleteDialog = useCallback(
    (workspace: AccountWorkspace) => {
      setSavedWorkspaceDeleteDialog({ workspace, error: '', pending: false })
    },
    [],
  )

  const closeSavedWorkspaceDeleteDialog = useCallback(() => {
    if (savedWorkspaceDeleteDialog?.pending) return
    setSavedWorkspaceDeleteDialog(null)
  }, [savedWorkspaceDeleteDialog?.pending])

  const submitSavedWorkspaceDeleteDialog = useCallback(async () => {
    if (!savedWorkspaceDeleteDialog) return
    setSavedWorkspaceDeleteDialog((current) => (current ? { ...current, pending: true, error: '' } : current))
    const result = await deleteSavedWorkspace(savedWorkspaceDeleteDialog.workspace.workspace_id)
    if (result.ok) {
      setSavedWorkspaceDeleteDialog(null)
      return
    }
    setSavedWorkspaceDeleteDialog((current) =>
      current ? { ...current, pending: false, error: result.error } : current,
    )
  }, [deleteSavedWorkspace, savedWorkspaceDeleteDialog])

  const savedWorkspaceDeleteDialogDeletesTable = Boolean(
    savedWorkspaceDeleteDialog?.workspace.is_workspace_admin &&
      savedWorkspaceDeleteDialog.workspace.access_mode !== 'configured',
  )

  const closeRuntimeSettingsDialog = useCallback(() => {
    closeRuntimeSettings()
  }, [closeRuntimeSettings])

  const openProfileSettingsDialog = () => {
    rememberDialogTrigger()
    setAccountMenuOpen(false)
    setProfileSettingsOpen(true)
  }

  const closeProfileSettingsDialog = useCallback(() => {
    setProfileSettingsOpen(false)
  }, [])

  const {
    closePlayerDeleteDialog,
    closePlayerEditDialog,
    openCreatePlayerDialog,
    openPlayerDeleteDialog,
    openPlayerEditDialog,
    playerDeleteDialog,
    playerEditDialog,
    setPlayerEditDialog,
    submitPlayerDeleteDialog,
    submitPlayerEditDialog,
  } = usePlayerProfileActions({
    auth,
    baseUrl,
    selectedPlayer,
    selectedCampaignId,
    rememberDialogTrigger,
    refreshCampaignWorkspace,
    setProfileSettingsOpen,
    setPlayerDetail,
    setSelectedPlayerId,
    playerUpserted,
    pushError,
  })

  const promptCreatePlayer = useCallback(() => {
    openCreatePlayerDialog(selectedCampaignId)
    return Promise.resolve()
  }, [openCreatePlayerDialog, selectedCampaignId])

  const campaignChooserKey = useMemo(
    () => `${storedSelectionScope}:${campaigns.map((item) => item.campaign_id).join(',')}`,
    [campaigns, storedSelectionScope],
  )

  const closeCampaignChooserDialog = useCallback(() => {
    setCampaignChooserDismissedKey(campaignChooserKey)
    setCampaignChooserOpen(false)
  }, [campaignChooserKey])

  const chooseCampaign = useCallback(
    (campaignId: number) => {
      setSelectedCampaignId(campaignId)
      setCampaignChooserOpen(false)
      setMainTab('turns')
    },
    [setSelectedCampaignId],
  )

  const createCampaignFromChooser = useCallback(() => {
    setCampaignChooserOpen(false)
    openCreateCampaignDialog()
  }, [openCreateCampaignDialog])

  const openCharacterJoinDialog = useCallback(() => {
    if (!selectedCampaignId) return
    rememberDialogTrigger()
    setProfileSettingsOpen(false)
    setCharacterJoinDialogOpen(true)
  }, [rememberDialogTrigger, selectedCampaignId])

  const closeCharacterJoinDialog = useCallback(() => {
    setCharacterJoinDialogOpen(false)
  }, [])

  const joinAsExistingPlayer = useCallback(
    (player: Player) => {
      setSelectedPlayerId(player.player_id)
      setCharacterJoinDialogOpen(false)
    },
    [setSelectedPlayerId],
  )

  const createCharacterFromJoinDialog = useCallback(() => {
    setCharacterJoinDialogOpen(false)
    openCreatePlayerDialog(selectedCampaignId)
  }, [openCreatePlayerDialog, selectedCampaignId])

  const clearAuthToken = () => {
    clearRuntimeAuthToken()
    setAccountMenuOpen(false)
    setProfileSettingsOpen(false)
  }

  const switchRuntime = useCallback(
    async (provider: string, model: string) => {
      if (!provider || !model) return
      setRuntimePending(true)
      try {
        const nextConfig = await apiFetch<LlmRuntimeConfig>(
          baseUrl,
          '/api/llm/config',
          auth,
          {
            method: 'PATCH',
            headers: runtimeConfigHeaders,
            body: JSON.stringify({ provider, model, persist: true }),
          },
        )
        setLlmConfig(nextConfig)
        setHealth((current) =>
          current
            ? {
                ...current,
                llm: nextConfig.current,
              }
            : current,
        )
      } catch (error) {
        pushError('system', `Runtime switch failed: ${error instanceof Error ? error.message : String(error)}`)
      } finally {
        setRuntimePending(false)
      }
    },
    [auth, baseUrl, pushError, runtimeConfigHeaders],
  )

  useEffect(() => {
    refreshRoot()
  }, [refreshRoot])

  useEffect(() => {
    localStorage.setItem('aidm:theme', theme)
  }, [theme])

  useEffect(() => {
    const currentMap = viewerMaps[0]
    setMapManagementForm({
      title: currentMap?.title ?? (campaign ? `${campaign.title} Map` : ''),
      description: currentMap?.description ?? campaign?.location ?? '',
      visibility: currentMap?.visibility ?? 'player',
    })
  }, [campaign, setMapManagementForm, viewerMaps])

  useEffect(() => {
    submitActionRef.current = submitAction
    toggleFullscreenRef.current = toggleFullscreen
  })

  useEffect(() => {
    sessionActionDialogRef.current = sessionActionDialog
  }, [sessionActionDialog])

  useEffect(() => {
    campaignActionDialogRef.current = campaignActionDialog
  }, [campaignActionDialog])

  const closeCurrentDialog = useCallback(() => {
    if (diceRoll) {
      closeDiceRoll()
      return
    }
    const activeCampaignDialog = campaignActionDialogRef.current
    if (activeCampaignDialog) {
      if (!activeCampaignDialog.pending) {
        setCampaignActionDialog(null)
      }
      return
    }
    const activeSessionDialog = sessionActionDialogRef.current
    if (activeSessionDialog) {
      if (!activeSessionDialog.pending) {
        setSessionActionDialog(null)
      }
      return
    }
    if (savedWorkspaceDeleteDialog) {
      closeSavedWorkspaceDeleteDialog()
      return
    }
    if (runtimeSettingsOpen) {
      closeRuntimeSettingsDialog()
      return
    }
    if (shareSessionUrl) {
      closeShareSessionDialog()
      return
    }
    if (worldDeleteDialog) {
      closeWorldDeleteDialog()
      return
    }
    if (worldManagerOpen) {
      closeWorldManagerDialog()
      return
    }
    if (campaignArchiveDialog) {
      closeCampaignArchiveDialog()
      return
    }
    if (sessionArchiveDialog) {
      closeSessionArchiveDialog()
      return
    }
    if (campaignPackImportOpen) {
      closeCampaignPackImportDialog()
      return
    }
    if (campaignChooserOpen) {
      closeCampaignChooserDialog()
      return
    }
    if (characterJoinDialogOpen) {
      closeCharacterJoinDialog()
      return
    }
    if (profileSettingsOpen) {
      closeProfileSettingsDialog()
      return
    }
    if (playerDeleteDialog) {
      if (!playerDeleteDialog.pending) {
        closePlayerDeleteDialog()
      }
      return
    }
    if (playerEditDialog) {
      if (!playerEditDialog.pending) {
        closePlayerEditDialog()
      }
      return
    }
    if (createCampaignOpen) {
      closeCreateCampaignDialog()
    }
  }, [
    closeCreateCampaignDialog,
    closeDiceRoll,
    closePlayerDeleteDialog,
    closeShareSessionDialog,
    closePlayerEditDialog,
    closeCharacterJoinDialog,
    closeProfileSettingsDialog,
    closeRuntimeSettingsDialog,
    closeSavedWorkspaceDeleteDialog,
    closeWorldManagerDialog,
    closeWorldDeleteDialog,
    closeCampaignArchiveDialog,
    closeSessionArchiveDialog,
    closeCampaignPackImportDialog,
    closeCampaignChooserDialog,
    campaignArchiveDialog,
    campaignPackImportOpen,
    campaignChooserOpen,
    characterJoinDialogOpen,
    createCampaignOpen,
    diceRoll,
    playerDeleteDialog,
    playerEditDialog,
    profileSettingsOpen,
    runtimeSettingsOpen,
    savedWorkspaceDeleteDialog,
    setCampaignActionDialog,
    setSessionActionDialog,
    sessionArchiveDialog,
    shareSessionUrl,
    worldDeleteDialog,
    worldManagerOpen,
  ])

  const activeModalKey = diceRoll
    ? `dice-roll-${diceRoll.rollKey}`
    : campaignActionDialog
      ? 'campaign-action'
      : sessionActionDialog
        ? 'session-action'
        : worldDeleteDialog
          ? 'world-delete'
          : worldManagerOpen
            ? 'world-manager'
            : campaignArchiveDialog
              ? 'campaign-archive'
              : sessionArchiveDialog
                ? 'session-archive'
                : campaignPackImportOpen
                  ? 'campaign-pack-import'
                  : campaignChooserOpen
                    ? 'campaign-chooser'
                    : characterJoinDialogOpen
                      ? 'character-join'
                      : playerDeleteDialog
                        ? 'player-delete'
                        : playerEditDialog
                          ? `player-edit-${playerEditDialog.mode}`
                          : savedWorkspaceDeleteDialog
                            ? 'saved-workspace-delete'
                            : runtimeSettingsOpen
                              ? 'runtime-settings'
                              : shareSessionUrl
                                ? 'share-session'
                                : profileSettingsOpen
                                  ? 'profile-settings'
                                  : createCampaignOpen
                                    ? 'create-campaign'
                                    : null
  const modalOpen = Boolean(activeModalKey)
  useModalFocusTrap({
    activeKey: activeModalKey,
    dialogRef: modalDialogRef,
    onClose: closeCurrentDialog,
    returnFocusRef: dialogReturnFocusRef,
  })
  const {
    createAccountFromTitleScreen,
    createCampaignFromTitleScreen,
    continueFromTitleScreen,
    logInFromTitleScreen,
    playNowFromTitleScreen,
    playNowPending,
    showTitleScreen,
    titleScreenAccountReady,
    titleScreenCanContinue,
  } = usePlayNowOnboarding({
    activeSessionId,
    auth,
    authRequired: health?.auth_required ?? null,
    hostedAccessReady: hostedWorkspaceAccessReady,
    backendReady: health?.status === 'ok',
    baseUrl,
    campaignCount: campaigns.length,
    closeMobilePanels,
    modalOpen,
    runtimeSettingsOpen,
    selectedCampaignId,
    selectedPlayerDetailId,
    selectedPlayerId,
    selectedSessionId,
    workspaceLoading,
    campaignUpserted,
    sessionUpserted,
    playerUpserted,
    adoptAccountSession,
    clearAuthTokenErrors,
    loadPlayerDetail,
    loadSessionData,
    openCreateCampaignDialog,
    openLogIn: () => {
      setRuntimeAuthIntent('login')
      openRuntimeSettings('auth')
    },
    openCreateAccount: () => {
      setRuntimeAuthIntent('signup')
      openRuntimeSettings('auth')
    },
    pushError,
    refreshCampaignWorkspace,
    refreshRoot,
    setClarificationRequest,
    setLogEntries,
    setMainTab,
    setOptimisticEntries,
    setPlayerDetail,
    setSelectedCampaignId,
    setSelectedPlayerId,
    setSelectedSessionId,
    setSessionState,
    setStreamingTurn,
    setTurnStatuses,
    currentResponsePresent: Boolean(currentResponseEntry),
    dmResponseBlocking,
    sendPending,
    socketStatus,
    startAdventure,
    turnRowCount,
  })

  useEffect(() => {
    if (showTitleScreen && (mobileRailOpen || mobileInspectorOpen)) {
      closeMobilePanels()
    }
  }, [closeMobilePanels, mobileInspectorOpen, mobileRailOpen, showTitleScreen])

  useEffect(() => {
    if (
      showTitleScreen ||
      (!auth && (!runtimeAccount?.workspaceId || runtimeAccount.workspaceId !== workspaceId)) ||
      selectedCampaignId ||
      health?.status !== 'ok' ||
      workspaceLoading ||
      loadingCampaignId !== null ||
      modalOpen ||
      campaignChooserDismissedKey === campaignChooserKey
    ) {
      return
    }
    rememberDialogTrigger()
    setCampaignChooserOpen(true)
  }, [
    auth,
    campaignChooserDismissedKey,
    campaignChooserKey,
    health?.status,
    loadingCampaignId,
    modalOpen,
    rememberDialogTrigger,
    runtimeAccount?.workspaceId,
    selectedCampaignId,
    showTitleScreen,
    workspaceId,
    workspaceLoading,
  ])

  useEffect(() => {
    if (
      !selectedCampaignId ||
      !campaign ||
      selectedPlayerId ||
      workspaceLoading ||
      loadingCampaignId === selectedCampaignId ||
      showTitleScreen ||
      modalOpen
    ) {
      return
    }
    if (promptedCharacterCampaignIdsRef.current.has(selectedCampaignId)) return
    promptedCharacterCampaignIdsRef.current.add(selectedCampaignId)
    setCharacterJoinDialogOpen(true)
  }, [
    campaign,
    loadingCampaignId,
    modalOpen,
    selectedCampaignId,
    selectedPlayerId,
    showTitleScreen,
    workspaceLoading,
  ])

  useEffect(() => {
    if (showTitleScreen || modalOpen || diceRoll) return undefined
    const handleKeyDown = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase()
      const modifier = event.metaKey || event.ctrlKey
      if (!modifier) return

      if (key === 'k') {
        event.preventDefault()
        actionInputRef.current?.focus()
        return
      }

      if (key === 'enter') {
        event.preventDefault()
        submitActionRef.current?.()
        return
      }

      if (key === '.' && canStopTts) {
        event.preventDefault()
        stopTtsAudio()
        return
      }

      if (event.shiftKey && key === 'f') {
        event.preventDefault()
        void toggleFullscreenRef.current?.()
        return
      }

      if (event.shiftKey && key === 'r') {
        event.preventDefault()
        void refreshCurrentWorkspace()
        return
      }

      if (key === 'j' && !isEditableShortcutTarget(event.target)) {
        event.preventDefault()
        setMainTab('turns')
        scrollTurnFeedToLatest()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [
    canStopTts,
    diceRoll,
    modalOpen,
    refreshCurrentWorkspace,
    scrollTurnFeedToLatest,
    showTitleScreen,
    stopTtsAudio,
  ])

  useEffect(() => {
    const updateFullscreenState = () => {
      const active = Boolean(document.fullscreenElement)
      setIsFullscreen(active)
      if (active) {
        setFullscreenFallback(false)
      }
    }
    updateFullscreenState()
    document.addEventListener('fullscreenchange', updateFullscreenState)
    return () => {
      document.removeEventListener('fullscreenchange', updateFullscreenState)
    }
  }, [])

  useEffect(() => {
    if (!fullscreenFallback) return undefined
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setFullscreenFallback(false)
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [fullscreenFallback])

  useEffect(() => {
    if (!accountMenuOpen && !sessionMenuOpen) return undefined

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target
      if (!(target instanceof Node)) return
      if (accountMenuOpen && !accountMenuRef.current?.contains(target)) {
        setAccountMenuOpen(false)
        setBetaNotesOpen(false)
      }
      if (sessionMenuOpen && !sessionMenuRef.current?.contains(target)) {
        setSessionMenuOpen(false)
      }
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (betaNotesOpen) {
          closeBetaNotes()
          return
        }
        setAccountMenuOpen(false)
        setSessionMenuOpen(false)
      }
    }

    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [accountMenuOpen, betaNotesOpen, closeBetaNotes, sessionMenuOpen])

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (selectedCampaignId) {
      params.set('campaign', String(selectedCampaignId))
      localStorage.setItem(
        selectionStorageKey(storedSelectionScope, 'selectedCampaignId'),
        String(selectedCampaignId),
      )
    } else {
      params.delete('campaign')
      localStorage.removeItem(selectionStorageKey(storedSelectionScope, 'selectedCampaignId'))
    }
    if (selectedSessionId) {
      params.set('session', String(selectedSessionId))
      localStorage.setItem(
        selectionStorageKey(storedSelectionScope, 'selectedSessionId'),
        String(selectedSessionId),
      )
    } else {
      params.delete('session')
      localStorage.removeItem(selectionStorageKey(storedSelectionScope, 'selectedSessionId'))
    }
    if (selectedPlayerId) {
      localStorage.setItem(
        selectionStorageKey(storedSelectionScope, 'selectedPlayerId'),
        String(selectedPlayerId),
      )
    } else {
      localStorage.removeItem(selectionStorageKey(storedSelectionScope, 'selectedPlayerId'))
    }
    localStorage.removeItem('aidm:selectedCampaignId')
    localStorage.removeItem('aidm:selectedSessionId')
    localStorage.removeItem('aidm:selectedPlayerId')
    params.delete('player')
    params.delete('backend')
    params.delete('api')
    const query = params.toString()
    const nextUrl = `${window.location.pathname}${query ? `?${query}` : ''}`
    window.history.replaceState(null, '', nextUrl)
  }, [selectedCampaignId, selectedPlayerId, selectedSessionId, storedSelectionScope])

  useEffect(() => {
    if (selectedCampaignId) {
      refreshCampaignWorkspace(selectedCampaignId)
    }
  }, [refreshCampaignWorkspace, selectedCampaignId])

  useEffect(() => {
    if (!activeSessionId) {
      clearSessionData()
      setSessionRecap('')
      setDirectorCommentary(null)
      setTurnStatuses({})
      setClarificationRequest(null)
      return
    }
    setSessionLogCursor(null)
    setSessionLogHasMore(false)
    setTurnStatuses({})
    setClarificationRequest(null)
    loadSessionData(activeSessionId).then(clearAuthTokenErrors).catch((error: unknown) => {
      if (isUnauthorizedError(error)) {
        openAuthTokenPrompt()
        clearAuthTokenErrors()
        return
      }
      pushError('workspace', `Session refresh failed: ${error instanceof Error ? error.message : String(error)}`)
    })
  }, [
    clearAuthTokenErrors,
    clearSessionData,
    loadSessionData,
    openAuthTokenPrompt,
    pushError,
    activeSessionId,
    setSessionLogCursor,
    setSessionLogHasMore,
  ])

  useEffect(() => {
    if (!activeSessionId) {
      setSessionRecap('')
      return undefined
    }
    // When a populated session's timeline is being cleared (for example,
    // during delete/switch), wait for the replacement log instead of racing a
    // recap request against the session mutation.
    if ((activeSession?.turn_count ?? 0) > 0 && !latestDmEntry?.id) {
      setSessionRecap('')
      return undefined
    }
    let cancelled = false
    setSessionRecap('')
    apiFetch<SessionRecapResponse>(baseUrl, `/api/sessions/${activeSessionId}/recap`, auth)
      .then((payload) => {
        if (!cancelled) {
          setSessionRecap(payload.recap || '')
        }
      })
      .catch(() => {
        if (!cancelled) {
          setSessionRecap('')
        }
      })
    return () => {
      cancelled = true
    }
  }, [activeSession?.turn_count, activeSessionId, auth, baseUrl, latestDmEntry?.id])

  useEffect(() => {
    if (!activeSessionId || !canUseOperatorTools) {
      setDirectorCommentary(null)
      return undefined
    }
    let cancelled = false
    setDirectorCommentary(null)
    apiFetch<CampaignPackCommentaryResponse>(
      baseUrl,
      `/api/sessions/${activeSessionId}/campaign-pack/commentary`,
      auth,
    )
      .then((payload) => {
        if (!cancelled) {
          setDirectorCommentary(payload.enabled ? payload : null)
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return
        if (error instanceof ApiClientError && (error.status === 403 || error.status === 404)) {
          setDirectorCommentary(null)
          return
        }
        setDirectorCommentary(null)
      })
    return () => {
      cancelled = true
    }
  }, [activeSessionId, auth, baseUrl, canUseOperatorTools])

  const speakSessionRecap = useCallback(
    (text: string) => {
      if (!text.trim()) return
      if (!ttsEnabledRef.current || !queueTtsNarrationRef.current) {
        pushError('tts', 'Turn TTS on to hear recap narration.')
        return
      }
      queueTtsNarrationRef.current(text)
    },
    [pushError, queueTtsNarrationRef, ttsEnabledRef],
  )

  useEffect(() => {
    if (!selectedPlayerDetailId) {
      playerRequestRef.current += 1
      setPlayerDetail(null)
      return
    }
    void loadPlayerDetail(selectedPlayerDetailId)
  }, [
    loadPlayerDetail,
    selectedPlayerDetailId,
    setPlayerDetail,
  ])

  const toggleInventoryEquipment = useCallback(async (item: InventoryRow) => {
    if (!selectedPlayerDetailId) {
      pushError('validation', 'Choose a player before changing equipment.')
      return
    }
    if (!item.equippable) {
      pushError('validation', `${item.item} cannot be equipped.`)
      return
    }
    const itemKey = item.id || item.item
    const requestAuth = auth
    const requestAccessSnapshot = storedRuntimeAccessSnapshot(requestAuth)
    setEquipmentPendingItemKey(itemKey)
    try {
      const updated = await apiFetch<PlayerEquipmentUpdateResponse>(
        baseUrl,
        `/api/players/${selectedPlayerDetailId}/inventory/equipment`,
        requestAuth,
        {
          method: 'PATCH',
          body: JSON.stringify({
            action: item.equipped ? 'unequip' : 'equip',
            item_id: item.id || undefined,
            item_name: item.id ? undefined : item.item,
            session_id: activeSessionId || undefined,
          }),
        },
      )
      setPlayerDetail(updated)
      if (activeSessionId) {
        await loadSessionData(activeSessionId)
      }
    } catch (error) {
      if (isUnauthorizedError(error)) {
        if (requestAccessSnapshot !== storedRuntimeAccessSnapshot()) return
        openAuthTokenPrompt()
        clearAuthTokenErrors()
        return
      }
      pushError('workspace', `Equipment update failed: ${error instanceof Error ? error.message : String(error)}`)
    } finally {
      setEquipmentPendingItemKey((current) => (current === itemKey ? null : current))
    }
  }, [
    activeSessionId,
    auth,
    baseUrl,
    clearAuthTokenErrors,
    loadSessionData,
    openAuthTokenPrompt,
    pushError,
    selectedPlayerDetailId,
    setPlayerDetail,
  ])

  useSessionSocket({
    auth,
    baseUrl,
    selectedSessionId: activeSessionId,
    selectedPlayerId: selectedPlayerDetailId,
    selectedCampaignId: socketCampaignId,
    socketReconnectKey,
    socketRef,
    loadSessionData,
    refreshPlayerDetail: loadPlayerDetail,
    pushError,
    rememberStreamedTtsTurn,
    resetTtsFailureForNextResponse,
    stopTtsAudio,
    setActivePlayers,
    setSessionState,
    setSocketStatus,
    setSendPending,
    setOptimisticEntries,
    setStreamingTurn,
    setTurnStatuses,
    setClarificationRequest,
    setSceneMusicSyncState,
    setSceneState,
    spokenTextLengthRef,
    speakableStreamingTextRef,
    queueTtsNarrationRef,
    ttsEnabledRef,
    ttsQueueSuppressedRef,
    ttsFailureReportedRef,
    ttsPartialFlushTimerRef,
    lastSpokenDmEntryRef,
    lastSpokenTurnIdRef,
    lastSpokenTextRef,
    onConnectionInterrupted: handleConnectionInterrupted,
    onRollRequired: handleRollRequired,
    onRollResolved: handleRollResolved,
    onTurnDuplicate: handleTurnDuplicate,
  })

  const updateSceneMusicControl = useCallback(
    (payload: SceneMusicControlPayload) => {
      if (!activeSessionId || !selectedPlayerDetailId) return
      const socket = socketRef.current
      if (!socket) {
        pushError('connection', 'Socket is not connected; reconnect before changing session music.')
        return
      }
      socket.emit('music_control', {
        session_id: activeSessionId,
        player_id: selectedPlayerDetailId,
        track_id: payload.trackId,
        status: payload.status,
        position: payload.position,
      })
    },
    [activeSessionId, pushError, selectedPlayerDetailId],
  )

  const resolveClarification = useCallback(
    (selectedItemId: string) => {
      if (!clarificationRequest || !activeSessionId || !selectedPlayerDetailId) return
      const socket = socketRef.current
      if (!socket) {
        pushError('connection', 'Socket is not connected; reconnect before choosing an item.')
        return
      }
      setSendPending(true)
      socket.emit('resolve_clarification', {
        session_id: activeSessionId,
        player_id: selectedPlayerDetailId,
        turn_id: clarificationRequest.turnId,
        selected_item_id: selectedItemId,
      })
      setClarificationRequest(null)
    },
    [activeSessionId, clarificationRequest, pushError, selectedPlayerDetailId, socketRef],
  )

  useEffect(() => {
    if (health?.status !== 'ok' || workspaceLoading || sessionLoading) return
    clearResolvedOperationalErrors()
  }, [clearResolvedOperationalErrors, health?.status, sessionLoading, workspaceLoading])

  useEffect(() => {
    if (socketStatus !== 'joined' && socketStatus !== 'idle') return
    clearResolvedOperationalErrors()
  }, [clearResolvedOperationalErrors, socketStatus])

  const displayPlayer = selectedPlayerMatchesDetail ? playerDetail : selectedPlayer
  const displayCharacter = {
    name: displayPlayer?.character_name ?? 'No player selected',
    ancestryClass: displayPlayer
      ? `${displayPlayer.race || 'Adventurer'} ${displayPlayer.char_class || displayPlayer.class_ || 'Class unset'}`
      : 'Load or create a player',
    level: displayPlayer?.level ?? '—',
    detailId: displayPlayer?.player_id ? `Player #${displayPlayer.player_id}` : 'No player',
  }
  const xpProgress = normalizeXp(playerDetail?.stats ?? playerDetail?.character_sheet, displayCharacter.level)
  const capacity = inventoryCapacity(playerDetail?.stats ?? playerDetail?.character_sheet)
  const inventoryWeightLabel = buildInventoryWeightLabel(inventoryRows, capacity)
  const inventoryGoldLabel = buildInventoryGoldLabel(playerDetail?.stats, playerDetail?.character_sheet)
  const characterAvatarSrc =
    displayPlayer?.profile_image ||
    profileIconSrcForCharacter({
      race: displayPlayer?.race,
      sex: displayPlayer?.sex,
      seed: displayCharacter.name,
    }) ||
    avatarDataUri(displayCharacter.name, 'character')
  const memorySnippets = memorySnippetRecords(sessionState?.memory_snippets)
  const activeSessionSnapshot = isRecord(sessionState?.state_snapshot)
    ? sessionState.state_snapshot
    : snapshotRecord(activeSession)
  const turnRecoveryGate = useMemo(
    () => turnRecoveryGateFromSnapshot(activeSessionSnapshot),
    [activeSessionSnapshot],
  )
  const turnRecoveryTurnId = turnRecoveryGate?.turnId ?? null

  useEffect(() => {
    setTurnRecoveryPending(false)
    setTurnRecoveryError('')
    setTurnRecoverySuccess('')
  }, [activeSessionId])

  useEffect(() => {
    if (turnRecoveryTurnId === null) return
    setTurnRecoveryError('')
    setTurnRecoverySuccess('')
  }, [turnRecoveryTurnId])

  const resolveTurnRecovery = useCallback(
    async (resolution: TurnRecoveryResolution, operatorNote: string) => {
      const note = operatorNote.trim()
      if (!activeSessionId || !turnRecoveryGate || !canUseOperatorTools) return
      if (!note || note.length > 1000) {
        setTurnRecoveryError('Enter an operator note between 1 and 1000 characters.')
        return
      }

      setTurnRecoveryPending(true)
      setTurnRecoveryError('')
      setTurnRecoverySuccess('')
      try {
        const response = await apiFetch<TurnRecoveryResponse>(
          baseUrl,
          `/api/sessions/${activeSessionId}/recovery/resolve`,
          auth,
          {
            method: 'POST',
            body: JSON.stringify({
              turn_id: turnRecoveryGate.turnId,
              resolution,
              operator_note: note,
            }),
          },
        )
        if (
          response.resolved !== true ||
          response.session_id !== activeSessionId ||
          response.turn_id !== turnRecoveryGate.turnId ||
          response.resolution !== resolution
        ) {
          throw new Error('Recovery response did not match the requested session and turn.')
        }
        await loadSessionData(activeSessionId)
        setTurnRecoverySuccess(
          response.idempotent_replay
            ? 'Recovery was already resolved. Session state is refreshed.'
            : 'Recovery resolved. Session state is refreshed and play can resume.',
        )
      } catch (error) {
        if (isUnauthorizedError(error)) {
          openAuthTokenPrompt()
          clearAuthTokenErrors()
        }
        let refreshDetail: string
        try {
          await loadSessionData(activeSessionId)
          refreshDetail = 'Authoritative session state was refreshed; the recovery request was not retried.'
        } catch (refreshError) {
          refreshDetail = `Session refresh also failed: ${
            refreshError instanceof Error ? refreshError.message : String(refreshError)
          }`
        }
        setTurnRecoveryError(
          `Recovery failed: ${error instanceof Error ? error.message : String(error)} ${refreshDetail}`,
        )
      } finally {
        setTurnRecoveryPending(false)
      }
    },
    [
      activeSessionId,
      auth,
      baseUrl,
      canUseOperatorTools,
      clearAuthTokenErrors,
      loadSessionData,
      openAuthTokenPrompt,
      turnRecoveryGate,
    ],
  )
  const contentSettings = useMemo(
    () => contentSettingsFromSnapshot(activeSessionSnapshot),
    [activeSessionSnapshot],
  )
  const {
    contentSettingsPending,
    updateContentRating,
    updateContentToneTags,
  } = useSessionContentSettings({
    activeSessionId,
    auth,
    baseUrl,
    canEditContentSettings: canUseOperatorTools,
    contentSettings,
    clearAuthTokenErrors,
    pushError,
    sessionUpserted,
    setSessionState,
  })
  const worldStatePanel = worldStateFromSnapshot(activeSessionSnapshot)
  const gameplayControls = useMemo(
    () => gameplayControlsFromSnapshot(activeSessionSnapshot, selectedPlayerId),
    [activeSessionSnapshot, selectedPlayerId],
  )
  const recentMemory = recentMemoryFromSnippets(memorySnippets, selectedSessionId)
  const visibleRecentMemory = inspectorTab === 'canon' ? recentMemory : recentMemory.slice(0, 3)
  const selectedSegment =
    segments.find((segment) => segment.is_triggered) ?? segments[0] ?? null
  const mapTitle = viewerMaps[0]?.title ?? 'No map recorded'
  const mapDescription =
    viewerMaps[0]?.description ||
    sessionState?.current_location ||
    campaign?.location ||
    'No location recorded'
  const questTitle =
    sessionState?.current_quest || campaign?.current_quest || 'No quest recorded'
  const mapPanelTitle =
    viewerMaps[0]?.title || selectedSegment?.title || (sessionState?.current_location ? 'Current Location' : mapTitle)
  const mapMeta = buildMapMeta(viewerMaps[0], selectedSegment)
  const sessionCards: SessionCard[] = sessions.map((session, index) => ({
    id: session.session_id,
    title: sessionDisplayName(session, campaign?.world_id ?? selectedCampaignId),
    meta: `${
      session.is_archived
        ? 'Archived'
        : session.session_id === selectedSessionId
          ? 'Active'
          : index === 0
            ? 'Latest'
            : 'Past'
    }  •  Started ${formatShortAge(session.created_at)}`,
  }))
  const filteredCampaigns = campaigns.filter((item) =>
    item.title.toLowerCase().includes(campaignFilter.trim().toLowerCase()),
  )
  const worldNameById = new Map<number, string>()
  worlds.forEach((world) => worldNameById.set(world.world_id, world.name))
  campaigns.forEach((item) => {
    if (item.world_name) {
      worldNameById.set(item.world_id, item.world_name)
    }
  })
  const campaignCards: CampaignCard[] = [...filteredCampaigns]
    .sort((left, right) => {
      if (left.campaign_id === selectedCampaignId) return -1
      if (right.campaign_id === selectedCampaignId) return 1
      return 0
    })
    .map((item) => {
      const worldLabel = worldNameById.get(item.world_id) ?? `World ${item.world_id}`
      const statusLabel = item.is_archived ? 'Archived' : 'Active'
      return {
        title: item.title,
        meta: `${statusLabel}  •  ${worldLabel}  •  ${pluralize(campaignSessionMeta[item.campaign_id]?.count ?? 0, 'Session')}  •  Updated ${formatShortAge(campaignSessionMeta[item.campaign_id]?.updatedAt ?? item.created_at)}`,
        id: item.campaign_id,
        avatar: avatarDataUri(`${item.campaign_id}-${item.title}`),
      }
    })
  const lastSync = sessionState?.updated_at ?? activeSession?.created_at ?? null
  const runtime = llmConfig?.current ?? health?.llm ?? null
  const latestRuntime = runtime?.latest_turn ?? null
  const configuredProvider = stringValue(runtime?.provider, 'Unknown')
  const configuredModel = stringValue(runtime?.model, 'Unknown')
  const safeModeActive = configuredProvider.trim().toLowerCase() === 'fallback'
  const runtimeProviders = llmConfig?.providers ?? []
  const selectedProviderOption = runtimeProviders.find(
    (provider) => provider.id === configuredProvider,
  )
  const runtimeModels = selectedProviderOption?.models ?? [
    { id: configuredModel, label: configuredModel },
  ]
  const showProcessLocalDiagnostic = Boolean(
    llmConfig?.runtime_scope === 'process' &&
    (llmConfig.worker_count ?? 1) > 1 &&
    llmConfig.restart_required_for_other_workers &&
    currentUserIsWorkspaceAdmin,
  )
  const runtimeScopeTitle =
    'Provider changes apply to this backend process; restart the other workers to synchronize them.'
  const backendStatusLabel =
    health === null ? 'Checking' : health.status === 'ok' ? 'Connected' : 'Offline'
  const backendStatusTone =
    health === null ? 'neutral' : health.status === 'ok' ? 'good' : 'warn'
  const backendDisplayUrl = baseUrl || 'Same origin'
  const tableDisplayName = health?.auth_required === false
    ? 'Local Table'
    : tableStatusDisplayName(runtimeAccount, workspaceId)
  const runtimeLabel = runtimePending
    ? 'Switching'
    : safeModeActive
      ? 'Safe Mode'
      : runtime?.configured
        ? 'Live'
        : health === null
          ? 'Checking'
          : health.status === 'ok'
            ? 'Missing key'
            : 'Offline'
  const runtimeTone =
    runtimePending || health === null ? 'neutral' : runtime?.configured && !safeModeActive ? 'good' : 'warn'
  const ttsConfigurationStatus = ttsConfigLoadFailed
    ? 'Status unavailable'
    : ttsConfig === null
      ? 'Checking'
      : ttsConfig.configured ? 'Configured' : 'Unavailable'
  const ttsConfigurationDataStatus = ttsConfigLoadFailed
    ? 'error'
    : ttsConfig === null
      ? 'checking'
      : ttsConfig.configured ? 'configured' : 'unavailable'
  const ttsControlLabel = ttsConfigLoadFailed
    ? 'Narration status unavailable; configuration check failed'
    : ttsConfig === null
      ? 'Narration availability is still being checked'
      : ttsConfig.configured
        ? `${ttsEnabled ? 'Turn TTS off' : 'Turn TTS on'}; Deepgram is configured`
        : 'Narration unavailable; Deepgram is not configured'
  const ttsControlTitle = ttsConfigLoadFailed
    ? 'Could not check Deepgram narration configuration; refresh to retry'
    : ttsConfig === null
      ? 'Checking Deepgram narration availability'
      : ttsConfig.configured
        ? `Deepgram narration configured: ${ttsConfig.model} (${ttsStatusLabel})`
        : 'Deepgram narration is unavailable because the backend is not configured'
  const betaRuntimeNotices: Array<{
    id: string
    title: string
    message: string
    tone: 'info' | 'warn'
  }> = []
  if (safeModeActive) {
    betaRuntimeNotices.push({
      id: 'fallback-provider',
      title: 'Safe Mode',
      message: 'Fallback DM active. Ask the table operator to restore the live provider.',
      tone: 'warn',
    })
  } else if (health?.status === 'ok' && runtime && !runtime.configured) {
    betaRuntimeNotices.push({
      id: 'missing-provider-key',
      title: 'Provider Key',
      message: 'Live DM is unavailable. Ask the table operator to configure the selected provider.',
      tone: 'warn',
    })
  }
  const loadedTextLength = timeline.reduce((total, entry) => total + entry.text.length, 0)
  const estimatedContextTokens = Math.round(loadedTextLength / 4)
  const contextMeterPercent = Math.min(
    100,
    Math.max(estimatedContextTokens > 0 ? 4 : 0, Math.round((estimatedContextTokens / 128000) * 100)),
  )
  const contextLabel = estimatedContextTokens
    ? `~${formatCompactNumber(estimatedContextTokens).toLowerCase()} tok`
    : 'No log'
  const responseTokenEstimate = Math.max(1, Math.round(latestDmText.length / 4))
  const executionTimeSeconds =
    latestRuntime?.latency_ms !== null && latestRuntime?.latency_ms !== undefined
      ? latestRuntime.latency_ms / 1000
      : metrics?.turn_latency_ms_avg
        ? metrics.turn_latency_ms_avg / 1000
        : 8.7
  const dmExecutionStats = {
    tokens: responseTokenEstimate || 256,
    time: `${executionTimeSeconds.toFixed(1)}s`,
    model: configuredModel || 'Unknown',
    temperature: '0.7',
  }
  const toggleCampaignRail = useCallback(() => {
    if (compactViewport) {
      if (mobileRailOpen) {
        closeMobilePanelsAndRestoreFocus()
        return
      }
      mobilePanelReturnFocusRef.current = campaignRailToggleRef.current
      setAccountMenuOpen(false)
      setBetaNotesOpen(false)
      setMobileInspectorOpen(false)
      setMobileRailOpen(true)
      return
    }
    setRailCollapsed((current) => !current)
  }, [closeMobilePanelsAndRestoreFocus, compactViewport, mobileRailOpen])
  const toggleMobileInspector = useCallback(() => {
    if (mobileInspectorOpen) {
      closeMobilePanelsAndRestoreFocus()
      return
    }
    mobilePanelReturnFocusRef.current = mobileInspectorToggleRef.current
    setAccountMenuOpen(false)
    setBetaNotesOpen(false)
    setMobileRailOpen(false)
    setMobileInspectorOpen(true)
  }, [closeMobilePanelsAndRestoreFocus, mobileInspectorOpen])
  const setMainTabFromRail = useCallback((nextTab: SetStateAction<MainTab>) => {
    setMainTab((current) =>
      typeof nextTab === 'function'
        ? (nextTab as (currentTab: MainTab) => MainTab)(current)
        : nextTab,
    )
    if (compactViewport) {
      closeMobilePanelsAndRestoreFocus()
    }
  }, [closeMobilePanelsAndRestoreFocus, compactViewport])
  const setInspectorTabFromRail = useCallback((nextTab: SetStateAction<InspectorTab>) => {
    setInspectorTab((current) =>
      typeof nextTab === 'function'
        ? (nextTab as (currentTab: InspectorTab) => InspectorTab)(current)
        : nextTab,
    )
    if (compactViewport) {
      mobilePanelReturnFocusRef.current = mobileInspectorToggleRef.current
      setMobileRailOpen(false)
      setMobileInspectorOpen(true)
    }
  }, [compactViewport])
  const fullscreenActive = isFullscreen || fullscreenFallback
  const campaignRailToggleLabel = compactViewport
    ? mobileRailOpen ? 'Close campaign menu' : 'Open campaign menu'
    : railCollapsed ? 'Show campaign rail' : 'Hide campaign rail'
  const mobileInspectorToggleLabel = mobileInspectorOpen
    ? 'Close character panel'
    : 'Open character panel'
  const compactDrawerOpen = compactViewport
    && !showTitleScreen
    && (mobileRailOpen || mobileInspectorOpen)
  const shellClassName = [
    `prototype-shell theme-${theme}`,
    railCollapsed ? 'rail-collapsed' : '',
    fullscreenActive ? 'fullscreen-active' : '',
    safeModeActive ? 'safe-mode-active' : '',
    betaRuntimeNotices.length ? 'runtime-notices-active' : '',
    showTitleScreen ? 'title-screen-active' : '',
    boardViewMode === 'theater' && !showTitleScreen ? 'theater-shell' : '',
    boardViewMode === 'theater' && !showTitleScreen ? 'theater-mode-active' : '',
    mobileRailOpen ? 'mobile-rail-open' : '',
    mobileInspectorOpen ? 'mobile-inspector-open' : '',
  ].filter(Boolean).join(' ')
  return (
    <div
      ref={rootRef}
      className={shellClassName}
    >
      <header
        className="ops-bar"
        inert={compactDrawerOpen ? true : undefined}
      >
        <div className="ops-brand">
          <Flame size={25} fill="currentColor" />
          <strong>AI-DM</strong>
        </div>
        <button
          ref={campaignRailToggleRef}
          type="button"
          className="top-icon"
          aria-label={campaignRailToggleLabel}
          aria-controls={CAMPAIGN_RAIL_ID}
          aria-expanded={compactViewport ? mobileRailOpen : !railCollapsed}
          onClick={toggleCampaignRail}
        >
          <Menu size={21} />
        </button>
        <div className="ops-segment backend-segment">
          <div>
            <strong>Table</strong>
            <StatusDot label={backendStatusLabel} tone={backendStatusTone} />
          </div>
          <span>{tableDisplayName}</span>
          <ExternalLink size={15} />
          <button
            type="button"
            aria-label="Change table access"
            title="Change table access"
            onClick={openWorkspaceAuthDialog}
          >
            <Settings size={16} />
          </button>
        </div>
        <div className="ops-segment compact">
          <div>
            <strong>Provider</strong>
            <select
              className="runtime-select"
              value={configuredProvider}
              disabled={runtimePending || !runtimeProviders.length}
              title={
                latestRuntime
                  ? `Latest completed turn: ${providerLabel(latestRuntime.provider)} / ${latestRuntime.model}`
                  : 'Current runtime provider'
              }
              onChange={(event) => {
                const nextProvider = event.target.value
                const nextOption = runtimeProviders.find((provider) => provider.id === nextProvider)
                const currentModelStillAvailable = nextOption?.models.some(
                  (model) => model.id === configuredModel,
                )
                const nextModel = currentModelStillAvailable
                  ? configuredModel
                  : nextOption?.default_model || nextOption?.models[0]?.id || configuredModel
                void switchRuntime(nextProvider, nextModel)
              }}
            >
              {runtimeProviders.length ? (
                runtimeProviders.map((provider) => (
                  <option
                    key={provider.id}
                    value={provider.id}
                    disabled={!provider.configured}
                  >
                    {provider.label}
                    {provider.configured ? '' : ' (no key)'}
                  </option>
                ))
              ) : (
                <option value={configuredProvider}>{providerLabel(configuredProvider)}</option>
              )}
            </select>
            <span className="runtime-tools" aria-hidden="true">
              <ThinIcon name="cloud" size={13} />
              <ThinIcon name="refresh" size={13} />
            </span>
          </div>
          <StatusDot label={runtimeLabel} tone={runtimeTone} />
          {showProcessLocalDiagnostic ? (
            <span className="runtime-scope-diagnostic" title={runtimeScopeTitle}>
              Process-local · restart workers
            </span>
          ) : null}
        </div>
        <div className="ops-segment compact">
          <div>
            <strong>Model</strong>
            <select
              className="runtime-select"
              value={configuredModel}
              disabled={runtimePending || !runtimeModels.length || !runtime?.configured}
              title="Current runtime model"
              onChange={(event) => {
                void switchRuntime(configuredProvider, event.target.value)
              }}
            >
              {runtimeModels.map((model) => (
                <option key={model.id} value={model.id}>
                  {model.id}
                </option>
              ))}
            </select>
          </div>
          <StatusDot label={runtimeLabel} tone={runtimeTone} />
        </div>
        <div className="ops-segment context-meter">
          <div>
            <strong>Context</strong>
            <span title="Approximate text loaded in the current session log">
              {contextLabel}
            </span>
          </div>
          <div className="meter">
            <span style={{ width: `${contextMeterPercent}%` }} />
          </div>
        </div>
        <div className="ops-segment mini-stat">
          <strong>Session</strong>
          <span>
            {activeSession ? (
              <SessionDuration
                key={activeSession.session_id}
                startedAt={activeSession.created_at}
              />
            ) : 'No session'}
          </span>
        </div>
        <div className="ops-segment mini-stat">
          <Lock size={18} />
          <strong>Auto-Save</strong>
          <StatusDot label="On" />
        </div>
        <div className="ops-segment mini-stat">
          <Radio size={18} />
          <strong>Realtime</strong>
          <StatusDot label={realtimeLabel} tone={realtimeTone} />
        </div>
        <div className="ops-actions">
          <button
            type="button"
            className={`top-icon ${ttsEnabled ? 'selected' : ''} ${ttsSpeaking ? 'speaking' : ''}`}
            aria-label={ttsControlLabel}
            aria-pressed={ttsEnabled}
            data-tts-configuration={ttsConfigurationDataStatus}
            title={`${ttsConfigurationStatus}: ${ttsControlTitle}`}
            onClick={toggleTts}
          >
            {ttsEnabled ? <Volume2 size={18} /> : <VolumeX size={18} />}
          </button>
          {compactViewport ? (
            <>
              <button
                ref={mobileInspectorToggleRef}
                type="button"
                className="top-icon mobile-inspector-toggle"
                aria-label={mobileInspectorToggleLabel}
                aria-controls={INSPECTOR_PANEL_ID}
                aria-expanded={mobileInspectorOpen}
                title={mobileInspectorToggleLabel}
                onClick={toggleMobileInspector}
              >
                <PanelRightOpen size={18} />
              </button>
              <button
                type="button"
                className="top-icon mobile-table-settings-toggle"
                aria-label="Open table settings"
                title="Open table settings"
                onClick={() => {
                  closeMobilePanels()
                  openWorkspaceAuthDialog()
                }}
              >
                <Settings size={18} />
              </button>
            </>
          ) : null}
          <button
            type="button"
            className="top-icon mobile-optional"
            aria-label={fullscreenActive ? 'Exit fullscreen' : 'Enter fullscreen'}
            aria-pressed={fullscreenActive}
            onClick={() => void toggleFullscreen()}
          >
            {fullscreenActive ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
          </button>
          <button
            type="button"
            className="top-icon mobile-optional"
            aria-label="Toggle theme"
            aria-pressed={theme === 'light'}
            onClick={() => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))}
          >
            <Sun size={18} />
          </button>
          <div className="account-menu-wrap" ref={accountMenuRef}>
            <button
              type="button"
              className="top-icon"
              aria-label="Account"
              aria-expanded={accountMenuOpen}
              aria-controls="account-menu"
              onClick={() => {
                setBetaNotesOpen(false)
                setAccountMenuOpen((current) => !current)
              }}
            >
              <UserCircle size={19} />
            </button>
            <button
              type="button"
              className="top-icon small mobile-optional"
              aria-label="More account options"
              aria-expanded={accountMenuOpen}
              aria-controls="account-menu"
              onClick={() => {
                setBetaNotesOpen(false)
                setAccountMenuOpen((current) => !current)
              }}
            >
              <ChevronDown size={16} />
            </button>
            {accountMenuOpen ? (
              <div
                id="account-menu"
                className="account-menu"
                role="group"
                aria-label="Account options"
              >
                <strong role="presentation">{runtimeAccount?.displayName ?? 'No account connected'}</strong>
                <span role="presentation">
                  {runtimeAccount?.workspaceRole
                    ? `${runtimeAccount.workspaceRole} / ${runtimeAccount.workspaceId ?? 'workspace'}`
                    : selectedPlayer?.character_name ?? 'Choose account'}
                </span>
                <button type="button" onClick={() => void refreshCurrentWorkspace()}>
                  Refresh workspace
                </button>
                <button type="button" onClick={openProfileSettingsDialog}>
                  Profile settings
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setSocketReconnectKey((current) => current + 1)
                    setAccountMenuOpen(false)
                  }}
                >
                  Reconnect socket
                </button>
                <button type="button" onClick={openRuntimeSettingsDialog}>
                  Runtime settings
                </button>
                <button
                  ref={betaNotesToggleRef}
                  type="button"
                  aria-expanded={betaNotesOpen}
                  aria-controls="beta-runtime-information"
                  onClick={() => setBetaNotesOpen((current) => !current)}
                >
                  Beta information
                </button>
                {authToken ? (
                  <button type="button" onClick={clearAuthToken}>
                    Sign out
                  </button>
                ) : null}
              </div>
            ) : null}
            {accountMenuOpen && betaNotesOpen ? (
              <BetaRuntimeNotesPanel onClose={closeBetaNotes} />
            ) : null}
          </div>
        </div>
      </header>

      {betaRuntimeNotices.length ? (
        <div
          className="beta-runtime-notices"
          aria-label="Beta runtime notices"
          inert={compactDrawerOpen ? true : undefined}
        >
          {betaRuntimeNotices.map((notice) => (
            <div
              key={notice.id}
              className={`beta-runtime-notice ${notice.tone}`}
            >
              <div className="runtime-notice-copy" role="status" aria-live="polite">
                <strong>{notice.title}</strong>
                <span title={notice.message}>{notice.message}</span>
              </div>
              <details className="runtime-notice-details">
                <summary aria-label={`Read full ${notice.title} notice`}>Details</summary>
                <div className="runtime-notice-full" role="note">
                  <strong>{`Full ${notice.title} notice`}</strong>
                  <p>{`Recovery guidance: ${notice.message}`}</p>
                </div>
              </details>
            </div>
          ))}
        </div>
      ) : null}

      {showTitleScreen ? (
        <TitleScreen
          accountReady={titleScreenAccountReady}
          pending={playNowPending}
          canContinue={titleScreenCanContinue}
          campaignCount={campaigns.length}
          selectedCampaignTitle={campaign?.title ?? null}
          runtimeConfigured={!safeModeActive && Boolean(runtime?.configured)}
          onPlayNow={() => void playNowFromTitleScreen()}
          onLogIn={logInFromTitleScreen}
          onCreateAccount={createAccountFromTitleScreen}
          onCreateCampaign={createCampaignFromTitleScreen}
          onContinue={continueFromTitleScreen}
        />
      ) : null}

      <div
        className="workspace-surfaces"
        aria-hidden={showTitleScreen ? true : undefined}
        inert={showTitleScreen ? true : undefined}
      >
        <button
          type="button"
          className="mobile-panel-scrim"
          aria-hidden="true"
          tabIndex={-1}
          onClick={closeMobilePanelsAndRestoreFocus}
        />

      <CampaignRail
        inert={compactViewport && (!mobileRailOpen || modalOpen)}
        modal={compactViewport && mobileRailOpen && !showTitleScreen && !modalOpen}
        onRequestClose={closeMobilePanelsAndRestoreFocus}
        backendStatus={health?.status ?? null}
        campaignTitle={campaign?.title ? truncateText(campaign.title, 12) : null}
        campaignCards={campaignCards}
        sessionCards={sessionCards}
        campaignFilter={campaignFilter}
        setCampaignFilter={setCampaignFilter}
        selectedCampaignId={selectedCampaignId}
        selectedSessionId={selectedSessionId}
        loadingCampaignId={loadingCampaignId}
        sessionLoading={sessionLoading}
        workspaceLoading={workspaceLoading}
        mainTab={mainTab}
        setMainTab={setMainTabFromRail}
        inspectorTab={inspectorTab}
        setInspectorTab={setInspectorTabFromRail}
        canUseOperatorTools={canUseOperatorTools}
        canManageCampaign={Boolean(campaign)}
        canManageSession={Boolean(activeSession)}
        canOpenCampaignArchive={health?.status === 'ok'}
        canOpenSessionArchive={Boolean(selectedCampaignId)}
        selectionLocked={sendPending || dmResponseBlocking || queuedActionRetryable || Boolean(diceRoll)}
        onRenameCampaign={openRenameCampaignDialog}
        onArchiveCampaign={openCampaignArchiveManager}
        onDeleteCampaign={openDeleteCampaignDialog}
        onCreateCampaign={openCreateCampaignDialog}
        onImportCampaignPack={openCampaignPackImportDialog}
        onManageWorlds={openWorldManagerDialog}
        onRenameSession={openRenameSessionDialog}
        onArchiveSession={openSessionArchiveManager}
        onDeleteSession={openDeleteSessionDialog}
        onStartSession={startSession}
        onSelectCampaign={(campaignId) => {
          if (
            campaignId !== selectedCampaignId &&
            (sendPending || dmResponseBlocking || queuedActionRetryable || Boolean(diceRoll))
          ) {
            pushError('validation', 'Wait for the current turn to finish or dismiss its retry before changing campaigns.')
            return
          }
          if (campaignId !== selectedCampaignId) {
            setSelectedCampaignId(campaignId)
          }
          setMainTab('turns')
          closeMobilePanelsAndRestoreFocus()
        }}
        onSelectSession={(sessionId) => {
          if (
            sessionId !== selectedSessionId &&
            (sendPending || dmResponseBlocking || queuedActionRetryable || Boolean(diceRoll))
          ) {
            pushError('validation', 'Wait for the current turn to finish or dismiss its retry before changing sessions.')
            return
          }
          if (sessionId !== selectedSessionId) {
            setSelectedSessionId(sessionId)
            setOptimisticEntries([])
            setStreamingTurn(null)
          }
          setMainTab('turns')
          closeMobilePanelsAndRestoreFocus()
        }}
        lastSyncLabel={formatShortAge(lastSync)}
        onRefreshWorkspace={() => void refreshCurrentWorkspace()}
        errors={errors}
      />

      <div
        className="workspace-main-board-isolation"
        inert={compactDrawerOpen ? true : undefined}
      >
        <SessionBoard
        activeSessionTitle={activeSessionTitle}
        campaignTitle={campaignTitle}
        sessionId={activeSessionId}
        playerId={selectedPlayerDetailId}
        showSceneMusicPlayer={showSceneMusicPlayer}
        duckMusicForNarration={ttsSpeaking}
        sceneMusicSyncState={sceneMusicSyncState}
        sceneState={sceneState}
        onSceneMusicControl={updateSceneMusicControl}
        contentSettings={contentSettings}
        contentSettingsPending={contentSettingsPending}
        canUseOperatorTools={canUseOperatorTools}
        canEditContentSettings={canUseOperatorTools}
        onContentRatingChange={updateContentRating}
        onContentToneTagsChange={updateContentToneTags}
        onBoardViewModeChange={setBoardViewMode}
        directorCommentary={directorCommentary}
        sessionRecap={sessionRecap}
        onSpeakSessionRecap={speakSessionRecap}
        workspaceLoading={workspaceLoading}
        sessionLoading={sessionLoading}
        mainTab={mainTab}
        setMainTab={setMainTab}
        showMobilePresenceStrip={compactViewport}
        activePlayers={activePlayersWithHealth}
        downloadCampaignChronicle={downloadCampaignChronicle}
        downloadSessionChronicle={downloadSessionChronicle}
        downloadSessionJson={downloadSessionJson}
        sessionImportPending={sessionImportPending}
        sessionImportInputRef={sessionImportInputRef}
        importSessionJson={importSessionJson}
        shareSession={shareSession}
        sessionMenuRef={sessionMenuRef}
        sessionMenuOpen={sessionMenuOpen}
        setSessionMenuOpen={setSessionMenuOpen}
        refreshCurrentWorkspace={refreshCurrentWorkspace}
        activeSession={activeSession}
        openRenameSessionDialog={openRenameSessionDialog}
        openDeleteSessionDialog={openDeleteSessionDialog}
        notesCount={memorySnippets.length}
        turnFeedRef={turnFeedRef}
        updateJumpToLatestVisibility={updateJumpToLatestVisibility}
        sessionLogHasMore={sessionLogHasMore}
        olderLogLoading={olderLogLoading}
        loadOlderSessionLog={loadOlderSessionLog}
        turnRows={turnRows}
        dismissTimelineEntry={dismissTimelineEntry}
        reportedBadTurnIds={reportedBadTurnIds}
        reportingBadTurnIds={reportingBadTurnIds}
        reportBadTurn={reportBadTurn}
        ratedTurnQualityIds={ratedTurnQualityIds}
        ratingTurnQualityIds={ratingTurnQualityIds}
        submitTurnQuality={submitTurnQuality}
        expandedTurnIds={expandedTurnIds}
        setExpandedTurnIds={setExpandedTurnIds}
        selectedPlayer={selectedPlayer}
        currentResponseEntry={currentResponseEntry}
        latestDmText={latestDmText}
        sendPending={sendPending}
        streamingTurnActive={dmResponseBlocking}
        pendingRollNotice={pendingRollNotice}
        onPreparePendingRoll={preparePendingRoll}
        turnRecoveryGate={turnRecoveryGate}
        turnRecoveryPending={turnRecoveryPending}
        turnRecoveryError={turnRecoveryError}
        turnRecoverySuccess={turnRecoverySuccess}
        onResolveTurnRecovery={resolveTurnRecovery}
        combatState={worldStatePanel.combat}
        worldState={worldStatePanel}
        gameplayControls={gameplayControls}
        operationalError={latestPlayError?.message}
        onRecoverOperationalError={async () => {
          setSocketReconnectKey((current) => current + 1)
          await refreshCurrentWorkspace()
          if (latestPlayError) {
            setErrors((current) => current.filter((error) => error.id !== latestPlayError.id))
          }
        }}
        dmExecutionStats={dmExecutionStats}
        welcomeText={welcomeText}
        showJumpToLatest={showJumpToLatest}
        scrollTurnFeedToLatest={scrollTurnFeedToLatest}
        questTitle={questTitle}
        sessionState={sessionState}
        campaign={campaign}
        recentMemory={recentMemory}
        clarificationRequest={clarificationRequest}
        resolveClarification={resolveClarification}
        onStartAdventure={startAdventure}
        actionComposerProps={{
          actionInputRef,
          actionText,
          adminPasscode,
          adminToolsUnlocked,
          canUseOperatorTools,
          setActionText,
          setAdminPasscode,
          selectedCharacterName: selectedPlayer?.character_name ?? null,
          selectedPlayerId,
          activePlayers,
          composerMode,
          selectedDie,
          sendPending,
          turnControl,
          turnControlStatusLabel,
          selectedPlayerHasTurn,
          queuedActionText,
          queuedActionRetryable,
          retryRecoverableSubmission,
          clearQueuedAction,
          updateTurnControl,
          ttsEnabled,
          ttsStatusClassName: effectiveTtsStatus,
          ttsStatusLabel,
          ttsLatencyLabel,
          canStopTts,
          stopTtsAudio,
          submitAction,
          toggleAdminTools,
          startDiceRoll,
          preloadDiceRollDialog,
          applyComposerMode,
          updateSelectedDie,
          rollMode,
          setRollMode,
          rollReason,
          setRollReason,
          pendingRollOptions,
          rollTargetPendingTurnId,
          setRollTargetPendingTurnId,
          spellName,
          selectedAbility,
          selectedAbilityKey,
          abilityOptions,
          updateRollAbilityKey,
          updateSpellName,
          interactionTargets,
          selectedInteractionTarget,
          selectedInteractionTargetId,
          selectedInteractionType,
          setSelectedInteractionTargetId,
          setSelectedInteractionType,
          selectedInventoryAction,
          selectedItem,
          itemDraftName,
          itemQuantity,
          itemCostGold,
          itemOptions,
          setSelectedItemId,
          setItemQuantity,
          updateActionText,
          updateSelectedInventoryAction,
          updateItemDraftName,
          updateItemCostGold,
        }}
        />
      </div>

      <InspectorPanel
        inert={compactViewport && (!mobileInspectorOpen || modalOpen)}
        modal={compactViewport && mobileInspectorOpen && !showTitleScreen && !modalOpen}
        onRequestClose={closeMobilePanelsAndRestoreFocus}
        inspectorTab={inspectorTab}
        setInspectorTab={setInspectorTab}
        setMainTab={setMainTab}
        baseUrl={baseUrl}
        auth={auth}
        canUseOperatorTools={canUseOperatorTools}
        displayCharacter={displayCharacter}
        characterAvatarSrc={characterAvatarSrc}
        xpProgress={xpProgress}
        playersCount={players.length}
        activePlayers={activePlayersWithHealth}
        selectedPlayerId={selectedPlayerId}
        loadPlayer={openCharacterJoinDialog}
        createDefaultPlayer={promptCreatePlayer}
        editSelectedPlayer={openPlayerEditDialog}
        deleteSelectedPlayer={openPlayerDeleteDialog}
        selectedCampaignId={selectedCampaignId}
        selectedSessionId={activeSessionId}
        createPlayerPending={createPlayerPending}
        statBlock={statBlock}
        spellbook={spellbook}
        spellResources={spellResources}
        characterTraits={characterTraits}
        inventoryRows={inventoryRows}
        inventoryWeightLabel={inventoryWeightLabel}
        inventoryGoldLabel={inventoryGoldLabel}
        equipmentPendingItemKey={equipmentPendingItemKey}
        toggleInventoryEquipment={toggleInventoryEquipment}
        memorySnippetCount={memorySnippets.length}
        visibleRecentMemory={visibleRecentMemory}
        worldStatePanel={worldStatePanel}
        mapPanelTitle={mapPanelTitle}
        mapDescription={mapDescription}
        mapMeta={mapMeta}
        questTitle={questTitle}
        selectedSegment={selectedSegment}
        maps={viewerMaps}
        createDefaultMap={createDefaultMap}
        campaign={campaign}
        createMapPending={createMapPending}
        mapManagementForm={mapManagementForm}
        setMapManagementForm={setMapManagementForm}
        mapSavePending={mapSavePending}
        saveMapManagement={saveMapManagement}
        segments={segments}
        segmentSavePending={segmentSavePending}
        activateSegment={activateSegment}
        segmentDeletePendingId={segmentDeletePendingId}
        deleteSegment={deleteSegment}
        segmentManagementForm={segmentManagementForm}
        setSegmentManagementForm={setSegmentManagementForm}
        createSegment={createSegment}
        campaignPackSnapshot={turnControlSnapshot}
        campaignPackControlPending={campaignPackControlPending}
        controlCampaignPackProgress={controlCampaignPackProgress}
      />

      {sharedRollNotice ? (
        <aside
          className="shared-roll-notice"
          role="status"
          aria-live="polite"
          aria-hidden={compactDrawerOpen ? true : undefined}
        >
          <span>
            {activePlayers.find((player) => player.id === sharedRollNotice.playerId)?.character_name
              ?? `Player ${sharedRollNotice.playerId}`}
          </span>
          <strong>{diceRollMessage(sharedRollNotice.roll)}</strong>
        </aside>
      ) : null}
      </div>

      {diceRoll ? (
        <div
          className="modal-backdrop dice-roll-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeDiceRoll()
            }
          }}
        >
          <Suspense
            fallback={
              <section className="dice-dialog dice-loading" role="status" aria-live="polite">
                <div className="dice-loading-body">
                  <strong>Preparing dice...</strong>
                  <span>{diceRoll.die.toUpperCase()}</span>
                </div>
              </section>
            }
          >
            <DiceRollDialog
              die={diceRoll.die}
              result={diceRoll.roll?.kept ?? null}
              rolls={diceRoll.roll?.rolls ?? null}
              mode={diceRoll.roll?.mode ?? 'normal'}
              modifier={diceRoll.roll?.modifier ?? null}
              total={diceRoll.roll?.total ?? null}
              provenance={diceRoll.provenance}
              targetLabel={diceRoll.targetLabel}
              rollKey={diceRoll.rollKey}
              status={diceRoll.status}
              dialogRef={modalDialogRef}
              error={diceRoll.error}
              onCancel={closeDiceRoll}
              onComplete={completeDiceRoll}
              onRetry={retryDiceRoll}
            />
          </Suspense>
        </div>
      ) : null}

      <RuntimeSettingsDialog
        defaultBaseUrl={DEFAULT_BASE_URL}
        dialogRef={modalDialogRef}
        error={runtimeSettingsError}
        form={runtimeSettingsForm}
        legacyPasswordSetupRequired={legacyPasswordSetupRequired}
        mode={runtimeSettingsMode}
        onAuthIntentChange={setRuntimeAuthIntent}
        onAuthStepChange={setRuntimeAuthStep}
        onClose={closeRuntimeSettingsDialog}
        onErrorChange={setRuntimeSettingsError}
        onFormChange={setRuntimeSettingsForm}
        onLegacyPasswordSetupRequiredChange={setLegacyPasswordSetupRequired}
        onOpenSavedWorkspaceDelete={openSavedWorkspaceDeleteDialog}
        onSelectSavedWorkspace={selectSavedWorkspace}
        onSubmit={submitRuntimeSettings}
        onWorkspaceActionChange={setRuntimeWorkspaceAction}
        onWorkspaceCreateAccessModeChange={setRuntimeWorkspaceCreateAccessMode}
        onWorkspaceJoinMethodChange={setRuntimeWorkspaceJoinMethod}
        open={runtimeSettingsOpen}
        runtimeAccount={runtimeAccount}
        runtimeAuthIntent={runtimeAuthIntent}
        runtimeAuthStep={runtimeAuthStep}
        runtimeCreatedWorkspaceToken={runtimeCreatedWorkspaceToken}
        runtimeWorkspaceAction={runtimeWorkspaceAction}
        runtimeWorkspaceCreateAccessMode={runtimeWorkspaceCreateAccessMode}
        runtimeWorkspaceJoinMethod={runtimeWorkspaceJoinMethod}
        workspaceId={workspaceId}
      />

      {savedWorkspaceDeleteDialog ? (
        <SavedWorkspaceDeleteDialog
          deletesTable={savedWorkspaceDeleteDialogDeletesTable}
          dialog={savedWorkspaceDeleteDialog}
          dialogRef={modalDialogRef}
          onClose={closeSavedWorkspaceDeleteDialog}
          onConfirm={() => void submitSavedWorkspaceDeleteDialog()}
        />
      ) : null}

      {shareSessionUrl ? (
        <ShareSessionDialog
          dialogRef={modalDialogRef}
          onClose={closeShareSessionDialog}
          onCopy={copyShareSessionUrl}
          url={shareSessionUrl}
        />
      ) : null}

      <ProfileSettingsDialog
        canEditCharacter={Boolean(selectedPlayer)}
        canSwitchCharacter={Boolean(selectedCampaignId)}
        dialogRef={modalDialogRef}
        onBackendSettings={() => {
          setProfileSettingsOpen(false)
          openRuntimeSettingsDialog()
        }}
        onClose={closeProfileSettingsDialog}
        onEditCharacter={openPlayerEditDialog}
        onReconnectRealtime={() => {
          setSocketReconnectKey((current) => current + 1)
          closeProfileSettingsDialog()
        }}
        onRefreshWorkspace={() => void refreshCurrentWorkspace()}
        onSignOut={clearAuthToken}
        onSwitchCharacter={openCharacterJoinDialog}
        open={profileSettingsOpen}
        signedIn={Boolean(authToken)}
        summary={{
          account: runtimeAccount?.displayName ?? selectedPlayer?.name ?? 'No account connected',
          table: runtimeAccount?.workspaceId
            ? `${runtimeAccount.workspaceId}${runtimeAccount.workspaceRole ? ` / ${runtimeAccount.workspaceRole}` : ''}`
            : workspaceId
              ? workspaceId
              : workspaceToken
                ? 'Token set'
                : 'No table token',
          character: displayCharacter.name,
          campaign: campaign?.title ?? 'No campaign selected',
          session: activeSessionName,
          backend: backendDisplayUrl,
          narration: `${ttsStatusLabel}${ttsLatencyLabel ? ` / ${ttsLatencyLabel}` : ''}`,
        }}
      />

      <CharacterJoinDialog
        campaignTitle={campaign?.title ?? null}
        dialogRef={modalDialogRef}
        onClose={closeCharacterJoinDialog}
        onCreateCharacter={createCharacterFromJoinDialog}
        onJoinPlayer={joinAsExistingPlayer}
        open={characterJoinDialogOpen}
        players={players}
        portraitSrcForPlayer={characterPortraitSrc}
      />

      {campaignArchiveDialog ? (
        <Suspense fallback={<ModalLoading dialogRef={modalDialogRef} label="Opening campaign archive…" />}>
          <CampaignArchiveDialog
            campaign={campaign}
            dialog={campaignArchiveDialog}
            dialogRef={modalDialogRef}
            onArchiveSelected={() => void archiveSelectedCampaignFromManager()}
            onClose={closeCampaignArchiveDialog}
            onRestore={(campaignId) => void restoreCampaignFromArchive(campaignId)}
            worldNameById={worldNameById}
          />
        </Suspense>
      ) : null}

      {sessionArchiveDialog ? (
        <Suspense fallback={<ModalLoading dialogRef={modalDialogRef} label="Opening session archive…" />}>
          <SessionArchiveDialog
            activeSession={activeSession}
            campaign={campaign}
            dialog={sessionArchiveDialog}
            dialogRef={modalDialogRef}
            onArchiveSelected={() => void archiveSelectedSessionFromManager()}
            onClose={closeSessionArchiveDialog}
            onRestore={(sessionId) => void restoreSessionFromArchive(sessionId)}
            selectedCampaignId={selectedCampaignId}
          />
        </Suspense>
      ) : null}

      {campaignPackImportOpen ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeCampaignPackImportDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog campaign-pack-import-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="campaign-pack-import-title"
          >
            <header>
              <div>
                <span>Campaign Pack</span>
                <h2 id="campaign-pack-import-title">Import Campaign Pack</h2>
              </div>
              <button
                type="button"
                aria-label="Close campaign pack import"
                onClick={closeCampaignPackImportDialog}
              >
                <X size={18} />
              </button>
            </header>
            <Suspense fallback={<div role="status">Loading campaign pack tools...</div>}>
              <CampaignPackImportDialog
                auth={auth}
                baseUrl={baseUrl}
                onClose={closeCampaignPackImportDialog}
                onImported={handleCampaignPackImported}
                pushError={pushError}
              />
            </Suspense>
          </section>
        </div>
      ) : null}

      {campaignChooserOpen ? (
        <Suspense fallback={<ModalLoading dialogRef={modalDialogRef} label="Opening your campaigns…" />}>
          <CampaignChooserDialog
            campaigns={campaigns}
            canCreateCampaign={canUseOperatorTools}
            dialogRef={modalDialogRef}
            onChoose={chooseCampaign}
            onClose={closeCampaignChooserDialog}
            onCreate={createCampaignFromChooser}
            worldNameById={worldNameById}
          />
        </Suspense>
      ) : null}

      {playerEditDialog ? (
        <Suspense fallback={<ModalLoading dialogRef={modalDialogRef} label="Opening character editor…" />}>
          <PlayerEditDialog
            auth={auth}
            baseUrl={baseUrl}
            dialog={playerEditDialog}
            dialogRef={modalDialogRef}
            onClose={closePlayerEditDialog}
            onSubmit={(event) => void submitPlayerEditDialog(event)}
            setDialog={setPlayerEditDialog}
          />
        </Suspense>
      ) : null}

      {playerDeleteDialog ? (
        <PlayerDeleteDialog
          dialog={playerDeleteDialog}
          dialogRef={modalDialogRef}
          onClose={closePlayerDeleteDialog}
          onConfirm={() => void submitPlayerDeleteDialog()}
        />
      ) : null}

      {campaignActionDialog ? (
        <CampaignActionDialog
          dialog={campaignActionDialog}
          dialogRef={modalDialogRef}
          onClose={closeCampaignActionDialog}
          onDescriptionChange={(description) =>
            setCampaignActionDialog((current) =>
              current ? { ...current, description, error: '' } : current,
            )
          }
          onSubmit={(event) => void submitCampaignActionDialog(event)}
          onTitleChange={(title) =>
            setCampaignActionDialog((current) =>
              current ? { ...current, title, error: '' } : current,
            )
          }
        />
      ) : null}

      {sessionActionDialog ? (
        <SessionActionDialog
          dialog={sessionActionDialog}
          dialogRef={modalDialogRef}
          onClose={closeSessionActionDialog}
          onNameChange={(name) =>
            setSessionActionDialog((current) =>
              current ? { ...current, name, error: '' } : current,
            )
          }
          onSubmit={(event) => void submitSessionActionDialog(event)}
        />
      ) : null}

      {worldManagerOpen ? (
        <WorldManagerDialog
          deleteDialogOpen={worldDeleteDialog !== null}
          dialogRef={modalDialogRef}
          form={worldForm}
          onClose={closeWorldManagerDialog}
          onEditWorld={editWorld}
          onOpenDelete={openWorldDeleteDialog}
          onResetForm={resetWorldForm}
          onSubmit={(event) => void submitWorldForm(event)}
          setForm={setWorldForm}
          worlds={worldSelectOptions}
        />
      ) : null}

      {worldDeleteDialog ? (
        <WorldDeleteDialog
          dialog={worldDeleteDialog}
          dialogRef={modalDialogRef}
          onClose={closeWorldDeleteDialog}
          onDelete={() => void submitWorldDeleteDialog()}
          onForceDelete={() => void submitWorldDeleteDialog(true)}
        />
      ) : null}

      {createCampaignOpen ? (
        <Suspense fallback={<ModalLoading dialogRef={modalDialogRef} label="Preparing campaign creation…" />}>
          <CreateCampaignDialog
            defaultWorldId={campaignWorldId}
            dialogRef={modalDialogRef}
            error={createCampaignError}
            form={createCampaignForm}
            onClose={closeCreateCampaignDialog}
            onSubmit={(event) => void submitCreateCampaign(event)}
            packOptions={createCampaignPackOptions}
            packOptionsPending={createCampaignPackOptionsPending}
            pending={createCampaignPending}
            setForm={setCreateCampaignForm}
            worldSelectOptions={worldSelectOptions}
          />
        </Suspense>
      ) : null}
    </div>
  )
}

function App() {
  const {
    confirmPendingBackendTrust,
    pendingBackendTrust,
    rejectPendingBackendTrust,
  } = useShareBackendTrust(DEFAULT_BASE_URL)
  const dialogRef = useRef<HTMLElement | null>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)

  useModalFocusTrap({
    activeKey: pendingBackendTrust ? 'backend-trust' : null,
    dialogRef,
    onClose: rejectPendingBackendTrust,
    returnFocusRef,
  })

  if (pendingBackendTrust) {
    return (
      <BackendTrustDialog
        backend={pendingBackendTrust}
        dialogRef={dialogRef}
        onConfirm={confirmPendingBackendTrust}
        onReject={rejectPendingBackendTrust}
      />
    )
  }

  return <AIDMApp />
}

export default App
