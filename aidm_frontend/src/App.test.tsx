/// <reference types="node" />
// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import App from './App'
import { INITIATIVE_ROLL_ABILITY_KEY } from './gameActions'
import type {
  BetaSummary,
  Campaign,
  CampaignSegment,
  CampaignWorkspace,
  ClarificationRequest,
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
    account_id: null,
    username: null,
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
      state_snapshot: {},
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
      const workspaceToken = new Headers(init?.headers).get('X-AIDM-Workspace-Token')
      const workspaceIdHeader = new Headers(init?.headers).get('X-AIDM-Workspace-Id')
      if (method === 'GET' && path === '/api/accounts/me') {
        const accountToken = authorization?.replace(/^Bearer\s+/i, '') ?? ''
        if (!accountToken) {
          return jsonResponse({ error: 'Missing or invalid account session.', error_code: 'unauthorized' }, { status: 401 })
        }
        const selectedWorkspaceId = workspaceToken
          ? workspaceToken === 'aidan_test'
            ? 'aidan_test'
            : 'owner'
          : workspaceIdHeader === 'owner'
            ? 'owner'
            : null
        const workspaces = selectedWorkspaceId === 'aidan_test'
          ? [
              {
                workspace_id: 'aidan_test',
                workspace_name: 'Aidan Test',
                table_name: 'Aidan Test',
                access_mode: 'token',
                workspace_role: 'admin',
                is_workspace_admin: true,
                created_at: null,
                updated_at: null,
              },
            ]
          : [
              {
                workspace_id: 'owner',
                workspace_name: 'Test',
                table_name: 'Test',
                access_mode: 'token',
                workspace_role: 'admin',
                is_workspace_admin: true,
                created_at: null,
                updated_at: null,
              },
              {
                workspace_id: 'friend',
                workspace_name: 'Friend Table',
                table_name: 'Friend Table',
                access_mode: 'password',
                workspace_role: 'player',
                is_workspace_admin: false,
                created_at: null,
                updated_at: null,
              },
            ]
        const selectedWorkspace = workspaces.find((workspace) => workspace.workspace_id === selectedWorkspaceId)
        const requiresPasswordSetup = accountToken === 'legacy-account-token'
        return jsonResponse({
          account_id: 1,
          username: 'danny',
          first_name: 'Danny',
          last_name: 'Reichner',
          display_name: 'Danny Reichner',
          workspace_id: selectedWorkspace?.workspace_id ?? null,
          workspace_role: selectedWorkspace?.workspace_role ?? null,
          is_workspace_admin: selectedWorkspace?.is_workspace_admin ?? false,
          requires_password_setup: requiresPasswordSetup,
          workspaces,
        })
      }
      if (method === 'POST' && path === '/api/accounts/login') {
        return jsonResponse({
          account: {
            account_id: 1,
            username: body.username?.toLowerCase?.() ?? 'danny',
            first_name: body.first_name ?? 'Danny',
            last_name: body.last_name ?? 'Reichner',
            display_name: `${body.first_name ?? 'Danny'} ${body.last_name ?? 'Reichner'}`.trim(),
            workspace_id: null,
            workspace_role: null,
            is_workspace_admin: false,
            workspaces: [],
          },
          account_token: 'account-token',
          workspace_id: null,
          workspace_role: null,
          is_workspace_admin: false,
          claimed_player_ids: [],
          workspaces: [],
        })
      }
      if (method === 'POST' && path === '/api/accounts/workspace') {
        const workspaceId = body.table_name === 'Friday Night'
          ? 'Friday_Night'
          : body.workspace_token === 'aidan_test'
            ? 'aidan_test'
            : 'owner'
        const workspaces = [
          {
            workspace_id: workspaceId,
            workspace_name: body.table_name ?? workspaceId,
            table_name: body.table_name ?? workspaceId,
            access_mode: body.table_password ? 'password' : 'token',
            workspace_role: 'admin',
            is_workspace_admin: true,
            created_at: null,
            updated_at: null,
          },
        ]
        return jsonResponse({
          account: {
            account_id: 1,
            username: 'danny',
            first_name: 'Danny',
            last_name: 'Reichner',
            display_name: 'Danny Reichner',
            workspace_id: workspaceId,
            workspace_role: 'admin',
            is_workspace_admin: true,
            workspaces,
          },
          account_token: authorization?.replace(/^Bearer\s+/i, '') || 'account-token',
          workspace_id: workspaceId,
          workspace_role: 'admin',
          is_workspace_admin: true,
          claimed_player_ids: [],
          workspaces,
        })
      }
      if (method === 'POST' && path === '/api/accounts/workspaces') {
        const workspaceId = String(body.table_name ?? 'New Table').replace(/[^A-Za-z0-9_-]+/g, '_')
        const workspaces = [
          {
            workspace_id: workspaceId,
            workspace_name: body.table_name,
            table_name: body.table_name,
            access_mode: body.access_mode,
            workspace_role: 'admin',
            is_workspace_admin: true,
            created_at: null,
            updated_at: null,
          },
        ]
        return jsonResponse(
          {
            account: {
              account_id: 1,
              username: 'danny',
              first_name: 'Danny',
              last_name: 'Reichner',
              display_name: 'Danny Reichner',
              workspace_id: workspaceId,
              workspace_role: 'admin',
              is_workspace_admin: true,
              workspaces,
            },
            account_token: authorization?.replace(/^Bearer\s+/i, '') || 'account-token',
            workspace_id: workspaceId,
            workspace_role: 'admin',
            is_workspace_admin: true,
            claimed_player_ids: [],
            workspaces,
            ...(body.access_mode === 'token' ? { workspace_token: `generated-token-for-${workspaceId}` } : {}),
          },
          { status: 201 },
        )
      }
      if (method === 'POST' && path === '/api/accounts/workspace/select') {
        const workspaceId = body.workspace_id ?? 'owner'
        const workspaces = [
          {
            workspace_id: workspaceId,
            workspace_role: 'admin',
            is_workspace_admin: true,
            created_at: null,
            updated_at: null,
          },
        ]
        return jsonResponse({
          account: {
            account_id: 1,
            username: 'danny',
            first_name: 'Danny',
            last_name: 'Reichner',
            display_name: 'Danny Reichner',
            workspace_id: workspaceId,
            workspace_role: 'admin',
            is_workspace_admin: true,
            workspaces,
          },
          account_token: authorization?.replace(/^Bearer\s+/i, '') || 'account-token',
          workspace_id: workspaceId,
          workspace_role: 'admin',
          is_workspace_admin: true,
          claimed_player_ids: [],
          workspaces,
        })
      }
      if (method === 'DELETE' && path.startsWith('/api/accounts/workspaces/')) {
        const removedWorkspaceId = decodeURIComponent(path.slice('/api/accounts/workspaces/'.length))
        const deletingTable = removedWorkspaceId === 'owner'
        const workspaces = deletingTable
          ? []
          : [
              {
                workspace_id: 'owner',
                workspace_name: 'Test',
                table_name: 'Test',
                access_mode: 'token',
                workspace_role: 'admin',
                is_workspace_admin: true,
                created_at: null,
                updated_at: null,
              },
            ]
        return jsonResponse({
          account: {
            account_id: 1,
            username: 'danny',
            first_name: 'Danny',
            last_name: 'Reichner',
            display_name: 'Danny Reichner',
            workspace_id: deletingTable ? null : 'owner',
            workspace_role: deletingTable ? null : 'admin',
            is_workspace_admin: !deletingTable,
            workspaces,
          },
          account_token: authorization?.replace(/^Bearer\s+/i, '') || 'account-token',
          workspace_id: deletingTable ? null : 'owner',
          workspace_role: deletingTable ? null : 'admin',
          is_workspace_admin: !deletingTable,
          claimed_player_ids: [],
          workspaces,
          workspace_action: deletingTable ? 'deleted' : 'removed',
          workspace_id_removed: removedWorkspaceId,
        })
      }
      if (
        requiredAuthToken &&
        path.startsWith('/api/') &&
        authorization !== `Bearer ${requiredAuthToken}` &&
        workspaceToken !== requiredAuthToken &&
        workspaceIdHeader !== 'owner'
      ) {
        return jsonResponse(
          {
            details: {},
            error: 'Missing or invalid workspace token.',
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
            state_snapshot: session?.state_snapshot ?? {},
            updated_at: fixedNow.toISOString(),
          },
        )
      }

      const equipmentMatch = path.match(/^\/api\/players\/(\d+)\/inventory\/equipment$/)
      if (method === 'PATCH' && equipmentMatch) {
        const playerId = Number(equipmentMatch[1])
        const current = playerDetails[playerId]
        if (!current) {
          return jsonResponse({ error: 'Player not found.', error_code: 'player_not_found' }, { status: 404 })
        }
        const itemId = body.item_id ?? body.itemId
        const itemName = body.item_name ?? body.itemName
        const action = body.action === 'unequip' ? 'unequip' : 'equip'
        const inventory = Array.isArray(current.inventory)
          ? current.inventory.map((entry) => ({ ...(entry as Record<string, unknown>) }))
          : []
        const target = inventory.find((entry) =>
          itemId ? entry.id === itemId : String(entry.name).toLowerCase() === String(itemName).toLowerCase()
        )
        if (target) {
          const targetName = String(target.name ?? target.item ?? '').toLowerCase()
          target.equipped = action === 'equip'
          target.slot = action === 'equip'
            ? target.slot ?? (/greataxe|great axe|greatsword|great sword|maul|two.?hand/.test(targetName) ? 'two_hands' : 'main_hand')
            : target.slot
        }
        const updated = {
          ...current,
          inventory,
          snapshot_changed: Boolean(body.session_id ?? body.sessionId),
          equipment_update: {
            action,
            session_id: body.session_id ?? body.sessionId ?? null,
            snapshot_changed: Boolean(body.session_id ?? body.sessionId),
          },
        }
        playerDetails[playerId] = updated as PlayerDetail
        return jsonResponse(updated)
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
          account_id: null,
          username: null,
          campaign_id: campaignId,
          name: body.name ?? 'Local Player',
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
          state_snapshot: session.state_snapshot,
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
          state_snapshot: session.state_snapshot,
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
    document.cookie = 'aidm_account_token=; Max-Age=0; Path=/; SameSite=Lax'
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

  it('allows the next send while the previous saved turn is only canon pending', async () => {
    await renderLoadedApp()

    await act(async () => {
      socketHandler<{ turn_id: number; turn_number?: number }>('dm_response_start')({
        turn_id: 77,
        turn_number: 4,
      })
      socketHandler<{ turn_id: number; chunk: string }>('dm_chunk')({
        turn_id: 77,
        chunk: 'The arena dust settles as the last beam of energy fades.',
      })
      socketHandler<{ session_id: number; turn_id: number; status: string; details: Record<string, unknown> }>(
        'turn_status',
      )({
        session_id: 20,
        turn_id: 77,
        status: 'saved',
        details: { stage: 'dm_response' },
      })
      socketHandler<{ session_id: number; turn_id: number; status: string; details: Record<string, unknown> }>(
        'turn_status',
      )({
        session_id: 20,
        turn_id: 77,
        status: 'canon_pending',
        details: { job_id: 9 },
      })
    })

    await waitFor(() => expect(screen.getAllByText('canon pending').length).toBeGreaterThan(0))

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'I launch forward before the smoke clears.' } })
    socketMock.socket.emit.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: 'I launch forward before the smoke clears.',
        }),
      ),
    )
  })

  it('keeps a pending player message below the previous DM while log refreshes settle', async () => {
    const rendered = await renderLoadedApp()
    const pendingMessage = 'I sprint through the smoke before the echo fades.'

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: pendingMessage } })
    socketMock.socket.emit.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: pendingMessage,
        }),
      ),
    )

    const rowTexts = [...rendered.container.querySelectorAll('.turn-feed .turn-row')].map(
      (row) => row.textContent ?? '',
    )
    const latestDmIndex = rowTexts.findIndex((text) => text.includes('The chamber beyond is much larger'))
    const pendingIndex = rowTexts.findIndex((text) => text.includes(pendingMessage))
    expect(latestDmIndex).toBeGreaterThanOrEqual(0)
    expect(pendingIndex).toBeGreaterThan(latestDmIndex)
    expect(rowTexts.at(-1)).toContain(pendingMessage)

    const logFetchCount = fetchCalls.filter((call) => call.method === 'GET' && call.path === '/api/sessions/20/log').length
    await act(async () => {
      socketHandler<{ session_id?: number }>('session_log_update')({ session_id: 20 })
    })
    await waitFor(() =>
      expect(fetchCalls.filter((call) => call.method === 'GET' && call.path === '/api/sessions/20/log').length)
        .toBeGreaterThan(logFetchCount),
    )
    expect(screen.getByText(pendingMessage)).toBeInTheDocument()

    const sendPayload = socketMock.socket.emit.mock.calls.find(([event]) => event === 'send_message')?.[1] as {
      client_message_id?: string
    }
    await act(async () => {
      socketHandler<{
        message: string
        speaker: string
        turn_id: number
        turn_number: number
        requires_roll: boolean
        rules_hint: Record<string, unknown>
        context_version: string
        client_message_id: string
        action_intent: Record<string, unknown>
      }>('new_message')({
        message: pendingMessage,
        speaker: 'Ember',
        turn_id: 78,
        turn_number: 5,
        requires_roll: false,
        rules_hint: { requires_roll: false },
        context_version: 'v2',
        client_message_id: sendPayload.client_message_id ?? '',
        action_intent: {
          kind: 'message',
          source: 'composer',
          text: pendingMessage,
          client_message_id: sendPayload.client_message_id ?? '',
        },
      })
    })

    expect(screen.getAllByText(pendingMessage)).toHaveLength(1)
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
        account_id: null,
        username: null,
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
    await act(async () => {
      socketHandler<
        Array<{
          id: number
          character_name: string
          name: string
          race?: string
          sex?: string
          profile_image?: string
          class_?: string
          char_class?: string
        }>
      >('active_players')([
        {
          id: 30,
          character_name: 'Ember',
          name: 'Danny',
          race: 'Human',
          sex: 'female',
          profile_image: '/profile-icons/human_female.png',
          class_: 'Wizard',
          char_class: 'Wizard',
        },
        {
          id: 31,
          character_name: 'Borin',
          name: 'Maya',
          race: 'Dwarf',
          sex: 'male',
          profile_image: '/profile-icons/dwarf_male.png',
          class_: 'Fighter',
          char_class: 'Fighter',
        },
      ])
    })

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'the silver key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Interact' }))

    expect(screen.getByLabelText('Interaction options')).toBeInTheDocument()
    expect(screen.getByLabelText('Interaction target')).toHaveValue('player:31')
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

  it('shows a party-visible roll wait indicator with the remaining character and check', async () => {
    playersByCampaign[10] = [
      ...playersByCampaign[10],
      {
        player_id: 31,
        workspace_id: 'owner',
        account_id: null,
        username: null,
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
    sessionLogs[20] = [
      {
        id: 1,
        entry_type: 'player',
        message: 'Ember: I shove the warehouse door open.',
        metadata: { turn_id: 7, turn_number: 3, persistence_status: 'saved' },
        timestamp: '2026-06-06T10:40:00.000Z',
      },
      {
        id: 2,
        entry_type: 'dm',
        message: 'DM: The bandits draw steel. Everyone roll initiative.',
        metadata: {
          turn_id: 7,
          turn_number: 3,
          requires_roll: true,
          outcome_status: 'deferred',
          rule_type: 'initiative',
          remaining_player_ids: [30, 31],
          persistence_status: 'saved',
        },
        timestamp: '2026-06-06T10:41:00.000Z',
      },
      {
        id: 3,
        entry_type: 'system',
        message: '**Check Resolved**: turn 7 resolved with roll 12.',
        metadata: {
          turn_id: 8,
          turn_number: 4,
          resolved_turn_id: 7,
          roll_value: 12,
          remaining_player_ids: [31],
          persistence_status: 'saved',
        },
        timestamp: '2026-06-06T10:42:00.000Z',
      },
    ]

    await renderLoadedApp()

    const banner = screen.getByRole('status', { name: 'Pending roll' })
    expect(within(banner).getByText('Waiting on Borin to roll')).toBeInTheDocument()
    expect(within(banner).getByText('Turn 3: initiative')).toBeInTheDocument()
    expect(within(banner).getByText('The bandits draw steel. Everyone roll initiative.')).toBeInTheDocument()
    expect(within(banner).getByText('Roll needed')).toBeInTheDocument()
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

  it('rolls initiative from the Roll selector using the dexterity modifier', async () => {
    await renderLoadedApp()

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.click(screen.getByRole('button', { name: 'Roll' }))
    fireEvent.change(screen.getByLabelText('Roll ability'), {
      target: { value: INITIATIVE_ROLL_ABILITY_KEY },
    })

    expect(screen.getByRole('option', { name: 'Initiative (DEX +1)' })).toBeInTheDocument()
    expect(actionInput).toHaveValue('I roll for initiative:')
    expect(screen.getByLabelText('Roll modifier')).toHaveValue(1)
    expect(screen.getByLabelText('Roll reason')).toHaveValue('initiative')

    fireEvent.click(screen.getByRole('button', { name: 'Roll dice' }))
    const dialog = await screen.findByRole('dialog', { name: 'Dice Roller' })
    expect((actionInput as HTMLTextAreaElement).value).toMatch(/^I roll for initiative: \d+/)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Complete roll' }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: expect.stringMatching(/^I roll for initiative: \d+/),
          action_intent: expect.objectContaining({
            kind: 'roll',
            ability: {
              key: 'dexterity',
              label: 'Initiative',
              modifier: 1,
            },
            roll: expect.objectContaining({
              modifier: 1,
              reason: 'initiative',
            }),
          }),
        }),
      ),
    )
  })

  it('shows active players from the session socket roster and clears them on disconnect', async () => {
    await renderLoadedApp()

    await act(async () => {
      socketHandler<
        Array<{
          id: number
          character_name: string
          name: string
          race?: string
          sex?: string
          profile_image?: string
          class_?: string
          char_class?: string
          is_typing?: boolean
        }>
      >('active_players')([
        {
          id: 30,
          character_name: 'Ember',
          name: 'Danny',
          race: 'Human',
          sex: 'female',
          profile_image: '/profile-icons/human_female.png',
          class_: 'Wizard',
          char_class: 'Wizard',
          is_typing: true,
        },
        {
          id: 31,
          character_name: 'Borin',
          name: 'Maya',
          race: 'Dwarf',
          sex: 'male',
          profile_image: '/profile-icons/dwarf_male.png',
          class_: 'Fighter',
          char_class: 'Fighter',
          is_typing: true,
        },
      ])
    })

    const roster = screen.getByLabelText('Active players in this session')
    expect(screen.getByText('Active Players (2)')).toBeInTheDocument()
    expect(within(roster).getByText('Borin')).toBeInTheDocument()
    expect(within(roster).getByText('Maya - Dwarf Fighter')).toBeInTheDocument()
    expect(within(roster).getByAltText('Borin character icon')).toHaveAttribute('src', '/profile-icons/dwarf_male.png')
    expect(within(roster).getByLabelText('Borin is typing')).toHaveTextContent('Typing...')
    expect(within(roster).queryByLabelText('Ember is typing')).not.toBeInTheDocument()
    expect(within(roster).getByText('You')).toBeInTheDocument()

    await act(async () => {
      socketHandler<void>('disconnect')()
    })

    expect(screen.getByText('Active Players (0)')).toBeInTheDocument()
    expect(screen.getByText('No active players connected.')).toBeInTheDocument()
  })

  it('equips an inventory item from the sidebar', async () => {
    playerDetails[30] = {
      ...playerDetails[30],
      inventory: [
        { id: 'greataxe', name: 'Greataxe', quantity: 1, weight: 7 },
        { id: 'handaxe', name: 'Handaxe', quantity: 1, weight: 2, type: 'misc' },
      ],
    }
    await renderLoadedApp()

    expect(screen.getByRole('button', { name: 'Equip Greataxe' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Equip Handaxe' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Equip Greataxe' }))

    await waitFor(() =>
      expect(fetchCalls.some((call) => call.method === 'PATCH' && call.path === '/api/players/30/inventory/equipment')).toBe(true),
    )
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'PATCH',
          path: '/api/players/30/inventory/equipment',
          body: expect.objectContaining({ session_id: 20 }),
        }),
      ]),
    )
    expect(await screen.findByText(/Equipped - two hands/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Unequip Greataxe' })).toBeInTheDocument()
  })

  it('shows the selected character spellbook in the inspector', async () => {
    playerDetails[30] = {
      ...playerDetails[30],
      character_sheet: {
        ...(playerDetails[30].character_sheet as Record<string, unknown>),
        spellbook: {
          knownSpells: [
            {
              id: 'spell-cobalt-charm',
              name: 'Cobalt Charm',
              level: 1,
              sourceType: 'class_catalog',
              sourceDetail: 'sorcerer',
              description: 'Tint a social moment with charged blue sparks.',
              catalog: 'aidm-original',
            },
            {
              id: 'spell-river-ward',
              name: 'River Ward',
              level: 1,
              sourceType: 'race_catalog',
              sourceDetail: 'riverborn',
              description: 'Raise a quick protective sign from moving water.',
              catalog: 'aidm-original',
            },
          ],
        },
      },
    }

    await renderLoadedApp()

    expect(screen.getByText('Spellbook (2)')).toBeInTheDocument()
    expect(screen.getByText('Cobalt Charm')).toBeInTheDocument()
    expect(screen.getByText(/Tint a social moment/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Magic' }))

    expect(screen.getByText('River Ward')).toBeInTheDocument()
    expect(screen.getByText(/Raise a quick protective sign/)).toBeInTheDocument()
  })

  it('shows custom race active abilities and passive traits in the magic inspector', async () => {
    playerDetails[30] = {
      ...playerDetails[30],
      race: 'Himeros',
      race_selection: {
        raceId: 'himeros',
        raceName: 'Himeros',
        source: 'custom',
        customRaceDefinition: {
          traits: [
            {
              id: 'himeros_aura_of_desire',
              name: 'Aura of Desire',
              category: 'active_ability',
              description: 'Creatures of your choice within 30 feet must make a Wisdom saving throw.',
              mechanics: {
                activeAbility: {
                  actionType: 'action',
                  cooldown: 'longRest',
                  effectType: 'charm',
                },
              },
            },
            {
              id: 'himeros_divine_beauty',
              name: 'Divine Beauty',
              category: 'skill',
              description: 'You have proficiency in the Persuasion skill. If already proficient, you gain expertise.',
              mechanics: {
                skillProficiency: { skill: 'Persuasion', expertiseIfProficient: true },
              },
            },
          ],
        } as Record<string, unknown>,
      } as PlayerDetail['race_selection'],
    }

    await renderLoadedApp()

    expect(screen.getByText('Abilities & Traits (2)')).toBeInTheDocument()
    expect(screen.getByText('Aura of Desire')).toBeInTheDocument()
    expect(screen.getByText(/Race \/ Himeros \/ Action \/ Long Rest/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Magic' }))

    expect(screen.getByText('Divine Beauty')).toBeInTheDocument()
    expect(screen.getByText(/Persuasion skill/)).toBeInTheDocument()
  })

  it('keeps turn mode overrides behind the hidden admin tools', async () => {
    await renderLoadedApp()
    socketMock.socket.emit.mockClear()

    expect(screen.queryByRole('button', { name: 'Auto' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Structured' })).not.toBeInTheDocument()

    const actionLabel = screen.getByText(/Your Action/i)
    for (let index = 0; index < 5; index += 1) {
      fireEvent.click(actionLabel)
    }

    fireEvent.click(screen.getByRole('button', { name: 'Structured' }))

    expect(socketMock.socket.emit).toHaveBeenCalledWith(
      'set_turn_control',
      expect.objectContaining({
        session_id: 20,
        player_id: 30,
        mode: 'structured',
        source: 'manual',
        active_player_id: 30,
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Auto' }))

    expect(socketMock.socket.emit).toHaveBeenCalledWith(
      'set_turn_control',
      expect.objectContaining({
        session_id: 20,
        player_id: 30,
        mode: 'free',
        source: 'auto',
        active_player_id: null,
      }),
    )
  })

  it('lets an outside player send into spotlight so the conductor can judge joining', async () => {
    sessionStates[20] = {
      ...sessionStates[20],
      state_snapshot: {
        turnControl: {
          mode: 'spotlight',
          activePlayerId: 31,
          activePlayerName: 'Borin',
        },
      },
    }
    await renderLoadedApp()

    expect(await screen.findByText('Auto: Spotlight - Borin')).toBeInTheDocument()
    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'I step beside Borin and add my support.' } })
    socketMock.socket.emit.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    expect(socketMock.socket.emit).toHaveBeenCalledWith('send_message', expect.objectContaining({
      message: 'I step beside Borin and add my support.',
    }))
    expect(screen.queryByText('Queued draft')).not.toBeInTheDocument()
  })

  it('keeps structured out-of-turn actions as queued drafts instead of sending them', async () => {
    sessionStates[20] = {
      ...sessionStates[20],
      state_snapshot: {
        turnControl: {
          mode: 'structured',
          activePlayerId: 31,
          activePlayerName: 'Borin',
        },
      },
    }
    await renderLoadedApp()

    expect(screen.getByText('Auto: Structured - Borin')).toBeInTheDocument()
    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'I kick open the side door.' } })
    socketMock.socket.emit.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    expect(socketMock.socket.emit).not.toHaveBeenCalledWith('send_message', expect.anything())
    expect(actionInput).toHaveValue('I kick open the side door.')
    expect(screen.getByText('Queued draft')).toBeInTheDocument()
    expect(screen.getAllByText('I kick open the side door.').length).toBeGreaterThan(0)
  })

  it('renders Scene State from the live session state snapshot without a workspace reload', async () => {
    sessionsByCampaign[10] = [
      {
        ...sessionsByCampaign[10][0],
        state_snapshot: {},
      },
    ]
    sessionStates[20] = {
      ...sessionStates[20],
      state_snapshot: {
        currentScene: {
          name: 'Blackwake Tavern',
          locationId: 'blackwake_tavern',
          sceneType: 'social',
          mood: 'tense',
          dangerLevel: 2,
          activeQuestIds: [
            'find_missing_sailor',
            'question_captain_velra',
            'search_north_docks',
            'trace_lantern_bridge',
            'chart_ash_gate',
          ],
        },
        quests: [
          {
            id: 'find_missing_sailor',
            title: 'Find the Missing Sailor',
            status: 'active',
            stage: 'Investigate the docks',
          },
          {
            id: 'question_captain_velra',
            title: 'Question Captain Velra',
            status: 'active',
            stage: 'Ask about the missing crew',
          },
          {
            id: 'search_north_docks',
            title: 'Search North Docks',
            status: 'active',
            stage: 'Check the moorings',
          },
          {
            id: 'trace_lantern_bridge',
            title: 'Trace Lantern Bridge',
            status: 'active',
            stage: 'Follow the lantern ash',
          },
          {
            id: 'chart_ash_gate',
            title: 'Chart the Ash Gate',
            status: 'active',
            stage: 'Map the sealed entrance',
          },
        ],
        locations: [
          {
            id: 'blackwake_tavern',
            name: 'Blackwake Tavern',
            status: 'visited',
            type: 'tavern',
            lastVisitedTurn: 12,
          },
          {
            id: 'north_docks',
            name: 'North Docks',
            status: 'visited',
            type: 'road',
            lastVisitedTurn: 11,
          },
          {
            id: 'ash_gate',
            name: 'Ash Gate',
            status: 'visited',
            type: 'ruins',
            lastVisitedTurn: 10,
          },
          {
            id: 'lantern_bridge',
            name: 'Lantern Bridge',
            status: 'visited',
            type: 'road',
            lastVisitedTurn: 9,
          },
          {
            id: 'saltmarket',
            name: 'Saltmarket',
            status: 'visited',
            type: 'town',
            lastVisitedTurn: 8,
          },
          {
            id: 'old_lighthouse',
            name: 'Old Lighthouse',
            status: 'visited',
            type: 'ruins',
            lastVisitedTurn: 7,
          },
        ],
        knownNpcs: [
          {
            id: 'captain_velra',
            name: 'Captain Velra',
            race: 'Human',
            role: 'dock captain',
            disposition: 'friendly',
            status: 'met',
            lastSeenTurn: 12,
          },
          {
            id: 'marta_fenwick',
            name: 'Marta Fenwick',
            race: 'Halfling',
            role: 'shopkeeper',
            disposition: 'friendly',
            status: 'met',
            lastSeenTurn: 11,
          },
          {
            id: 'new_sentry',
            name: 'New Sentry',
            race: 'Elf',
            role: 'guard',
            disposition: 'neutral',
            status: 'known',
            lastSeenTurn: 10,
          },
          {
            id: 'dock_mage',
            name: 'Dock Mage',
            race: 'Tiefling',
            role: 'mage',
            disposition: 'suspicious',
            status: 'known',
            lastSeenTurn: 9,
          },
          {
            id: 'harbor_clerk',
            name: 'Harbor Clerk',
            race: 'Dwarf',
            role: 'clerk',
            disposition: 'neutral',
            status: 'known',
            lastSeenTurn: 8,
          },
          {
            id: 'old_hermit',
            name: 'Old Hermit',
            race: 'Gnome',
            role: 'witness',
            disposition: 'unknown',
            status: 'known',
            lastSeenTurn: 7,
          },
        ],
      },
    }

    await renderLoadedApp()

    expect(screen.getByText('Scene State')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getAllByText('Blackwake Tavern').length).toBeGreaterThan(0)
    })
    expect(screen.getByText('Find the Missing Sailor')).toBeInTheDocument()
    expect(screen.getByText('Chart the Ash Gate')).toBeInTheDocument()
    expect(screen.getByText('Captain Velra (Human)')).toBeInTheDocument()
    expect(screen.queryByText('Old Hermit (Gnome)')).not.toBeInTheDocument()
    expect(screen.queryByText('Old Lighthouse')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Show 1 older NPC/i }))
    expect(screen.getByText('Old Hermit (Gnome)')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Show 1 older place/i }))
    expect(screen.getByText('Old Lighthouse')).toBeInTheDocument()
  })

  it('emits typing presence while the composer text changes', async () => {
    await renderLoadedApp()

    socketMock.socket.emit.mockClear()
    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'check the rune' } })

    expect(socketMock.socket.emit).toHaveBeenCalledWith('typing_status', {
      session_id: 20,
      player_id: 30,
      is_typing: true,
    })

    fireEvent.change(actionInput, { target: { value: '' } })
    expect(socketMock.socket.emit).toHaveBeenCalledWith('typing_status', {
      session_id: 20,
      player_id: 30,
      is_typing: false,
    })
  })

  it('keeps default chat text and persists reader font controls', async () => {
    await renderLoadedApp()

    const feed = document.querySelector<HTMLElement>('.turn-feed')
    expect(feed).toHaveClass('chat-text-size-default')
    expect(feed).toHaveClass('chat-text-font-default')

    fireEvent.click(screen.getByRole('button', { name: 'Chat text options' }))
    fireEvent.change(screen.getByLabelText('Chat text size'), { target: { value: 'large' } })
    fireEvent.change(screen.getByLabelText('Chat text font'), { target: { value: 'sans' } })

    expect(feed).toHaveClass('chat-text-size-large')
    expect(feed).toHaveClass('chat-text-font-sans')
    expect(localStorage.getItem('aidm:chatTextSettings')).toBe(
      JSON.stringify({ size: 'large', font: 'sans' }),
    )
  })

  it('keeps item clarification choices visible through log refresh and resolves by socket', async () => {
    await renderLoadedApp()

    const clarification: ClarificationRequest = {
      id: 'clarify_77_001',
      turnId: 77,
      sessionId: 20,
      playerId: 30,
      type: 'item_resolution',
      prompt: 'Which sword do you use?',
      originalPlayerMessage: 'I swing my sword at the goblin.',
      originalAction: {
        id: 'act_001',
        type: 'combat.attack',
        actorId: 'player_30',
        weaponName: 'sword',
        sourceText: 'I swing my sword at the goblin.',
        requiresDMResolution: true,
      },
      options: [
        { itemId: 'great', label: 'Greatsword', description: 'weapon' },
        { itemId: 'long', label: 'Longsword', description: 'weapon' },
      ],
    }

    await act(async () => {
      socketHandler<ClarificationRequest>('clarification_required')(clarification)
    })
    expect(screen.getByText('Which sword do you use?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Greatsword/ })).toBeInTheDocument()

    await act(async () => {
      socketHandler<{ session_id?: number }>('session_log_update')({ session_id: 20 })
    })
    expect(screen.getByRole('button', { name: /Greatsword/ })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Greatsword/ }))
    expect(socketMock.socket.emit).toHaveBeenCalledWith(
      'resolve_clarification',
      expect.objectContaining({
        session_id: 20,
        player_id: 30,
        turn_id: 77,
        selected_item_id: 'great',
      }),
    )
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

  it('prompts for an account when the public app requires a table token', async () => {
    requiredAuthToken = 'shared-token'
    localStorage.setItem('aidm:selectedPlayerId', '30')

    render(<App />)

    let dialog = await screen.findByRole('dialog', { name: 'Log In' })
    expect(screen.queryByLabelText('Scene music player')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Backend URL')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Table Token')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sign Up' }))

    const usernameInput = within(dialog).getByLabelText('Username')
    await waitFor(() => expect(usernameInput).toHaveFocus())
    fireEvent.change(usernameInput, { target: { value: 'Danny' } })
    fireEvent.change(within(dialog).getByLabelText('First Name'), { target: { value: 'Danny' } })
    fireEvent.change(within(dialog).getByLabelText('Last Name'), { target: { value: 'Reichner' } })
    fireEvent.change(within(dialog).getByLabelText('Password'), { target: { value: 'secret' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))

    dialog = await screen.findByRole('dialog', { name: 'Join Table' })
    expect(screen.queryByLabelText('Scene music player')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Username')).not.toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Table Token'), { target: { value: 'shared-token' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Join Table' }))

    await screen.findByRole('heading', { name: /Session Alpha/i })
    expect(screen.getByLabelText('Scene music player')).toBeInTheDocument()
    expect(sessionStorage.getItem('aidm:authToken')).toBe('account-token')
    expect(sessionStorage.getItem('aidm:workspaceToken')).toBe('shared-token')
    expect(screen.queryByRole('dialog', { name: 'Join Table' })).not.toBeInTheDocument()
    await waitFor(() =>
      expect(screen.queryByText('Table token required. Enter the table token to connect.')).not.toBeInTheDocument(),
    )
    expect(screen.queryByText('Player load failed: Missing or invalid workspace token.')).not.toBeInTheDocument()
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

  it('keeps restored legacy passwordless sessions in password setup', async () => {
    requiredAuthToken = 'owner-token'
    sessionStorage.setItem('aidm:authToken', 'legacy-account-token')
    sessionStorage.setItem('aidm:workspaceToken', 'owner-token')
    localStorage.setItem('aidm:workspaceId', 'owner')

    render(<App />)

    const dialog = await screen.findByRole('dialog', { name: 'Log In' })
    expect(within(dialog).getAllByText('Passwords are required now. Please set one now.')).not.toHaveLength(0)
    expect(within(dialog).getByLabelText('New Password')).toBeInTheDocument()
    expect(screen.queryByLabelText('Scene music player')).not.toBeInTheDocument()
    await waitFor(() => expect(sessionStorage.getItem('aidm:workspaceToken')).toBeNull())
    expect(localStorage.getItem('aidm:workspaceId')).toBeNull()
  })

  it('opens account auth from the backend gear when no account is active', async () => {
    await renderLoadedApp()
    expect(screen.getByLabelText('Scene music player')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Change table access' }))

    const dialog = await screen.findByRole('dialog', { name: 'Log In' })
    expect(screen.queryByLabelText('Scene music player')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Backend URL')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Table Token')).not.toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Sign Up' })).toBeInTheDocument()
  })

  it('opens table auth from the backend gear when an account is active', async () => {
    sessionStorage.setItem('aidm:authToken', 'account-token')
    sessionStorage.setItem('aidm:workspaceToken', 'old-workspace')
    sessionStorage.setItem(
      'aidm:account',
      JSON.stringify({
        accountId: 1,
        username: 'danny',
        displayName: 'Danny Reichner',
        workspaceId: 'owner',
        workspaceRole: 'admin',
        isWorkspaceAdmin: true,
        workspaces: [
          {
            workspace_id: 'owner',
            workspace_role: 'admin',
            is_workspace_admin: true,
            created_at: null,
            updated_at: null,
          },
        ],
      }),
    )
    localStorage.setItem('aidm:workspaceId', 'owner')
    window.history.replaceState(null, '', '/?campaign=10&session=20')

    render(<App />)
    await screen.findByRole('button', { name: 'Change table access' })
    await screen.findByText('Test')
    expect(screen.getByText('Table')).toBeInTheDocument()
    expect(screen.queryByText('Same origin')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Change table access' }))

    const dialog = await screen.findByRole('dialog', { name: 'Join Table' })
    expect(within(dialog).queryByLabelText('Backend URL')).not.toBeInTheDocument()
    expect(within(dialog).getByRole('group', { name: 'Saved tables' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Test admin' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Delete Test' })).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Remove Friend Table' })).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Table Token')).toHaveValue('old-workspace')

    fireEvent.click(within(dialog).getByRole('button', { name: 'Remove Friend Table' }))
    let confirmDialog = await screen.findByRole('dialog', { name: 'Remove Saved Table' })
    expect(within(confirmDialog).getByText('Friend Table')).toBeInTheDocument()
    expect(within(confirmDialog).getByText('This removes the table from your saved tables only.')).toBeInTheDocument()
    fireEvent.click(within(confirmDialog).getByRole('button', { name: 'Remove' }))
    await waitFor(() =>
      expect(fetchCalls).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            method: 'DELETE',
            path: '/api/accounts/workspaces/friend',
          }),
        ]),
      ),
    )
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Remove Saved Table' })).not.toBeInTheDocument())

    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete Test' }))
    confirmDialog = await screen.findByRole('dialog', { name: 'Delete Table' })
    expect(within(confirmDialog).getByText('Test')).toBeInTheDocument()
    expect(
      within(confirmDialog).getByText('This permanently deletes the table for everyone. This cannot be undone.'),
    ).toBeInTheDocument()
    fireEvent.click(within(confirmDialog).getByRole('button', { name: 'Delete Table' }))
    await waitFor(() =>
      expect(fetchCalls).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            method: 'DELETE',
            path: '/api/accounts/workspaces/owner',
          }),
        ]),
      ),
    )
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Delete Table' })).not.toBeInTheDocument())
  })

  it('creates a token table and warns that the generated token is only shown once', async () => {
    sessionStorage.setItem('aidm:authToken', 'account-token')
    sessionStorage.setItem(
      'aidm:account',
      JSON.stringify({
        accountId: 1,
        username: 'danny',
        displayName: 'Danny Reichner',
        workspaceId: 'owner',
        workspaceRole: 'admin',
        isWorkspaceAdmin: true,
        workspaces: [
          {
            workspace_id: 'owner',
            workspace_role: 'admin',
            is_workspace_admin: true,
            created_at: null,
            updated_at: null,
          },
        ],
      }),
    )
    localStorage.setItem('aidm:workspaceId', 'owner')

    render(<App />)
    await screen.findByRole('button', { name: 'Change table access' })

    fireEvent.click(screen.getByRole('button', { name: 'Change table access' }))
    let dialog = await screen.findByRole('dialog', { name: 'Join Table' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }))

    dialog = await screen.findByRole('dialog', { name: 'Create Table' })
    fireEvent.change(within(dialog).getByLabelText('Table Name'), { target: { value: 'Token Table' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Token' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create Table' }))

    dialog = await screen.findByRole('dialog', { name: 'Save Table Token' })
    expect(within(dialog).getByLabelText('Generated table token')).toHaveValue('generated-token-for-Token_Table')
    expect(within(dialog).getByText('You will not be able to view it after you leave this page.')).toBeInTheDocument()
    expect(sessionStorage.getItem('aidm:workspaceToken')).toBeNull()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Done' }))
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Save Table Token' })).not.toBeInTheDocument())
  })

  it('does not join a session socket with a stale selected player', async () => {
    localStorage.setItem('aidm:selectedPlayerId', '999')

    await renderLoadedApp()

    await screen.findByRole('dialog', { name: 'Join Campaign' })
    await waitFor(() => expect(localStorage.getItem('aidm:selectedPlayerId')).toBeNull())
    expect(socketMock.socket.emit).not.toHaveBeenCalledWith(
      'join_session',
      expect.objectContaining({ player_id: 999 }),
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

    let dialog = await screen.findByRole('dialog', { name: 'Log In' })
    fireEvent.change(within(dialog).getByLabelText('Username'), { target: { value: 'Aidan' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))

    dialog = await screen.findByRole('dialog', { name: 'Join Table' })
    fireEvent.change(within(dialog).getByLabelText('Table Token'), { target: { value: 'aidan_test' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Join Table' }))

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
    const sessionStateFetchesBefore = fetchCalls.filter(
      (call) => call.method === 'GET' && call.path === '/api/sessions/20/state',
    ).length

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
    expect(
      fetchCalls.filter((call) => call.method === 'GET' && call.path === '/api/sessions/20/state'),
    ).toHaveLength(sessionStateFetchesBefore)
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/players/30',
        }),
      ]),
    )
  })

  it('refreshes session state when a state_applied turn reports world snapshot changes', async () => {
    await renderLoadedApp()
    const sessionStateFetchesBefore = fetchCalls.filter(
      (call) => call.method === 'GET' && call.path === '/api/sessions/20/state',
    ).length

    sessionStates[20] = {
      ...sessionStates[20],
      state_snapshot: {
        currentScene: {
          name: 'Moonlit Harbor',
          locationId: 'moonlit_harbor',
          sceneType: 'exploration',
          dangerLevel: 1,
          activeQuestIds: ['find_missing_sailor'],
        },
        quests: [
          {
            id: 'find_missing_sailor',
            title: 'Find the Missing Sailor',
            status: 'active',
            stage: 'Search the moonlit harbor',
          },
        ],
      },
    }

    await act(async () => {
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: { player_id: number; world_state_changed: boolean; snapshot_changed: boolean }
      }>('turn_status')({
        session_id: 20,
        turn_id: 7,
        status: 'state_applied',
        details: {
          player_id: 30,
          world_state_changed: true,
          snapshot_changed: true,
        },
      })
    })

    await screen.findByText('Moonlit Harbor')
    expect(
      fetchCalls.filter((call) => call.method === 'GET' && call.path === '/api/sessions/20/state'),
    ).toHaveLength(sessionStateFetchesBefore + 1)
  })

  it('does not reload session state twice for matching state_applied and canon_applied world flags', async () => {
    await renderLoadedApp()
    const sessionStateFetchesBefore = fetchCalls.filter(
      (call) => call.method === 'GET' && call.path === '/api/sessions/20/state',
    ).length

    sessionStates[20] = {
      ...sessionStates[20],
      state_snapshot: {
        currentScene: {
          name: 'Old Bell Tower',
          locationId: 'old_bell_tower',
          sceneType: 'exploration',
          dangerLevel: 2,
          activeQuestIds: [],
        },
      },
    }

    await act(async () => {
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: { player_id: number; world_state_changed: boolean; snapshot_changed: boolean }
      }>('turn_status')({
        session_id: 20,
        turn_id: 8,
        status: 'state_applied',
        details: {
          player_id: 30,
          world_state_changed: true,
          snapshot_changed: true,
        },
      })
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: { player_id: number; state_applied: boolean; world_state_changed: boolean; snapshot_changed: boolean }
      }>('turn_status')({
        session_id: 20,
        turn_id: 8,
        status: 'canon_applied',
        details: {
          player_id: 30,
          state_applied: true,
          world_state_changed: true,
          snapshot_changed: true,
        },
      })
    })

    await screen.findByText('Old Bell Tower')
    expect(
      fetchCalls.filter((call) => call.method === 'GET' && call.path === '/api/sessions/20/state'),
    ).toHaveLength(sessionStateFetchesBefore + 1)
  })

  it('refreshes the selected player when a transfer affects them from another player turn', async () => {
    await renderLoadedApp()

    playerDetails[30] = {
      ...playerDetails[30],
      inventory: [
        { name: 'Healing Potion', quantity: 2, weight: 0.5 },
        { name: 'Small Roll', quantity: 1 },
      ],
    }

    await act(async () => {
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: {
          player_id: number
          affected_player_ids: number[]
          inventory_changes_applied: Array<{ player_id: number; item_name: string; quantity: number }>
        }
      }>('turn_status')({
        session_id: 20,
        turn_id: 6,
        status: 'state_applied',
        details: {
          player_id: 31,
          affected_player_ids: [31, 30],
          inventory_changes_applied: [{ player_id: 30, item_name: 'Small Roll', quantity: 1 }],
        },
      })
    })

    await screen.findByText('Small Roll')
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
    fireEvent.change(within(creator).getByLabelText('Character Name'), {
      target: { value: 'Borin' },
    })
    fireEvent.click(within(creator).getByRole('button', { name: 'View Dwarf details' }))
    const dwarfDetails = await screen.findByRole('dialog', { name: 'Dwarf' })
    expect(within(dwarfDetails).getByText(/Dwarves are stone-wise, craft-proud/)).toBeInTheDocument()
    expect(within(dwarfDetails).getByText('Common, Dwarvish')).toBeInTheDocument()
    expect(within(dwarfDetails).getByText(/Average height:/)).toBeInTheDocument()
    fireEvent.click(within(dwarfDetails).getByRole('button', { name: 'Select Dwarf' }))
    fireEvent.click(within(creator).getByRole('button', { name: 'Male Dwarf' }))
    fireEvent.click(within(creator).getByRole('button', { name: 'Preview Cleric class' }))
    const clericDetails = await screen.findByRole('dialog', { name: 'Cleric' })
    expect(within(clericDetails).getByText(/Clerics heal, protect/)).toBeInTheDocument()
    expect(within(clericDetails).getByText('Life')).toBeInTheDocument()
    fireEvent.click(within(clericDetails).getByRole('button', { name: 'Select Cleric - Life' }))
    fireEvent.click(within(creator).getByRole('button', { name: 'Create Character' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Create Character' })).not.toBeInTheDocument())
    expect(fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'POST',
          path: '/api/players/campaigns/10/players',
          body: expect.objectContaining({
            character_name: 'Borin',
            race: 'Dwarf',
            sex: 'male',
            char_class: 'Cleric - Life',
          }),
        }),
      ]),
    )
    const createCall = fetchCalls.find(
      (call) => call.method === 'POST' && call.path === '/api/players/campaigns/10/players',
    )
    expect(createCall?.body).not.toHaveProperty('name')
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
    const raceInput = within(dialog).getByLabelText('Search races')
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
