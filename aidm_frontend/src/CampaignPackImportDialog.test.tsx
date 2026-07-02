// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CampaignPackImportDialog } from './CampaignPackImportDialog'

const apiFetchMock = vi.hoisted(() => vi.fn())

vi.mock('./api', () => ({
  ApiClientError: class ApiClientError extends Error {},
  apiFetch: apiFetchMock,
}))

describe('CampaignPackImportDialog', () => {
  afterEach(() => {
    cleanup()
    apiFetchMock.mockReset()
  })

  it('surfaces the campaign pack authoring report from lint responses', async () => {
    apiFetchMock.mockResolvedValue({
      ok: true,
      issues: [],
      preview: {
        imported: false,
        pack_id: 'author_pack',
        schema_version: '1',
        pack_version: '1.0.0',
        counts: {
          locations: 1,
          npcs: 1,
          quests: 1,
          segments: 0,
          checkpoints: 3,
          encounters: 2,
          enemies: 1,
          bestiary_entries: 0,
        },
        preview: {
          title: 'Author Pack',
          world: { mode: 'new', name: 'Author World' },
          starting_location_id: 'loc_start',
          starting_quest_id: 'quest_start',
          visible_at_start: {
            locations: ['loc_start'],
            npcs: ['npc_guide'],
            quests: ['quest_start'],
          },
        },
      },
      graph: {
        nodes: ['cp_start', 'cp_end', 'cp_orphan'],
        edges: [{ from: 'cp_start', to: 'cp_end', type: 'next' }],
        reachable: ['cp_start', 'cp_end'],
      },
      authoring_report: {
        starting: {
          locationId: 'loc_start',
          questId: 'quest_start',
          checkpointId: 'cp_start',
        },
        collections: [
          {
            collection: 'locations',
            count: 1,
            visibleAtStartCount: 1,
            hiddenToPlayersCount: 0,
            visibleAtStartIds: ['loc_start'],
            hiddenToPlayersIds: [],
          },
          {
            collection: 'lore',
            count: 1,
            visibleAtStartCount: 0,
            hiddenToPlayersCount: 1,
            visibleAtStartIds: [],
            hiddenToPlayersIds: ['lore_secret'],
          },
        ],
        checkpoints: {
          total: 3,
          reachable: 2,
          unreachableIds: ['cp_orphan'],
          optionalIds: ['cp_orphan'],
          terminalIds: ['cp_end'],
          items: [],
        },
        encounters: {
          total: 2,
          linkedToCheckpoint: 1,
          unlinkedIds: ['enc_loose'],
          items: [],
        },
      },
    })

    render(
      <CampaignPackImportDialog
        auth="token"
        baseUrl="http://127.0.0.1:5050"
        onClose={vi.fn()}
        onImported={vi.fn()}
        pushError={vi.fn()}
      />,
    )

    fireEvent.change(screen.getByLabelText('JSON Preview'), {
      target: { value: JSON.stringify({ packId: 'author_pack' }) },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Check Pack' }))

    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        'http://127.0.0.1:5050',
        '/api/campaigns/pack-tools/lint',
        'token',
        expect.objectContaining({ method: 'POST' }),
      ),
    )

    const report = await screen.findByLabelText('Campaign pack authoring report')
    expect(within(report).getByText('2 / 3 reachable')).toBeInTheDocument()
    expect(within(report).getByText('1 / 2 linked')).toBeInTheDocument()
    expect(within(report).getByText('locations: 1 / 1 visible')).toBeInTheDocument()
    expect(within(report).getByText('lore: 1 / 1 hidden')).toBeInTheDocument()
    expect(within(report).getByText('Unreachable checkpoints: cp_orphan')).toBeInTheDocument()
  })

  it('forges a campaign pack and populates the import preview', async () => {
    apiFetchMock.mockResolvedValue({
      ok: true,
      sourceFilename: 'forge_lanterns.json',
      pack: {
        packId: 'forge_lanterns',
        title: 'Lanterns Under Blackwater',
      },
      payload: {
        sourceFilename: 'forge_lanterns.json',
        pack: {
          packId: 'forge_lanterns',
          title: 'Lanterns Under Blackwater',
        },
      },
      manifestText: JSON.stringify(
        {
          sourceFilename: 'forge_lanterns.json',
          pack: {
            packId: 'forge_lanterns',
            title: 'Lanterns Under Blackwater',
          },
        },
        null,
        2,
      ),
      lint: {
        ok: true,
        issues: [],
        preview: {
          imported: false,
          pack_id: 'forge_lanterns',
          schema_version: '1',
          pack_version: '1.0.0',
          counts: {
            locations: 3,
            npcs: 2,
            quests: 1,
            segments: 1,
            checkpoints: 4,
            encounters: 2,
            enemies: 2,
            bestiary_entries: 2,
          },
          preview: {
            title: 'Lanterns Under Blackwater',
            world: { mode: 'new', name: 'Lanterns Under Blackwater Setting' },
            starting_location: 'The Breakwater Gate',
            starting_quest: 'Uncover Lanterns',
            visible_at_start: {
              locations: ['start'],
              npcs: ['guide'],
              quests: ['quest'],
            },
          },
        },
        graph: {
          nodes: ['cp_1', 'cp_2', 'cp_3', 'cp_4'],
          edges: [],
          reachable: ['cp_1', 'cp_2', 'cp_3', 'cp_4'],
        },
        authoring_report: {
          checkpoints: {
            total: 4,
            reachable: 4,
            optionalIds: ['cp_3'],
            terminalIds: ['cp_4'],
            items: [],
          },
          encounters: {
            total: 2,
            linkedToCheckpoint: 2,
            unlinkedIds: [],
            items: [],
          },
          collections: [],
        },
      },
    })

    render(
      <CampaignPackImportDialog
        auth="token"
        baseUrl="http://127.0.0.1:5050"
        onClose={vi.fn()}
        onImported={vi.fn()}
        pushError={vi.fn()}
      />,
    )

    fireEvent.change(screen.getByLabelText('Pack Title'), {
      target: { value: 'Lanterns Under Blackwater' },
    })
    fireEvent.change(screen.getByLabelText('Premise'), {
      target: { value: 'Harbor intrigue and a drowned archive.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Forge Pack' }))

    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        'http://127.0.0.1:5050',
        '/api/campaigns/pack-tools/forge',
        'token',
        expect.objectContaining({
          method: 'POST',
          body: JSON.stringify({
            title: 'Lanterns Under Blackwater',
            prompt: 'Harbor intrigue and a drowned archive.',
          }),
        }),
      ),
    )
    expect((screen.getByLabelText('JSON Preview') as HTMLTextAreaElement).value).toContain('"packId": "forge_lanterns"')
    expect(screen.getByText('Lanterns Under Blackwater')).toBeInTheDocument()
    expect(screen.getByText('4 / 4 reachable')).toBeInTheDocument()
  })
})
