/// <reference types="node" />
// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { UNRECORDED_SCENE_NAME } from '../gameSelectors'
import type { SessionBoardProps } from '../SessionBoard'
import type { ActivePlayer } from '../types'
import { makeBoardFixture } from './preview/fixtures'
import { accountSheetChunk, drawerChunk, sessionDialogsChunk, settingsSheetChunk } from './storyChunks'
import { StoryTopBar } from './StoryTopBar'
import type { StoryChromeProps, StoryDrawerTab } from './types'

function chrome(overrides: Partial<StoryChromeProps> = {}): StoryChromeProps {
  return {
    connection: 'ready',
    safeMode: false,
    canUseOperatorTools: false,
    ttsAvailable: true,
    ttsEnabled: false,
    onToggleTts: vi.fn(),
    isFullscreen: false,
    onToggleFullscreen: vi.fn(),
    onOpenConsole: vi.fn(),
    onSwitchAdventure: vi.fn(),
    onExitToTitle: vi.fn(),
    onOpenSettings: vi.fn(),
    onOpenAccount: vi.fn(),
    ...overrides,
  }
}

function renderTopBar({
  chromeProps = {},
  board = makeBoardFixture('exploration'),
  onTextSizeChange = vi.fn(),
  onDrawerOpenChange = vi.fn(),
  drawerOpen = false,
  hero = null,
  heroDown = false,
}: {
  chromeProps?: Partial<StoryChromeProps>
  board?: SessionBoardProps
  onTextSizeChange?: (size: 'default' | 'large' | 'extra') => void
  onDrawerOpenChange?: (open: boolean, tab?: StoryDrawerTab) => void
  drawerOpen?: boolean
  hero?: ActivePlayer | null
  heroDown?: boolean
} = {}) {
  return render(
    <StoryTopBar
      board={board}
      chrome={chrome(chromeProps)}
      hero={hero}
      heroDown={heroDown}
      drawerOpen={drawerOpen}
      drawerTab="character"
      onDrawerOpenChange={onDrawerOpenChange}
      textSize="default"
      onTextSizeChange={onTextSizeChange}
    />,
  )
}

function openMenu() {
  fireEvent.click(screen.getAllByRole('button', { name: 'Adventure menu' })[1])
  return screen.getByRole('menu', { name: 'Adventure menu' })
}

function itemNames(menu: HTMLElement) {
  return within(menu)
    .getAllByRole('menuitem')
    .map((item) => item.textContent)
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('StoryTopBar', () => {
  it('names the campaign and the current scene', () => {
    renderTopBar()
    expect(screen.getByRole('banner')).toHaveTextContent('The Lantern Road')
    expect(screen.getByText('The Drowned Lantern')).toBeInTheDocument()
  })

  it('never shows the "No scene recorded" placeholder', () => {
    const board = makeBoardFixture('exploration')
    renderTopBar({
      board: {
        ...board,
        sceneState: null,
        activeSessionTitle: 'Chapter 1',
        worldState: { ...board.worldState, sceneName: UNRECORDED_SCENE_NAME },
      },
    })
    expect(screen.getByRole('banner')).not.toHaveTextContent('No scene recorded')
    expect(document.querySelector('.story-scene-name')).toHaveTextContent('Chapter 1')
  })

  it('leaves the chapter line empty for an adventure with no chapter, and never prints a placeholder', () => {
    const board = makeBoardFixture('empty')
    renderTopBar({
      board: {
        ...board,
        activeSession: null,
        sceneState: null,
        activeSessionTitle: 'No session selected',
        campaignTitle: 'The Lantern Road',
      },
    })
    const banner = screen.getByRole('banner')
    expect(banner).toHaveTextContent('The Lantern Road')
    expect(banner).not.toHaveTextContent(/No session selected|Select a campaign|No campaign selected/)
    expect(document.querySelector('.story-scene-name')).toHaveTextContent('')
    expect(document.querySelector('.story-clock')).toBeNull()
    cleanup()

    // Still loading the adventure itself: no headline at all rather than a placeholder.
    renderTopBar({
      board: { ...board, campaign: null, activeSession: null, campaignTitle: 'No campaign selected', activeSessionTitle: 'Select a campaign' },
    })
    expect(screen.getByRole('banner')).not.toHaveTextContent(/No session selected|Select a campaign|No campaign selected/)
  })

  it("reads the hero's hit points beside the titles and opens the Character tab", () => {
    const onDrawerOpenChange = vi.fn()
    const hero = (currentHp: number): ActivePlayer => ({
      id: 7,
      character_name: 'Ysolde Marrow',
      name: 'Danny',
      health: { tone: 'wounded', label: 'Wounded', currentHp, maxHp: 24 },
    })
    const { unmount } = renderTopBar({ hero: hero(12), onDrawerOpenChange })
    const hp = screen.getByRole('button', { name: 'HP 12 / 24' })
    expect(hp).toHaveTextContent('12 / 24')
    expect(hp.querySelector('svg')).toHaveClass('lucide-heart')
    expect(hp).not.toHaveAttribute('data-tone')
    expect(hp.previousElementSibling).toHaveClass('story-titles')
    fireEvent.click(hp)
    expect(onDrawerOpenChange).toHaveBeenCalledWith(true, 'character')
    unmount()

    const { unmount: unmountQuarter } = renderTopBar({ hero: hero(6) })
    expect(screen.getByRole('button', { name: 'HP 6 / 24' })).toHaveAttribute('data-tone', 'danger')
    unmountQuarter()

    const { unmount: unmountDown } = renderTopBar({ hero: hero(0), heroDown: true })
    expect(screen.getByRole('button', { name: 'HP Down' })).toHaveTextContent('Down')
    expect(screen.getByRole('button', { name: 'HP Down' })).toHaveAttribute('data-tone', 'danger')
    unmountDown()

    renderTopBar({ hero: null })
    expect(screen.queryByRole('button', { name: /^HP / })).toBeNull()
  })

  it('shows the hit points only below the width where the dock shows the party', () => {
    const css = readFileSync(resolve(__dirname, '../styles/story-shell.css'), 'utf8')
    expect(css).toMatch(/\.story-shell \.story-topbar-hp \{[^}]*display: none;/)
    const narrow = css.slice(css.indexOf('@media (max-width: 899.98px)'))
    const block = narrow.slice(0, narrow.indexOf('}\n}') + 1)
    expect(block).toContain('.story-shell .story-party { display: none; }')
    expect(block).toContain('.story-shell .story-topbar-hp { display: inline-flex; }')
  })

  it('shows the day clock after the scene name', () => {
    renderTopBar()
    const clock = screen.getByText('Day 2 · Evening')
    expect(clock).toHaveClass('story-clock')
    expect(clock.previousElementSibling).toHaveTextContent('The Drowned Lantern')
  })

  it('lists the adventure menu in order for a player', () => {
    renderTopBar()
    expect(screen.getAllByRole('button', { name: 'Adventure menu' })[1]).toHaveAttribute('aria-haspopup', 'menu')
    const menu = openMenu()
    expect(itemNames(menu)).toEqual([
      'Switch adventure',
      'Download Chronicle',
      'Share',
      'Export save',
      'Fullscreen',
      'Settings',
      'Account',
      'Classic view',
      'Exit to title',
    ])
    expect(within(menu).getAllByRole('menuitemradio').map((item) => item.textContent)).toEqual([
      'Default',
      'Large',
      'Extra',
    ])
  })

  it('shows Account only when there is an account to open', () => {
    const onOpenAccount = vi.fn()
    renderTopBar({ chromeProps: { onOpenAccount } })
    fireEvent.click(within(openMenu()).getByRole('menuitem', { name: 'Account' }))
    expect(onOpenAccount).toHaveBeenCalledOnce()
    cleanup()

    renderTopBar({ chromeProps: { onOpenAccount: undefined } })
    const menu = openMenu()
    expect(within(menu).queryByRole('menuitem', { name: 'Account' })).toBeNull()
    expect(itemNames(menu)).toContain('Settings')
  })

  it('gates chapter items and names the console for operators', () => {
    const onContinue = vi.fn()
    renderTopBar({
      chromeProps: {
        endedChapter: { onContinue },
        onStartNextChapter: vi.fn(),
        canUseOperatorTools: true,
        isFullscreen: true,
      },
    })
    const menu = openMenu()
    const names = itemNames(menu)
    expect(names.slice(0, 3)).toEqual(['Continue the adventure', 'Start next chapter', 'Switch adventure'])
    expect(names).toContain('Table tools')
    expect(names).toContain('Exit fullscreen')
    expect(names).not.toContain('Classic view')
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Continue the adventure' }))
    expect(onContinue).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('menu')).toBeNull()
  })

  it('runs menu actions and writes the text size choice', () => {
    const onExitToTitle = vi.fn()
    const downloadSessionChronicle = vi.fn(async () => undefined)
    const onTextSizeChange = vi.fn()
    renderTopBar({
      chromeProps: { onExitToTitle },
      board: { ...makeBoardFixture('exploration'), downloadSessionChronicle },
      onTextSizeChange,
    })
    let menu = openMenu()
    fireEvent.click(within(menu).getByRole('menuitemradio', { name: 'Large' }))
    expect(onTextSizeChange).toHaveBeenCalledWith('large')
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Download Chronicle' }))
    expect(downloadSessionChronicle).toHaveBeenCalledTimes(1)
    menu = openMenu()
    fireEvent.click(within(menu).getByRole('menuitem', { name: 'Exit to title' }))
    expect(onExitToTitle).toHaveBeenCalledTimes(1)
  })

  it('moves through the menu with arrow keys and closes on Escape, returning focus', () => {
    renderTopBar()
    const trigger = screen.getAllByRole('button', { name: 'Adventure menu' })[1]
    fireEvent.click(trigger)
    const menu = screen.getByRole('menu')
    expect(within(menu).getByRole('menuitem', { name: 'Switch adventure' })).toHaveFocus()
    fireEvent.keyDown(menu, { key: 'ArrowDown' })
    expect(within(menu).getByRole('menuitem', { name: 'Download Chronicle' })).toHaveFocus()
    fireEvent.keyDown(menu, { key: 'End' })
    expect(within(menu).getByRole('menuitem', { name: 'Exit to title' })).toHaveFocus()
    fireEvent.keyDown(menu, { key: 'ArrowDown' })
    expect(within(menu).getByRole('menuitem', { name: 'Switch adventure' })).toHaveFocus()
    fireEvent.keyDown(menu, { key: 'Escape' })
    expect(screen.queryByRole('menu')).toBeNull()
    expect(trigger).toHaveFocus()
  })

  it('shows narration only when it is available', () => {
    const onToggleTts = vi.fn()
    const { unmount } = renderTopBar({ chromeProps: { ttsAvailable: true, ttsEnabled: true, onToggleTts } })
    const narration = screen.getByRole('button', { name: 'Narration' })
    expect(narration).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(narration)
    expect(onToggleTts).toHaveBeenCalledTimes(1)
    unmount()
    renderTopBar({ chromeProps: { ttsAvailable: false } })
    expect(screen.queryByRole('button', { name: 'Narration' })).toBeNull()
  })

  it('reports the connection only when it is not ready', () => {
    const { rerender } = renderTopBar()
    expect(screen.getByRole('status')).toBeEmptyDOMElement()
    const board = makeBoardFixture('exploration')
    const bar = (connection: StoryChromeProps['connection']) => (
      <StoryTopBar
        board={board}
        chrome={chrome({ connection })}
        hero={null}
        heroDown={false}
        drawerOpen={false}
        drawerTab="character"
        onDrawerOpenChange={vi.fn()}
        textSize="default"
        onTextSizeChange={vi.fn()}
      />
    )
    rerender(bar('connecting'))
    expect(screen.getByRole('status')).toHaveTextContent('Connecting…')
    rerender(bar('reconnecting'))
    expect(screen.getByRole('status')).toHaveTextContent('Reconnecting…')
    rerender(bar('offline'))
    expect(screen.getByRole('status')).toHaveTextContent('Offline')
    expect(screen.getByRole('status')).toHaveAttribute('data-connection', 'offline')
  })

  it('labels the scripted narrator in safe mode', () => {
    const { unmount } = renderTopBar({ chromeProps: { safeMode: true } })
    expect(screen.getByText('Scripted narrator')).toHaveAttribute(
      'title',
      'No AI provider is connected. The built-in narrator is running the adventure.',
    )
    unmount()
    renderTopBar()
    expect(screen.queryByText('Scripted narrator')).toBeNull()
  })

  it('starts fetching what a button opens as soon as the pointer enters it or focus reaches it', async () => {
    const preload = {
      settings: vi.spyOn(settingsSheetChunk, 'preload'),
      account: vi.spyOn(accountSheetChunk, 'preload'),
      dialogs: vi.spyOn(sessionDialogsChunk, 'preload'),
      drawer: vi.spyOn(drawerChunk, 'preload'),
    }
    const { unmount } = renderTopBar({
      chromeProps: { onStartNextChapter: vi.fn() },
      hero: { id: 7, character_name: 'Ysolde Marrow', name: 'Danny', health: { tone: 'wounded', label: 'Wounded', currentHp: 12, maxHp: 24 } },
    })

    fireEvent.pointerEnter(screen.getAllByRole('button', { name: 'Adventure menu' })[1])
    expect(preload.settings).toHaveBeenCalledOnce()
    expect(preload.account).toHaveBeenCalledOnce()
    expect(preload.dialogs).toHaveBeenCalledOnce()
    expect(preload.drawer).not.toHaveBeenCalled()
    await vi.waitFor(() => expect(settingsSheetChunk.isLoaded()).toBe(true))
    fireEvent.focus(screen.getAllByRole('button', { name: 'Adventure menu' })[0])
    expect(preload.settings).toHaveBeenCalledTimes(2)

    fireEvent.pointerEnter(screen.getByRole('button', { name: 'Open character panel' }))
    expect(preload.drawer).toHaveBeenCalledOnce()
    fireEvent.focus(screen.getByRole('button', { name: 'HP 12 / 24' }))
    expect(preload.drawer).toHaveBeenCalledTimes(2)
    await vi.waitFor(() => expect(drawerChunk.isLoaded()).toBe(true))
    unmount()

    // A player with no account and no chapter to start has neither fetched.
    vi.clearAllMocks()
    renderTopBar({ chromeProps: { onOpenAccount: undefined } })
    fireEvent.pointerEnter(screen.getAllByRole('button', { name: 'Adventure menu' })[1])
    expect(preload.settings).toHaveBeenCalledOnce()
    expect(preload.account).not.toHaveBeenCalled()
    expect(preload.dialogs).not.toHaveBeenCalled()
  })

  it('opens and closes the character panel', () => {
    const onDrawerOpenChange = vi.fn()
    const { unmount } = renderTopBar({ onDrawerOpenChange })
    fireEvent.click(screen.getByRole('button', { name: 'Open character panel' }))
    expect(onDrawerOpenChange).toHaveBeenCalledWith(true, 'character')
    unmount()
    renderTopBar({ onDrawerOpenChange, drawerOpen: true })
    fireEvent.click(screen.getByRole('button', { name: 'Close character panel' }))
    expect(onDrawerOpenChange).toHaveBeenLastCalledWith(false, 'character')
  })
})
