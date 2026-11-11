// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { useState } from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
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

async function renderDrawer({
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
  // Each section arrives as its own chunk.
  await waitFor(() => expect(screen.getByRole('tabpanel')).not.toBeEmptyDOMElement())
  return { board, inspector, onTabChange, onClose }
}

beforeEach(() => {
  // JSDOM has no canvas implementation; rendering and map behavior are covered in StoryMap.test.tsx.
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
})

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('StoryDrawer', () => {
  it('moves between the five tabs with the arrow, Home and End keys', async () => {
    mockViewportWidth(1440)
    const { onTabChange } = await renderDrawer()
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

  it('has no Magic tab for a hero without spells, and shows their abilities and traits on Character instead', async () => {
    mockViewportWidth(1440)
    const base = testInspector()
    const inspector = testInspector({
      spellbook: { ...base.spellbook, knownSpells: [] },
      characterTraits: [{
        id: 'second_wind',
        name: 'Second Wind',
        category: 'class',
        typeLabel: 'Feature',
        source: 'Fighter',
        description: 'Regain 1d10 + level hit points.',
        actionType: 'bonus action',
        cooldown: 'short rest',
        active: true,
      }],
    })
    const board = testBoard({ gameplayControls: { ...testBoard().gameplayControls, spells: [], narratedSpells: [] } })
    const { onTabChange } = await renderDrawer({ tab: 'magic', board, inspector })

    const tabs = within(screen.getByRole('tablist', { name: 'Character panel sections' })).getAllByRole('tab')
    expect(tabs.map((tab) => tab.textContent)).toEqual(['Character', 'Pack', 'Journal', 'Map'])
    expect(screen.getByRole('tab', { name: 'Character' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tabpanel')).toHaveTextContent('Second WindRegain 1d10 + level hit points.')
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Character' }), { key: 'ArrowRight' })
    expect(onTabChange).toHaveBeenLastCalledWith('pack')
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Pack' }), { key: 'ArrowRight' })
    expect(onTabChange).toHaveBeenLastCalledWith('journal')
    cleanup()

    // A spellcaster keeps the Magic tab, and the traits stay there.
    await renderDrawer({ inspector: testInspector({ characterTraits: inspector.characterTraits }) })
    expect(screen.getByRole('tab', { name: 'Magic' })).toBeInTheDocument()
    expect(screen.getByRole('tabpanel')).not.toHaveTextContent('Second Wind')
  })

  it('sits beside the story on wide screens and closes with Escape or the close button', async () => {
    mockViewportWidth(1440)
    const { onClose } = await renderDrawer()

    const sheet = screen.getByRole('complementary', { name: 'Character panel' })
    expect(sheet).not.toHaveAttribute('aria-modal')
    fireEvent.keyDown(screen.getByRole('tab', { name: 'Character' }), { key: 'Escape' })
    fireEvent.click(screen.getByRole('button', { name: 'Close character panel' }))
    expect(onClose).toHaveBeenCalledTimes(2)
  })

  it('traps focus as a modal overlay on narrower screens', async () => {
    mockViewportWidth(1024)
    const { onClose } = await renderDrawer({ tab: 'journal' })

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

  it('shows the character at a glance and wires the character actions', async () => {
    mockViewportWidth(1440)
    const { inspector } = await renderDrawer()

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

  it('offers a hero, and nothing of a sheet, while this adventure has none', async () => {
    mockViewportWidth(1440)
    const { inspector } = await renderDrawer({ inspector: testInspector({ selectedPlayerId: null, playersCount: 0 }) })
    const panel = screen.getByRole('tabpanel')

    expect(panel).toHaveTextContent('No hero yet.')
    expect(panel).not.toHaveTextContent(/player/i)
    expect(within(panel).queryByRole('progressbar')).not.toBeInTheDocument()
    expect(panel.querySelector('.story-abilities, .story-vitals')).toBeNull()
    expect(within(panel).getAllByRole('button').map((button) => button.textContent)).toEqual(['Create a hero'])
    fireEvent.click(within(panel).getByRole('button', { name: 'Create a hero' }))
    expect(inspector.createDefaultPlayer).toHaveBeenCalledTimes(1)
    cleanup()

    const joinable = await renderDrawer({ inspector: testInspector({ selectedPlayerId: null, playersCount: 2 }) })
    fireEvent.click(screen.getByRole('button', { name: 'Choose a hero' }))
    expect(joinable.inspector.loadPlayer).toHaveBeenCalledTimes(1)
    // The other tabs still open.
    fireEvent.click(screen.getByRole('tab', { name: 'Journal' }))
    await waitFor(() => expect(screen.getByRole('tabpanel')).not.toHaveTextContent('No hero yet.'))
  })

  it('signs the proficiency bonus and initiative and shows HP against its maximum when one is known', async () => {
    mockViewportWidth(1440)
    const vital = (label: string) => screen.getByText(label, { selector: 'dt' }).nextElementSibling
    const base = testInspector()
    const [viewer, other] = base.activePlayers
    await renderDrawer({
      inspector: testInspector({
        statBlock: { ...base.statBlock, hp: '14', init: '1', proficiency: '2' },
        activePlayers: [{ ...viewer, health: { tone: 'uninjured', label: 'Uninjured', currentHp: 14, maxHp: 14 } }, other],
      }),
    })
    expect(vital('HP')).toHaveTextContent(/^14\/14$/)
    expect(vital('Initiative')).toHaveTextContent(/^\+1$/)
    expect(vital('Proficiency bonus')).toHaveTextContent(/^\+2$/)
    cleanup()

    const unknownHealth = [{ ...viewer, health: null }, other]
    await renderDrawer({ inspector: testInspector({ statBlock: { ...base.statBlock, hp: '7 / 12', init: '-1', proficiency: '+3' }, activePlayers: unknownHealth }) })
    expect(vital('HP')).toHaveTextContent(/^7\/12$/)
    expect(vital('Initiative')).toHaveTextContent(/^-1$/)
    expect(vital('Proficiency bonus')).toHaveTextContent(/^\+3$/)
    cleanup()

    await renderDrawer({ inspector: testInspector({ statBlock: { ...base.statBlock, hp: '12', init: '0' }, activePlayers: unknownHealth }) })
    expect(vital('HP')).toHaveTextContent(/^12$/)
    expect(vital('Initiative')).toHaveTextContent(/^\+0$/)
  })

  it('shows conditions from combat on the character', async () => {
    mockViewportWidth(1440)
    const combat = testCombat()
    combat.allies[0] = { ...combat.allies[0], conditions: ['poisoned'] }
    await renderDrawer({ board: testBoard({ worldState: testWorld({ combat }) }) })
    expect(within(screen.getByLabelText('Conditions')).getByText('poisoned')).toBeInTheDocument()
  })

  it('shows saving throws on one line with the proficient ones first, hit dice, and conditions outside combat', async () => {
    mockViewportWidth(1440)
    const base = testInspector()
    await renderDrawer({
      inspector: testInspector({
        statBlock: {
          ...base.statBlock,
          savingThrows: [
            { ability: 'STR', modifier: '+4', proficient: true },
            { ability: 'DEX', modifier: '+1', proficient: false },
            { ability: 'CON', modifier: '+4', proficient: true },
          ],
          hitDice: { current: 2, max: 3, die: 'd10' },
          conditions: ['poisoned'],
        },
      }),
    })

    const saves = screen.getByText('Saves', { selector: 'dt' }).nextElementSibling as HTMLElement
    expect(saves).toHaveTextContent(/^STR \+4·CON \+4·DEX \+1$/)
    expect(within(saves).getByText('STR +4')).toHaveAttribute('data-proficient')
    expect(within(saves).getByText('DEX +1')).not.toHaveAttribute('data-proficient')
    expect(screen.getByText('Hit dice', { selector: 'dt' }).nextElementSibling).toHaveTextContent(/^2\/3 d10$/)
    expect(within(screen.getByLabelText('Conditions')).getByText('poisoned')).toBeInTheDocument()
  })

  it('shows a dying character’s death saves on the sheet and at the table', async () => {
    mockViewportWidth(1440)
    const board = testBoard()
    const sessionState = board.sessionState
    if (!sessionState) throw new Error('fixture has a session state')
    await renderDrawer({
      board: {
        ...board,
        sessionState: {
          ...sessionState,
          state_snapshot: {
            combat: {
              status: 'active',
              participants: [
                { id: 'player_30', team: 'player', conditions: ['unconscious', 'dying'], deathSaves: { successes: 2, failures: 1 } },
                { id: 'player_31', team: 'player', conditions: ['unconscious', 'stable'] },
              ],
            },
          },
        },
      },
      inspector: testInspector({ statBlock: { ...testInspector().statBlock, conditions: ['unconscious', 'dying', 'poisoned'] } }),
    })

    expect(screen.getAllByRole('img', { name: 'Dying: 2 successes, 1 failure' })).toHaveLength(2)
    expect(screen.getByRole('img', { name: 'Down — stable' })).toBeInTheDocument()
    expect(within(screen.getByLabelText('Conditions')).getAllByText(/./).map((chip) => chip.textContent)).toEqual(['poisoned'])
  })

  it('names a hero out of the fight so on the sheet and at the table, never as a condition', async () => {
    mockViewportWidth(1440)
    const board = testBoard()
    const sessionState = board.sessionState
    if (!sessionState) throw new Error('fixture has a session state')
    const out = ['unconscious', 'stable', 'out_of_fight']
    await renderDrawer({
      board: {
        ...board,
        sessionState: {
          ...sessionState,
          state_snapshot: { combat: { status: 'active', participants: [{ id: 'player_30', team: 'player', conditions: out }] } },
        },
      },
      inspector: testInspector({ statBlock: { ...testInspector().statBlock, conditions: [...out, 'poisoned'] } }),
    })

    expect(screen.getAllByRole('img', { name: 'Out of the fight' })).toHaveLength(2)
    expect(within(screen.getByLabelText('Conditions')).getAllByText(/./).map((chip) => chip.textContent)).toEqual(['poisoned'])
  })

  it('toggles equipment from the pack and shows progress while it saves', async () => {
    mockViewportWidth(1440)
    const { inspector } = await renderDrawer({ tab: 'pack' })

    expect(screen.getByText('43 gp · 19.9 / 180 lb')).toBeInTheDocument()
    const equip = screen.getByRole('button', { name: 'Equip Longbow' })
    expect(equip).toHaveAttribute('aria-pressed', 'false')
    fireEvent.click(equip)
    expect(inspector.toggleInventoryEquipment).toHaveBeenCalledWith(inspector.inventoryRows[0])
    expect(screen.getByText('×2')).toBeInTheDocument()
    cleanup()

    await renderDrawer({ tab: 'pack', inspector: testInspector({ equipmentPendingItemKey: 'longbow' }) })
    expect(screen.getByRole('button', { name: 'Updating Longbow' })).toBeDisabled()
  })

  it('travels from the map with the classic travel intent, but not during combat', async () => {
    mockViewportWidth(1440)
    const { board } = await renderDrawer({ tab: 'map' })
    fireEvent.click(await screen.findByRole('button', { name: 'Go to North causeway' }))
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
    await renderDrawer({ tab: 'map', board: combatBoard })
    const travel = screen.getByRole('button', { name: 'Go to North causeway' })
    expect(travel).toHaveAttribute('aria-disabled', 'true')
    expect(travel).toHaveAccessibleDescription('Not while combat is underway.')
    fireEvent.click(travel)
    expect(combatBoard.actionComposerProps.submitAction).not.toHaveBeenCalled()
  })

  it('marks the spells the rules engine resolves', async () => {
    mockViewportWidth(1440)
    await renderDrawer({
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

  it('shows every objective with its state, the finished quests, clues and papers', async () => {
    mockViewportWidth(1440)
    await renderDrawer({
      tab: 'journal',
      inspector: testInspector({
        worldStatePanel: testWorld({
          activeQuests: [
            {
              id: 'road',
              title: 'The Road of Unremembered Kings',
              status: 'active',
              stage: 'Follow the road',
              objective: 'Learn why the names were erased',
              objectives: [
                { id: 'stone', description: 'Recover the road-stone fragment', status: 'completed', optional: false },
                { id: 'names', description: 'Learn why the names were erased', status: 'open', optional: false },
                { id: 'cairns', description: 'Pass the cairns', status: 'blocked', optional: false },
                { id: 'wardens', description: 'Spare the wardens', status: 'failed', optional: false },
                { id: 'shrine', description: 'Light the roadside shrine', status: 'open', optional: true },
              ],
            },
          ],
          finishedQuests: [{ id: 'bell', title: 'The Bell at Third Mile', status: 'completed', stage: '', objective: '' }],
          clues: [{ id: 'clue', title: 'Pale Road-Stone Fragment', summary: 'Half a name-rune.' }],
          handouts: [{ id: 'page', title: 'Page from the Ledger', summary: 'A ledger page.', text: 'Route: Lantern Post by dusk.' }],
          lore: [{ id: 'oath', title: 'The Roadward Oath', summary: 'Protect travellers without price.' }],
        }),
      }),
    })

    const objectives = screen.getByText('Recover the road-stone fragment').closest('ul') as HTMLElement
    expect(within(objectives).getAllByRole('img').map((glyph) => glyph.getAttribute('aria-label'))).toEqual([
      'Completed objective',
      'Open objective',
      'Blocked objective',
      'Failed objective',
      'Open objective',
    ])
    expect(within(objectives).getByText(/Light the roadside shrine/)).toHaveTextContent('Light the roadside shrine optional')

    const finished = screen.getByRole('button', { name: 'Finished · 1' })
    expect(screen.queryByText('The Bell at Third Mile')).not.toBeInTheDocument()
    fireEvent.click(finished)
    expect(finished).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('The Bell at Third Mile')).toBeInTheDocument()

    expect(screen.getByRole('heading', { name: 'Clues' })).toBeInTheDocument()
    expect(screen.getByText('Half a name-rune.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Papers' })).toBeInTheDocument()
    expect(screen.queryByText('Route: Lantern Post by dusk.')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Page from the Ledger' }))
    expect(screen.getByText('Route: Lantern Post by dusk.')).toHaveClass('story-paper-text')
    fireEvent.click(screen.getByRole('button', { name: 'The Roadward Oath' }))
    expect(screen.getByText('Protect travellers without price.')).toBeInTheDocument()
  })

  it('lists quests, people, places and memory, and downloads the chronicle', async () => {
    mockViewportWidth(1440)
    const { board } = await renderDrawer({ tab: 'journal' })
    expect(screen.getByText('The Grey Caravan')).toBeInTheDocument()
    expect(screen.getByText('Follow the lanterns')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'People' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Places' })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Recent memory' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Download Chronicle' }))
    expect(board.downloadSessionChronicle).toHaveBeenCalledTimes(1)
  })
})
