/// <reference types="node" />
// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import type { Campaign, JsonRecord, SessionSummary } from './types'
import {
  App,
  appTestState,
  fixedNow,
  jsonResponse,
  renderStoryApp,
  sessionSummaryFixture,
  setupAppTest,
  socketHandler,
  socketMock,
  teardownAppTest,
} from './App.testHarness'

const OLD_NOTICES = /Choose a campaign, session, and player|Wait for the current DM response/

function sendMessageEmits() {
  return socketMock.socket.emit.mock.calls.filter(([event]) => event === 'send_message')
}

async function joinSocket(id = 30, characterName = 'Ember') {
  await act(async () => {
    socketHandler<void>('connect')()
    socketHandler<Array<{ id: number; character_name: string; name: string }>>('active_players')([
      { id, character_name: characterName, name: 'Danny' },
    ])
  })
}

/** Session 20 is the adventure's own first chapter, with nothing played in it yet. */
function unplayedFirstChapter({ hero }: { hero: boolean }) {
  appTestState.sessionsByCampaign[10] = [
    { ...appTestState.sessionsByCampaign[10][0]!, turn_count: 0, chapter_number: 1, display_name: 'Smoke Campaign Opening' },
  ]
  appTestState.sessionLogs[20] = []
  if (!hero) {
    appTestState.playersByCampaign[10] = []
    localStorage.removeItem('aidm:selectedPlayerId')
  }
  localStorage.removeItem('aidm:playView')
}

/** The server refuses to read an adventure as a hero who is not in it, as `viewer_projection` does. */
function refuseHeroesNotInTheirAdventure() {
  const harnessFetch = globalThis.fetch
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), 'http://localhost:3000')
    const heroId = Number(url.searchParams.get('player_id'))
    const campaignId = Number(url.pathname.match(/^\/api\/campaigns\/(\d+)\/workspace$/)?.[1])
    const sessionId = Number(url.pathname.match(/^\/api\/sessions\/(\d+)\/(?:log|state)$/)?.[1])
    const sessionCampaignId = Object.values(appTestState.sessionsByCampaign).flat()
      .find((session) => session.session_id === sessionId)?.campaign_id
    const readCampaignId = campaignId || sessionCampaignId
    if (heroId && readCampaignId && !(appTestState.playersByCampaign[readCampaignId] ?? []).some((player) => player.player_id === heroId)) {
      appTestState.fetchCalls.push({
        method: init?.method ?? 'GET',
        path: url.pathname,
        search: url.search,
        origin: url.origin,
        body: null,
        authorization: null,
        workspaceToken: null,
        workspaceIdHeader: null,
      })
      return jsonResponse(
        { error: 'You can only view the story as one of your own heroes.', error_code: 'forbidden' },
        { status: 403 },
      )
    }
    return harnessFetch(input, init)
  }))
}

function reads(pattern: RegExp) {
  return appTestState.fetchCalls.filter((call) => call.method === 'GET' && pattern.test(call.path)).map((call) => `${call.path}${call.search}`)
}

async function createHeroNamed(name: string) {
  const creator = await screen.findByRole('dialog', { name: /^A new hero/ })
  fireEvent.change(within(creator).getByLabelText('Hero name'), { target: { value: name } })
  fireEvent.click(within(creator).getByRole('radio', { name: 'Dwarf' }))
  fireEvent.click(within(creator).getByRole('button', { name: 'Continue' }))
  fireEvent.click(within(creator).getByRole('radio', { name: /^Cleric/ }))
  fireEvent.click(within(creator).getByRole('button', { name: 'Continue' }))
  fireEvent.click(within(creator).getByRole('checkbox', { name: 'Medicine' }))
  fireEvent.click(within(creator).getByRole('checkbox', { name: 'Persuasion' }))
  fireEvent.click(within(creator).getByRole('button', { name: 'Continue' }))
  fireEvent.click(within(creator).getByRole('button', { name: `Begin as ${name}` }))
  await waitFor(() => expect(screen.queryByRole('dialog', { name: /^A new hero/ })).not.toBeInTheDocument())
}

function typeAndSend(text: string) {
  const input = screen.getByLabelText('Your Action')
  fireEvent.change(input, { target: { value: text } })
  fireEvent.keyDown(input, { key: 'Enter' })
}

async function serverEmit(event: string, payload: unknown) {
  await act(async () => {
    socketMock.serverEmit(event, payload)
  })
}

/** The turn the first action started finishes: its narration ends and the engine's log update arrives. */
async function finishTurn(turnId: number) {
  await serverEmit('dm_response_start', { turn_id: turnId })
  await serverEmit('dm_response_end', { turn_id: turnId, text: 'The door holds.' })
  await serverEmit('session_log_update', { session_id: 20, turn_id: turnId })
}

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

describe('Beginning an adventure you chose yourself', () => {
  beforeEach(setupAppTest)
  afterEach(teardownAppTest)

  it('opens the hero creator from Begin when the adventure has no hero, then begins in its own first chapter once', async () => {
    unplayedFirstChapter({ hero: false })
    render(<App />)

    // Arriving, the chooser offers the first hero; set aside, Begin brings the creator straight up.
    const chooser = await screen.findByRole('dialog', { name: 'Choose your character' })
    fireEvent.click(within(chooser).getByRole('button', { name: 'Cancel' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Begin the adventure' }))
    await createHeroNamed('Borin')

    await waitFor(() => expect(socketMock.io).toHaveBeenCalled())
    await joinSocket(100, 'Borin')
    await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
    expect(sendMessageEmits()[0]?.[1]).toMatchObject({ session_id: 20, campaign_id: 10, player_id: 100, message_kind: 'opening' })
    expect(appTestState.fetchCalls.some((call) => call.path === '/api/sessions/start')).toBe(false)

    // Nothing asks for the opening a second time.
    await joinSocket(100, 'Borin')
    expect(sendMessageEmits()).toHaveLength(1)
    expect(document.body).not.toHaveTextContent(OLD_NOTICES)
  })

  it('begins a new adventure from a pack in its own first chapter: Begin, the creator, the opening, once', async () => {
    await renderStoryApp()
    fireEvent.click(screen.getAllByRole('button', { name: 'Adventure menu' })[0]!)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Switch adventure' }))
    fireEvent.click(await screen.findByRole('button', { name: 'New adventure' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Begin Shadow Under Eryn Luin' }))

    // The previous adventure's hero stays behind; the new one has none, so the creator comes straight up.
    await createHeroNamed('Borin')
    expect(screen.queryByRole('dialog', { name: 'Choose your character' })).not.toBeInTheDocument()
    expect(reads(/^\/api\/campaigns\/101\/workspace$/)).not.toContain('/api/campaigns/101/workspace?view=player&player_id=30')

    await waitFor(() => expect(socketMock.io).toHaveBeenCalledTimes(2))
    await joinSocket(100, 'Borin')
    await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
    expect(sendMessageEmits()[0]?.[1]).toMatchObject({ session_id: 201, campaign_id: 101, player_id: 100, message_kind: 'opening' })
    expect(appTestState.fetchCalls.some((call) => call.path === '/api/sessions/start')).toBe(false)
    await joinSocket(100, 'Borin')
    expect(sendMessageEmits()).toHaveLength(1)
    expect(document.body).not.toHaveTextContent(OLD_NOTICES)
  })

  it('opens the chooser from Begin when heroes can join, and begins once one is chosen', async () => {
    unplayedFirstChapter({ hero: true })
    localStorage.removeItem('aidm:selectedPlayerId')
    render(<App />)

    const arrival = await screen.findByRole('dialog', { name: 'Choose your character' })
    fireEvent.click(within(arrival).getByRole('button', { name: 'Cancel' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Begin the adventure' }))
    const chooser = await screen.findByRole('dialog', { name: 'Choose your character' })
    expect(within(chooser).getAllByRole('button').map((button) => button.textContent).filter(Boolean)).toEqual(['Join', 'Cancel', 'New character'])
    fireEvent.click(within(chooser).getByRole('button', { name: 'Join as Ember' }))

    await waitFor(() => expect(socketMock.io).toHaveBeenCalled())
    await joinSocket()
    await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
    expect(sendMessageEmits()[0]?.[1]).toMatchObject({ session_id: 20, player_id: 30, message_kind: 'opening' })
  })

  it('does not begin when the hero choice is left without a hero', async () => {
    unplayedFirstChapter({ hero: false })
    render(<App />)

    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Choose your character' })).getByRole('button', { name: 'Cancel' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Begin the adventure' }))
    const creator = await screen.findByRole('dialog', { name: /^A new hero/ })
    fireEvent.click(within(creator).getByRole('button', { name: 'Leave hero creation' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Discard' }))

    expect(await screen.findByRole('button', { name: 'Begin the adventure' })).toBeInTheDocument()
    expect(screen.queryByText('The story is beginning')).not.toBeInTheDocument()
    expect(sendMessageEmits()).toHaveLength(0)
  })

  it('begins an adventure whose one chapter is unplayed in that chapter, and a first chapter recalls no Previously', async () => {
    unplayedFirstChapter({ hero: true })
    render(<App />)

    fireEvent.click(await screen.findByRole('button', { name: 'Begin the adventure' }))
    await waitFor(() => expect(socketMock.io).toHaveBeenCalled())
    await joinSocket()
    await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
    expect(sendMessageEmits()[0]?.[1]).toMatchObject({ session_id: 20, message_kind: 'opening' })
    expect(appTestState.fetchCalls.some((call) => call.path === '/api/sessions/start')).toBe(false)
  })

  it('shows a first chapter that has begun without a Previously, whatever the recap says', async () => {
    appTestState.sessionsByCampaign[10] = [{ ...appTestState.sessionsByCampaign[10][0]!, turn_count: 1, chapter_number: 1 }]
    appTestState.sessionLogs[20] = [
      {
        id: 1,
        entry_type: 'system',
        message: '**Welcome to the table. Choose your opening move when you are ready.**',
        metadata: { kind: 'session_welcome' },
        timestamp: fixedNow.toISOString(),
      },
      {
        id: 2,
        entry_type: 'dm',
        message: 'DM: Evening mist gathers over the road outside Bree.',
        metadata: { turn_id: 1, persistence_status: 'saved' },
        timestamp: fixedNow.toISOString(),
      },
    ]
    const harnessFetch = globalThis.fetch
    let recapRead = false
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      if (new URL(String(input), 'http://localhost:3000').pathname === '/api/sessions/20/recap') {
        recapRead = true
        return jsonResponse({ recap: 'The adventure began. Evening mist gathers over the road outside Bree.' })
      }
      return harnessFetch(input, init)
    }))
    localStorage.removeItem('aidm:playView')
    render(<App />)

    const feed = await screen.findByRole('log', { name: 'Session timeline' })
    await waitFor(() => expect(feed).toHaveTextContent('Evening mist gathers over the road outside Bree.'))
    await waitFor(() => expect(recapRead).toBe(true))
    expect(within(feed).getByText('Chapter 1')).toBeInTheDocument()
    expect(within(feed).queryByText('Previously')).not.toBeInTheDocument()
  })

  it('shows an empty character panel with the way to a hero, and the hero choice when one can join', async () => {
    unplayedFirstChapter({ hero: false })
    render(<App />)
    fireEvent.click(within(await screen.findByRole('dialog', { name: 'Choose your character' })).getByRole('button', { name: 'Cancel' }))

    fireEvent.click(screen.getByRole('button', { name: 'Open character panel' }))
    const panel = await screen.findByRole('tabpanel')
    await waitFor(() => expect(panel).toHaveTextContent('No hero yet.'))
    expect(panel).not.toHaveTextContent(/player|Level/)
    expect(within(panel).queryByRole('button', { name: 'Choose a hero' })).not.toBeInTheDocument()
    fireEvent.click(within(panel).getByRole('button', { name: 'Create a hero' }))
    expect(await screen.findByRole('dialog', { name: /^A new hero/ })).toBeInTheDocument()
  })

  it('ends the Scene list of More… with the ways onward the Map tab lists', async () => {
    appTestState.sessionStates[20] = {
      ...appTestState.sessionStates[20]!,
      state_snapshot: {
        currentScene: { locationId: 'hall', name: 'Ash Hall' },
        locations: [
          { id: 'hall', name: 'Ash Hall', status: 'visited', connectedLocationIds: ['causeway'] },
          { id: 'causeway', name: 'North causeway', status: 'known' },
        ],
      },
    }
    await renderStoryApp()
    await joinSocket()

    fireEvent.click(screen.getByRole('button', { name: 'Actions' }))
    const scene = within(screen.getByRole('region', { name: 'Scene' }))
    fireEvent.click(scene.getByRole('button', { name: 'Go to North causeway' }))
    await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
    expect(sendMessageEmits()[0]?.[1]).toMatchObject({
      message: 'The party travels to North causeway.',
      action_intent: expect.objectContaining({ kind: 'travel', location: { id: 'causeway', name: 'North causeway' } }),
    })
  })
})

describe('A remembered choice the server refuses', () => {
  beforeEach(setupAppTest)
  afterEach(teardownAppTest)

  it('forgets a hero from another data folder and offers the hero choice instead of stranding the adventure', async () => {
    unplayedFirstChapter({ hero: true })
    localStorage.setItem('aidm:selectedPlayerId', '77')
    refuseHeroesNotInTheirAdventure()
    render(<App />)

    expect(await screen.findByRole('dialog', { name: 'Choose your character' })).toBeInTheDocument()
    expect(reads(/^\/api\/campaigns\/10\/workspace$/)).toEqual(expect.arrayContaining([
      '/api/campaigns/10/workspace?view=player&player_id=77',
      '/api/campaigns/10/workspace?view=player',
    ]))
    expect(localStorage.getItem('aidm:open:selectedPlayerId')).toBeNull()
    expect(document.body).not.toHaveTextContent(/Workspace load failed|own heroes/)

    // The adventure's own chapter is open: chosen, the hero begins there.
    fireEvent.click(within(screen.getByRole('dialog', { name: 'Choose your character' })).getByRole('button', { name: 'Join as Ember' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Begin the adventure' }))
    await waitFor(() => expect(socketMock.io).toHaveBeenCalled())
    await joinSocket()
    await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
    expect(sendMessageEmits()[0]?.[1]).toMatchObject({ session_id: 20, message_kind: 'opening' })
    expect(appTestState.fetchCalls.some((call) => call.path === '/api/sessions/start')).toBe(false)
  })

  it('reads another adventure as itself after switching: the previous hero, place and chapter stay behind', async () => {
    const erynLuin: Campaign = {
      ...appTestState.campaigns[0]!,
      campaign_id: 11,
      title: 'Shadow Under Eryn Luin',
      session_count: 1,
      latest_session_id: 25,
    }
    const opening: SessionSummary = sessionSummaryFixture({ session_id: 25, campaign_id: 11, display_name: 'Shadow Under Eryn Luin Opening' })
    appTestState.campaigns = [...appTestState.campaigns, erynLuin]
    appTestState.sessionsByCampaign[11] = [opening]
    appTestState.playersByCampaign[11] = []
    appTestState.mapsByCampaign[11] = []
    appTestState.segmentsByCampaign[11] = []
    appTestState.sessionLogs[25] = []
    refuseHeroesNotInTheirAdventure()
    await renderStoryApp()
    expect(document.querySelector('.story-topbar')).toHaveTextContent('Session Alpha')

    fireEvent.click(screen.getAllByRole('button', { name: 'Adventure menu' })[0]!)
    fireEvent.click(screen.getByRole('menuitem', { name: 'Switch adventure' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Continue Shadow Under Eryn Luin' }))

    expect(await screen.findByRole('dialog', { name: 'Choose your character' })).toHaveTextContent('Shadow Under Eryn Luin')
    expect(reads(/^\/api\/campaigns\/11\/workspace$/)).not.toContain('/api/campaigns/11/workspace?view=player&player_id=30')
    const header = document.querySelector('.story-topbar') as HTMLElement
    expect(header).toHaveTextContent('Shadow Under Eryn Luin')
    expect(header).not.toHaveTextContent(/Session Alpha|Smoke Campaign/)
    expect(localStorage.getItem('aidm:open:selectedPlayerId')).toBeNull()
  })
})

describe('Typing ahead while the turn finishes', () => {
  beforeEach(setupAppTest)
  afterEach(teardownAppTest)

  it('holds an action sent while the turn finishes, then sends it by itself when the turn unlocks', async () => {
    await renderStoryApp()
    await joinSocket()

    typeAndSend('I test the sealed door again.')
    await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
    typeAndSend('I draw my blade.')

    const dock = document.querySelector('.story-dock') as HTMLElement
    expect(within(dock).getByRole('status')).toHaveTextContent('Waiting for the story to catch up — "I draw my blade."')
    expect(screen.getByLabelText('Your Action')).toHaveValue('')
    expect(sendMessageEmits()).toHaveLength(1)
    expect(document.body).not.toHaveTextContent(OLD_NOTICES)

    await finishTurn(50)
    await waitFor(() => expect(sendMessageEmits()).toHaveLength(2))
    expect(sendMessageEmits()[1]?.[1]).toMatchObject({ session_id: 20, player_id: 30, message: 'I draw my blade.' })
    expect(document.body).not.toHaveTextContent('Waiting for the story to catch up')
  })

  it('gives the held words back to the box when the turn ends in a roll prompt', async () => {
    await renderStoryApp()
    await joinSocket()

    typeAndSend('I test the sealed door again.')
    await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
    const sent = sendMessageEmits()[0]?.[1] as { client_message_id: string }
    await serverEmit('new_message', {
      turn_id: 50,
      speaker: 'Ember',
      message: 'I test the sealed door again.',
      client_message_id: sent.client_message_id,
    })
    typeAndSend('I draw my blade.')
    await serverEmit('roll_required', {
      session_id: 20,
      pending_turn_id: 50,
      rule_type: 'ability_check',
      prompt: 'Roll Strength to force the door.',
      roll_spec: { die: 'd20', reason: 'Strength check' },
    })
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5))
    })

    expect(sendMessageEmits()).toHaveLength(1)
    expect(document.body).not.toHaveTextContent('Waiting for the story to catch up')
    expect(screen.getByLabelText('Your Action')).toHaveValue('I draw my blade.')
  })

  it('cancels a held action, giving its words back to the box', async () => {
    await renderStoryApp()
    await joinSocket()

    typeAndSend('I test the sealed door again.')
    await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
    typeAndSend('I draw my blade.')
    fireEvent.click(within(document.querySelector('.story-dock') as HTMLElement).getByRole('button', { name: 'Cancel' }))

    expect(screen.getByLabelText('Your Action')).toHaveValue('I draw my blade.')
    await finishTurn(50)
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5))
    })
    expect(sendMessageEmits()).toHaveLength(1)
  })
})

describe('Fights in the Story view', () => {
  beforeEach(setupAppTest)
  afterEach(teardownAppTest)

  it('offers what may be said to a captive and sends it as the player’s words', async () => {
    const snapshot: JsonRecord = {
      currentScene: {
        name: 'Ash Hall',
        captives: [
          {
            id: 'cutthroat',
            name: 'Ashmarked Cutthroat',
            enemyId: 'enemy_1',
            offeredActions: [{ kind: 'talk', label: 'Question the Ashmarked Cutthroat' }],
          },
        ],
      },
    }
    appTestState.sessionStates[20] = { ...appTestState.sessionStates[20]!, state_snapshot: snapshot }
    await renderStoryApp()
    await joinSocket()

    const suggestions = await screen.findByRole('group', { name: 'Suggestions' })
    fireEvent.click(within(suggestions).getByRole('button', { name: 'Question the Ashmarked Cutthroat' }))
    await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
    expect(sendMessageEmits()[0]?.[1]).toMatchObject({ message: 'Question the Ashmarked Cutthroat' })
  })

  it.each(['combat_action_unavailable', 'combat_action_invalid'])(
    'takes the sent line back and says why when the server refuses a fight action (%s)',
    async (errorCode) => {
      await renderStoryApp()
      await joinSocket()

      typeAndSend('I strike the goblin sentry.')
      await waitFor(() => expect(sendMessageEmits()).toHaveLength(1))
      const feed = screen.getByRole('log', { name: 'Session timeline' })
      expect(feed).toHaveTextContent('I strike the goblin sentry.')

      await serverEmit('error', { error_code: errorCode, error: 'Goblin Sentry is out of reach.' })

      expect(feed).not.toHaveTextContent('I strike the goblin sentry.')
      expect(screen.getByLabelText('Your Action')).toHaveValue('I strike the goblin sentry.')
      const notices = document.querySelector('.story-composer-slot .story-notices') as HTMLElement
      expect(within(notices).getByRole('status')).toHaveTextContent('Goblin Sentry is out of reach.')
      expect(screen.getByRole('button', { name: 'Send' })).toBeEnabled()
    },
  )
})
