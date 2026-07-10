// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { SessionPresenceStrip } from './SessionPresenceStrip'

afterEach(() => cleanup())

describe('SessionPresenceStrip', () => {
  it('distinguishes the selected player, remote typing, and health state', () => {
    render(
      <SessionPresenceStrip
        activePlayers={[
          {
            id: 30,
            character_name: 'Ember',
            name: 'Danny',
            race: 'human',
            sex: 'female',
            is_typing: true,
            health: { tone: 'uninjured', label: 'Healthy', currentHp: 12, maxHp: 12 },
          },
          {
            id: 31,
            character_name: 'Thorne',
            name: 'Alex',
            race: 'elf',
            sex: 'male',
            is_typing: true,
            health: { tone: 'wounded', label: 'Wounded', currentHp: 5, maxHp: 10 },
          },
        ]}
        selectedPlayerId={30}
        selectedPlayerHasTurn
        turnControlStatusLabel="Free play"
      />,
    )

    const strip = screen.getByRole('region', { name: 'Mobile active players' })
    expect(within(strip).getByText('2 online')).toBeInTheDocument()
    expect(within(strip).getByText('Thorne typing')).toBeInTheDocument()
    expect(within(strip).getByText('You')).toBeInTheDocument()
    expect(within(strip).getByLabelText('Thorne is typing')).toBeInTheDocument()
    expect(within(strip).getByLabelText('Thorne health: Wounded')).toHaveAttribute('title', 'Wounded: 5/10 HP')
  })

  it('announces the turn-control status when nobody else is online', () => {
    render(
      <SessionPresenceStrip
        activePlayers={[]}
        selectedPlayerId={30}
        selectedPlayerHasTurn={false}
        turnControlStatusLabel="Waiting for the spotlight"
      />,
    )

    const strip = screen.getByRole('region', { name: 'Mobile active players' })
    expect(within(strip).getByText('Solo')).toBeInTheDocument()
    expect(within(strip).getByText('No friends online')).toBeInTheDocument()
    expect(within(strip).getByText('Waiting for the spotlight')).toBeInTheDocument()
  })
})
