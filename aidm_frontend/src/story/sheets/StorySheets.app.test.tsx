/// <reference types="node" />
// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it } from 'vitest'
import { App, appTestState, loadConsoleChunks, renderStoryApp, setupAppTest, teardownAppTest } from '../../App.testHarness'

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

function openMenuItem(name: string) {
  fireEvent.click(screen.getAllByRole('button', { name: 'Adventure menu' })[0]!)
  fireEvent.click(screen.getByRole('menuitem', { name }))
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

    const connection = await screen.findByRole('dialog', { name: 'Backend Settings' })
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
  })
})
