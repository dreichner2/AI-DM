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
    canUseOperatorTools: true,
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
    retryRecoverableSubmission: vi.fn(() => true),
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
    setSelectedItemId: vi.fn(),
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
    canUseOperatorTools: true,
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
    onPreparePendingRoll: vi.fn(),
    turnRecoveryGate: null,
    turnRecoveryPending: false,
    turnRecoveryError: '',
    turnRecoverySuccess: '',
    onResolveTurnRecovery: vi.fn(async () => undefined),
    combatState: {
      active: false,
      status: 'none',
      round: '1',
      battlefield: 'No battlefield recorded',
      goal: 'Resolve the threat',
      creatureSource: '',
      resolverMethod: '',
      tacticalLevel: 'normal',
      endReason: '',
      combatStartedBy: '',
      enemyGroupSummary: '',
      initiativeRequired: false,
      debugEnabled: false,
      enemies: [],
      allies: [],
      telegraphs: [],
      legalActionBundles: [],
    },
    worldState: {
      sceneName: 'Sealed Door',
      sceneDescription: 'Old glyphs pulse across the door while rain taps the broken roof.',
      sceneType: 'exploration',
      mood: 'mystery',
      dangerLevel: '4',
      activeQuests: [
        {
          id: 'quest-door',
          title: 'Open the sealed door',
          status: 'active',
          stage: 'Study the glyphs',
          objective: 'Find the forgotten keeper’s handprint',
        },
      ],
      presentNpcs: [
        {
          id: 'keeper',
          name: 'The Keeper',
          race: 'Human',
          role: 'Guide',
          disposition: 'wary',
          status: 'active',
        },
      ],
      sceneItems: [{ id: 'glyph-key', name: 'Glyph key', quantity: 1, type: 'object' }],
      availableExits: [{ id: 'rain-gate', name: 'Rain Gate', status: 'visited', type: 'gate' }],
      knownLocations: [],
      knownNpcs: [],
      combat: {
        active: false,
        status: 'none',
        round: '1',
        battlefield: 'No battlefield recorded',
        goal: 'Resolve the threat',
        creatureSource: '',
        resolverMethod: '',
        tacticalLevel: 'normal',
        endReason: '',
        combatStartedBy: '',
        enemyGroupSummary: '',
        initiativeRequired: false,
        debugEnabled: false,
        enemies: [],
        allies: [],
        telegraphs: [],
        legalActionBundles: [],
      },
    },
    gameplayControls: {
      actorId: '',
      actorName: '',
      inCombat: false,
      activeActorId: '',
      isActorTurn: true,
      actionRemaining: null,
      bonusActionRemaining: null,
      concentration: '',
      spells: [],
      capabilities: [],
      interactables: [],
    },
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
    recentMemory: [
      ['The first remembered beat glows in the margin.', 'Turn 1'],
      ['The second remembered beat names the keeper.', 'Turn 2'],
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
  it('shows the current situation and the single next-step line near the top of the Turns feed', () => {
    render(
      <SessionBoard
        {...sessionBoardProps({
          activePlayers: [{ id: 30, character_name: 'Ember', name: 'Danny' }],
          streamingTurnActive: true,
          worldState: {
            ...sessionBoardProps().worldState,
            combat: {
              ...sessionBoardProps().worldState.combat,
              active: true,
              status: 'active',
              round: '3',
              legalActionBundles: [{
                schemaVersion: 1,
                playerId: 30,
                actorId: 'ember',
                actorName: 'Ember',
                round: 3,
                currentActorId: 'keeper',
                currentActorName: 'The Keeper',
                isCurrentActor: false,
                economyTracking: 'turn',
                subTurnCountersTracked: false,
                actionRemaining: 0,
                bonusActionRemaining: 0,
                movementRemaining: 0,
                reactionRemaining: 0,
                actions: [],
              }],
            },
          },
        })}
      />,
    )

    const situation = screen.getByRole('region', { name: 'Current Situation' })
    const timeline = screen.getByRole('log', { name: 'Session timeline' })
    expect(timeline).not.toContainElement(situation)
    expect(timeline).toHaveAttribute('aria-live', 'off')
    expect(screen.getByText('Dungeon Master response in progress.')).toHaveAttribute(
      'aria-live',
      'polite',
    )
    const detailsToggle = within(situation).getByRole('button', { name: 'Show Current Situation details' })
    expect(detailsToggle).toHaveAttribute('aria-expanded', 'false')
    expect(within(situation).queryByText('Old glyphs pulse across the door')).not.toBeInTheDocument()
    fireEvent.click(detailsToggle)
    expect(detailsToggle).toHaveAttribute('aria-expanded', 'true')
    expect(detailsToggle).toHaveAccessibleName('Hide Current Situation details')
    expect(situation).toHaveTextContent('Sealed Door · Ash Hall')
    expect(situation).toHaveTextContent('Old glyphs pulse across the door')
    expect(situation).toHaveTextContent('Ember')
    expect(situation).toHaveTextContent('The Keeper')
    expect(situation).toHaveTextContent('Find the forgotten keeper’s handprint')
    expect(situation).toHaveTextContent('Glyph key')
    expect(situation).toHaveTextContent('Rain Gate')
    expect(situation).toHaveTextContent('Round 3 · The Keeper is acting')
    expect(situation).toHaveTextContent('Waiting for the DM response')
  })

  it('renders durable state consequences as player-language changes in their timeline row', () => {
    render(
      <SessionBoard
        {...sessionBoardProps({
          turnRows: [{
            id: 'state-2',
            role: 'system',
            speaker: 'System',
            text: 'State updated:\n- Removed Healing Potion x1.\n- Restored 5 HP.',
            timestamp: '2026-06-06T10:42:00.000Z',
            metadata: {
              turn_id: 2,
              state_log: {
                lines: [
                  { status: 'applied', message: 'Removed Healing Potion x1.', visibleToPlayer: true },
                  { status: 'modified', message: 'Restored 5 HP.', visibleToPlayer: true },
                  { status: 'applied', message: 'Hidden operator fact.', visibleToPlayer: false },
                ],
              },
            },
          }],
        })}
      />,
    )

    const changes = screen.getByRole('region', { name: 'What changed' })
    expect(changes).toHaveTextContent('Removed Healing Potion x1.')
    expect(changes).toHaveTextContent('Restored 5 HP.')
    expect(changes).not.toHaveTextContent('Hidden operator fact.')
    expect(screen.queryByText(/^State updated:/)).not.toBeInTheDocument()
  })

  it('submits exact scene item and adjacent-exit identities from Current Situation', () => {
    const composer = actionComposerProps()
    render(<SessionBoard {...sessionBoardProps({ actionComposerProps: composer })} />)

    const situation = screen.getByRole('region', { name: 'Current Situation' })
    fireEvent.click(within(situation).getByRole('button', { name: 'Show Current Situation details' }))
    fireEvent.click(within(situation).getByRole('button', { name: 'Pick up Glyph key' }))
    fireEvent.click(within(situation).getByRole('button', { name: 'Travel to Rain Gate' }))
    fireEvent.click(within(situation).getByRole('button', { name: 'Take a short rest' }))

    expect(composer.submitAction).toHaveBeenNthCalledWith(
      1,
      'Ember picks up Glyph key.',
      expect.objectContaining({
        kind: 'item',
        source: 'scene_panel',
        inventory_action: 'pick_up',
        item: { id: 'glyph-key', name: 'Glyph key', quantity: 1 },
      }),
    )
    expect(composer.submitAction).toHaveBeenNthCalledWith(
      2,
      'The party travels to Rain Gate.',
      expect.objectContaining({
        kind: 'travel',
        source: 'scene_panel',
        location: { id: 'rain-gate', name: 'Rain Gate' },
      }),
    )
    expect(composer.submitAction).toHaveBeenNthCalledWith(
      3,
      'Ember completes a short rest.',
      expect.objectContaining({
        kind: 'rest',
        source: 'scene_panel',
        rest_type: 'short_rest',
      }),
    )
  })

  it('keeps the compact situation summary concise with keyboard-native scene details', () => {
    render(<SessionBoard {...sessionBoardProps({ showMobilePresenceStrip: true })} />)

    const situation = screen.getByRole('region', { name: 'Current Situation' })
    const detailsToggle = within(situation).getByRole('button', { name: 'Show Current Situation details' })
    expect(situation).toHaveTextContent('Next: Ember can act')
    expect(detailsToggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(detailsToggle)
    expect(situation).toHaveTextContent('Party')
    expect(situation).toHaveTextContent('Ember')
    expect(situation).toHaveTextContent('Find the forgotten keeper’s handprint')
  })

  it('keeps the latest DM response in Turns and normalizes retired main-tab values', async () => {
    const setMainTab = vi.fn()
    const props = sessionBoardProps()
    render(
      <SessionBoard
        {...props}
        mainTab="dm"
        setMainTab={setMainTab}
        currentResponseEntry={props.turnRows[0]}
        turnRows={[]}
      />,
    )

    expect(screen.queryByRole('tablist', { name: 'Session views' })).not.toBeInTheDocument()
    expect(screen.getByText(/Hidden tail for theater prose/i)).toBeInTheDocument()
    await waitFor(() => expect(setMainTab).toHaveBeenCalledWith('turns'))
  })

  it('does not expose raw session summaries when no player-facing recap exists', () => {
    const props = sessionBoardProps()
    render(
      <SessionBoard
        {...props}
        sessionRecap=""
        activeSession={{ ...props.activeSession!, latest_summary: 'Stale session-card summary.' }}
        sessionState={{ ...props.sessionState!, rolling_summary: 'T1 | P30: raw state ledger' }}
      />,
    )

    expect(screen.queryByLabelText('Previously On')).not.toBeInTheDocument()
    expect(screen.queryByText(/raw state ledger/i)).not.toBeInTheDocument()
    expect(screen.queryByText(/Stale session-card summary/i)).not.toBeInTheDocument()
  })

  it('keeps uncertain delivery visible with explicit safe retry and checked-discard actions', () => {
    const retryRecoverableSubmission = vi.fn(() => true)
    const clearQueuedAction = vi.fn()
    const submitAction = vi.fn(() => true)
    render(
      <SessionBoard
        {...sessionBoardProps({
          actionComposerProps: {
            ...actionComposerProps(),
            actionText: 'I inspect the ceiling instead.',
            queuedActionText: 'I open the sealed door.',
            queuedActionRetryable: true,
            retryRecoverableSubmission,
            clearQueuedAction,
            submitAction,
          },
        })}
      />,
    )

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Delivery uncertain')
    expect(alert).toHaveTextContent('I open the sealed door.')
    fireEvent.click(within(alert).getByRole('button', { name: 'Retry safely' }))
    fireEvent.click(within(alert).getByRole('button', { name: 'Discard after checking' }))

    expect(retryRecoverableSubmission).toHaveBeenCalledTimes(1)
    expect(clearQueuedAction).toHaveBeenCalledTimes(1)
    expect(submitAction).not.toHaveBeenCalled()
  })

  it('keeps a recoverable operational error visible with a refresh action', () => {
    const onRecoverOperationalError = vi.fn()
    const operationalProps = {
      operationalError: 'Realtime disconnected before the turn was confirmed.',
      onRecoverOperationalError,
    }
    const rendered = render(
      <SessionBoard
        {...sessionBoardProps(operationalProps)}
      />,
    )

    const alert = screen.getByRole('alert')
    expect(alert).toHaveTextContent('Play connection needs attention')
    expect(screen.getByRole('log', { name: 'Session timeline' })).not.toContainElement(alert)
    fireEvent.click(within(alert).getByRole('button', { name: 'Refresh and reconnect' }))
    expect(onRecoverOperationalError).toHaveBeenCalledTimes(1)

    rendered.rerender(
      <SessionBoard {...sessionBoardProps({ ...operationalProps, mainTab: 'dm' })} />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('Play connection needs attention')
  })

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
    expect(within(directorPanel).getByText('The first remembered beat glows in the margin.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Download session Chronicle' }))
    expect(downloadSessionChronicle).toHaveBeenCalledTimes(1)
  })

  it('notifies the shell mode owner when the board switches into theater', async () => {
    localStorage.setItem('aidm:boardViewMode', 'ops')
    const onBoardViewModeChange = vi.fn()
    render(<SessionBoard {...sessionBoardProps({ onBoardViewModeChange })} />)

    await waitFor(() => expect(onBoardViewModeChange).toHaveBeenCalledWith('ops'))

    fireEvent.click(screen.getByRole('button', { name: 'View settings' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'Board view mode' })).getByRole('button', {
      name: 'Theater',
    }))

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

    const menu = screen.getByRole('group', { name: 'Session menu' })
    fireEvent.click(within(menu).getByRole('button', { name: 'Download campaign Chronicle' }))

    expect(downloadCampaignChronicle).toHaveBeenCalledTimes(1)
  })

  it('keys duplicate-name inventory choices by item id', () => {
    const setSelectedItemId = vi.fn()
    const redPotion = { id: 'potion-red', name: 'Healing Potion', quantity: '1' }
    const bluePotion = { id: 'potion-blue', name: 'Healing Potion', quantity: '1' }
    render(
      <SessionBoard
        {...sessionBoardProps({
          actionComposerProps: {
            ...actionComposerProps(),
            composerMode: 'item',
            selectedItem: redPotion,
            itemOptions: [redPotion, bluePotion],
            setSelectedItemId,
          },
        })}
      />,
    )

    const itemSelect = screen.getByRole('combobox', { name: 'Inventory item' })
    expect(itemSelect).toHaveValue('potion-red')

    fireEvent.change(itemSelect, { target: { value: 'potion-blue' } })

    expect(setSelectedItemId).toHaveBeenCalledWith('potion-blue')
  })

  it('keeps legacy id-less inventory choices selectable', () => {
    const setSelectedItemId = vi.fn()
    const torch = { name: 'Torch', quantity: '1' }
    const rope = { name: 'Rope', quantity: '1' }
    render(
      <SessionBoard
        {...sessionBoardProps({
          actionComposerProps: {
            ...actionComposerProps(),
            composerMode: 'item',
            selectedItem: torch,
            itemOptions: [torch, rope],
            setSelectedItemId,
          },
        })}
      />,
    )

    const itemSelect = screen.getByRole('combobox', { name: 'Inventory item' })
    expect(itemSelect).toHaveValue('legacy-item-0')

    fireEvent.change(itemSelect, { target: { value: 'legacy-item-1' } })

    expect(setSelectedItemId).toHaveBeenCalledWith('legacy-item-1')
  })

  it('keeps player-safe session actions while hiding operator and lifecycle controls', () => {
    render(
      <SessionBoard
        {...sessionBoardProps({
          canUseOperatorTools: false,
          canEditContentSettings: false,
          sessionMenuOpen: true,
          actionComposerProps: {
            ...actionComposerProps(),
            canUseOperatorTools: false,
            adminToolsUnlocked: true,
          },
        })}
      />,
    )

    expect(screen.queryByRole('button', { name: 'Director Commentary' })).not.toBeInTheDocument()
    expect(screen.queryByText('Operator')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Admin mode' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Export' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Share' })).toBeInTheDocument()

    const menu = screen.getByRole('group', { name: 'Session menu' })
    expect(within(menu).getByRole('button', { name: 'Download session Chronicle' })).toBeInTheDocument()
    expect(within(menu).getByRole('button', { name: 'Download campaign Chronicle' })).toBeInTheDocument()
    expect(within(menu).queryByRole('button', { name: 'Rename session' })).not.toBeInTheDocument()
    expect(within(menu).queryByRole('button', { name: 'Delete session' })).not.toBeInTheDocument()
  })

  it('opens the configured roller when the selected character owes a pending check', () => {
    const onPreparePendingRoll = vi.fn()
    const guidance = {
      pendingTurnId: 71,
      ruleType: 'saving_throw',
      dcHint: null,
      prompt: 'Make a Wisdom saving throw.',
      remainingPlayerIds: [30],
      rollSpec: {
        die: 'd20',
        mode: 'disadvantage' as const,
        ruleType: 'saving_throw',
        reason: 'Wisdom saving throw',
        resultVisibility: 'hidden_until_landed' as const,
        ability: { key: 'wisdom', label: 'WIS' },
      },
    }
    render(
      <SessionBoard
        {...sessionBoardProps({
          onPreparePendingRoll,
          pendingRollNotice: {
            turnId: 71,
            turnLabel: 'Turn 7',
            ruleLabel: 'Saving throw',
            detail: 'Make a Wisdom saving throw.',
            waitingOnLabel: 'Ember',
            waitingPlayerIds: [30],
            waitingPlayerNames: ['Ember'],
            pendingCount: 1,
            isWaitingOnSelectedPlayer: true,
            guidance,
          },
        })}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: 'Roll now' }))

    expect(onPreparePendingRoll).toHaveBeenCalledWith(guidance)
  })

  it('blocks play for every viewer while exposing recovery decisions only to operators', () => {
    const gate = {
      status: 'required' as const,
      reason: 'post_dm_state_application_failed' as const,
      turnId: 72,
      narrationSaved: true as const,
      mechanicsApplied: true,
      mechanicsStatus: 'partial' as const,
      preDmMechanicsApplied: true,
      preDmAppliedChangeCount: 2,
      postDmMechanicsApplied: false as const,
      createdAt: '2026-06-06T10:46:00.000Z',
    }
    render(
      <SessionBoard
        {...sessionBoardProps({
          canUseOperatorTools: false,
          canEditContentSettings: false,
          turnRecoveryGate: gate,
          actionComposerProps: {
            ...actionComposerProps(),
            actionText: 'I keep my hand on the sealed door.',
            canUseOperatorTools: false,
          },
        })}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('Turn 72 needs recovery')
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Narration was saved after 2 pre-DM changes; post-DM mechanics were not applied',
    )
    expect(screen.getByRole('alert')).toHaveTextContent(
      'The pre-DM changes remain authoritative. Do not replay or duplicate them',
    )
    expect(screen.getByText(/Your draft is safe/i)).toBeInTheDocument()
    expect(screen.queryByRole('form', { name: 'Resolve turn recovery' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/Your Action/i)).not.toBeInTheDocument()
    expect(screen.getByText('Actions are paused for state recovery')).toBeInTheDocument()
  })

  it('requires an explicit recovery decision and bounded operator note before resolving', () => {
    const onResolveTurnRecovery = vi.fn(async () => undefined)
    render(
      <SessionBoard
        {...sessionBoardProps({
          turnRecoveryGate: {
            status: 'required',
            reason: 'post_dm_state_application_failed',
            turnId: 73,
            narrationSaved: true,
            mechanicsApplied: false,
            mechanicsStatus: 'none',
            preDmMechanicsApplied: false,
            preDmAppliedChangeCount: 0,
            postDmMechanicsApplied: false,
            createdAt: '2026-06-06T10:47:00.000Z',
          },
          onResolveTurnRecovery,
        })}
      />,
    )

    const form = screen.getByRole('form', { name: 'Resolve turn recovery' })
    const submit = within(form).getByRole('button', { name: 'Resolve and resume play' })
    expect(submit).toBeDisabled()

    fireEvent.click(within(form).getByRole('radio', { name: /State corrected/i }))
    expect(submit).toBeDisabled()
    fireEvent.change(within(form).getByLabelText('Operator note'), {
      target: { value: 'Verified HP and inventory against turn 72; corrected the session snapshot.' },
    })
    expect(submit).toBeEnabled()
    fireEvent.click(submit)

    expect(onResolveTurnRecovery).toHaveBeenCalledWith(
      'state_corrected',
      'Verified HP and inventory against turn 72; corrected the session snapshot.',
    )
  })

  it('announces recovery failure and locks the resolution form while a retry is in flight', () => {
    const gate = {
      status: 'required' as const,
      reason: 'post_dm_state_application_failed' as const,
      turnId: 74,
      narrationSaved: true as const,
      mechanicsApplied: false,
      mechanicsStatus: 'none' as const,
      preDmMechanicsApplied: false,
      preDmAppliedChangeCount: 0,
      postDmMechanicsApplied: false as const,
      createdAt: '2026-06-06T10:48:00.000Z',
    }
    const rendered = render(
      <SessionBoard
        {...sessionBoardProps({
          turnRecoveryGate: gate,
          turnRecoveryError: 'Recovery failed: request timed out. The recovery request was not retried.',
        })}
      />,
    )

    expect(screen.getByRole('alert')).toHaveTextContent('Recovery failed: request timed out')

    rendered.rerender(
      <SessionBoard
        {...sessionBoardProps({
          turnRecoveryGate: gate,
          turnRecoveryPending: true,
        })}
      />,
    )
    expect(screen.getByRole('button', { name: 'Resolving and refreshing…' })).toBeDisabled()
    expect(screen.getByLabelText('Operator note')).toBeDisabled()
    expect(screen.getByRole('radio', { name: /State corrected/i })).toBeDisabled()
  })
})
