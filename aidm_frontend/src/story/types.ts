import type { ReactNode } from 'react'
import type { InspectorPanelProps } from '../InspectorPanel'
import type { SessionBoardProps } from '../SessionBoard'

/**
 * Story mode contract.
 *
 * Story mode is a second presentation of the same session data the classic
 * console shows. It receives exactly the prop objects App already builds for
 * SessionBoard and InspectorPanel, plus a small set of chrome callbacks, so the
 * two views can never disagree about game state.
 */

/** One truthful line about whether the table can be played right now. */
export type StoryConnection = 'ready' | 'connecting' | 'reconnecting' | 'offline'

export type StoryDrawerTab = 'character' | 'pack' | 'magic' | 'journal' | 'map'

export type StoryChromeProps = {
  connection: StoryConnection
  /** True when the scripted narrator (no AI provider) is running the table. */
  safeMode: boolean
  canUseOperatorTools: boolean
  /** Narration audio. `ttsAvailable` false hides the control entirely. */
  ttsAvailable: boolean
  ttsEnabled: boolean
  onToggleTts: () => void
  isFullscreen: boolean
  onToggleFullscreen: () => void
  /** Opens the classic three-column console (campaign tools, director, diagnostics). */
  onOpenConsole: () => void
  /** Opens the campaign chooser ("Switch adventure"). */
  onSwitchAdventure: () => void
  /** Returns to the title screen. */
  onExitToTitle: () => void
  /** Opens the Settings sheet. */
  onOpenSettings: () => void
  /** Opens the Account sheet. Absent at a local table nobody signs in to, which has no Account item. */
  onOpenAccount?: () => void
  /** Starts the next chapter of this adventure. Absent when the viewer may not start sessions. */
  onStartNextChapter?: () => void
  /**
   * Set when the open session is an ended chapter (read-only). `onContinue`
   * moves to the adventure's current chapter.
   */
  endedChapter?: { onContinue: () => void } | null
}

export type StoryViewProps = {
  board: SessionBoardProps
  inspector: InspectorPanelProps
  chrome: StoryChromeProps
  /** The composer dock (StoryComposer). Rendered at the bottom of the story column. */
  composer: ReactNode
  /** The character drawer (StoryDrawer). StoryView owns where it sits, not what it shows. */
  drawer: ReactNode
  drawerOpen: boolean
  drawerTab: StoryDrawerTab
  onDrawerOpenChange: (open: boolean, tab?: StoryDrawerTab) => void
}

export type StoryComposerProps = {
  board: SessionBoardProps
  chrome: Pick<StoryChromeProps, 'connection' | 'endedChapter' | 'canUseOperatorTools'> &
    Partial<Pick<StoryChromeProps, 'onOpenConsole'>>
  onOpenDrawer: (tab: StoryDrawerTab) => void
}

export type StoryDrawerProps = {
  board: SessionBoardProps
  inspector: InspectorPanelProps
  open: boolean
  tab: StoryDrawerTab
  onTabChange: (tab: StoryDrawerTab) => void
  onClose: () => void
  /** Fills the composer with a travel/talk/inspect action chosen from the drawer. */
  onSuggestAction?: (text: string) => void
}
