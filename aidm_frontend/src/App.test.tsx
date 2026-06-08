/// <reference types="node" />
// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import type {
  BetaSummary,
  Campaign,
  CampaignSegment,
  CampaignWorkspace,
  Health,
  LlmRuntimeConfig,
  MapItem,
  Player,
  PlayerDetail,
  SessionImportResponse,
  SessionLogEntry,
  SessionState,
  SessionSummary,
  TtsRuntimeConfig,
  World,
} from './types'

const socketMock = vi.hoisted(() => {
  const socket = {
    emit: vi.fn(),
    on: vi.fn(),
    disconnect: vi.fn(),
  }
  socket.on.mockImplementation(() => socket)
  return { socket }
})

vi.mock('socket.io-client', () => ({
  io: vi.fn(() => socketMock.socket),
}))

vi.mock('./DiceRollDialog', () => ({
  default: ({
    die,
    result,
    status,
    targetLabel,
    onCancel,
    onComplete,
  }: {
    die: string
    result: number
    status: string
    targetLabel?: string | null
    onCancel: () => void
    onComplete: () => void
  }) => (
    <section role="dialog" aria-label="Dice Roller">
      <strong>{die.toUpperCase()}</strong>
      <span>Result {result}</span>
      <span>Status {status}</span>
      {targetLabel ? <span>{targetLabel}</span> : null}
      <button type="button" onClick={onCancel}>
        Cancel roll
      </button>
      <button type="button" onClick={onComplete}>
        Complete roll
      </button>
    </section>
  ),
}))

const fixedNow = new Date('2026-06-06T12:00:00.000Z')

const health: Health = {
  status: 'ok',
  service: 'aidm',
  env: 'test',
  auth_required: false,
  rules_engine_enabled: true,
  segment_evaluator_enabled: true,
  llm: {
    provider: 'deepseek',
    model: 'deepseek-v4-pro',
    fallback_models: [],
    configured: true,
    latest_turn: null,
  },
}

const metrics: BetaSummary = {
  turn_latency_ms_avg: 1800,
  ai_failure_rate: 0,
  session_completion_rate: 1,
  coherence_feedback_avg: null,
  coherence_feedback_count: 0,
  total_turns: 2,
  total_sessions: 1,
}

const runtime: LlmRuntimeConfig = {
  current: health.llm!,
  persisted: true,
  providers: [
    {
      id: 'deepseek',
      label: 'DeepSeek',
      default_model: 'deepseek-v4-pro',
      configured: true,
      models: [{ id: 'deepseek-v4-pro', label: 'DeepSeek V4 Pro' }],
    },
  ],
}

const ttsConfig: TtsRuntimeConfig = {
  provider: 'deepgram',
  configured: true,
  model: 'aura-2-draco-en',
}

let campaigns: Campaign[]
let worlds: World[]
let sessionsByCampaign: Record<number, SessionSummary[]>
let playersByCampaign: Record<number, Player[]>
let mapsByCampaign: Record<number, MapItem[]>
let segmentsByCampaign: Record<number, CampaignSegment[]>
let sessionLogs: Record<number, SessionLogEntry[]>
let sessionStates: Record<number, SessionState>
let playerDetails: Record<number, PlayerDetail>
let fetchCalls: Array<{ method: string; path: string; origin: string; body: unknown }>
let ttsFetchHandler: ((path: string, body: unknown) => Promise<Response>) | null
let requiredAuthToken: string | null

const previousLongDmText =
  'The sealed door vibrates as old glyphs wake one by one across the frame, each symbol answering Ember with a thin blue pulse. The first hinge groans, the second hinge clicks, and the stone remembers the handprint of a forgotten keeper. Hidden tail for expansion verification.'

const latestLongDmText =
  'The chamber beyond is much larger than the hallway promised. Brass walkways cross a black-water reservoir, lanterns bloom in glass cages, and a silent mechanism turns somewhere under the floor with the patience of a clock that has never stopped. Full narrator ending remains visible.'

const lightThemeContrastForegrounds = ['--heading', '--text', '--muted']
const lightThemeContrastBackgrounds = ['--bg', '--surface', '--surface-2', '--panel', '--paper', '--field', '--button']

function createStorageMock(): Storage {
  const store = new Map<string, string>()
  return {
    get length() {
      return store.size
    },
    clear: vi.fn(() => store.clear()),
    getItem: vi.fn((key: string) => store.get(key) ?? null),
    key: vi.fn((index: number) => [...store.keys()][index] ?? null),
    removeItem: vi.fn((key: string) => {
      store.delete(key)
    }),
    setItem: vi.fn((key: string, value: string) => {
      store.set(key, value)
    }),
  }
}

function installStorageMocks() {
  vi.stubGlobal('localStorage', createStorageMock())
  vi.stubGlobal('sessionStorage', createStorageMock())
}

function resetApiData() {
  const campaign: Campaign = {
    campaign_id: 10,
    title: 'Smoke Campaign',
    description: 'A regression campaign.',
    world_id: 5,
    world_name: 'Smoke World',
    created_at: '2026-06-06T10:00:00.000Z',
    updated_at: '2026-06-06T10:30:00.000Z',
    status: 'active',
    is_archived: false,
    current_quest: null,
    location: null,
    session_count: 1,
    latest_session_id: 20,
    latest_activity_at: '2026-06-06T10:45:00.000Z',
  }
  const session: SessionSummary = {
    session_id: 20,
    campaign_id: 10,
    created_at: '2026-06-06T10:35:00.000Z',
    updated_at: '2026-06-06T10:40:00.000Z',
    latest_activity_at: '2026-06-06T10:45:00.000Z',
    display_name: 'Session Alpha',
    status: 'active',
    deleted_at: null,
    turn_count: 2,
    latest_summary: 'The party is testing a sealed door.',
    is_archived: false,
    state_snapshot: {},
  }
  const player: Player = {
    player_id: 30,
    workspace_id: 'owner',
    campaign_id: 10,
    name: 'Danny',
    character_name: 'Ember',
    race: 'Human',
    sex: 'female',
    profile_image: '/profile-icons/human_female.png',
    class_: 'Wizard',
    char_class: 'Wizard',
    level: 2,
    created_at: '2026-06-06T10:36:00.000Z',
    updated_at: '2026-06-06T10:37:00.000Z',
  }

  campaigns = [campaign]
  worlds = [
    {
      world_id: 5,
      name: 'Smoke World',
      description: 'The regression test world.',
      created_at: '2026-06-06T09:00:00.000Z',
    },
  ]
  sessionsByCampaign = { 10: [session] }
  playersByCampaign = { 10: [player] }
  mapsByCampaign = { 10: [] }
  segmentsByCampaign = { 10: [] }
  sessionLogs = {
    20: [
      {
        id: 1,
        entry_type: 'player',
        message: 'Ember: I test the sealed door.',
        metadata: { turn_id: 1, persistence_status: 'saved' },
        timestamp: '2026-06-06T10:40:00.000Z',
      },
      {
        id: 2,
        entry_type: 'dm',
        message: `DM: ${previousLongDmText}`,
        metadata: { turn_id: 1, persistence_status: 'saved' },
        timestamp: '2026-06-06T10:41:00.000Z',
      },
      {
        id: 3,
        entry_type: 'dm',
        message: `DM: ${latestLongDmText}`,
        metadata: { turn_id: 2, persistence_status: 'saved' },
        timestamp: '2026-06-06T10:42:00.000Z',
      },
    ],
  }
  sessionStates = {
    20: {
      session_id: 20,
      campaign_id: 10,
      current_location: 'Ash Hall',
      current_quest: 'Open the sealed door',
      rolling_summary: 'The party is testing a sealed door.',
      active_segments: [],
      memory_snippets: [
        { turn_id: 1, dm_output: 'The first canon fact glows in the margin.' },
        { turn_id: 2, dm_output: 'The second canon fact names the keeper.' },
        { turn_id: 3, dm_output: 'The third canon fact marks the hidden bridge.' },
        { turn_id: 4, dm_output: 'The fourth canon fact reveals the lantern city.' },
      ],
      updated_at: '2026-06-06T10:45:00.000Z',
    },
  }
  playerDetails = {
    30: {
      ...player,
      stats: { strength: 16, dexterity: 12, constitution: 14, intelligence: 18, wisdom: 10, charisma: 8 },
      inventory: [{ name: 'Healing Potion', quantity: 2, weight: 0.5 }],
      character_sheet: { hp: 14, max_hp: 16, ac: 13, speed: 30 },
    },
  }
  fetchCalls = []
  ttsFetchHandler = null
  requiredAuthToken = null
}

function jsonResponse(payload: unknown, init: ResponseInit = {}) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init.headers,
    },
  })
}

function readCssWithImports(filePath: string, seen = new Set<string>()): string {
  const resolvedPath = resolve(filePath)
  if (seen.has(resolvedPath)) return ''
  seen.add(resolvedPath)
  const css = readFileSync(resolvedPath, 'utf8')
  return css.replace(/@import\s+['"](?<path>[^'"]+)['"]\s*;/g, (_match, importPath: string) =>
    readCssWithImports(resolve(dirname(resolvedPath), importPath), seen),
  )
}

function lightThemeColors() {
  const css = readCssWithImports(`${process.cwd()}/src/App.css`)
  const themeBlock = css.match(/\.prototype-shell\.theme-light\s*{(?<body>[\s\S]*?)}/)?.groups?.body
  if (!themeBlock) throw new Error('Missing light theme CSS block')
  return Object.fromEntries(
    [...themeBlock.matchAll(/(?<name>--[\w-]+):\s*(?<value>#[0-9a-fA-F]{6})\s*;/g)].map((match) => [
      match.groups?.name ?? '',
      match.groups?.value ?? '',
    ]),
  )
}

function relativeLuminance(hexColor: string) {
  const channels = [1, 3, 5].map((start) => parseInt(hexColor.slice(start, start + 2), 16) / 255)
  const [red, green, blue] = channels.map((channel) =>
    channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4,
  )
  return 0.2126 * red + 0.7152 * green + 0.0722 * blue
}

function contrastRatio(foreground: string, background: string) {
  const foregroundLuminance = relativeLuminance(foreground)
  const backgroundLuminance = relativeLuminance(background)
  const lighter = Math.max(foregroundLuminance, backgroundLuminance)
  const darker = Math.min(foregroundLuminance, backgroundLuminance)
  return (lighter + 0.05) / (darker + 0.05)
}

function workspacePayload(campaignId: number): CampaignWorkspace {
  const campaign = campaigns.find((item) => item.campaign_id === campaignId)
  if (!campaign) throw new Error(`Unknown campaign ${campaignId}`)
  const sessions = sessionsByCampaign[campaignId] ?? []
  const players = playersByCampaign[campaignId] ?? []
  return {
    campaign: {
      ...campaign,
      session_count: sessions.length,
      latest_session_id: sessions[0]?.session_id ?? null,
      latest_activity_at: sessions[0]?.latest_activity_at ?? campaign.updated_at ?? campaign.created_at,
    },
    sessions,
    players,
    maps: mapsByCampaign[campaignId] ?? [],
    segments: segmentsByCampaign[campaignId] ?? [],
    summary: {
      session_count: sessions.length,
      player_count: players.length,
      map_count: mapsByCampaign[campaignId]?.length ?? 0,
      segment_count: segmentsByCampaign[campaignId]?.length ?? 0,
      latest_session_id: sessions[0]?.session_id ?? null,
      latest_activity_at: sessions[0]?.latest_activity_at ?? campaign.updated_at ?? campaign.created_at,
    },
    has_more: { sessions: false, players: false, maps: false, segments: false },
    next_cursor: { sessions: null, players: null, maps: null, segments: null },
    limits: { sessions: null, players: null, maps: null, segments: null },
  }
}

function installFetchMock() {
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), 'http://localhost:3000')
      const path = url.pathname
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(String(init.body)) : null
      fetchCalls.push({ method, path, origin: url.origin, body })

      if (method === 'GET' && path === '/api/health') return jsonResponse(health)
      const authorization = new Headers(init?.headers).get('Authorization')
      if (
        requiredAuthToken &&
        path.startsWith('/api/') &&
        authorization !== `Bearer ${requiredAuthToken}`
      ) {
        return jsonResponse(
          {
            details: {},
            error: 'Missing or invalid bearer token.',
            error_code: 'unauthorized',
          },
          { status: 401 },
        )
      }
      if (method === 'GET' && path === '/api/campaigns') return jsonResponse(campaigns)
      if (method === 'GET' && path === '/api/worlds') return jsonResponse(worlds)
      if (method === 'GET' && path === '/api/beta/summary') return jsonResponse(metrics)
      if (method === 'GET' && path === '/api/llm/config') return jsonResponse(runtime)
      if (method === 'GET' && path === '/api/tts/config') return jsonResponse(ttsConfig)
      if (method === 'POST' && (path === '/api/tts/stream' || path === '/api/tts/speak')) {
        if (ttsFetchHandler) return ttsFetchHandler(path, body)
        return new Response(new Blob(['audio'], { type: 'audio/mpeg' }), {
          status: 200,
          headers: { 'Content-Type': 'audio/mpeg' },
        })
      }

      const workspaceMatch = path.match(/^\/api\/campaigns\/(\d+)\/workspace$/)
      if (method === 'GET' && workspaceMatch) {
        const campaignId = Number(workspaceMatch[1])
        if (!campaigns.some((campaign) => campaign.campaign_id === campaignId)) {
          return jsonResponse({ error: 'Campaign not found.', error_code: 'campaign_not_found' }, { status: 404 })
        }
        return jsonResponse(workspacePayload(campaignId))
      }

      const logMatch = path.match(/^\/api\/sessions\/(\d+)\/log$/)
      if (method === 'GET' && logMatch) {
        const sessionId = Number(logMatch[1])
        return jsonResponse({
          session_id: sessionId,
          entries: sessionLogs[sessionId] ?? [],
          has_more: false,
          next_cursor: null,
        })
      }

      const stateMatch = path.match(/^\/api\/sessions\/(\d+)\/state$/)
      if (method === 'GET' && stateMatch) {
        const sessionId = Number(stateMatch[1])
        const session =
          Object.values(sessionsByCampaign)
            .flat()
            .find((item) => item.session_id === sessionId) ?? null
        return jsonResponse(
          sessionStates[sessionId] ?? {
            session_id: sessionId,
            campaign_id: session?.campaign_id ?? 10,
            current_location: null,
            current_quest: null,
            rolling_summary: '',
            active_segments: [],
            memory_snippets: [],
            updated_at: fixedNow.toISOString(),
          },
        )
      }

      const playerMatch = path.match(/^\/api\/players\/(\d+)$/)
      if (method === 'GET' && playerMatch) {
        const player = playerDetails[Number(playerMatch[1])]
        if (!player) {
          return jsonResponse({ error: 'Player not found.', error_code: 'player_not_found' }, { status: 404 })
        }
        return jsonResponse(player)
      }
      if (method === 'PATCH' && playerMatch) {
        const playerId = Number(playerMatch[1])
        const current = playerDetails[playerId]
        const updated: PlayerDetail = {
          ...current,
          name: body.name ?? current.name,
          character_name: body.character_name ?? current.character_name,
          race: body.race ?? current.race,
          sex: body.sex ?? current.sex,
          profile_image: body.profile_image ?? current.profile_image,
          class_: body.char_class ?? body.class_ ?? current.class_,
          char_class: body.char_class ?? current.char_class,
          level: body.level ?? current.level,
          updated_at: fixedNow.toISOString(),
        }
        playerDetails[playerId] = updated
        const campaignId = updated.campaign_id ?? current.campaign_id ?? 10
        playersByCampaign[campaignId] = (playersByCampaign[campaignId] ?? []).map((player) =>
          player.player_id === playerId ? updated : player,
        )
        return jsonResponse(updated)
      }

      const campaignPlayersMatch = path.match(/^\/api\/players\/campaigns\/(\d+)\/players$/)
      if (method === 'POST' && campaignPlayersMatch) {
        const campaignId = Number(campaignPlayersMatch[1])
        const playerId = 100 + (playersByCampaign[campaignId]?.length ?? 0)
        const player: PlayerDetail = {
          player_id: playerId,
          workspace_id: 'owner',
          campaign_id: campaignId,
          name: body.name,
          character_name: body.character_name,
          race: body.race ?? '',
          sex: body.sex ?? '',
          profile_image: body.profile_image ?? '/profile-icons/human_male.png',
          class_: body.char_class ?? '',
          char_class: body.char_class ?? '',
          level: body.level ?? 1,
          created_at: fixedNow.toISOString(),
          updated_at: fixedNow.toISOString(),
          stats: {},
          inventory: [],
          character_sheet: {},
        }
        playerDetails[playerId] = player
        playersByCampaign[campaignId] = [...(playersByCampaign[campaignId] ?? []), player]
        return jsonResponse({ player_id: playerId }, { status: 201 })
      }

      if (method === 'POST' && path === '/api/worlds') {
        const world: World = {
          world_id: 99,
          name: body.name,
          description: body.description,
          created_at: fixedNow.toISOString(),
        }
        worlds = [...worlds, world]
        return jsonResponse(world)
      }

      const worldMatch = path.match(/^\/api\/worlds\/(\d+)$/)
      if (method === 'PATCH' && worldMatch) {
        const worldId = Number(worldMatch[1])
        let updated: World | null = null
        worlds = worlds.map((world) => {
          if (world.world_id !== worldId) return world
          updated = {
            ...world,
            name: body.name ?? world.name,
            description: body.description ?? world.description,
          }
          return updated
        })
        campaigns = campaigns.map((campaign) =>
          campaign.world_id === worldId
            ? { ...campaign, world_name: updated?.name ?? campaign.world_name }
            : campaign,
        )
        return updated
          ? jsonResponse(updated)
          : jsonResponse({ error: 'World not found.', error_code: 'world_not_found' }, { status: 404 })
      }
      if (method === 'DELETE' && worldMatch) {
        const worldId = Number(worldMatch[1])
        const inUse = campaigns.some((campaign) => campaign.world_id === worldId)
        if (inUse) {
          return jsonResponse(
            {
              error: 'World is still in use.',
              error_code: 'world_in_use',
            },
            { status: 409 },
          )
        }
        worlds = worlds.filter((world) => world.world_id !== worldId)
        return jsonResponse({ deleted: true, world_id: worldId })
      }

      if (method === 'POST' && path === '/api/campaigns') {
        const selectedWorld = worlds.find((world) => world.world_id === body.world_id)
        const campaign: Campaign = {
          campaign_id: 99,
          title: body.title,
          description: body.description,
          world_id: body.world_id,
          world_name: selectedWorld?.name ?? null,
          created_at: fixedNow.toISOString(),
          updated_at: fixedNow.toISOString(),
          status: 'active',
          is_archived: false,
          current_quest: null,
          location: null,
          session_count: 0,
          latest_session_id: null,
          latest_activity_at: fixedNow.toISOString(),
        }
        campaigns = [...campaigns, campaign]
        sessionsByCampaign[99] = []
        playersByCampaign[99] = []
        mapsByCampaign[99] = []
        segmentsByCampaign[99] = []
        return jsonResponse({ campaign_id: 99 })
      }

      if (method === 'POST' && path === '/api/sessions/start') {
        const sessionId = 21
        const session: SessionSummary = {
          session_id: sessionId,
          campaign_id: body.campaign_id,
          created_at: fixedNow.toISOString(),
          updated_at: fixedNow.toISOString(),
          latest_activity_at: fixedNow.toISOString(),
          display_name: 'Session Beta',
          status: 'active',
          deleted_at: null,
          turn_count: 0,
          latest_summary: '',
          is_archived: false,
          state_snapshot: {},
        }
        sessionsByCampaign[body.campaign_id] = [
          session,
          ...(sessionsByCampaign[body.campaign_id] ?? []),
        ]
        sessionLogs[sessionId] = []
        sessionStates[sessionId] = {
          session_id: sessionId,
          campaign_id: body.campaign_id,
          current_location: 'New camp',
          current_quest: 'Begin the next scene',
          rolling_summary: '',
          active_segments: [],
          memory_snippets: [],
          updated_at: fixedNow.toISOString(),
        }
        return jsonResponse({ session_id: sessionId })
      }

      if (method === 'POST' && path === '/api/sessions/import') {
        const campaignId = Number(
          body.campaign_id ??
            body.campaignId ??
            body.selectedIds?.campaignId ??
            body.selectedIds?.campaign_id ??
            body.campaign?.campaign_id ??
            10,
        )
        const sessionId = 30
        const session: SessionSummary = {
          session_id: sessionId,
          campaign_id: campaignId,
          created_at: fixedNow.toISOString(),
          updated_at: fixedNow.toISOString(),
          latest_activity_at: fixedNow.toISOString(),
          display_name: body.selectedSession?.display_name ?? body.name ?? 'Imported Session',
          status: 'active',
          deleted_at: null,
          turn_count: Array.isArray(body.turnEvents) ? body.turnEvents.length : 0,
          latest_summary: body.sessionState?.rolling_summary ?? '',
          is_archived: false,
          state_snapshot: {
            imported: true,
          },
        }
        sessionsByCampaign[campaignId] = [
          session,
          ...(sessionsByCampaign[campaignId] ?? []),
        ]
        sessionLogs[sessionId] = Array.isArray(body.logEntries)
          ? body.logEntries.map((entry: SessionLogEntry, index: number) => ({
              id: 700 + index,
              message: entry.message,
              entry_type: entry.entry_type,
              metadata: entry.metadata ?? {},
              timestamp: entry.timestamp ?? fixedNow.toISOString(),
            }))
          : []
        sessionStates[sessionId] = {
          session_id: sessionId,
          campaign_id: campaignId,
          current_location: body.sessionState?.current_location ?? null,
          current_quest: body.sessionState?.current_quest ?? null,
          rolling_summary: body.sessionState?.rolling_summary ?? '',
          active_segments: body.sessionState?.active_segments ?? [],
          memory_snippets: body.sessionState?.memory_snippets ?? [],
          updated_at: fixedNow.toISOString(),
        }
        const response: SessionImportResponse = {
          imported: true,
          session_id: sessionId,
          session,
          counts: {
            turn_events: Array.isArray(body.turnEvents) ? body.turnEvents.length : 0,
            projected_log_entries: 0,
            log_entries: Array.isArray(body.logEntries) ? body.logEntries.length : 0,
            session_state: body.sessionState ? 1 : 0,
          },
        }
        return jsonResponse(response, { status: 201 })
      }

      const sessionMatch = path.match(/^\/api\/sessions\/(\d+)$/)
      if (method === 'PATCH' && sessionMatch) {
        const sessionId = Number(sessionMatch[1])
        const updated = { ...sessionsByCampaign[10][0], display_name: body.name, updated_at: fixedNow.toISOString() }
        sessionsByCampaign[10] = sessionsByCampaign[10].map((session) =>
          session.session_id === sessionId ? updated : session,
        )
        return jsonResponse(updated)
      }
      if (method === 'DELETE' && sessionMatch) {
        const sessionId = Number(sessionMatch[1])
        sessionsByCampaign[10] = sessionsByCampaign[10].filter((session) => session.session_id !== sessionId)
        return jsonResponse({ deleted: true })
      }

      if (method === 'POST' && path === '/api/maps') {
        const map: MapItem = {
          map_id: 40,
          world_id: body.world_id,
          campaign_id: body.campaign_id,
          title: body.title,
          description: body.description,
          map_data: body.map_data ?? {},
          created_at: fixedNow.toISOString(),
          updated_at: fixedNow.toISOString(),
        }
        mapsByCampaign[body.campaign_id] = [map]
        return jsonResponse({ map_id: map.map_id }, { status: 201 })
      }

      const mapMatch = path.match(/^\/api\/maps\/(\d+)$/)
      if (method === 'PATCH' && mapMatch) {
        const mapId = Number(mapMatch[1])
        mapsByCampaign[10] = (mapsByCampaign[10] ?? []).map((map) =>
          map.map_id === mapId
            ? {
                ...map,
                title: body.title ?? map.title,
                description: body.description ?? map.description,
                updated_at: fixedNow.toISOString(),
              }
            : map,
        )
        return jsonResponse({ message: 'Map updated successfully' })
      }

      if (method === 'POST' && path === '/api/segments') {
        const segment: CampaignSegment = {
          segment_id: 50 + (segmentsByCampaign[body.campaign_id]?.length ?? 0),
          campaign_id: body.campaign_id,
          title: body.title,
          description: body.description,
          trigger_condition: body.trigger_condition,
          tags: body.tags,
          is_triggered: Boolean(body.is_triggered),
          created_at: fixedNow.toISOString(),
          updated_at: fixedNow.toISOString(),
        }
        segmentsByCampaign[body.campaign_id] = [
          segment,
          ...(segmentsByCampaign[body.campaign_id] ?? []),
        ]
        return jsonResponse({ segment_id: segment.segment_id }, { status: 201 })
      }

      const segmentMatch = path.match(/^\/api\/segments\/(\d+)$/)
      if (method === 'PATCH' && segmentMatch) {
        const segmentId = Number(segmentMatch[1])
        segmentsByCampaign[10] = (segmentsByCampaign[10] ?? []).map((segment) =>
          segment.segment_id === segmentId
            ? {
                ...segment,
                title: body.title ?? segment.title,
                description: body.description ?? segment.description,
                trigger_condition: body.trigger_condition ?? segment.trigger_condition,
                tags: body.tags ?? segment.tags,
                is_triggered: body.is_triggered ?? segment.is_triggered,
                updated_at: fixedNow.toISOString(),
              }
            : segment,
        )
        return jsonResponse({ message: 'Segment updated successfully' })
      }
      if (method === 'DELETE' && segmentMatch) {
        const segmentId = Number(segmentMatch[1])
        segmentsByCampaign[10] = (segmentsByCampaign[10] ?? []).filter(
          (segment) => segment.segment_id !== segmentId,
        )
        return jsonResponse({ message: 'Segment deleted' })
      }

      return jsonResponse({ error: `Unhandled ${method} ${path}` }, { status: 404 })
    }),
  )
}

async function renderLoadedApp() {
  const rendered = render(<App />)
  await screen.findByRole('heading', { name: /Session Alpha/i })
  await waitFor(() => expect(screen.getAllByText('Ember').length).toBeGreaterThan(0))
  return rendered
}

function toggleAdminToolsViaComposerLabel() {
  const actionLabel = screen.getByText(/Your Action/)
  for (let index = 0; index < 5; index += 1) {
    fireEvent.click(actionLabel)
  }
}

function socketHandler<TPayload>(eventName: string) {
  const call = socketMock.socket.on.mock.calls.find(([event]) => event === eventName)
  if (!call) throw new Error(`Missing socket handler for ${eventName}`)
  return call[1] as (payload: TPayload) => void
}

describe('App user workflow regressions', () => {
  beforeEach(() => {
    socketMock.socket.emit.mockClear()
    socketMock.socket.on.mockClear()
    socketMock.socket.disconnect.mockClear()
    socketMock.socket.on.mockImplementation(() => socketMock.socket)
    installStorageMocks()
    resetApiData()
    window.history.replaceState(null, '', '/')
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: undefined,
    })
    localStorage.clear()
    sessionStorage.clear()
    localStorage.setItem('aidm:selectedCampaignId', '10')
    localStorage.setItem('aidm:selectedSessionId', '20')
    localStorage.setItem('aidm:selectedPlayerId', '30')
    installFetchMock()
    Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', {
      configurable: true,
      value: vi.fn().mockRejectedValue(new Error('blocked')),
    })
  })

  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
    vi.unstubAllGlobals()
  })

  it('switches composer modes and rewrites the action text without stale prefixes', async () => {
    await renderLoadedApp()

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'test the sigil' } })

    fireEvent.click(screen.getByRole('button', { name: 'OOC' }))
    expect(actionInput).toHaveValue('[OOC] test the sigil')

    fireEvent.click(screen.getByRole('button', { name: 'Roll' }))
    expect(actionInput).toHaveValue('I roll a d20: test the sigil')
    expect(screen.getByLabelText('Roll options')).toBeInTheDocument()
    expect(screen.getByLabelText('Roll ability')).toHaveValue('plain_roll')

    fireEvent.change(screen.getByLabelText('Roll ability'), { target: { value: 'strength' } })
    expect(actionInput).toHaveValue('I roll a d20+3 for STR check: test the sigil')
    expect(screen.getByLabelText('Roll modifier')).toHaveValue(3)
    expect(screen.getByLabelText('Roll reason')).toHaveValue('STR check')

    fireEvent.click(screen.getByRole('button', { name: 'Item' }))
    expect(actionInput).toHaveValue('Ember uses Healing Potion: test the sigil')
    expect(screen.getByLabelText('Item options')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Emote' }))
    expect(actionInput).toHaveValue('/emote test the sigil')

    fireEvent.click(screen.getByRole('button', { name: 'Action mode' }))
    expect(actionInput).toHaveValue('test the sigil')
  })

  it('sends structured item composer metadata for buying arbitrary items', async () => {
    await renderLoadedApp()

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'before leaving town' } })
    fireEvent.click(screen.getByRole('button', { name: 'Item' }))
    fireEvent.change(screen.getByLabelText('Inventory action'), { target: { value: 'buy' } })
    fireEvent.change(screen.getByLabelText('Item name'), { target: { value: 'rope' } })
    fireEvent.change(screen.getByLabelText('Gold cost'), { target: { value: '5' } })

    expect(actionInput).toHaveValue('Ember tries to buy rope for 5 gold: before leaving town')
    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: 'Ember tries to buy rope for 5 gold: before leaving town',
          action_intent: expect.objectContaining({
            kind: 'item',
            inventory_action: 'buy',
            cost_gold: 5,
            item: {
              name: 'rope',
              quantity: 1,
            },
          }),
        }),
      ),
    )
  })

  it('keeps admin mode hidden until the composer label gesture unlocks it', async () => {
    await renderLoadedApp()

    expect(screen.queryByRole('button', { name: 'Admin mode' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Admin' })).not.toBeInTheDocument()

    toggleAdminToolsViaComposerLabel()

    expect(screen.getByRole('button', { name: 'Admin mode' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Admin' })).toBeInTheDocument()

    toggleAdminToolsViaComposerLabel()

    expect(screen.queryByRole('button', { name: 'Admin mode' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Admin' })).not.toBeInTheDocument()
  })

  it('sends player interaction mode with target metadata', async () => {
    playersByCampaign[10] = [
      ...playersByCampaign[10],
      {
        player_id: 31,
        workspace_id: 'owner',
        campaign_id: 10,
        name: 'Maya',
        character_name: 'Borin',
        race: 'Dwarf',
        sex: 'male',
        profile_image: '/profile-icons/dwarf_male.png',
        class_: 'Fighter',
        char_class: 'Fighter',
        level: 2,
        created_at: '2026-06-06T10:38:00.000Z',
        updated_at: '2026-06-06T10:39:00.000Z',
      },
    ]
    await renderLoadedApp()

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'the silver key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Interact' }))

    expect(screen.getByLabelText('Interaction options')).toBeInTheDocument()
    expect(screen.getByLabelText('Interaction target')).toHaveValue('31')
    expect(actionInput).toHaveValue('Ember says to Borin: the silver key')

    fireEvent.change(screen.getByLabelText('Interaction type'), { target: { value: 'take_from' } })
    expect(actionInput).toHaveValue('Ember tries to take something from Borin: the silver key')
    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: 'Ember tries to take something from Borin: the silver key',
          action_intent: expect.objectContaining({
            kind: 'interact',
            interaction: expect.objectContaining({
              type: 'take_from',
              label: 'Take from',
            }),
            target: expect.objectContaining({
              player_id: 31,
              character_name: 'Borin',
              player_name: 'Maya',
            }),
          }),
        }),
      ),
    )
  })

  it('sends admin mode with an admin passcode and typed admin intent', async () => {
    await renderLoadedApp()

    toggleAdminToolsViaComposerLabel()
    fireEvent.click(screen.getByRole('button', { name: 'Admin mode' }))
    expect(screen.getByLabelText('Admin passcode')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Admin passcode'), { target: { value: 'letmein' } })
    fireEvent.change(screen.getByLabelText(/Your Action/i), {
      target: { value: '[ADMIN] make the locked gate open now' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          admin_passcode: 'letmein',
          message: '[ADMIN] make the locked gate open now',
          action_intent: expect.objectContaining({
            kind: 'admin',
            text: '[ADMIN] make the locked gate open now',
          }),
        }),
      ),
    )
  })

  it('opens the dice roller from Roll options and sends the completed roll', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Roll' }))
    expect(screen.getByLabelText('Roll options')).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Dice Roller' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Roll dice' }))

    const dialog = await screen.findByRole('dialog', { name: 'Dice Roller' })
    expect(within(dialog).getByText('D20')).toBeInTheDocument()
    expect((screen.getByLabelText(/Your Action/i) as HTMLTextAreaElement).value).toMatch(/^I roll a d20:/)

    fireEvent.click(within(dialog).getByRole('button', { name: 'Complete roll' }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          session_id: 20,
          campaign_id: 10,
          player_id: 30,
          action_intent: expect.objectContaining({
            kind: 'roll',
            source: 'dice_roller',
            roll: expect.objectContaining({
              die: 'd20',
              result_visibility: 'hidden_until_landed',
            }),
          }),
        }),
      ),
    )
  })

  it('rolls selected ability checks from the Roll selector', async () => {
    await renderLoadedApp()

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'kick the door' } })
    fireEvent.click(screen.getByRole('button', { name: 'Roll' }))
    fireEvent.change(screen.getByLabelText('Roll ability'), { target: { value: 'strength' } })

    expect(actionInput).toHaveValue('I roll a d20+3 for STR check: kick the door')
    expect(screen.getByLabelText('Roll modifier')).toHaveValue(3)

    fireEvent.click(screen.getByRole('button', { name: 'Roll dice' }))
    const dialog = await screen.findByRole('dialog', { name: 'Dice Roller' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Complete roll' }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          action_intent: expect.objectContaining({
            kind: 'roll',
            ability: {
              key: 'strength',
              label: 'STR',
              modifier: 3,
            },
            roll: expect.objectContaining({
              modifier: 3,
              reason: 'STR check',
            }),
          }),
        }),
      ),
    )
  })

  it('shows active players from the session socket roster and clears them on disconnect', async () => {
    await renderLoadedApp()

    await act(async () => {
      socketHandler<Array<{ id: number; character_name: string; name: string }>>('active_players')([
        { id: 30, character_name: 'Ember', name: 'Danny' },
        { id: 31, character_name: 'Borin', name: 'Maya' },
      ])
    })

    const roster = screen.getByLabelText('Active players in this session')
    expect(screen.getByText('Active Players (2)')).toBeInTheDocument()
    expect(within(roster).getByText('Borin')).toBeInTheDocument()
    expect(within(roster).getByText('Maya')).toBeInTheDocument()
    expect(within(roster).getByText('You')).toBeInTheDocument()

    await act(async () => {
      socketHandler<void>('disconnect')()
    })

    expect(screen.getByText('Active Players (0)')).toBeInTheDocument()
    expect(screen.getByText('No active players connected.')).toBeInTheDocument()
  })

  it('shows another player socket message in the turn feed immediately', async () => {
    await renderLoadedApp()

    await act(async () => {
      socketHandler<{
        message: string
        speaker: string
        turn_id: number
        requires_roll: boolean
        rules_hint: Record<string, unknown>
        context_version: string
        client_message_id: string
        action_intent: Record<string, unknown>
      }>('new_message')({
        message: 'Borin passes Ember the silver key.',
        speaker: 'Borin',
        turn_id: 44,
        requires_roll: false,
        rules_hint: { requires_roll: false },
        context_version: 'v2',
        client_message_id: 'borin-live-1',
        action_intent: {
          kind: 'message',
          source: 'composer',
          text: 'Borin passes Ember the silver key.',
          client_message_id: 'borin-live-1',
        },
      })
    })

    expect(screen.getByText('Borin passes Ember the silver key.')).toBeInTheDocument()
    expect(screen.getByText('Borin')).toBeInTheDocument()

    await act(async () => {
      socketHandler<{ message: string; speaker: string; turn_id: number }>('new_message')({
        message: 'Borin passes Ember the silver key.',
        speaker: 'Borin',
        turn_id: 44,
      })
    })

    expect(screen.getAllByText('Borin passes Ember the silver key.')).toHaveLength(1)
  })

  it('copies a share link with the active backend URL and session selection', async () => {
    localStorage.setItem('aidm:baseUrl', 'https://backend-tunnel.ngrok-free.app')
    const writeText = vi.fn((value: string) => Promise.resolve(value))
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    await renderLoadedApp()
    fireEvent.click(screen.getByRole('button', { name: 'Share' }))

    await waitFor(() => expect(writeText).toHaveBeenCalledOnce())
    const shareUrl = new URL(String(writeText.mock.calls[0]?.[0]))
    expect(shareUrl.searchParams.get('campaign')).toBe('10')
    expect(shareUrl.searchParams.get('session')).toBe('20')
    expect(shareUrl.searchParams.get('backend')).toBe('https://backend-tunnel.ngrok-free.app')
    expect(shareUrl.searchParams.has('player')).toBe(false)
  })

  it('copies a same-origin share link without a backend parameter by default', async () => {
    const writeText = vi.fn((value: string) => Promise.resolve(value))
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    await renderLoadedApp()
    fireEvent.click(screen.getByRole('button', { name: 'Share' }))

    await waitFor(() => expect(writeText).toHaveBeenCalledOnce())
    const shareUrl = new URL(String(writeText.mock.calls[0]?.[0]))
    expect(shareUrl.searchParams.get('campaign')).toBe('10')
    expect(shareUrl.searchParams.get('session')).toBe('20')
    expect(shareUrl.searchParams.has('backend')).toBe(false)
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/health',
          origin: 'http://localhost:3000',
        }),
      ]),
    )
  })

  it('prompts for an auth token when the public app requires one', async () => {
    requiredAuthToken = 'shared-token'
    localStorage.setItem('aidm:selectedPlayerId', '30')

    render(<App />)

    const dialog = await screen.findByRole('dialog', { name: 'Auth Token Required' })
    expect(within(dialog).queryByLabelText('Backend URL')).not.toBeInTheDocument()
    expect(within(dialog).getByText('Paste the shared token for this AIDM session.')).toBeInTheDocument()

    const tokenInput = within(dialog).getByLabelText('Auth Token')
    await waitFor(() => expect(tokenInput).toHaveFocus())
    fireEvent.change(tokenInput, { target: { value: 'shared-token' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }))

    await screen.findByRole('heading', { name: /Session Alpha/i })
    expect(sessionStorage.getItem('aidm:authToken')).toBe('shared-token')
    expect(screen.queryByRole('dialog', { name: 'Auth Token Required' })).not.toBeInTheDocument()
    await waitFor(() =>
      expect(screen.queryByText('Auth token required. Paste the shared token to connect.')).not.toBeInTheDocument(),
    )
    expect(screen.queryByText('Player load failed: Missing or invalid bearer token.')).not.toBeInTheDocument()
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/campaigns',
        }),
        expect.objectContaining({
          method: 'GET',
          path: '/api/campaigns/10/workspace',
        }),
        expect.objectContaining({
          method: 'GET',
          path: '/api/players/30',
        }),
      ]),
    )
  })

  it('clears stale owner selections after connecting to an empty auth workspace', async () => {
    requiredAuthToken = 'aidan_test'
    campaigns = []
    worlds = []
    sessionsByCampaign = {}
    playersByCampaign = {}
    mapsByCampaign = {}
    segmentsByCampaign = {}
    sessionLogs = {}
    sessionStates = {}
    playerDetails = {}

    render(<App />)

    const dialog = await screen.findByRole('dialog', { name: 'Auth Token Required' })
    fireEvent.change(within(dialog).getByLabelText('Auth Token'), { target: { value: 'aidan_test' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Connect' }))

    await screen.findByText('No campaigns match.')
    await waitFor(() => {
      expect(screen.queryByText(/Workspace load failed:/)).not.toBeInTheDocument()
      expect(screen.queryByText(/Session refresh failed:/)).not.toBeInTheDocument()
      expect(screen.queryByText(/Player load failed:/)).not.toBeInTheDocument()
    })
    await waitFor(() => {
      const params = new URLSearchParams(window.location.search)
      expect(params.has('campaign')).toBe(false)
      expect(params.has('session')).toBe(false)
    })
  })

  it('exposes character load, create, and edit actions in the inspector', async () => {
    await renderLoadedApp()

    const characterActions = screen.getByLabelText('Character actions')
    fireEvent.click(within(characterActions).getByRole('button', { name: 'Load' }))
    expect(await screen.findByRole('dialog', { name: 'Join Campaign' })).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Close character chooser'))

    fireEvent.click(within(characterActions).getByRole('button', { name: 'Edit' }))
    expect(await screen.findByRole('dialog', { name: 'Edit Character' })).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Close character editor'))

    fireEvent.click(within(characterActions).getByRole('button', { name: 'New' }))
    expect(await screen.findByRole('dialog', { name: 'Create Character' })).toBeInTheDocument()
  })

  it('shows a manual share link when clipboard access is unavailable', async () => {
    localStorage.setItem('aidm:baseUrl', 'https://backend-tunnel.ngrok-free.app')

    await renderLoadedApp()
    fireEvent.click(screen.getByRole('button', { name: 'Share' }))

    const dialog = await screen.findByRole('dialog', { name: 'Share Session' })
    const shareInput = within(dialog).getByLabelText('Session share link')
    const shareValue = (shareInput as HTMLInputElement).value
    expect(shareValue).toContain('backend=https%3A%2F%2Fbackend-tunnel.ngrok-free.app')
    expect(shareValue).toContain('campaign=10')
    expect(shareValue).toContain('session=20')
  })

  it('uses a backend URL from a share link without leaving it in the address bar', async () => {
    window.history.replaceState(
      null,
      '',
      '/?campaign=10&session=20&backend=https%3A%2F%2Fbackend-tunnel.ngrok-free.app',
    )

    await renderLoadedApp()

    expect(localStorage.getItem('aidm:baseUrl')).toBe('https://backend-tunnel.ngrok-free.app')
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/health',
          origin: 'https://backend-tunnel.ngrok-free.app',
        }),
      ]),
    )
    await waitFor(() => {
      expect(window.location.search).toBe('?campaign=10&session=20')
    })
  })

  it('lets first-time campaign visitors join as an existing character', async () => {
    localStorage.removeItem('aidm:selectedPlayerId')

    await renderLoadedApp()

    const dialog = await screen.findByRole('dialog', { name: 'Join Campaign' })
    expect(within(dialog).getByRole('button', { name: 'Join as Ember' })).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Join as Ember' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Join Campaign' })).not.toBeInTheDocument())
    await waitFor(() => expect(socketMock.socket.on).toHaveBeenCalledWith('connect', expect.any(Function)))
    await act(async () => {
      socketHandler<void>('connect')()
    })
    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'join_session',
        expect.objectContaining({
          session_id: 20,
          player_id: 30,
        }),
      ),
    )
  })

  it('refreshes the selected player when inventory state is applied before canon finishes', async () => {
    await renderLoadedApp()

    playerDetails[30] = {
      ...playerDetails[30],
      inventory: [
        { name: 'Healing Potion', quantity: 2, weight: 0.5 },
        { name: 'Stick', quantity: 1 },
      ],
    }

    await act(async () => {
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: { player_id: number; inventory_changes_applied: Array<{ item_name: string; quantity: number }> }
      }>('turn_status')({
        session_id: 20,
        turn_id: 4,
        status: 'state_applied',
        details: {
          player_id: 30,
          inventory_changes_applied: [{ item_name: 'Stick', quantity: 1 }],
        },
      })
    })

    await screen.findByText('Stick')
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/players/30',
        }),
      ]),
    )
  })

  it('refreshes the selected player when immediate inventory state arrives as canon_applied', async () => {
    await renderLoadedApp()

    playerDetails[30] = {
      ...playerDetails[30],
      inventory: [
        { name: 'Healing Potion', quantity: 2, weight: 0.5 },
        { name: 'Rope', quantity: 1 },
      ],
    }

    await act(async () => {
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: {
          player_id: number
          state_applied: boolean
          inventory_changes_applied: Array<{ item_name: string; quantity: number; already_applied: boolean }>
        }
      }>('turn_status')({
        session_id: 20,
        turn_id: 5,
        status: 'canon_applied',
        details: {
          player_id: 30,
          state_applied: true,
          inventory_changes_applied: [{ item_name: 'Rope', quantity: 1, already_applied: true }],
        },
      })
    })

    await screen.findByText('Rope')
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/players/30',
        }),
      ]),
    )
  })

  it('lets first-time campaign visitors create a character before joining as a player', async () => {
    localStorage.removeItem('aidm:selectedPlayerId')

    await renderLoadedApp()

    const chooser = await screen.findByRole('dialog', { name: 'Join Campaign' })
    fireEvent.click(within(chooser).getByRole('button', { name: 'Create Character' }))

    const creator = await screen.findByRole('dialog', { name: 'Create Character' })
    fireEvent.change(within(creator).getByLabelText('Player Name'), {
      target: { value: 'Maya' },
    })
    fireEvent.change(within(creator).getByLabelText('Character Name'), {
      target: { value: 'Borin' },
    })
    fireEvent.change(within(creator).getByLabelText('Race'), {
      target: { value: 'Dwarf' },
    })
    fireEvent.change(within(creator).getByLabelText('Sex'), {
      target: { value: 'male' },
    })
    fireEvent.change(within(creator).getByLabelText('Class'), {
      target: { value: 'Cleric' },
    })
    fireEvent.click(within(creator).getByRole('button', { name: 'Create Character' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Create Character' })).not.toBeInTheDocument())
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'POST',
          path: '/api/players/campaigns/10/players',
          body: expect.objectContaining({
            name: 'Maya',
            character_name: 'Borin',
            race: 'Dwarf',
            sex: 'male',
            char_class: 'Cleric',
          }),
        }),
      ]),
    )
    expect(await screen.findByText('Borin')).toBeInTheDocument()
  })

  it('keeps focus in the edited character field instead of snapping back to player name', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Account' }))
    fireEvent.click(within(screen.getByRole('menu', { name: 'Account options' })).getByRole('menuitem', {
      name: 'Profile settings',
    }))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit character' }))

    const dialog = await screen.findByRole('dialog', { name: 'Edit Character' })
    const raceInput = within(dialog).getByLabelText('Race')
    raceInput.focus()
    fireEvent.change(raceInput, { target: { value: 'Elf' } })

    expect(document.activeElement).toBe(raceInput)
  })

  it('opens create campaign and submits through world plus campaign endpoints', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Add campaign' }))
    const dialog = await screen.findByRole('dialog', { name: 'Create New Campaign' })
    fireEvent.change(within(dialog).getByLabelText('Campaign Name'), {
      target: { value: 'Crystal Road' },
    })
    fireEvent.change(within(dialog).getByLabelText('Description'), {
      target: { value: 'Find the lantern city.' },
    })
    fireEvent.change(within(dialog).getByLabelText('New World Name'), {
      target: { value: 'Crystal Reach' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create Campaign' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Create New Campaign' })).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getAllByText('Crystal Road').length).toBeGreaterThan(0))
    expect(await screen.findByRole('dialog', { name: 'Join Campaign' })).toBeInTheDocument()
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'POST', path: '/api/worlds' }),
        expect.objectContaining({ method: 'POST', path: '/api/campaigns' }),
      ]),
    )
  })

  it('can create a campaign from an existing world without creating a duplicate world', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Add campaign' }))
    const dialog = await screen.findByRole('dialog', { name: 'Create New Campaign' })
    fireEvent.change(within(dialog).getByLabelText('Campaign Name'), {
      target: { value: 'Lantern Annex' },
    })
    fireEvent.change(within(dialog).getByLabelText('Description'), {
      target: { value: 'A side story in the smoke world.' },
    })
    fireEvent.change(within(dialog).getByLabelText('World'), {
      target: { value: '5' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create Campaign' }))

    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Create New Campaign' })).not.toBeInTheDocument(),
    )
    const worldCreates = fetchCalls.filter(
      (call) => call.method === 'POST' && call.path === '/api/worlds',
    )
    const campaignCreate = fetchCalls.find(
      (call) => call.method === 'POST' && call.path === '/api/campaigns',
    )
    expect(worldCreates).toHaveLength(0)
    expect(campaignCreate?.body).toEqual(
      expect.objectContaining({ title: 'Lantern Annex', world_id: 5 }),
    )
  })

  it('opens the session menu and supports rename and delete actions', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Session menu' }))
    const sessionMenu = await screen.findByRole('menu', { name: 'Session menu' })
    fireEvent.click(within(sessionMenu).getByRole('menuitem', { name: 'Rename session' }))
    const renameDialog = await screen.findByRole('dialog', { name: 'Rename Session' })
    fireEvent.change(within(renameDialog).getByLabelText('Session Name'), {
      target: { value: 'Session Beta' },
    })
    fireEvent.click(within(renameDialog).getByRole('button', { name: 'Rename Session' }))

    await screen.findByRole('heading', { name: /Session Beta/i })
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'PATCH', path: '/api/sessions/20' }),
      ]),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Session menu' }))
    const reopenedSessionMenu = await screen.findByRole('menu', { name: 'Session menu' })
    fireEvent.click(within(reopenedSessionMenu).getByRole('menuitem', { name: 'Delete session' }))
    const deleteDialog = await screen.findByRole('dialog', { name: 'Delete Session' })
    fireEvent.click(within(deleteDialog).getByRole('button', { name: 'Delete Session' }))

    await screen.findByText('No sessions yet.')
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'DELETE', path: '/api/sessions/20' }),
      ]),
    )
  })

  it('imports an exported session JSON file and selects the restored session', async () => {
    await renderLoadedApp()

    const importPayload = {
      exportedAt: fixedNow.toISOString(),
      selectedIds: {
        campaignId: 10,
        sessionId: 20,
        playerId: 10,
      },
      selectedSession: {
        session_id: 20,
        display_name: 'Restored Trial',
        state_snapshot: {},
      },
      sessionState: {
        current_location: 'Restored Hall',
        current_quest: 'Check import flow',
        rolling_summary: 'Imported summary appears after restore.',
        active_segments: [],
        memory_snippets: [],
      },
      logEntries: [
        {
          id: 1,
          message: 'Imported log entry',
          entry_type: 'dm',
          metadata: {},
          timestamp: fixedNow.toISOString(),
        },
      ],
      turnEvents: [],
    }
    const file = new File([JSON.stringify(importPayload)], 'aidm-session-20.json', {
      type: 'application/json',
    })

    fireEvent.click(screen.getByRole('button', { name: 'Import' }))
    fireEvent.change(screen.getByLabelText('Import session file'), {
      target: { files: [file] },
    })

    expect(await screen.findByRole('heading', { name: /Restored Trial/i })).toBeInTheDocument()
    expect(await screen.findByText('Imported log entry')).toBeInTheDocument()
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'POST',
          path: '/api/sessions/import',
          body: expect.objectContaining({
            selectedSession: expect.objectContaining({ display_name: 'Restored Trial' }),
          }),
        }),
      ]),
    )
  })

  it('keeps long DM responses visible in the current response and full response views', async () => {
    await renderLoadedApp()

    expect(screen.getAllByText(/Full narrator ending remains visible/i).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('tab', { name: 'DM Response' }))
    expect(screen.getAllByText(/Full narrator ending remains visible/i).length).toBeGreaterThan(0)
  })

  it('expands prior turns so long historical responses can be read', async () => {
    await renderLoadedApp()

    expect(screen.queryByText(/Hidden tail for expansion verification/i)).not.toBeInTheDocument()
    const expandButtons = screen.getAllByRole('button', { name: 'Expand turn' })
    fireEvent.click(expandButtons[1])

    expect(screen.getByText(/Hidden tail for expansion verification/i)).toBeInTheDocument()
    expect(expandButtons[1]).toHaveAttribute('aria-expanded', 'true')
  })

  it('updates the campaign session count after starting a new session', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))

    expect(await screen.findByRole('heading', { name: /Session Beta/i })).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText(/2 Sessions/i).length).toBeGreaterThan(0))
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'POST', path: '/api/sessions/start' }),
      ]),
    )
  })

  it('opens all canon facts from the View All Canon control', async () => {
    await renderLoadedApp()

    expect(screen.queryByText(/first canon fact/i)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /View All Canon/i }))

    await waitFor(() => expect(screen.getAllByText(/first canon fact/i).length).toBeGreaterThan(0))
    expect(screen.getByText('Session State')).toBeInTheDocument()
  })

  it('manages map details and campaign segments from the map tab', async () => {
    await renderLoadedApp()

    const inspectorPanels = screen.getByRole('tablist', { name: 'Inspector panels' })
    fireEvent.click(within(inspectorPanels).getByRole('tab', { name: 'Map' }))

    fireEvent.change(screen.getByLabelText('Map title'), {
      target: { value: 'Ash Gate Map' },
    })
    fireEvent.change(screen.getByLabelText('Map description'), {
      target: { value: 'The ruined gate and reservoir crossing.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create map details' }))

    await screen.findByText('Ash Gate Map')
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'POST', path: '/api/maps' }),
      ]),
    )

    fireEvent.change(screen.getByLabelText('Segment title'), {
      target: { value: 'Ash Gate' },
    })
    fireEvent.change(screen.getByLabelText('Segment description'), {
      target: { value: 'The first dangerous crossing.' },
    })
    fireEvent.change(screen.getByLabelText('Trigger condition'), {
      target: { value: 'When the party approaches the gate.' },
    })
    fireEvent.change(screen.getByLabelText('Tags'), {
      target: { value: 'danger, gate' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add segment' }))

    await screen.findByText('Ash Gate')
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'POST', path: '/api/segments' }),
      ]),
    )

    const activeCheckbox = screen.getByLabelText('Start as active segment')
    fireEvent.click(activeCheckbox)
    fireEvent.change(screen.getByLabelText('Segment title'), {
      target: { value: 'Hidden Bridge' },
    })
    fireEvent.change(screen.getByLabelText('Segment description'), {
      target: { value: 'A quiet route around the reservoir.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add segment' }))

    await screen.findByText('Hidden Bridge')
    const hiddenBridgeArticle = screen.getByText('Hidden Bridge').closest('article')
    expect(hiddenBridgeArticle).not.toBeNull()
    if (!hiddenBridgeArticle) return
    fireEvent.click(within(hiddenBridgeArticle).getByRole('button', { name: 'Set active' }))

    await waitFor(() =>
      expect(within(hiddenBridgeArticle).getByRole('button', { name: 'Set active' })).toBeDisabled(),
    )
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'POST',
          path: '/api/segments/activate',
          body: expect.objectContaining({
            campaign_id: 10,
            exclusive: true,
            segment_id: 51,
          }),
        }),
      ]),
    )

    const ashGateArticle = screen.getByText('Ash Gate').closest('article')
    expect(ashGateArticle).not.toBeNull()
    if (!ashGateArticle) return
    fireEvent.click(within(ashGateArticle).getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(screen.queryByText('Ash Gate')).not.toBeInTheDocument())
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'DELETE', path: '/api/segments/50' }),
      ]),
    )
  })

  it('shows and clears the jump-to-latest control based on turn feed scroll position', async () => {
    const { container } = await renderLoadedApp()
    const feed = container.querySelector<HTMLElement>('.turn-feed')
    expect(feed).not.toBeNull()
    if (!feed) return

    Object.defineProperty(feed, 'scrollHeight', { configurable: true, value: 1200 })
    Object.defineProperty(feed, 'clientHeight', { configurable: true, value: 300 })
    Object.defineProperty(feed, 'scrollTo', {
      configurable: true,
      value: vi.fn(({ top }: ScrollToOptions) => {
        feed.scrollTop = Number(top)
      }),
    })
    feed.scrollTop = 0

    fireEvent.scroll(feed)
    const latestButton = await screen.findByRole('button', { name: /Latest/i })
    expect(latestButton).toBeInTheDocument()

    fireEvent.click(latestButton)
    expect(feed.scrollTo).toHaveBeenCalledWith({ top: 1200, behavior: 'smooth' })
    expect(screen.queryByRole('button', { name: /Latest/i })).not.toBeInTheDocument()
  })

  it('exposes selected navigation, tab, and menu states to assistive tech', async () => {
    await renderLoadedApp()

    expect(screen.getByRole('button', { name: /Smoke Campaign/i })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: /Session Alpha/i })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: 'Turns' })).toHaveAttribute('aria-current', 'page')

    const sessionViews = screen.getByRole('tablist', { name: 'Session views' })
    expect(within(sessionViews).getByRole('tab', { name: 'Turns' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(within(sessionViews).getByRole('tab', { name: 'DM Response' }))
    expect(within(sessionViews).getByRole('tab', { name: 'DM Response' })).toHaveAttribute('aria-selected', 'true')

    const inspectorPanels = screen.getByRole('tablist', { name: 'Inspector panels' })
    fireEvent.click(within(inspectorPanels).getByRole('tab', { name: 'Canon' }))
    expect(within(inspectorPanels).getByRole('tab', { name: 'Canon' })).toHaveAttribute('aria-selected', 'true')

    const accountButton = screen.getByRole('button', { name: 'Account' })
    fireEvent.click(accountButton)
    expect(accountButton).toHaveAttribute('aria-expanded', 'true')
    expect(within(screen.getByRole('menu', { name: 'Account options' })).getByRole('menuitem', {
      name: 'Profile settings',
    })).toBeInTheDocument()

    const sessionMenuButton = screen.getByRole('button', { name: 'Session menu' })
    fireEvent.click(sessionMenuButton)
    expect(sessionMenuButton).toHaveAttribute('aria-expanded', 'true')
    expect(within(screen.getByRole('menu', { name: 'Session menu' })).getByRole('menuitem', {
      name: 'Rename session',
    })).toBeInTheDocument()
  })

  it('keeps icon-only controls named and light theme contrast readable', async () => {
    const { container } = await renderLoadedApp()

    const iconOnlyButtons = [...container.querySelectorAll<HTMLButtonElement>('button')].filter((button) => {
      const visibleText = button.textContent?.trim() ?? ''
      return visibleText.length === 0 && button.querySelector('svg')
    })
    expect(iconOnlyButtons.length).toBeGreaterThan(0)
    iconOnlyButtons.forEach((button) => {
      expect(button.getAttribute('aria-label') || button.getAttribute('title')).toBeTruthy()
    })

    const colors = lightThemeColors()
    for (const foreground of lightThemeContrastForegrounds) {
      for (const background of lightThemeContrastBackgrounds) {
        expect(colors[foreground]).toMatch(/^#[0-9a-fA-F]{6}$/)
        expect(colors[background]).toMatch(/^#[0-9a-fA-F]{6}$/)
        expect(contrastRatio(colors[foreground], colors[background])).toBeGreaterThanOrEqual(4.5)
      }
    }
  })

  it('traps modal focus and returns focus to the opener when closed', async () => {
    await renderLoadedApp()

    const addCampaignButton = screen.getByRole('button', { name: 'Add campaign' })
    addCampaignButton.focus()
    fireEvent.click(addCampaignButton)

    const dialog = await screen.findByRole('dialog', { name: 'Create New Campaign' })
    const campaignNameInput = within(dialog).getByLabelText('Campaign Name')
    await waitFor(() => expect(document.activeElement).toBe(campaignNameInput))

    const closeButton = within(dialog).getByRole('button', { name: 'Close create campaign' })
    const submitButton = within(dialog).getByRole('button', { name: 'Create Campaign' })
    closeButton.focus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(submitButton)

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Create New Campaign' })).not.toBeInTheDocument())
    expect(document.activeElement).toBe(addCampaignButton)
  })

  it('toggles TTS and falls back when browser fullscreen is blocked', async () => {
    await renderLoadedApp()

    const ttsButton = screen.getByRole('button', { name: 'Turn TTS on' })
    fireEvent.click(ttsButton)
    expect(await screen.findByRole('button', { name: 'Turn TTS off' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(screen.getByRole('button', { name: 'Enter fullscreen' }))
    expect(await screen.findByRole('button', { name: 'Exit fullscreen' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getAllByText(/Native fullscreen was blocked/i).length).toBeGreaterThan(0)
  })

  it('starts TTS from streamed DM chunks before the response ends', async () => {
    await renderLoadedApp()

    ttsFetchHandler = vi.fn(async () => jsonResponse({ error: 'stream probe' }, { status: 400 }))

    fireEvent.click(screen.getByRole('button', { name: 'Turn TTS on' }))
    await screen.findByRole('button', { name: 'Turn TTS off' })

    await act(async () => {
      socketHandler<{ turn_id: number }>('dm_response_start')({ turn_id: 76 })
      socketHandler<{ turn_id: number; chunk: string }>('dm_chunk')({
        turn_id: 76,
        chunk: 'The first torch gutters out, and a cold draft rolls over the stone.',
      })
    })

    await waitFor(() => expect(ttsFetchHandler).toHaveBeenCalledTimes(1))
    expect(fetchCalls.filter((call) => call.method === 'POST' && call.path === '/api/tts/stream')).toEqual([
      expect.objectContaining({
        body: { text: 'The first torch gutters out, and a cold draft rolls over the stone.' },
      }),
    ])
  })

  it('prefetches the next queued TTS sentence while current audio is playing', async () => {
    await renderLoadedApp()

    let objectUrlIndex = 0
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => `blob:tts-${++objectUrlIndex}`),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })

    const audioInstances: Array<{
      onended: (() => void) | null
      onerror: ((event: Event) => void) | null
      onpause: (() => void) | null
      play: () => Promise<void>
      pause: () => void
      preload: string
      src: string
    }> = []

    vi.stubGlobal(
      'Audio',
      vi.fn(function MockAudio(this: (typeof audioInstances)[number], src: string) {
        this.src = src
        this.preload = ''
        this.onended = null
        this.onerror = null
        this.onpause = null
        this.play = vi.fn(() => Promise.resolve())
        this.pause = vi.fn()
        audioInstances.push(this)
      }),
    )
    ttsFetchHandler = vi.fn(async () =>
      new Response(new Blob(['audio'], { type: 'audio/mpeg' }), {
        status: 200,
        headers: { 'Content-Type': 'audio/mpeg' },
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Turn TTS on' }))
    await screen.findByRole('button', { name: 'Turn TTS off' })

    await act(async () => {
      socketHandler<{ turn_id: number }>('dm_response_start')({ turn_id: 82 })
      socketHandler<{ turn_id: number; chunk: string }>('dm_chunk')({
        turn_id: 82,
        chunk:
          'First sentence carries enough detail to cross the playback threshold. ' +
          'Second sentence follows with another complete narration beat for prefetch.',
      })
    })

    await waitFor(() => expect(ttsFetchHandler).toHaveBeenCalledTimes(2))
    expect(audioInstances).toHaveLength(1)
    expect(fetchCalls.filter((call) => call.method === 'POST' && call.path === '/api/tts/stream').map((call) => call.body))
      .toEqual([
        { text: 'First sentence carries enough detail to cross the playback threshold.' },
        { text: 'Second sentence follows with another complete narration beat for prefetch.' },
      ])

    await act(async () => {
      audioInstances[0].onended?.()
    })
    await waitFor(() => expect(audioInstances).toHaveLength(2))
  })

  it('stops queued TTS without fan-out when the first audio request fails', async () => {
    await renderLoadedApp()

    ttsFetchHandler = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })

    fireEvent.click(screen.getByRole('button', { name: 'Turn TTS on' }))
    await screen.findByRole('button', { name: 'Turn TTS off' })

    const streamChunk =
      'The hallway bends sharply left, and the torchlight thins into a wavering copper line. ' +
      'Somewhere below, a chain drags once across stone. ' +
      'The silence after it feels deliberate, like something is waiting for your next breath.'

    await act(async () => {
      socketHandler<{
        turn_id: number
        requires_roll?: boolean
        rules_hint?: Record<string, never>
      }>('dm_response_start')({
        turn_id: 77,
      })
      socketHandler<{
        turn_id: number
        chunk: string
        requires_roll?: boolean
        rules_hint?: Record<string, never>
      }>('dm_chunk')({
        turn_id: 77,
        chunk: streamChunk,
      })
      socketHandler<void>('dm_response_end')()
    })

    await waitFor(() => expect(screen.getAllByText(/TTS failed: Failed to fetch/i).length).toBeGreaterThan(0))

    const ttsCalls = fetchCalls.filter((call) => call.method === 'POST' && call.path.startsWith('/api/tts/'))
    expect(ttsCalls).toEqual([
      expect.objectContaining({
        method: 'POST',
        path: '/api/tts/stream',
        body: { text: 'The hallway bends sharply left, and the torchlight thins into a wavering copper line.' },
      }),
      expect.objectContaining({
        method: 'POST',
        path: '/api/tts/stream',
        body: { text: 'The hallway bends sharply left, and the torchlight thins into a wavering copper line.' },
      }),
    ])
  })

  it('pauses TTS after a hard request failure so later DM responses do not retry', async () => {
    const rendered = await renderLoadedApp()

    ttsFetchHandler = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })

    fireEvent.click(screen.getByRole('button', { name: 'Turn TTS on' }))
    await screen.findByRole('button', { name: 'Turn TTS off' })

    await act(async () => {
      socketHandler<{ turn_id: number }>('dm_response_start')({ turn_id: 80 })
      socketHandler<{ turn_id: number; chunk: string }>('dm_chunk')({
        turn_id: 80,
        chunk: 'The cinders brighten along the archway as a low voice echoes from the vault.',
      })
      socketHandler<void>('dm_response_end')()
    })

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Turn TTS on' })).toHaveAttribute('aria-pressed', 'false')
    })

    await act(async () => {
      socketHandler<{ turn_id: number }>('dm_response_start')({ turn_id: 81 })
      socketHandler<{ turn_id: number; chunk: string }>('dm_chunk')({
        turn_id: 81,
        chunk: 'A second line of narration arrives, but TTS should stay paused after the first failure.',
      })
      socketHandler<void>('dm_response_end')()
    })

    const errorItems = [...rendered.container.querySelectorAll('.rail-error-history li')]
    expect(errorItems.map((item) => item.textContent)).toEqual([
      expect.stringContaining('TTS failed: Failed to fetch'),
    ])
    expect(ttsFetchHandler).toHaveBeenCalledTimes(2)
    expect(fetchCalls.filter((call) => call.method === 'POST' && call.path === '/api/tts/stream')).toHaveLength(2)
  })

  it('suppresses remaining streamed TTS chunks after playback fails', async () => {
    await renderLoadedApp()

    let objectUrlIndex = 0
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => `blob:tts-${++objectUrlIndex}`),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
    vi.stubGlobal(
      'Audio',
      vi.fn(function MockAudio(this: {
        onended: (() => void) | null
        onerror: ((event: Event) => void) | null
        onpause: (() => void) | null
        play: () => Promise<void>
        pause: () => void
        preload: string
        src: string
      }, src: string) {
        this.src = src
        this.preload = ''
        this.onended = null
        this.onerror = null
        this.onpause = null
        this.play = vi.fn(() => Promise.reject(new Error('Audio error')))
        this.pause = vi.fn()
      }),
    )
    ttsFetchHandler = vi.fn(async () =>
      new Response(new Blob(['audio'], { type: 'audio/mpeg' }), {
        status: 200,
        headers: { 'Content-Type': 'audio/mpeg' },
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Turn TTS on' }))
    await screen.findByRole('button', { name: 'Turn TTS off' })

    await act(async () => {
      socketHandler<{ turn_id: number }>('dm_response_start')({ turn_id: 78 })
      socketHandler<{ turn_id: number; chunk: string }>('dm_chunk')({
        turn_id: 78,
        chunk: 'The first torch gutters out, and a cold draft rolls over the stone.',
      })
    })

    await waitFor(() =>
      expect(screen.getAllByText(/TTS playback failed: Audio error/i).length).toBeGreaterThan(0),
    )
    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Turn TTS on' })).toHaveAttribute('aria-pressed', 'false')
    })

    await act(async () => {
      socketHandler<{ turn_id: number; chunk: string }>('dm_chunk')({
        turn_id: 78,
        chunk: ' The second torch dies, and the chamber answers with a hollow metallic knock.',
      })
      socketHandler<void>('dm_response_end')()
    })

    expect(ttsFetchHandler).toHaveBeenCalledTimes(1)
    expect(fetchCalls.filter((call) => call.method === 'POST' && call.path === '/api/tts/stream')).toHaveLength(1)
  })

  it('reports one TTS failure when multiple chunks are queued before playback fails', async () => {
    const rendered = await renderLoadedApp()

    let objectUrlIndex = 0
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => `blob:tts-${++objectUrlIndex}`),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })
    vi.stubGlobal(
      'Audio',
      vi.fn(function MockAudio(this: {
        onended: (() => void) | null
        onerror: ((event: Event) => void) | null
        onpause: (() => void) | null
        play: () => Promise<void>
        pause: () => void
        preload: string
        src: string
      }, src: string) {
        this.src = src
        this.preload = ''
        this.onended = null
        this.onerror = null
        this.onpause = null
        this.play = vi.fn(() => Promise.reject(new Error('Audio error')))
        this.pause = vi.fn()
      }),
    )
    ttsFetchHandler = vi.fn(async () =>
      new Response(new Blob(['audio'], { type: 'audio/mpeg' }), {
        status: 200,
        headers: { 'Content-Type': 'audio/mpeg' },
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Turn TTS on' }))
    await screen.findByRole('button', { name: 'Turn TTS off' })

    await act(async () => {
      socketHandler<{ turn_id: number }>('dm_response_start')({ turn_id: 79 })
      socketHandler<{ turn_id: number; chunk: string }>('dm_chunk')({
        turn_id: 79,
        chunk:
          'The first torch gutters out, and a cold draft rolls over the stone. ' +
          'The second torch dies, and the chamber answers with a hollow metallic knock.',
      })
      socketHandler<void>('dm_response_end')()
    })

    await waitFor(() => {
      const errorItems = [...rendered.container.querySelectorAll('.rail-error-history li')]
      expect(errorItems.map((item) => item.textContent)).toEqual([
        expect.stringContaining('TTS playback failed: Audio error'),
      ])
    })

    expect(ttsFetchHandler).toHaveBeenCalledTimes(1)
    expect(fetchCalls.filter((call) => call.method === 'POST' && call.path === '/api/tts/stream')).toHaveLength(1)
  })
})
