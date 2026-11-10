import { StrictMode, useEffect, useState, type CSSProperties } from 'react'
import { createRoot } from 'react-dom/client'
import '../../index.css'
import '../../App.css'
import type { SessionBoardProps } from '../../SessionBoard'
import { StoryView } from '../StoryView'
import type { StoryChromeProps, StoryConnection, StoryDrawerTab } from '../types'
import { isStoryPreviewState, makeBoardFixture, makeInspectorFixture, STREAMING_NARRATION, type StoryPreviewState } from './fixtures'

/**
 * Development-only harness for the Story stage (story-stage-preview.html).
 * `?state=empty|opening|exploration|streaming|combat|ended`, plus optional
 * `drawer=character|pack|magic|journal|map`, `connection=connecting|reconnecting|offline`,
 * `safe=1` and `operator=1`.
 */

const noop = () => {}
const params = new URLSearchParams(window.location.search)
const stateParam = params.get('state')
const previewState: StoryPreviewState = isStoryPreviewState(stateParam) ? stateParam : 'exploration'
const drawerParam = params.get('drawer')
const DRAWER_TABS: StoryDrawerTab[] = ['character', 'pack', 'magic', 'journal', 'map']
const initialDrawer = DRAWER_TABS.find((tab) => tab === drawerParam) ?? null
const connectionParam = params.get('connection')
const connection: StoryConnection =
  connectionParam === 'connecting' || connectionParam === 'reconnecting' || connectionParam === 'offline'
    ? connectionParam
    : 'ready'

const placeholderComposer: CSSProperties = {
  display: 'grid',
  placeItems: 'center',
  height: 64,
  borderTop: '1px solid var(--story-hairline)',
  fontSize: '0.72rem',
  letterSpacing: '0.16em',
  textTransform: 'uppercase',
  color: 'var(--story-faint)',
}

const placeholderDrawer: CSSProperties = {
  height: '100%',
  boxSizing: 'border-box',
  borderLeft: '1px solid var(--story-glass-edge)',
  background: 'var(--story-glass)',
  backdropFilter: 'blur(var(--story-glass-blur))',
}

function streamTo(board: SessionBoardProps, length: number): SessionBoardProps {
  const current = board.currentResponseEntry
  if (!current) return board
  const done = length >= STREAMING_NARRATION.length
  const text = STREAMING_NARRATION.slice(0, length)
  return {
    ...board,
    currentResponseEntry: done
      ? { ...current, text, streaming: false, metadata: { ...current.metadata, persistence_status: 'saved' } }
      : { ...current, text },
    latestDmText: text,
    sendPending: !done,
    streamingTurnActive: !done,
  }
}

export function StagePreview() {
  const [board, setBoard] = useState(() => {
    const fixture = makeBoardFixture(previewState)
    // The streamed reply arrives a moment after the action, as it does at the table.
    return previewState === 'streaming' ? { ...fixture, currentResponseEntry: null } : fixture
  })

  useEffect(() => {
    if (previewState !== 'streaming') return
    const reply = makeBoardFixture('streaming').currentResponseEntry
    if (!reply) return
    const timer = window.setTimeout(() => {
      setBoard((current) => ({ ...current, currentResponseEntry: { ...reply, text: '' } }))
    }, 1800)
    return () => window.clearTimeout(timer)
  }, [])
  const [inspector] = useState(makeInspectorFixture)
  const [drawerOpen, setDrawerOpen] = useState(initialDrawer !== null)
  const [drawerTab, setDrawerTab] = useState<StoryDrawerTab>(initialDrawer ?? 'character')

  const streaming = board.currentResponseEntry?.streaming === true
  useEffect(() => {
    if (!streaming) return
    const timer = window.setInterval(() => {
      const chunk = 3 + Math.round(Math.random() * 9)
      setBoard((current) => streamTo(current, (current.currentResponseEntry?.text.length ?? 0) + chunk))
    }, 70)
    return () => window.clearInterval(timer)
  }, [streaming])

  useEffect(() => {
    if (previewState !== 'combat') return
    const timer = window.setTimeout(() => {
      setBoard((current) => ({
        ...current,
        activePlayers: current.activePlayers.map((player) =>
          player.id === current.playerId && player.health
            ? { ...player, health: { ...player.health, currentHp: 12, tone: 'badly-wounded', label: 'Badly wounded' } }
            : player,
        ),
      }))
    }, 1400)
    return () => window.clearTimeout(timer)
  }, [])

  const chrome: StoryChromeProps = {
    connection,
    safeMode: params.get('safe') === '1',
    canUseOperatorTools: params.get('operator') === '1',
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
    onStartNextChapter: noop,
    endedChapter: previewState === 'ended' ? { onContinue: noop } : null,
  }

  return (
    <StoryView
      board={board}
      inspector={inspector}
      chrome={chrome}
      composer={<div style={placeholderComposer}>composer</div>}
      drawer={<div style={placeholderDrawer} />}
      drawerOpen={drawerOpen}
      drawerTab={drawerTab}
      onDrawerOpenChange={(open, tab) => {
        setDrawerOpen(open)
        if (tab) setDrawerTab(tab)
      }}
    />
  )
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <StagePreview />
  </StrictMode>,
)
