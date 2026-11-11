import { StrictMode, useEffect, useState, type CSSProperties } from 'react'
import { createRoot } from 'react-dom/client'
import '../../index.css'
import '../../App.css'
import type { ActionComposerProps } from '../../ActionComposer'
import {
  INITIATIVE_ROLL_ABILITY_KEY,
  PLAIN_ROLL_ABILITY_KEY,
  interactionTargetId,
  itemOptionSelectionKey,
  type ComposerMode,
  type InteractionTarget,
  type InteractionType,
  type InventoryAction,
  type RollMode,
} from '../../gameActions'
import { abilityOptionsFromStatBlock, itemOptionsFromInventory } from '../../gameSelectors'
import type { GameplayInteractableControl, GameplayObjectAction, GameplaySpellControl } from '../../gameplayControlState'
import type { InspectorPanelProps } from '../../InspectorPanel'
import type { SessionBoardProps } from '../../SessionBoard'
import type { TimelineEntry } from '../../types'
import { StoryComposer } from '../StoryComposer'
import { StoryDrawer } from '../StoryDrawer'
import { useStoryNoticeLine, type StoryNotice } from '../storyNoticeLine'
import { StoryNotices } from '../StoryNotices'
import { StoryView } from '../StoryView'
import type { StoryDrawerTab } from '../types'
import { isStoryPreviewState, makeBoardFixture, makeInspectorFixture, type StoryPreviewState } from './fixtures'

/**
 * Development-only harness for the Story dock and drawer (story-dock-preview.html).
 * `?state=idle|tray|roll|combat|combat-waiting|narrating|uncertain|ended|dying|trade|rest|ending`,
 * or the server-offered states `offered` (chips and the Scene tab from `offeredActions`, the
 * journal block and story beats), `wizard` (the combat ribbon for a spellcaster), `downed`
 * (a stable hero out of combat, someone else at the table) and `closein` (an attack that moves first),
 * plus `drawer=character|pack|magic|journal|map`, `section=<tray section>` (opens the tray there),
 * `notice=status|alert` (the app's notice line above the dock), `operator=1` and `shell=1`
 * (inside the real StoryView instead of a bare column). The composer
 * state is simulated locally so typing, the tray and the drawer can be exercised.
 */

type DockPreviewState =
  | 'idle'
  | 'tray'
  | 'roll'
  | 'combat'
  | 'combat-waiting'
  | 'narrating'
  | 'uncertain'
  | 'ended'
  | 'dying'
  | 'trade'
  | 'rest'
  | 'ending'
  | 'offered'
  | 'wizard'
  | 'downed'
  | 'closein'

const DOCK_STATES: DockPreviewState[] = [
  'idle',
  'tray',
  'roll',
  'combat',
  'combat-waiting',
  'narrating',
  'uncertain',
  'ended',
  'dying',
  'trade',
  'rest',
  'ending',
  'offered',
  'wizard',
  'downed',
  'closein',
]
const DRAWER_TABS: StoryDrawerTab[] = ['character', 'pack', 'magic', 'journal', 'map']
const params = new URLSearchParams(window.location.search)
const dockState = DOCK_STATES.find((state) => state === params.get('state')) ?? 'idle'
const initialDrawer = DRAWER_TABS.find((tab) => tab === params.get('drawer')) ?? null
const traySection = params.get('section')
const PREVIEW_NOTICES: Record<string, StoryNotice> = {
  status: { key: 'copied', text: 'Session link copied.', fades: true },
  alert: { key: 'failed', text: 'Could not start the next chapter: the game server did not answer.', tone: 'fail' },
}
const previewNotices = PREVIEW_NOTICES[params.get('notice') ?? ''] ? [PREVIEW_NOTICES[params.get('notice') ?? '']] : []
const operator = params.get('operator') === '1'
const inShell = params.get('shell') === '1'
document.body.style.margin = '0'
const combatState = ['combat', 'combat-waiting', 'dying', 'wizard', 'closein'].includes(dockState)

function fixtureState(): StoryPreviewState {
  if (dockState === 'combat-waiting' || dockState === 'wizard' || dockState === 'closein') return 'combat'
  if (dockState === 'narrating') return 'streaming'
  if (dockState === 'offered' || dockState === 'downed') return 'exploration'
  return isStoryPreviewState(dockState) ? dockState : 'exploration'
}

const pageStyle: CSSProperties = {
  position: 'fixed',
  inset: 0,
  overflow: 'hidden',
  background: [
    'radial-gradient(60% 36% at 50% 108%, var(--mood-ember), transparent 70%)',
    'radial-gradient(120% 62% at 50% 112%, var(--mood-glow), transparent 62%)',
    'var(--story-bg)',
  ].join(', '),
}

const columnStyle: CSSProperties = {
  position: 'relative',
  display: 'flex',
  flexDirection: 'column',
  height: '100dvh',
  maxWidth: 'calc(var(--story-measure) + 10ch)',
  margin: '0 auto',
  padding: '0 var(--story-gutter)',
  boxSizing: 'border-box',
}

const proseStyle: CSSProperties = {
  flex: 1,
  minHeight: 0,
  overflow: 'hidden',
  paddingTop: 'calc(var(--story-topbar-height) + 24px)',
  fontFamily: 'var(--story-prose)',
  fontSize: 'var(--story-prose-size)',
  lineHeight: 'var(--story-prose-leading)',
  color: 'var(--story-ink)',
  WebkitMaskImage: 'linear-gradient(to bottom, transparent, #000 48px, #000 calc(100% - 72px), transparent)',
  maskImage: 'linear-gradient(to bottom, transparent, #000 48px, #000 calc(100% - 72px), transparent)',
  display: 'flex',
  flexDirection: 'column',
  justifyContent: 'flex-end',
}

const noop = () => {}

function offer(id: GameplayObjectAction['id'], label: string, extra: Partial<GameplayObjectAction> = {}): GameplayObjectAction {
  return { id, label, available: true, reason: '', phrase: true, ...extra }
}

function sceneObject(id: string, name: string, states: string[], actions: GameplayObjectAction[]): GameplayInteractableControl {
  return { id, name, kind: 'object', description: '', revision: 1, states, available: true, reason: '', offered: true, actions }
}

/** The Lantern Post as the server now describes it: offered actions, take labels, the journal and story beats. */
function offeredBoard(board: SessionBoardProps): SessionBoardProps {
  const interactables = [
    sceneObject('inn_caravan_ledger', 'Caravan Ledger', [], [
      offer('search', 'Search Caravan Ledger'),
      offer('inspect', 'Read the caravan ledger', { authored: true, check: { skill: 'investigation', ability: 'intelligence' } }),
    ]),
    // The search failed once: it may be tried again by taking time. One chip still stands for the bench.
    sceneObject('inn_drivers_bench', "Driver's Bench", ['Open'], [
      offer('search', "Search under the driver's bench", { authored: true, retry: true }),
      offer('inspect', "Look over the driver's bench", { authored: true }),
    ]),
    sceneObject('inn_wagon_strongbox', 'Wagon Strongbox', ['Closed', 'Locked'], [
      offer('search', 'Search the strongbox', { authored: true, available: false, reason: 'It is closed and locked.' }),
      offer('unlock', 'Unlock the strongbox', { authored: true, available: false, reason: 'It is locked, and you carry no key that fits.' }),
      offer('break', 'Break the strongbox open', { authored: true }),
      offer('inspect', 'Examine Wagon Strongbox'),
    ]),
    sceneObject('inn_wagon_bell', 'Wagon Bell', [], [offer('use', 'Ring the wagon bell', { authored: true })]),
  ]
  const storyBeats = [
    { kind: 'clue', text: 'Clue — The erased caravan ledger' },
    { kind: 'item_gained', text: 'Taken — Caravan Strongbox Key' },
    { kind: 'place', text: 'New place on the map — The Old North Road' },
    { kind: 'hint', text: 'Scripted narrator — use the suggestions, or connect an AI narrator in Settings for free-form play' },
  ]
  const withBeats = (row: TimelineEntry): TimelineEntry => ({ ...row, metadata: { ...row.metadata, storyBeats } })
  const lastDm = [...board.turnRows, ...(board.currentResponseEntry ? [board.currentResponseEntry] : [])]
    .reverse()
    .find((row) => row.role === 'dm')
  const turnRows = board.turnRows.map((row) => (row === lastDm ? withBeats(row) : row))
  const currentResponseEntry =
    board.currentResponseEntry && board.currentResponseEntry === lastDm ? withBeats(board.currentResponseEntry) : board.currentResponseEntry
  const journal = {
    quest: {
      id: 'road',
      title: 'The Road of Unremembered Kings',
      summary: 'Find out who erased the caravan’s names.',
      status: 'active',
      stageTitle: 'The Caravan Without Names',
      stageSummary: 'Question the witnesses at the Lantern Post and learn what the ledger hides.',
    },
    objectives: [
      { id: 'ledger', title: 'Read the caravan ledger', optional: false, status: 'completed' },
      { id: 'stone', title: 'Recover the road-stone chip', optional: true, status: 'active' },
    ],
    clues: [{ id: 'clue_ledger', title: 'The erased caravan ledger', text: 'Every driver’s and guard’s name has been scratched out in black strokes that smell faintly of cold ash.' }],
    people: [{ id: 'mara', name: 'Mara Reedwake', role: 'innkeeper and local witness', summary: 'Keeps the Lantern Post and saw the caravan arrive.', met: true }],
    places: [
      { id: 'post', name: 'The Lantern Post', state: 'current' },
      { id: 'road', name: 'The Old North Road', state: 'discovered' },
    ],
  }
  return {
    ...board,
    turnRows,
    currentResponseEntry,
    worldState: {
      ...board.worldState,
      sceneItems: [
        { id: 'key', name: 'Caravan Strongbox Key', quantity: 1, type: 'key', takeLabel: 'Take the Caravan Strongbox Key' },
        { id: 'coins', name: 'gp', quantity: 15, type: 'currency', takeLabel: 'Take 15 gp' },
      ],
    },
    gameplayControls: { ...board.gameplayControls, interactables },
    sessionState: board.sessionState
      ? { ...board.sessionState, state_snapshot: { ...board.sessionState.state_snapshot, journal } }
      : null,
  }
}

function wizardSpell(id: string, name: string, level: number, maxTargets: number, extra: Partial<GameplaySpellControl> = {}): GameplaySpellControl {
  return {
    id,
    name,
    level,
    description: '',
    delivery: 'spell attack',
    castingTime: 'action',
    castLevels: level ? [level] : [],
    effectLabel: '',
    effectSummary: level ? 'three darts of force, 1d4+1 each' : 'Ranged spell attack · 1d10 fire',
    rulesBacked: true,
    relation: 'enemy',
    rangeBands: ['near', 'far'],
    minTargets: 1,
    maxTargets,
    concentration: false,
    prepared: true,
    resourceLabel: level ? 'Level 1 slot · 2 left' : 'Cantrip',
    available: true,
    reason: '',
    targets: [
      { id: 'lanternman-1', name: 'Cracked lanternman', team: 'enemy', rangeBand: 'melee', currentHp: null, maxHp: null, conditions: [], available: true, reason: '' },
      { id: 'lanternman-3', name: 'Burning-hand lanternman', team: 'enemy', rangeBand: 'far', currentHp: null, maxHp: null, conditions: [], available: true, reason: '' },
    ],
    ...extra,
  }
}

/** The combat ribbon for a wizard who has cast two spells this fight, with a story action the server allows mid-fight. */
function wizardBoard(board: SessionBoardProps): SessionBoardProps {
  const [bundle] = board.combatState.legalActionBundles
  const quarterstaff = { ...bundle.actions[0], id: 'combat.attack.quarterstaff', label: 'Attack with Quarterstaff', weaponName: 'Quarterstaff' }
  const arcaneRecovery = {
    ...bundle.actions[0],
    id: 'class.use.arcane_recovery',
    type: 'class_feature',
    label: 'Arcane Recovery',
    description: 'Recover spell slots during a short rest.',
    available: false,
    reason: 'Only during a short rest.',
    requiresTarget: false,
    targets: [],
    capabilityEffect: 'restore_slots',
  }
  const movement = bundle.actions
    .filter((action) => action.type === 'advance' || action.type === 'reposition')
    .map((action) => ({ ...action, available: true, reason: '' }))
  const rest = bundle.actions.filter((action) => !['attack', 'advance', 'reposition'].includes(action.type))
  const combat = {
    ...board.combatState,
    legalActionBundles: [{ ...bundle, actions: [quarterstaff, arcaneRecovery, ...movement, ...rest] }],
  }
  const cast = (id: string, turnId: number, spell: string): TimelineEntry => ({
    id,
    role: 'player',
    speaker: 'Ysolde Marrow',
    text: `Ysolde Marrow casts ${spell} at Cracked lanternman.`,
    timestamp: null,
    metadata: { turn_id: turnId, action_intent: { kind: 'spell', spell: { name: spell } } },
  })
  return {
    ...board,
    turnRows: [...board.turnRows, cast('cast-1', 90, 'Magic Missile'), cast('cast-2', 91, 'Fire Bolt')],
    combatState: combat,
    worldState: { ...board.worldState, combat },
    gameplayControls: {
      ...board.gameplayControls,
      spells: [
        wizardSpell('spell_fire_bolt', 'Fire Bolt', 0, 1),
        wizardSpell('spell_magic_missile', 'Magic Missile', 1, 3),
        wizardSpell('spell_shield', 'Shield', 1, 1, { castingTime: 'reaction', available: false, reason: 'This reaction triggers automatically.' }),
      ],
      interactables: [
        sceneObject('cairn_banner_stone', 'Banner-stone', [], [
          offer('inspect', 'Examine Banner-stone'),
          offer('use', 'Speak the names at the banner-stone', { authored: true }),
        ]),
      ],
    },
  }
}

/** A stable hero at 0 HP outside combat, with the rest of the party connected. */
function downedBoard(board: SessionBoardProps): SessionBoardProps {
  const [viewer, ...others] = board.activePlayers
  return {
    ...board,
    activePlayers: [{ ...viewer, health: { tone: 'dead', label: 'Dead', currentHp: 0, maxHp: 24 } }, ...others],
    sessionState: board.sessionState
      ? {
          ...board.sessionState,
          state_snapshot: {
            ...board.sessionState.state_snapshot,
            playerCharacters: [{ playerId: viewer.id, health: { conditions: ['unconscious', 'stable'] } }],
          },
        }
      : null,
  }
}

/** A fighter out of reach with the move unspent: the longsword attack closes in first. */
function closeInBoard(board: SessionBoardProps): SessionBoardProps {
  const [bundle] = board.combatState.legalActionBundles
  const [bow, ...rest] = bundle.actions
  const longsword = {
    ...bow,
    id: 'combat.attack.longsword',
    label: 'Attack with Longsword',
    description: 'Close in and strike with your longsword.',
    weaponName: 'Longsword',
    requiresMove: true,
    targets: bow.targets.slice(0, 1),
    cost: { action: 1, bonusAction: 0, movement: 1, reaction: 0 },
  }
  const combat = {
    ...board.combatState,
    legalActionBundles: [{ ...bundle, movementRemaining: 1, actions: [longsword, ...rest] }],
  }
  return { ...board, combatState: combat, worldState: { ...board.worldState, combat } }
}

function previewBoard(): SessionBoardProps {
  const board = makeBoardFixture(fixtureState())
  if (dockState === 'offered') return offeredBoard(board)
  if (dockState === 'wizard') return wizardBoard(board)
  if (dockState === 'downed') return downedBoard(board)
  if (dockState === 'closein') return closeInBoard(board)
  if (dockState !== 'combat-waiting') return board
  const [bundle] = board.combatState.legalActionBundles
  // Off the viewer's turn the server offers nothing legal.
  const combat = {
    ...board.combatState,
    turnOrder: board.combatState.turnOrder?.map((entry) => ({ ...entry, isCurrent: entry.id === 'lanternman-2' })),
    legalActionBundles: [{
      ...bundle,
      isCurrentActor: false,
      currentActorId: 'lanternman-2',
      currentActorName: 'Lanternman',
      actions: bundle.actions.map((action) => ({ ...action, available: false, reason: 'Lanternman is acting now.' })),
    }],
  }
  return {
    ...board,
    combatState: combat,
    worldState: { ...board.worldState, combat },
  }
}

function previewInspector(board: SessionBoardProps): InspectorPanelProps {
  const inspector = makeInspectorFixture()
  return {
    ...inspector,
    canUseOperatorTools: operator,
    worldStatePanel: {
      ...inspector.worldStatePanel,
      knownNpcs: [
        ...inspector.worldStatePanel.presentNpcs,
        { id: 'hask', name: 'Driver Hask', race: 'Human', role: 'Caravan driver', disposition: 'missing', status: 'missing' },
      ],
      knownLocations: [
        ...inspector.worldStatePanel.availableExits,
        ...inspector.worldStatePanel.knownLocations,
      ],
    },
    activePlayers: board.activePlayers,
  }
}

/** A local stand-in for useComposerActions: enough state to drive the dock by hand. */
function usePreviewComposer(base: ActionComposerProps, inspector: InspectorPanelProps): ActionComposerProps {
  const [actionText, setActionText] = useState(dockState === 'uncertain' ? 'I lift the lantern and step onto the causeway.' : '')
  const [composerMode, setComposerMode] = useState<ComposerMode>('action')
  const [selectedDie, setSelectedDie] = useState('d20')
  const [rollMode, setRollMode] = useState<RollMode>('normal')
  const [rollReason, setRollReason] = useState('')
  const [rollTargetPendingTurnId, setRollTargetPendingTurnId] = useState('')
  const [selectedAbilityKey, setSelectedAbilityKey] = useState(PLAIN_ROLL_ABILITY_KEY)
  const [spellName, setSpellName] = useState('')
  const [selectedItemId, setSelectedItemId] = useState('')
  const [itemQuantity, setItemQuantity] = useState('1')
  const [selectedInventoryAction, setSelectedInventoryAction] = useState<InventoryAction>('use')
  const [selectedInteractionTargetId, setSelectedInteractionTargetId] = useState('')
  const [selectedInteractionType, setSelectedInteractionType] = useState<InteractionType>('speak_to')
  const [adminPasscode, setAdminPasscode] = useState('')
  const [adminToolsUnlocked, setAdminToolsUnlocked] = useState(false)
  const [queuedActionRetryable, setQueuedActionRetryable] = useState(dockState === 'uncertain')

  const abilityOptions = abilityOptionsFromStatBlock(inspector.statBlock)
  const itemOptions = itemOptionsFromInventory(inspector.inventoryRows)
  const interactionTargets: InteractionTarget[] = [
    ...base.activePlayers
      .filter((player) => player.id !== base.selectedPlayerId)
      .map((player) => ({
        kind: 'player' as const,
        player_id: player.id,
        character_name: player.character_name,
        player_name: player.name,
        active: true,
      })),
    { kind: 'npc', npc_id: 'marta', character_name: 'Marta Vell', player_name: 'Innkeeper', active: true },
  ]
  const selectedItem =
    itemOptions.find((item, index) => itemOptionSelectionKey(item, index) === selectedItemId) ?? itemOptions[0] ?? null
  const selectedAbility =
    selectedAbilityKey === INITIATIVE_ROLL_ABILITY_KEY
      ? abilityOptions.find((ability) => ability.key === 'dexterity') ?? null
      : abilityOptions.find((ability) => ability.key === selectedAbilityKey) ?? null
  const selectedInteractionTarget =
    interactionTargets.find((target) => interactionTargetId(target) === selectedInteractionTargetId) ??
    interactionTargets[0] ??
    null

  const log = (label: string, ...details: unknown[]) => console.info(`[dock preview] ${label}`, ...details)

  return {
    ...base,
    actionText,
    setActionText,
    updateActionText: setActionText,
    composerMode,
    applyComposerMode: (mode) => {
      if (mode !== 'admin' || adminToolsUnlocked) setComposerMode(mode)
    },
    selectedDie,
    updateSelectedDie: setSelectedDie,
    rollMode,
    setRollMode,
    rollReason,
    setRollReason,
    rollTargetPendingTurnId,
    setRollTargetPendingTurnId,
    selectedAbilityKey,
    selectedAbility,
    abilityOptions,
    updateRollAbilityKey: setSelectedAbilityKey,
    spellName,
    updateSpellName: setSpellName,
    itemOptions,
    selectedItem,
    setSelectedItemId,
    itemQuantity,
    setItemQuantity,
    selectedInventoryAction,
    updateSelectedInventoryAction: setSelectedInventoryAction,
    interactionTargets,
    selectedInteractionTarget,
    selectedInteractionTargetId,
    setSelectedInteractionTargetId,
    selectedInteractionType,
    setSelectedInteractionType,
    adminPasscode,
    setAdminPasscode,
    adminToolsUnlocked,
    canUseOperatorTools: operator,
    toggleAdminTools: () => setAdminToolsUnlocked((current) => !current),
    queuedActionText: queuedActionRetryable ? actionText : '',
    queuedActionRetryable,
    retryRecoverableSubmission: () => {
      log('retry')
      setQueuedActionRetryable(false)
      return true
    },
    clearQueuedAction: () => setQueuedActionRetryable(false),
    submitAction: (message, intent) => {
      log('submit', message ?? actionText, intent ?? { kind: composerMode })
      setActionText('')
      return true
    },
    startDiceRoll: (die) => log('start dice roll', die ?? selectedDie, rollMode, rollReason),
    preloadDiceRollDialog: noop,
  }
}

export function DockPreview() {
  const [board] = useState(previewBoard)
  const [inspector] = useState(() => previewInspector(board))
  const composer = usePreviewComposer(board.actionComposerProps, inspector)
  const [drawerTab, setDrawerTab] = useState<StoryDrawerTab>(initialDrawer ?? 'character')
  const [drawerOpen, setDrawerOpen] = useState(Boolean(initialDrawer))
  const prose = board.latestDmText.split('\n\n').slice(-3)
  const liveBoard: SessionBoardProps = {
    ...board,
    canUseOperatorTools: operator,
    sendPending: dockState === 'narrating',
    streamingTurnActive: dockState === 'narrating',
    pendingRollNotice: dockState === 'roll'
      ? {
          turnId: 41,
          waitingOnLabel: 'Ysolde Marrow',
          waitingPlayerIds: [30],
          waitingPlayerNames: ['Ysolde Marrow'],
          turnLabel: 'Turn 12',
          ruleLabel: 'stealth check',
          detail: 'You edge along the wagon toward the reeds.',
          pendingCount: 1,
          isWaitingOnSelectedPlayer: true,
          guidance: {
            pendingTurnId: 41,
            ruleType: 'stealth check',
            dcHint: '13',
            prompt: 'You edge along the wagon toward the reeds.',
            remainingPlayerIds: [30],
            rollSpec: {
              die: 'd20',
              mode: 'normal',
              ruleType: 'stealth check',
              reason: 'Stealth check',
              resultVisibility: 'hidden_until_landed',
              ability: { key: 'dexterity', label: 'Dexterity' },
            },
          },
        }
      : null,
    onPreparePendingRoll: (guidance) => {
      composer.setRollMode(guidance.rollSpec.mode)
      composer.setRollReason(guidance.rollSpec.reason)
      composer.setRollTargetPendingTurnId(String(guidance.pendingTurnId))
      composer.applyComposerMode('roll')
    },
    actionComposerProps: composer,
  }

  useEffect(() => {
    const button = (selector: string, text?: string) =>
      [...document.querySelectorAll<HTMLButtonElement>(selector)].find((candidate) => !text || candidate.textContent?.startsWith(text))
    if (dockState === 'trade' && !traySection) {
      button('.story-suggestions button', 'Trade with')?.click()
      return
    }
    const section = traySection ?? (dockState === 'rest' ? 'Scene' : dockState === 'tray' ? '' : null)
    if (section === null) return
    button('button[aria-label="Actions"]')?.click()
    if (section) window.setTimeout(() => button('.story-tray-nav-link', section)?.click(), 0)
  }, [])

  const noticeLine = useStoryNoticeLine(previewNotices)
  const endedChapter = dockState === 'ended' ? { onContinue: () => console.info('[dock preview] continue') } : null
  const openDrawer = (tab: StoryDrawerTab) => {
    setDrawerTab(tab)
    setDrawerOpen(true)
  }
  const composerElement = (
    <>
      <StoryNotices line={noticeLine} />
      <StoryComposer
        board={liveBoard}
        chrome={{ connection: 'ready', canUseOperatorTools: operator, endedChapter }}
        onOpenDrawer={openDrawer}
      />
    </>
  )
  const drawerElement = (
    <StoryDrawer
      board={liveBoard}
      inspector={inspector}
      open={drawerOpen}
      tab={drawerTab}
      onTabChange={setDrawerTab}
      onClose={() => setDrawerOpen(false)}
    />
  )

  if (inShell) {
    return (
      <StoryView
        board={liveBoard}
        inspector={inspector}
        chrome={{
          connection: 'ready',
          safeMode: false,
          canUseOperatorTools: operator,
          ttsAvailable: true,
          ttsEnabled: false,
          onToggleTts: noop,
          isFullscreen: false,
          onToggleFullscreen: noop,
          onOpenConsole: noop,
          onSwitchAdventure: noop,
          onExitToTitle: noop,
          onOpenSettings: noop,
          onOpenAccount: noop,
          endedChapter,
        }}
        composer={composerElement}
        drawer={drawerElement}
        drawerOpen={drawerOpen}
        drawerTab={drawerTab}
        onDrawerOpenChange={(open, tab) => {
          setDrawerOpen(open)
          if (tab) setDrawerTab(tab)
        }}
      />
    )
  }

  return (
    <div className="story-shell" data-mood={combatState ? 'combat' : 'hearth'} style={pageStyle}>
      <main style={columnStyle} aria-label="Story">
        <div style={proseStyle}>
          {prose.map((paragraph) => (
            <p key={paragraph.slice(0, 32)} style={{ margin: '0 0 0.9em' }}>{paragraph}</p>
          ))}
        </div>
        {composerElement}
      </main>
      {drawerElement}
    </div>
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <DockPreview />
  </StrictMode>,
)
