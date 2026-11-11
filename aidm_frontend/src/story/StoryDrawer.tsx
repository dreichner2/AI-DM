import { lazy, Suspense, useEffect, useMemo, useRef, useState, useSyncExternalStore, type KeyboardEvent } from 'react'
import { X } from 'lucide-react'
import { worldMapFromSnapshot, type WorldZoneSummary } from '../gameSelectors'
import { subscribeToMediaQueryChange } from '../mediaQuery'
import { useModalFocusTrap } from '../useModalFocusTrap'
import { travelSubmission, zoneMoveSubmission } from './actionIntents'
import { heroHasSpells } from './heroSpells'
import { drawerSectionChunks } from './storyChunks'
import { travelBlockedReason as travelBlocked } from './travelBlock'
import type { StoryDrawerProps, StoryDrawerTab } from './types'

const CharacterSection = drawerSectionChunks.character.component((module) => module.CharacterSection)
const PackSection = drawerSectionChunks.pack.component((module) => module.PackSection)
const MagicSection = drawerSectionChunks.magic.component((module) => module.MagicSection)
const JournalSection = drawerSectionChunks.journal.component((module) => module.JournalSection)
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
  const hasSpells = heroHasSpells(board, inspector)
  const tabs = DRAWER_TABS.filter((candidate) => candidate.id !== 'magic' || hasSpells)
  // A tab this hero does not have (Magic without spells) shows the character instead.
  const shownTab = tabs.some((candidate) => candidate.id === tab) ? tab : 'character'

  if (open && !rendered) setRendered(true)

  useEffect(() => {
    if (open || !rendered) return undefined
    const timer = window.setTimeout(() => setRendered(false), CLOSE_ANIMATION_MS)
    return () => window.clearTimeout(timer)
  }, [open, rendered])

  const tabRef = useRef(shownTab)
  useEffect(() => {
    tabRef.current = shownTab
  }, [shownTab])

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

  const travelBlockedReason = travelBlocked(board)
  const travel = (exit: { id: string; name: string }) => {
    const { message, intent } = travelSubmission(exit)
    if (board.actionComposerProps.submitAction(message, intent) && modal) onClose()
  }
  const moveInto = (zone: WorldZoneSummary) => {
    const { message, intent } = zoneMoveSubmission(zone)
    if (board.actionComposerProps.submitAction(message, intent) && modal) onClose()
  }

  const handleTabKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = tabs.findIndex((candidate) => candidate.id === shownTab)
    let next: number | null = null
    if (event.key === 'ArrowRight') next = (index + 1) % tabs.length
    else if (event.key === 'ArrowLeft') next = (index - 1 + tabs.length) % tabs.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = tabs.length - 1
    if (next === null) return
    event.preventDefault()
    const nextTab = tabs[next].id
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
            {tabs.map((candidate) => {
              const selected = candidate.id === shownTab
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
          id={drawerPanelId(shownTab)}
          className="story-drawer-panel"
          role="tabpanel"
          aria-labelledby={drawerTabId(shownTab)}
          tabIndex={0}
        >
          <Suspense fallback={null}>
            {shownTab === 'character' ? <CharacterSection board={board} inspector={inspector} withTraits={!hasSpells} /> : null}
            {shownTab === 'pack' ? <PackSection inspector={inspector} /> : null}
            {shownTab === 'magic' ? <MagicSection board={board} inspector={inspector} /> : null}
            {shownTab === 'journal' ? <JournalSection board={board} inspector={inspector} /> : null}
          </Suspense>
          {shownTab === 'map' ? (
            <Suspense fallback={<p className="story-sheet-meta">Loading map…</p>}>
              <MapSection
                inspector={inspector}
                worldMap={worldMap}
                travelBlockedReason={travelBlockedReason}
                onTravel={travel}
                viewerId={board.actionComposerProps.selectedPlayerId ? `player_${board.actionComposerProps.selectedPlayerId}` : ''}
                canMove={!board.worldState.combat.active && !board.sendPending && !board.streamingTurnActive}
                onMove={moveInto}
              />
            </Suspense>
          ) : null}
        </div>
      </section>
    </>
  )
}
