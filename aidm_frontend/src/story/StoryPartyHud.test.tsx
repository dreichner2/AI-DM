// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ActivePlayer } from '../types'
import { drawerChunk } from './storyChunks'
import { StoryPartyHud } from './StoryPartyHud'

const down = { tone: 'dead' as const, label: 'Dead', currentHp: 0, maxHp: 14 }

function party(): ActivePlayer[] {
  return [
    { id: 30, character_name: 'Liora Quill', name: 'Danny', health: down },
    { id: 31, character_name: 'Brannoc', name: 'Imogen', health: down },
    { id: 32, character_name: 'Wren', name: 'Sol', health: down },
  ]
}

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('StoryPartyHud', () => {
  it('names a downed hero by their state: Dying, Down — stable, Out of the fight, or Down without one', () => {
    render(
      <StoryPartyHud
        activePlayers={[...party(), { id: 33, character_name: 'Pell', name: 'Kit', health: down }]}
        viewerPlayerId={30}
        vitality={{
          30: { state: 'stable', successes: 0, failures: 0 },
          31: { state: 'dying', successes: 1, failures: 0 },
          32: { state: 'out', successes: 0, failures: 0 },
        }}
        onDrawerOpenChange={vi.fn()}
      />,
    )
    const portraits = within(screen.getByRole('group', { name: 'Party' })).getAllByRole('button')
    expect(portraits.map((portrait) => portrait.getAttribute('aria-label'))).toEqual([
      'Liora Quill: Down — stable',
      'Brannoc: Dying',
      'Wren: Out of the fight',
      'Pell: Down',
    ])
    expect(screen.getByRole('img', { name: 'Down — stable' })).toBeInTheDocument()
  })

  it.each([
    [28, 28, 'Uninjured'],
    [26, 28, 'Lightly hurt'],
    [14, 20, 'Lightly hurt'],
    [13, 20, 'Wounded'],
    [8, 20, 'Wounded'],
    [7, 20, 'Badly wounded'],
    [3, 20, 'Badly wounded'],
    [2, 20, 'Near death'],
  ])('names a standing hero at %i of %i HP %s', (currentHp, maxHp, word) => {
    render(
      <StoryPartyHud
        activePlayers={[{ id: 30, character_name: 'Arden Vale', name: 'Danny', health: { tone: 'wounded', label: 'Wounded', currentHp, maxHp } }]}
        viewerPlayerId={30}
        vitality={{}}
        onDrawerOpenChange={vi.fn()}
      />,
    )
    const portrait = within(screen.getByRole('group', { name: 'Party' })).getByRole('button')
    expect(portrait).toHaveAttribute('aria-label', `Arden Vale: ${word}`)
    expect(portrait).toHaveAttribute('title', `Arden Vale: ${word}`)
  })

  it('starts fetching the character panel when the pointer or focus reaches a portrait', () => {
    const preload = vi.spyOn(drawerChunk, 'preload').mockImplementation(() => undefined)
    render(<StoryPartyHud activePlayers={party()} viewerPlayerId={30} vitality={{}} onDrawerOpenChange={vi.fn()} />)
    const [viewer, other] = within(screen.getByRole('group', { name: 'Party' })).getAllByRole('button')

    fireEvent.pointerEnter(viewer)
    expect(preload).toHaveBeenCalledOnce()
    fireEvent.focus(other)
    expect(preload).toHaveBeenCalledTimes(2)
  })

  it('marks the viewer who holds Inspiration beside their name, and only then', () => {
    const hero: ActivePlayer[] = [{ id: 30, character_name: 'Arden Vale', name: 'Danny', health: { tone: 'uninjured', label: 'Uninjured', currentHp: 12, maxHp: 12 } }]
    const { rerender } = render(
      <StoryPartyHud activePlayers={hero} viewerPlayerId={30} vitality={{}} viewerInspired onDrawerOpenChange={vi.fn()} />,
    )
    const mark = screen.getByRole('img', { name: 'Inspiration' })
    expect(mark).toHaveAttribute('title', 'Inspiration')
    expect(mark.closest('.story-party-name')).toHaveTextContent('Arden Vale')

    rerender(<StoryPartyHud activePlayers={hero} viewerPlayerId={30} vitality={{}} onDrawerOpenChange={vi.fn()} />)
    expect(screen.queryByRole('img', { name: 'Inspiration' })).not.toBeInTheDocument()
  })
})
