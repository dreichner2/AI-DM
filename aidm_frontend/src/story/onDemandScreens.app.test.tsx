/// <reference types="node" />
// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  App,
  appTestState,
  renderLoadedApp,
  renderStoryApp,
  setupAppTest,
  teardownAppTest,
} from '../App.testHarness'
import { accountSheetChunk, sessionDialogsChunk, settingsSheetChunk } from './storyChunks'

// Screens the Story view opens on demand, recorded when each module is first imported.
const loads = vi.hoisted(() => new Set<string>())
vi.mock('./sheets/StoryAccountSheet', async (importOriginal) => {
  loads.add('StoryAccountSheet')
  return importOriginal()
})
vi.mock('./sheets/StorySettingsSheet', async (importOriginal) => {
  loads.add('StorySettingsSheet')
  return importOriginal()
})
vi.mock('../CampaignSessionActionDialogs', async (importOriginal) => {
  loads.add('CampaignSessionActionDialogs')
  return importOriginal()
})
vi.mock('./StoryDrawer', async (importOriginal) => {
  loads.add('StoryDrawer')
  return importOriginal()
})
vi.mock('./drawer/CharacterSection', async (importOriginal) => {
  loads.add('CharacterSection')
  return importOriginal()
})
vi.mock('./drawer/PackSection', async (importOriginal) => {
  loads.add('PackSection')
  return importOriginal()
})
vi.mock('./drawer/MagicSection', async (importOriginal) => {
  loads.add('MagicSection')
  return importOriginal()
})
vi.mock('./drawer/JournalSection', async (importOriginal) => {
  loads.add('JournalSection')
  return importOriginal()
})
vi.mock('../ttsNarrationQueue', async (importOriginal) => {
  loads.add('ttsNarrationQueue')
  return importOriginal()
})

let idleCallbacks: Array<() => void> = []

function runIdleCallbacks() {
  const callbacks = idleCallbacks
  idleCallbacks = []
  act(() => callbacks.forEach((callback) => callback()))
}

function openMenuItem(name: string) {
  fireEvent.click(screen.getAllByRole('button', { name: 'Adventure menu' })[0]!)
  fireEvent.click(screen.getByRole('menuitem', { name }))
}

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

beforeEach(() => {
  setupAppTest()
  idleCallbacks = []
  vi.stubGlobal('requestIdleCallback', vi.fn((callback: () => void) => idleCallbacks.push(callback)))
  vi.stubGlobal('cancelIdleCallback', vi.fn())
})

afterEach(teardownAppTest)

// The first test sees a fresh module registry: it runs first so that nothing has fetched these yet.
describe('Screens the Story view opens on demand', () => {
  it('are not fetched with the first story, and the drawer follows once the browser is idle', async () => {
    await renderStoryApp()

    expect([...loads]).toEqual([])
    runIdleCallbacks()
    await waitFor(() =>
      expect([...loads].sort()).toEqual(['CharacterSection', 'JournalSection', 'MagicSection', 'PackSection', 'StoryDrawer']),
    )
  })

  it('fetch the chapter dialogs and the Settings and Account sheets once Table tools shows', async () => {
    const preloads = [sessionDialogsChunk, settingsSheetChunk, accountSheetChunk].map((chunk) => vi.spyOn(chunk, 'preload'))
    await renderLoadedApp()

    preloads.forEach((preload) => expect(preload).not.toHaveBeenCalled())
    runIdleCallbacks()
    preloads.forEach((preload) => expect(preload).toHaveBeenCalledOnce())
  })

  it('show the Settings sheet once its menu item is chosen, with no loading state', async () => {
    await renderStoryApp()

    openMenuItem('Settings')
    expect(screen.queryByText('One moment. Your adventure is safe.')).not.toBeInTheDocument()
    expect(await screen.findByRole('dialog', { name: 'Settings' })).toBeInTheDocument()
  })

  it('show the Account sheet once its menu item is chosen', async () => {
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
    expect(within(await screen.findByRole('dialog', { name: 'Account' })).getByText('Danny Reichner')).toBeInTheDocument()
  })

  it('show the chapter dialog once Start next chapter is chosen', async () => {
    await renderStoryApp()

    openMenuItem('Start next chapter')
    expect(await screen.findByRole('dialog', { name: 'Start the next chapter' })).toBeInTheDocument()
  })

  it('show the character panel and each of its sections once their controls are used', async () => {
    await renderStoryApp()
    expect(screen.queryByRole('tablist', { name: 'Character panel sections' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Open character panel' }))
    const tabs = within(await screen.findByRole('tablist', { name: 'Character panel sections' })).getAllByRole('tab')
    for (const tab of tabs.filter((candidate) => candidate.textContent !== 'Map')) {
      fireEvent.click(tab)
      await waitFor(() => expect(screen.getByRole('tabpanel')).toHaveAttribute('aria-labelledby', tab.id))
      await waitFor(() => expect(screen.getByRole('tabpanel')).not.toBeEmptyDOMElement())
    }
  })
})
