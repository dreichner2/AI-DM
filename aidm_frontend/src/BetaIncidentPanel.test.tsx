// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { BetaIncidentPanel } from './BetaIncidentPanel'

const apiFetchMock = vi.hoisted(() => vi.fn())

vi.mock('./api', () => ({
  ApiClientError: class ApiClientError extends Error {},
  apiFetch: apiFetchMock,
}))

function supportBundleCalls() {
  return apiFetchMock.mock.calls.filter(([, path]) =>
    String(path).startsWith('/api/beta/support-bundle?'),
  )
}

describe('BetaIncidentPanel support bundle export', () => {
  beforeEach(() => {
    apiFetchMock.mockImplementation((_baseUrl: string, path: string) => {
      if (path.startsWith('/api/beta/incidents?')) {
        return Promise.resolve({
          summary: {},
          incidents: [
            {
              type: 'failed_turn',
              severity: 'high',
              session_id: 42,
              message: 'The turn needs operator review.',
            },
          ],
        })
      }
      if (path.startsWith('/api/beta/session-quality?')) {
        return Promise.resolve({
          session: { session_id: 42, name: 'Smoke Session' },
          summary: {},
          provider_model_turn_counts: [],
          operator_summary: {},
        })
      }
      if (path.startsWith('/api/beta/support-bundle?')) {
        return Promise.resolve({ generated_at: '2026-07-10T00:00:00Z' })
      }
      return Promise.reject(new Error(`Unexpected API path: ${path}`))
    })
  })

  afterEach(() => {
    cleanup()
    apiFetchMock.mockReset()
    vi.restoreAllMocks()
  })

  it('requires confirmation before workspace or session support bundle downloads', async () => {
    const createObjectURL = vi.fn(() => 'blob:support-bundle')
    const revokeObjectURL = vi.fn()
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: createObjectURL,
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: revokeObjectURL,
    })
    const downloadClick = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => undefined)

    render(
      <BetaIncidentPanel
        baseUrl="https://backend.example.test"
        auth="Bearer test-token"
        selectedSessionId={42}
      />,
    )

    const workspaceExport = await screen.findByRole('button', {
      name: 'Export workspace support bundle',
    })
    const sessionExport = await screen.findByRole('button', {
      name: 'Export support bundle for session 42',
    })

    fireEvent.click(workspaceExport)
    const workspaceDialog = screen.getByRole('dialog', { name: 'Confirm support bundle export' })
    expect(workspaceDialog).toHaveAccessibleDescription(
      /bundle can contain session IDs, provider\/model metadata, and audit references and should be handled as operator data/i,
    )
    expect(within(workspaceDialog).getByText('workspace support bundle')).toBeInTheDocument()
    await waitFor(() => expect(within(workspaceDialog).getByRole('button', { name: 'Cancel' })).toHaveFocus())
    expect(supportBundleCalls()).toHaveLength(0)

    fireEvent.click(within(workspaceDialog).getByRole('button', { name: 'Cancel' }))
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Confirm support bundle export' })).not.toBeInTheDocument(),
    )
    expect(supportBundleCalls()).toHaveLength(0)
    expect(workspaceExport).toHaveFocus()

    fireEvent.click(sessionExport)
    const sessionDialog = screen.getByRole('dialog', { name: 'Confirm support bundle export' })
    expect(within(sessionDialog).getByText('session 42 support bundle')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Confirm support bundle export' })).not.toBeInTheDocument(),
    )
    expect(supportBundleCalls()).toHaveLength(0)
    expect(sessionExport).toHaveFocus()

    fireEvent.click(sessionExport)
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Confirm support bundle export' })).getByRole('button', {
        name: 'Download bundle',
      }),
    )
    await waitFor(() => expect(supportBundleCalls()).toHaveLength(1))
    expect(supportBundleCalls()[0]?.[1]).toContain('session_id=42')

    fireEvent.click(workspaceExport)
    fireEvent.click(
      within(screen.getByRole('dialog', { name: 'Confirm support bundle export' })).getByRole('button', {
        name: 'Download bundle',
      }),
    )
    await waitFor(() => expect(supportBundleCalls()).toHaveLength(2))
    expect(supportBundleCalls()[1]?.[1]).not.toContain('session_id=')
    expect(createObjectURL).toHaveBeenCalledTimes(2)
    expect(downloadClick).toHaveBeenCalledTimes(2)
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:support-bundle')
  })
})
