// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { WorldZoneSummary } from '../gameSelectors'
import { worldStateFromSnapshot } from '../gameSelectors'
import { MapSection } from './drawer/MapSection'
import { testBoard, testCombat, testInspector, testWorld, VIEWER_ID } from './dockTestFixtures'
import { StoryCombatRibbon } from './StoryCombatRibbon'
import { StoryTopBar } from './StoryTopBar'
import { StoryWhereLine } from './StoryWhereLine'
import type { StoryChromeProps } from './types'

const VIEWER = `player_${VIEWER_ID}`
const ZONES: WorldZoneSummary[] = [
  {
    id: 'inn_yard',
    name: 'The Yard',
    current: true,
    adjacent: false,
    exit: true,
    here: [
      { id: VIEWER, name: 'Ysolde', kind: 'hero' },
      { id: 'player_31', name: 'Corin Ashby', kind: 'hero' },
      { id: 'osric', name: 'Osric Hale', kind: 'person' },
      { id: 'tovin', name: 'Tovin Bell', kind: 'person' },
      { id: 'hob', name: 'Old Hob', kind: 'person' },
      { id: 'wren', name: 'Wren', kind: 'person' },
    ],
  },
  {
    id: 'inn_common_room',
    name: 'The Common Room',
    current: false,
    adjacent: true,
    exit: false,
    moveLabel: 'Go into the common room',
    here: [{ id: 'mara', name: 'Mara Reedwake', kind: 'person' }],
  },
  { id: 'inn_cellar', name: 'The Cellar', current: false, adjacent: false, exit: false, here: [] },
  { id: 'inn_stable', name: 'The Stable', current: false, adjacent: true, exit: false, moveLabel: 'Go to the stable', here: [] },
]

function mockViewportWidth(width: number) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('max-width: 600px') ? width <= 600 : false,
    media: query,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  }))
}

function zoneTexts() {
  return [...document.querySelectorAll('.story-where-zone')].map((zone) => zone.textContent)
}

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('StoryWhereLine', () => {
  it('reads the viewer\'s zone first as "you", then the rooms next door, with long lists counted', () => {
    mockViewportWidth(1280)
    const onMove = vi.fn()
    render(<StoryWhereLine zones={ZONES} viewerId={VIEWER} canMove onMove={onMove} />)

    expect(zoneTexts()).toEqual([
      'The Yard — you, Corin Ashby, Osric Hale +3',
      ' · The Common Room — Mara Reedwake',
      ' · The Stable',
      ' · The Cellar',
    ])
    expect(document.querySelector('.story-where-zone[data-current]')).toHaveTextContent('The Yard')
    // The rooms next door are links that step in; the viewer's own and the far ones are plain.
    const line = screen.getByLabelText('Where everyone stands')
    expect(within(line).getAllByRole('button').map((button) => button.textContent)).toEqual(['The Common Room', 'The Stable'])
    fireEvent.click(within(line).getByRole('button', { name: 'The Common Room' }))
    expect(onMove).toHaveBeenCalledWith(expect.objectContaining({ id: 'inn_common_room', moveLabel: 'Go into the common room' }))
  })

  it('offers no move while one cannot be made (a fight, a reply on its way)', () => {
    mockViewportWidth(1280)
    render(<StoryWhereLine zones={ZONES} viewerId={VIEWER} canMove={false} onMove={vi.fn()} />)

    expect(within(screen.getByLabelText('Where everyone stands')).queryAllByRole('button')).toEqual([])
  })

  it('at 390 px shows the viewer\'s zone and opens the rest on a tap', () => {
    mockViewportWidth(390)
    render(<StoryWhereLine zones={ZONES} viewerId={VIEWER} canMove onMove={vi.fn()} />)

    const line = screen.getByLabelText('Where everyone stands')
    const own = within(line).getByRole('button', { name: 'The Yard' })
    expect(own).toHaveAttribute('aria-expanded', 'false')
    expect(line).not.toHaveAttribute('data-open')
    fireEvent.click(own)
    expect(own).toHaveAttribute('aria-expanded', 'true')
    expect(line).toHaveAttribute('data-open')
  })
})

describe('the where-line in the top bar', () => {
  const chrome: StoryChromeProps = {
    connection: 'ready',
    safeMode: false,
    canUseOperatorTools: false,
    ttsAvailable: false,
    ttsEnabled: false,
    onToggleTts: vi.fn(),
    isFullscreen: false,
    onToggleFullscreen: vi.fn(),
    onOpenConsole: vi.fn(),
    onSwitchAdventure: vi.fn(),
    onExitToTitle: vi.fn(),
    onOpenSettings: vi.fn(),
  }

  function renderTopBar(zones: WorldZoneSummary[] | undefined) {
    const board = testBoard({ worldState: testWorld({ zones }) })
    render(
      <StoryTopBar
        board={board}
        chrome={chrome}
        hero={null}
        heroDown={false}
        drawerOpen={false}
        drawerTab="character"
        onDrawerOpenChange={vi.fn()}
        textSize="default"
        onTextSizeChange={vi.fn()}
      />,
    )
    return board
  }

  it('shows under the scene name in a place with rooms, and a room next door steps in', async () => {
    mockViewportWidth(1280)
    const board = renderTopBar(ZONES)

    const line = await screen.findByLabelText('Where everyone stands')
    expect(line).toHaveTextContent('The Yard — you, Corin Ashby, Osric Hale +3')
    fireEvent.click(within(line).getByRole('button', { name: 'The Common Room' }))
    expect(board.actionComposerProps.submitAction).toHaveBeenCalledWith(
      'Go into the common room.',
      expect.objectContaining({ kind: 'zone_move', zone: { id: 'inn_common_room', name: 'The Common Room' } }),
    )
  })

  it('is absent where the place is one area', () => {
    mockViewportWidth(1280)
    renderTopBar(undefined)

    expect(screen.queryByLabelText('Where everyone stands')).toBeNull()
  })
})

describe('distances in the fight ribbon', () => {
  it('shows how far each participant is from the viewer in place of the band word', () => {
    const combat = testCombat()
    combat.enemies = combat.enemies.map((enemy, index) => ({
      ...enemy,
      ...(index === 0 ? { reach: 'one_move', reachLabel: 'a move away · about 30 ft', health: 'Unhurt', hp: null } : {}),
    }))
    render(
      <StoryCombatRibbon
        combat={combat}
        playerId={VIEWER_ID}
        activePlayers={[{ id: VIEWER_ID, character_name: 'Ysolde Marrow', name: 'Danny' }]}
        controls={testBoard().gameplayControls}
        recentSpellNames={[]}
        disabled={false}
        down={false}
        submitAction={vi.fn(() => true)}
        onOpenTray={vi.fn()}
      />,
    )

    const enemy = document.querySelector(`[data-combatant-id="${combat.enemies[0].id}"]`)
    expect(enemy).toHaveTextContent('a move away · 30 ft · Unhurt')
    expect(enemy).not.toHaveTextContent(/\bnear\b/)
  })

  it('reads reach words from the player view', () => {
    const world = worldStateFromSnapshot({
      combat: {
        status: 'active',
        participants: [
          { id: 'enemy_lantern', name: 'Pale Lantern', team: 'enemy', position: { rangeBand: 'far' }, reach: 'out_of_reach', reachLabel: 'out of reach this turn · 60 ft or more' },
        ],
      },
    })
    expect(world.combat.enemies[0]).toMatchObject({ rangeBand: 'far', reach: 'out_of_reach', reachLabel: 'out of reach this turn · 60 ft or more' })
  })
})

describe('the Map tab', () => {
  it('lists THIS PLACE, who stands in each room, above WAYS ONWARD', () => {
    const onMove = vi.fn()
    const inspector = testInspector({
      worldStatePanel: testWorld({ zones: ZONES, availableExits: [{ id: 'road', name: 'The Old North Road', status: 'known', type: 'road' }] }),
    })
    render(<MapSection inspector={inspector} worldMap={null} travelBlockedReason="" onTravel={vi.fn()} viewerId={VIEWER} canMove onMove={onMove} />)

    expect(screen.getAllByRole('heading').map((heading) => heading.textContent)).toEqual(
      expect.arrayContaining(['This place', 'Ways onward']),
    )
    const headings = screen.getAllByRole('heading').map((heading) => heading.textContent)
    expect(headings.indexOf('This place')).toBeLessThan(headings.indexOf('Ways onward'))
    const rooms = within(screen.getByRole('region', { name: 'This place' })).getAllByRole('listitem').map((item) => item.textContent)
    expect(rooms).toEqual([
      'The Yard — you, Corin Ashby, Osric Hale, Tovin Bell, Old Hob, Wren',
      'The Common Room — Mara ReedwakeGo into the common room',
      'The Stable — nobodyGo to the stable',
      'The Cellar — nobody',
    ])
    fireEvent.click(screen.getByRole('button', { name: 'Go to the stable' }))
    expect(onMove).toHaveBeenCalledWith(expect.objectContaining({ id: 'inn_stable' }))
  })
})
