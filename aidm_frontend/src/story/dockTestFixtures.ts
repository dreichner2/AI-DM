import { vi } from 'vitest'
import type { ActionComposerProps } from '../ActionComposer'
import type {
  CombatLegalAction,
  CombatLegalActionBundle,
  CombatParticipantSummary,
  CombatStatePanel,
  PendingRollNotice,
  WorldStatePanel,
} from '../gameSelectors'
import type { GameplayControlState, GameplayTarget } from '../gameplayControlState'
import type { InspectorPanelProps } from '../InspectorPanel'
import type { SessionBoardProps } from '../SessionBoard'
import { makeBoardFixture, makeInspectorFixture } from './preview/fixtures'

/**
 * Test data for the Story dock and drawer. The shared preview fixtures supply a
 * complete, type-correct board; everything a test asserts on is set here.
 */

export const VIEWER_ID = 30

function target(id: string, name: string, team: string, overrides: Partial<GameplayTarget> = {}): GameplayTarget {
  return {
    id,
    name,
    team,
    rangeBand: 'near',
    currentHp: 10,
    maxHp: 10,
    conditions: [],
    available: true,
    reason: '',
    ...overrides,
  }
}

export function testControls(overrides: Partial<GameplayControlState> = {}): GameplayControlState {
  return {
    actorId: 'player_30',
    actorName: 'Ysolde',
    inCombat: false,
    activeActorId: '',
    isActorTurn: true,
    actionRemaining: null,
    bonusActionRemaining: null,
    concentration: '',
    spells: [
      {
        id: 'spell_magic_missile',
        name: 'Magic Missile',
        level: 1,
        description: 'Force darts.',
        delivery: 'automatic',
        castingTime: 'action',
        castLevels: [1],
        effectLabel: 'damage · force',
        effectSummary: 'three darts of force, 1d4+1 each',
        rulesBacked: true,
        outOfCombat: true,
        relation: 'enemy',
        rangeBands: ['near', 'far'],
        minTargets: 1,
        maxTargets: 3,
        concentration: false,
        prepared: true,
        resourceLabel: 'Level 1 slot · 2 left',
        available: true,
        reason: '',
        targets: [
          target('goblin_1', 'Goblin', 'enemy'),
          target('goblin_2', 'Goblin archer', 'enemy'),
          target('player_30', 'Ysolde', 'player', { available: false, reason: 'Requires a hostile target.' }),
        ],
      },
    ],
    capabilities: [
      {
        id: 'lay_on_hands',
        name: 'Lay on Hands',
        description: 'Spend a healing pool.',
        actionEconomy: 'action',
        targetPolicy: 'self_or_ally',
        effectType: 'healing_pool',
        current: 5,
        max: 10,
        refreshesOn: 'long rest',
        available: true,
        reason: '',
        targets: [
          target('player_30', 'Ysolde', 'player', { currentHp: 7, maxHp: 14 }),
          target('player_31', 'Brannoc', 'player', { currentHp: 3, maxHp: 20 }),
        ],
        poolId: 'lay_on_hands',
        poolName: 'Lay on Hands',
        poolCost: null,
      },
    ],
    interactables: [
      {
        id: 'lead-wagon',
        name: 'Lead wagon',
        kind: 'container',
        description: 'A hitched caravan wagon.',
        revision: 4,
        states: ['Closed'],
        available: true,
        reason: '',
        actions: [
          { id: 'inspect', label: 'Inspect', available: true, reason: '' },
          { id: 'open', label: 'Open', available: true, reason: '' },
          { id: 'search', label: 'Search', available: true, reason: '' },
        ],
      },
    ],
    rest: {
      shortAvailable: true,
      shortReason: '',
      longAvailable: true,
      longReason: '',
      hitDice: { current: 3, max: 3, die: 'd10' },
    },
    ...overrides,
  }
}

function emptyCombat(): CombatStatePanel {
  return {
    active: false,
    status: 'none',
    round: '1',
    battlefield: '',
    goal: '',
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
  }
}

export function testWorld(overrides: Partial<WorldStatePanel> = {}): WorldStatePanel {
  return {
    sceneName: 'The Drowned Lantern',
    sceneDescription: 'A low inn.',
    sceneType: 'social',
    mood: 'uneasy',
    dangerLevel: '1',
    activeQuests: [
      { id: 'grey-caravan', title: 'The Grey Caravan', status: 'active', stage: 'Find the drivers', objective: 'Follow the lanterns' },
    ],
    presentNpcs: [
      { id: 'marta', name: 'Marta Vell', race: 'Human', role: 'Innkeeper', disposition: 'wary', status: 'present' },
    ],
    sceneItems: [{ id: 'ledger', name: 'caravan ledger', quantity: 1, type: 'object' }],
    availableExits: [{ id: 'causeway', name: 'North causeway', status: 'known', type: 'road' }],
    knownLocations: [
      { id: 'causeway', name: 'North causeway', status: 'known', type: 'road' },
      { id: 'mill', name: 'The Drowned Mill', status: 'rumoured', type: 'ruin' },
    ],
    knownNpcs: [
      { id: 'marta', name: 'Marta Vell', race: 'Human', role: 'Innkeeper', disposition: 'wary', status: 'present' },
    ],
    combat: emptyCombat(),
    ...overrides,
  }
}

function participant(overrides: Partial<CombatParticipantSummary>): CombatParticipantSummary {
  return {
    id: 'combatant',
    name: 'Combatant',
    team: 'enemy',
    kind: 'creature',
    source: '',
    health: 'Unhurt',
    healthTone: 'healthy',
    conditions: [],
    morale: '—',
    moraleEvents: [],
    intent: '',
    telegraph: '',
    tacticSource: '',
    brainSource: '',
    position: '',
    selectionScore: '',
    selectionMethod: '',
    ...overrides,
  }
}

function combatAction(overrides: Partial<CombatLegalAction>): CombatLegalAction {
  return {
    id: 'combat.action',
    type: 'turn_action',
    label: 'Action',
    description: '',
    message: '',
    available: true,
    reason: '',
    requiresTarget: false,
    authoritative: true,
    economy: {
      action: 1,
      movement: 'optional',
      endsTurn: false,
      tracking: 'persisted_turn_economy',
      reactionTracked: true,
      subTurnCountersTracked: true,
    },
    targets: [],
    rangeClassification: '',
    allowedRangeBands: [],
    weaponName: '',
    ...overrides,
  }
}

export function testCombat({
  current = true,
  bundle = {},
}: { current?: boolean; bundle?: Partial<CombatLegalActionBundle> } = {}): CombatStatePanel {
  return {
    ...emptyCombat(),
    active: true,
    status: 'active',
    round: '2',
    enemies: [
      participant({ id: 'goblin_1', name: 'Goblin Sentry', health: 'Wounded', healthTone: 'hurt', conditions: ['frightened'] }),
      participant({ id: 'archer_1', name: 'Distant Archer', health: 'Unhurt' }),
    ],
    allies: [participant({ id: 'player_30', name: 'Ysolde Marrow', team: 'player', health: 'Hurt', healthTone: 'hurt' })],
    telegraphs: ['The goblin glances toward the tunnel mouth.', 'The archer nocks an arrow.', 'A third cue.'],
    legalActionBundles: [
      {
        schemaVersion: 1,
        playerId: VIEWER_ID,
        actorId: 'player_30',
        actorName: 'Ysolde Marrow',
        round: 2,
        currentActorId: current ? 'player_30' : 'goblin_1',
        currentActorName: current ? 'Ysolde Marrow' : 'Goblin Sentry',
        isCurrentActor: current,
        economyTracking: 'persisted_turn_economy',
        subTurnCountersTracked: true,
        actionRemaining: 1,
        bonusActionRemaining: 0,
        movementRemaining: 1,
        reactionRemaining: 1,
        actions: [
          combatAction({
            id: 'combat.attack.longsword',
            type: 'attack',
            label: 'Attack with Longsword',
            requiresTarget: true,
            weaponName: 'Longsword',
            targets: [
              { id: 'goblin_1', name: 'Goblin Sentry', rangeBand: 'near', available: true, reason: '' },
              { id: 'archer_1', name: 'Distant Archer', rangeBand: 'far', available: false, reason: 'Target is at far range.' },
            ],
          }),
          combatAction({
            id: 'combat.disengage',
            type: 'disengage',
            label: 'Disengage',
            message: 'Ysolde Marrow disengages and moves to a safer position.',
            available: false,
            reason: 'Your movement is already spent.',
          }),
          combatAction({
            id: 'combat.end_turn',
            type: 'end_turn',
            label: 'End turn',
            message: 'Ysolde Marrow ends their turn.',
          }),
        ],
        ...bundle,
      },
    ],
  }
}

export function testRollNotice(overrides: Partial<PendingRollNotice> = {}): PendingRollNotice {
  return {
    turnId: 41,
    waitingOnLabel: 'Ysolde Marrow',
    waitingPlayerIds: [VIEWER_ID],
    waitingPlayerNames: ['Ysolde Marrow'],
    turnLabel: 'Turn 12',
    ruleLabel: 'stealth check',
    detail: 'You edge toward the reeds.',
    pendingCount: 1,
    isWaitingOnSelectedPlayer: true,
    guidance: {
      pendingTurnId: 41,
      ruleType: 'stealth check',
      dcHint: '13',
      prompt: 'You edge toward the reeds.',
      remainingPlayerIds: [VIEWER_ID],
      rollSpec: {
        die: 'd20',
        mode: 'normal',
        ruleType: 'stealth check',
        reason: 'Stealth check',
        resultVisibility: 'hidden_until_landed',
        ability: { key: 'dexterity', label: 'Dexterity' },
      },
    },
    ...overrides,
  }
}

/** The composer with every callback a spy; the state fields come from the overrides. */
export function testComposer(overrides: Partial<ActionComposerProps> = {}): ActionComposerProps {
  const base = makeBoardFixture('exploration').actionComposerProps
  return {
    ...base,
    selectedCharacterName: 'Ysolde',
    selectedPlayerId: VIEWER_ID,
    composerMode: 'action',
    sendPending: false,
    selectedPlayerHasTurn: true,
    turnControlStatusLabel: 'Free play',
    turnControl: { mode: 'free', source: 'auto', activePlayerId: null, activePlayerName: null },
    queuedActionText: '',
    queuedActionRetryable: false,
    abilityOptions: [
      { key: 'strength', label: 'STR', score: '12', modifier: '+1' },
      { key: 'dexterity', label: 'DEX', score: '17', modifier: '+3' },
    ],
    itemOptions: [
      { id: 'potion', name: 'Potion of Healing', quantity: '2', equipped: false, slot: '' },
      { id: 'longbow', name: 'Longbow', quantity: '1', equipped: true, slot: 'main_hand' },
    ],
    interactionTargets: [
      { kind: 'player', player_id: 31, character_name: 'Brannoc', player_name: 'Imogen', active: true },
    ],
    activePlayers: [
      { id: VIEWER_ID, character_name: 'Ysolde', name: 'Danny' },
      { id: 31, character_name: 'Brannoc', name: 'Imogen' },
    ],
    setActionText: vi.fn(),
    updateActionText: vi.fn(),
    setAdminPasscode: vi.fn(),
    retryRecoverableSubmission: vi.fn(() => true),
    clearQueuedAction: vi.fn(),
    updateTurnControl: vi.fn(),
    stopTtsAudio: vi.fn(),
    submitAction: vi.fn(() => true),
    toggleAdminTools: vi.fn(),
    startDiceRoll: vi.fn(),
    preloadDiceRollDialog: vi.fn(),
    applyComposerMode: vi.fn(),
    updateSelectedDie: vi.fn(),
    setRollMode: vi.fn(),
    setRollReason: vi.fn(),
    setRollTargetPendingTurnId: vi.fn(),
    updateRollAbilityKey: vi.fn(),
    updateSpellName: vi.fn(),
    setSelectedInteractionTargetId: vi.fn(),
    setSelectedInteractionType: vi.fn(),
    setSelectedItemId: vi.fn(),
    setItemQuantity: vi.fn(),
    updateSelectedInventoryAction: vi.fn(),
    updateItemDraftName: vi.fn(),
    updateItemCostGold: vi.fn(),
    ...overrides,
  }
}

export function testBoard(
  overrides: Partial<SessionBoardProps> = {},
  composer: Partial<ActionComposerProps> = {},
): SessionBoardProps {
  const world = overrides.worldState ?? testWorld()
  return {
    ...makeBoardFixture('exploration'),
    playerId: VIEWER_ID,
    sendPending: false,
    streamingTurnActive: false,
    pendingRollNotice: null,
    onPreparePendingRoll: vi.fn(),
    turnRecoveryGate: null,
    operationalError: '',
    onRecoverOperationalError: vi.fn(),
    clarificationRequest: null,
    combatState: world.combat,
    worldState: world,
    gameplayControls: testControls(),
    downloadSessionChronicle: vi.fn(async () => {}),
    ...overrides,
    actionComposerProps: testComposer(composer),
  }
}

export function testInspector(overrides: Partial<InspectorPanelProps> = {}): InspectorPanelProps {
  const base = makeInspectorFixture()
  return {
    ...base,
    selectedPlayerId: VIEWER_ID,
    activePlayers: [
      { id: VIEWER_ID, character_name: 'Ysolde', name: 'Danny', health: { tone: 'wounded', label: 'Wounded', currentHp: 9, maxHp: 14 } },
      { id: 31, character_name: 'Brannoc', name: 'Imogen', is_typing: true },
    ],
    statBlock: { ...base.statBlock, hp: '9 / 14' },
    inventoryRows: [
      { id: 'longbow', item: 'Longbow', count: '1', weight: '2 lb', icon: 'sword', weightValue: 2, type: 'weapon', subtype: '', equipped: false, slot: '', equippable: true },
      { id: 'potion', item: 'Potion of Healing', count: '2', weight: '1 lb', icon: 'potion', weightValue: 1, type: 'consumable', subtype: '', equipped: false, slot: '', equippable: false },
    ],
    equipmentPendingItemKey: null,
    toggleInventoryEquipment: vi.fn(async () => {}),
    loadPlayer: vi.fn(),
    createDefaultPlayer: vi.fn(async () => {}),
    editSelectedPlayer: vi.fn(),
    deleteSelectedPlayer: vi.fn(),
    worldStatePanel: testWorld(),
    ...overrides,
  }
}
