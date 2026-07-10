// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { RuntimeSettingsDialog } from './RuntimeSettingsDialog'
import { LEGACY_PASSWORD_SETUP_MESSAGE } from './useRuntimeSettings'

type RuntimeSettingsDialogProps = ComponentProps<typeof RuntimeSettingsDialog>

function dialogProps(overrides: Partial<RuntimeSettingsDialogProps> = {}): RuntimeSettingsDialogProps {
  return {
    defaultBaseUrl: '',
    dialogRef: createRef<HTMLElement>(),
    error: '',
    form: {
      baseUrl: 'http://127.0.0.1:5050',
      workspaceToken: '',
      workspaceName: '',
      workspacePassword: '',
      username: '',
      firstName: '',
      lastName: '',
      password: '',
    },
    legacyPasswordSetupRequired: false,
    mode: 'settings',
    onAuthIntentChange: vi.fn(),
    onAuthStepChange: vi.fn(),
    onClose: vi.fn(),
    onErrorChange: vi.fn(),
    onFormChange: vi.fn(),
    onLegacyPasswordSetupRequiredChange: vi.fn(),
    onOpenSavedWorkspaceDelete: vi.fn(),
    onSelectSavedWorkspace: vi.fn(),
    onSubmit: vi.fn((event) => event.preventDefault()),
    onWorkspaceActionChange: vi.fn(),
    onWorkspaceCreateAccessModeChange: vi.fn(),
    onWorkspaceJoinMethodChange: vi.fn(),
    open: true,
    runtimeAccount: null,
    runtimeCreatedWorkspaceToken: '',
    runtimeAuthIntent: 'login',
    runtimeAuthStep: 'account',
    runtimeWorkspaceAction: 'join',
    runtimeWorkspaceCreateAccessMode: 'password',
    runtimeWorkspaceJoinMethod: 'token',
    workspaceId: '',
    ...overrides,
  }
}

describe('RuntimeSettingsDialog', () => {
  afterEach(cleanup)

  it('preserves backend settings form, reset, submit, and dismissal behavior', () => {
    const props = dialogProps({ defaultBaseUrl: 'https://api.example.test' })
    let latestForm = props.form
    props.onFormChange = vi.fn((update) => {
      latestForm = typeof update === 'function' ? update(latestForm) : update
    })
    render(<RuntimeSettingsDialog {...props} />)

    const dialog = screen.getByRole('dialog', { name: 'Backend Settings' })
    const backendUrl = within(dialog).getByLabelText('Backend URL')
    expect(backendUrl).toHaveValue('http://127.0.0.1:5050')
    expect(backendUrl).toHaveAttribute('data-autofocus', 'true')

    fireEvent.change(backendUrl, { target: { value: 'https://next.example.test' } })
    expect(latestForm).toEqual({ ...props.form, baseUrl: 'https://next.example.test' })

    fireEvent.click(within(dialog).getByRole('button', { name: 'Reset' }))
    expect(props.onFormChange).toHaveBeenLastCalledWith({
      baseUrl: 'https://api.example.test',
      workspaceToken: '',
      workspaceName: '',
      workspacePassword: '',
      username: '',
      firstName: '',
      lastName: '',
      password: '',
    })

    fireEvent.click(within(dialog).getByRole('button', { name: 'Save Settings' }))
    expect(props.onSubmit).toHaveBeenCalledTimes(1)

    fireEvent.mouseDown(dialog.parentElement as HTMLElement)
    expect(props.onClose).toHaveBeenCalledTimes(1)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close backend settings' }))
    expect(props.onClose).toHaveBeenCalledTimes(2)
  })

  it('preserves account choices and legacy password recovery affordances', () => {
    const props = dialogProps({
      error: LEGACY_PASSWORD_SETUP_MESSAGE,
      form: {
        ...dialogProps().form,
        username: 'legacy-player',
      },
      legacyPasswordSetupRequired: true,
      mode: 'auth',
      runtimeAuthIntent: 'signup',
    })
    render(<RuntimeSettingsDialog {...props} />)

    const dialog = screen.getByRole('dialog', { name: 'Sign Up' })
    expect(within(dialog).getByRole('button', { name: 'Close account prompt' })).toBeInTheDocument()
    expect(within(dialog).getAllByText(LEGACY_PASSWORD_SETUP_MESSAGE)).not.toHaveLength(0)
    expect(within(dialog).getByLabelText('New Password')).toHaveAttribute('autocomplete', 'new-password')
    expect(within(dialog).getByLabelText('First Name')).toBeInTheDocument()
    expect(within(dialog).getByLabelText('Last Name')).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Log In' }))
    expect(props.onAuthIntentChange).toHaveBeenCalledWith('login')
    expect(props.onLegacyPasswordSetupRequiredChange).toHaveBeenCalledWith(false)
    expect(props.onErrorChange).toHaveBeenCalledWith('')

    fireEvent.change(within(dialog).getByLabelText('Username'), {
      target: { value: 'updated-player' },
    })
    expect(props.onLegacyPasswordSetupRequiredChange).toHaveBeenLastCalledWith(false)
    expect(props.onErrorChange).toHaveBeenLastCalledWith('')
  })

  it('preserves saved-table actions and generated-token completion state', () => {
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
    const props = dialogProps({
      mode: 'auth',
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
    const { rerender } = render(<RuntimeSettingsDialog {...props} />)

    const dialog = screen.getByRole('dialog', { name: 'Join Table' })
    const savedTables = within(dialog).getByRole('group', { name: 'Saved tables' })
    const selectedTable = within(savedTables).getByRole('button', {
      name: 'Ember Table owner / admin',
    })
    expect(selectedTable).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(selectedTable)
    expect(props.onSelectSavedWorkspace).toHaveBeenCalledWith('ember-table')

    fireEvent.click(within(savedTables).getByRole('button', { name: 'Delete Ember Table' }))
    expect(props.onOpenSavedWorkspaceDelete).toHaveBeenCalledWith(adminWorkspace)
    expect(within(savedTables).getByRole('button', { name: 'Remove Moon Table' })).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Password' }))
    expect(props.onWorkspaceJoinMethodChange).toHaveBeenCalledWith('password')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }))
    expect(props.onWorkspaceActionChange).toHaveBeenCalledWith('create')

    rerender(
      <RuntimeSettingsDialog
        {...props}
        runtimeCreatedWorkspaceToken="generated-secret-token"
      />,
    )
    const tokenDialog = screen.getByRole('dialog', { name: 'Save Table Token' })
    expect(within(tokenDialog).getByLabelText('Generated table token')).toHaveValue(
      'generated-secret-token',
    )
    expect(within(tokenDialog).getByRole('button', { name: 'Done' })).toBeInTheDocument()
    expect(within(tokenDialog).queryByRole('button', { name: 'Back' })).not.toBeInTheDocument()
  })
})
