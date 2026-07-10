// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ActionComposerProps } from './ActionComposer'
import { DEFAULT_CONTENT_SETTINGS } from './contentSettings'
import { SessionBoard } from './SessionBoard'

type SessionBoardProps = ComponentProps<typeof SessionBoard>

const longDmText =
  'The sealed door vibrates as old glyphs wake one by one across the frame, each symbol answering Ember with a thin blue pulse. The first hinge groans, the second hinge clicks, and the stone remembers the handprint of a forgotten keeper. Hidden tail for theater prose.'

function actionComposerProps(): ActionComposerProps {
  return {
    actionInputRef: createRef<HTMLTextAreaElement>(),
    actionText: '',
    adminPasscode: '',
    adminToolsUnlocked: false,
    setActionText: vi.fn(),
    updateActionText: vi.fn(),
    setAdminPasscode: vi.fn(),
    selectedCharacterName: 'Ember',
    selectedPlayerId: 30,
    activePlayers: [],
    composerMode: 'action',
    selectedDie: 'd20',
    sendPending: false,
    turnControl: {
      mode: 'free',
      source: 'auto',
      activePlayerId: null,
      activePlayerName: null,
    },
    turnControlStatusLabel: 'Free play',
    selectedPlayerHasTurn: true,
    queuedActionText: '',
    clearQueuedAction: vi.fn(),
    updateTurnControl: vi.fn(),
    ttsEnabled: false,
    ttsStatusClassName: 'idle',
    ttsStatusLabel: 'Off',
    ttsLatencyLabel: '',
    canStopTts: false,
    stopTtsAudio: vi.fn(),
    submitAction: vi.fn(),
    toggleAdminTools: vi.fn(),
    startDiceRoll: vi.fn(),
    preloadDiceRollDialog: vi.fn(),
    applyComposerMode: vi.fn(),
    updateSelectedDie: vi.fn(),
    rollMode: 'normal',
    setRollMode: vi.fn(),
    rollModifier: '0',
    setRollModifier: vi.fn(),
    rollProficiencyApplied: false,
    rollProficiencyBonus: 2,
    setRollProficiencyApplied: vi.fn(),
    rollReason: '',
    setRollReason: vi.fn(),
    pendingRollOptions: [],
    rollTargetPendingTurnId: '',
    setRollTargetPendingTurnId: vi.fn(),
    spellName: '',
    selectedAbility: null,
    selectedAbilityKey: '',
    abilityOptions: [],
    updateRollAbilityKey: vi.fn(),
    updateSpellName: vi.fn(),
    interactionTargets: [],
    selectedInteractionTarget: null,
    selectedInteractionTargetId: '',
    selectedInteractionType: 'speak_to',
    setSelectedInteractionTargetId: vi.fn(),
    setSelectedInteractionType: vi.fn(),
    selectedInventoryAction: 'use',
    selectedItem: null,
    itemDraftName: '',
    itemQuantity: '1',
    itemCostGold: '',
    itemOptions: [],
    setSelectedItemName: vi.fn(),
    setItemQuantity: vi.fn(),
    updateSelectedInventoryAction: vi.fn(),
    updateItemDraftName: vi.fn(),
    updateItemCostGold: vi.fn(),
  }
}

function sessionBoardProps(overrides: Partial<SessionBoardProps> = {}): SessionBoardProps {
  return {
    activeSessionTitle: 'Session Alpha',
    campaignTitle: 'Smoke Campaign',
    sessionId: 20,
    playerId: 30,
    showSceneMusicPlayer: false,
    duckMusicForNarration: false,
    sceneMusicSyncState: null,
    sceneState: {
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
    },
    onSceneMusicControl: vi.fn(),
    contentSettings: DEFAULT_CONTENT_SETTINGS,
    contentSettingsPending: false,
    canEditContentSettings: true,
    onContentRatingChange: vi.fn(),
    onContentToneTagsChange: vi.fn(),
    onBoardViewModeChange: vi.fn(),
    directorCommentary: {
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
          edgeType: 'alternate',
          fromCheckpointId: 'cp_gate',
          fromTitle: 'Rain Gate',
        },
      ],
      alternateEndings: [],
      undiscoveredRecords: {
        locations: [
          {
            id: 'loc_watchtower',
            title: 'Abandoned Watchtower',
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
      commentary: ['Roads not taken: 1 branch remains off the table.'],
    },
    sessionRecap: 'The party is testing a sealed door.',
    onSpeakSessionRecap: vi.fn(),
    workspaceLoading: false,
    sessionLoading: false,
    mainTab: 'turns',
    setMainTab: vi.fn(),
    showMobilePresenceStrip: false,
    activePlayers: [],
    downloadCampaignChronicle: vi.fn(async () => undefined),
    downloadSessionChronicle: vi.fn(async () => undefined),
    downloadSessionJson: vi.fn(async () => undefined),
    sessionImportPending: false,
    sessionImportInputRef: createRef<HTMLInputElement>(),
    importSessionJson: vi.fn(async () => undefined),
    shareSession: vi.fn(),
    sessionMenuRef: createRef<HTMLDivElement>(),
    sessionMenuOpen: false,
    setSessionMenuOpen: vi.fn(),
    refreshCurrentWorkspace: vi.fn(async () => undefined),
    activeSession: {
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
    },
    openRenameSessionDialog: vi.fn(),
    openDeleteSessionDialog: vi.fn(),
    notesCount: 2,
    turnFeedRef: createRef<HTMLElement>(),
    updateJumpToLatestVisibility: vi.fn(),
    sessionLogHasMore: false,
    olderLogLoading: false,
    loadOlderSessionLog: vi.fn(async () => undefined),
    turnRows: [
      {
        id: 'dm-1',
        role: 'dm',
        speaker: 'DM',
        text: longDmText,
        timestamp: '2026-06-06T10:41:00.000Z',
        metadata: { turn_id: 1, persistence_status: 'saved' },
      },
    ],
    dismissTimelineEntry: vi.fn(),
    reportedBadTurnIds: new Set(),
    reportingBadTurnIds: new Set(),
    reportBadTurn: vi.fn(),
    ratedTurnQualityIds: new Set(),
    ratingTurnQualityIds: new Set(),
    submitTurnQuality: vi.fn(),
    expandedTurnIds: new Set(),
    setExpandedTurnIds: vi.fn(),
    selectedPlayer: null,
    currentResponseEntry: null,
    latestDmText: longDmText,
    sendPending: false,
    streamingTurnActive: false,
    pendingRollNotice: null,
    dmExecutionStats: {
      tokens: 72,
      time: '1.2s',
      model: 'test-model',
      temperature: '0.7',
    },
    welcomeText: 'Welcome to Session Alpha.',
    showJumpToLatest: false,
    scrollTurnFeedToLatest: vi.fn(),
    questTitle: 'Open the sealed door',
    sessionState: {
      session_id: 20,
      campaign_id: 10,
      current_location: 'Ash Hall',
      current_quest: 'Open the sealed door',
      rolling_summary: 'The party is testing a sealed door.',
      active_segments: [],
      memory_snippets: [],
      state_snapshot: {},
      updated_at: '2026-06-06T10:45:00.000Z',
    },
    campaign: {
      campaign_id: 10,
      title: 'Smoke Campaign',
      description: null,
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
    },
    canonFacts: [
      ['The first canon fact glows in the margin.', 'Turn 1'],
      ['The second canon fact names the keeper.', 'Turn 2'],
    ],
    clarificationRequest: null,
    resolveClarification: vi.fn(),
    onStartAdventure: vi.fn(),
    actionComposerProps: actionComposerProps(),
    ...overrides,
  }
}

beforeEach(() => {
  localStorage.clear()
})

afterEach(() => {
  cleanup()
})

describe('SessionBoard visible theater surfaces', () => {
  it('shows full DM prose, a recap, Director Commentary, and Chronicle export in theater mode', () => {
    localStorage.setItem('aidm:boardViewMode', 'theater')
    const downloadSessionChronicle = vi.fn(async () => undefined)
    render(<SessionBoard {...sessionBoardProps({ downloadSessionChronicle })} />)

    expect(screen.getByText(/Hidden tail for theater prose/i)).toBeInTheDocument()
    expect(screen.getByLabelText('Previously On')).toHaveTextContent('The party is testing a sealed door.')

    fireEvent.click(screen.getByRole('button', { name: 'Director Commentary' }))
    expect(screen.getByRole('heading', { name: 'Director Commentary' })).toBeInTheDocument()
    const directorPanel = screen.getByRole('region', { name: 'Director Commentary' })
    expect(within(directorPanel).getByText('Ash Hall')).toBeInTheDocument()
    expect(within(directorPanel).getByText('The Branching Pack')).toBeInTheDocument()
    expect(within(directorPanel).getAllByText('Abandoned Watchtower').length).toBeGreaterThan(0)
    expect(within(directorPanel).getByText('The first canon fact glows in the margin.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Download session Chronicle' }))
    expect(downloadSessionChronicle).toHaveBeenCalledTimes(1)
  })

  it('notifies the shell mode owner when the board switches into theater', async () => {
    localStorage.setItem('aidm:boardViewMode', 'ops')
    const onBoardViewModeChange = vi.fn()
    render(<SessionBoard {...sessionBoardProps({ onBoardViewModeChange })} />)

    await waitFor(() => expect(onBoardViewModeChange).toHaveBeenCalledWith('ops'))

    fireEvent.click(screen.getByRole('button', { name: 'Theater view' }))

    await waitFor(() => expect(onBoardViewModeChange).toHaveBeenCalledWith('theater'))
    expect(localStorage.getItem('aidm:boardViewMode')).toBe('theater')
  })

  it('exposes tone tag controls in the Operator drawer', () => {
    const onContentToneTagsChange = vi.fn()
    render(<SessionBoard {...sessionBoardProps({ onContentToneTagsChange })} />)

    fireEvent.click(screen.getByText('Operator'))
    fireEvent.click(screen.getByRole('button', { name: 'Noir' }))

    expect(onContentToneTagsChange).toHaveBeenCalledWith(['noir'])
  })

  it('keeps Chronicle campaign export available in the session menu', () => {
    const downloadCampaignChronicle = vi.fn(async () => undefined)
    const sessionMenuRef = createRef<HTMLDivElement>()
    render(
      <SessionBoard
        {...sessionBoardProps({
          downloadCampaignChronicle,
          sessionMenuOpen: true,
          sessionMenuRef,
        })}
      />,
    )

    const menu = screen.getByRole('menu', { name: 'Session menu' })
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Download campaign Chronicle' }))

    expect(downloadCampaignChronicle).toHaveBeenCalledTimes(1)
  })
})
