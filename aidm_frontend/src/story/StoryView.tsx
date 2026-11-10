import { useEffect, useMemo, useState } from 'react'
import { partyVitalityFromSnapshot } from '../gameSelectors'
import { StoryAtmosphere } from './StoryAtmosphere'
import { StoryFeed, type StoryTextSize } from './StoryFeed'
import { storyMood } from './storyMood'
import { StoryPartyHud } from './StoryPartyHud'
import { StoryTopBar } from './StoryTopBar'
import type { StoryDrawerTab, StoryViewProps } from './types'

/** Shared with the classic console's reading controls. */
const CHAT_TEXT_SETTINGS_STORAGE_KEY = 'aidm:chatTextSettings'

const DRAWER_HOTKEYS: Record<string, StoryDrawerTab> = {
  c: 'character',
  i: 'pack',
  g: 'magic',
  j: 'journal',
  m: 'map',
}

function isTextSize(value: unknown): value is StoryTextSize {
  return value === 'default' || value === 'large' || value === 'extra'
}

function readTextSettings(): Record<string, unknown> {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(CHAT_TEXT_SETTINGS_STORAGE_KEY) ?? 'null')
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? (parsed as Record<string, unknown>) : {}
  } catch {
    return {}
  }
}

function loadTextSize(): StoryTextSize {
  const { size } = readTextSettings()
  return isTextSize(size) ? size : 'default'
}

function saveTextSize(size: StoryTextSize) {
  try {
    const current = readTextSettings()
    localStorage.setItem(
      CHAT_TEXT_SETTINGS_STORAGE_KEY,
      JSON.stringify({ font: 'default', ...current, size }),
    )
  } catch {
    // The choice still applies to this page when storage is unavailable.
  }
}

function typingInField(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  return Boolean(target.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]'))
}

function modalDialogOpen() {
  return Boolean(document.querySelector('[role="dialog"][aria-modal="true"], [role="alertdialog"][aria-modal="true"], dialog[open]'))
}

/**
 * Story mode: the default play screen. A single centred story column under a
 * floating top bar, the composer docked below it, the drawer on the right, all
 * over a living, mood-lit background.
 */
export function StoryView({
  board,
  inspector,
  chrome,
  composer,
  drawer,
  drawerOpen,
  drawerTab,
  onDrawerOpenChange,
}: StoryViewProps) {
  const [textSize, setTextSize] = useState<StoryTextSize>(loadTextSize)
  const mood = storyMood(board.worldState, board.sceneState, board.combatState)
  const viewerPlayerId = board.selectedPlayer?.player_id ?? board.playerId ?? inspector.selectedPlayerId
  const viewer = board.activePlayers.find((player) => player.id === viewerPlayerId)
  const snapshot = board.sessionState?.state_snapshot
  const vitality = useMemo(() => partyVitalityFromSnapshot(snapshot), [snapshot])

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.metaKey || event.ctrlKey || event.altKey) return
      if (typingInField(event.target) || modalDialogOpen()) return
      if (event.key === 'Escape') {
        if (drawerOpen) {
          event.preventDefault()
          onDrawerOpenChange(false)
        }
        return
      }
      const tab = DRAWER_HOTKEYS[event.key.toLowerCase()]
      if (!tab || event.shiftKey) return
      event.preventDefault()
      onDrawerOpenChange(!(drawerOpen && drawerTab === tab), tab)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [drawerOpen, drawerTab, onDrawerOpenChange])

  const changeTextSize = (size: StoryTextSize) => {
    setTextSize(size)
    saveTextSize(size)
  }

  return (
    <div
      className="story-shell"
      data-mood={mood}
      data-phase={board.worldState.clock?.phase}
      data-drawer-open={drawerOpen ? '' : undefined}
    >
      <StoryAtmosphere mood={mood} viewerHp={viewer?.health?.currentHp ?? null} />
      <h1 className="story-sr-only">{board.activeSessionTitle}</h1>
      <div className="story-stage">
        <StoryTopBar
          board={board}
          chrome={chrome}
          drawerOpen={drawerOpen}
          drawerTab={drawerTab}
          onDrawerOpenChange={onDrawerOpenChange}
          textSize={textSize}
          onTextSizeChange={changeTextSize}
        />
        <main className="story-main" aria-label="Story">
          <StoryFeed board={board} textSize={textSize} onExitToTitle={chrome.onExitToTitle} />
          <div className="story-composer-slot">{composer}</div>
        </main>
        <aside
          className="story-drawer-slot"
          aria-label="Character and journal"
          data-open={drawerOpen ? '' : undefined}
          inert={!drawerOpen}
        >
          {drawer}
        </aside>
      </div>
      <StoryPartyHud
        activePlayers={board.activePlayers}
        viewerPlayerId={viewerPlayerId}
        vitality={vitality}
        onDrawerOpenChange={onDrawerOpenChange}
      />
    </div>
  )
}
