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
import {
  abilityOptionsFromStatBlock,
  itemOptionsFromInventory,
  type CombatLegalAction,
} from '../../gameSelectors'
import type { InspectorPanelProps } from '../../InspectorPanel'
import type { SessionBoardProps } from '../../SessionBoard'
import { StoryComposer } from '../StoryComposer'
import { StoryDrawer } from '../StoryDrawer'
import { StoryView } from '../StoryView'
import type { StoryDrawerTab } from '../types'
import { makeBoardFixture, makeInspectorFixture } from './fixtures'

/**
 * Development-only harness for the Story dock and drawer (story-dock-preview.html).
 * `?state=idle|tray|roll|combat|combat-waiting|narrating|uncertain|ended`, plus
 * `drawer=character|pack|magic|journal|map`, `operator=1` and `shell=1` (inside the real
 * StoryView instead of a bare column). The composer state
 * is simulated locally so typing, the tray and the drawer can be exercised.
 */

type DockPreviewState = 'idle' | 'tray' | 'roll' | 'combat' | 'combat-waiting' | 'narrating' | 'uncertain' | 'ended'

const DOCK_STATES: DockPreviewState[] = ['idle', 'tray', 'roll', 'combat', 'combat-waiting', 'narrating', 'uncertain', 'ended']
const DRAWER_TABS: StoryDrawerTab[] = ['character', 'pack', 'magic', 'journal', 'map']
const params = new URLSearchParams(window.location.search)
const dockState = DOCK_STATES.find((state) => state === params.get('state')) ?? 'idle'
const initialDrawer = DRAWER_TABS.find((tab) => tab === params.get('drawer')) ?? null
const operator = params.get('operator') === '1'
const inShell = params.get('shell') === '1'
document.body.style.margin = '0'
const combatState = dockState === 'combat' || dockState === 'combat-waiting'

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

function previewBoard(): SessionBoardProps {
  const fixtureState = combatState ? 'combat' : dockState === 'narrating' ? 'streaming' : dockState === 'ended' ? 'ended' : 'exploration'
  const board = makeBoardFixture(fixtureState)
  if (!combatState) return board
  const [bundle] = board.combatState.legalActionBundles
  const endTurn: CombatLegalAction = {
    ...bundle.actions[0],
    id: 'combat.end_turn',
    type: 'end_turn',
    label: 'End turn',
    description: 'Take no further action and pass the combat turn.',
    message: 'Ysolde Marrow ends their turn.',
    requiresTarget: false,
    targets: [],
  }
  const actions = [
    ...bundle.actions.map((action) =>
      action.id === 'dash' ? { ...action, available: false, reason: 'Your movement is already spent.' } : action,
    ),
    endTurn,
  ]
  const waiting = dockState === 'combat-waiting'
  const combat = {
    ...board.combatState,
    legalActionBundles: [{
      ...bundle,
      actions,
      movementRemaining: 0,
      isCurrentActor: !waiting,
      currentActorId: waiting ? 'lanternman-2' : bundle.currentActorId,
      currentActorName: waiting ? 'Lanternman' : bundle.currentActorName,
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
    if (dockState === 'tray') document.querySelector<HTMLButtonElement>('button[aria-label="Actions"]')?.click()
  }, [])

  const endedChapter = dockState === 'ended' ? { onContinue: () => console.info('[dock preview] continue') } : null
  const openDrawer = (tab: StoryDrawerTab) => {
    setDrawerTab(tab)
    setDrawerOpen(true)
  }
  const composerElement = (
    <StoryComposer
      board={liveBoard}
      chrome={{ connection: 'ready', canUseOperatorTools: operator, endedChapter }}
      onOpenDrawer={openDrawer}
    />
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
