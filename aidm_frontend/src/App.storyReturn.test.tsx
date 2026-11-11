// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { returnChunk } from './story/storyChunks'
import type { SessionSummary } from './types'
import {
  appTestState,
  fixedNow,
  jsonResponse,
  renderStoryApp,
  setupAppTest,
  socketHandler,
  socketMock,
  teardownAppTest,
} from './App.testHarness'

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

/** Routes the given paths to their handlers; everything else to the harness's own fetch. */
function routeFetch(routes: Record<string, (body: unknown) => Response>) {
  const harnessFetch = globalThis.fetch
  const calls: Array<{ method: string; path: string; body: unknown }> = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = new URL(String(input), 'http://localhost:3000').pathname
      const method = init?.method ?? 'GET'
      const route = routes[`${method} ${path}`]
      if (!route) return harnessFetch(input, init)
      const body = init?.body ? JSON.parse(String(init.body)) : null
      calls.push({ method, path, body })
      return route(body)
    }),
  )
  return calls
}

beforeAll(async () => {
  Element.prototype.scrollTo = () => {}
  returnChunk.preload()
  await import('./story/StoryReturn')
})

describe('bringing the party back, in the app', () => {
  beforeEach(setupAppTest)
  afterEach(teardownAppTest)

  it('notes /plan words as a plan instead of sending them as an action', async () => {
    const calls = routeFetch({
      'POST /api/sessions/20/plans': () => jsonResponse({ session_id: 20, plans: [], plan: { id: 'plan_1', text: 'Ask Osric about the letter' } }),
    })
    await renderStoryApp()
    await joinSocket()

    const input = screen.getByLabelText('Your Action')
    fireEvent.change(input, { target: { value: '/plan Ask Osric about the letter' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => expect(calls).toEqual([{ method: 'POST', path: '/api/sessions/20/plans', body: { text: 'Ask Osric about the letter', player_id: 30 } }]))
    await waitFor(() => expect(input).toHaveValue(''))
    expect(sendMessageEmits()).toHaveLength(0)
  })

  it('after a chapter break the next action begins the next chapter and is sent there', async () => {
    appTestState.sessionLogs[20] = [
      ...appTestState.sessionLogs[20]!,
      {
        id: 99,
        entry_type: 'dm',
        message: 'DM: The road north opens under the rain.',
        metadata: {
          turn_id: 99,
          persistence_status: 'saved',
          storyBeats: [{ kind: 'chapter_end', text: 'End of chapter 1 · The Empty Caravan', chapterEnd: { number: 1, title: 'The Empty Caravan', decided: [], open: [] } }],
        },
        timestamp: fixedNow.toISOString(),
      },
    ]
    const calls = routeFetch({
      'POST /api/sessions/20/next-chapter': () => {
        const ended: SessionSummary = { ...appTestState.sessionsByCampaign[10][0]!, status: 'ended', is_current: false, chapter_number: 1 }
        const current: SessionSummary = { ...ended, session_id: 21, display_name: 'Chapter 2', status: 'active', is_current: true, chapter_number: 2, turn_count: 0 }
        appTestState.sessionsByCampaign[10] = [current, ended]
        appTestState.sessionLogs[21] = []
        appTestState.sessionStates[21] = { ...appTestState.sessionStates[20], session_id: 21, state_snapshot: {} }
        return jsonResponse({ session_id: 21, campaign_id: 10, idempotent_replay: false }, { status: 201 })
      },
    })
    await renderStoryApp()
    await joinSocket()
    expect(await screen.findByRole('region', { name: 'End of chapter 1 · The Empty Caravan' })).toBeInTheDocument()

    const input = screen.getByLabelText('Your Action')
    fireEvent.change(input, { target: { value: 'I take the north road.' } })
    fireEvent.keyDown(input, { key: 'Enter' })

    await waitFor(() => expect(calls.map((call) => call.path)).toEqual(['/api/sessions/20/next-chapter']))
    expect(sendMessageEmits()).toHaveLength(0)
    await waitFor(() => expect(localStorage.getItem('aidm:open:selectedSessionId')).toBe('21'))
    await joinSocket()
    await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
    expect(sendMessageEmits()[0]?.[1]).toMatchObject({ session_id: 21, player_id: 30, message: 'I take the north road.' })
  })
})
