import { useMemo, useReducer } from 'react'
import type {
  Campaign,
  CampaignWorkspace,
  Player,
  PlayerDetail,
  SessionLogEntry,
  SessionState,
  SessionSummary,
} from './types'
import {
  cacheCampaignWorkspace,
  cacheRootCampaigns,
  createWorkspaceCache,
  removeCampaign,
  selectCampaign,
  selectCampaigns,
  selectMaps,
  selectPlayers,
  selectSegments,
  selectSessions,
  upsertCampaign,
  upsertPlayer,
  upsertSession,
  type WorkspaceCache,
} from './workspaceCache'

type ValueUpdater<T> = T | ((current: T) => T)

export type SessionPage = {
  sessionId: number
  entries: SessionLogEntry[]
  nextCursor: number | null
  hasMore: boolean
  sessionState: SessionState
}

type LoadedLog = {
  entries: SessionLogEntry[]
  cursor: number | null
  hasMore: boolean
}

type WorkspaceStoreState = {
  cache: WorkspaceCache
  selectedCampaignId: number | null
  selectedSessionId: number | null
  selectedPlayerId: number | null
  playerDetail: PlayerDetail | null
  sessionState: SessionState | null
  logEntries: SessionLogEntry[]
  // Session whose first log page is in logEntries; logs, cursor and state are
  // only ever shown for this session.
  sessionLogSessionId: number | null
  sessionLogCursor: number | null
  sessionLogHasMore: boolean
  workspaceLoading: boolean
  loadingCampaignId: number | null
  sessionLoading: boolean
}

type WorkspaceStoreInitialSelection = {
  selectedCampaignId: number | null
  selectedSessionId: number | null
  selectedPlayerId: number | null
}

type WorkspaceStoreAction =
  | { type: 'setSelectedCampaignId'; value: ValueUpdater<number | null> }
  | { type: 'setSelectedSessionId'; value: ValueUpdater<number | null> }
  | { type: 'setSelectedPlayerId'; value: ValueUpdater<number | null> }
  | { type: 'setPlayerDetail'; value: ValueUpdater<PlayerDetail | null> }
  | { type: 'setSessionState'; value: ValueUpdater<SessionState | null> }
  | { type: 'setLogEntries'; value: ValueUpdater<SessionLogEntry[]> }
  | { type: 'setSessionLogCursor'; value: ValueUpdater<number | null> }
  | { type: 'setSessionLogHasMore'; value: ValueUpdater<boolean> }
  | { type: 'setWorkspaceLoading'; value: ValueUpdater<boolean> }
  | { type: 'setLoadingCampaignId'; value: ValueUpdater<number | null> }
  | { type: 'setSessionLoading'; value: ValueUpdater<boolean> }
  | { type: 'sessionPageLoaded'; page: SessionPage }
  | { type: 'rootCampaignsLoaded'; campaigns: Campaign[] }
  | { type: 'campaignWorkspaceLoaded'; workspace: CampaignWorkspace }
  | { type: 'campaignUpserted'; campaign: Campaign }
  | { type: 'campaignRemoved'; campaignId: number }
  | { type: 'sessionUpserted'; session: SessionSummary }
  | { type: 'playerUpserted'; player: Player }

function resolveValue<T>(current: T, value: ValueUpdater<T>) {
  return typeof value === 'function' ? (value as (current: T) => T)(current) : value
}

/**
 * Merges a freshly loaded newest log page into the history already loaded for
 * the same session: older pages stay, the newest page replaces its own range,
 * and the cursor/hasMore keep describing the oldest loaded page.
 */
function mergeNewestLogPage(current: LoadedLog, page: LoadedLog): LoadedLog {
  if (!page.entries.length) return page
  const oldestPageEntryId = Math.min(...page.entries.map((entry) => entry.id))
  const olderEntries = current.entries.filter((entry) => entry.id < oldestPageEntryId)
  if (!olderEntries.length) return page
  return {
    entries: [...olderEntries, ...page.entries],
    cursor: current.cursor,
    hasMore: current.hasMore,
  }
}

function createInitialState(selection: WorkspaceStoreInitialSelection): WorkspaceStoreState {
  return {
    cache: createWorkspaceCache(),
    selectedCampaignId: selection.selectedCampaignId,
    selectedSessionId: selection.selectedSessionId,
    selectedPlayerId: selection.selectedPlayerId,
    playerDetail: null,
    sessionState: null,
    logEntries: [],
    sessionLogSessionId: null,
    sessionLogCursor: null,
    sessionLogHasMore: false,
    workspaceLoading: false,
    loadingCampaignId: null,
    sessionLoading: false,
  }
}

function workspaceStoreReducer(
  state: WorkspaceStoreState,
  action: WorkspaceStoreAction,
): WorkspaceStoreState {
  switch (action.type) {
    case 'setSelectedCampaignId':
      return { ...state, selectedCampaignId: resolveValue(state.selectedCampaignId, action.value) }
    case 'setSelectedSessionId': {
      const selectedSessionId = resolveValue(state.selectedSessionId, action.value)
      if (selectedSessionId === state.selectedSessionId) return state
      // Another session's story must never render under the new selection.
      return {
        ...state,
        selectedSessionId,
        logEntries: [],
        sessionLogSessionId: null,
        sessionLogCursor: null,
        sessionLogHasMore: false,
        sessionState: null,
      }
    }
    case 'setSelectedPlayerId':
      return { ...state, selectedPlayerId: resolveValue(state.selectedPlayerId, action.value) }
    case 'setPlayerDetail':
      return { ...state, playerDetail: resolveValue(state.playerDetail, action.value) }
    case 'setSessionState':
      return { ...state, sessionState: resolveValue(state.sessionState, action.value) }
    case 'setLogEntries':
      return { ...state, logEntries: resolveValue(state.logEntries, action.value) }
    case 'setSessionLogCursor':
      return { ...state, sessionLogCursor: resolveValue(state.sessionLogCursor, action.value) }
    case 'setSessionLogHasMore':
      return { ...state, sessionLogHasMore: resolveValue(state.sessionLogHasMore, action.value) }
    case 'setWorkspaceLoading':
      return { ...state, workspaceLoading: resolveValue(state.workspaceLoading, action.value) }
    case 'setLoadingCampaignId':
      return { ...state, loadingCampaignId: resolveValue(state.loadingCampaignId, action.value) }
    case 'setSessionLoading':
      return { ...state, sessionLoading: resolveValue(state.sessionLoading, action.value) }
    case 'sessionPageLoaded': {
      const { page } = action
      const newestPage = { entries: page.entries, cursor: page.nextCursor, hasMore: page.hasMore }
      const log = state.sessionLogSessionId === page.sessionId
        ? mergeNewestLogPage(
            { entries: state.logEntries, cursor: state.sessionLogCursor, hasMore: state.sessionLogHasMore },
            newestPage,
          )
        : newestPage
      return {
        ...state,
        logEntries: log.entries,
        sessionLogSessionId: page.sessionId,
        sessionLogCursor: log.cursor,
        sessionLogHasMore: log.hasMore,
        sessionState: page.sessionState,
      }
    }
    case 'rootCampaignsLoaded':
      return { ...state, cache: cacheRootCampaigns(state.cache, action.campaigns) }
    case 'campaignWorkspaceLoaded':
      return { ...state, cache: cacheCampaignWorkspace(state.cache, action.workspace) }
    case 'campaignUpserted':
      return { ...state, cache: upsertCampaign(state.cache, action.campaign) }
    case 'campaignRemoved':
      return { ...state, cache: removeCampaign(state.cache, action.campaignId) }
    case 'sessionUpserted':
      return { ...state, cache: upsertSession(state.cache, action.session) }
    case 'playerUpserted':
      return { ...state, cache: upsertPlayer(state.cache, action.player) }
    default:
      return state
  }
}

export function useWorkspaceStore(selection: WorkspaceStoreInitialSelection) {
  const [state, dispatch] = useReducer(
    workspaceStoreReducer,
    selection,
    createInitialState,
  )

  const actions = useMemo(
    () => ({
      setSelectedCampaignId: (value: ValueUpdater<number | null>) =>
        dispatch({ type: 'setSelectedCampaignId', value }),
      setSelectedSessionId: (value: ValueUpdater<number | null>) =>
        dispatch({ type: 'setSelectedSessionId', value }),
      setSelectedPlayerId: (value: ValueUpdater<number | null>) =>
        dispatch({ type: 'setSelectedPlayerId', value }),
      setPlayerDetail: (value: ValueUpdater<PlayerDetail | null>) =>
        dispatch({ type: 'setPlayerDetail', value }),
      setSessionState: (value: ValueUpdater<SessionState | null>) =>
        dispatch({ type: 'setSessionState', value }),
      setLogEntries: (value: ValueUpdater<SessionLogEntry[]>) =>
        dispatch({ type: 'setLogEntries', value }),
      setSessionLogCursor: (value: ValueUpdater<number | null>) =>
        dispatch({ type: 'setSessionLogCursor', value }),
      setSessionLogHasMore: (value: ValueUpdater<boolean>) =>
        dispatch({ type: 'setSessionLogHasMore', value }),
      setWorkspaceLoading: (value: ValueUpdater<boolean>) =>
        dispatch({ type: 'setWorkspaceLoading', value }),
      setLoadingCampaignId: (value: ValueUpdater<number | null>) =>
        dispatch({ type: 'setLoadingCampaignId', value }),
      setSessionLoading: (value: ValueUpdater<boolean>) =>
        dispatch({ type: 'setSessionLoading', value }),
      sessionPageLoaded: (page: SessionPage) =>
        dispatch({ type: 'sessionPageLoaded', page }),
      rootCampaignsLoaded: (campaigns: Campaign[]) =>
        dispatch({ type: 'rootCampaignsLoaded', campaigns }),
      campaignWorkspaceLoaded: (workspace: CampaignWorkspace) =>
        dispatch({ type: 'campaignWorkspaceLoaded', workspace }),
      campaignUpserted: (campaign: Campaign) =>
        dispatch({ type: 'campaignUpserted', campaign }),
      campaignRemoved: (campaignId: number) =>
        dispatch({ type: 'campaignRemoved', campaignId }),
      sessionUpserted: (session: SessionSummary) =>
        dispatch({ type: 'sessionUpserted', session }),
      playerUpserted: (player: Player) =>
        dispatch({ type: 'playerUpserted', player }),
    }),
    [],
  )

  const campaigns = useMemo(() => selectCampaigns(state.cache), [state.cache])
  const campaign = useMemo(
    () => selectCampaign(state.cache, state.selectedCampaignId),
    [state.cache, state.selectedCampaignId],
  )
  const sessions = useMemo(
    () => selectSessions(state.cache, state.selectedCampaignId),
    [state.cache, state.selectedCampaignId],
  )
  const players = useMemo(
    () => selectPlayers(state.cache, state.selectedCampaignId),
    [state.cache, state.selectedCampaignId],
  )
  const maps = useMemo(
    () => selectMaps(state.cache, state.selectedCampaignId),
    [state.cache, state.selectedCampaignId],
  )
  const segments = useMemo(
    () => selectSegments(state.cache, state.selectedCampaignId),
    [state.cache, state.selectedCampaignId],
  )

  const sessionHydrated =
    state.selectedSessionId !== null &&
    state.sessionLogSessionId === state.selectedSessionId &&
    state.sessionState?.session_id === state.selectedSessionId

  return {
    ...state,
    sessionHydrated,
    campaigns,
    campaign,
    sessions,
    players,
    maps,
    segments,
    ...actions,
  }
}
