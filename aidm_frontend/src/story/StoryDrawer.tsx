import { lazy, Suspense, useEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent } from 'react'
import { X } from 'lucide-react'
import { worldMapFromSnapshot } from '../gameSelectors'
import { subscribeToMediaQueryChange } from '../mediaQuery'
import { useModalFocusTrap } from '../useModalFocusTrap'
import { travelSubmission } from './actionIntents'
import { CharacterSection } from './drawer/CharacterSection'
import { JournalSection } from './drawer/JournalSection'
import { MagicSection } from './drawer/MagicSection'
import { PackSection } from './drawer/PackSection'
import type { StoryDrawerProps, StoryDrawerTab } from './types'

const MapSection = lazy(() => import('./drawer/MapSection').then(({ MapSection: Component }) => ({ default: Component })))

/** Below this width the drawer overlays the story instead of sitting beside it. */
const MODAL_DRAWER_QUERY = '(max-width: 1179px)'
const CLOSE_ANIMATION_MS = 180

const DRAWER_TABS: ReadonlyArray<{ id: StoryDrawerTab; label: string }> = [
  { id: 'character', label: 'Character' },
  { id: 'pack', label: 'Pack' },
  { id: 'magic', label: 'Magic' },
  { id: 'journal', label: 'Journal' },
  { id: 'map', label: 'Map' },
]

function drawerTabId(tab: StoryDrawerTab) {
  return `story-drawer-tab-${tab}`
}

function drawerPanelId(tab: StoryDrawerTab) {
  return `story-drawer-panel-${tab}`
}

function subscribeToModalQuery(listener: () => void) {
  if (typeof window.matchMedia !== 'function') return () => undefined
  return subscribeToMediaQueryChange(window.matchMedia(MODAL_DRAWER_QUERY), listener)
}

function modalQueryMatches() {
  return typeof window.matchMedia === 'function' && window.matchMedia(MODAL_DRAWER_QUERY).matches
}

/**
 * The character, pack, magic, journal and map sheet. Beside the story on wide
 * screens; a focus-trapping overlay on narrower ones.
 */
export function StoryDrawer({ board, inspector, open, tab, onTabChange, onClose }: StoryDrawerProps) {
  const worldMap = useMemo(() => worldMapFromSnapshot(board.sessionState?.state_snapshot), [board.sessionState?.state_snapshot])
  const modal = useSyncExternalStore(subscribeToModalQuery, modalQueryMatches, () => false)
  const [rendered, setRendered] = useState(open)
  const sheetRef = useRef<HTMLElement | null>(null)
  const returnFocusRef = useRef<HTMLElement | null>(null)

  if (open && !rendered) setRendered(true)

  useEffect(() => {
    if (open || !rendered) return undefined
    const timer = window.setTimeout(() => setRendered(false), CLOSE_ANIMATION_MS)
    return () => window.clearTimeout(timer)
  }, [open, rendered])

  const tabRef = useRef(tab)
  useEffect(() => {
    tabRef.current = tab
  }, [tab])

  // Focus moves to the selected tab when the sheet opens (the modal trap does
  // the same for the overlay); later tab changes manage their own focus.
  useEffect(() => {
    if (open && !modal) document.getElementById(drawerTabId(tabRef.current))?.focus()
  }, [open, modal])

  useModalFocusTrap({
    activeKey: open && modal ? 'story-drawer' : null,
    dialogRef: sheetRef,
    onClose,
    returnFocusRef,
  })

  if (!rendered) return null

  const composer = board.actionComposerProps
  const travelBlockedReason = board.worldState.combat.active
    ? 'Not while combat is underway.'
    : board.sendPending || board.streamingTurnActive
      ? 'Wait for the Dungeon Master to finish.'
      : !composer.selectedPlayerId || !composer.selectedCharacterName
        ? 'Choose a character first.'
        : ''
  const travel = (exit: { id: string; name: string }) => {
    const { message, intent } = travelSubmission(exit)
    if (composer.submitAction(message, intent) && modal) onClose()
  }

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = DRAWER_TABS.findIndex((candidate) => candidate.id === tab)
    let next: number | null = null
    if (event.key === 'ArrowRight') next = (index + 1) % DRAWER_TABS.length
    else if (event.key === 'ArrowLeft') next = (index - 1 + DRAWER_TABS.length) % DRAWER_TABS.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = DRAWER_TABS.length - 1
    if (next === null) return
    event.preventDefault()
    const nextTab = DRAWER_TABS[next].id
    onTabChange(nextTab)
    document.getElementById(drawerTabId(nextTab))?.focus()
  }

  const handleSheetKeyDown = (event: KeyboardEvent<HTMLElement>) => {
    if (modal || event.key !== 'Escape') return
    event.preventDefault()
    onClose()
  }

  return (
    <>
      {modal ? (
        <div
          className="story-drawer-backdrop"
          data-closing={open ? undefined : true}
          aria-hidden="true"
          onClick={onClose}
        />
      ) : null}
      <section
        ref={sheetRef}
        className="story-drawer"
        data-modal={modal || undefined}
        data-closing={open ? undefined : true}
        role={modal ? 'dialog' : 'complementary'}
        aria-modal={modal || undefined}
        aria-label="Character panel"
        inert={open ? undefined : true}
        onKeyDown={handleSheetKeyDown}
      >
        <header className="story-drawer-header">
          <div className="story-drawer-tabs" role="tablist" aria-label="Character panel sections">
            {DRAWER_TABS.map((candidate) => {
              const selected = candidate.id === tab
              return (
                <button
                  key={candidate.id}
                  id={drawerTabId(candidate.id)}
                  type="button"
                  role="tab"
                  className="story-link"
                  aria-selected={selected}
                  aria-controls={drawerPanelId(candidate.id)}
                  tabIndex={selected ? 0 : -1}
                  data-autofocus={selected || undefined}
                  onClick={() => onTabChange(candidate.id)}
                  onKeyDown={handleTabKeyDown}
                >
                  {candidate.label}
                </button>
              )
            })}
          </div>
          <button
            type="button"
            className="story-icon-button story-drawer-close"
            aria-label="Close character panel"
            onClick={onClose}
          >
            <X size={20} strokeWidth={1.6} aria-hidden="true" />
          </button>
        </header>
        <div
          id={drawerPanelId(tab)}
          className="story-drawer-panel"
          role="tabpanel"
          aria-labelledby={drawerTabId(tab)}
          tabIndex={0}
        >
          {tab === 'character' ? <CharacterSection board={board} inspector={inspector} /> : null}
          {tab === 'pack' ? <PackSection inspector={inspector} /> : null}
          {tab === 'magic' ? <MagicSection board={board} inspector={inspector} /> : null}
          {tab === 'journal' ? <JournalSection board={board} inspector={inspector} /> : null}
          {tab === 'map' ? (
            <Suspense fallback={<p className="story-sheet-meta">Loading map…</p>}>
              <MapSection inspector={inspector} worldMap={worldMap} travelBlockedReason={travelBlockedReason} onTravel={travel} />
            </Suspense>
          ) : null}
        </div>
      </section>
    </>
  )
}
