// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BestiaryDebugPanel } from './BestiaryDebugPanel'

const apiFetchMock = vi.hoisted(() => vi.fn())

vi.mock('./api', () => ({
  ApiClientError: class ApiClientError extends Error {},
  apiFetch: apiFetchMock,
}))

const wolf = {
  id: 'wolf',
  version: 1,
  name: 'Wolf',
  source: 'core_bestiary',
  descriptionShort: 'A reliable predator.',
  descriptionLong: 'A reliable predator.',
  creatureType: 'beast',
  visualTags: ['beast', 'predator'],
  level: 1,
  challengeTier: 'easy',
  size: 'medium',
  stats: { maxHp: 11, armorClass: 13 },
  movement: { walk: 40 },
  senses: { passivePerception: 13 },
  abilities: [{ id: 'wolf_bite', name: 'Bite', type: 'attack' }],
  behavior: { combatRole: 'beast', morale: 50 },
  aiNarrationHints: [],
  balance: {
    estimatedTier: 'easy',
    targetTier: 'easy',
    estimatedDamagePerRound: 5,
    estimatedDurability: 11,
    estimatedControlStrength: 1,
    warnings: [],
    reviewed: true,
  },
}

const ashenGoblin = {
  ...wolf,
  id: 'ashen_goblin',
  name: 'Ashen Goblin',
  source: 'campaign_pack',
  creatureType: 'humanoid',
  visualTags: ['ash', 'goblin'],
  behavior: { combatRole: 'skirmisher', morale: 45 },
}

describe('BestiaryDebugPanel', () => {
  afterEach(() => {
    cleanup()
    apiFetchMock.mockReset()
  })

  it('loads compact bestiary rows, filters creatures, and seeds campaign packs', async () => {
    apiFetchMock.mockImplementation((_baseUrl: string, path: string) => {
      if (path === '/api/bestiary/core') {
        return Promise.resolve({ entries: [wolf] })
      }
      if (path === '/api/campaigns/7/bestiary') {
        return Promise.resolve({
          campaign_id: 7,
          entries: [
            {
              bestiary_entry_id: 1,
              workspace_id: 'owner',
              campaign_id: 7,
              session_id: null,
              scope: 'campaign',
              creature_id: 'ashen_goblin',
              version: 1,
              name: 'Ashen Goblin',
              source: 'campaign_pack',
              persistence: 'campaign',
              region_id: null,
              location_ids: [],
              faction_ids: [],
              tags: ['ash', 'goblin'],
              creature: ashenGoblin,
              balance: ashenGoblin.balance,
              created_because: null,
              base_creature_id: null,
              variant_reason: null,
              created_at_turn: null,
              created_by_model: null,
              created_at: null,
              updated_at: null,
            },
          ],
        })
      }
      if (path === '/api/sessions/9/combat/debug?limit=12') {
        return Promise.resolve({
          events: [
            {
              debug_event_id: 4,
              event_type: 'pre_dm_combat_plan',
              payload: {
                resolver: { resolutionMethod: 'campaign_bestiary_match' },
                intentPlan: { summaryForDm: 'Goblin retreats.' },
              },
            },
          ],
        })
      }
      if (path === '/api/campaigns/7/bestiary/generate-pack') {
        return Promise.resolve({ campaign_id: 7, creatures: [ashenGoblin], entries: [] })
      }
      return Promise.reject(new Error(`Unexpected path ${path}`))
    })

    render(<BestiaryDebugPanel baseUrl="" auth="" selectedCampaignId={7} selectedSessionId={9} />)

    const panel = screen.getByLabelText('Bestiary debug panel')
    const list = screen.getByLabelText('Bestiary creatures')
    expect(await within(list).findByText('Ashen Goblin')).toBeInTheDocument()
    expect(within(list).getByText('Wolf')).toBeInTheDocument()
    expect(within(panel).getByText('campaign pack / easy')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Search bestiary'), { target: { value: 'wolf' } })
    expect(within(list).queryByText('Ashen Goblin')).not.toBeInTheDocument()
    expect(within(list).getByText('Wolf')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Search bestiary'), { target: { value: '' } })
    fireEvent.click(screen.getByRole('button', { name: /wolf/i }))
    expect(within(panel).getByText('beast / beast / easy')).toBeInTheDocument()

    expect(screen.getByText(/Combat debug/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Campaign pack themes'), { target: { value: 'ash, crown' } })
    fireEvent.click(screen.getByRole('button', { name: /seed/i }))

    await waitFor(() => {
      expect(apiFetchMock).toHaveBeenCalledWith(
        '',
        '/api/campaigns/7/bestiary/generate-pack',
        '',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({ themes: ['ash', 'crown'], count: 6 }),
        }),
      )
    })
  })
})
