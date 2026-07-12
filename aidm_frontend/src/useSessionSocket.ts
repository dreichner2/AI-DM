import {
  useEffect,
  useRef,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react'
import { io, type Socket } from 'socket.io-client'
import {
  isBackendOriginTrusted,
  ngrokBrowserWarningBypassHeaders,
  normalizeBaseUrl,
  storedWorkspaceId,
  storedWorkspaceToken,
} from './api'
import { stringValue, turnStatusAllowsNextSend } from './gameSelectors'
import type { RollMode, RollResolvedPayload } from './gameActions'
import type { SceneMusicSyncState } from './SceneMusicPlayer'
import { normalizeSceneState, type SceneDisplayState, type SceneStatePayload } from './sceneState'
import { normalizeTurnControl } from './turnControl'
import type {
  ActivePlayer,
  ClarificationRequest,
  JsonRecord,
  RulesHint,
  SessionState,
  SocketErrorPayload,
  StreamingTurn,
  TimelineEntry,
} from './types'

type TurnStatusPayload = {
  session_id?: number
  turn_id?: number | null
  status?: string
  details?: JsonRecord
}

type DmResponseEndPayload = {
  session_id?: number
  turn_id?: number
  turn_number?: number | null
  text?: string
  requires_roll?: boolean
  rules_hint?: RulesHint
  ok?: boolean
  error?: string
}

type NewMessagePayload = {
  message?: string
  speaker?: string
  turn_id?: number
  turn_number?: number | null
  requires_roll?: boolean
  rules_hint?: RulesHint
  context_version?: string
  action_intent?: JsonRecord
  client_message_id?: string | null
}

type MusicStatePayload = {
  session_id?: number
  sessionId?: number
  track_id?: string
  trackId?: string
  status?: string
  position?: number
  updated_at_ms?: number
  updatedAtMs?: number
  updated_by_player_id?: number | null
  updatedByPlayerId?: number | null
}

type SocketErrorCategory = 'connection' | 'workspace'

export type TurnDuplicatePayload = {
  session_id: number
  turn_id: number
  client_message_id: string
}

type UseSessionSocketOptions = {
  auth: string
  baseUrl: string
  selectedSessionId: number | null
  selectedPlayerId: number | null
  selectedCampaignId: number | null
  socketReconnectKey: number
  socketRef: MutableRefObject<Socket | null>
  loadSessionData: (sessionId: number) => Promise<void>
  refreshPlayerDetail: (playerId: number) => Promise<void>
  pushError: (category: SocketErrorCategory, message: string) => void
  rememberStreamedTtsTurn: (turnId: number, text: string) => void
  resetTtsFailureForNextResponse: () => void
  stopTtsAudio: (options?: { suppressQueue?: boolean }) => void
  setActivePlayers: Dispatch<SetStateAction<ActivePlayer[]>>
  setSessionState: Dispatch<SetStateAction<SessionState | null>>
  setSocketStatus: Dispatch<SetStateAction<string>>
  setSendPending: Dispatch<SetStateAction<boolean>>
  setOptimisticEntries: Dispatch<SetStateAction<TimelineEntry[]>>
  setStreamingTurn: Dispatch<SetStateAction<StreamingTurn | null>>
  setTurnStatuses: Dispatch<SetStateAction<Record<number, string>>>
  setClarificationRequest: Dispatch<SetStateAction<ClarificationRequest | null>>
  setSceneMusicSyncState: Dispatch<SetStateAction<SceneMusicSyncState | null>>
  setSceneState: Dispatch<SetStateAction<SceneDisplayState | null>>
  spokenTextLengthRef: MutableRefObject<number>
  speakableStreamingTextRef: MutableRefObject<string>
  queueTtsNarrationRef: MutableRefObject<((text: string) => void) | null>
  ttsEnabledRef: MutableRefObject<boolean>
  ttsQueueSuppressedRef: MutableRefObject<boolean>
  ttsFailureReportedRef: MutableRefObject<boolean>
  ttsPartialFlushTimerRef: MutableRefObject<number | null>
  lastSpokenDmEntryRef: MutableRefObject<string | null>
  lastSpokenTurnIdRef: MutableRefObject<number | null>
  lastSpokenTextRef: MutableRefObject<string | null>
  onConnectionInterrupted: () => void
  onRollResolved: (payload: RollResolvedPayload) => void
  onTurnDuplicate: (payload: TurnDuplicatePayload) => void
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null
}

function positiveInteger(value: unknown) {
  const number = Number(value)
  return Number.isInteger(number) && number > 0 ? number : null
}

function finiteNumber(value: unknown) {
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function normalizeRollResolvedPayload(payload: unknown): RollResolvedPayload | null {
  const value = recordValue(payload)
  if (!value || value.authoritative !== true) return null
  const sessionId = positiveInteger(value.session_id)
  const turnId = positiveInteger(value.turn_id)
  const playerId = positiveInteger(value.player_id)
  const clientMessageId = value.client_message_id === null ? null : stringValue(value.client_message_id)
  const pendingTurnId = value.pending_turn_id === null || value.pending_turn_id === undefined
    ? null
    : positiveInteger(value.pending_turn_id)
  const die = stringValue(value.die).toLowerCase()
  const sides = Number(die.replace(/^d/, ''))
  const mode = stringValue(value.mode) as RollMode
  const rolls = Array.isArray(value.rolls) ? value.rolls.map(Number) : []
  const kept = finiteNumber(value.kept)
  const modifier = finiteNumber(value.modifier)
  const total = finiteNumber(value.total)
  const resultVisibility = stringValue(value.result_visibility)
  if (!sessionId || !turnId || !playerId || !/^d(?:4|6|8|10|12|20|100)$/.test(die)) return null
  if (!['normal', 'advantage', 'disadvantage'].includes(mode)) return null
  if (!rolls.length || rolls.some((roll) => !Number.isInteger(roll) || roll < 1 || roll > sides)) return null
  if (kept === null || modifier === null || total === null || !Number.isInteger(kept) || !rolls.includes(kept)) return null
  if (total !== kept + modifier || !['hidden_until_landed', 'visible'].includes(resultVisibility)) return null
  if (value.client_message_id !== null && !clientMessageId) return null
  if (value.pending_turn_id !== null && value.pending_turn_id !== undefined && !pendingTurnId) return null

  let ability: RollResolvedPayload['ability']
  if (value.ability === null) {
    ability = null
  } else if (value.ability !== undefined) {
    const abilityValue = recordValue(value.ability)
    if (!abilityValue) return null
    const key = stringValue(abilityValue.key)
    const label = stringValue(abilityValue.label)
    const abilityResolvedModifier = finiteNumber(abilityValue.modifier)
    const score = abilityValue.score === null || typeof abilityValue.score === 'number' || typeof abilityValue.score === 'string'
      ? abilityValue.score
      : undefined
    if (!key || !label || abilityResolvedModifier === null || score === undefined) return null
    ability = { key, label, score, modifier: abilityResolvedModifier }
  }

  let proficiency: RollResolvedPayload['proficiency']
  if (value.proficiency !== undefined) {
    const proficiencyValue = recordValue(value.proficiency)
    if (!proficiencyValue) return null
    const bonus = finiteNumber(proficiencyValue.bonus)
    const skills = Array.isArray(proficiencyValue.skills)
      ? proficiencyValue.skills.map((skill) => stringValue(skill)).filter(Boolean)
      : null
    if (bonus === null || !skills) return null
    proficiency = { bonus, skills }
  }

  let modifierBreakdown: RollResolvedPayload['modifier_breakdown']
  if (value.modifier_breakdown !== undefined) {
    const breakdownValue = recordValue(value.modifier_breakdown)
    if (!breakdownValue) return null
    const abilityModifier = finiteNumber(breakdownValue.ability_modifier)
    const proficiencyBonus = finiteNumber(breakdownValue.proficiency_bonus)
    const woundPenalty = finiteNumber(breakdownValue.wound_penalty)
    const breakdownTotal = finiteNumber(breakdownValue.total)
    if ([abilityModifier, proficiencyBonus, woundPenalty, breakdownTotal].some((part) => part === null)) return null
    modifierBreakdown = {
      ability_modifier: abilityModifier as number,
      proficiency_bonus: proficiencyBonus as number,
      wound_penalty: woundPenalty as number,
      total: breakdownTotal as number,
    }
  }

  return {
    session_id: sessionId,
    turn_id: turnId,
    player_id: playerId,
    client_message_id: clientMessageId || null,
    pending_turn_id: pendingTurnId,
    rule_type: stringValue(value.rule_type),
    die,
    mode,
    rolls,
    kept,
    modifier,
    total,
    reason: stringValue(value.reason),
    result_visibility: resultVisibility as RollResolvedPayload['result_visibility'],
    ...(ability !== undefined ? { ability } : {}),
    ...(proficiency ? { proficiency } : {}),
    ...(modifierBreakdown ? { modifier_breakdown: modifierBreakdown } : {}),
    authoritative: true,
  }
}

export function normalizeTurnDuplicatePayload(payload: unknown): TurnDuplicatePayload | null {
  const value = recordValue(payload)
  if (!value) return null
  const sessionId = positiveInteger(value.session_id)
  const turnId = positiveInteger(value.turn_id)
  const clientMessageId = stringValue(value.client_message_id)
  return sessionId && turnId && clientMessageId
    ? { session_id: sessionId, turn_id: turnId, client_message_id: clientMessageId }
    : null
}

function normalizeMusicState(payload: MusicStatePayload): SceneMusicSyncState | null {
  const sessionId = Number(payload.session_id ?? payload.sessionId)
  const trackId = stringValue(payload.track_id ?? payload.trackId)
  const status = stringValue(payload.status)
  const position = Number(payload.position)
  const updatedAtMs = Number(payload.updated_at_ms ?? payload.updatedAtMs)
  const updatedByPlayerId = Number(payload.updated_by_player_id ?? payload.updatedByPlayerId)
  if (!Number.isInteger(sessionId) || sessionId <= 0) return null
  if (!trackId || !['playing', 'paused'].includes(status)) return null
  if (!Number.isFinite(position) || position < 0) return null
  if (!Number.isFinite(updatedAtMs) || updatedAtMs <= 0) return null
  return {
    sessionId,
    trackId,
    status: status as SceneMusicSyncState['status'],
    position,
    updatedAtMs,
    receivedAtMs: Date.now(),
    updatedByPlayerId: Number.isInteger(updatedByPlayerId) && updatedByPlayerId > 0 ? updatedByPlayerId : null,
  }
}

function socketMessage(payload: SocketErrorPayload) {
  return payload.error ?? payload.message ?? payload.error_code ?? 'Socket error'
}

function numericArray(value: unknown): number[] {
  if (!Array.isArray(value)) return []
  return value
    .map((entry) => Number(entry))
    .filter((entry) => Number.isInteger(entry) && entry > 0)
}

function normalizeActivePlayers(payload: unknown): ActivePlayer[] {
  if (!Array.isArray(payload)) return []
  return payload
    .map((entry): ActivePlayer | null => {
      if (!entry || typeof entry !== 'object') return null
      const value = entry as Record<string, unknown>
      const id = Number(value.id)
      if (!Number.isInteger(id) || id <= 0) return null
      return {
        id,
        character_name: stringValue(value.character_name) || `Player ${id}`,
        name: stringValue(value.name) || 'Connected player',
        race: stringValue(value.race) || null,
        sex: stringValue(value.sex) || null,
        profile_image: stringValue(value.profile_image) || null,
        class_: stringValue(value.class_) || null,
        char_class: stringValue(value.char_class) || null,
        is_typing: value.is_typing === true,
      }
    })
    .filter((entry): entry is ActivePlayer => entry !== null)
}

function timelineEntryFromNewMessage(payload: NewMessagePayload): TimelineEntry | null {
  const turnId = Number(payload.turn_id)
  const message = stringValue(payload.message)
  const speaker = stringValue(payload.speaker)
  if (!Number.isInteger(turnId) || turnId <= 0 || !message || !speaker) {
    return null
  }
  const clientMessageId = stringValue(payload.client_message_id)
  return {
    id: clientMessageId ? `socket-player-${clientMessageId}` : `socket-player-${turnId}`,
    role: 'player',
    speaker,
    text: message,
    timestamp: null,
    metadata: {
      turn_id: turnId,
      turn_number: typeof payload.turn_number === 'number' ? payload.turn_number : null,
      requires_roll: Boolean(payload.requires_roll),
      rules_hint: payload.rules_hint ?? {},
      context_version: stringValue(payload.context_version) || null,
      action_intent: payload.action_intent ?? null,
      client_message_id: clientMessageId || null,
      persistence_status: 'received',
    },
  }
}

export function buildSessionSocketConnection(baseUrl: string, auth: string) {
  const socketBaseUrl = normalizeBaseUrl(baseUrl)
  const trustedBackend = isBackendOriginTrusted(socketBaseUrl)
  const ngrokBypassHeaders = socketBaseUrl ? ngrokBrowserWarningBypassHeaders(socketBaseUrl) : undefined
  const workspaceToken = trustedBackend ? storedWorkspaceToken(socketBaseUrl).trim() : ''
  const workspaceId = trustedBackend ? storedWorkspaceId(socketBaseUrl).trim() : ''
  const accountToken = trustedBackend ? auth.trim() : ''
  const socketAuth =
    accountToken || workspaceToken || workspaceId
      ? {
          ...(accountToken ? { account_token: accountToken } : {}),
          ...(workspaceToken ? { workspace_token: workspaceToken } : {}),
          ...(!workspaceToken && workspaceId ? { workspace_id: workspaceId } : {}),
          ...(!accountToken && workspaceToken ? { token: workspaceToken } : {}),
        }
      : undefined
  const socketOptions = {
    auth: socketAuth,
    transports: ['websocket', 'polling'],
    ...(!trustedBackend ? { withCredentials: false } : {}),
    ...(ngrokBypassHeaders
      ? {
          extraHeaders: ngrokBypassHeaders,
          transportOptions: {
            polling: {
              extraHeaders: ngrokBypassHeaders,
            },
            websocket: {
              extraHeaders: ngrokBypassHeaders,
            },
          },
        }
      : {}),
  }
  return { socketBaseUrl, socketOptions }
}

export function useSessionSocket({
  auth,
  baseUrl,
  selectedSessionId,
  selectedPlayerId,
  selectedCampaignId,
  socketReconnectKey,
  socketRef,
  loadSessionData,
  refreshPlayerDetail,
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
  onConnectionInterrupted,
  onRollResolved,
  onTurnDuplicate,
}: UseSessionSocketOptions) {
  const lastWorldSnapshotRefreshRef = useRef<{ sessionId: number; turnId: number } | null>(null)
  const lastJoinedSessionRef = useRef<number | null>(null)

  useEffect(() => {
    if (!selectedSessionId || !selectedPlayerId || !selectedCampaignId) {
      socketRef.current?.disconnect()
      socketRef.current = null
      lastWorldSnapshotRefreshRef.current = null
      setActivePlayers([])
      setSceneMusicSyncState(null)
      setSceneState(null)
      setSocketStatus('idle')
      return
    }

    lastWorldSnapshotRefreshRef.current = null
    setSceneMusicSyncState(null)
    setSceneState(null)
    const { socketBaseUrl, socketOptions } = buildSessionSocketConnection(baseUrl, auth)
    const socket = socketBaseUrl ? io(socketBaseUrl, socketOptions) : io(socketOptions)
    socketRef.current = socket
    setSocketStatus('connecting')
    let hasConnected = false
    let refreshInFlight = false

    socket.on('connect', () => {
      const shouldRefreshAfterJoin = hasConnected || lastJoinedSessionRef.current === selectedSessionId
      hasConnected = true
      lastJoinedSessionRef.current = selectedSessionId
      setSendPending(false)
      setSocketStatus('joining')
      socket.emit('join_session', {
        session_id: selectedSessionId,
        player_id: selectedPlayerId,
      })
      if (shouldRefreshAfterJoin && !refreshInFlight) {
        refreshInFlight = true
        void loadSessionData(selectedSessionId)
          .catch((error: unknown) => {
            pushError(
              'workspace',
              `Session refresh after reconnect failed: ${error instanceof Error ? error.message : String(error)}`,
            )
          })
          .finally(() => {
            refreshInFlight = false
          })
      }
    })

    socket.on('connect_error', (error) => {
      onConnectionInterrupted()
      setSendPending(false)
      setSocketStatus('error')
      pushError('connection', `Socket connection failed: ${error.message}`)
    })

    socket.on('active_players', (payload: unknown) => {
      setActivePlayers(normalizeActivePlayers(payload))
      setSocketStatus('joined')
    })

    socket.on('turn_control_updated', (payload: unknown) => {
      if (!payload || typeof payload !== 'object') return
      const value = payload as Record<string, unknown>
      const sessionId = Number(value.session_id ?? value.sessionId)
      if (Number.isInteger(sessionId) && sessionId > 0 && sessionId !== selectedSessionId) return
      const turnControl = normalizeTurnControl(value)
      setSessionState((current) => {
        if (!current) return current
        const currentSnapshot = current.state_snapshot && typeof current.state_snapshot === 'object'
          ? current.state_snapshot
          : {}
        return {
          ...current,
          state_snapshot: {
            ...currentSnapshot,
            turnControl,
          },
        }
      })
    })

    socket.on('music_state', (payload: MusicStatePayload) => {
      if (!payload || typeof payload !== 'object') return
      const musicState = normalizeMusicState(payload)
      if (!musicState || musicState.sessionId !== selectedSessionId) return
      setSceneMusicSyncState(musicState)
    })

    socket.on('scene_state', (payload: SceneStatePayload) => {
      if (!payload || typeof payload !== 'object') return
      const nextSceneState = normalizeSceneState(payload)
      if (!nextSceneState || nextSceneState.sessionId !== selectedSessionId) return
      setSceneState(nextSceneState)
    })

    socket.on('new_message', (payload: NewMessagePayload) => {
      const entry = timelineEntryFromNewMessage(payload)
      if (!entry) return
      setOptimisticEntries((current) => {
        const nextTurnId = entry.metadata.turn_id
        const nextClientMessageId = stringValue(entry.metadata.client_message_id)
        const existingIndex = current.findIndex((item) => {
          const currentTurnId = item.metadata.turn_id
          const currentClientMessageId = stringValue(item.metadata.client_message_id)
          return (
            (typeof nextTurnId === 'number' && currentTurnId === nextTurnId) ||
            (nextClientMessageId && currentClientMessageId === nextClientMessageId) ||
            item.id === entry.id
          )
        })
        if (existingIndex < 0) return [...current, entry]
        return current.map((item, index) => (index === existingIndex ? entry : item))
      })
    })

    socket.on('roll_resolved', (payload: unknown) => {
      const resolved = normalizeRollResolvedPayload(payload)
      if (!resolved || resolved.session_id !== selectedSessionId) return
      onRollResolved(resolved)
    })

    socket.on('turn_duplicate', (payload: unknown) => {
      const duplicate = normalizeTurnDuplicatePayload(payload)
      if (!duplicate || duplicate.session_id !== selectedSessionId) return
      onTurnDuplicate(duplicate)
    })

    socket.on(
      'dm_response_start',
      (payload: {
        turn_id: number
        turn_number?: number | null
        requires_roll?: boolean
        rules_hint?: RulesHint
      }) => {
        resetTtsFailureForNextResponse()
        stopTtsAudio({ suppressQueue: false })
        setClarificationRequest(null)
        setSendPending(true)
        spokenTextLengthRef.current = 0
        setStreamingTurn({
          turnId: payload.turn_id,
          turnNumber: typeof payload.turn_number === 'number' ? payload.turn_number : null,
          text: '',
          requiresRoll: Boolean(payload.requires_roll),
          rulesHint: payload.rules_hint ?? {},
        })
      },
    )

    socket.on(
      'dm_chunk',
      (payload: {
        turn_id: number
        turn_number?: number | null
        chunk?: string
        requires_roll?: boolean
        rules_hint?: RulesHint
      }) => {
        setStreamingTurn((current) => {
          if (!current || current.turnId !== payload.turn_id) {
            return {
              turnId: payload.turn_id,
              turnNumber: typeof payload.turn_number === 'number' ? payload.turn_number : null,
              text: payload.chunk ?? '',
              requiresRoll: Boolean(payload.requires_roll),
              rulesHint: payload.rules_hint ?? {},
            }
          }
          return {
            ...current,
            turnNumber: current.turnNumber ?? (typeof payload.turn_number === 'number' ? payload.turn_number : null),
            text: `${current.text}${payload.chunk ?? ''}`,
            requiresRoll: Boolean(payload.requires_roll),
            rulesHint: payload.rules_hint ?? current.rulesHint,
          }
        })
      },
    )

    socket.on('dm_response_end', (payload: DmResponseEndPayload = {}) => {
      const ok = payload.ok !== false
      setSendPending(false)
      setStreamingTurn((current) => {
        const payloadTurnId = Number(payload.turn_id)
        const turnId = Number.isInteger(payloadTurnId) && payloadTurnId > 0
          ? payloadTurnId
          : current?.turnId ?? null
        const payloadText = typeof payload.text === 'string' ? payload.text : null
        const completedText = ok
          ? payloadText ?? current?.text ?? ''
          : payloadText || current?.text || 'The DM response failed before completing.'
        const turnNumber = typeof payload.turn_number === 'number'
          ? payload.turn_number
          : current?.turnNumber ?? null
        const requiresRoll = payload.requires_roll ?? current?.requiresRoll ?? false
        const rulesHint = payload.rules_hint ?? current?.rulesHint ?? {}

        if (turnId !== null) {
          const completedEntry: TimelineEntry = {
            id: ok ? `stream-${turnId}` : `stream-failed-${turnId}`,
            role: 'dm',
            speaker: 'DM',
            text: completedText,
            timestamp: null,
            metadata: {
              turn_id: turnId,
              turn_number: turnNumber,
              requires_roll: requiresRoll,
              ...(!ok ? { stream_status: 'failed', error: payload.error ?? null } : {}),
              ...rulesHint,
            },
            streaming: false,
          }
          setOptimisticEntries((optimistic) => {
            const existingIndex = optimistic.findIndex(
              (entry) => entry.role === 'dm' && Number(entry.metadata.turn_id) === turnId,
            )
            if (existingIndex < 0) return [...optimistic, completedEntry]
            return optimistic.map((entry, index) => (index === existingIndex ? completedEntry : entry))
          })

          if (ok && completedText) {
            if (!current) spokenTextLengthRef.current = 0
            const cleanText = completedText.replace(/<thought>[\s\S]*?(?:<\/thought>|$)/gi, '')
            const remaining = cleanText.slice(spokenTextLengthRef.current).trim()
            if (
              remaining &&
              ttsEnabledRef.current &&
              !ttsQueueSuppressedRef.current &&
              !ttsFailureReportedRef.current
            ) {
              queueTtsNarrationRef.current?.(remaining)
            }
            lastSpokenDmEntryRef.current = completedEntry.id
            lastSpokenTurnIdRef.current = turnId
            lastSpokenTextRef.current = completedText
            rememberStreamedTtsTurn(turnId, completedText)
          }
        }
        if (ttsPartialFlushTimerRef.current !== null) {
          window.clearTimeout(ttsPartialFlushTimerRef.current)
          ttsPartialFlushTimerRef.current = null
        }
        spokenTextLengthRef.current = 0
        speakableStreamingTextRef.current = ''
        return null
      })
      if (!ok) {
        pushError('connection', payload.error ? `DM response failed: ${payload.error}` : 'DM response failed.')
      }
    })

    socket.on('session_log_update', (payload: { session_id?: number }) => {
      if (payload.session_id === selectedSessionId) {
        loadSessionData(selectedSessionId)
          .then(() => {
            setStreamingTurn(null)
          })
          .catch((error: unknown) => {
            pushError('workspace', `Log refresh failed: ${error instanceof Error ? error.message : String(error)}`)
          })
      }
    })

    socket.on('turn_status', (payload: TurnStatusPayload) => {
      if (payload.session_id !== selectedSessionId || typeof payload.turn_id !== 'number') return
      const status = stringValue(payload.status)
      if (!status) return
      if (turnStatusAllowsNextSend(status, payload.details)) {
        setSendPending(false)
      }
      if (status === 'canon_applied' || status === 'state_applied') {
        const playerId = Number(payload.details?.player_id)
        const affectedPlayerIds = numericArray(payload.details?.affected_player_ids)
        const selectedPlayer = Number(selectedPlayerId)
        const shouldRefreshWorldSnapshot =
          payload.details?.world_state_changed === true || payload.details?.snapshot_changed === true
        if (shouldRefreshWorldSnapshot) {
          const lastRefresh = lastWorldSnapshotRefreshRef.current
          if (!lastRefresh || lastRefresh.sessionId !== selectedSessionId || lastRefresh.turnId !== payload.turn_id) {
            lastWorldSnapshotRefreshRef.current = {
              sessionId: selectedSessionId,
              turnId: payload.turn_id,
            }
            loadSessionData(selectedSessionId).catch((error: unknown) => {
              pushError('workspace', `Session refresh failed: ${error instanceof Error ? error.message : String(error)}`)
            })
          }
        }
        const shouldRefreshSelectedPlayer =
          Number.isInteger(selectedPlayer) &&
          selectedPlayer > 0 &&
          (playerId === selectedPlayer || affectedPlayerIds.includes(selectedPlayer))
        if (shouldRefreshSelectedPlayer) {
          refreshPlayerDetail(selectedPlayer).catch((error: unknown) => {
            pushError('workspace', `Player refresh failed: ${error instanceof Error ? error.message : String(error)}`)
          })
        }
      }
      setTurnStatuses((current) => ({
        ...current,
        [payload.turn_id as number]: status,
      }))
    })

    socket.on('clarification_required', (payload: ClarificationRequest) => {
      if (payload.sessionId !== selectedSessionId || payload.playerId !== selectedPlayerId) return
      setSendPending(false)
      setStreamingTurn(null)
      setClarificationRequest(payload)
    })

    socket.on('error', (payload: SocketErrorPayload) => {
      setSendPending(false)
      setClarificationRequest(null)
      pushError('connection', socketMessage(payload))
    })

    socket.on('disconnect', () => {
      onConnectionInterrupted()
      setSendPending(false)
      setStreamingTurn(null)
      setActivePlayers([])
      setSceneMusicSyncState(null)
      setSceneState(null)
      setSocketStatus('offline')
    })

    return () => {
      socket.emit('leave_session', {
        session_id: selectedSessionId,
        player_id: selectedPlayerId,
      })
      socket.disconnect()
      if (socketRef.current === socket) {
        socketRef.current = null
      }
      setActivePlayers([])
      setSceneMusicSyncState(null)
      setSceneState(null)
    }
  }, [
    auth,
    baseUrl,
    loadSessionData,
    refreshPlayerDetail,
    lastSpokenDmEntryRef,
    lastSpokenTextRef,
    lastSpokenTurnIdRef,
    onConnectionInterrupted,
    onRollResolved,
    onTurnDuplicate,
    pushError,
    queueTtsNarrationRef,
    rememberStreamedTtsTurn,
    resetTtsFailureForNextResponse,
    selectedCampaignId,
    selectedPlayerId,
    selectedSessionId,
    setActivePlayers,
    setOptimisticEntries,
    setClarificationRequest,
    setSceneMusicSyncState,
    setSceneState,
    setSendPending,
    setSessionState,
    setSocketStatus,
    setStreamingTurn,
    setTurnStatuses,
    speakableStreamingTextRef,
    spokenTextLengthRef,
    socketRef,
    socketReconnectKey,
    stopTtsAudio,
    ttsEnabledRef,
    ttsFailureReportedRef,
    ttsPartialFlushTimerRef,
    ttsQueueSuppressedRef,
  ])
}
