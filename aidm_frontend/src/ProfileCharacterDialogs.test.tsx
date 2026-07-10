// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CharacterJoinDialog, ProfileSettingsDialog } from './ProfileCharacterDialogs'
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

function profileProps(
  overrides: Partial<ComponentProps<typeof ProfileSettingsDialog>> = {},
): ComponentProps<typeof ProfileSettingsDialog> {
  return {
    canEditCharacter: true,
    canSwitchCharacter: false,
    dialogRef: createRef<HTMLElement>(),
    onBackendSettings: vi.fn(),
    onClose: vi.fn(),
    onEditCharacter: vi.fn(),
    onReconnectRealtime: vi.fn(),
    onRefreshWorkspace: vi.fn(),
    onSignOut: vi.fn(),
    onSwitchCharacter: vi.fn(),
    open: true,
    signedIn: true,
    summary: {
      account: 'Danny Vale',
      backend: 'Same origin',
      campaign: 'Ashes of Ember',
      character: 'Ember',
      narration: 'Ready / 320ms',
      session: 'The Sealed Door',
      table: 'ember-table / owner',
    },
    ...overrides,
  }
}

describe('ProfileCharacterDialogs', () => {
  afterEach(cleanup)

  it('renders the profile summary and forwards account actions with capability guards', () => {
    const props = profileProps()
    render(<ProfileSettingsDialog {...props} />)

    const dialog = screen.getByRole('dialog', { name: 'Profile Settings' })
    Object.values(props.summary).forEach((value) => {
      expect(within(dialog).getByText(value)).toBeInTheDocument()
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Edit character' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Refresh workspace' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Reconnect realtime' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Backend settings' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Sign out' }))
    expect(props.onEditCharacter).toHaveBeenCalledTimes(1)
    expect(props.onRefreshWorkspace).toHaveBeenCalledTimes(1)
    expect(props.onReconnectRealtime).toHaveBeenCalledTimes(1)
    expect(props.onBackendSettings).toHaveBeenCalledTimes(1)
    expect(props.onSignOut).toHaveBeenCalledTimes(1)
    expect(within(dialog).getByRole('button', { name: 'Switch character' })).toBeDisabled()
    fireEvent.mouseDown(dialog.parentElement as HTMLElement)
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it('preserves character choices, portraits, join, and create behavior', () => {
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

    const dialog = screen.getByRole('dialog', { name: 'Join Campaign' })
    expect(within(dialog).getByText('Choose who you are playing in Ashes of Ember.')).toBeInTheDocument()
    const joinButton = within(dialog).getByRole('button', { name: 'Join as Ember' })
    expect(joinButton.querySelector('img')).toHaveAttribute(
      'src',
      '/portraits/ember.png',
    )
    fireEvent.click(joinButton)
    expect(onJoinPlayer).toHaveBeenCalledWith(player)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create Character' }))
    expect(onCreateCharacter).toHaveBeenCalledTimes(1)

    rerender(<CharacterJoinDialog {...props} players={[]} />)
    expect(screen.getByText('No characters yet.')).toBeInTheDocument()
  })
})
