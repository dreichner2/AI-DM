// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { SessionBoardProps } from '../SessionBoard'
import { makeBoardFixture } from './preview/fixtures'
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
}: {
  chromeProps?: Partial<StoryChromeProps>
  board?: SessionBoardProps
  onTextSizeChange?: (size: 'default' | 'large' | 'extra') => void
  onDrawerOpenChange?: (open: boolean, tab?: StoryDrawerTab) => void
  drawerOpen?: boolean
} = {}) {
  return render(
    <StoryTopBar
      board={board}
      chrome={chrome(chromeProps)}
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
})

describe('StoryTopBar', () => {
  it('names the campaign and the current scene', () => {
    renderTopBar()
    expect(screen.getByRole('banner')).toHaveTextContent('The Lantern Road')
    expect(screen.getByText('The Drowned Lantern')).toBeInTheDocument()
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
