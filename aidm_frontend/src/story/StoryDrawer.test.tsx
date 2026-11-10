// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { InspectorPanelProps } from '../InspectorPanel'
import type { SessionBoardProps } from '../SessionBoard'
import { testBoard, testCombat, testInspector, testWorld } from './dockTestFixtures'
import { StoryDrawer } from './StoryDrawer'
import type { StoryDrawerTab } from './types'

function mockViewportWidth(width: number) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('max-width: 1179px') ? width <= 1179 : false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }))
}

function renderDrawer({
  tab = 'character',
  board = testBoard(),
  inspector = testInspector(),
  onClose = vi.fn(),
}: {
  tab?: StoryDrawerTab
  board?: SessionBoardProps
  inspector?: InspectorPanelProps
  onClose?: () => void
} = {}) {
  const onTabChange = vi.fn()
  function Harness() {
    const [current, setCurrent] = useState(tab)
    return (
      <>
        <button type="button">Outside</button>
        <StoryDrawer
          board={board}
          inspector={inspector}
          open
          tab={current}
          onTabChange={(next) => {
            onTabChange(next)
            setCurrent(next)
          }}
          onClose={onClose}
        />
      </>
    )
  }
  render(<Harness />)
  return { board, inspector, onTabChange, onClose }
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('StoryDrawer', () => {
  it('moves between the five tabs with the arrow, Home and End keys', () => {
    mockViewportWidth(1440)
    const { onTabChange } = renderDrawer()
    const tablist = screen.getByRole('tablist', { name: 'Character panel sections' })
    const tabs = within(tablist).getAllByRole('tab')

    expect(tabs.map((tab) => tab.textContent)).toEqual(['Character', 'Pack', 'Magic', 'Journal', 'Map'])
    expect(tabs[0]).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', tabs[0].id)

    fireEvent.keyDown(tabs[0], { key: 'ArrowRight' })
    expect(onTabChange).toHaveBeenLastCalledWith('pack')
    expect(screen.getByRole('tab', { name: 'Pack' })).toHaveFocus()
    expect(screen.getByRole('tab', { name: 'Pack' })).toHaveAttribute('aria-selected', 'true')

    fireEvent.keyDown(screen.getByRole('tab', { name: 'Pack' }), { key: 'End' })
    expect(onTabChange).toHaveBeenLastCalledWith('map')
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Map' }), { key: 'ArrowRight' })
    expect(onTabChange).toHaveBeenLastCalledWith('character')
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Character' }), { key: 'ArrowLeft' })
    expect(onTabChange).toHaveBeenLastCalledWith('map')
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Map' }), { key: 'Home' })
    expect(onTabChange).toHaveBeenLastCalledWith('character')
  })

  it('sits beside the story on wide screens and closes with Escape or the close button', () => {
    mockViewportWidth(1440)
    const { onClose } = renderDrawer()

    const sheet = screen.getByRole('complementary', { name: 'Character panel' })
    expect(sheet).not.toHaveAttribute('aria-modal')
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Character' }), { key: 'Escape' })
    fireEvent.click(screen.getByRole('button', { name: 'Close character panel' }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('traps focus as a modal overlay on narrower screens', async () => {
    mockViewportWidth(1024)
    const { onClose } = renderDrawer({ tab: 'journal' })

    const dialog = screen.getByRole('dialog', { name: 'Character panel' })
    expect(dialog).toHaveAttribute('aria-modal', 'true')
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Journal' })).toHaveFocus())

    const close = screen.getByRole('button', { name: 'Close character panel' })
    const lastFocusable = within(dialog).getByRole('button', { name: 'Download Chronicle' })
    lastFocusable.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(dialog).toContainElement(document.activeElement as HTMLElement)
    expect(screen.getByRole('button', { name: 'Outside' })).not.toHaveFocus()

    close.focus()
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(onClose).toHaveBeenCalled()
  })

  it('shows the character at a glance and wires the character actions', () => {
    mockViewportWidth(1440)
    const { inspector } = renderDrawer()

    expect(screen.getByRole('heading', { name: 'Ysolde Marrow' })).toBeInTheDocument()
    expect(screen.getByText('Level 3 Elf Ranger')).toBeInTheDocument()
    expect(screen.getByText('9/14')).toHaveAttribute('data-health', 'wounded')
    expect(screen.getByRole('progressbar', { name: 'Experience' })).toHaveAttribute('aria-valuetext', '1,450 / 2,700 XP')

    fireEvent.click(screen.getByRole('button', { name: 'Edit character' }))
    fireEvent.click(screen.getByRole('button', { name: 'Switch character' }))
    fireEvent.click(screen.getByRole('button', { name: 'New character' }))
    expect(screen.queryByRole('button', { name: 'Delete character' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Manage' }))
    fireEvent.click(screen.getByRole('button', { name: 'Delete character' }))

    expect(inspector.editSelectedPlayer).toHaveBeenCalledTimes(1)
    expect(inspector.loadPlayer).toHaveBeenCalledTimes(1)
    expect(inspector.createDefaultPlayer).toHaveBeenCalledTimes(1)
    expect(inspector.deleteSelectedPlayer).toHaveBeenCalledTimes(1)

    const table = screen.getByRole('list', { name: '' })
    expect(within(table).getByText('You')).toBeInTheDocument()
    expect(within(table).getByText('typing…')).toBeInTheDocument()
  })

  it('shows conditions from combat on the character', () => {
    mockViewportWidth(1440)
    const combat = testCombat()
    combat.allies[0] = { ...combat.allies[0], conditions: ['poisoned'] }
    renderDrawer({ board: testBoard({ worldState: testWorld({ combat }) }) })
    expect(within(screen.getByLabelText('Conditions')).getByText('poisoned')).toBeInTheDocument()
  })

  it('toggles equipment from the pack and shows progress while it saves', () => {
    mockViewportWidth(1440)
    const { inspector } = renderDrawer({ tab: 'pack' })

    expect(screen.getByText('43 gp · 19.9 / 180 lb')).toBeInTheDocument()
    const equip = screen.getByRole('button', { name: 'Equip Longbow' })
    expect(equip).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(equip)
    expect(inspector.toggleInventoryEquipment).toHaveBeenCalledWith(inspector.inventoryRows[0])
    expect(screen.getByText('×2')).toBeInTheDocument()
    cleanup()

    renderDrawer({ tab: 'pack', inspector: testInspector({ equipmentPendingItemKey: 'longbow' }) })
    expect(screen.getByRole('button', { name: 'Updating Longbow' })).toBeDisabled()
  })

  it('travels from the map with the classic travel intent, but not during combat', () => {
    mockViewportWidth(1440)
    const { board } = renderDrawer({ tab: 'map' })
    fireEvent.click(screen.getByRole('button', { name: 'Go to North causeway' }))
    expect(board.actionComposerProps.submitAction).toHaveBeenCalledWith('The party travels to North causeway.', {
      kind: 'travel',
      source: 'scene_panel',
      text: 'The party travels to North causeway.',
      client_message_id: expect.any(String),
      location: { id: 'causeway', name: 'North causeway' },
    })
    expect(screen.getByText('The Drowned Mill')).toBeInTheDocument()
    cleanup()

    const combatBoard = testBoard({ worldState: testWorld({ combat: testCombat() }) })
    renderDrawer({ tab: 'map', board: combatBoard })
    const travel = screen.getByRole('button', { name: 'Go to North causeway' })
    expect(travel).toHaveAttribute('aria-disabled', 'true')
    expect(travel).toHaveAccessibleDescription('Not while combat is underway.')
    fireEvent.click(travel)
    expect(combatBoard.actionComposerProps.submitAction).not.toHaveBeenCalled()
  })

  it('marks the spells the rules engine resolves', () => {
    mockViewportWidth(1440)
    renderDrawer({
      tab: 'magic',
      board: testBoard({
        gameplayControls: { ...testBoard().gameplayControls, spells: [{ ...testBoard().gameplayControls.spells[0], id: 'hunters-mark', name: "Hunter's Mark" }] },
      }),
    })
    expect(screen.getByRole('img', { name: '1st: 2 of 3 available' })).toBeInTheDocument()
    const resolved = screen.getByText("Hunter's Mark").closest('li')
    const narrated = screen.getByText('Fog Cloud').closest('li')
    expect(within(resolved as HTMLElement).getByLabelText('Resolved by the rules engine')).toBeInTheDocument()
    expect(within(narrated as HTMLElement).queryByLabelText('Resolved by the rules engine')).not.toBeInTheDocument()
    expect(screen.getAllByText('conc.')).toHaveLength(2)
    expect(screen.getByText('Favored Foe')).toBeInTheDocument()
  })

  it('lists quests, people, places and memory, and downloads the chronicle', () => {
    mockViewportWidth(1440)
    const { board } = renderDrawer({ tab: 'journal' })
    expect(screen.getByText('The Grey Caravan')).toBeInTheDocument()
    expect(screen.getByText('Follow the lanterns')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'People' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Places' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Recent memory' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Download Chronicle' }))
    expect(board.downloadSessionChronicle).toHaveBeenCalledTimes(1)
  })
})
