/// <reference types="node" />
/* eslint-disable react-refresh/only-export-components -- Vitest harness exports shared fixtures and helpers. */
// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { expect, vi } from 'vitest'
import type { RefObject } from 'react'
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
    connected: true,
    emit: vi.fn(),
    on: vi.fn(),
    disconnect: vi.fn(),
  }
  socket.on.mockImplementation(() => socket)
  return { io: vi.fn<(url?: string) => typeof socket>(() => socket), socket }
})

vi.mock('socket.io-client', () => ({
  io: socketMock.io,
}))

vi.mock('./DiceRollDialog', () => ({
  default: ({
    die,
    result,
    rolls,
    status,
    targetLabel,
    dialogRef,
    onCancel,
    onComplete,
    onRetry,
  }: {
    die: string
    result: number | null
    rolls?: number[] | null
    status: string
    targetLabel?: string | null
    dialogRef?: RefObject<HTMLElement | null>
    onCancel: () => void
    onComplete: () => void
    onRetry: () => void
  }) => (
    <section ref={dialogRef} role="dialog" aria-label="Dice Roller">
      <strong>{die.toUpperCase()}</strong>
      <span>Result {result}</span>
      {rolls ? <span>Faces {rolls.join(', ')}</span> : null}
      <span>Status {status}</span>
      {targetLabel ? <span>{targetLabel}</span> : null}
      <button type="button" onClick={onCancel} data-autofocus>
        Cancel roll
      </button>
      <button type="button" onClick={onComplete}>
        Complete roll
      </button>
      <button type="button" onClick={onRetry}>
        Retry safely
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
    {
      id: 'fallback',
      label: 'Fallback',
      default_model: 'deterministic-v1',
      configured: true,
      models: [{ id: 'deterministic-v1', label: 'Deterministic Local Fallback' }],
    },
  ],
}

const ttsConfig: TtsRuntimeConfig = {
  provider: 'deepgram',
  configured: true,
  model: 'aura-2-draco-en',
}

const exampleCampaignPacks = [
  {
    pack_id: 'middle_earth.shadow_under_eryn_luin',
    title: 'Shadow Under Eryn Luin',
    description:
      'An original Lord of the Rings-inspired campaign in Middle-earth. The company is drawn into a quiet borderland crisis where old Dwarf-roads beneath the Blue Mountains have awakened, a forgotten oath is being exploited, and a remnant servant of the Shadow seeks a buried seeing-stone shard before the Free Peoples can seal it away.',
    short_description:
      'An original Lord of the Rings-inspired campaign in Middle-earth. The company is drawn into a quiet borderland crisis where old Dwarf-roads beneath the Blue Mountains...',
    version: '1.0.1',
    schema_version: '1',
    source_filename: 'shadow_under_eryn_luin_campaign_pack.json',
    world_name: 'Middle-earth: Western Eriador',
    length_estimate: {
      label: 'Medium campaign',
      sessions_min: 4,
      sessions_max: 6,
      hours_min: 12,
      hours_max: 18,
      checkpoint_count: 6,
      encounter_count: 4,
      pacing:
        'Six checkpoint spine with meaningful shortcuts through Moonwell, the Black Pines, and Khazad-tarn Gate. Most groups can finish in four to six sessions depending on how much they negotiate, explore, or rescue.',
    },
    source: 'bundled_example',
  },
]

let campaigns: Campaign[]
let worlds: World[]
let sessionsByCampaign: Record<number, SessionSummary[]>
let playersByCampaign: Record<number, Player[]>
let mapsByCampaign: Record<number, MapItem[]>
let segmentsByCampaign: Record<number, CampaignSegment[]>
let sessionLogs: Record<number, SessionLogEntry[]>
let sessionStates: Record<number, SessionState>
let playerDetails: Record<number, PlayerDetail>
let fetchCalls: Array<{
  method: string
  path: string
  origin: string
  body: unknown
  authorization: string | null
  workspaceToken: string | null
  workspaceIdHeader: string | null
}>
let ttsFetchHandler: ((path: string, body: unknown) => Promise<Response>) | null
let ttsConfigFetchError: string | null
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

function installMatchMediaMock(matches: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
}

function installLegacyMatchMediaMock(matches: boolean) {
  const addListener = vi.fn()
  const removeListener = vi.fn()
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches,
      media: query,
      onchange: null,
      addListener,
      removeListener,
      dispatchEvent: vi.fn(),
    })),
  )
  return { addListener, removeListener }
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
        { turn_id: 1, dm_output: 'The first remembered beat glows in the margin.' },
        { turn_id: 2, dm_output: 'The second remembered beat names the keeper.' },
        { turn_id: 3, dm_output: 'The third remembered beat marks the hidden bridge.' },
        { turn_id: 4, dm_output: 'The fourth remembered beat reveals the lantern city.' },
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
      weapon_proficiencies: ['category:simple'],
      character_sheet: { hp: 14, max_hp: 16, ac: 13, speed: 30 },
    },
  }
  fetchCalls = []
  ttsFetchHandler = null
  ttsConfigFetchError = null
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
      const authorization = new Headers(init?.headers).get('Authorization')
      const workspaceToken = new Headers(init?.headers).get('X-AIDM-Workspace-Token')
      const workspaceIdHeader = new Headers(init?.headers).get('X-AIDM-Workspace-Id')
      fetchCalls.push({ method, path, origin: url.origin, body, authorization, workspaceToken, workspaceIdHeader })

      if (method === 'GET' && path === '/api/health') return jsonResponse(health)
      if (method === 'GET' && path === '/api/capabilities') {
        const workspaceId = workspaceIdHeader ?? 'owner'
        const isAdmin = authorization !== 'Bearer player-token'
        return jsonResponse({
          workspace_id: workspaceId,
          account_id: authorization ? 1 : null,
          is_workspace_admin: isAdmin,
          capabilities: isAdmin
            ? ['player_read', 'player_action', 'dm_authoring', 'dm_runtime_control', 'debug_read', 'admin_workspace']
            : ['player_read', 'player_action'],
          descriptions: {},
        })
      }
      if (method === 'POST' && path === '/api/accounts/play-now') {
        const campaign = campaigns[0]
        const session = campaign ? sessionsByCampaign[campaign.campaign_id]?.[0] : undefined
        const player = session ? playerDetails[playersByCampaign[session.campaign_id]?.[0]?.player_id] : undefined
        const world = worlds.find((item) => item.world_id === campaign?.world_id)
        if (!campaign || !session || !player || !world) {
          return jsonResponse({ error: 'Missing Play Now fixture.' }, { status: 500 })
        }
        const workspace = {
          workspace_id: 'owner',
          workspace_name: 'Guest Table',
          table_name: 'Guest Table',
          access_mode: 'password',
          workspace_role: 'player',
          is_workspace_admin: false,
          created_at: null,
          updated_at: null,
        }
        return jsonResponse({
          mode: 'play_now',
          workspace_id: 'owner',
          campaign_id: campaign.campaign_id,
          session_id: session.session_id,
          player_id: player.player_id,
          world_id: world.world_id,
          idempotent_replay: false,
          guest_account: true,
          account_session: {
            account: {
              account_id: 41,
              username: 'guest-41',
              first_name: 'Guest',
              last_name: 'Adventurer',
              display_name: 'Guest Adventurer',
              workspace_id: 'owner',
              workspace_role: 'player',
              is_workspace_admin: false,
              requires_password_setup: false,
              workspaces: [workspace],
            },
            account_token: 'guest-token',
            account_token_transport: 'bearer',
            workspace_id: 'owner',
            workspace_role: 'player',
            is_workspace_admin: false,
            claimed_player_ids: [player.player_id],
            workspaces: [workspace],
          },
          campaign,
          session,
          player,
          pregen: {
            character_id: 'ember',
            character_name: player.character_name,
            name: player.name,
            race: player.race,
            sex: player.sex,
            class_: player.class_,
            char_class: player.char_class,
            level: player.level,
            tagline: 'A ready-made adventurer.',
            profile_image: player.profile_image,
            stats: player.stats,
            inventory: player.inventory,
            character_sheet: player.character_sheet,
          },
          example_pack: {
            example_pack_id: 'road.unremembered-kings',
            pack_id: 'road.unremembered-kings',
            source_filename: 'road.json',
            source: 'bundled_example',
          },
          join_context: {
            workspace_id: 'owner',
            campaign_id: campaign.campaign_id,
            session_id: session.session_id,
            player_id: player.player_id,
            world_id: world.world_id,
            socket: {
              event: 'join_session',
              payload: { workspace_id: 'owner', session_id: session.session_id, player_id: player.player_id },
            },
            send_message: {
              event: 'send_message',
              payload: {
                workspace_id: 'owner',
                campaign_id: campaign.campaign_id,
                session_id: session.session_id,
                player_id: player.player_id,
                world_id: world.world_id,
              },
            },
          },
        })
      }
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
      if (method === 'GET' && path === '/api/campaigns/example-packs') {
        return jsonResponse({
          packs: exampleCampaignPacks,
          count: exampleCampaignPacks.length,
        })
      }
      if (method === 'GET' && path === '/api/worlds') return jsonResponse(worlds)
      if (method === 'GET' && path === '/api/beta/summary') return jsonResponse(metrics)
      if (method === 'GET' && path === '/api/beta/incidents') {
        return jsonResponse({
          incidents: [
            {
              type: 'failed_turn',
              severity: 'high',
              campaign_id: 10,
              session_id: 20,
              turn_id: 2,
              provider: 'deepseek',
              model: 'deepseek-v4-pro',
              status: 'failed',
              latency_ms: 1800,
              message: 'DM turn failed before completion.',
              created_at: fixedNow.toISOString(),
            },
            {
              type: 'bad_turn_report',
              severity: 'medium',
              campaign_id: 10,
              session_id: 20,
              turn_id: 2,
              feedback_id: 901,
              category: 'continuity',
              provider: 'deepseek',
              model: 'deepseek-v4-pro',
              coherence_score: 1,
              message: 'Tester reported broken continuity.',
              created_at: fixedNow.toISOString(),
            },
            {
              type: 'failed_canon_job',
              severity: 'medium',
              campaign_id: 10,
              session_id: 20,
              turn_id: 2,
              job_id: 44,
              status: 'failed',
              attempts: 2,
              message: 'Canon extraction job failed.',
              created_at: fixedNow.toISOString(),
            },
            {
              type: 'telemetry_event',
              event_name: 'socket.dm_persist_failed',
              count: 1,
              severity: 'high',
              message: 'socket.dm_persist_failed recorded 1 time.',
            },
          ],
          summary: {
            failed_turn_count: 1,
            failed_canon_job_count: 1,
            bad_turn_report_count: 1,
            telemetry_incident_count: 1,
          },
          limit: 20,
        })
      }
      if (method === 'GET' && path === '/api/beta/session-quality') {
        const sessionId = Number(url.searchParams.get('session_id') ?? 20)
        return jsonResponse({
          session: {
            session_id: sessionId,
            campaign_id: 10,
            name: 'Session Alpha',
          },
          summary: {
            quality_status: 'review',
            total_turn_count: 2,
            completed_turn_count: 1,
            failed_turn_count: 1,
            awaiting_clarification_turn_count: 0,
            turn_failure_rate: 0.5,
            dm_response_latency_ms_avg: 1800,
            dm_response_latency_ms_p95: 1800,
            dm_response_latency_sample_count: 2,
            latest_turn_id: 2,
            latest_turn_status: 'failed',
            latest_turn_at: fixedNow.toISOString(),
            canon_job_count: 1,
            canon_job_failed_count: 1,
            canon_job_failure_rate: 1,
            bad_turn_report_count: 1,
            coherence_feedback_avg: 3.5,
            coherence_feedback_count: 2,
            state_mutation_count: 2,
            operator_action_count: 1,
          },
          operator_summary: {
            headline: 'Review recommended: 1 failed turn, 1 failed canon job, 1 bad-turn report.',
            details: [
              'Provider/model: deepseek / deepseek-v4-pro (2 turns).',
              'Latency: 1800 ms p95, 1800 ms avg across 2 samples.',
              'State/audit activity: 2 state mutations, 1 operator action.',
            ],
          },
          provider_model_turn_counts: [
            {
              provider: 'deepseek',
              model: 'deepseek-v4-pro',
              turn_count: 2,
            },
          ],
          recent_state_mutations: [],
          recent_operator_actions: [],
          limit: 5,
        })
      }
      if (method === 'GET' && path === '/api/beta/support-bundle') {
        const sessionId = url.searchParams.get('session_id')
        const numericSessionId = sessionId ? Number(sessionId) : null
        return jsonResponse({
          generated_at: fixedNow.toISOString(),
          workspace_id: 'owner',
          filters: {
            limit: Number(url.searchParams.get('limit') ?? 20),
            session_id: numericSessionId,
          },
          runtime: { env: 'test', auth_required: true },
          session: numericSessionId === null ? null : { session_id: numericSessionId, campaign_id: 10 },
          beta_summary: metrics,
          beta_slo: { status: 'visible' },
          session_quality:
            numericSessionId === null
              ? null
              : {
                  session: { session_id: numericSessionId, campaign_id: 10, name: 'Session Alpha' },
                  summary: {
                    quality_status: 'review',
                    total_turn_count: 2,
                    completed_turn_count: 1,
                    failed_turn_count: 1,
                    awaiting_clarification_turn_count: 0,
                    turn_failure_rate: 0.5,
                    dm_response_latency_ms_avg: 1800,
                    dm_response_latency_ms_p95: 1800,
                    dm_response_latency_sample_count: 2,
                    latest_turn_id: 2,
                    latest_turn_status: 'failed',
                    latest_turn_at: fixedNow.toISOString(),
                    canon_job_count: 1,
                    canon_job_failed_count: 1,
                    canon_job_failure_rate: 1,
                    bad_turn_report_count: 1,
                    coherence_feedback_avg: 3.5,
                    coherence_feedback_count: 2,
                    state_mutation_count: 2,
                    operator_action_count: 1,
                  },
                  operator_summary: {
                    headline: 'Review recommended: 1 failed turn, 1 failed canon job, 1 bad-turn report.',
                    details: [
                      'Provider/model: deepseek / deepseek-v4-pro (2 turns).',
                      'Latency: 1800 ms p95, 1800 ms avg across 2 samples.',
                      'State/audit activity: 2 state mutations, 1 operator action.',
                    ],
                  },
                  provider_model_turn_counts: [
                    { provider: 'deepseek', model: 'deepseek-v4-pro', turn_count: 2 },
                  ],
                  recent_state_mutations: [],
                  recent_operator_actions: [],
                  limit: 20,
                },
          incidents: {
            incidents: [],
            summary: {
              failed_turn_count: 1,
              failed_canon_job_count: 1,
              bad_turn_report_count: 1,
              telemetry_incident_count: 1,
            },
            limit: 20,
            session_id: numericSessionId ?? undefined,
          },
          audits: {
            state_mutations: [],
            operator_actions: [],
            summary: {
              state_mutation_count: 0,
              operator_action_count: 0,
            },
            limit: 20,
            session_id: numericSessionId ?? undefined,
          },
          recent_turns: [],
          canon_jobs: [],
          session_log_entries: [],
          turn_events: [],
          telemetry: {},
        })
      }
      if (method === 'GET' && path === '/api/llm/config') return jsonResponse(runtime)
      if ((method === 'PATCH' || method === 'POST') && path === '/api/llm/config') {
        const runtimeBody = body as { provider?: string; model?: string; persist?: boolean }
        return jsonResponse({
          ...runtime,
          current: {
            ...runtime.current,
            provider: runtimeBody.provider,
            model: runtimeBody.model,
            configured: true,
          },
          persisted: runtimeBody.persist !== false,
        })
      }
      if (method === 'GET' && path === '/api/tts/config') {
        return ttsConfigFetchError
          ? jsonResponse({ error: ttsConfigFetchError }, { status: 503 })
          : jsonResponse(ttsConfig)
      }
      if (method === 'POST' && (path === '/api/tts/stream' || path === '/api/tts/speak')) {
        if (ttsFetchHandler) return ttsFetchHandler(path, body)
        return new Response(new Blob(['audio'], { type: 'audio/mpeg' }), {
          status: 200,
          headers: { 'Content-Type': 'audio/mpeg' },
        })
      }
      if (method === 'POST' && path === '/api/feedback/coherence') {
        return jsonResponse(
          {
            feedback_id: 902,
            feedback: {
              feedback_id: 902,
              session_id: body.session_id,
              turn_id: body.turn_id ?? null,
              feedback_type: 'coherence',
              category: body.category ?? 'coherence',
              coherence_score: body.coherence_score,
              notes: body.notes ?? null,
              provider: 'deepseek',
              model: 'deepseek-v4-pro',
              turn_status: 'completed',
              turn_latency_ms: 1800,
              created_at: fixedNow.toISOString(),
            },
          },
          { status: 201 },
        )
      }
      if (method === 'POST' && path === '/api/feedback/bad-turn') {
        return jsonResponse(
          {
            feedback: {
              feedback_id: 901,
              session_id: body.session_id,
              turn_id: body.turn_id,
              feedback_type: 'bad_turn',
              category: body.category ?? 'other',
              coherence_score: 1,
              notes: null,
              provider: 'deepseek',
              model: 'deepseek-v4-pro',
              turn_status: 'completed',
              turn_latency_ms: 1800,
              created_at: fixedNow.toISOString(),
            },
          },
          { status: 201 },
        )
      }

      const recoveryMatch = path.match(/^\/api\/sessions\/(\d+)\/recovery\/resolve$/)
      if (method === 'POST' && recoveryMatch) {
        const sessionId = Number(recoveryMatch[1])
        const currentState = sessionStates[sessionId]
        if (!currentState) {
          return jsonResponse(
            { error: 'Session not found.', error_code: 'session_not_found' },
            { status: 404 },
          )
        }
        const currentSnapshot = currentState.state_snapshot ?? {}
        const nextSnapshot = { ...currentSnapshot }
        delete nextSnapshot.turnRecoveryGate
        sessionStates[sessionId] = {
          ...currentState,
          state_snapshot: nextSnapshot,
          updated_at: fixedNow.toISOString(),
        }
        return jsonResponse({
          resolved: true,
          idempotent_replay: false,
          session_id: sessionId,
          turn_id: body.turn_id,
          resolution: body.resolution,
          state_revision: 4,
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
          weapon_proficiencies: [],
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

      const examplePackImportMatch = path.match(/^\/api\/campaigns\/example-packs\/(.+)\/import$/)
      if (method === 'POST' && examplePackImportMatch) {
        const packId = decodeURIComponent(examplePackImportMatch[1])
        const pack = exampleCampaignPacks.find((item) => item.pack_id === packId)
        if (!pack) {
          return jsonResponse(
            { error: 'Example campaign pack not found.', error_code: 'example_campaign_pack_not_found' },
            { status: 404 },
          )
        }
        let worldId = Number((body as { world_id?: number } | null)?.world_id)
        let selectedWorld = worlds.find((world) => world.world_id === worldId) ?? null
        if (!selectedWorld) {
          worldId = 77
          selectedWorld = {
            world_id: worldId,
            name: pack.world_name ?? `${pack.title} World`,
            description: null,
            created_at: fixedNow.toISOString(),
          }
          worlds = [...worlds, selectedWorld]
        }
        const campaignId = 101
        const sessionId = 201
        const campaign: Campaign = {
          campaign_id: campaignId,
          title: pack.title,
          description: pack.description,
          world_id: worldId,
          world_name: selectedWorld.name,
          created_at: fixedNow.toISOString(),
          updated_at: fixedNow.toISOString(),
          status: 'active',
          is_archived: false,
          current_quest: 'Find the Missing Caravan',
          location: 'Graymere Watch',
          session_count: 1,
          latest_session_id: sessionId,
          latest_activity_at: fixedNow.toISOString(),
        }
        const session: SessionSummary = {
          session_id: sessionId,
          campaign_id: campaignId,
          created_at: fixedNow.toISOString(),
          updated_at: fixedNow.toISOString(),
          latest_activity_at: fixedNow.toISOString(),
          display_name: pack.title,
          status: 'active',
          deleted_at: null,
          turn_count: 0,
          latest_summary: '',
          is_archived: false,
          state_snapshot: { campaignPack: { packId: pack.pack_id, title: pack.title } },
        }
        campaigns = [campaign, ...campaigns]
        sessionsByCampaign[campaignId] = [session]
        playersByCampaign[campaignId] = []
        mapsByCampaign[campaignId] = []
        segmentsByCampaign[campaignId] = []
        sessionLogs[sessionId] = []
        sessionStates[sessionId] = {
          session_id: sessionId,
          campaign_id: campaignId,
          current_location: 'Graymere Watch',
          current_quest: 'The Shard Beneath the Blue Mountains',
          rolling_summary: '',
          active_segments: [],
          memory_snippets: [],
          state_snapshot: { campaignPack: { packId: pack.pack_id, title: pack.title } },
          updated_at: fixedNow.toISOString(),
        }
        return jsonResponse({
          imported: true,
          pack_id: pack.pack_id,
          campaign_id: campaignId,
          session_id: sessionId,
          campaign,
          session,
          counts: {},
        })
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
          visibility: body.visibility ?? 'player',
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
                visibility: body.visibility ?? map.visibility,
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
          external_id: null,
          source: 'manual',
          source_pack_id: null,
          metadata: {},
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
  const call = [...socketMock.socket.on.mock.calls].reverse().find(([event]) => event === eventName)
  if (!call) throw new Error(`Missing socket handler for ${eventName}`)
  return call[1] as (payload: TPayload) => void
}

function AppUnderTest() {
  return <App />
}

export const appTestState = {
  fixedNow,
  health,
  metrics,
  runtime,
  ttsConfig,
  get campaigns() {
    return campaigns
  },
  set campaigns(value: Campaign[]) {
    campaigns = value
  },
  get worlds() {
    return worlds
  },
  set worlds(value: World[]) {
    worlds = value
  },
  get sessionsByCampaign() {
    return sessionsByCampaign
  },
  set sessionsByCampaign(value: Record<number, SessionSummary[]>) {
    sessionsByCampaign = value
  },
  get playersByCampaign() {
    return playersByCampaign
  },
  set playersByCampaign(value: Record<number, Player[]>) {
    playersByCampaign = value
  },
  get mapsByCampaign() {
    return mapsByCampaign
  },
  set mapsByCampaign(value: Record<number, MapItem[]>) {
    mapsByCampaign = value
  },
  get segmentsByCampaign() {
    return segmentsByCampaign
  },
  set segmentsByCampaign(value: Record<number, CampaignSegment[]>) {
    segmentsByCampaign = value
  },
  get sessionLogs() {
    return sessionLogs
  },
  set sessionLogs(value: Record<number, SessionLogEntry[]>) {
    sessionLogs = value
  },
  get sessionStates() {
    return sessionStates
  },
  set sessionStates(value: Record<number, SessionState>) {
    sessionStates = value
  },
  get playerDetails() {
    return playerDetails
  },
  set playerDetails(value: Record<number, PlayerDetail>) {
    playerDetails = value
  },
  get fetchCalls() {
    return fetchCalls
  },
  get ttsFetchHandler() {
    return ttsFetchHandler
  },
  set ttsFetchHandler(value: ((path: string, body: unknown) => Promise<Response>) | null) {
    ttsFetchHandler = value
  },
  get ttsConfigFetchError() {
    return ttsConfigFetchError
  },
  set ttsConfigFetchError(value: string | null) {
    ttsConfigFetchError = value
  },
  get requiredAuthToken() {
    return requiredAuthToken
  },
  set requiredAuthToken(value: string | null) {
    requiredAuthToken = value
  },
}

export function setupAppTest() {
  socketMock.io.mockClear()
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
  for (const cookie of document.cookie.split(';')) {
    const name = cookie.split('=', 1)[0]?.trim()
    if (name) document.cookie = `${name}=; Max-Age=0; Path=/; SameSite=Lax`
  }
  if (health.llm) {
    health.llm.provider = 'deepseek'
    health.llm.model = 'deepseek-v4-pro'
    health.llm.configured = true
    health.llm.latest_turn = null
  }
  health.auth_required = false
  runtime.current = health.llm!
  runtime.persisted = true
  delete runtime.runtime_scope
  delete runtime.restart_required_for_other_workers
  delete runtime.worker_count
  ttsConfig.configured = true
  ttsConfig.model = 'aura-2-draco-en'
  localStorage.setItem('aidm:selectedCampaignId', '10')
  localStorage.setItem('aidm:selectedSessionId', '20')
  localStorage.setItem('aidm:selectedPlayerId', '30')
  installFetchMock()
  Object.defineProperty(HTMLElement.prototype, 'requestFullscreen', {
    configurable: true,
    value: vi.fn().mockRejectedValue(new Error('blocked')),
  })
}

export function teardownAppTest() {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
}

export {
  AppUnderTest as App,
  contrastRatio,
  fixedNow,
  installLegacyMatchMediaMock,
  installMatchMediaMock,
  jsonResponse,
  lightThemeColors,
  lightThemeContrastBackgrounds,
  lightThemeContrastForegrounds,
  renderLoadedApp,
  socketHandler,
  socketMock,
  toggleAdminToolsViaComposerLabel,
}
