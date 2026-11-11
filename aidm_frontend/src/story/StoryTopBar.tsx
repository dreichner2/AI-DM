import {
  lazy,
  Suspense,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
} from 'react'
import {
  ArrowLeftRight,
  BookPlus,
  Download,
  LayoutPanelLeft,
  LogOut,
  Maximize2,
  Menu,
  Minimize2,
  Music,
  Play,
  ScrollText,
  Settings,
  Share2,
  Type,
  UserRound,
  Volume2,
  VolumeX,
  type LucideIcon,
} from 'lucide-react'
import type { SessionBoardProps } from '../SessionBoard'
import { EmberMark } from './glyphs'
import type { StoryTextSize } from './sheets/storyPreferences'
import type { StoryChromeProps, StoryDrawerTab } from './types'

const SceneMusicPlayer = lazy(() =>
  import('../SceneMusicPlayer').then((module) => ({ default: module.SceneMusicPlayer })),
)

const SAFE_MODE_TITLE = 'No AI provider is connected. The built-in narrator is running the adventure.'
const TEXT_SIZES: ReadonlyArray<[StoryTextSize, string]> = [
  ['default', 'Default'],
  ['large', 'Large'],
  ['extra', 'Extra'],
]
const CONNECTION_LABELS: Record<StoryChromeProps['connection'], string> = {
  ready: '',
  connecting: 'Connecting…',
  reconnecting: 'Reconnecting…',
  offline: 'Offline',
}

type StoryTopBarProps = {
  board: SessionBoardProps
  chrome: StoryChromeProps
  drawerOpen: boolean
  drawerTab: StoryDrawerTab
  onDrawerOpenChange: (open: boolean, tab?: StoryDrawerTab) => void
  textSize: StoryTextSize
  onTextSizeChange: (size: StoryTextSize) => void
}

type MenuEntry =
  | { kind: 'item'; id: string; label: string; icon: LucideIcon; onSelect: () => void }
  | { kind: 'separator'; id: string }
  | { kind: 'text-size'; id: string }

function menuItems(menu: HTMLElement | null) {
  return menu ? [...menu.querySelectorAll<HTMLElement>('[role="menuitem"], [role="menuitemradio"]')] : []
}

/** The scene name, cross-fading to a new name when the party moves on. */
function SceneName({ name }: { name: string }) {
  const [shown, setShown] = useState({ name, previous: '', version: 0 })
  if (shown.name !== name) {
    setShown({ name, previous: shown.name, version: shown.version + 1 })
  }
  return (
    <span className="story-scene-name">
      {shown.previous ? (
        <span key={`previous-${shown.version}`} className="story-scene-name-text" data-leaving="" aria-hidden="true">
          {shown.previous}
        </span>
      ) : null}
      <span key={`current-${shown.version}`} className="story-scene-name-text" data-entering={shown.version ? '' : undefined}>
        {shown.name}
      </span>
    </span>
  )
}

function IconButton({
  label,
  title = label,
  onClick,
  pressed,
  expanded,
  className = '',
  children,
}: {
  label: string
  title?: string
  onClick: () => void
  pressed?: boolean
  expanded?: boolean
  className?: string
  children: ReactNode
}) {
  return (
    <button
      type="button"
      className={`story-icon-button ${className}`.trim()}
      aria-label={label}
      title={title}
      aria-pressed={pressed}
      aria-expanded={expanded}
      onClick={onClick}
    >
      {children}
    </button>
  )
}

export function StoryTopBar({
  board,
  chrome,
  drawerOpen,
  drawerTab,
  onDrawerOpenChange,
  textSize,
  onTextSizeChange,
}: StoryTopBarProps) {
  const [menuOpen, setMenuOpen] = useState(false)
  const [musicOpen, setMusicOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement | null>(null)
  const menuTriggerRef = useRef<HTMLButtonElement | null>(null)
  const musicRef = useRef<HTMLDivElement | null>(null)
  const sceneName =
    board.sceneState?.locationName?.trim() || board.worldState.sceneName?.trim() || board.activeSessionTitle
  const characterOpen = drawerOpen && drawerTab === 'character'
  const clockLabel = board.worldState.clock?.label ?? ''

  useEffect(() => {
    if (!menuOpen) return
    menuItems(menuRef.current)[0]?.focus()
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target as Node | null
      if (target && (menuRef.current?.contains(target) || menuTriggerRef.current?.contains(target))) return
      setMenuOpen(false)
    }
    document.addEventListener('pointerdown', closeOnOutside)
    return () => document.removeEventListener('pointerdown', closeOnOutside)
  }, [menuOpen])

  useEffect(() => {
    if (!musicOpen) return
    const closeOnOutside = (event: PointerEvent) => {
      const target = event.target as Node | null
      if (target && (musicRef.current?.contains(target) || (target as Element).closest?.('[data-music-toggle]'))) return
      setMusicOpen(false)
    }
    document.addEventListener('pointerdown', closeOnOutside)
    return () => document.removeEventListener('pointerdown', closeOnOutside)
  }, [musicOpen])

  const toggleMenu = (trigger: HTMLButtonElement) => {
    menuTriggerRef.current = trigger
    setMusicOpen(false)
    setMenuOpen((open) => !open)
  }

  const closeMenu = (returnFocus: boolean) => {
    setMenuOpen(false)
    if (returnFocus) menuTriggerRef.current?.focus()
  }

  const select = (action: () => void) => () => {
    closeMenu(true)
    action()
  }

  const entries: MenuEntry[] = [
    ...(chrome.endedChapter
      ? [{ kind: 'item', id: 'continue', label: 'Continue the adventure', icon: Play, onSelect: chrome.endedChapter.onContinue } as const]
      : []),
    ...(chrome.onStartNextChapter
      ? [{ kind: 'item', id: 'next-chapter', label: 'Start next chapter', icon: BookPlus, onSelect: chrome.onStartNextChapter } as const]
      : []),
    { kind: 'item', id: 'switch', label: 'Switch adventure', icon: ArrowLeftRight, onSelect: chrome.onSwitchAdventure },
    { kind: 'separator', id: 'sep-1' },
    {
      kind: 'item',
      id: 'chronicle',
      label: 'Download Chronicle',
      icon: ScrollText,
      onSelect: () => void board.downloadSessionChronicle(),
    },
    { kind: 'item', id: 'share', label: 'Share', icon: Share2, onSelect: board.shareSession },
    { kind: 'item', id: 'export', label: 'Export save', icon: Download, onSelect: () => void board.downloadSessionJson() },
    { kind: 'separator', id: 'sep-2' },
    { kind: 'text-size', id: 'text-size' },
    {
      kind: 'item',
      id: 'fullscreen',
      label: chrome.isFullscreen ? 'Exit fullscreen' : 'Fullscreen',
      icon: chrome.isFullscreen ? Minimize2 : Maximize2,
      onSelect: chrome.onToggleFullscreen,
    },
    { kind: 'item', id: 'settings', label: 'Settings', icon: Settings, onSelect: chrome.onOpenSettings },
    ...(chrome.onOpenAccount
      ? [{ kind: 'item', id: 'account', label: 'Account', icon: UserRound, onSelect: chrome.onOpenAccount } as const]
      : []),
    { kind: 'separator', id: 'sep-3' },
    {
      kind: 'item',
      id: 'console',
      label: chrome.canUseOperatorTools ? 'Table tools' : 'Classic view',
      icon: LayoutPanelLeft,
      onSelect: chrome.onOpenConsole,
    },
    { kind: 'item', id: 'exit', label: 'Exit to title', icon: LogOut, onSelect: chrome.onExitToTitle },
  ]

  const onMenuKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    const items = menuItems(menuRef.current)
    const index = items.indexOf(document.activeElement as HTMLElement)
    const focusAt = (next: number) => items[(next + items.length) % items.length]?.focus()
    switch (event.key) {
      case 'ArrowDown':
        event.preventDefault()
        focusAt(index + 1)
        break
      case 'ArrowUp':
        event.preventDefault()
        focusAt(index - 1)
        break
      case 'Home':
        event.preventDefault()
        focusAt(0)
        break
      case 'End':
        event.preventDefault()
        focusAt(items.length - 1)
        break
      case 'Escape':
        event.preventDefault()
        event.stopPropagation()
        closeMenu(true)
        break
      case 'Tab':
        setMenuOpen(false)
        break
    }
  }

  const connectionLabel = CONNECTION_LABELS[chrome.connection]

  return (
    <header className="story-topbar">
      <div className="story-topbar-lead">
        <button
          type="button"
          className="story-mark"
          aria-label="Adventure menu"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={(event) => toggleMenu(event.currentTarget)}
        >
          <EmberMark size={24} />
        </button>
        <div className="story-titles">
          <span className="story-label story-campaign-title">{board.campaignTitle || board.activeSessionTitle}</span>
          <span className="story-scene-line">
            <SceneName name={sceneName} />
            {clockLabel ? <span className="story-clock">{clockLabel}</span> : null}
          </span>
        </div>
      </div>

      <div className="story-topbar-actions">
        <span className="story-connection" role="status" data-connection={chrome.connection}>
          {connectionLabel}
          {chrome.connection === 'connecting' || chrome.connection === 'reconnecting' ? (
            <span className="story-dots" aria-hidden="true">
              <i />
              <i />
              <i />
            </span>
          ) : null}
        </span>
        {chrome.safeMode ? (
          <span className="story-label story-safe-mode" title={SAFE_MODE_TITLE}>
            Scripted narrator
          </span>
        ) : null}
        {chrome.ttsAvailable ? (
          <IconButton
            className="story-topbar-optional"
            label="Narration"
            title={chrome.ttsEnabled ? 'Narration on' : 'Narration off'}
            pressed={chrome.ttsEnabled}
            onClick={chrome.onToggleTts}
          >
            {chrome.ttsEnabled ? <Volume2 size={19} strokeWidth={1.6} /> : <VolumeX size={19} strokeWidth={1.6} />}
          </IconButton>
        ) : null}
        {board.showSceneMusicPlayer ? (
          <span className="story-topbar-optional story-music" data-music-toggle="">
            <IconButton
              label="Scene music"
              expanded={musicOpen}
              onClick={() => {
                setMenuOpen(false)
                setMusicOpen((open) => !open)
              }}
            >
              <Music size={18} strokeWidth={1.6} />
            </IconButton>
          </span>
        ) : null}
        <IconButton
          label={characterOpen ? 'Close character panel' : 'Open character panel'}
          expanded={characterOpen}
          onClick={() => onDrawerOpenChange(!characterOpen, 'character')}
        >
          <UserRound size={19} strokeWidth={1.6} />
        </IconButton>
        <button
          type="button"
          className="story-icon-button"
          aria-label="Adventure menu"
          title="Adventure menu"
          aria-haspopup="menu"
          aria-expanded={menuOpen}
          onClick={(event) => toggleMenu(event.currentTarget)}
        >
          <Menu size={19} strokeWidth={1.6} />
        </button>
      </div>

      {board.showSceneMusicPlayer ? (
        <div ref={musicRef} className="story-popover story-music-popover" hidden={!musicOpen}>
          <Suspense fallback={null}>
            <SceneMusicPlayer
              sessionId={board.sessionId}
              playerId={board.playerId}
              duckForNarration={board.duckMusicForNarration}
              musicSyncState={board.sceneMusicSyncState}
              sceneState={board.sceneState}
              autoFollowScene
              onMusicControl={board.onSceneMusicControl}
            />
          </Suspense>
        </div>
      ) : null}

      {menuOpen ? (
        <div ref={menuRef} className="story-popover story-menu" role="menu" aria-label="Adventure menu" onKeyDown={onMenuKeyDown}>
          {entries.map((entry) => {
            if (entry.kind === 'separator') {
              return <div key={entry.id} className="story-menu-separator" role="separator" />
            }
            if (entry.kind === 'text-size') {
              return (
                <div key={entry.id} className="story-menu-row" role="group" aria-label="Text size">
                  <span className="story-menu-row-label">
                    <Type size={16} strokeWidth={1.6} aria-hidden="true" />
                    Text size
                  </span>
                  <span className="story-menu-choices">
                    {TEXT_SIZES.map(([size, label]) => (
                      <button
                        key={size}
                        type="button"
                        role="menuitemradio"
                        className="story-link story-menu-choice"
                        aria-checked={textSize === size}
                        tabIndex={-1}
                        onClick={() => onTextSizeChange(size)}
                      >
                        {label}
                      </button>
                    ))}
                  </span>
                </div>
              )
            }
            const Icon = entry.icon
            return (
              <button
                key={entry.id}
                type="button"
                role="menuitem"
                className="story-menu-item"
                tabIndex={-1}
                onClick={select(entry.onSelect)}
              >
                <Icon size={16} strokeWidth={1.6} aria-hidden="true" />
                {entry.label}
              </button>
            )
          })}
        </div>
      ) : null}
    </header>
  )
}
