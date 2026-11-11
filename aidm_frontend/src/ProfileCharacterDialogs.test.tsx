// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest'
import { CharacterJoinDialog } from './ProfileCharacterDialogs'
import type { JoinableHero, Player } from './types'

const player: Player = {
  player_id: 30,
  workspace_id: 'ember-table',
  account_id: 7,
  username: 'danny',
  campaign_id: 10,
  name: 'Danny',
  character_name: 'Ember',
  race: 'Human',
  sex: 'female',
  profile_image: '/profile-icons/human_female.png',
  class_: 'Wizard',
  char_class: 'Wizard',
  level: 2,
  created_at: null,
  updated_at: null,
}

const ARDEN: JoinableHero = {
  player_id: 41,
  character_name: 'Arden Vale',
  level: 3,
  race: 'Human',
  class: 'Fighter',
  portrait: '/profile-icons/human_male.png',
  adventure_title: 'The Road of Unremembered Kings',
  last_played_at: '2026-11-10T20:00:00+00:00',
}
const SEREN: JoinableHero = {
  ...ARDEN,
  player_id: 42,
  character_name: 'Seren Ashgrove',
  level: 1,
  class: 'Cleric',
  adventure_title: 'Shadow over the Greenway',
}

let fetchMock: Mock<typeof fetch>

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function answer(routes: Record<string, () => Response>) {
  fetchMock.mockImplementation(async (input) => {
    const url = String(input)
    const route = Object.keys(routes).find((path) => url.endsWith(path))
    return route ? routes[route]() : json({ error: 'Not found' }, 404)
  })
}

function dialogProps(change: Partial<ComponentProps<typeof CharacterJoinDialog>> = {}): ComponentProps<typeof CharacterJoinDialog> {
  return {
    campaignTitle: 'Ashes of Ember',
    dialogRef: createRef<HTMLElement>(),
    onClose: vi.fn(),
    onCreateCharacter: vi.fn(),
    onJoinPlayer: vi.fn(),
    open: true,
    players: [player],
    portraitSrcForPlayer: vi.fn(() => '/portraits/ember.png'),
    auth: '',
    baseUrl: 'http://localhost:5050',
    campaignId: 10,
    onBrought: vi.fn(),
    ...change,
  }
}

beforeEach(() => {
  fetchMock = vi.fn<typeof fetch>()
  vi.stubGlobal('fetch', fetchMock)
  answer({ '/api/players/campaigns/10/joinable-heroes': () => json({ heroes: [] }) })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

describe('ProfileCharacterDialogs', () => {
  it('lists characters as rows with portraits, join, and create behavior', async () => {
    const props = dialogProps()
    const { rerender } = render(<CharacterJoinDialog {...props} />)

    const dialog = screen.getByRole('dialog', { name: 'Choose your character' })
    expect(dialog).toHaveAccessibleDescription('Choose who you are playing in Ashes of Ember.')
    const row = within(dialog).getByRole('listitem')
    expect(row.querySelector('img')).toHaveAttribute('src', '/portraits/ember.png')
    expect(row).toHaveTextContent('Level 2 Wizard · Danny')
    fireEvent.click(within(row).getByRole('button', { name: 'Join as Ember' }))
    expect(props.onJoinPlayer).toHaveBeenCalledWith(player)
    fireEvent.click(within(dialog).getByRole('button', { name: 'New character' }))
    expect(props.onCreateCharacter).toHaveBeenCalledTimes(1)

    rerender(<CharacterJoinDialog {...props} players={[]} />)
    expect(screen.getByText(/No characters yet\./)).toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByRole('heading', { name: 'Your heroes' })).not.toBeInTheDocument()
  })

  it('renders nothing while closed', () => {
    render(<CharacterJoinDialog {...dialogProps({ open: false })} />)

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it("offers the player's heroes from their other adventures above New character, as rows", async () => {
    answer({ '/api/players/campaigns/10/joinable-heroes': () => json({ heroes: [ARDEN, SEREN] }) })
    render(<CharacterJoinDialog {...dialogProps()} />)

    const yours = await screen.findByRole('list', { name: 'Your heroes' })
    const rows = within(yours).getAllByRole('listitem')
    expect(rows.map((row) => within(row).getByRole('button').textContent)).toEqual(['Play as Arden', 'Play as Seren'])
    expect(rows[0]).toHaveTextContent('Arden Vale')
    expect(rows[0]).toHaveTextContent('Level 3 · Human Fighter · last in The Road of Unremembered Kings')
    expect(rows[1]).toHaveTextContent('Level 1 · Human Cleric · last in Shadow over the Greenway')
    expect(rows[0].querySelector('img')).toHaveAttribute('src', ARDEN.portrait)
    // This adventure's own heroes come first; New character stays last.
    const dialog = screen.getByRole('dialog', { name: 'Choose your character' })
    const order = Array.from(dialog.querySelectorAll('ul, footer'))
    expect(order.map((element) => element.getAttribute('aria-label') ?? element.getAttribute('aria-labelledby') ?? element.tagName)).toEqual([
      'Existing characters',
      'character-join-yours',
      'FOOTER',
    ])
  })

  it('brings the chosen hero in and hands their new record on', async () => {
    const brought = { player_id: 77, character_name: 'Arden Vale' }
    answer({
      '/api/players/campaigns/10/joinable-heroes': () => json({ heroes: [ARDEN] }),
      '/api/players/campaigns/10/players/bring': () => json({ player_id: 77, message: 'Player successfully created' }, 201),
      '/api/players/77': () => json(brought),
    })
    const props = dialogProps({ players: [] })
    render(<CharacterJoinDialog {...props} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Play as Arden' }))

    await waitFor(() => expect(props.onBrought).toHaveBeenCalledWith(brought))
    const bring = fetchMock.mock.calls.find(([url]) => String(url).endsWith('/players/bring'))
    expect(bring?.[1]).toMatchObject({ method: 'POST', body: JSON.stringify({ player_id: 41 }) })
    expect(screen.queryByText(/No characters yet/)).not.toBeInTheDocument()
  })

  it('shows a refusal as one quiet line under the heroes', async () => {
    answer({
      '/api/players/campaigns/10/joinable-heroes': () => json({ heroes: [ARDEN] }),
      '/api/players/campaigns/10/players/bring': () =>
        json({ error: 'A hero named Arden Vale is already in this adventure.', error_code: 'hero_name_taken' }, 409),
    })
    const props = dialogProps()
    render(<CharacterJoinDialog {...props} />)

    fireEvent.click(await screen.findByRole('button', { name: 'Play as Arden' }))

    expect(await screen.findByRole('status')).toHaveTextContent('A hero named Arden Vale is already in this adventure.')
    expect(props.onBrought).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Play as Arden' })).toBeEnabled()
  })
})
