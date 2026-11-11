import { useCallback, useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from 'react'
import { apiFetch } from './api'
import { isRecord } from './gameSelectors'
import type { MainTab } from './SessionBoard'
import type { TitleHero } from './story/TitleHeroes'
import type { TitleResume, TitleScreenProps } from './TitleScreen'
import type {
  AccountSession,
  Campaign,
  CampaignWorkspace,
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
const TITLE_HERO_STORAGE_KEY = 'aidm:titleHero'
const PREGENERATED_CHARACTERS_PATH = '/api/onboarding/pregenerated-characters'
const NO_CAMPAIGNS: Campaign[] = []

type ValueUpdater<T> = T | ((current: T) => T)

type PregeneratedCharactersResponse = {
  characters?: unknown
  default_character_id?: unknown
}

type HeroLibrary =
  | { status: 'idle' }
  | { status: 'ready'; heroes: TitleHero[]; defaultHeroId: string }
  // `requestKey` names the credentials that failed; new credentials try again.
  | { status: 'failed'; requestKey: string }

type ResumeChapter = {
  campaignId: number
  sessionId: number | null
  chapterNumber: number | null
  scene: string | null
  lastPlayedAt: string | null
  continueLine: string | null
  /** A turn has been played: the current chapter has turns, or it follows an earlier one. */
  begun: boolean
}

type UsePlayNowOnboardingOptions = {
  activeSessionId: number | null
  auth: string
  authRequired: boolean | null
  hostedAccessReady?: boolean
  backendReady: boolean
  baseUrl: string
  campaignCount: number
  /** The already-loaded campaign list; the most recently played one is offered to resume. */
  campaigns?: Campaign[]
  /** No AI provider is active, so the scripted narrator runs. */
  safeMode?: boolean
  /** The Story view is the play view; without an adventure it has nothing to show, so the title screen does. */
  storyView?: boolean
  openCampaignChooser?: () => void
  closeMobilePanels: () => void
  modalOpen: boolean
  runtimeSettingsOpen: boolean
  selectedCampaignId: number | null
  selectedPlayerDetailId: number | null
  selectedPlayerId: number | null
  selectedSessionId: number | null
  workspaceLoading: boolean
  sessionLoading: boolean
  loadedSessionId: number | null
  campaignUpserted: (campaign: Campaign) => void
  sessionUpserted: (session: SessionSummary) => void
  playerUpserted: (player: PlayerDetail) => void
  adoptAccountSession?: (session: AccountSession) => void | Promise<void>
  clearAuthTokenErrors: () => void
  loadPlayerDetail: (playerId: number) => Promise<void>
  loadSessionData: (sessionId: number) => Promise<void>
  openCreateCampaignDialog: () => void
  openLogIn?: () => void
  openCreateAccount?: () => void
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

function readStoredHeroId() {
  try {
    return localStorage.getItem(TITLE_HERO_STORAGE_KEY)
  } catch {
    return null
  }
}

function text(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function titleHeroesFromPayload(payload: PregeneratedCharactersResponse): TitleHero[] {
  const characters = Array.isArray(payload.characters) ? payload.characters.filter(isRecord) : []
  return characters
    .map((character) => ({
      id: text(character.id),
      name: text(character.character_name),
      race: text(character.race),
      charClass: text(character.char_class),
      tagline: text(character.tagline),
      portrait: text(character.portrait),
    }))
    .filter((hero) => hero.id && hero.name)
}

function campaignActivityAt(campaign: Campaign) {
  return campaign.latest_activity_at ?? campaign.updated_at ?? campaign.created_at
}

function latestCampaign(campaigns: Campaign[]) {
  let latest: Campaign | null = null
  let latestTime = -Infinity
  for (const campaign of campaigns) {
    if (campaign.is_archived) continue
    const time = Date.parse(campaignActivityAt(campaign) ?? '')
    const comparable = Number.isFinite(time) ? time : -Infinity
    if (!latest || comparable > latestTime) {
      latest = campaign
      latestTime = comparable
    }
  }
  return latest
}

function currentChapterSession(sessions: SessionSummary[]) {
  return sessions.find((session) => session.is_current) ?? sessions[0] ?? null
}

// The rolling summary is never shown on the title screen: only the server's continue line.
function resumeChapterFromWorkspace(campaignId: number, workspace: CampaignWorkspace): ResumeChapter {
  const session = currentChapterSession(workspace.sessions ?? [])
  return {
    campaignId,
    sessionId: session?.session_id ?? null,
    chapterNumber: session?.chapter_number ?? null,
    scene: text(session?.scene_title) || text(workspace.campaign?.location) || null,
    lastPlayedAt: session?.latest_activity_at ?? null,
    continueLine: text(session?.continue_line) || null,
    begun: Boolean(session && (session.turn_count > 0 || session.chapter_number > 1)),
  }
}

export function usePlayNowOnboarding({
  activeSessionId,
  auth,
  authRequired,
  hostedAccessReady = false,
  backendReady,
  baseUrl,
  campaignCount,
  campaigns = NO_CAMPAIGNS,
  safeMode = false,
  storyView = false,
  openCampaignChooser,
  closeMobilePanels,
  modalOpen,
  runtimeSettingsOpen,
  selectedCampaignId,
  selectedPlayerDetailId,
  selectedPlayerId,
  selectedSessionId,
  workspaceLoading,
  sessionLoading,
  loadedSessionId,
  campaignUpserted,
  sessionUpserted,
  playerUpserted,
  adoptAccountSession,
  clearAuthTokenErrors,
  loadPlayerDetail,
  loadSessionData,
  openCreateCampaignDialog,
  openLogIn,
  openCreateAccount,
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
  const [titleRequested, setTitleRequested] = useState(false)
  const [playNowPending, setPlayNowPending] = useState(false)
  const [heroLibrary, setHeroLibrary] = useState<HeroLibrary>({ status: 'idle' })
  const [storedHeroId, setStoredHeroId] = useState(readStoredHeroId)
  const [resumeChapter, setResumeChapter] = useState<ResumeChapter | null>(null)
  const playNowPendingRef = useRef(false)
  const playNowAutoStartRef = useRef(false)
  const heroRequestKeyRef = useRef<string | null>(null)
  const resumeRequestRef = useRef<number | null>(null)

  const titleScreenAccountReady = authRequired !== true || hostedAccessReady
  const selectedHeroId =
    heroLibrary.status === 'ready'
      ? heroLibrary.heroes.some((hero) => hero.id === storedHeroId)
        ? storedHeroId
        : heroLibrary.defaultHeroId
      : null
  const resumeCampaign = useMemo(() => latestCampaign(campaigns), [campaigns])
  const resumeCampaignId = resumeCampaign?.campaign_id ?? null
  const resumeChapterForCampaign = resumeChapter?.campaignId === resumeCampaignId ? resumeChapter : null

  const rememberTitleScreenChoice = useCallback(() => {
    try {
      localStorage.setItem(PLAY_NOW_SEEN_STORAGE_KEY, 'true')
    } catch {
      // The current browser session can continue even when storage is unavailable.
    }
    setTitleScreenDismissed(true)
    setTitleRequested(false)
  }, [])

  const returnToTitle = useCallback(() => {
    closeMobilePanels()
    setTitleRequested(true)
  }, [closeMobilePanels])

  const selectTitleHero = useCallback((heroId: string) => {
    setStoredHeroId(heroId)
    try {
      localStorage.setItem(TITLE_HERO_STORAGE_KEY, heroId)
    } catch {
      // The choice still holds for this visit when storage is unavailable.
    }
  }, [])

  const logInFromTitleScreen = useCallback(() => {
    closeMobilePanels()
    openLogIn?.()
  }, [closeMobilePanels, openLogIn])

  const createAccountFromTitleScreen = useCallback(() => {
    closeMobilePanels()
    openCreateAccount?.()
  }, [closeMobilePanels, openCreateAccount])

  const continueFromTitleScreen = useCallback(() => {
    if (authRequired === true && !hostedAccessReady) {
      logInFromTitleScreen()
      return
    }
    if (resumeCampaignId !== null) {
      setSelectedCampaignId(resumeCampaignId)
      if (resumeChapterForCampaign?.sessionId) setSelectedSessionId(resumeChapterForCampaign.sessionId)
      setMainTab('turns')
    }
    rememberTitleScreenChoice()
    closeMobilePanels()
  }, [
    authRequired,
    closeMobilePanels,
    hostedAccessReady,
    logInFromTitleScreen,
    rememberTitleScreenChoice,
    resumeCampaignId,
    resumeChapterForCampaign,
    setMainTab,
    setSelectedCampaignId,
    setSelectedSessionId,
  ])

  const openAdventuresFromTitleScreen = useCallback(() => {
    if (authRequired === true && !hostedAccessReady) {
      logInFromTitleScreen()
      return
    }
    setTitleRequested(false)
    closeMobilePanels()
    openCampaignChooser?.()
  }, [authRequired, closeMobilePanels, hostedAccessReady, logInFromTitleScreen, openCampaignChooser])

  const createCampaignFromTitleScreen = useCallback(() => {
    if (authRequired === true && !hostedAccessReady) {
      logInFromTitleScreen()
      return
    }
    rememberTitleScreenChoice()
    closeMobilePanels()
    openCreateCampaignDialog()
  }, [
    authRequired,
    closeMobilePanels,
    hostedAccessReady,
    logInFromTitleScreen,
    openCreateCampaignDialog,
    rememberTitleScreenChoice,
  ])

  // Without the hero list the server's default hero plays, exactly as before heroes were choosable.
  const playNowCharacterId = heroLibrary.status === 'ready' ? selectedHeroId : null

  // `fresh` always starts a new ready-made adventure; the first Play now sends nothing. `examplePackId` names the
  // ready-made adventure (an ending's Play it again); without it the server's default.
  const playNowFromTitleScreen = useCallback(async ({ fresh = false, examplePackId }: { fresh?: boolean; examplePackId?: string } = {}) => {
    if (playNowPendingRef.current) return
    const hostedGuestPlayNow = authRequired === true && !hostedAccessReady
    playNowPendingRef.current = true
    setPlayNowPending(true)
    try {
      const payload = await apiFetch<PlayNowResponse>(
        baseUrl,
        hostedGuestPlayNow ? '/api/accounts/play-now' : '/api/onboarding/play-now',
        auth,
        {
          method: 'POST',
          body: JSON.stringify({
            ...(playNowCharacterId ? { character_id: playNowCharacterId } : {}),
            ...(fresh ? { fresh: true } : {}),
            ...(examplePackId ? { example_pack_id: examplePackId } : {}),
          }),
        },
      )
      if (hostedGuestPlayNow && !payload.account_session) {
        throw new Error('Play Now did not return guest account access.')
      }
      if (payload.account_session) {
        if (!adoptAccountSession) {
          throw new Error('Play Now could not activate the guest account session.')
        }
        await adoptAccountSession(payload.account_session)
      }
      try {
        localStorage.setItem('aidm:workspaceId', payload.workspace_id)
      } catch {
        // The default local workspace still works if localStorage is unavailable.
      }
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
        // Armed only once the table is hydrated, so the opening is sent once
        // against the loaded session rather than racing the refresh chain.
        playNowAutoStartRef.current = payload.session.turn_count === 0
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
    adoptAccountSession,
    auth,
    authRequired,
    baseUrl,
    campaignUpserted,
    clearAuthTokenErrors,
    closeMobilePanels,
    loadPlayerDetail,
    loadSessionData,
    hostedAccessReady,
    playerUpserted,
    playNowCharacterId,
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
      dmResponseBlocking ||
      playNowPending ||
      workspaceLoading ||
      sessionLoading ||
      loadedSessionId !== activeSessionId
    ) {
      return
    }
    playNowAutoStartRef.current = false
    startAdventure()
  }, [
    activeSessionId,
    currentResponsePresent,
    dmResponseBlocking,
    loadedSessionId,
    playNowPending,
    selectedPlayerDetailId,
    sendPending,
    sessionLoading,
    socketStatus,
    startAdventure,
    turnRowCount,
    workspaceLoading,
  ])

  const hostedFirstRun = authRequired === true && !hostedAccessReady
  const showTitleScreen =
    backendReady &&
    authRequired !== null &&
    !runtimeSettingsOpen &&
    !workspaceLoading &&
    !modalOpen &&
    (
      titleRequested ||
      hostedFirstRun ||
      (storyView && !selectedCampaignId) ||
      (
        !selectedCampaignId &&
        !selectedSessionId &&
        !selectedPlayerId &&
        (campaignCount === 0 || (authRequired === false && !titleScreenDismissed))
      )
    )

  const heroRequestKey = `${baseUrl}|${auth}|${titleScreenAccountReady}`
  useEffect(() => {
    if (!showTitleScreen || heroLibrary.status === 'ready') return
    if (heroLibrary.status === 'failed' && heroLibrary.requestKey === heroRequestKey) return
    if (heroRequestKeyRef.current === heroRequestKey) return
    heroRequestKeyRef.current = heroRequestKey
    apiFetch<PregeneratedCharactersResponse>(baseUrl, PREGENERATED_CHARACTERS_PATH, auth)
      .then((payload) => {
        const heroes = titleHeroesFromPayload(payload)
        if (!heroes.length) throw new Error('No ready-made heroes are available.')
        const defaultId = text(payload.default_character_id)
        setHeroLibrary({
          status: 'ready',
          heroes,
          defaultHeroId: heroes.some((hero) => hero.id === defaultId) ? defaultId : heroes[0].id,
        })
      })
      .catch(() =>
        setHeroLibrary((current) =>
          current.status === 'ready' ? current : { status: 'failed', requestKey: heroRequestKey },
        ),
      )
      .finally(() => {
        if (heroRequestKeyRef.current === heroRequestKey) heroRequestKeyRef.current = null
      })
  }, [auth, baseUrl, heroLibrary, heroRequestKey, showTitleScreen])

  // Read the resumable campaign's current chapter each time the title screen opens.
  useEffect(() => {
    if (!showTitleScreen || resumeCampaignId === null) {
      resumeRequestRef.current = null
      return
    }
    if (resumeRequestRef.current === resumeCampaignId) return
    resumeRequestRef.current = resumeCampaignId
    apiFetch<CampaignWorkspace>(baseUrl, `/api/campaigns/${resumeCampaignId}/workspace`, auth)
      .then((workspace) => setResumeChapter(resumeChapterFromWorkspace(resumeCampaignId, workspace)))
      // The block still offers the campaign from the list when its chapter cannot be read.
      .catch(() => undefined)
  }, [auth, baseUrl, resumeCampaignId, showTitleScreen])

  const resume: TitleResume | null = resumeCampaign
    ? {
        title: resumeCampaign.title,
        chapterNumber: resumeChapterForCampaign?.chapterNumber ?? null,
        scene: resumeChapterForCampaign?.scene ?? (text(resumeCampaign.location) || null),
        lastPlayedAt: resumeChapterForCampaign?.lastPlayedAt ?? campaignActivityAt(resumeCampaign),
        continueLine: resumeChapterForCampaign?.continueLine ?? null,
        // Until its chapter is read, an adventure with chapters counts as begun.
        begun: resumeChapterForCampaign ? resumeChapterForCampaign.begun : resumeCampaign.session_count > 0,
      }
    : null

  const titleScreenProps: TitleScreenProps = {
    pending: playNowPending,
    accountReady: titleScreenAccountReady,
    heroes: heroLibrary.status === 'ready' ? heroLibrary.heroes : [],
    heroesStatus: heroLibrary.status === 'idle' ? 'loading' : heroLibrary.status,
    selectedHeroId,
    onSelectHero: selectTitleHero,
    resume,
    safeMode,
    onPlayNow: (options) => void playNowFromTitleScreen(options),
    onContinue: continueFromTitleScreen,
    onCreateCampaign: createCampaignFromTitleScreen,
    onOpenAdventures: openAdventuresFromTitleScreen,
    onLogIn: logInFromTitleScreen,
    onCreateAccount: createAccountFromTitleScreen,
  }

  return {
    createAccountFromTitleScreen,
    createCampaignFromTitleScreen,
    continueFromTitleScreen,
    logInFromTitleScreen,
    playNowFromTitleScreen,
    playNowPending,
    returnToTitle,
    showTitleScreen,
    titleScreenAccountReady,
    titleScreenCanContinue: campaignCount > 0,
    titleScreenProps,
  }
}
