import { describe, expect, it } from 'vitest'
import type { Campaign, CampaignWorkspace, Player, SessionSummary } from './types'
import {
  cacheCampaignWorkspace,
  cacheRootCampaigns,
  createWorkspaceCache,
  removeCampaign,
  selectCampaign,
  selectCampaigns,
  selectMaps,
  selectPlayers,
  selectSegments,
  selectSessions,
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
    created_at: '2026-06-06T10:00:00.000Z',
    updated_at: '2026-06-06T10:00:00.000Z',
    latest_activity_at: '2026-06-06T10:00:00.000Z',
    display_name: `Session ${id}`,
    status: 'active',
    deleted_at: null,
    turn_count: 0,
    latest_summary: '',
    is_archived: false,
    state_snapshot: {},
  }
}

function player(id: number, campaignId: number): Player {
  return {
    player_id: id,
    workspace_id: 'owner',
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
    limits: { sessions: null, players: null, maps: null, segments: null },
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
