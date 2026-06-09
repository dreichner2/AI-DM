import type { ActivePlayer, JsonRecord, TurnControl, TurnControlMode } from './types'

export const DEFAULT_TURN_CONTROL: TurnControl = {
  mode: 'free',
  activePlayerId: null,
  activePlayerName: null,
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
  return {
    mode,
    activePlayerId: mode === 'free' ? null : positiveId(raw.activePlayerId ?? raw.active_player_id),
    activePlayerName: mode === 'free' ? null : cleanString(raw.activePlayerName ?? raw.active_player_name) || null,
    updatedByPlayerId: positiveId(raw.updatedByPlayerId ?? raw.updated_by_player_id),
    updatedAt: cleanString(raw.updatedAt ?? raw.updated_at) || null,
  }
}

export function turnControlFromSnapshot(snapshot: JsonRecord | null | undefined): TurnControl {
  if (!isRecord(snapshot)) return DEFAULT_TURN_CONTROL
  return normalizeTurnControl(snapshot.turnControl ?? snapshot.turn_control)
}

export function turnControlWithActiveName(turnControl: TurnControl, activePlayers: ActivePlayer[]): TurnControl {
  if (turnControl.mode === 'free' || turnControl.activePlayerName || !turnControl.activePlayerId) {
    return turnControl
  }
  const activePlayer = activePlayers.find((player) => player.id === turnControl.activePlayerId)
  return {
    ...turnControl,
    activePlayerName: activePlayer?.character_name ?? activePlayer?.name ?? `Player ${turnControl.activePlayerId}`,
  }
}

export function playerHasTurn(turnControl: TurnControl, selectedPlayerId: number | null): boolean {
  if (turnControl.mode === 'free') return true
  if (!turnControl.activePlayerId) return true
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
  return playerHasTurn(turnControl, selectedPlayerId)
}

export function turnControlStatusLabel(turnControl: TurnControl) {
  if (turnControl.mode === 'free') return 'Free play'
  const activeName = turnControl.activePlayerName ?? 'No active player'
  return turnControl.mode === 'spotlight' ? `Spotlight: ${activeName}` : `Structured: ${activeName}`
}

export function turnControlBlockMessage(turnControl: TurnControl) {
  const activeName = turnControl.activePlayerName ?? 'Another player'
  const modeLabel = turnControl.mode === 'spotlight' ? 'spotlight' : 'turn'
  return `${activeName} has the ${modeLabel}. Your action is queued until your turn opens.`
}
