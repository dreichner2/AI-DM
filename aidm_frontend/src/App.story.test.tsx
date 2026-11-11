/// <reference types="node" />
// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ClarificationRequest, SessionSummary } from './types'
import {
  App,
  appTestState,
  fixedNow,
  jsonResponse,
  loadConsoleChunks,
  renderLoadedApp,
  renderStoryApp,
  setupAppTest,
  socketHandler,
  socketMock,
  teardownAppTest,
} from './App.testHarness'

// The classic console is a lazy chunk: record when each of its modules is first imported.
const consoleModuleLoads = vi.hoisted(() => [] as string[])
vi.mock('./SessionBoard', async (importOriginal) => {
  consoleModuleLoads.push('SessionBoard')
  return importOriginal()
})
vi.mock('./InspectorPanel', async (importOriginal) => {
  consoleModuleLoads.push('InspectorPanel')
  return importOriginal()
})
vi.mock('./CampaignRail', async (importOriginal) => {
  consoleModuleLoads.push('CampaignRail')
  return importOriginal()
})

const CHAPTER_ENDED_NOTICE = 'That chapter has ended — you are now in the current chapter.'

function sendMessageEmits() {
  return socketMock.socket.emit.mock.calls.filter(([event]) => event === 'send_message')
}

async function joinSocket() {
  await act(async () => {
    socketHandler<void>('connect')()
    socketHandler<Array<{ id: number; character_name: string; name: string }>>('active_players')([
      { id: 30, character_name: 'Ember', name: 'Danny' },
    ])
  })
}

/** Session 20 has ended; session 21 is the campaign's current chapter. */
function moveCampaignToChapterTwo() {
  const ended: SessionSummary = {
    ...appTestState.sessionsByCampaign[10][0]!,
    status: 'ended',
    is_current: false,
    chapter_number: 1,
  }
  const current: SessionSummary = {
    ...ended,
    session_id: 21,
    display_name: 'Session Beta',
    status: 'active',
    is_current: true,
    chapter_number: 2,
    turn_count: 1,
  }
  appTestState.sessionsByCampaign[10] = [current, ended]
  appTestState.sessionLogs[21] = [
    {
      id: 40,
      entry_type: 'dm',
      message: 'DM: Morning finds the caravan on the far bank of the river.',
      metadata: { turn_id: 40, persistence_status: 'saved' },
      timestamp: fixedNow.toISOString(),
    },
  ]
  appTestState.sessionStates[21] = {
    ...appTestState.sessionStates[20],
    session_id: 21,
    state_snapshot: {},
  }
}

function stubDrawerViewport(wide: boolean) {
  vi.stubGlobal(
    'matchMedia',
    vi.fn((query: string) => ({
      matches: wide && query.includes('min-width: 1180px'),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  )
}

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

describe('Story mode in the app', () => {
  beforeEach(setupAppTest)
  afterEach(teardownAppTest)

  // First on purpose: once a later test opens the console its modules stay loaded.
  it('opens Story view by default without loading the classic console', async () => {
    const { container } = await renderStoryApp()

    expect(container.querySelector('.story-shell')).toBeInTheDocument()
    expect(container.querySelector('.ops-bar')).not.toBeInTheDocument()
    expect(container.querySelector('.campaign-rail')).not.toBeInTheDocument()
    expect(screen.queryByRole('tablist', { name: 'Inspector panels' })).not.toBeInTheDocument()
    expect(consoleModuleLoads).toEqual([])
  })

  it('treats an unknown stored view as Story and stays dark under the light console theme', async () => {
    localStorage.setItem('aidm:theme', 'light')
    localStorage.setItem('aidm:playView', 'theater')
    const { container } = render(<App />)

    await screen.findByRole('log', { name: 'Session timeline' })
    const shell = container.querySelector('.prototype-shell')
    expect(shell).toHaveClass('theme-dark', 'story-view-active')
    expect(shell).not.toHaveClass('theme-light')
  })

  it('shows every narration in full', async () => {
    await renderStoryApp()

    const feed = screen.getByRole('log', { name: 'Session timeline' })
    expect(feed).toHaveTextContent('Hidden tail for expansion verification.')
    expect(feed).toHaveTextContent('Full narrator ending remains visible.')
  })

  it('sends an action typed into the Story composer', async () => {
    await renderStoryApp()
    await joinSocket()

    const input = screen.getByLabelText('Your Action')
    fireEvent.change(input, { target: { value: 'I lift the lantern toward the brass walkway.' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
    expect(sendMessageEmits()[0]?.[1]).toMatchObject({
      session_id: 20,
      player_id: 30,
      message: 'I lift the lantern toward the brass walkway.',
    })
  })

  it('switches to the console and back without leaving the session', async () => {
    await loadConsoleChunks()
    await renderStoryApp()

    fireEvent.click(screen.getAllByRole('button', { name: 'Adventure menu' })[0]!)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Table tools' }))

    expect(await screen.findByRole('heading', { name: /Session Alpha/i })).toBeInTheDocument()
    expect(localStorage.getItem('aidm:playView')).toBe('console')
    expect(document.querySelector('.story-shell')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Return to Story view' }))
    const feed = await screen.findByRole('log', { name: 'Session timeline' })
    expect(feed).toHaveTextContent('Full narrator ending remains visible.')
    expect(localStorage.getItem('aidm:playView')).toBe('story')
    expect(localStorage.getItem('aidm:open:selectedSessionId')).toBe('20')
    expect(socketMock.io).toHaveBeenCalledTimes(1)
  })

  it('reads the table through the selected hero’s eyes in Story view, and in full in Table tools', async () => {
    await loadConsoleChunks()
    await renderStoryApp()
    const reads = () =>
      appTestState.fetchCalls
        .filter((call) => call.method === 'GET' && /^\/api\/(sessions\/20\/(state|log)|campaigns\/10\/workspace)$/.test(call.path))
        .map((call) => `${call.path}${call.search}`)

    await waitFor(() => expect(reads()).toEqual(expect.arrayContaining([
      '/api/sessions/20/state?view=player&player_id=30',
      '/api/sessions/20/log?limit=200&view=player&player_id=30',
      '/api/campaigns/10/workspace?view=player&player_id=30',
    ])))
    expect(reads().every((read) => read.includes('view=player&player_id=30'))).toBe(true)

    appTestState.fetchCalls.length = 0
    fireEvent.click(screen.getAllByRole('button', { name: 'Adventure menu' })[0]!)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Table tools' }))
    await waitFor(() => expect(reads()).toEqual(expect.arrayContaining([
      '/api/sessions/20/state',
      '/api/sessions/20/log?limit=200',
      '/api/campaigns/10/workspace',
    ])))
    expect(reads().some((read) => read.includes('view='))).toBe(false)
  })

  it('returns to Story view from the console View settings', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'View settings' }))
    fireEvent.click(within(screen.getByRole('group', { name: 'View settings' })).getByRole('button', { name: 'Story view' }))

    expect(await screen.findByRole('log', { name: 'Session timeline' })).toHaveTextContent('Full narrator ending')
    expect(document.querySelector('.ops-bar')).not.toBeInTheDocument()
  })

  it('remembers the drawer on wide screens and starts it closed on narrow ones', async () => {
    stubDrawerViewport(true)
    await renderStoryApp()

    fireEvent.click(screen.getByRole('button', { name: 'Open character panel' }))
    fireEvent.click(await screen.findByRole('tab', { name: 'Journal' }))
    expect(JSON.parse(localStorage.getItem('aidm:storyDrawer') ?? 'null')).toEqual({ open: true, tab: 'journal' })

    cleanup()
    await renderStoryApp()
    expect(screen.getByRole('tab', { name: 'Journal' })).toHaveAttribute('aria-selected', 'true')
    expect(document.querySelector('.story-shell')).toHaveAttribute('data-drawer-open')

    cleanup()
    stubDrawerViewport(false)
    await renderStoryApp()
    expect(document.querySelector('.story-shell')).not.toHaveAttribute('data-drawer-open')
    expect(screen.queryByRole('tab', { name: 'Journal' })).not.toBeInTheDocument()
    expect(JSON.parse(localStorage.getItem('aidm:storyDrawer') ?? 'null')).toEqual({ open: false, tab: 'journal' })
  })

  it('reads an ended chapter without joining it and continues to the current chapter', async () => {
    moveCampaignToChapterTwo()
    await renderStoryApp()

    expect(screen.getByText('This chapter has closed.')).toBeInTheDocument()
    expect(screen.queryByLabelText('Your Action')).not.toBeInTheDocument()
    expect(socketMock.io).not.toHaveBeenCalled()

    fireEvent.click(screen.getByRole('button', { name: 'Continue the adventure' }))

    expect(await screen.findByText(/Morning finds the caravan/)).toBeInTheDocument()
    expect(localStorage.getItem('aidm:open:selectedSessionId')).toBe('21')
    expect(screen.getByLabelText('Your Action')).toBeInTheDocument()
    await waitFor(() => expect(socketMock.io).toHaveBeenCalledTimes(1))
  })

  it('replaces the console composer with the continue action for an ended chapter', async () => {
    moveCampaignToChapterTwo()
    await renderLoadedApp()

    expect(screen.getByText('This chapter has closed.')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Your Action/i)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Continue the adventure' }))
    expect(await screen.findByRole('heading', { name: /Session Beta/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/Your Action/i)).toBeInTheDocument()
  })

  it('follows a session_ended socket error to the current chapter with a notice', async () => {
    await renderStoryApp()
    await joinSocket()
    moveCampaignToChapterTwo()

    await act(async () => {
      socketMock.serverEmit('error', {
        error_code: 'session_ended',
        error: 'This chapter has ended. Continue in the current chapter.',
        details: { current_session_id: 21 },
      })
    })

    expect(await screen.findByText(/Morning finds the caravan/)).toBeInTheDocument()
    expect(localStorage.getItem('aidm:open:selectedSessionId')).toBe('21')
    const notices = document.querySelector('.story-notices')
    expect(notices).toHaveAttribute('role', 'status')
    expect(notices).toHaveTextContent(CHAPTER_ENDED_NOTICE)
  })

  it('follows a session_ended REST refusal to the current chapter in the console', async () => {
    appTestState.sessionStates[20] = {
      ...appTestState.sessionStates[20],
      state_snapshot: {
        campaignPack: {
          packId: 'ash_road',
          title: 'The Ash Road',
          version: '1.0.0',
          activeCheckpointId: 'gate',
          checkpoints: [{ id: 'gate', title: 'The Broken Gate', nextCheckpointIds: [] }],
          checkpointStatuses: { gate: 'active' },
        },
      },
    }
    await renderLoadedApp()
    moveCampaignToChapterTwo()
    const harnessFetch = globalThis.fetch
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (new URL(String(input), 'http://localhost:3000').pathname === '/api/sessions/20/campaign-pack/progress') {
        return jsonResponse(
          {
            error: 'This chapter has ended. Continue in the current chapter.',
            error_code: 'session_ended',
            details: { current_session_id: 21 },
          },
          { status: 409 },
        )
      }
      return harnessFetch(input, init)
    }))

    fireEvent.click(await screen.findByRole('button', { name: 'Advance' }))

    expect(await screen.findByRole('heading', { name: /Session Beta/i })).toBeInTheDocument()
    expect(screen.getByText(CHAPTER_ENDED_NOTICE)).toBeInTheDocument()
    expect(screen.queryByText(/Campaign pack checkpoint update failed/)).not.toBeInTheDocument()
  })

  it('opens the current chapter when a campaign has no valid selection', async () => {
    moveCampaignToChapterTwo()
    // The current chapter is not the first item of the list.
    appTestState.sessionsByCampaign[10] = [...appTestState.sessionsByCampaign[10]].reverse()
    localStorage.removeItem('aidm:selectedSessionId')
    localStorage.removeItem('aidm:playView')
    render(<App />)

    expect(await screen.findByText(/Morning finds the caravan/)).toBeInTheDocument()
    expect(localStorage.getItem('aidm:open:selectedSessionId')).toBe('21')
  })

  it('resolves a clarification from the chips above the Story composer', async () => {
    await renderStoryApp()
    await joinSocket()
    const clarification: ClarificationRequest = {
      id: 'clarify_77_001',
      turnId: 77,
      sessionId: 20,
      playerId: 30,
      type: 'item_resolution',
      prompt: 'Which sword do you use?',
      originalPlayerMessage: 'I swing my sword at the goblin.',
      originalAction: { id: 'act_001', type: 'combat.attack' },
      options: [
        { itemId: 'great', label: 'Greatsword', description: 'weapon' },
        { itemId: 'long', label: 'Longsword' },
      ],
    }

    await act(async () => {
      socketHandler<ClarificationRequest>('clarification_required')(clarification)
    })
    const question = screen.getByRole('region', { name: 'The Dungeon Master asks' })
    expect(question).toHaveTextContent('Which sword do you use?')
    fireEvent.click(within(question).getByRole('button', { name: 'Longsword' }))

    expect(socketMock.socket.emit).toHaveBeenCalledWith(
      'resolve_clarification',
      expect.objectContaining({ session_id: 20, player_id: 30, turn_id: 77, selected_item_id: 'long' }),
    )
    expect(screen.queryByRole('region', { name: 'The Dungeon Master asks' })).not.toBeInTheDocument()
  })
})
