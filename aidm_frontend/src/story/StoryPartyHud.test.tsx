// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ActivePlayer } from '../types'
import { StoryPartyHud } from './StoryPartyHud'

const down = { tone: 'dead' as const, label: 'Dead', currentHp: 0, maxHp: 14 }

function party(): ActivePlayer[] {
  return [
    { id: 30, character_name: 'Liora Quill', name: 'Danny', health: down },
    { id: 31, character_name: 'Brannoc', name: 'Imogen', health: down },
    { id: 32, character_name: 'Wren', name: 'Sol', health: down },
  ]
}

afterEach(() => cleanup())

describe('StoryPartyHud', () => {
  it('names a downed hero by their state: Dying, Down — stable, Dead, or Down without one', () => {
    render(
      <StoryPartyHud
        activePlayers={[...party(), { id: 33, character_name: 'Pell', name: 'Kit', health: down }]}
        viewerPlayerId={30}
        vitality={{
          30: { state: 'stable', successes: 0, failures: 0 },
          31: { state: 'dying', successes: 1, failures: 0 },
          32: { state: 'dead', successes: 0, failures: 0 },
        }}
        onDrawerOpenChange={vi.fn()}
      />,
    )
    const portraits = within(screen.getByRole('group', { name: 'Party' })).getAllByRole('button')
    expect(portraits.map((portrait) => portrait.getAttribute('aria-label'))).toEqual([
      'Liora Quill: Down — stable',
      'Brannoc: Dying',
      'Wren: Dead',
      'Pell: Down',
    ])
    expect(screen.getByRole('img', { name: 'Down — stable' })).toBeInTheDocument()
  })
})
