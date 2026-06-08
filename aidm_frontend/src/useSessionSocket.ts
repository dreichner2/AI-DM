import {
  useEffect,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from 'react'
import { io, type Socket } from 'socket.io-client'
import { ngrokBrowserWarningBypassHeaders, normalizeBaseUrl } from './api'
import { stringValue } from './gameSelectors'
import type {
  ActivePlayer,
  ClarificationRequest,
  JsonRecord,
  RulesHint,
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

type SocketErrorCategory = 'connection' | 'workspace'

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
  setSocketStatus: Dispatch<SetStateAction<string>>
  setSendPending: Dispatch<SetStateAction<boolean>>
  setOptimisticEntries: Dispatch<SetStateAction<TimelineEntry[]>>
  setStreamingTurn: Dispatch<SetStateAction<StreamingTurn | null>>
  setTurnStatuses: Dispatch<SetStateAction<Record<number, string>>>
  setClarificationRequest: Dispatch<SetStateAction<ClarificationRequest | null>>
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
}

function socketMessage(payload: SocketErrorPayload) {
  return payload.error ?? payload.message ?? payload.error_code ?? 'Socket error'
}

function normalizeActivePlayers(payload: unknown): ActivePlayer[] {
  if (!Array.isArray(payload)) return []
  return payload
    .map((entry) => {
      if (!entry || typeof entry !== 'object') return null
      const value = entry as Record<string, unknown>
      const id = Number(value.id)
      if (!Number.isInteger(id) || id <= 0) return null
      return {
        id,
        character_name: stringValue(value.character_name) || `Player ${id}`,
        name: stringValue(value.name) || 'Connected player',
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
}: UseSessionSocketOptions) {
  useEffect(() => {
    if (!selectedSessionId || !selectedPlayerId || !selectedCampaignId) {
      socketRef.current?.disconnect()
      socketRef.current = null
      setActivePlayers([])
      setSocketStatus('idle')
      return
    }

    const socketBaseUrl = normalizeBaseUrl(baseUrl)
    const ngrokBypassHeaders = socketBaseUrl ? ngrokBrowserWarningBypassHeaders(socketBaseUrl) : undefined
    const socketOptions = {
      auth: auth ? { token: auth } : undefined,
      transports: ['websocket', 'polling'],
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
    const socket = socketBaseUrl ? io(socketBaseUrl, socketOptions) : io(socketOptions)
    socketRef.current = socket
    setSocketStatus('connecting')

    socket.on('connect', () => {
      setSendPending(false)
      setSocketStatus('joining')
      socket.emit('join_session', {
        session_id: selectedSessionId,
        player_id: selectedPlayerId,
      })
    })

    socket.on('connect_error', (error) => {
      setSendPending(false)
      setSocketStatus('error')
      pushError('connection', `Socket connection failed: ${error.message}`)
    })

    socket.on('active_players', (payload: unknown) => {
      setActivePlayers(normalizeActivePlayers(payload))
      setSocketStatus('joined')
    })

    socket.on('new_message', (payload: NewMessagePayload) => {
      const entry = timelineEntryFromNewMessage(payload)
      if (!entry) return
      setOptimisticEntries((current) => {
        const nextTurnId = entry.metadata.turn_id
        const nextClientMessageId = stringValue(entry.metadata.client_message_id)
        const exists = current.some((item) => {
          const currentTurnId = item.metadata.turn_id
          const currentClientMessageId = stringValue(item.metadata.client_message_id)
          return (
            (typeof nextTurnId === 'number' && currentTurnId === nextTurnId) ||
            (nextClientMessageId && currentClientMessageId === nextClientMessageId) ||
            item.id === entry.id
          )
        })
        return exists ? current : [...current, entry]
      })
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
      if (!ok) {
        setSendPending(false)
        setStreamingTurn((current) => {
          if (current) {
            const failedEntry: TimelineEntry = {
              id: `stream-failed-${current.turnId}`,
              role: 'dm',
              speaker: 'DM',
              text: current.text || 'The DM response failed before completing.',
              timestamp: null,
              metadata: {
                turn_id: current.turnId,
                turn_number: current.turnNumber ?? null,
                requires_roll: current.requiresRoll,
                stream_status: 'failed',
                error: payload.error ?? null,
                ...current.rulesHint,
              },
              streaming: false,
            }
            setOptimisticEntries((opt) => [...opt, failedEntry])
          }
          if (ttsPartialFlushTimerRef.current !== null) {
            window.clearTimeout(ttsPartialFlushTimerRef.current)
            ttsPartialFlushTimerRef.current = null
          }
          spokenTextLengthRef.current = 0
          speakableStreamingTextRef.current = ''
          return null
        })
        pushError('connection', payload.error ? `DM response failed: ${payload.error}` : 'DM response failed.')
        return
      }
      setStreamingTurn((current) => {
        if (current) {
          const syntheticEntry: TimelineEntry = {
            id: `stream-${current.turnId}`,
            role: 'dm',
            speaker: 'DM',
            text: current.text,
            timestamp: null,
            metadata: {
              turn_id: current.turnId,
              turn_number: current.turnNumber ?? null,
              requires_roll: current.requiresRoll,
              ...current.rulesHint,
            },
            streaming: false,
          }
          setOptimisticEntries((opt) => [...opt, syntheticEntry])

          if (current.text) {
            const cleanText = current.text.replace(/<thought>[\s\S]*?(?:<\/thought>|$)/gi, '')
            const remaining = cleanText.slice(spokenTextLengthRef.current).trim()
            if (
              remaining &&
              ttsEnabledRef.current &&
              !ttsQueueSuppressedRef.current &&
              !ttsFailureReportedRef.current
            ) {
              queueTtsNarrationRef.current?.(remaining)
            }
            lastSpokenDmEntryRef.current = syntheticEntry.id
            lastSpokenTurnIdRef.current = current.turnId
            lastSpokenTextRef.current = current.text
            rememberStreamedTtsTurn(current.turnId, current.text)
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
    })

    socket.on('session_log_update', (payload: { session_id?: number }) => {
      if (payload.session_id === selectedSessionId) {
        loadSessionData(selectedSessionId)
          .then(() => {
            setOptimisticEntries([])
            setStreamingTurn(null)
            setClarificationRequest(null)
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
      if (status === 'saved' || status === 'failed') {
        setSendPending(false)
      }
      if (status === 'canon_applied' || status === 'state_applied') {
        const playerId = Number(payload.details?.player_id)
        if (Number.isInteger(playerId) && playerId > 0 && playerId === selectedPlayerId) {
          refreshPlayerDetail(playerId).catch((error: unknown) => {
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
      setSendPending(false)
      setStreamingTurn(null)
      setActivePlayers([])
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
    }
  }, [
    auth,
    baseUrl,
    loadSessionData,
    refreshPlayerDetail,
    lastSpokenDmEntryRef,
    lastSpokenTextRef,
    lastSpokenTurnIdRef,
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
    setSendPending,
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
