import { describe, expect, it } from 'vitest'
import type { Campaign, CampaignWorkspace, Player, SessionListResponse, SessionSummary } from './types'
import {
  cacheCampaignWorkspace,
  cacheOlderSessions,
  cacheRootCampaigns,
  createWorkspaceCache,
  removeCampaign,
  removeSession,
  selectCampaign,
  selectCampaigns,
  selectMaps,
  selectPlayers,
  selectSegments,
  selectSessions,
  selectSessionsCursor,
  upsertCampaign,
  upsertPlayer,
  upsertSession,
} from './workspaceCache'

function campaign(id: number, title = `Campaign ${id}`): Campaign {
  return {
    campaign_id: id,
    title,
    description: '',
    world_id: id * 10,
    world_name: `World ${id * 10}`,
    created_at: '2026-06-06T10:00:00.000Z',
    updated_at: '2026-06-06T10:00:00.000Z',
    status: 'active',
    is_archived: false,
    current_quest: null,
    location: null,
    session_count: 0,
    latest_session_id: null,
    latest_activity_at: null,
  }
}

function session(id: number, campaignId: number): SessionSummary {
  return {
    session_id: id,
    campaign_id: campaignId,
    name: null,
    display_name: `Session ${id}`,
    status: 'active',
    is_current: false,
    is_archived: false,
    chapter_number: 1,
    chapter_recap: '',
    ended_at: null,
    created_at: '2026-06-06T10:00:00.000Z',
    updated_at: '2026-06-06T10:00:00.000Z',
    latest_activity_at: '2026-06-06T10:00:00.000Z',
    turn_count: 0,
    scene_title: null,
    summary_excerpt: '',
    imported: false,
    play_now: false,
  }
}

function player(id: number, campaignId: number): Player {
  return {
    player_id: id,
    workspace_id: 'owner',
    account_id: null,
    username: null,
    campaign_id: campaignId,
    name: `Player ${id}`,
    character_name: `Hero ${id}`,
    race: '',
    sex: '',
    profile_image: '/profile-icons/human_male.png',
    class_: '',
    char_class: '',
    level: 1,
    created_at: '2026-06-06T10:00:00.000Z',
    updated_at: '2026-06-06T10:00:00.000Z',
  }
}

function workspace(campaignId: number): CampaignWorkspace {
  return {
    campaign: campaign(campaignId, 'Smoke Campaign'),
    sessions: [session(20, campaignId), session(21, campaignId)],
    players: [player(30, campaignId)],
    maps: [
      {
        map_id: 40,
        world_id: campaignId * 10,
        campaign_id: campaignId,
        title: 'Ash Hall',
        description: '',
        map_data: {},
        visibility: 'player',
        created_at: '2026-06-06T10:00:00.000Z',
        updated_at: '2026-06-06T10:00:00.000Z',
      },
    ],
    segments: [
      {
        segment_id: 50,
        campaign_id: campaignId,
        title: 'Opening',
        description: '',
        trigger_condition: '',
        tags: '',
        external_id: null,
        source: 'manual',
        source_pack_id: null,
        metadata: {},
        is_triggered: true,
        created_at: '2026-06-06T10:00:00.000Z',
        updated_at: '2026-06-06T10:00:00.000Z',
      },
    ],
    summary: {
      session_count: 2,
      player_count: 1,
      map_count: 1,
      segment_count: 1,
      latest_session_id: 20,
      latest_activity_at: '2026-06-06T10:00:00.000Z',
    },
    has_more: { sessions: false, players: false, maps: false, segments: false },
    next_cursor: { sessions: null, players: null, maps: null, segments: null },
    limits: { sessions: 30, players: null, maps: null, segments: null },
  }
}

function pagedWorkspace(campaignId: number, sessionIds: number[], cursor: string | null): CampaignWorkspace {
  const base = workspace(campaignId)
  return {
    ...base,
    sessions: sessionIds.map((id) => session(id, campaignId)),
    has_more: { ...base.has_more, sessions: cursor !== null },
    next_cursor: { ...base.next_cursor, sessions: cursor },
  }
}

function olderPage(campaignId: number, sessionIds: number[], cursor: string | null): SessionListResponse {
  return {
    campaign_id: campaignId,
    sessions: sessionIds.map((id) => session(id, campaignId)),
    limit: 30,
    has_more: cursor !== null,
    next_cursor: cursor,
  }
}

describe('workspaceCache', () => {
  it('normalizes root campaign and campaign workspace payloads', () => {
    const cache = cacheCampaignWorkspace(
      cacheRootCampaigns(createWorkspaceCache(), [campaign(10), campaign(11)]),
      workspace(10),
    )

    expect(selectCampaigns(cache).map((item) => item.campaign_id)).toEqual([10, 11])
    expect(selectCampaign(cache, 10)?.title).toBe('Smoke Campaign')
    expect(selectSessions(cache, 10).map((item) => item.session_id)).toEqual([20, 21])
    expect(selectPlayers(cache, 10).map((item) => item.player_id)).toEqual([30])
    expect(selectMaps(cache, 10).map((item) => item.map_id)).toEqual([40])
    expect(selectSegments(cache, 10).map((item) => item.segment_id)).toEqual([50])
  })

  it('updates individual entities without rebuilding unrelated campaign slices', () => {
    let cache = cacheCampaignWorkspace(createWorkspaceCache(), workspace(10))
    cache = upsertCampaign(cache, { ...campaign(10), title: 'Renamed' })
    cache = upsertSession(cache, { ...session(21, 10), display_name: 'Renamed Session' })
    cache = upsertPlayer(cache, { ...player(30, 10), character_name: 'Ember' })

    expect(selectCampaign(cache, 10)?.title).toBe('Renamed')
    expect(selectSessions(cache, 10).find((item) => item.session_id === 21)?.display_name).toBe('Renamed Session')
    expect(selectPlayers(cache, 10)[0]?.character_name).toBe('Ember')
  })

  it('removes a campaign and its scoped workspace entities', () => {
    const cache = removeCampaign(
      cacheCampaignWorkspace(cacheCampaignWorkspace(createWorkspaceCache(), workspace(10)), workspace(11)),
      10,
    )

    expect(selectCampaign(cache, 10)).toBeNull()
    expect(selectSessions(cache, 10)).toEqual([])
    expect(selectPlayers(cache, 10)).toEqual([])
    expect(selectMaps(cache, 10)).toEqual([])
    expect(selectSegments(cache, 10)).toEqual([])
    expect(selectCampaign(cache, 11)?.campaign_id).toBe(11)
  })

  it('appends older session pages after the first page and tracks their cursor', () => {
    let cache = cacheCampaignWorkspace(createWorkspaceCache(), pagedWorkspace(10, [25, 24], 'page-2'))
    expect(selectSessionsCursor(cache, 10)).toBe('page-2')

    cache = cacheOlderSessions(cache, olderPage(10, [24, 23, 22], 'page-3'))
    expect(selectSessions(cache, 10).map((item) => item.session_id)).toEqual([25, 24, 23, 22])
    expect(selectSessionsCursor(cache, 10)).toBe('page-3')

    cache = cacheOlderSessions(cache, olderPage(10, [21], null))
    expect(selectSessions(cache, 10).map((item) => item.session_id)).toEqual([25, 24, 23, 22, 21])
    expect(selectSessionsCursor(cache, 10)).toBeNull()
  })

  it('keeps loaded older pages through a workspace refresh while more pages remain', () => {
    let cache = cacheCampaignWorkspace(createWorkspaceCache(), pagedWorkspace(10, [25, 24], 'page-2'))
    cache = cacheOlderSessions(cache, olderPage(10, [23, 22], 'page-3'))

    const refreshed = cacheCampaignWorkspace(cache, pagedWorkspace(10, [26, 25], 'fresh-page-2'))
    expect(selectSessions(refreshed, 10).map((item) => item.session_id)).toEqual([26, 25, 24, 23, 22])
    expect(selectSessionsCursor(refreshed, 10)).toBe('page-3')

    const complete = cacheCampaignWorkspace(cache, pagedWorkspace(10, [25, 24], null))
    expect(selectSessions(complete, 10).map((item) => item.session_id)).toEqual([25, 24])
    expect(selectSessionsCursor(complete, 10)).toBeNull()
  })

  it('removes a deleted session from its campaign list', () => {
    let cache = cacheCampaignWorkspace(createWorkspaceCache(), pagedWorkspace(10, [25, 24], 'page-2'))
    cache = cacheOlderSessions(cache, olderPage(10, [23], 'page-3'))
    cache = removeSession(cache, 23)

    expect(selectSessions(cache, 10).map((item) => item.session_id)).toEqual([25, 24])
    const refreshed = cacheCampaignWorkspace(cache, pagedWorkspace(10, [25, 24], 'page-2'))
    expect(selectSessions(refreshed, 10).map((item) => item.session_id)).toEqual([25, 24])
  })

  it('prunes cached campaigns that are absent from a root campaign refresh', () => {
    const cache = cacheRootCampaigns(cacheCampaignWorkspace(createWorkspaceCache(), workspace(10)), [])

    expect(selectCampaigns(cache)).toEqual([])
    expect(selectCampaign(cache, 10)).toBeNull()
    expect(selectSessions(cache, 10)).toEqual([])
    expect(selectPlayers(cache, 10)).toEqual([])
    expect(selectMaps(cache, 10)).toEqual([])
    expect(selectSegments(cache, 10)).toEqual([])
  })
})
