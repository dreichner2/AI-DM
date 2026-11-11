// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { InspectorPanelProps } from '../InspectorPanel'
import type { SessionBoardProps } from '../SessionBoard'
import { makeBoardFixture, makeInspectorFixture } from './preview/fixtures'
import { setStoryReduceMotion, setStoryTextSize, setStoryTypeface } from './sheets/storyPreferences'
import { drawerChunk, drawerSectionChunks } from './storyChunks'
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
  inspector = makeInspectorFixture(),
  drawerOpen = false,
  drawerTab = 'character',
  onDrawerOpenChange = vi.fn(),
}: {
  board?: SessionBoardProps
  inspector?: InspectorPanelProps
  drawerOpen?: boolean
  drawerTab?: StoryDrawerTab
  onDrawerOpenChange?: (open: boolean, tab?: StoryDrawerTab) => void
} = {}) {
  const view = (props: SessionBoardProps) => (
    <StoryView
      board={props}
      inspector={inspector}
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
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
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
    cleanup()

    // A hero without spells has no Magic tab for G to open.
    const noMagic = vi.fn()
    const board = makeBoardFixture('exploration')
    const inspector = makeInspectorFixture()
    renderView({
      board: { ...board, gameplayControls: { ...board.gameplayControls, spells: [], narratedSpells: [] } },
      inspector: { ...inspector, spellbook: { ...inspector.spellbook, knownSpells: [] } },
      onDrawerOpenChange: noMagic,
    })
    fireEvent.keyDown(document.body, { key: 'g' })
    expect(noMagic).not.toHaveBeenCalled()
  })

  it('fetches the drawer once the browser is idle after the story first shows, or at the first drawer key', () => {
    const idle: Array<() => void> = []
    vi.stubGlobal('requestIdleCallback', vi.fn((callback: () => void) => idle.push(callback)))
    vi.stubGlobal('cancelIdleCallback', vi.fn())
    const preloads = [drawerChunk, ...Object.values(drawerSectionChunks)].map((chunk) =>
      vi.spyOn(chunk, 'preload').mockImplementation(() => undefined),
    )
    const { rerenderBoard } = renderView()
    rerenderBoard(makeBoardFixture('combat'))
    expect(idle).toHaveLength(1)
    preloads.forEach((preload) => expect(preload).not.toHaveBeenCalled())
    act(() => idle[0]())
    preloads.forEach((preload) => expect(preload).toHaveBeenCalledOnce())

    fireEvent.keyDown(document.body, { key: 'j' })
    preloads.forEach((preload) => expect(preload).toHaveBeenCalledTimes(2))
    fireEvent.keyDown(document.body, { key: 'x' })
    preloads.forEach((preload) => expect(preload).toHaveBeenCalledTimes(2))
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

  it('marks the time of day on the shell so the light can follow it', () => {
    const { container, rerenderBoard } = renderView()
    expect(container.querySelector('.story-shell')).toHaveAttribute('data-phase', 'evening')
    rerenderBoard(makeBoardFixture('combat'))
    expect(container.querySelector('.story-shell')).toHaveAttribute('data-phase', 'night')
  })

  it('drains a dying party member of colour and shows their death saves beside the portrait', () => {
    renderView({ board: makeBoardFixture('dying') })
    const party = screen.getByRole('group', { name: 'Party' })
    const [viewer, brannoc] = within(party).getAllByRole('button')
    expect(viewer).toHaveAttribute('data-vitality', 'dying')
    expect(brannoc).not.toHaveAttribute('data-vitality')
    expect(within(party).getByRole('img', { name: 'Dying: 1 success, 1 failure' })).toBeInTheDocument()
    expect(within(party).queryByText(/^HP /)).not.toBeInTheDocument()
  })

  it('names a party member out of the fight beside the portrait', () => {
    const board = makeBoardFixture('dying')
    const sessionState = board.sessionState
    if (!sessionState) throw new Error('fixture has a session state')
    renderView({
      board: {
        ...board,
        sessionState: {
          ...sessionState,
          state_snapshot: { combat: { status: 'active', participants: [{ id: 'player-30', team: 'player', conditions: ['unconscious', 'stable', 'out_of_fight'] }] } },
        },
      },
    })
    const party = screen.getByRole('group', { name: 'Party' })
    expect(within(party).getAllByRole('button')[0]).toHaveAttribute('aria-label', 'Ysolde Marrow: Out of the fight')
    expect(within(party).getByRole('img', { name: 'Out of the fight' })).toBeInTheDocument()
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
      'Ysolde Marrow: Uninjured',
      'Brannoc Deepvale: Uninjured',
      'Wren Tallowmere: Lightly hurt',
      'Corin Ashby: Uninjured',
    ])
    expect(within(party).getByText('HP 24 / 24')).toBeInTheDocument()
    expect(within(party).getByText('1 more in the party')).toBeInTheDocument()
    expect(portraits[1]).toHaveAttribute('data-typing')
    fireEvent.click(portraits[2])
    expect(onDrawerOpenChange).toHaveBeenCalledWith(true, 'character')
  })

  it('shows the chosen hero in the dock before the first chapter begins', () => {
    const inspector = makeInspectorFixture()
    const board = makeBoardFixture('empty')
    renderView({ board: { ...board, activeSession: null, sessionState: null, activePlayers: [] }, inspector })

    const party = screen.getByRole('group', { name: 'Party' })
    const [hero, ...others] = within(party).getAllByRole('button')
    expect(others).toEqual([])
    expect(hero).toHaveAttribute('aria-label', 'Ysolde Marrow: Uninjured')
    expect(hero.querySelector('img')).toHaveAttribute('src', inspector.characterAvatarSrc)
    expect(within(party).getByText('Ysolde Marrow')).toBeInTheDocument()
    expect(within(party).getByText('HP 24 / 24')).toBeInTheDocument()
    // Narrow screens read the same hit points in the top bar.
    expect(screen.getByRole('button', { name: 'HP 24 / 24' })).toBeInTheDocument()
    // No chapter yet: the hidden heading names the adventure.
    expect(screen.getByRole('heading', { level: 1, name: 'The Lantern Road' })).toBeInTheDocument()
  })

  it('applies and remembers the text size in the console’s reading settings', () => {
    localStorage.setItem('aidm:chatTextSettings', JSON.stringify({ size: 'default', font: 'mono' }))
    renderView()
    fireEvent.click(screen.getAllByRole('button', { name: 'Adventure menu' })[0])
    fireEvent.click(screen.getByRole('menuitemradio', { name: 'Extra' }))
    expect(screen.getByRole('log')).toHaveAttribute('data-text-size', 'extra')
    expect(JSON.parse(localStorage.getItem('aidm:chatTextSettings') ?? '{}')).toEqual({ size: 'extra', font: 'mono' })
  })

  it('applies text size, typeface and reduce motion from Settings the moment they change', () => {
    const board = makeBoardFixture('combat')
    const { container, rerenderBoard } = renderView({ board })
    const shell = container.querySelector('.story-shell')
    expect(shell).toHaveAttribute('data-typeface', 'default')
    expect(shell).not.toHaveAttribute('data-reduce-motion')

    act(() => setStoryTextSize('large'))
    expect(screen.getByRole('log')).toHaveAttribute('data-text-size', 'large')
    act(() => setStoryTypeface('sans'))
    expect(shell).toHaveAttribute('data-typeface', 'sans')
    act(() => setStoryReduceMotion(true))
    expect(shell).toHaveAttribute('data-reduce-motion')
    expect(container.querySelector('.story-atmosphere')).toHaveAttribute('data-still')

    // Held still: losing HP no longer flashes the screen edge.
    rerenderBoard({
      ...board,
      activePlayers: board.activePlayers.map((player) =>
        player.id === board.playerId && player.health ? { ...player, health: { ...player.health, currentHp: 5 } } : player,
      ),
    })
    expect(container.querySelector('.story-hurt-flash')).toBeNull()

    act(() => setStoryReduceMotion(false))
    expect(shell).not.toHaveAttribute('data-reduce-motion')
  })
})
