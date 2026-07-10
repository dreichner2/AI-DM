import { type SceneMusicTag, isSceneMusicTag } from './musicLibrary'

export type SceneDisplayState = {
  sessionId: number
  locationId: string | null
  locationName: string
  sceneType: string
  mood: string | null
  dangerLevel: number
  combatState: string
  inCombat: boolean
  musicTag: SceneMusicTag
  actingPlayerId: number | null
}

export type SceneStatePayload = {
  session_id?: number
  sessionId?: number
  location_id?: string | null
  locationId?: string | null
  location_name?: string
  locationName?: string
  scene_type?: string
  sceneType?: string
  mood?: string | null
  danger_level?: number
  dangerLevel?: number
  combat_state?: string
  combatState?: string
  in_combat?: boolean
  inCombat?: boolean
  music_tag?: string
  musicTag?: string
  acting_player_id?: number | null
  actingPlayerId?: number | null
}

function textValue(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function optionalTextValue(value: unknown) {
  const text = textValue(value)
  return text || null
}

function numberValue(value: unknown, fallback: number) {
  const numericValue = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(numericValue) ? numericValue : fallback
}

function playerIdValue(value: unknown) {
  const numericValue = Number(value)
  return Number.isInteger(numericValue) && numericValue > 0 ? numericValue : null
}

export function normalizeSceneState(payload: SceneStatePayload): SceneDisplayState | null {
  const sessionId = Number(payload.session_id ?? payload.sessionId)
  if (!Number.isInteger(sessionId) || sessionId <= 0) return null

  const musicTag = textValue(payload.music_tag ?? payload.musicTag)
  return {
    sessionId,
    locationId: optionalTextValue(payload.location_id ?? payload.locationId),
    locationName: textValue(payload.location_name ?? payload.locationName) || 'Unknown location',
    sceneType: textValue(payload.scene_type ?? payload.sceneType) || 'scene',
    mood: optionalTextValue(payload.mood),
    dangerLevel: Math.max(0, Math.min(10, numberValue(payload.danger_level ?? payload.dangerLevel, 0))),
    combatState: textValue(payload.combat_state ?? payload.combatState) || 'none',
    inCombat: Boolean(payload.in_combat ?? payload.inCombat),
    musicTag: isSceneMusicTag(musicTag) ? musicTag : 'calm',
    actingPlayerId: playerIdValue(payload.acting_player_id ?? payload.actingPlayerId),
  }
}
