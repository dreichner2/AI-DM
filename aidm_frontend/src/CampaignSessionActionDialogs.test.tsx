// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CampaignActionDialog, SessionActionDialog } from './CampaignSessionActionDialogs'
import type { Campaign, SessionSummary } from './types'

const campaign: Campaign = {
  campaign_id: 10,
  title: 'Ashes of Ember',
  description: 'Old description',
  world_id: 5,
  world_name: 'Smoke World',
  created_at: null,
  updated_at: null,
  status: 'active',
  is_archived: false,
  current_quest: null,
  location: null,
  session_count: 1,
  latest_session_id: 20,
  latest_activity_at: null,
}

const session: SessionSummary = {
  session_id: 20,
  campaign_id: 10,
  created_at: null,
  status: 'active',
  deleted_at: null,
  updated_at: null,
  latest_activity_at: null,
  display_name: 'The Sealed Door',
  turn_count: 2,
  latest_summary: '',
  is_archived: false,
  state_snapshot: {},
}

describe('CampaignSessionActionDialogs', () => {
  afterEach(cleanup)

  it('forwards campaign rename fields and form submission', () => {
    const onSubmit = vi.fn((event) => event.preventDefault())
    const props: ComponentProps<typeof CampaignActionDialog> = {
      dialog: {
        mode: 'rename',
        campaign,
        title: campaign.title,
        description: campaign.description ?? '',
        error: '',
        pending: false,
      },
      dialogRef: createRef<HTMLElement>(),
      onClose: vi.fn(),
      onDescriptionChange: vi.fn(),
      onSubmit,
      onTitleChange: vi.fn(),
    }
    render(<CampaignActionDialog {...props} />)

    const dialog = screen.getByRole('dialog', { name: 'Rename Campaign' })
    fireEvent.change(within(dialog).getByLabelText('Campaign Name'), {
      target: { value: 'Ember Reborn' },
    })
    fireEvent.change(within(dialog).getByLabelText('Description'), {
      target: { value: 'New description' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save Campaign' }))
    expect(props.onTitleChange).toHaveBeenCalledWith('Ember Reborn')
    expect(props.onDescriptionChange).toHaveBeenCalledWith('New description')
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('preserves destructive campaign copy, styling, pending labels, and dismissal guard', () => {
    const onClose = vi.fn()
    render(
      <CampaignActionDialog
        dialog={{
          mode: 'delete',
          campaign,
          title: campaign.title,
          description: '',
          error: 'Cannot delete.',
          pending: true,
        }}
        dialogRef={createRef<HTMLElement>()}
        onClose={onClose}
        onDescriptionChange={vi.fn()}
        onSubmit={vi.fn((event) => event.preventDefault())}
        onTitleChange={vi.fn()}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: 'Delete Campaign' })
    expect(within(dialog).getByText(/permanently deletes the campaign/i)).toBeInTheDocument()
    expect(within(dialog).getByText('Cannot delete.')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Deleting...' })).toHaveClass('danger')
    fireEvent.mouseDown(dialog.parentElement as HTMLElement)
    expect(onClose).not.toHaveBeenCalled()
  })

  it('preserves session rename and delete semantics', () => {
    const onNameChange = vi.fn()
    const onSubmit = vi.fn((event) => event.preventDefault())
    const props: ComponentProps<typeof SessionActionDialog> = {
      dialog: {
        mode: 'rename',
        session,
        name: session.display_name,
        error: '',
        pending: false,
      },
      dialogRef: createRef<HTMLElement>(),
      onClose: vi.fn(),
      onNameChange,
      onSubmit,
    }
    const { rerender } = render(<SessionActionDialog {...props} />)
    const renameDialog = screen.getByRole('dialog', { name: 'Rename Session' })
    fireEvent.change(within(renameDialog).getByLabelText('Session Name'), {
      target: { value: 'A New Door' },
    })
    fireEvent.click(within(renameDialog).getByRole('button', { name: 'Rename Session' }))
    expect(onNameChange).toHaveBeenCalledWith('A New Door')
    expect(onSubmit).toHaveBeenCalledTimes(1)

    rerender(
      <SessionActionDialog
        {...props}
        dialog={{ ...props.dialog, mode: 'delete', error: 'Delete failed.' }}
      />,
    )
    const deleteDialog = screen.getByRole('dialog', { name: 'Delete Session' })
    expect(within(deleteDialog).getByText(/saved turn history/i)).toBeInTheDocument()
    expect(within(deleteDialog).getByText('Delete failed.')).toBeInTheDocument()
    expect(within(deleteDialog).getByRole('button', { name: 'Delete Session' })).toHaveClass('danger')
  })
})
