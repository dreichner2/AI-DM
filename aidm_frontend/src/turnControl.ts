import type { ActivePlayer, JsonRecord, TurnControl, TurnControlMode } from './types'

export const DEFAULT_TURN_CONTROL: TurnControl = {
  mode: 'free',
  source: 'auto',
  focusType: null,
  activePlayerId: null,
  activePlayerName: null,
  participantPlayerIds: [],
  participantPlayerNames: [],
  pendingJoinRequests: [],
  reason: null,
  confidence: null,
  updatedByPlayerId: null,
  updatedAt: null,
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function cleanString(value: unknown) {
  if (typeof value === 'string' && value.trim()) return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return ''
}

function positiveId(value: unknown) {
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function positiveIds(value: unknown) {
  if (!Array.isArray(value)) return []
  const result: number[] = []
  value.forEach((entry) => {
    const parsed = positiveId(entry)
    if (parsed && !result.includes(parsed)) result.push(parsed)
  })
  return result
}

function cleanStringList(value: unknown) {
  if (!Array.isArray(value)) return []
  return value.map(cleanString).filter((entry) => entry)
}

function normalizeMode(value: unknown): TurnControlMode {
  const mode = cleanString(value).toLowerCase()
  return mode === 'spotlight' || mode === 'structured' ? mode : 'free'
}

export function normalizeTurnControl(rawValue: unknown): TurnControl {
  const payload = isRecord(rawValue) && isRecord(rawValue.turnControl)
    ? rawValue.turnControl
    : isRecord(rawValue) && isRecord(rawValue.turn_control)
      ? rawValue.turn_control
      : rawValue
  const raw = isRecord(payload) ? payload : {}
  const mode = normalizeMode(raw.mode)
  const activePlayerId = mode === 'free' ? null : positiveId(raw.activePlayerId ?? raw.active_player_id)
  const participantPlayerIds =
    mode === 'free'
      ? []
      : positiveIds(raw.participantPlayerIds ?? raw.participant_player_ids)
  const normalizedParticipantIds =
    activePlayerId && !participantPlayerIds.includes(activePlayerId)
      ? [activePlayerId, ...participantPlayerIds]
      : participantPlayerIds
  return {
    mode,
    source: cleanString(raw.source) || 'auto',
    focusType: mode === 'free' ? null : cleanString(raw.focusType ?? raw.focus_type) || null,
    activePlayerId,
    activePlayerName: mode === 'free' ? null : cleanString(raw.activePlayerName ?? raw.active_player_name) || null,
    participantPlayerIds: normalizedParticipantIds,
    participantPlayerNames: mode === 'free' ? [] : cleanStringList(raw.participantPlayerNames ?? raw.participant_player_names),
    pendingJoinRequests: Array.isArray(raw.pendingJoinRequests ?? raw.pending_join_requests)
      ? ((raw.pendingJoinRequests ?? raw.pending_join_requests) as JsonRecord[])
      : [],
    reason: cleanString(raw.reason) || null,
    confidence: typeof raw.confidence === 'number' ? Math.max(0, Math.min(1, raw.confidence)) : null,
    updatedByPlayerId: positiveId(raw.updatedByPlayerId ?? raw.updated_by_player_id),
    updatedAt: cleanString(raw.updatedAt ?? raw.updated_at) || null,
  }
}

export function turnControlFromSnapshot(snapshot: JsonRecord | null | undefined): TurnControl {
  if (!isRecord(snapshot)) return DEFAULT_TURN_CONTROL
  return normalizeTurnControl(snapshot.turnControl ?? snapshot.turn_control)
}

export function turnControlWithActiveName(turnControl: TurnControl, activePlayers: ActivePlayer[]): TurnControl {
  if (turnControl.mode === 'free') {
    return turnControl
  }
  const activePlayer = turnControl.activePlayerId
    ? activePlayers.find((player) => player.id === turnControl.activePlayerId)
    : null
  const participantNames = (turnControl.participantPlayerIds ?? []).map((playerId) => {
    const participant = activePlayers.find((player) => player.id === playerId)
    return participant?.character_name ?? participant?.name ?? `Player ${playerId}`
  })
  return {
    ...turnControl,
    activePlayerName:
      turnControl.activePlayerName ??
      (turnControl.activePlayerId
        ? activePlayer?.character_name ?? activePlayer?.name ?? `Player ${turnControl.activePlayerId}`
        : null),
    participantPlayerNames:
      participantNames.length > 0 ? participantNames : turnControl.participantPlayerNames ?? [],
  }
}

export function playerHasTurn(turnControl: TurnControl, selectedPlayerId: number | null): boolean {
  if (turnControl.mode === 'free') return true
  if (!turnControl.activePlayerId) return true
  if (turnControl.mode === 'spotlight' && selectedPlayerId && (turnControl.participantPlayerIds ?? []).includes(selectedPlayerId)) {
    return true
  }
  return selectedPlayerId === turnControl.activePlayerId
}

export function canSubmitWithTurnControl(
  turnControl: TurnControl,
  selectedPlayerId: number | null,
  actionKind: string | null | undefined,
  hasPendingRoll: boolean,
) {
  if (actionKind === 'admin') return true
  if (actionKind === 'roll' && hasPendingRoll) return true
  if (turnControl.mode === 'spotlight') return true
  return playerHasTurn(turnControl, selectedPlayerId)
}

export function turnControlStatusLabel(turnControl: TurnControl) {
  const sourceLabel = turnControl.source === 'ai' ? 'AI' : turnControl.source === 'manual' ? 'Manual' : 'Auto'
  if (turnControl.mode === 'free') return `${sourceLabel}: Free play`
  const participantNames = turnControl.participantPlayerNames ?? []
  if (turnControl.mode === 'spotlight' && participantNames.length > 1) {
    return `${sourceLabel}: Spotlight - ${participantNames.join(', ')}`
  }
  const activeName = turnControl.activePlayerName ?? 'No active player'
  return turnControl.mode === 'spotlight' ? `${sourceLabel}: Spotlight - ${activeName}` : `${sourceLabel}: Structured - ${activeName}`
}

export function turnControlBlockMessage(turnControl: TurnControl) {
  const activeName = turnControl.activePlayerName ?? 'Another player'
  if (turnControl.mode === 'spotlight') {
    return `${activeName} has the spotlight. Your action will ask to join the focused scene.`
  }
  return `${activeName} has the turn. Your action was not sent; your draft is kept.`
}
