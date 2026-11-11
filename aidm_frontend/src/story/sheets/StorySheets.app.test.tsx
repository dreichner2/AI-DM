/// <reference types="node" />
// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  App,
  appTestState,
  jsonResponse,
  loadConsoleChunks,
  renderStoryApp,
  setupAppTest,
  teardownAppTest,
} from '../../App.testHarness'

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

function openMenuItem(name: string) {
  fireEvent.click(screen.getAllByRole('button', { name: 'Adventure menu' })[0]!)
  fireEvent.click(screen.getByRole('menuitem', { name }))
}

function narratorUpdates() {
  return appTestState.fetchCalls.filter((call) => call.method === 'PATCH' && call.path === '/api/llm/config')
}

/** Answers narrator updates with `refusals` in turn, then lets the harness accept them. */
function refuseNarratorUpdates(...refusals: Array<{ error: string; error_code: string; status: number }>) {
  const harnessFetch = globalThis.fetch
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = new URL(String(input), 'http://localhost:3000').pathname
    if (init?.method === 'PATCH' && path === '/api/llm/config' && refusals.length) {
      // Through the harness first, so the request is recorded like any other.
      await harnessFetch(input, init)
      const { status, ...payload } = refusals.shift()!
      return jsonResponse(payload, { status })
    }
    return harnessFetch(input, init)
  }))
}

describe('Sheets opened from the Story menu', () => {
  beforeEach(setupAppTest)
  afterEach(teardownAppTest)

  it('opens Settings, where turning music off removes the scene music and the view can change', async () => {
    await loadConsoleChunks()
    await renderStoryApp()
    expect(screen.getByRole('button', { name: 'Scene music' })).toBeInTheDocument()

    openMenuItem('Settings')
    const settings = await screen.findByRole('dialog', { name: 'Settings' })
    fireEvent.click(within(within(settings).getByRole('group', { name: 'Music' })).getByRole('button', { name: 'Off' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Scene music' })).not.toBeInTheDocument())

    fireEvent.click(within(within(settings).getByRole('group', { name: 'Play in' })).getByRole('button', { name: 'Table tools' }))
    expect(await screen.findByRole('heading', { name: /Session Alpha/i })).toBeInTheDocument()
    expect(localStorage.getItem('aidm:playView')).toBe('console')
    expect(screen.getByRole('dialog', { name: 'Settings' })).toBeInTheDocument()
  })

  it('opens connection settings from the Settings disclosure', async () => {
    await renderStoryApp()

    openMenuItem('Settings')
    const settings = await screen.findByRole('dialog', { name: 'Settings' })
    expect(within(settings).getByText('At a local table on this computer.')).toBeInTheDocument()
    fireEvent.click(within(settings).getByText('Table and connection'))
    fireEvent.click(within(settings).getByRole('button', { name: 'Connection settings' }))

    const connection = await screen.findByRole('dialog', { name: 'Connection' })
    expect(within(connection).getByText('Only change this if you run the game server somewhere else.')).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Settings' })).not.toBeInTheDocument()
  })

  it('has no Account item at a local table nobody signs in to', async () => {
    await renderStoryApp()

    fireEvent.click(screen.getAllByRole('button', { name: 'Adventure menu' })[0]!)
    const menu = screen.getByRole('menu', { name: 'Adventure menu' })
    expect(within(menu).getByRole('menuitem', { name: 'Settings' })).toBeInTheDocument()
    expect(within(menu).queryByRole('menuitem', { name: 'Account' })).not.toBeInTheDocument()
  })

  it('shows a signed-in player who and where they are, and Switch table opens the table list', async () => {
    appTestState.health.auth_required = true
    sessionStorage.setItem('aidm:authToken', 'account-token')
    sessionStorage.setItem(
      'aidm:account',
      JSON.stringify({
        accountId: 1,
        username: 'danny',
        displayName: 'Danny Reichner',
        workspaceId: 'owner',
        workspaceRole: 'admin',
        isWorkspaceAdmin: true,
        workspaces: [
          { workspace_id: 'owner', workspace_role: 'admin', is_workspace_admin: true, created_at: null, updated_at: null },
        ],
      }),
    )
    localStorage.setItem('aidm:workspaceId', 'owner')
    localStorage.removeItem('aidm:playView')
    window.history.replaceState(null, '', '/?campaign=10&session=20')
    render(<App />)
    await screen.findByRole('log', { name: 'Session timeline' })

    openMenuItem('Account')
    const account = await screen.findByRole('dialog', { name: 'Account' })
    expect(within(account).getByText('Danny Reichner')).toBeInTheDocument()
    expect(within(account).getByText(/^At .+ · admin$/)).toBeInTheDocument()

    fireEvent.click(within(account).getByRole('button', { name: 'Switch table' }))
    const tables = await screen.findByRole('group', { name: 'Saved tables' })
    expect(within(tables).getByText('Here now')).toBeInTheDocument()
    expect(screen.getByRole('dialog', { name: 'Account' })).toHaveAccessibleDescription(
      'Enter the code for the table you want to join.',
    )
  })

  it('opens Your adventures from Switch adventure and offers a new one', async () => {
    await renderStoryApp()

    openMenuItem('Switch adventure')
    const adventures = await screen.findByRole('dialog', { name: 'Your adventures' })
    expect(within(adventures).getByText('Here now')).toBeInTheDocument()
    fireEvent.click(within(adventures).getByRole('button', { name: 'New adventure' }))

    const newAdventure = await screen.findByRole('dialog', { name: 'New adventure' })
    expect(within(newAdventure).getByRole('heading', { name: 'Choose a beginning' })).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Your adventures' })).not.toBeInTheDocument()
    expect(within(newAdventure).getByRole('button', { name: 'Begin with a blank page' })).toBeEnabled()
  })

  it('offers no blank page while the scripted narrator runs the table', async () => {
    appTestState.health.llm!.provider = 'fallback'
    appTestState.health.llm!.model = 'deterministic-v1'
    await renderStoryApp()

    openMenuItem('Switch adventure')
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Your adventures' })).getByRole('button', { name: 'New adventure' }))
    const newAdventure = await screen.findByRole('dialog', { name: 'New adventure' })
    expect(within(newAdventure).getByText('Needs an AI narrator — choose one in Settings')).toBeInTheDocument()
    expect(within(newAdventure).getByRole('button', { name: 'Begin with a blank page' })).toBeDisabled()
  })

  it('opens Settings on the narrator and changes it with the table’s update request', async () => {
    await renderStoryApp()
    const reads = () => appTestState.fetchCalls.filter((call) => call.method === 'GET' && call.path === '/api/llm/config').length
    const readsBefore = reads()

    openMenuItem('Settings')
    const settings = await screen.findByRole('dialog', { name: 'Settings' })
    await waitFor(() => expect(reads()).toBe(readsBefore + 1))
    const narrator = within(settings).getByRole('region', { name: 'Narrator' })
    expect(narrator).toHaveTextContent('DeepSeek · DeepSeek V4 Pro')
    const choices = within(narrator).getByRole('group', { name: 'Narrator' })
    expect(within(choices).getAllByRole('button').map((choice) => choice.textContent)).toEqual(['Scripted narrator', 'DeepSeek'])

    fireEvent.click(within(choices).getByRole('button', { name: 'Scripted narrator' }))
    await waitFor(() => expect(narrator).toHaveTextContent('NarratorScripted narrator'))
    expect(narratorUpdates().map((call) => call.body)).toEqual([
      { provider: 'fallback', model: 'deterministic-v1', persist: true },
    ])
  })

  it('keeps the narrator and shows the server’s refusal at the foot of the open sheet', async () => {
    await renderStoryApp()
    refuseNarratorUpdates({
      error: 'Only the owner table admin can change global LLM runtime config.',
      error_code: 'runtime_config_admin_required',
      status: 403,
    })

    openMenuItem('Settings')
    const settings = await screen.findByRole('dialog', { name: 'Settings' })
    const narrator = within(settings).getByRole('region', { name: 'Narrator' })
    fireEvent.click(within(narrator).getByRole('button', { name: 'Scripted narrator' }))

    await waitFor(() =>
      expect(within(settings).getByRole('status')).toHaveTextContent(
        'Only the owner table admin can change global LLM runtime config.',
      ),
    )
    expect(narratorUpdates()).toHaveLength(1)
    expect(narrator).toHaveTextContent('DeepSeek · DeepSeek V4 Pro')
    expect(within(narrator).getByRole('button', { name: 'DeepSeek' })).toHaveAttribute('aria-pressed', 'true')
    // The sheet carries the line; the dock behind it does not repeat it.
    expect(document.querySelector('.story-composer-slot .story-notices')).toBeNull()

    fireEvent.click(within(settings).getByRole('button', { name: 'Close settings' }))
    const dockLine = document.querySelector('.story-composer-slot .story-notices') as HTMLElement
    expect(within(dockLine).getByRole('status')).toHaveTextContent(
      'Only the owner table admin can change global LLM runtime config.',
    )
  })

  it('applies the narrator for this run when the server may not save the choice', async () => {
    await renderStoryApp()
    refuseNarratorUpdates({
      error: 'Persisting LLM config from the API is disabled outside local/test environments.',
      error_code: 'llm_config_persist_disabled',
      status: 403,
    })

    openMenuItem('Settings')
    const settings = await screen.findByRole('dialog', { name: 'Settings' })
    const narrator = within(settings).getByRole('region', { name: 'Narrator' })
    fireEvent.click(within(narrator).getByRole('button', { name: 'Scripted narrator' }))

    await waitFor(() => expect(narrator).toHaveTextContent('NarratorScripted narrator'))
    expect(narratorUpdates().map((call) => call.body)).toEqual([
      { provider: 'fallback', model: 'deterministic-v1', persist: true },
      { provider: 'fallback', model: 'deterministic-v1', persist: false },
    ])
    expect(within(settings).getByRole('status')).toBeEmptyDOMElement()
  })
})
