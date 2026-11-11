// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LEGACY_PASSWORD_SETUP_MESSAGE } from '../../useRuntimeSettings'
import { StoryAccountSheet, type AccountAccessProps, type AccountSheetView } from './StoryAccountSheet'

type AccountSheetProps = ComponentProps<typeof StoryAccountSheet>

const adminWorkspace = {
  workspace_id: 'ember-table',
  workspace_name: 'Ember Workspace',
  table_name: 'Ember Table',
  access_mode: 'token' as const,
  workspace_role: 'owner',
  is_workspace_admin: true,
  created_at: null,
  updated_at: null,
}
const memberWorkspace = {
  workspace_id: 'moon-table',
  workspace_name: 'Moon Table',
  access_mode: 'configured' as const,
  workspace_role: 'player',
  is_workspace_admin: false,
  created_at: null,
  updated_at: null,
}

function accessProps(overrides: Partial<AccountAccessProps> = {}): AccountAccessProps {
  return {
    error: '',
    form: {
      baseUrl: '',
      workspaceToken: '',
      workspaceName: '',
      workspacePassword: '',
      username: '',
      firstName: '',
      lastName: '',
      password: '',
      recoveryCode: '',
    },
    legacyPasswordSetupRequired: false,
    onAuthIntentChange: vi.fn(),
    onAuthStepChange: vi.fn(),
    onErrorChange: vi.fn(),
    onFormChange: vi.fn(),
    onLegacyPasswordSetupRequiredChange: vi.fn(),
    onOpenSavedWorkspaceDelete: vi.fn(),
    onSelectSavedWorkspace: vi.fn(),
    onSubmit: vi.fn((event) => event.preventDefault()),
    onWorkspaceActionChange: vi.fn(),
    onWorkspaceCreateAccessModeChange: vi.fn(),
    onWorkspaceJoinMethodChange: vi.fn(),
    runtimeAccount: null,
    runtimeAuthIntent: 'login',
    runtimeAuthStep: 'account',
    runtimeCreatedWorkspaceToken: '',
    runtimeWorkspaceAction: 'join',
    runtimeWorkspaceCreateAccessMode: 'password',
    runtimeWorkspaceJoinMethod: 'token',
    workspaceId: '',
    ...overrides,
  }
}

function sheetProps(
  overrides: Partial<Omit<AccountSheetProps, 'view' | 'account'>> = {},
  view: AccountSheetView = { view: 'summary', account: { name: 'Ember Vale', table: 'Ember Table', role: 'owner' } },
): AccountSheetProps {
  return {
    access: accessProps(),
    dialogRef: createRef<HTMLElement>(),
    onClose: vi.fn(),
    onLogOut: vi.fn(),
    onSwitchTable: vi.fn(),
    ...overrides,
    ...view,
  }
}

afterEach(cleanup)

describe('StoryAccountSheet', () => {
  it('shows who you are and where, with switch table and log out', () => {
    const props = sheetProps()
    render(<StoryAccountSheet {...props} />)

    const dialog = screen.getByRole('dialog', { name: 'Account' })
    expect(within(dialog).getByText('Ember Vale')).toBeInTheDocument()
    expect(within(dialog).getByText('At Ember Table · owner')).toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Backend URL')).not.toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Switch table' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Log out' }))
    expect(props.onSwitchTable).toHaveBeenCalledOnce()
    expect(props.onLogOut).toHaveBeenCalledOnce()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close account' }))
    expect(props.onClose).toHaveBeenCalledOnce()
  })

  it('offers log in and create account in plain words when signed out', () => {
    const access = accessProps()
    render(<StoryAccountSheet {...sheetProps({ access}, { view: 'access' })} />)

    const dialog = screen.getByRole('dialog', { name: 'Account' })
    expect(dialog).toHaveAccessibleDescription('Log in with your username. Use your password if one is set.')
    const actions = within(dialog).getByRole('group', { name: 'Account action' })
    expect(within(actions).getByRole('button', { name: 'Log in' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(dialog).getByLabelText('Username')).toHaveAttribute('data-autofocus')
    expect(within(dialog).queryByLabelText('First Name')).not.toBeInTheDocument()

    fireEvent.click(within(actions).getByRole('button', { name: 'Create account' }))
    expect(access.onAuthIntentChange).toHaveBeenCalledWith('signup')
    fireEvent.change(within(dialog).getByLabelText('Password'), { target: { value: 'secret' } })
    expect(access.onFormChange).toHaveBeenCalled()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Continue' }))
    expect(access.onSubmit).toHaveBeenCalledOnce()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close account prompt' }))
  })

  it('keeps the legacy password recovery affordances', () => {
    const access = accessProps({
      error: LEGACY_PASSWORD_SETUP_MESSAGE,
      form: { ...accessProps().form, username: 'legacy-player' },
      legacyPasswordSetupRequired: true,
      runtimeAuthIntent: 'signup',
    })
    render(<StoryAccountSheet {...sheetProps({ access}, { view: 'access' })} />)

    const dialog = screen.getByRole('dialog', { name: 'Account' })
    expect(within(dialog).getAllByText(LEGACY_PASSWORD_SETUP_MESSAGE)).not.toHaveLength(0)
    expect(within(dialog).getByLabelText('New Password')).toHaveAttribute('autocomplete', 'new-password')
    expect(within(dialog).getByLabelText('Recovery Code')).toHaveAttribute('autocomplete', 'one-time-code')
    expect(within(dialog).queryByLabelText('First Name')).not.toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Log in' }))
    expect(access.onAuthIntentChange).toHaveBeenCalledWith('login')
    expect(access.onLegacyPasswordSetupRequiredChange).toHaveBeenCalledWith(false)
    expect(access.onErrorChange).toHaveBeenCalledWith('')
  })

  it('lists your tables and joins or creates one', () => {
    const access = accessProps({
      runtimeAccount: {
        accountId: 7,
        username: 'ember',
        firstName: 'Ember',
        lastName: 'Vale',
        displayName: 'Ember Vale',
        workspaceId: adminWorkspace.workspace_id,
        workspaceRole: 'owner',
        isWorkspaceAdmin: true,
        requiresPasswordSetup: false,
        workspaces: [adminWorkspace, memberWorkspace],
      },
      runtimeAuthStep: 'workspace',
      workspaceId: adminWorkspace.workspace_id,
    })
    const { rerender } = render(<StoryAccountSheet {...sheetProps({ access}, { view: 'access' })} />)

    const dialog = screen.getByRole('dialog', { name: 'Account' })
    const tables = within(dialog).getByRole('group', { name: 'Saved tables' })
    const [ember, moon] = within(tables).getAllByRole('listitem')
    expect(within(ember).getByText('Here now')).toBeInTheDocument()
    fireEvent.click(within(moon).getByRole('button', { name: 'Play at Moon Table' }))
    expect(access.onSelectSavedWorkspace).toHaveBeenCalledWith('moon-table')
    fireEvent.click(within(ember).getByRole('button', { name: 'Delete Ember Table' }))
    expect(access.onOpenSavedWorkspaceDelete).toHaveBeenCalledWith(adminWorkspace)
    expect(within(moon).getByRole('button', { name: 'Remove Moon Table' })).not.toHaveClass('story-link-danger')

    const choices = within(dialog).getByRole('group', { name: 'Table action' })
    expect(within(choices).getByRole('button', { name: 'Join a table with a code' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(dialog).getByLabelText('Table code')).toBeInTheDocument()
    fireEvent.click(within(choices).getByRole('button', { name: 'Join with a password' }))
    expect(access.onWorkspaceActionChange).toHaveBeenLastCalledWith('join')
    expect(access.onWorkspaceJoinMethodChange).toHaveBeenLastCalledWith('password')
    fireEvent.click(within(choices).getByRole('button', { name: 'Create a table' }))
    expect(access.onWorkspaceActionChange).toHaveBeenLastCalledWith('create')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Back' }))
    expect(access.onAuthStepChange).toHaveBeenCalledWith('account')

    rerender(
      <StoryAccountSheet
        {...sheetProps({ access: { ...access, runtimeWorkspaceAction: 'create', runtimeWorkspaceCreateAccessMode: 'token' }}, { view: 'access' })}
      />,
    )
    expect(within(dialog).getByLabelText('Table Name')).toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Table Password')).not.toBeInTheDocument()
    fireEvent.click(within(within(dialog).getByRole('group', { name: 'Table access' })).getByRole('button', { name: 'Password' }))
    expect(access.onWorkspaceCreateAccessModeChange).toHaveBeenCalledWith('password')
    expect(within(dialog).getByRole('button', { name: 'Create table' })).toBeInTheDocument()

    rerender(
      <StoryAccountSheet {...sheetProps({ access: { ...access, runtimeCreatedWorkspaceToken: 'generated-secret' }}, { view: 'access' })} />,
    )
    expect(within(dialog).getByLabelText('Generated table code')).toHaveValue('generated-secret')
    expect(within(dialog).getByRole('button', { name: 'Done' })).toBeInTheDocument()
    expect(within(dialog).queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()
  })
})
