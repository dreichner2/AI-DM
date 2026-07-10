import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { apiFetch } from './api'
import type { MainTab } from './SessionBoard'
import type {
  Campaign,
  ClarificationRequest,
  PlayNowResponse,
  PlayerDetail,
  SessionLogEntry,
  SessionState,
  SessionSummary,
  StreamingTurn,
  TimelineEntry,
} from './types'

const PLAY_NOW_SEEN_STORAGE_KEY = 'aidm:hasPlayed'

type ValueUpdater<T> = T | ((current: T) => T)

type UsePlayNowOnboardingOptions = {
  activeSessionId: number | null
  auth: string
  authRequired: boolean | null
  backendReady: boolean
  baseUrl: string
  campaignCount: number
  closeMobilePanels: () => void
  modalOpen: boolean
  runtimeSettingsOpen: boolean
  selectedCampaignId: number | null
  selectedPlayerDetailId: number | null
  selectedPlayerId: number | null
  selectedSessionId: number | null
  workspaceLoading: boolean
  campaignUpserted: (campaign: Campaign) => void
  sessionUpserted: (session: SessionSummary) => void
  playerUpserted: (player: PlayerDetail) => void
  clearAuthTokenErrors: () => void
  loadPlayerDetail: (playerId: number) => Promise<void>
  loadSessionData: (sessionId: number) => Promise<void>
  openCreateCampaignDialog: () => void
  pushError: (category: 'persistence' | 'workspace', message: string) => void
  refreshCampaignWorkspace: (campaignId: number) => Promise<void>
  refreshRoot: () => Promise<void>
  setClarificationRequest: Dispatch<SetStateAction<ClarificationRequest | null>>
  setLogEntries: (value: ValueUpdater<SessionLogEntry[]>) => void
  setMainTab: Dispatch<SetStateAction<MainTab>>
  setOptimisticEntries: Dispatch<SetStateAction<TimelineEntry[]>>
  setPlayerDetail: (value: ValueUpdater<PlayerDetail | null>) => void
  setSelectedCampaignId: (value: ValueUpdater<number | null>) => void
  setSelectedPlayerId: (value: ValueUpdater<number | null>) => void
  setSelectedSessionId: (value: ValueUpdater<number | null>) => void
  setSessionState: (value: ValueUpdater<SessionState | null>) => void
  setStreamingTurn: Dispatch<SetStateAction<StreamingTurn | null>>
  setTurnStatuses: Dispatch<SetStateAction<Record<number, string>>>
  currentResponsePresent: boolean
  dmResponseBlocking: boolean
  sendPending: boolean
  socketStatus: string
  startAdventure: () => void
  turnRowCount: number
}

function readPlayNowSeenFlag() {
  try {
    return localStorage.getItem(PLAY_NOW_SEEN_STORAGE_KEY) === 'true'
  } catch {
    return false
  }
}

export function usePlayNowOnboarding({
  activeSessionId,
  auth,
  authRequired,
  backendReady,
  baseUrl,
  campaignCount,
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
  clearAuthTokenErrors,
  loadPlayerDetail,
  loadSessionData,
  openCreateCampaignDialog,
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
  currentResponsePresent,
  dmResponseBlocking,
  sendPending,
  socketStatus,
  startAdventure,
  turnRowCount,
}: UsePlayNowOnboardingOptions) {
  const [titleScreenDismissed, setTitleScreenDismissed] = useState(readPlayNowSeenFlag)
  const [playNowPending, setPlayNowPending] = useState(false)
  const playNowPendingRef = useRef(false)
  const playNowAutoStartRef = useRef(false)

  const rememberTitleScreenChoice = useCallback(() => {
    try {
      localStorage.setItem(PLAY_NOW_SEEN_STORAGE_KEY, 'true')
    } catch {
      // The current browser session can continue even when storage is unavailable.
    }
    setTitleScreenDismissed(true)
  }, [])

  const continueFromTitleScreen = useCallback(() => {
    rememberTitleScreenChoice()
    closeMobilePanels()
  }, [closeMobilePanels, rememberTitleScreenChoice])

  const createCampaignFromTitleScreen = useCallback(() => {
    rememberTitleScreenChoice()
    closeMobilePanels()
    openCreateCampaignDialog()
  }, [closeMobilePanels, openCreateCampaignDialog, rememberTitleScreenChoice])

  const playNowFromTitleScreen = useCallback(async () => {
    if (playNowPendingRef.current) return
    playNowPendingRef.current = true
    setPlayNowPending(true)
    try {
      const payload = await apiFetch<PlayNowResponse>(
        baseUrl,
        '/api/onboarding/play-now',
        auth,
        {
          method: 'POST',
          body: JSON.stringify({}),
        },
      )
      try {
        localStorage.setItem('aidm:workspaceId', payload.workspace_id)
      } catch {
        // The default local workspace still works if localStorage is unavailable.
      }
      playNowAutoStartRef.current = payload.session.turn_count === 0
      campaignUpserted(payload.campaign)
      sessionUpserted(payload.session)
      playerUpserted(payload.player)
      setPlayerDetail(payload.player)
      setLogEntries([])
      setSessionState(null)
      setOptimisticEntries([])
      setStreamingTurn(null)
      setTurnStatuses({})
      setClarificationRequest(null)
      setSelectedCampaignId(payload.campaign_id)
      setSelectedSessionId(payload.session_id)
      setSelectedPlayerId(payload.player_id)
      setMainTab('turns')
      closeMobilePanels()
      rememberTitleScreenChoice()
      clearAuthTokenErrors()
      try {
        await refreshRoot()
        await refreshCampaignWorkspace(payload.campaign_id)
        await loadSessionData(payload.session_id)
        await loadPlayerDetail(payload.player_id)
      } catch (refreshError) {
        const message = refreshError instanceof Error ? refreshError.message : String(refreshError)
        pushError('workspace', `Play Now opened, but refresh failed: ${message}`)
      }
    } catch (error) {
      playNowAutoStartRef.current = false
      const message = error instanceof Error ? error.message : String(error)
      pushError('persistence', `Play Now failed: ${message}`)
    } finally {
      playNowPendingRef.current = false
      setPlayNowPending(false)
    }
  }, [
    auth,
    baseUrl,
    campaignUpserted,
    clearAuthTokenErrors,
    closeMobilePanels,
    loadPlayerDetail,
    loadSessionData,
    playerUpserted,
    pushError,
    refreshCampaignWorkspace,
    refreshRoot,
    rememberTitleScreenChoice,
    sessionUpserted,
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
  ])

  useEffect(() => {
    if (!playNowAutoStartRef.current) return
    if (turnRowCount > 0 || currentResponsePresent) {
      playNowAutoStartRef.current = false
      return
    }
    if (
      !activeSessionId ||
      !selectedPlayerDetailId ||
      socketStatus !== 'joined' ||
      sendPending ||
      dmResponseBlocking
    ) {
      return
    }
    playNowAutoStartRef.current = false
    startAdventure()
  }, [
    activeSessionId,
    currentResponsePresent,
    dmResponseBlocking,
    selectedPlayerDetailId,
    sendPending,
    socketStatus,
    startAdventure,
    turnRowCount,
  ])

  const showTitleScreen =
    backendReady &&
    authRequired === false &&
    !runtimeSettingsOpen &&
    !workspaceLoading &&
    !selectedCampaignId &&
    !selectedSessionId &&
    !selectedPlayerId &&
    !modalOpen &&
    (campaignCount === 0 || !titleScreenDismissed)

  return {
    createCampaignFromTitleScreen,
    continueFromTitleScreen,
    playNowFromTitleScreen,
    playNowPending,
    showTitleScreen,
    titleScreenCanContinue: campaignCount > 0,
  }
}
