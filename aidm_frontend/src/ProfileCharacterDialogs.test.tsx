// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CharacterJoinDialog } from './ProfileCharacterDialogs'
import type { Player } from './types'

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

describe('ProfileCharacterDialogs', () => {
  afterEach(cleanup)

  it('lists characters as rows with portraits, join, and create behavior', () => {
    const onJoinPlayer = vi.fn()
    const onCreateCharacter = vi.fn()
    const props: ComponentProps<typeof CharacterJoinDialog> = {
      campaignTitle: 'Ashes of Ember',
      dialogRef: createRef<HTMLElement>(),
      onClose: vi.fn(),
      onCreateCharacter,
      onJoinPlayer,
      open: true,
      players: [player],
      portraitSrcForPlayer: vi.fn(() => '/portraits/ember.png'),
    }
    const { rerender } = render(<CharacterJoinDialog {...props} />)

    const dialog = screen.getByRole('dialog', { name: 'Choose your character' })
    expect(dialog).toHaveAccessibleDescription('Choose who you are playing in Ashes of Ember.')
    const row = within(dialog).getByRole('listitem')
    expect(row.querySelector('img')).toHaveAttribute('src', '/portraits/ember.png')
    expect(row).toHaveTextContent('Level 2 Wizard · Danny')
    fireEvent.click(within(row).getByRole('button', { name: 'Join as Ember' }))
    expect(onJoinPlayer).toHaveBeenCalledWith(player)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create Character' }))
    expect(onCreateCharacter).toHaveBeenCalledTimes(1)

    rerender(<CharacterJoinDialog {...props} players={[]} />)
    expect(screen.getByText(/No characters yet\./)).toBeInTheDocument()
  })

  it('renders nothing while closed', () => {
    render(
      <CharacterJoinDialog
        campaignTitle={null}
        dialogRef={createRef<HTMLElement>()}
        onClose={vi.fn()}
        onCreateCharacter={vi.fn()}
        onJoinPlayer={vi.fn()}
        open={false}
        players={[player]}
        portraitSrcForPlayer={() => ''}
      />,
    )

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
