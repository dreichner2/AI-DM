// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SavedWorkspaceDeleteDialog, ShareSessionDialog } from './WorkspaceDialogs'

const workspace = {
  workspace_id: 'ember-table',
  workspace_name: 'Ember Workspace',
  table_name: 'Ember Table',
  access_mode: 'token' as const,
  workspace_role: 'owner',
  is_workspace_admin: true,
  created_at: null,
  updated_at: null,
}

describe('WorkspaceDialogs', () => {
  afterEach(cleanup)

  it('preserves destructive table deletion semantics and pending dismissal guard', () => {
    const onClose = vi.fn()
    const onConfirm = vi.fn()
    const props: ComponentProps<typeof SavedWorkspaceDeleteDialog> = {
      deletesTable: true,
      dialog: { workspace, error: 'Deletion failed.', pending: false },
      dialogRef: createRef<HTMLElement>(),
      onClose,
      onConfirm,
    }
    const { rerender } = render(<SavedWorkspaceDeleteDialog {...props} />)

    const dialog = screen.getByRole('dialog', { name: 'Delete Table' })
    expect(within(dialog).getByText('Ember Table')).toBeInTheDocument()
    expect(within(dialog).getByText('Deletion failed.')).toBeInTheDocument()
    const deleteButton = within(dialog).getByRole('button', { name: 'Delete Table' })
    expect(deleteButton).toHaveClass('danger')
    fireEvent.click(deleteButton)
    expect(onConfirm).toHaveBeenCalledTimes(1)
    fireEvent.mouseDown(dialog.parentElement as HTMLElement)
    expect(onClose).toHaveBeenCalledTimes(1)

    rerender(
      <SavedWorkspaceDeleteDialog
        {...props}
        dialog={{ workspace, error: '', pending: true }}
      />,
    )
    const pendingDialog = screen.getByRole('dialog', { name: 'Delete Table' })
    expect(within(pendingDialog).getByRole('button', { name: 'Deleting...' })).toBeDisabled()
    fireEvent.mouseDown(pendingDialog.parentElement as HTMLElement)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it('preserves saved-table removal and session sharing interactions', () => {
    const onClose = vi.fn()
    const onConfirm = vi.fn()
    const memberWorkspace = {
      ...workspace,
      access_mode: 'configured' as const,
      workspace_role: 'player',
      is_workspace_admin: false,
    }
    const { rerender } = render(
      <SavedWorkspaceDeleteDialog
        deletesTable={false}
        dialog={{ workspace: memberWorkspace, error: '', pending: false }}
        dialogRef={createRef<HTMLElement>()}
        onClose={onClose}
        onConfirm={onConfirm}
      />,
    )
    const removeDialog = screen.getByRole('dialog', { name: 'Remove Saved Table' })
    expect(within(removeDialog).getByRole('button', { name: 'Remove' })).not.toHaveClass('danger')
    expect(within(removeDialog).getByText(/saved tables only/i)).toBeInTheDocument()

    const shareProps: ComponentProps<typeof ShareSessionDialog> = {
      dialogRef: createRef<HTMLElement>(),
      onClose,
      onCopy: vi.fn(),
      url: 'https://table.example.test/?session=42',
    }
    rerender(<ShareSessionDialog {...shareProps} />)
    const shareDialog = screen.getByRole('dialog', { name: 'Share Session' })
    expect(within(shareDialog).getByLabelText('Session share link')).toHaveValue(shareProps.url)
    fireEvent.click(within(shareDialog).getByRole('button', { name: 'Copy Link' }))
    expect(shareProps.onCopy).toHaveBeenCalledTimes(1)
    fireEvent.mouseDown(shareDialog.parentElement as HTMLElement)
    expect(onClose).toHaveBeenCalledTimes(1)
  })
})
