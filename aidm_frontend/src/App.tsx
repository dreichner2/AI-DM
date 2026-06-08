import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
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
  Radio,
  Settings,
  Sun,
  UserCircle,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react'
import { StatusDot, ThinIcon } from './AppChrome'
import { CampaignRail, type CampaignCard, type SessionCard } from './CampaignRail'
import {
  InspectorPanel,
  type InspectorTab,
} from './InspectorPanel'
import { ApiClientError, apiFetch } from './api'
import {
  POINT_BUY_ABILITIES,
  POINT_BUY_BUDGET,
  abilityModifier,
  clampPointBuyScore,
  pointBuySpent,
} from './characterStats'
import { SessionBoard, type MainTab } from './SessionBoard'
import {
  abilityOptionsFromStatBlock,
  buildMapMeta,
  buildTimeline,
  canonFactsFromMemorySnippets,
  formatCompactNumber,
  inventoryCapacity,
  inventoryGoldLabel as buildInventoryGoldLabel,
  inventoryWeightLabel as buildInventoryWeightLabel,
  isRecord,
  itemOptionsFromInventory,
  memorySnippetRecords,
  normalizeInventory,
  normalizeStats,
  normalizeXp,
  pendingRollOptionsFromTimeline,
  stringValue,
  truncateText,
} from './gameSelectors'
import { profileIconSrcForCharacter } from './profileIcons'
import './App.css'
import type {
  ActivePlayer,
  BetaSummary,
  Campaign,
  ClarificationRequest,
  Health,
  LlmRuntimeConfig,
  Player,
  PlayerDetail,
  SessionSummary,
  StreamingTurn,
  TimelineEntry,
  TtsRuntimeConfig,
  World,
} from './types'
import { useCampaignActions, type CampaignActionDialogState } from './useCampaignActions'
import { useComposerActions } from './useComposerActions'
import { usePlayerProfileActions } from './usePlayerProfileActions'
import { useSessionActions, type SessionActionDialogState } from './useSessionActions'
import { useSessionSocket } from './useSessionSocket'
import { useRuntimeSettings } from './useRuntimeSettings'
import { useTtsNarration } from './useTtsNarration'
import { useWorldMapSegmentActions } from './useWorldMapSegmentActions'
import { useWorkspaceQueries, type CampaignSessionMeta } from './useWorkspaceQueries'
import { useWorkspaceStore } from './useWorkspaceStore'

const DEFAULT_BASE_URL = import.meta.env.VITE_AIDM_API_BASE_URL ?? ''

const loadDiceRollDialog = () => import('./DiceRollDialog')
const DiceRollDialog = lazy(loadDiceRollDialog)

function preloadDiceRollDialog() {
  void loadDiceRollDialog()
}

type ThemeMode = 'dark' | 'light'

function isEditableShortcutTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

function focusableDialogElements(container: HTMLElement) {
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

type UiErrorCategory = 'connection' | 'tts' | 'validation' | 'persistence' | 'workspace' | 'system'

type UiError = {
  id: string
  category: UiErrorCategory
  message: string
  createdAt: number
}

type WorldFormState = {
  mode: 'create' | 'edit'
  worldId: number | null
  name: string
  description: string
  error: string
  pending: boolean
}

type WorldDeleteDialogState = {
  world: World
  error: string
  pending: boolean
  canForce: boolean
} | null

type CampaignArchiveDialogState = {
  items: Campaign[]
  loading: boolean
  error: string
  pendingId: number | null
} | null

type SessionArchiveDialogState = {
  items: SessionSummary[]
  loading: boolean
  error: string
  pendingId: number | null
} | null

const emptyWorldForm: WorldFormState = {
  mode: 'create',
  worldId: null,
  name: '',
  description: '',
  error: '',
  pending: false,
}

function isUnauthorizedError(error: unknown) {
  return error instanceof ApiClientError && error.status === 401
}

function isNotFoundError(error: unknown) {
  return error instanceof ApiClientError && error.status === 404
}

function isAuthTokenWorkspaceError(error: UiError) {
  return error.category === 'workspace' && error.message.includes('Missing or invalid bearer token')
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

function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [llmConfig, setLlmConfig] = useState<LlmRuntimeConfig | null>(null)
  const [ttsConfig, setTtsConfig] = useState<TtsRuntimeConfig | null>(null)
  const [runtimePending, setRuntimePending] = useState(false)
  const [campaignSessionMeta, setCampaignSessionMeta] = useState<
    Record<number, CampaignSessionMeta>
  >({})
  const [metrics, setMetrics] = useState<BetaSummary | null>(null)
  const [socketStatus, setSocketStatus] = useState('idle')
  const [activePlayers, setActivePlayers] = useState<ActivePlayer[]>([])
  const [sendPending, setSendPending] = useState(false)
  const [errors, setErrors] = useState<UiError[]>([])
  const [optimisticEntries, setOptimisticEntries] = useState<TimelineEntry[]>([])
  const [streamingTurn, setStreamingTurn] = useState<StreamingTurn | null>(null)
  const [turnStatuses, setTurnStatuses] = useState<Record<number, string>>({})
  const [clarificationRequest, setClarificationRequest] = useState<ClarificationRequest | null>(null)
  const [mainTab, setMainTab] = useState<MainTab>('turns')
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('party')
  const [campaignFilter, setCampaignFilter] = useState('')
  const [expandedTurnIds, setExpandedTurnIds] = useState<Set<string>>(() => new Set())
  const [showJumpToLatest, setShowJumpToLatest] = useState(false)
  const [sessionMenuOpen, setSessionMenuOpen] = useState(false)
  const [accountMenuOpen, setAccountMenuOpen] = useState(false)
  const [railCollapsed, setRailCollapsed] = useState(false)
  const [isFullscreen, setIsFullscreen] = useState(false)
  const [fullscreenFallback, setFullscreenFallback] = useState(false)
  const [theme, setTheme] = useState<ThemeMode>(() =>
    localStorage.getItem('aidm:theme') === 'light' ? 'light' : 'dark',
  )
  const [worlds, setWorlds] = useState<World[]>([])
  const [worldManagerOpen, setWorldManagerOpen] = useState(false)
  const [worldForm, setWorldForm] = useState<WorldFormState>(emptyWorldForm)
  const [worldDeleteDialog, setWorldDeleteDialog] = useState<WorldDeleteDialogState>(null)
  const [profileSettingsOpen, setProfileSettingsOpen] = useState(false)
  const [campaignArchiveDialog, setCampaignArchiveDialog] =
    useState<CampaignArchiveDialogState>(null)
  const [sessionArchiveDialog, setSessionArchiveDialog] =
    useState<SessionArchiveDialogState>(null)
  const [campaignChooserOpen, setCampaignChooserOpen] = useState(false)
  const [campaignChooserDismissedKey, setCampaignChooserDismissedKey] = useState('')
  const [characterJoinDialogOpen, setCharacterJoinDialogOpen] = useState(false)
  const [socketReconnectKey, setSocketReconnectKey] = useState(0)
  const [nowMs, setNowMs] = useState(() => Date.now())
  const resetRuntimeState = useCallback(() => {
    setHealth(null)
    setLlmConfig(null)
    setTtsConfig(null)
    setMetrics(null)
    setWorlds([])
  }, [])
  const reconnectSocket = useCallback(() => {
    setSocketReconnectKey((current) => current + 1)
  }, [])
  const {
    authToken,
    baseUrl,
    clearAuthToken: clearRuntimeAuthToken,
    closeRuntimeSettings,
    openAuthTokenPrompt,
    openRuntimeSettings,
    runtimeSettingsError,
    runtimeSettingsForm,
    runtimeSettingsMode,
    runtimeSettingsOpen,
    setRuntimeSettingsForm,
    submitRuntimeSettings,
  } = useRuntimeSettings({
    defaultBaseUrl: DEFAULT_BASE_URL,
    reconnectSocket,
    resetRuntimeState,
  })
  const rootRef = useRef<HTMLDivElement | null>(null)
  const accountMenuRef = useRef<HTMLDivElement | null>(null)
  const sessionMenuRef = useRef<HTMLDivElement | null>(null)
  const sessionImportInputRef = useRef<HTMLInputElement | null>(null)
  const modalDialogRef = useRef<HTMLElement | null>(null)
  const dialogReturnFocusRef = useRef<HTMLElement | null>(null)
  const closeCurrentDialogRef = useRef<() => void>(() => undefined)
  const promptedCharacterCampaignIdsRef = useRef<Set<number>>(new Set())
  const selectedPlayerByCampaignRef = useRef<Record<number, number>>({})
  const lastSelectedCampaignIdRef = useRef<number | null>(null)
  const actionInputRef = useRef<HTMLTextAreaElement | null>(null)
  const turnFeedRef = useRef<HTMLElement | null>(null)
  const submitActionRef = useRef<(() => void) | null>(null)
  const toggleFullscreenRef = useRef<(() => Promise<void>) | null>(null)
  const socketRef = useRef<Socket | null>(null)
  const playerRequestRef = useRef(0)
  const sessionActionDialogRef = useRef<SessionActionDialogState>(null)
  const campaignActionDialogRef = useRef<CampaignActionDialogState>(null)

  const auth = authToken.trim()
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
  const clearResolvedOperationalErrors = useCallback(() => {
    setErrors((current) =>
      current.filter((item) => {
        if (
          item.category === 'connection' &&
          item.message.startsWith('Socket connection failed:')
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
    () => players.find((player) => player.player_id === selectedPlayerId) ?? null,
    [players, selectedPlayerId],
  )
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
  const statBlock = normalizeStats(
    playerDetail?.stats,
    playerDetail?.character_sheet,
    selectedPlayer?.level ?? null,
  )
  const inventoryRows = normalizeInventory(playerDetail?.inventory)
  const abilityOptions = abilityOptionsFromStatBlock(statBlock)
  const itemOptions = itemOptionsFromInventory(inventoryRows)
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
  const pendingRollOptions = useMemo(() => pendingRollOptionsFromTimeline(timeline), [timeline])

  const activeSession =
    sessions.find((session) => session.session_id === selectedSessionId) ?? null
  const activeSessionName = activeSession
    ? sessionDisplayName(activeSession, campaign?.world_id ?? selectedCampaignId)
    : 'No session selected'
  const latestDmEntry =
    [...timeline].reverse().find((entry) => entry.role === 'dm') ?? null
  const currentResponseEntry =
    timeline.find((entry) => entry.streaming) ?? latestDmEntry
  const turnRows = timeline.filter((entry) => entry.id !== currentResponseEntry?.id)
  const speakableDmEntry =
    currentResponseEntry?.role === 'dm' && !currentResponseEntry.streaming
      ? currentResponseEntry
      : null
  const welcomeText = activeSession
    ? `Welcome to ${activeSessionName}. Choose an opening move and the DM will begin the scene.`
    : 'Start or select a session to begin play.'

  const latestDmText =
    currentResponseEntry?.text ||
    sessionState?.rolling_summary ||
    welcomeText

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
    interactionTargets,
    rollMode,
    rollModifier,
    rollReason,
    rollTargetPendingTurnId,
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
    setActionText,
    setAdminPasscode,
    setSelectedInteractionTargetId,
    setSelectedInteractionType,
    setItemQuantity,
    setRollMode,
    setRollModifier,
    setRollReason,
    setRollTargetPendingTurnId,
    setSelectedItemName,
    updateRollAbilityKey,
    updateSelectedInventoryAction,
    updateItemDraftName,
    updateItemCostGold,
    startDiceRoll,
    submitAction,
    toggleAdminTools,
    updateSelectedDie,
  } = useComposerActions({
    activePlayers,
    abilityOptions,
    campaign,
    itemOptions,
    pendingRollOptions,
    players,
    selectedCampaignId,
    selectedPlayer,
    selectedPlayerId,
    selectedSessionId,
    sendPending,
    setOptimisticEntries,
    setSendPending,
    socketRef,
    stopTtsAudio,
    streamingTurn,
    pushError,
  })

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
    sessions,
    selectedCampaignId,
    selectedSessionId,
    sessionLogCursor,
    sessionLogHasMore,
    setHealth,
    setMetrics,
    setLlmConfig,
    setTtsConfig,
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
    onUnauthorized: openAuthTokenPrompt,
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
    maps,
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
    maps,
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
    [auth, baseUrl, pushError],
  )

  useEffect(() => {
    refreshRoot()
  }, [refreshRoot])

  useEffect(() => {
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    localStorage.setItem('aidm:theme', theme)
  }, [theme])

  useEffect(() => {
    const currentMap = maps[0]
    setMapManagementForm({
      title: currentMap?.title ?? (campaign ? `${campaign.title} Map` : ''),
      description: currentMap?.description ?? campaign?.location ?? '',
    })
  }, [campaign, maps, setMapManagementForm])

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
    closePlayerDeleteDialog,
    closeShareSessionDialog,
    closePlayerEditDialog,
    closeCharacterJoinDialog,
    closeProfileSettingsDialog,
    closeRuntimeSettingsDialog,
    closeWorldManagerDialog,
    closeWorldDeleteDialog,
    closeCampaignArchiveDialog,
    closeSessionArchiveDialog,
    closeCampaignChooserDialog,
    campaignArchiveDialog,
    campaignChooserOpen,
    characterJoinDialogOpen,
    createCampaignOpen,
    playerDeleteDialog,
    playerEditDialog,
    profileSettingsOpen,
    runtimeSettingsOpen,
    setCampaignActionDialog,
    setSessionActionDialog,
    sessionArchiveDialog,
    shareSessionUrl,
    worldDeleteDialog,
    worldManagerOpen,
  ])

  useEffect(() => {
    closeCurrentDialogRef.current = closeCurrentDialog
  }, [closeCurrentDialog])

  const activeModalKey = campaignActionDialog
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
            : campaignChooserOpen
              ? 'campaign-chooser'
              : characterJoinDialogOpen
                ? 'character-join'
                : playerDeleteDialog
                  ? 'player-delete'
                  : playerEditDialog
                    ? `player-edit-${playerEditDialog.mode}`
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
  const runtimeSettingsIsAuthPrompt = runtimeSettingsMode === 'auth'
  const runtimeSettingsEyebrow = runtimeSettingsIsAuthPrompt ? 'Access' : 'Runtime'
  const runtimeSettingsTitle = runtimeSettingsIsAuthPrompt
    ? 'Auth Token Required'
    : 'Backend Settings'
  const runtimeSettingsCloseLabel = runtimeSettingsIsAuthPrompt
    ? 'Close auth token prompt'
    : 'Close backend settings'
  const runtimeSettingsHelpText = runtimeSettingsIsAuthPrompt
    ? 'Paste the shared token for this AIDM session.'
    : 'Leave Backend URL blank when the frontend and backend share one origin. Auth tokens are kept for this tab session and used for API, Socket.IO, and TTS requests.'

  useEffect(() => {
    if (
      !auth ||
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
    selectedCampaignId,
    workspaceLoading,
  ])

  useEffect(() => {
    if (!activeModalKey) return undefined
    const previouslyFocused =
      dialogReturnFocusRef.current ??
      (document.activeElement instanceof HTMLElement ? document.activeElement : null)
    const focusTimer = window.setTimeout(() => {
      const dialog = modalDialogRef.current
      const focusTarget = dialog
        ?.querySelector<HTMLElement>('[data-autofocus]')
        ?? dialog?.querySelector<HTMLElement>(
          'input:not([disabled]), textarea:not([disabled]), button:not([disabled])',
        )
      focusTarget?.focus()
    }, 0)

    const handleKeyDown = (event: KeyboardEvent) => {
      const dialog = modalDialogRef.current
      if (!dialog) return
      if (event.key === 'Escape') {
        event.preventDefault()
        event.stopPropagation()
        closeCurrentDialogRef.current()
        return
      }
      if (event.key !== 'Tab') return
      const focusable = focusableDialogElements(dialog)
      if (!focusable.length) {
        event.preventDefault()
        return
      }
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      window.clearTimeout(focusTimer)
      document.removeEventListener('keydown', handleKeyDown)
      if (previouslyFocused?.isConnected) {
        previouslyFocused.focus()
      }
      dialogReturnFocusRef.current = null
    }
  }, [activeModalKey])

  useEffect(() => {
    if (
      !selectedCampaignId ||
      !campaign ||
      selectedPlayerId ||
      workspaceLoading ||
      loadingCampaignId === selectedCampaignId ||
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
    workspaceLoading,
  ])

  useEffect(() => {
    if (modalOpen || diceRoll) return undefined
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
      }
      if (sessionMenuOpen && !sessionMenuRef.current?.contains(target)) {
        setSessionMenuOpen(false)
      }
    }
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
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
  }, [accountMenuOpen, sessionMenuOpen])

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
    if (!selectedSessionId) {
      clearSessionData()
      setTurnStatuses({})
      setClarificationRequest(null)
      return
    }
    setSessionLogCursor(null)
    setSessionLogHasMore(false)
    setTurnStatuses({})
    setClarificationRequest(null)
    loadSessionData(selectedSessionId).then(clearAuthTokenErrors).catch((error: unknown) => {
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
    selectedSessionId,
    setSessionLogCursor,
    setSessionLogHasMore,
  ])

  const loadPlayerDetail = useCallback(async (playerId: number) => {
    const requestId = ++playerRequestRef.current
    await apiFetch<PlayerDetail>(baseUrl, `/api/players/${playerId}`, auth)
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

  useEffect(() => {
    if (!selectedPlayerId) {
      playerRequestRef.current += 1
      setPlayerDetail(null)
      return
    }
    void loadPlayerDetail(selectedPlayerId)
  }, [
    loadPlayerDetail,
    selectedPlayerId,
    setPlayerDetail,
  ])

  useSessionSocket({
    auth,
    baseUrl,
    selectedSessionId,
    selectedPlayerId,
    selectedCampaignId,
    socketReconnectKey,
    socketRef,
    loadSessionData,
    refreshPlayerDetail: loadPlayerDetail,
    pushError,
    rememberStreamedTtsTurn,
    resetTtsFailureForNextResponse,
    stopTtsAudio,
    setActivePlayers,
    setSocketStatus,
    setSendPending,
    setOptimisticEntries,
    setStreamingTurn,
    setTurnStatuses,
    setClarificationRequest,
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
  })

  const resolveClarification = useCallback(
    (selectedItemId: string) => {
      if (!clarificationRequest || !selectedSessionId || !selectedPlayerId) return
      const socket = socketRef.current
      if (!socket) {
        pushError('connection', 'Socket is not connected; reconnect before choosing an item.')
        return
      }
      setSendPending(true)
      socket.emit('resolve_clarification', {
        session_id: selectedSessionId,
        player_id: selectedPlayerId,
        turn_id: clarificationRequest.turnId,
        selected_item_id: selectedItemId,
      })
      setClarificationRequest(null)
    },
    [clarificationRequest, pushError, selectedPlayerId, selectedSessionId, socketRef],
  )

  useEffect(() => {
    if (health?.status !== 'ok' || workspaceLoading || sessionLoading) return
    clearResolvedOperationalErrors()
  }, [clearResolvedOperationalErrors, health?.status, sessionLoading, workspaceLoading])

  useEffect(() => {
    if (socketStatus !== 'joined' && socketStatus !== 'idle') return
    clearResolvedOperationalErrors()
  }, [clearResolvedOperationalErrors, socketStatus])

  const displayCharacter = {
    name: selectedPlayer?.character_name ?? 'No player selected',
    ancestryClass: selectedPlayer
      ? `${selectedPlayer.race || 'Adventurer'} ${selectedPlayer.char_class || selectedPlayer.class_ || 'Class unset'}`
      : 'Load or create a player',
    level: selectedPlayer?.level ?? '—',
    detailId: selectedPlayer?.player_id ? `Player #${selectedPlayer.player_id}` : 'No player',
  }
  const xpProgress = normalizeXp(playerDetail?.stats ?? playerDetail?.character_sheet, displayCharacter.level)
  const capacity = inventoryCapacity(playerDetail?.stats ?? playerDetail?.character_sheet)
  const inventoryWeightLabel = buildInventoryWeightLabel(inventoryRows, capacity)
  const inventoryGoldLabel = buildInventoryGoldLabel(playerDetail?.stats, playerDetail?.character_sheet)
  const characterAvatarSrc =
    selectedPlayer?.profile_image ||
    profileIconSrcForCharacter({
      race: selectedPlayer?.race,
      sex: selectedPlayer?.sex,
      seed: displayCharacter.name,
    }) ||
    avatarDataUri(displayCharacter.name, 'character')
  const memorySnippets = memorySnippetRecords(sessionState?.memory_snippets)
  const canonFacts = canonFactsFromMemorySnippets(memorySnippets, selectedSessionId)
  const visibleCanonFacts = inspectorTab === 'canon' ? canonFacts : canonFacts.slice(0, 3)
  const selectedSegment =
    segments.find((segment) => segment.is_triggered) ?? segments[0] ?? null
  const mapTitle = maps[0]?.title ?? 'No map recorded'
  const mapDescription =
    maps[0]?.description ||
    sessionState?.current_location ||
    campaign?.location ||
    'No location recorded'
  const questTitle =
    sessionState?.current_quest || campaign?.current_quest || 'No quest recorded'
  const mapPanelTitle =
    maps[0]?.title || selectedSegment?.title || (sessionState?.current_location ? 'Current Location' : mapTitle)
  const mapMeta = buildMapMeta(maps[0], selectedSegment)
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
  const sessionDuration = activeSession
    ? formatDurationFrom(activeSession.created_at, nowMs)
    : 'No session'
  const runtime = llmConfig?.current ?? health?.llm ?? null
  const latestRuntime = runtime?.latest_turn ?? null
  const configuredProvider = stringValue(runtime?.provider, 'Unknown')
  const configuredModel = stringValue(runtime?.model, 'Unknown')
  const runtimeProviders = llmConfig?.providers ?? []
  const selectedProviderOption = runtimeProviders.find(
    (provider) => provider.id === configuredProvider,
  )
  const runtimeModels = selectedProviderOption?.models ?? [
    { id: configuredModel, label: configuredModel },
  ]
  const runtimeScopeLabel =
    llmConfig?.runtime_scope === 'process'
      ? 'Process-local'
      : 'Runtime'
  const runtimeScopeTitle = llmConfig?.restart_required_for_other_workers
    ? 'Provider changes apply to this backend process; restart other workers to match.'
    : 'Current runtime scope'
  const backendStatusLabel =
    health === null ? 'Checking' : health.status === 'ok' ? 'Connected' : 'Offline'
  const backendStatusTone =
    health === null ? 'neutral' : health.status === 'ok' ? 'good' : 'warn'
  const backendDisplayUrl = baseUrl || 'Same origin'
  const runtimeLabel = runtimePending
    ? 'Switching'
    : runtime?.configured
      ? 'Live'
      : health === null
        ? 'Checking'
        : health.status === 'ok'
        ? 'Missing key'
        : 'Offline'
  const runtimeTone =
    runtimePending || health === null ? 'neutral' : runtime?.configured ? 'good' : 'warn'
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
  const fullscreenActive = isFullscreen || fullscreenFallback
  const playerDialogPointBuySpent = playerEditDialog ? pointBuySpent(playerEditDialog.abilityScores) : 0
  const playerDialogPointBuyRemaining = POINT_BUY_BUDGET - playerDialogPointBuySpent

  return (
    <div
      ref={rootRef}
      className={`prototype-shell theme-${theme} ${railCollapsed ? 'rail-collapsed' : ''} ${fullscreenActive ? 'fullscreen-active' : ''}`}
    >
      <header className="ops-bar">
        <div className="ops-brand">
          <Flame size={25} fill="currentColor" />
          <strong>AI-DM</strong>
        </div>
        <button
          type="button"
          className="top-icon"
          aria-label={railCollapsed ? 'Show campaign rail' : 'Hide campaign rail'}
          aria-pressed={railCollapsed}
          onClick={() => setRailCollapsed((current) => !current)}
        >
          <Menu size={21} />
        </button>
        <div className="ops-segment backend-segment">
          <div>
            <strong>Backend</strong>
            <StatusDot label={backendStatusLabel} tone={backendStatusTone} />
          </div>
          <span>{backendDisplayUrl}</span>
          <ExternalLink size={15} />
          <button
            type="button"
            aria-label="Edit backend settings"
            title="Edit backend settings"
            onClick={openRuntimeSettingsDialog}
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
          <span title={runtimeScopeTitle}>{runtimeScopeLabel}</span>
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
          <span>{sessionDuration}</span>
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
            aria-label={ttsEnabled ? 'Turn TTS off' : 'Turn TTS on'}
            aria-pressed={ttsEnabled}
            title={
              ttsConfig?.configured
                ? `Deepgram narration: ${ttsConfig.model} (${ttsStatusLabel})`
                : 'Deepgram narration is not configured'
            }
            onClick={toggleTts}
          >
            {ttsEnabled ? <Volume2 size={18} /> : <VolumeX size={18} />}
          </button>
          <button
            type="button"
            className="top-icon"
            aria-label={fullscreenActive ? 'Exit fullscreen' : 'Enter fullscreen'}
            aria-pressed={fullscreenActive}
            onClick={() => void toggleFullscreen()}
          >
            {fullscreenActive ? <Minimize2 size={18} /> : <Maximize2 size={18} />}
          </button>
          <button
            type="button"
            className="top-icon"
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
              onClick={() => setAccountMenuOpen((current) => !current)}
            >
              <UserCircle size={19} />
            </button>
            <button
              type="button"
              className="top-icon small"
              aria-label="More account options"
              aria-expanded={accountMenuOpen}
              aria-controls="account-menu"
              onClick={() => setAccountMenuOpen((current) => !current)}
            >
              <ChevronDown size={16} />
            </button>
            {accountMenuOpen ? (
              <div
                id="account-menu"
                className="account-menu"
                role="menu"
                aria-label="Account options"
              >
                <strong role="presentation">{selectedPlayer?.character_name ?? 'No player selected'}</strong>
                <span role="presentation">{selectedPlayer?.name ?? 'Local profile'}</span>
                <button type="button" role="menuitem" onClick={() => void refreshCurrentWorkspace()}>
                  Refresh workspace
                </button>
                <button type="button" role="menuitem" onClick={openProfileSettingsDialog}>
                  Profile settings
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setSocketReconnectKey((current) => current + 1)
                    setAccountMenuOpen(false)
                  }}
                >
                  Reconnect socket
                </button>
                <button type="button" role="menuitem" onClick={openRuntimeSettingsDialog}>
                  Runtime settings
                </button>
                {authToken ? (
                  <button type="button" role="menuitem" onClick={clearAuthToken}>
                    Clear auth token
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      </header>

      <CampaignRail
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
        setMainTab={setMainTab}
        inspectorTab={inspectorTab}
        setInspectorTab={setInspectorTab}
        canManageCampaign={Boolean(campaign)}
        canManageSession={Boolean(activeSession)}
        canOpenCampaignArchive={health?.status === 'ok'}
        canOpenSessionArchive={Boolean(selectedCampaignId)}
        onRenameCampaign={openRenameCampaignDialog}
        onArchiveCampaign={openCampaignArchiveManager}
        onDeleteCampaign={openDeleteCampaignDialog}
        onCreateCampaign={openCreateCampaignDialog}
        onManageWorlds={openWorldManagerDialog}
        onRenameSession={openRenameSessionDialog}
        onArchiveSession={openSessionArchiveManager}
        onDeleteSession={openDeleteSessionDialog}
        onStartSession={startSession}
        onSelectCampaign={(campaignId) => {
          if (campaignId !== selectedCampaignId) {
            setSelectedCampaignId(campaignId)
          }
          setMainTab('turns')
        }}
        onSelectSession={(sessionId) => {
          if (sessionId !== selectedSessionId) {
            setSelectedSessionId(sessionId)
            setOptimisticEntries([])
            setStreamingTurn(null)
            setSendPending(false)
          }
          setMainTab('turns')
        }}
        lastSyncLabel={formatShortAge(lastSync)}
        onRefreshWorkspace={() => void refreshCurrentWorkspace()}
        errors={errors}
      />

      <SessionBoard
        activeSessionTitle={activeSessionTitle}
        campaignTitle={campaignTitle}
        workspaceLoading={workspaceLoading}
        sessionLoading={sessionLoading}
        mainTab={mainTab}
        setMainTab={setMainTab}
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
        expandedTurnIds={expandedTurnIds}
        setExpandedTurnIds={setExpandedTurnIds}
        selectedPlayer={selectedPlayer}
        currentResponseEntry={currentResponseEntry}
        latestDmText={latestDmText}
        sendPending={sendPending}
        streamingTurnActive={Boolean(streamingTurn)}
        dmExecutionStats={dmExecutionStats}
        welcomeText={welcomeText}
        showJumpToLatest={showJumpToLatest}
        scrollTurnFeedToLatest={scrollTurnFeedToLatest}
        questTitle={questTitle}
        sessionState={sessionState}
        campaign={campaign}
        canonFacts={canonFacts}
        clarificationRequest={clarificationRequest}
        resolveClarification={resolveClarification}
        actionComposerProps={{
          actionInputRef,
          actionText,
          adminPasscode,
          adminToolsUnlocked,
          setActionText,
          setAdminPasscode,
          selectedCharacterName: selectedPlayer?.character_name ?? null,
          composerMode,
          selectedDie,
          sendPending,
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
          rollModifier,
          setRollModifier,
          rollReason,
          setRollReason,
          pendingRollOptions,
          rollTargetPendingTurnId,
          setRollTargetPendingTurnId,
          selectedAbility,
          selectedAbilityKey,
          abilityOptions,
          updateRollAbilityKey,
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
          setSelectedItemName,
          setItemQuantity,
          updateSelectedInventoryAction,
          updateItemDraftName,
          updateItemCostGold,
        }}
      />

      <InspectorPanel
        inspectorTab={inspectorTab}
        setInspectorTab={setInspectorTab}
        setMainTab={setMainTab}
        displayCharacter={displayCharacter}
        characterAvatarSrc={characterAvatarSrc}
        xpProgress={xpProgress}
        playersCount={players.length}
        activePlayers={activePlayers}
        selectedPlayerId={selectedPlayerId}
        loadPlayer={openCharacterJoinDialog}
        createDefaultPlayer={promptCreatePlayer}
        editSelectedPlayer={openPlayerEditDialog}
        deleteSelectedPlayer={openPlayerDeleteDialog}
        selectedCampaignId={selectedCampaignId}
        createPlayerPending={createPlayerPending}
        statBlock={statBlock}
        inventoryRows={inventoryRows}
        inventoryWeightLabel={inventoryWeightLabel}
        inventoryGoldLabel={inventoryGoldLabel}
        memorySnippetCount={memorySnippets.length}
        visibleCanonFacts={visibleCanonFacts}
        mapPanelTitle={mapPanelTitle}
        mapDescription={mapDescription}
        mapMeta={mapMeta}
        questTitle={questTitle}
        selectedSegment={selectedSegment}
        maps={maps}
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
      />

      {diceRoll ? (
        <div
          className="modal-backdrop dice-roll-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && diceRoll.status === 'rolling') {
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
              result={diceRoll.result}
              targetLabel={diceRoll.targetLabel}
              rollKey={diceRoll.rollKey}
              status={diceRoll.status}
              onCancel={() => {
                if (diceRoll.status === 'rolling') {
                  closeDiceRoll()
                }
              }}
              onComplete={completeDiceRoll}
            />
          </Suspense>
        </div>
      ) : null}

      {runtimeSettingsOpen ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeRuntimeSettingsDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog runtime-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="runtime-settings-title"
          >
            <header>
              <div>
                <span>{runtimeSettingsEyebrow}</span>
                <h2 id="runtime-settings-title">{runtimeSettingsTitle}</h2>
              </div>
              <button
                type="button"
                aria-label={runtimeSettingsCloseLabel}
                onClick={closeRuntimeSettingsDialog}
              >
                <X size={18} />
              </button>
            </header>
            <form onSubmit={submitRuntimeSettings}>
              {runtimeSettingsIsAuthPrompt ? null : (
                <label>
                  Backend URL
                  <input
                    autoFocus={!runtimeSettingsIsAuthPrompt}
                    data-autofocus={!runtimeSettingsIsAuthPrompt ? true : undefined}
                    value={runtimeSettingsForm.baseUrl}
                    onChange={(event) =>
                      setRuntimeSettingsForm((current) => ({
                        ...current,
                        baseUrl: event.target.value,
                      }))
                    }
                    placeholder="Leave blank for same origin"
                  />
                </label>
              )}
              <label>
                Auth Token
                <input
                  autoFocus={runtimeSettingsIsAuthPrompt}
                  data-autofocus={runtimeSettingsIsAuthPrompt ? true : undefined}
                  value={runtimeSettingsForm.authToken}
                  onChange={(event) =>
                    setRuntimeSettingsForm((current) => ({
                      ...current,
                      authToken: event.target.value,
                    }))
                  }
                  placeholder="Optional bearer token"
                  type="password"
                  autoComplete="off"
                />
              </label>
              <p>{runtimeSettingsHelpText}</p>
              {runtimeSettingsError ? (
                <div className="dialog-error">{runtimeSettingsError}</div>
              ) : null}
              <footer>
                {runtimeSettingsIsAuthPrompt ? null : (
                  <button
                    type="button"
                    className="secondary"
                    onClick={() =>
                      setRuntimeSettingsForm({ baseUrl: DEFAULT_BASE_URL, authToken: '' })
                    }
                  >
                    Reset
                  </button>
                )}
                <button type="button" className="secondary" onClick={closeRuntimeSettingsDialog}>
                  Cancel
                </button>
                <button type="submit">
                  {runtimeSettingsIsAuthPrompt ? 'Connect' : 'Save Settings'}
                </button>
              </footer>
            </form>
          </section>
        </div>
      ) : null}

      {shareSessionUrl ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeShareSessionDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog share-session-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="share-session-title"
          >
            <header>
              <div>
                <span>Table Link</span>
                <h2 id="share-session-title">Share Session</h2>
              </div>
              <button
                type="button"
                aria-label="Close share session"
                onClick={closeShareSessionDialog}
              >
                <X size={18} />
              </button>
            </header>
            <label>
              Session Link
              <input
                data-autofocus
                readOnly
                aria-label="Session share link"
                value={shareSessionUrl}
                onFocus={(event) => event.currentTarget.select()}
              />
            </label>
            <p>
              Send this to someone who can open this frontend and reach this backend.
              They can choose or create their own character after it opens.
            </p>
            <footer>
              <button type="button" className="secondary" onClick={closeShareSessionDialog}>
                Close
              </button>
              <button type="button" onClick={copyShareSessionUrl}>
                Copy Link
              </button>
            </footer>
          </section>
        </div>
      ) : null}

      {profileSettingsOpen ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeProfileSettingsDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog profile-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="profile-settings-title"
          >
            <header>
              <div>
                <span>Profile</span>
                <h2 id="profile-settings-title">Profile Settings</h2>
              </div>
              <button
                type="button"
                aria-label="Close profile settings"
                onClick={closeProfileSettingsDialog}
              >
                <X size={18} />
              </button>
            </header>
            <div className="profile-dialog-body">
              <dl className="profile-summary-grid">
                <div>
                  <dt>Player</dt>
                  <dd>{selectedPlayer?.name ?? 'Local profile'}</dd>
                </div>
                <div>
                  <dt>Character</dt>
                  <dd>{displayCharacter.name}</dd>
                </div>
                <div>
                  <dt>Campaign</dt>
                  <dd>{campaign?.title ?? 'No campaign selected'}</dd>
                </div>
                <div>
                  <dt>Session</dt>
                  <dd>{activeSessionName}</dd>
                </div>
                <div>
                  <dt>Backend</dt>
                  <dd>{backendDisplayUrl}</dd>
                </div>
                <div>
                  <dt>Narration</dt>
                  <dd>{ttsStatusLabel}{ttsLatencyLabel ? ` / ${ttsLatencyLabel}` : ''}</dd>
                </div>
              </dl>
              <div className="profile-action-list">
                <button type="button" onClick={openPlayerEditDialog} disabled={!selectedPlayer}>
                  Edit character
                </button>
                <button type="button" onClick={openCharacterJoinDialog} disabled={!selectedCampaignId}>
                  Switch character
                </button>
                <button type="button" onClick={() => void refreshCurrentWorkspace()}>
                  Refresh workspace
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setSocketReconnectKey((current) => current + 1)
                    closeProfileSettingsDialog()
                  }}
                >
                  Reconnect realtime
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setProfileSettingsOpen(false)
                    openRuntimeSettingsDialog()
                  }}
                >
                  Backend settings
                </button>
                {authToken ? (
                  <button type="button" onClick={clearAuthToken}>
                    Clear auth token
                  </button>
                ) : null}
              </div>
            </div>
          </section>
        </div>
      ) : null}

      {characterJoinDialogOpen ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeCharacterJoinDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog character-join-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="character-join-title"
          >
            <header>
              <div>
                <span>Character</span>
                <h2 id="character-join-title">Join Campaign</h2>
              </div>
              <button type="button" aria-label="Close character chooser" onClick={closeCharacterJoinDialog}>
                <X size={18} />
              </button>
            </header>
            <div className="character-join-body">
              <p>
                {campaign?.title
                  ? `Choose who you are playing in ${campaign.title}.`
                  : 'Choose who you are playing.'}
              </p>
              {players.length ? (
                <div className="character-choice-list" aria-label="Existing characters">
                  {players.map((player) => {
                    const characterName = player.character_name || player.name || `Player ${player.player_id}`
                    const playerName = player.name || 'Unknown player'
                    const characterClass = player.char_class || player.class_ || 'Adventurer'
                    const characterPortraitSrc =
                      player.profile_image ||
                      profileIconSrcForCharacter({
                        race: player.race,
                        sex: player.sex,
                        seed: characterName,
                      }) ||
                      avatarDataUri(characterName, 'character')
                    return (
                      <button
                        key={player.player_id}
                        type="button"
                        className="character-choice-card"
                        aria-label={`Join as ${characterName}`}
                        onClick={() => joinAsExistingPlayer(player)}
                      >
                        <img
                          className="character-choice-portrait"
                          src={characterPortraitSrc}
                          alt=""
                          aria-hidden="true"
                        />
                        <span>
                          <strong>{characterName}</strong>
                          <small>
                            {playerName} / Level {player.level} {characterClass}
                          </small>
                        </span>
                        <em>Join</em>
                      </button>
                    )
                  })}
                </div>
              ) : (
                <div className="dialog-warning">
                  <strong>No characters yet.</strong>
                  <span>Create the first character for this campaign.</span>
                </div>
              )}
              <footer>
                <button type="button" className="secondary" onClick={closeCharacterJoinDialog}>
                  Cancel
                </button>
                <button type="button" onClick={createCharacterFromJoinDialog}>
                  Create Character
                </button>
              </footer>
            </div>
          </section>
        </div>
      ) : null}

      {campaignArchiveDialog ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeCampaignArchiveDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog archive-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="campaign-archive-title"
          >
            <header>
              <div>
                <span>Archive</span>
                <h2 id="campaign-archive-title">Campaign Archive</h2>
              </div>
              <button
                type="button"
                aria-label="Close campaign archive"
                onClick={closeCampaignArchiveDialog}
                disabled={campaignArchiveDialog.pendingId !== null}
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
                {campaignArchiveDialog.loading ? (
                  <div className="rail-skeleton-list" aria-label="Loading campaign archive">
                    <span />
                    <span />
                    <span />
                  </div>
                ) : campaignArchiveDialog.items.length ? (
                  campaignArchiveDialog.items.map((item) => {
                    const worldLabel = worldNameById.get(item.world_id) ?? `World ${item.world_id}`
                    const pending = campaignArchiveDialog.pendingId === item.campaign_id
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
                            onClick={() => void restoreCampaignFromArchive(item.campaign_id)}
                            disabled={campaignArchiveDialog.pendingId !== null}
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
              {campaignArchiveDialog.error ? (
                <div className="dialog-error">{campaignArchiveDialog.error}</div>
              ) : null}
              <footer>
                <button
                  type="button"
                  className="secondary"
                  onClick={closeCampaignArchiveDialog}
                  disabled={campaignArchiveDialog.pendingId !== null}
                >
                  Close
                </button>
                <button
                  type="button"
                  onClick={() => void archiveSelectedCampaignFromManager()}
                  disabled={!campaign || campaignArchiveDialog.pendingId !== null}
                >
                  {campaignArchiveDialog.pendingId === campaign?.campaign_id
                    ? 'Archiving...'
                    : 'Archive Selected Campaign'}
                </button>
              </footer>
            </div>
          </section>
        </div>
      ) : null}

      {sessionArchiveDialog ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeSessionArchiveDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog archive-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="session-archive-title"
          >
            <header>
              <div>
                <span>Archive</span>
                <h2 id="session-archive-title">Session Archive</h2>
              </div>
              <button
                type="button"
                aria-label="Close session archive"
                onClick={closeSessionArchiveDialog}
                disabled={sessionArchiveDialog.pendingId !== null}
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
                {sessionArchiveDialog.loading ? (
                  <div className="rail-skeleton-list" aria-label="Loading session archive">
                    <span />
                    <span />
                    <span />
                  </div>
                ) : sessionArchiveDialog.items.length ? (
                  sessionArchiveDialog.items.map((item) => {
                    const title = sessionDisplayName(item, campaign?.world_id ?? selectedCampaignId)
                    const pending = sessionArchiveDialog.pendingId === item.session_id
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
                            onClick={() => void restoreSessionFromArchive(item.session_id)}
                            disabled={sessionArchiveDialog.pendingId !== null}
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
              {sessionArchiveDialog.error ? (
                <div className="dialog-error">{sessionArchiveDialog.error}</div>
              ) : null}
              <footer>
                <button
                  type="button"
                  className="secondary"
                  onClick={closeSessionArchiveDialog}
                  disabled={sessionArchiveDialog.pendingId !== null}
                >
                  Close
                </button>
                <button
                  type="button"
                  onClick={() => void archiveSelectedSessionFromManager()}
                  disabled={!activeSession || sessionArchiveDialog.pendingId !== null}
                >
                  {sessionArchiveDialog.pendingId === activeSession?.session_id
                    ? 'Archiving...'
                    : 'Archive Selected Session'}
                </button>
              </footer>
            </div>
          </section>
        </div>
      ) : null}

      {campaignChooserOpen ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeCampaignChooserDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog campaign-chooser-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="campaign-chooser-title"
          >
            <header>
              <div>
                <span>Campaign</span>
                <h2 id="campaign-chooser-title">Choose Campaign</h2>
              </div>
              <button
                type="button"
                aria-label="Close campaign chooser"
                onClick={closeCampaignChooserDialog}
              >
                <X size={18} />
              </button>
            </header>
            <div className="character-join-body">
              <p>Choose the campaign before selecting or creating a character.</p>
              {campaigns.length ? (
                <div className="character-choice-list" aria-label="Available campaigns">
                  {campaigns.map((item) => {
                    const worldLabel = worldNameById.get(item.world_id) ?? `World ${item.world_id}`
                    return (
                      <button
                        key={item.campaign_id}
                        type="button"
                        className="character-choice-card"
                        aria-label={`Choose ${item.title}`}
                        onClick={() => chooseCampaign(item.campaign_id)}
                      >
                        <span>
                          <strong>{item.title}</strong>
                          <small>
                            {item.is_archived ? 'Archived' : 'Active'} / {worldLabel}
                          </small>
                        </span>
                        <em>Select</em>
                      </button>
                    )
                  })}
                </div>
              ) : (
                <div className="dialog-warning">
                  <strong>No campaigns yet.</strong>
                  <span>Create a campaign before choosing a character.</span>
                </div>
              )}
              <footer>
                <button type="button" className="secondary" onClick={closeCampaignChooserDialog}>
                  Cancel
                </button>
                <button type="button" data-autofocus onClick={createCampaignFromChooser}>
                  Create Campaign
                </button>
              </footer>
            </div>
          </section>
        </div>
      ) : null}

      {playerEditDialog ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !playerEditDialog.pending) {
              closePlayerEditDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog player-edit-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="player-edit-title"
          >
	            <header>
	              <div>
	                <span>Character</span>
	                <h2 id="player-edit-title">
	                  {playerEditDialog.mode === 'create' ? 'Create Character' : 'Edit Character'}
	                </h2>
	              </div>
	              <button
	                type="button"
	                aria-label={playerEditDialog.mode === 'create' ? 'Close character creator' : 'Close character editor'}
	                onClick={closePlayerEditDialog}
	                disabled={playerEditDialog.pending}
              >
                <X size={18} />
              </button>
            </header>
            <form onSubmit={(event) => void submitPlayerEditDialog(event)}>
              <label>
                Player Name
                <input
                  autoFocus
                  data-autofocus
                  value={playerEditDialog.name}
                  onChange={(event) =>
                    setPlayerEditDialog((current) =>
                      current ? { ...current, name: event.target.value } : current,
                    )
                  }
                />
              </label>
              <label>
                Character Name
                <input
                  value={playerEditDialog.characterName}
                  onChange={(event) =>
                    setPlayerEditDialog((current) =>
                      current ? { ...current, characterName: event.target.value } : current,
                    )
                  }
                />
              </label>
              <div className="dialog-grid two">
                <label>
                  Race
                  <input
                    value={playerEditDialog.race}
                    onChange={(event) =>
                      setPlayerEditDialog((current) =>
                        current ? { ...current, race: event.target.value } : current,
                      )
                    }
                  />
                </label>
                <label>
                  Sex
                  <select
                    value={playerEditDialog.sex}
                    onChange={(event) =>
                      setPlayerEditDialog((current) =>
                        current ? { ...current, sex: event.target.value } : current,
                      )
                    }
                  >
                    <option value="">Not set</option>
                    <option value="female">Female</option>
                    <option value="male">Male</option>
                    <option value="other">Other</option>
                  </select>
                </label>
              </div>
              <div className="dialog-grid two">
                <label>
                  Class
                  <input
                    value={playerEditDialog.charClass}
                    onChange={(event) =>
                      setPlayerEditDialog((current) =>
                        current ? { ...current, charClass: event.target.value } : current,
                      )
                    }
                  />
                </label>
                <label>
                  Level
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={playerEditDialog.level}
                    onChange={(event) =>
                      setPlayerEditDialog((current) =>
                        current ? { ...current, level: event.target.value } : current,
                      )
                    }
                  />
                </label>
              </div>
              {playerEditDialog.mode === 'create' ? (
                <section className="point-buy-panel" aria-label="Ability score point buy">
                  <div className="point-buy-summary">
                    <strong>Ability Scores</strong>
                    <span className={playerDialogPointBuyRemaining < 0 ? 'over-budget' : ''}>
                      {playerDialogPointBuyRemaining} / {POINT_BUY_BUDGET} left
                    </span>
                  </div>
                  <div className="point-buy-grid">
                    {POINT_BUY_ABILITIES.map((ability) => {
                      const score = playerEditDialog.abilityScores[ability.key]
                      return (
                        <label key={ability.key}>
                          <span>
                            {ability.label}
                            <small>{abilityModifier(score)}</small>
                          </span>
                          <input
                            type="number"
                            min={8}
                            max={15}
                            value={score}
                            aria-label={ability.name}
                            onChange={(event) =>
                              setPlayerEditDialog((current) =>
                                current
                                  ? {
                                      ...current,
                                      abilityScores: {
                                        ...current.abilityScores,
                                        [ability.key]: clampPointBuyScore(Number(event.target.value)),
                                      },
                                    }
                                  : current,
                              )
                            }
                          />
                        </label>
                      )
                    })}
                  </div>
                </section>
              ) : null}
              {playerEditDialog.error ? (
                <div className="dialog-error">{playerEditDialog.error}</div>
              ) : null}
              <footer>
                <button
                  type="button"
                  className="secondary"
                  onClick={closePlayerEditDialog}
                  disabled={playerEditDialog.pending}
                >
                  Cancel
	                </button>
	                <button type="submit" disabled={playerEditDialog.pending}>
	                  {playerEditDialog.pending
	                    ? playerEditDialog.mode === 'create'
	                      ? 'Creating...'
	                      : 'Saving...'
	                    : playerEditDialog.mode === 'create'
	                      ? 'Create Character'
	                      : 'Save Character'}
	                </button>
	              </footer>
            </form>
          </section>
        </div>
      ) : null}

      {playerDeleteDialog ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget && !playerDeleteDialog.pending) {
              closePlayerDeleteDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog player-delete-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="player-delete-title"
          >
            <header>
              <div>
                <span>Character</span>
                <h2 id="player-delete-title">Delete Character</h2>
              </div>
              <button
                type="button"
                aria-label="Close character delete"
                onClick={closePlayerDeleteDialog}
                disabled={playerDeleteDialog.pending}
              >
                <X size={18} />
              </button>
            </header>
            <div className="dialog-body">
              <div className="dialog-warning">
                <strong>{playerDeleteDialog.player.character_name || playerDeleteDialog.player.name}</strong>
                <span>
                  This permanently removes the character from this workspace. Past
                  turn history stays readable, but it will no longer point at this
                  character record.
                </span>
              </div>
              {playerDeleteDialog.error ? (
                <div className="dialog-error">{playerDeleteDialog.error}</div>
              ) : null}
              <footer>
                <button
                  type="button"
                  className="secondary"
                  data-autofocus
                  onClick={closePlayerDeleteDialog}
                  disabled={playerDeleteDialog.pending}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="danger"
                  onClick={() => void submitPlayerDeleteDialog()}
                  disabled={playerDeleteDialog.pending}
                >
                  {playerDeleteDialog.pending ? 'Deleting...' : 'Delete Character'}
                </button>
              </footer>
            </div>
          </section>
        </div>
      ) : null}

      {campaignActionDialog ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeCampaignActionDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog campaign-action-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="campaign-action-title"
          >
            <header>
              <div>
                <span>Campaign</span>
                <h2 id="campaign-action-title">
                  {campaignActionDialog.mode === 'rename'
                    ? 'Rename Campaign'
                    : campaignActionDialog.mode === 'archive'
                      ? 'Archive Campaign'
                      : campaignActionDialog.mode === 'restore'
                        ? 'Restore Campaign'
                        : 'Delete Campaign'}
                </h2>
              </div>
              <button
                type="button"
                aria-label="Close campaign action"
                onClick={closeCampaignActionDialog}
                disabled={campaignActionDialog.pending}
              >
                <X size={18} />
              </button>
            </header>
            <form onSubmit={(event) => void submitCampaignActionDialog(event)}>
              {campaignActionDialog.mode === 'rename' ? (
                <>
                  <label>
                    Campaign Name
                    <input
                      autoFocus
                      data-autofocus
                      value={campaignActionDialog.title}
                      onChange={(event) =>
                        setCampaignActionDialog((current) =>
                          current
                            ? { ...current, title: event.target.value, error: '' }
                            : current,
                        )
                      }
                      disabled={campaignActionDialog.pending}
                    />
                  </label>
                  <label>
                    Description
                    <textarea
                      value={campaignActionDialog.description}
                      onChange={(event) =>
                        setCampaignActionDialog((current) =>
                          current
                            ? { ...current, description: event.target.value, error: '' }
                            : current,
                        )
                      }
                      disabled={campaignActionDialog.pending}
                    />
                  </label>
                </>
              ) : (
                <div className="dialog-warning">
                  <strong>{campaignActionDialog.title}</strong>
                  <span>
                    {campaignActionDialog.mode === 'archive'
                      ? 'Archiving hides this campaign and its sessions from the normal workspace list without destroying saved history.'
                      : campaignActionDialog.mode === 'restore'
                        ? 'Restoring makes this campaign and sessions archived with it available for normal play again.'
                        : 'This permanently deletes the campaign, its sessions, maps, and campaign notes from this workspace. Characters stay in the workspace but are detached from it.'}
                  </span>
                </div>
              )}
              {campaignActionDialog.error ? (
                <div className="dialog-error">{campaignActionDialog.error}</div>
              ) : null}
              <footer>
                <button
                  type="button"
                  className="secondary"
                  onClick={closeCampaignActionDialog}
                  disabled={campaignActionDialog.pending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className={
                    campaignActionDialog.mode === 'archive' || campaignActionDialog.mode === 'delete'
                      ? 'danger'
                      : undefined
                  }
                  disabled={campaignActionDialog.pending}
                >
                  {campaignActionDialog.pending
                    ? campaignActionDialog.mode === 'rename'
                      ? 'Saving...'
                      : campaignActionDialog.mode === 'archive'
                        ? 'Archiving...'
                        : campaignActionDialog.mode === 'restore'
                          ? 'Restoring...'
                          : 'Deleting...'
                    : campaignActionDialog.mode === 'rename'
                      ? 'Save Campaign'
                      : campaignActionDialog.mode === 'archive'
                        ? 'Archive Campaign'
                        : campaignActionDialog.mode === 'restore'
                          ? 'Restore Campaign'
                          : 'Delete Campaign'}
                </button>
              </footer>
            </form>
          </section>
        </div>
      ) : null}

      {sessionActionDialog ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeSessionActionDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog session-action-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="session-action-title"
          >
            <header>
              <div>
                <span>Session</span>
                <h2 id="session-action-title">
                  {sessionActionDialog.mode === 'rename' ? 'Rename Session' : 'Delete Session'}
                </h2>
              </div>
              <button
                type="button"
                aria-label="Close session action"
                onClick={closeSessionActionDialog}
                disabled={sessionActionDialog.pending}
              >
                <X size={18} />
              </button>
            </header>
            <form onSubmit={(event) => void submitSessionActionDialog(event)}>
              {sessionActionDialog.mode === 'rename' ? (
                <label>
                  Session Name
                  <input
                    autoFocus
                    data-autofocus
                    value={sessionActionDialog.name}
                    onChange={(event) =>
                      setSessionActionDialog((current) =>
                        current
                          ? { ...current, name: event.target.value, error: '' }
                          : current,
                      )
                    }
                    disabled={sessionActionDialog.pending}
                  />
                </label>
              ) : (
                <div className="dialog-warning">
                  <strong>{sessionActionDialog.name}</strong>
                  <span>
                    This permanently deletes this session and its saved turn history. Use
                    the archive button if you only want to hide it.
                  </span>
                </div>
              )}
              {sessionActionDialog.error ? (
                <div className="dialog-error">{sessionActionDialog.error}</div>
              ) : null}
              <footer>
                <button
                  type="button"
                  className="secondary"
                  onClick={closeSessionActionDialog}
                  disabled={sessionActionDialog.pending}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className={sessionActionDialog.mode === 'delete' ? 'danger' : undefined}
                  disabled={sessionActionDialog.pending}
                >
                  {sessionActionDialog.pending
                    ? sessionActionDialog.mode === 'rename'
                      ? 'Renaming...'
                      : 'Deleting...'
                    : sessionActionDialog.mode === 'rename'
                      ? 'Rename Session'
                      : 'Delete Session'}
                </button>
              </footer>
            </form>
          </section>
        </div>
      ) : null}

      {worldManagerOpen ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeWorldManagerDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog world-manager-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="world-manager-title"
          >
            <header>
              <div>
                <span>Worlds</span>
                <h2 id="world-manager-title">Manage Worlds</h2>
              </div>
              <button
                type="button"
                aria-label="Close world manager"
                onClick={closeWorldManagerDialog}
                disabled={worldForm.pending || worldDeleteDialog !== null}
              >
                <X size={18} />
              </button>
            </header>
            <div className="world-manager-list" aria-label="World list">
              {worldSelectOptions.length ? (
                worldSelectOptions.map((world) => {
                  const isEditing = worldForm.mode === 'edit' && worldForm.worldId === world.world_id
                  return (
                    <div
                      key={world.world_id}
                      className={`world-manager-row ${isEditing ? 'active' : ''}`}
                    >
                      <span>
                        <strong>{world.name}</strong>
                        <small>{world.description || 'No description yet'}</small>
                      </span>
                      <div>
                        <button
                          type="button"
                          onClick={() => editWorld(world)}
                          disabled={worldForm.pending || worldDeleteDialog !== null}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="danger"
                          onClick={() => openWorldDeleteDialog(world)}
                          disabled={worldForm.pending || worldDeleteDialog !== null}
                        >
                          Delete
                        </button>
                      </div>
                    </div>
                  )
                })
              ) : (
                <div className="dialog-warning">
                  <strong>No worlds yet.</strong>
                  <span>Create a world below, then attach campaigns to it.</span>
                </div>
              )}
            </div>
            <form className="world-manager-form" onSubmit={(event) => void submitWorldForm(event)}>
              <div className="world-manager-form-heading">
                <strong>{worldForm.mode === 'edit' ? 'Edit World' : 'Create World'}</strong>
                {worldForm.mode === 'edit' ? (
                  <button
                    type="button"
                    className="secondary"
                    onClick={resetWorldForm}
                    disabled={worldForm.pending}
                  >
                    New World
                  </button>
                ) : null}
              </div>
              <label>
                World Name
                <input
                  data-autofocus
                  value={worldForm.name}
                  onChange={(event) =>
                    setWorldForm((current) => ({
                      ...current,
                      name: event.target.value,
                      error: '',
                    }))
                  }
                  placeholder="Crystal Reach"
                  disabled={worldForm.pending}
                />
              </label>
              <label>
                Description
                <textarea
                  value={worldForm.description}
                  onChange={(event) =>
                    setWorldForm((current) => ({
                      ...current,
                      description: event.target.value,
                      error: '',
                    }))
                  }
                  rows={3}
                  placeholder="Realm premise, tone, or key conflicts..."
                  disabled={worldForm.pending}
                />
              </label>
              {worldForm.error ? <div className="dialog-error">{worldForm.error}</div> : null}
              <footer>
                <button
                  type="button"
                  className="secondary"
                  onClick={closeWorldManagerDialog}
                  disabled={worldForm.pending || worldDeleteDialog !== null}
                >
                  Close
                </button>
                <button type="submit" disabled={worldForm.pending}>
                  {worldForm.pending
                    ? worldForm.mode === 'edit'
                      ? 'Saving...'
                      : 'Creating...'
                    : worldForm.mode === 'edit'
                      ? 'Save World'
                      : 'Create World'}
                </button>
              </footer>
            </form>
          </section>
        </div>
      ) : null}

      {worldDeleteDialog ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeWorldDeleteDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="world-delete-title"
          >
            <header>
              <div>
                <span>World</span>
                <h2 id="world-delete-title">Delete World</h2>
              </div>
              <button
                type="button"
                aria-label="Close delete world"
                onClick={closeWorldDeleteDialog}
                disabled={worldDeleteDialog.pending}
              >
                <X size={18} />
              </button>
            </header>
            <div className="dialog-body">
              <div className="dialog-warning">
                <strong>{worldDeleteDialog.world.name}</strong>
                <span>
                  This world can be deleted directly when nothing is using it.
                  If campaigns are linked, force delete removes those linked campaigns first.
                </span>
              </div>
              {worldDeleteDialog.error ? (
                <div className="dialog-error">{worldDeleteDialog.error}</div>
              ) : null}
              <footer>
                <button
                  type="button"
                  className="secondary"
                  onClick={closeWorldDeleteDialog}
                  disabled={worldDeleteDialog.pending}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="danger"
                  data-autofocus
                  onClick={() => void submitWorldDeleteDialog()}
                  disabled={worldDeleteDialog.pending}
                >
                  {worldDeleteDialog.pending ? 'Deleting...' : 'Delete World'}
                </button>
                {worldDeleteDialog.canForce ? (
                  <button
                    type="button"
                    className="danger"
                    onClick={() => void submitWorldDeleteDialog(true)}
                    disabled={worldDeleteDialog.pending}
                  >
                    {worldDeleteDialog.pending ? 'Deleting...' : 'Delete World and Campaigns'}
                  </button>
                ) : null}
              </footer>
            </div>
          </section>
        </div>
      ) : null}

      {createCampaignOpen ? (
        <div
          className="modal-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) {
              closeCreateCampaignDialog()
            }
          }}
        >
          <section
            ref={modalDialogRef}
            className="campaign-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="create-campaign-title"
          >
            <header>
              <div>
                <span>Campaign</span>
                <h2 id="create-campaign-title">Create New Campaign</h2>
              </div>
              <button
                type="button"
                aria-label="Close create campaign"
                onClick={closeCreateCampaignDialog}
              >
                <X size={18} />
              </button>
            </header>
            <form onSubmit={(event) => void submitCreateCampaign(event)}>
              <label>
                Campaign Name
                <input
                  autoFocus
                  data-autofocus
                  value={createCampaignForm.title}
                  onChange={(event) =>
                    setCreateCampaignForm((current) => ({
                      ...current,
                      title: event.target.value,
                    }))
                  }
                  placeholder="Ashes Beyond the Gate"
                  disabled={createCampaignPending}
                />
              </label>
              <label>
                Description
                <textarea
                  value={createCampaignForm.description}
                  onChange={(event) =>
                    setCreateCampaignForm((current) => ({
                      ...current,
                      description: event.target.value,
                    }))
                  }
                  rows={3}
                  placeholder="Opening premise, party goal, or tone..."
                  disabled={createCampaignPending}
                />
              </label>
              <label>
                World
                <select
                  value={createCampaignForm.worldName.trim() ? '' : createCampaignForm.worldId}
                  onChange={(event) =>
                    setCreateCampaignForm((current) => ({
                      ...current,
                      worldId: event.target.value,
                      worldName: '',
                    }))
                  }
                  disabled={createCampaignPending}
                >
                  <option value="">Create a new world</option>
                  {worldSelectOptions.map((world) => (
                    <option key={world.world_id} value={world.world_id}>
                      {world.name}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                New World Name
                <input
                  value={createCampaignForm.worldName}
                  onChange={(event) =>
                    setCreateCampaignForm((current) => ({
                      ...current,
                      worldId: '',
                      worldName: event.target.value,
                    }))
                  }
                  placeholder="Crystal Reach"
                  disabled={createCampaignPending}
                />
              </label>
              <p>
                Select an existing world, or enter a new world name to create one for this
                campaign.
              </p>
              {createCampaignError ? (
                <div className="dialog-error">{createCampaignError}</div>
              ) : null}
              <footer>
                <button
                  type="button"
                  className="secondary"
                  onClick={closeCreateCampaignDialog}
                  disabled={createCampaignPending}
                >
                  Cancel
                </button>
                <button type="submit" disabled={createCampaignPending}>
                  {createCampaignPending ? 'Creating...' : 'Create Campaign'}
                </button>
              </footer>
            </form>
          </section>
        </div>
      ) : null}
    </div>
  )
}

export default App
