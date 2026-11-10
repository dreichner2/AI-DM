// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { SessionBoardProps } from '../SessionBoard'
import { makeBoardFixture, makeInspectorFixture } from './preview/fixtures'
import type { StoryChromeProps, StoryDrawerTab } from './types'
import { StoryView } from './StoryView'

const chrome: StoryChromeProps = {
  connection: 'ready',
  safeMode: false,
  canUseOperatorTools: false,
  ttsAvailable: false,
  ttsEnabled: false,
  onToggleTts: () => {},
  isFullscreen: false,
  onToggleFullscreen: () => {},
  onOpenConsole: () => {},
  onSwitchAdventure: () => {},
  onExitToTitle: () => {},
  onOpenSettings: () => {},
  onOpenAccount: () => {},
}

function renderView({
  board = makeBoardFixture('exploration'),
  drawerOpen = false,
  drawerTab = 'character',
  onDrawerOpenChange = vi.fn(),
}: {
  board?: SessionBoardProps
  drawerOpen?: boolean
  drawerTab?: StoryDrawerTab
  onDrawerOpenChange?: (open: boolean, tab?: StoryDrawerTab) => void
} = {}) {
  const view = (props: SessionBoardProps) => (
    <StoryView
      board={props}
      inspector={makeInspectorFixture()}
      chrome={chrome}
      composer={<textarea aria-label="Your action" />}
      drawer={<div>Drawer contents</div>}
      drawerOpen={drawerOpen}
      drawerTab={drawerTab}
      onDrawerOpenChange={onDrawerOpenChange}
    />
  )
  const result = render(view(board))
  return { ...result, rerenderBoard: (next: SessionBoardProps) => result.rerender(view(next)) }
}

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

afterEach(() => {
  cleanup()
  localStorage.clear()
})

describe('StoryView', () => {
  it('lays the stage out in landmarks with a hidden session title', () => {
    renderView()
    expect(screen.getByRole('heading', { level: 1, name: 'The Drowned Lantern' })).toHaveClass('story-sr-only')
    expect(screen.getByRole('banner')).toBeInTheDocument()
    const main = screen.getByRole('main', { name: 'Story' })
    expect(within(main).getByRole('log', { name: 'Session timeline' })).toBeInTheDocument()
    expect(within(main).getByRole('textbox', { name: 'Your action' })).toBeInTheDocument()
    expect(screen.getByRole('complementary', { name: 'Character and journal' })).toHaveTextContent('Drawer contents')
  })

  it('opens the drawer on the right tab from the keyboard and closes it with Escape', () => {
    const onDrawerOpenChange = vi.fn()
    const { unmount } = renderView({ onDrawerOpenChange })
    const keys: Array<[string, StoryDrawerTab]> = [
      ['c', 'character'],
      ['i', 'pack'],
      ['g', 'magic'],
      ['j', 'journal'],
      ['m', 'map'],
    ]
    keys.forEach(([key, tab]) => {
      fireEvent.keyDown(document.body, { key })
      expect(onDrawerOpenChange).toHaveBeenLastCalledWith(true, tab)
    })
    unmount()

    const whileOpen = vi.fn()
    renderView({ onDrawerOpenChange: whileOpen, drawerOpen: true, drawerTab: 'journal' })
    fireEvent.keyDown(document.body, { key: 'j' })
    expect(whileOpen).toHaveBeenLastCalledWith(false, 'journal')
    fireEvent.keyDown(document.body, { key: 'm' })
    expect(whileOpen).toHaveBeenLastCalledWith(true, 'map')
    fireEvent.keyDown(document.body, { key: 'Escape' })
    expect(whileOpen).toHaveBeenLastCalledWith(false)
  })

  it('ignores hotkeys while typing or while a modal dialog is open', () => {
    const onDrawerOpenChange = vi.fn()
    renderView({ onDrawerOpenChange, drawerOpen: true })
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Your action' }), { key: 'c' })
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Your action' }), { key: 'Escape' })
    fireEvent.keyDown(document.body, { key: 'c', metaKey: true })
    const dialog = document.createElement('div')
    dialog.setAttribute('role', 'dialog')
    dialog.setAttribute('aria-modal', 'true')
    document.body.appendChild(dialog)
    fireEvent.keyDown(document.body, { key: 'j' })
    dialog.remove()
    expect(onDrawerOpenChange).not.toHaveBeenCalled()
  })

  it('lights the scene by mood, combat first', () => {
    const { container, rerenderBoard } = renderView()
    expect(container.querySelector('.story-shell')).toHaveAttribute('data-mood', 'hearth')
    rerenderBoard(makeBoardFixture('combat'))
    expect(container.querySelector('.story-shell')).toHaveAttribute('data-mood', 'combat')
  })

  it('flashes the screen edge when the viewer loses HP', () => {
    const board = makeBoardFixture('combat')
    const { container, rerenderBoard } = renderView({ board })
    expect(container.querySelector('.story-hurt-flash')).toBeNull()
    rerenderBoard({
      ...board,
      activePlayers: board.activePlayers.map((player) =>
        player.id === board.playerId && player.health ? { ...player, health: { ...player.health, currentHp: 5 } } : player,
      ),
    })
    expect(container.querySelector('.story-hurt-flash')).toBeInTheDocument()
  })

  it('shows the party with the viewer first and opens the character drawer from a portrait', () => {
    const onDrawerOpenChange = vi.fn()
    renderView({ onDrawerOpenChange })
    const party = screen.getByRole('group', { name: 'Party' })
    const portraits = within(party).getAllByRole('button')
    expect(portraits.map((portrait) => portrait.getAttribute('aria-label'))).toEqual([
      'Ysolde Marrow: Unhurt',
      'Brannoc Deepvale: Unhurt',
      'Wren Tallowmere: Wounded',
      'Corin Ashby: Unhurt',
    ])
    expect(within(party).getByText('HP 24 / 24')).toBeInTheDocument()
    expect(within(party).getByText('1 more in the party')).toBeInTheDocument()
    expect(portraits[1]).toHaveAttribute('data-typing')
    fireEvent.click(portraits[2])
    expect(onDrawerOpenChange).toHaveBeenCalledWith(true, 'character')
  })

  it('applies and remembers the text size in the console’s reading settings', () => {
    localStorage.setItem('aidm:chatTextSettings', JSON.stringify({ size: 'default', font: 'mono' }))
    renderView()
    fireEvent.click(screen.getAllByRole('button', { name: 'Adventure menu' })[0])
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Extra' }))
    expect(screen.getByRole('log')).toHaveAttribute('data-text-size', 'extra')
    expect(JSON.parse(localStorage.getItem('aidm:chatTextSettings') ?? '{}')).toEqual({ size: 'extra', font: 'mono' })
  })
})
