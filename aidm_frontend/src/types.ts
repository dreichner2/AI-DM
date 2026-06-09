import type { JsonRecord } from './apiContract.generated'

export type {
  Account,
  AccountSession,
  AccountWorkspace,
  Campaign,
  CampaignSegment,
  CampaignWorkspace,
  CharacterRaceSelection,
  CustomRaceGenerateResponse,
  CustomRaceSaveResponse,
  JsonRecord,
  MapItem,
  Player,
  PlayerDetail,
  RaceBalanceMetadata,
  RaceDefinition,
  RaceListResponse,
  RaceSource,
  RaceSummary,
  RaceVisualMetadata,
  SessionImportResponse,
  SessionEventsResponse,
  SessionLogEntry,
  SessionLogResponse,
  SessionState,
  SessionSummary,
  TurnEventPayload,
  World,
} from './apiContract.generated'

export type Health = {
  status: string
  service: string
  env: string
  auth_required: boolean
  rules_engine_enabled: boolean
  segment_evaluator_enabled: boolean
  llm?: {
    provider: string
    model: string
    fallback_models: string[]
    configured?: boolean
    latest_turn: {
      turn_id: number
      session_id: number
      provider: string
      model: string
      latency_ms: number | null
      completed_at: string | null
    } | null
  }
}

export type LlmModelOption = {
  id: string
  label: string
}

export type LlmProviderOption = {
  id: string
  label: string
  default_model: string
  configured: boolean
  base_url?: string
  capabilities?: JsonRecord
  models: LlmModelOption[]
}

export type LlmRuntimeConfig = {
  current: NonNullable<Health['llm']>
  providers: LlmProviderOption[]
  persisted: boolean
  runtime_scope?: 'process'
  restart_required_for_other_workers?: boolean
}

export type TtsRuntimeConfig = {
  provider: 'deepgram'
  configured: boolean
  model: string
}

export type CampaignCanon = {
  campaign_id: number
  entities: JsonRecord[]
  facts: JsonRecord[]
  threads: JsonRecord[]
  updates: JsonRecord[]
  limit?: number
  has_more?: {
    entities?: boolean
    facts?: boolean
    threads?: boolean
    updates?: boolean
  }
  next_cursor?: {
    entities?: number | null
    facts?: number | null
    threads?: number | null
    updates?: number | null
  }
  summary: JsonRecord
}

export type BetaSummary = {
  turn_latency_ms_avg: number | null
  ai_failure_rate: number
  session_completion_rate: number
  coherence_feedback_avg: number | null
  coherence_feedback_count: number
  total_turns: number
  total_sessions: number
}

export type ActivePlayer = {
  id: number
  character_name: string
  name: string
  race?: string | null
  sex?: string | null
  profile_image?: string | null
  class_?: string | null
  char_class?: string | null
  is_typing?: boolean
}

export type TurnControlMode = 'free' | 'spotlight' | 'structured'

export type TurnControl = {
  mode: TurnControlMode
  activePlayerId: number | null
  activePlayerName: string | null
  updatedByPlayerId?: number | null
  updatedAt?: string | null
}

export type RulesHint = {
  roll_type?: string | null
  dc_hint?: string | null
  reason?: string | null
  confidence?: number | null
  roll_value?: number | null
  outcome_deferred?: boolean | null
}

export type SocketErrorPayload = {
  error?: string
  message?: string
  error_code?: string
  details?: JsonRecord
}

export type TimelineRole = 'dm' | 'player' | 'system'

export type TimelineEntry = {
  id: string
  role: TimelineRole
  speaker: string
  text: string
  timestamp: string | null
  metadata: JsonRecord
  streaming?: boolean
}

export type StreamingTurn = {
  turnId: number
  turnNumber?: number | null
  text: string
  requiresRoll: boolean
  rulesHint: RulesHint
}

export type ClarificationRequest = {
  id: string
  turnId: number
  sessionId: number
  playerId: number
  type: 'item_resolution'
  prompt: string
  originalPlayerMessage: string
  originalAction: JsonRecord
  options: Array<{
    itemId: string
    label: string
    description?: string
  }>
}
