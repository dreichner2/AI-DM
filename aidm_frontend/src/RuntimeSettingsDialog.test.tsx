// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { createRef, type ComponentProps } from 'react'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { BackendTrustDialog, RuntimeSettingsDialog } from './RuntimeSettingsDialog'

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
      recoveryCode: '',
    },
    onClose: vi.fn(),
    onFormChange: vi.fn(),
    onSubmit: vi.fn((event) => event.preventDefault()),
    open: true,
    ...overrides,
  }
}

describe('RuntimeSettingsDialog', () => {
  afterEach(cleanup)

  it('names the connection in plain words and keeps its form, reset, submit, and dismissal behavior', () => {
    const props = dialogProps({ defaultBaseUrl: 'https://api.example.test' })
    let latestForm = props.form
    props.onFormChange = vi.fn((update) => {
      latestForm = typeof update === 'function' ? update(latestForm) : update
    })
    render(<RuntimeSettingsDialog {...props} />)

    const dialog = screen.getByRole('dialog', { name: 'Connection' })
    expect(within(dialog).getByText('Only change this if you run the game server somewhere else.')).toBeInTheDocument()
    expect(dialog).not.toHaveTextContent(/backend/i)
    expect([...dialog.querySelectorAll('.story-dialog-footer button')].map((button) => button.textContent)).toEqual([
      'Reset',
      'Cancel',
      'Save',
    ])
    const backendUrl = within(dialog).getByLabelText('Game server address')
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
      recoveryCode: '',
    })

    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(props.onSubmit).toHaveBeenCalledTimes(1)

    fireEvent.mouseDown(dialog.parentElement as HTMLElement)
    expect(props.onClose).toHaveBeenCalledTimes(1)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Close connection settings' }))
    expect(props.onClose).toHaveBeenCalledTimes(2)
  })

  it('renders nothing while closed', () => {
    render(<RuntimeSettingsDialog {...dialogProps({ open: false })} />)

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('asks before trusting a shared game server', () => {
    const onConfirm = vi.fn()
    const onReject = vi.fn()
    render(
      <BackendTrustDialog
        backend={{ origin: 'https://friend.example.test', baseUrl: 'https://friend.example.test/api' }}
        dialogRef={createRef<HTMLElement>()}
        onConfirm={onConfirm}
        onReject={onReject}
      />,
    )

    const dialog = screen.getByRole('dialog', { name: 'Connect to Shared Backend' })
    expect(within(dialog).getByText('https://friend.example.test')).toBeInTheDocument()
    expect(within(dialog).getByText('Server address: https://friend.example.test/api')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(onReject).toHaveBeenCalledOnce()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Trust and Connect' }))
    expect(onConfirm).toHaveBeenCalledOnce()
  })
})
