// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DEFAULT_CONTENT_SETTINGS } from './contentSettings'
import { DirectorCommentaryPanel, OperatorDrawer } from './SessionDirectorPanels'
import type { CampaignPackCommentaryResponse } from './types'

const commentary: CampaignPackCommentaryResponse = {
  enabled: true,
  sessionId: 20,
  campaignId: 10,
  pack: {
    packId: 'branching_pack',
    title: 'The Branching Pack',
    version: '1.0.0',
    schemaVersion: '1',
  },
  progress: {
    activeCheckpointId: 'cp_watchtower',
    completedCheckpointIds: ['cp_gate'],
    skippedCheckpointIds: [],
    failedCheckpointIds: [],
    statusByCheckpointId: { cp_gate: 'completed', cp_watchtower: 'active' },
    progressRevision: 2,
  },
  graph: {
    startCheckpointId: 'cp_gate',
    nodes: [
      { id: 'cp_gate', title: 'Rain Gate', terminal: false, sortOrder: 0 },
      { id: 'cp_watchtower', title: 'Abandoned Watchtower', terminal: false, sortOrder: 1 },
    ],
    nodeIds: ['cp_gate', 'cp_watchtower'],
    edges: [{ from: 'cp_gate', to: 'cp_watchtower', type: 'alternate' }],
    reachable: ['cp_gate', 'cp_watchtower'],
  },
  routeTaken: [
    {
      id: 'cp_gate',
      checkpointId: 'cp_gate',
      title: 'Rain Gate',
      summary: '',
      status: 'completed',
      reason: null,
    },
  ],
  roadsNotTaken: [
    {
      id: 'cp_watchtower',
      checkpointId: 'cp_watchtower',
      title: 'Abandoned Watchtower',
      summary: '',
      edgeType: 'alternate_path',
      fromCheckpointId: 'cp_gate',
      fromTitle: 'Rain Gate',
    },
  ],
  alternateEndings: [],
  undiscoveredRecords: {
    hidden_locations: [
      {
        id: 'loc_watchtower',
        title: 'Watchtower Vault',
        summary: '',
        hidden: true,
        checkpointIds: ['cp_watchtower'],
      },
    ],
  },
  summary: {
    routeTakenCount: 1,
    roadsNotTakenCount: 1,
    alternateEndingsCount: 0,
    undiscoveredRecordsCount: 1,
  },
  commentary: ['One alternate road remains unexplored.'],
}

afterEach(() => cleanup())

describe('DirectorCommentaryPanel', () => {
  it('renders campaign-pack, scene, response, and memory context and closes on request', () => {
    const onClose = vi.fn()
    render(
      <DirectorCommentaryPanel
        activeSessionTitle="Session Alpha"
        recentMemory={[
          ['The gate remembers Ember.', 'Turn 1'],
          ['The keeper left a warning.', 'Turn 2'],
        ]}
        commentary={commentary}
        contentSettings={{ ...DEFAULT_CONTENT_SETTINGS, contentRating: 'mature' }}
        currentResponseEntry={{
          id: 'dm-2',
          role: 'dm',
          speaker: 'DM',
          text: 'The watchtower answers.',
          timestamp: '2026-06-06T10:41:00.000Z',
          streaming: true,
          metadata: {},
        }}
        dmExecutionStats={{ tokens: 72, time: '1.2s', model: 'test-model', temperature: '0.7' }}
        latestDmText="The sealed door vibrates while old glyphs wake across its frame."
        onClose={onClose}
        questTitle="Open the sealed door"
        sceneState={{
          sessionId: 20,
          locationId: 'ash-hall',
          locationName: 'Ash Hall',
          sceneType: 'exploration',
          mood: 'mystery',
          dangerLevel: 4,
          combatState: 'none',
          inCombat: false,
          musicTag: 'mystery',
          actingPlayerId: 30,
        }}
        sessionState={null}
        streamLabel="Streaming..."
      />,
    )

    const panel = screen.getByRole('region', { name: 'Director Commentary' })
    expect(within(panel).getByText('The Branching Pack')).toBeInTheDocument()
    expect(within(panel).getByText('Ash Hall')).toBeInTheDocument()
    expect(within(panel).getByText('exploration / mystery / danger 4')).toBeInTheDocument()
    expect(within(panel).getByText('Alternate Path from Rain Gate')).toBeInTheDocument()
    expect(within(panel).getByText('Hidden Locations')).toBeInTheDocument()
    expect(within(panel).getByText('The gate remembers Ember.')).toBeInTheDocument()
    expect(within(panel).getByText('Streaming')).toBeInTheDocument()

    fireEvent.click(within(panel).getByRole('button', { name: 'Close Director Commentary' }))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('shows useful empty states without campaign-pack commentary', () => {
    render(
      <DirectorCommentaryPanel
        activeSessionTitle="Session Alpha"
        recentMemory={[]}
        commentary={null}
        contentSettings={DEFAULT_CONTENT_SETTINGS}
        currentResponseEntry={null}
        dmExecutionStats={{ tokens: 0, time: '0s', model: 'test-model', temperature: '0.7' }}
        latestDmText=""
        onClose={vi.fn()}
        questTitle="Open the sealed door"
        sceneState={null}
        sessionState={null}
        streamLabel="Ready"
      />,
    )

    const panel = screen.getByRole('region', { name: 'Director Commentary' })
    expect(within(panel).queryByText('Pack')).not.toBeInTheDocument()
    expect(within(panel).getByText('Scene unset')).toBeInTheDocument()
    expect(within(panel).getByText('No DM prose recorded yet.')).toBeInTheDocument()
    expect(within(panel).getByText('No memory snippets recorded yet.')).toBeInTheDocument()
  })
})

describe('OperatorDrawer', () => {
  it('routes rating and bounded tone-tag changes through typed callbacks', () => {
    const onContentRatingChange = vi.fn()
    const onContentToneTagsChange = vi.fn()
    render(
      <OperatorDrawer
        canEditContentSettings
        contentSettings={{ ...DEFAULT_CONTENT_SETTINGS, toneTags: ['noir'] }}
        contentSettingsPending={false}
        dmExecutionStats={{ tokens: 72, time: '1.2s', model: 'test-model', temperature: '0.7' }}
        onContentRatingChange={onContentRatingChange}
        onContentToneTagsChange={onContentToneTagsChange}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Mature' }))
    fireEvent.click(screen.getByRole('button', { name: 'Noir' }))

    expect(onContentRatingChange).toHaveBeenCalledWith('mature')
    expect(onContentToneTagsChange).toHaveBeenCalledWith([])
    expect(screen.queryByRole('group', { name: 'Board view mode' })).not.toBeInTheDocument()
  })

  it('disables content policy controls while an update is pending', () => {
    render(
      <OperatorDrawer
        canEditContentSettings
        contentSettings={DEFAULT_CONTENT_SETTINGS}
        contentSettingsPending
        dmExecutionStats={{ tokens: 0, time: '0s', model: 'test-model', temperature: '0.7' }}
        onContentRatingChange={vi.fn()}
        onContentToneTagsChange={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'Mature' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Noir' })).toBeDisabled()
  })
})
