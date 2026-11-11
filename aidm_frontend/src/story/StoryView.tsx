import { useEffect, useMemo } from 'react'
import { whenIdle } from '../lazyModule'
import { heroHasSpells } from './heroSpells'
import { setStoryTextSize, useStoryPreferences } from './sheets/storyPreferences'
import { StoryAtmosphere } from './StoryAtmosphere'
import { preloadStoryDrawer } from './storyChunks'
import { StoryFeed } from './StoryFeed'
import { storyMood } from './storyMood'
import { StoryPartyHud } from './StoryPartyHud'
import { StoryTopBar } from './StoryTopBar'
import type { StoryDrawerTab, StoryViewProps } from './types'
import { partyWithViewer, viewerHero } from './viewerHero'
import { partyHeroVitality } from './vitalityLabels'

const DRAWER_HOTKEYS: Record<string, StoryDrawerTab> = {
  c: 'character',
  i: 'pack',
  g: 'magic',
  j: 'journal',
  m: 'map',
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
  // Reading and motion settings apply the moment they change, from the Settings sheet or another tab.
  const { textSize, typeface, reduceMotion } = useStoryPreferences()
  const mood = storyMood(board.worldState, board.sceneState, board.combatState)
  const viewerPlayerId = board.selectedPlayer?.player_id ?? board.playerId ?? inspector.selectedPlayerId
  const viewer = viewerHero(board, inspector, viewerPlayerId)
  const party = partyWithViewer(board.activePlayers, viewer)
  const snapshot = board.sessionState?.state_snapshot
  const vitality = useMemo(() => partyHeroVitality(snapshot), [snapshot])
  // Dying, stable, unconscious or out of the fight: the hero is down whatever their hit points say.
  const viewerDown =
    viewerPlayerId !== null && (Boolean(vitality[viewerPlayerId]) || (viewer?.health ? viewer.health.currentHp <= 0 : false))
  const hasSpells = heroHasSpells(board, inspector)

  // The drawer's code arrives once the first story has painted, before anyone reaches for it.
  useEffect(() => whenIdle(preloadStoryDrawer), [])

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
      // A hero without spells has no Magic tab.
      if (!tab || event.shiftKey || (tab === 'magic' && !hasSpells)) return
      event.preventDefault()
      preloadStoryDrawer()
      onDrawerOpenChange(!(drawerOpen && drawerTab === tab), tab)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [drawerOpen, drawerTab, hasSpells, onDrawerOpenChange])

  return (
    <div
      className="story-shell"
      data-mood={mood}
      data-phase={board.worldState.clock?.phase}
      data-drawer-open={drawerOpen ? '' : undefined}
      data-typeface={typeface}
      data-reduce-motion={reduceMotion ? '' : undefined}
    >
      <StoryAtmosphere mood={mood} viewerHp={viewer?.health?.currentHp ?? null} />
      <h1 className="story-sr-only">{board.activeSession ? board.activeSessionTitle : board.campaign?.title}</h1>
      <div className="story-stage">
        <StoryTopBar
          board={board}
          chrome={chrome}
          hero={viewer}
          heroDown={viewerDown}
          drawerOpen={drawerOpen}
          drawerTab={drawerTab}
          onDrawerOpenChange={onDrawerOpenChange}
          textSize={textSize}
          onTextSizeChange={setStoryTextSize}
        />
        <main className="story-main" aria-label="Story">
          <StoryFeed
            board={board}
            textSize={textSize}
            onExitToTitle={chrome.onExitToTitle}
            onOpenBeatTarget={(target) => {
              if (target === 'settings') chrome.onOpenSettings()
              else onDrawerOpenChange(true, target)
            }}
          />
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
        activePlayers={party}
        viewerPlayerId={viewerPlayerId}
        vitality={vitality}
        onDrawerOpenChange={onDrawerOpenChange}
      />
    </div>
  )
}
