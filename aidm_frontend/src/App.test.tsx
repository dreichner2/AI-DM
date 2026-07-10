/// <reference types="node" />
// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { originScopedStorageKey } from './api'
import { actorCapabilitiesAllowOperatorTools } from './capabilities'
import type { ClarificationRequest, PlayerDetail } from './types'
import {
  appTestState,
  App,
  contrastRatio,
  fixedNow,
  installLegacyMatchMediaMock,
  installMatchMediaMock,
  jsonResponse,
  lightThemeColors,
  lightThemeContrastBackgrounds,
  lightThemeContrastForegrounds,
  renderLoadedApp,
  setupAppTest,
  socketHandler,
  socketMock,
  teardownAppTest,
  toggleAdminToolsViaComposerLabel,
} from './App.testHarness'

describe('actorCapabilitiesAllowOperatorTools', () => {
  it('allows operator surfaces only for backend-declared operator capabilities', () => {
    expect(actorCapabilitiesAllowOperatorTools(['player_read', 'player_action'])).toBe(false)
    expect(actorCapabilitiesAllowOperatorTools(['player_read', 'dm_authoring'])).toBe(true)
    expect(actorCapabilitiesAllowOperatorTools(['debug_read'])).toBe(true)
    expect(actorCapabilitiesAllowOperatorTools([])).toBe(false)
    expect(actorCapabilitiesAllowOperatorTools(null)).toBe(false)
  })
})

describe('App user workflow regressions', () => {
  beforeEach(setupAppTest)
  afterEach(teardownAppTest)

  it('switches composer modes and rewrites the action text without stale prefixes', async () => {
    await renderLoadedApp()

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'test the sigil' } })

    fireEvent.click(screen.getByRole('button', { name: 'OOC' }))
    expect(actionInput).toHaveValue('[OOC] test the sigil')

    fireEvent.click(screen.getByRole('button', { name: 'Roll' }))
    const rollOptions = screen.getByLabelText('Roll options')
    expect(rollOptions).toBeInTheDocument()
    expect(screen.queryByLabelText(/Your Action/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Spell' })).not.toBeInTheDocument()
    expect(within(rollOptions).getByRole('button', { name: 'Plain' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(within(rollOptions).getByRole('button', { name: 'STR +3' }))
    expect(screen.getByLabelText('Roll modifier')).toHaveValue(3)
    expect(screen.getByLabelText('Roll reason')).toHaveValue('STR check')

    fireEvent.click(within(rollOptions).getByRole('button', { name: '+PB +2' }))
    expect(screen.getByLabelText('Roll modifier')).toHaveValue(5)

    fireEvent.click(screen.getByRole('button', { name: 'Roll' }))
    const restoredActionInput = screen.getByLabelText(/Your Action/i)
    expect(restoredActionInput).toHaveValue('test the sigil')

    fireEvent.click(screen.getByRole('button', { name: 'Item' }))
    expect(restoredActionInput).toHaveValue('Ember uses Healing Potion: test the sigil')
    expect(screen.getByLabelText('Item options')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Emote' }))
    expect(restoredActionInput).toHaveValue('/emote test the sigil')

    fireEvent.click(screen.getByRole('button', { name: 'Action mode' }))
    expect(restoredActionInput).toHaveValue('test the sigil')
  })

  it('reports a saved DM turn for beta review with the session and turn id', async () => {
    await renderLoadedApp()

    const reportButtons = await screen.findAllByRole('button', { name: 'Report bad turn' })
    fireEvent.click(reportButtons[reportButtons.length - 1])

    await waitFor(() =>
      expect(appTestState.fetchCalls).toContainEqual(
        expect.objectContaining({
          method: 'POST',
          path: '/api/feedback/bad-turn',
          body: {
            session_id: 20,
            turn_id: 2,
            category: 'other',
          },
        }),
      ),
    )
    expect(screen.getByRole('button', { name: 'Bad turn reported' })).toBeDisabled()
  })

  it('submits beta turn quality feedback from the latest DM response', async () => {
    await renderLoadedApp()

    const prompt = await screen.findByRole('form', { name: 'Beta turn feedback' })
    fireEvent.click(within(prompt).getByRole('button', { name: 'Coherence 3' }))
    fireEvent.click(within(prompt).getByRole('button', { name: 'Fun 5' }))
    fireEvent.click(within(prompt).getByRole('button', { name: 'Rules 2' }))
    fireEvent.click(within(prompt).getByRole('button', { name: 'Record' }))

    await waitFor(() =>
      expect(appTestState.fetchCalls).toContainEqual(
        expect.objectContaining({
          method: 'POST',
          path: '/api/feedback/coherence',
          body: {
            session_id: 20,
            turn_id: 2,
            coherence_score: 3,
            category: 'beta_turn_prompt',
            fun_score: 5,
            rules_score: 2,
          },
        }),
      ),
    )
    expect(await screen.findByText('Feedback sent.')).toBeInTheDocument()
  })

  it('sends structured item composer metadata for buying arbitrary items', async () => {
    await renderLoadedApp()

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'before leaving town' } })
    fireEvent.click(screen.getByRole('button', { name: 'Item' }))
    fireEvent.change(screen.getByLabelText('Inventory action'), { target: { value: 'buy' } })
    fireEvent.change(screen.getByLabelText('Item name'), { target: { value: 'rope' } })
    fireEvent.change(screen.getByLabelText('Gold cost'), { target: { value: '5' } })

    expect(actionInput).toHaveValue('Ember tries to buy rope for 5 gold: before leaving town')
    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: 'Ember tries to buy rope for 5 gold: before leaving town',
          action_intent: expect.objectContaining({
            kind: 'item',
            inventory_action: 'buy',
            cost_gold: 5,
            item: {
              name: 'rope',
              quantity: 1,
            },
          }),
        }),
      ),
    )
  })

  it('starts an empty adventure with a generated DM opening prompt and roster', async () => {
    appTestState.sessionLogs[20] = []
    appTestState.sessionStates[20] = {
      ...appTestState.sessionStates[20],
      rolling_summary: '',
    }
    appTestState.playersByCampaign[10] = [
      ...appTestState.playersByCampaign[10],
      {
        player_id: 31,
        workspace_id: 'owner',
        account_id: null,
        username: null,
        campaign_id: 10,
        name: 'Mira Player',
        character_name: 'Mira',
        race: 'Elf',
        sex: 'female',
        profile_image: '/profile-icons/elf_female.png',
        class_: 'Ranger',
        char_class: 'Ranger',
        level: 1,
        created_at: '2026-06-06T10:38:00.000Z',
        updated_at: '2026-06-06T10:39:00.000Z',
      },
    ]
    await renderLoadedApp()

    const startButton = await screen.findByRole('button', { name: 'Start Adventure' })
    socketMock.socket.emit.mockClear()
    fireEvent.click(startButton)

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: expect.stringContaining('Please narrate the opening scene for this campaign.'),
        }),
      ),
    )
    const sendPayload = socketMock.socket.emit.mock.calls.find(([event]) => event === 'send_message')?.[1] as {
      message?: string
    }
    expect(sendPayload.message).toContain('Campaign: Smoke Campaign.')
    expect(sendPayload.message).toContain('The table currently has 2 players named: Ember, Mira.')
    expect(sendPayload.message).toContain('Current location: Ash Hall.')
    expect(sendPayload.message).toContain('what immediate choice or prompt is in front of them')
  })

  it('allows the next send while the previous saved turn is only canon pending', async () => {
    await renderLoadedApp()

    await act(async () => {
      socketHandler<{ turn_id: number; turn_number?: number }>('dm_response_start')({
        turn_id: 77,
        turn_number: 4,
      })
      socketHandler<{ turn_id: number; chunk: string }>('dm_chunk')({
        turn_id: 77,
        chunk: 'The arena dust settles as the last beam of energy fades.',
      })
      socketHandler<{ session_id: number; turn_id: number; status: string; details: Record<string, unknown> }>(
        'turn_status',
      )({
        session_id: 20,
        turn_id: 77,
        status: 'saved',
        details: { stage: 'dm_response' },
      })
      socketHandler<{ session_id: number; turn_id: number; status: string; details: Record<string, unknown> }>(
        'turn_status',
      )({
        session_id: 20,
        turn_id: 77,
        status: 'canon_pending',
        details: { job_id: 9 },
      })
    })

    await waitFor(() => expect(screen.getAllByText('canon pending').length).toBeGreaterThan(0))

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'I launch forward before the smoke clears.' } })
    socketMock.socket.emit.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: 'I launch forward before the smoke clears.',
        }),
      ),
    )
  })

  it('lets users dismiss a stuck pending local message from history', async () => {
    await renderLoadedApp()

    expect(screen.queryByRole('button', { name: 'Delete pending message' })).not.toBeInTheDocument()

    const pendingMessage = 'This pending message should be removable from history.'
    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: pendingMessage } })
    socketMock.socket.emit.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: pendingMessage,
        }),
      ),
    )
    expect(screen.getByText(pendingMessage)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Delete pending message' }))

    expect(screen.queryByText(pendingMessage)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete pending message' })).not.toBeInTheDocument()
  })

  it('keeps a pending player message below the previous DM while log refreshes settle', async () => {
    const rendered = await renderLoadedApp()
    const pendingMessage = 'I sprint through the smoke before the echo fades.'

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: pendingMessage } })
    socketMock.socket.emit.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: pendingMessage,
        }),
      ),
    )

    const rowTexts = [...rendered.container.querySelectorAll('.turn-feed .turn-row')].map(
      (row) => row.textContent ?? '',
    )
    const latestDmIndex = rowTexts.findIndex((text) => text.includes('The chamber beyond is much larger'))
    const pendingIndex = rowTexts.findIndex((text) => text.includes(pendingMessage))
    expect(latestDmIndex).toBeGreaterThanOrEqual(0)
    expect(pendingIndex).toBeGreaterThan(latestDmIndex)
    expect(rowTexts.at(-1)).toContain(pendingMessage)

    const logFetchCount = appTestState.fetchCalls.filter((call) => call.method === 'GET' && call.path === '/api/sessions/20/log').length
    await act(async () => {
      socketHandler<{ session_id?: number }>('session_log_update')({ session_id: 20 })
    })
    await waitFor(() =>
      expect(appTestState.fetchCalls.filter((call) => call.method === 'GET' && call.path === '/api/sessions/20/log').length)
        .toBeGreaterThan(logFetchCount),
    )
    expect(screen.getByText(pendingMessage)).toBeInTheDocument()

    const sendPayload = socketMock.socket.emit.mock.calls.find(([event]) => event === 'send_message')?.[1] as {
      client_message_id?: string
    }
    await act(async () => {
      socketHandler<{
        message: string
        speaker: string
        turn_id: number
        turn_number: number
        requires_roll: boolean
        rules_hint: Record<string, unknown>
        context_version: string
        client_message_id: string
        action_intent: Record<string, unknown>
      }>('new_message')({
        message: pendingMessage,
        speaker: 'Ember',
        turn_id: 78,
        turn_number: 5,
        requires_roll: false,
        rules_hint: { requires_roll: false },
        context_version: 'v2',
        client_message_id: sendPayload.client_message_id ?? '',
        action_intent: {
          kind: 'message',
          source: 'composer',
          text: pendingMessage,
          client_message_id: sendPayload.client_message_id ?? '',
        },
      })
    })

    expect(screen.getAllByText(pendingMessage)).toHaveLength(1)
  })

  it('keeps admin mode hidden until the composer label gesture unlocks it', async () => {
    await renderLoadedApp()

    expect(screen.queryByRole('button', { name: 'Admin mode' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Admin' })).not.toBeInTheDocument()

    toggleAdminToolsViaComposerLabel()

    expect(screen.getByRole('button', { name: 'Admin mode' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Admin' })).toBeInTheDocument()

    toggleAdminToolsViaComposerLabel()

    expect(screen.queryByRole('button', { name: 'Admin mode' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Admin' })).not.toBeInTheDocument()
  })

  it('sends player interaction mode with target metadata', async () => {
    appTestState.playersByCampaign[10] = [
      ...appTestState.playersByCampaign[10],
      {
        player_id: 31,
        workspace_id: 'owner',
        account_id: null,
        username: null,
        campaign_id: 10,
        name: 'Maya',
        character_name: 'Borin',
        race: 'Dwarf',
        sex: 'male',
        profile_image: '/profile-icons/dwarf_male.png',
        class_: 'Fighter',
        char_class: 'Fighter',
        level: 2,
        created_at: '2026-06-06T10:38:00.000Z',
        updated_at: '2026-06-06T10:39:00.000Z',
      },
    ]
    await renderLoadedApp()
    await act(async () => {
      socketHandler<
        Array<{
          id: number
          character_name: string
          name: string
          race?: string
          sex?: string
          profile_image?: string
          class_?: string
          char_class?: string
        }>
      >('active_players')([
        {
          id: 30,
          character_name: 'Ember',
          name: 'Danny',
          race: 'Human',
          sex: 'female',
          profile_image: '/profile-icons/human_female.png',
          class_: 'Wizard',
          char_class: 'Wizard',
        },
        {
          id: 31,
          character_name: 'Borin',
          name: 'Maya',
          race: 'Dwarf',
          sex: 'male',
          profile_image: '/profile-icons/dwarf_male.png',
          class_: 'Fighter',
          char_class: 'Fighter',
        },
      ])
    })

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'the silver key' } })
    fireEvent.click(screen.getByRole('button', { name: 'Interact' }))

    expect(screen.getByLabelText('Interaction options')).toBeInTheDocument()
    expect(screen.getByLabelText('Interaction target')).toHaveValue('player:31')
    expect(actionInput).toHaveValue('Ember says to Borin: the silver key')

    fireEvent.change(screen.getByLabelText('Interaction type'), { target: { value: 'take_from' } })
    expect(actionInput).toHaveValue('Ember tries to take something from Borin: the silver key')
    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: 'Ember tries to take something from Borin: the silver key',
          action_intent: expect.objectContaining({
            kind: 'interact',
            interaction: expect.objectContaining({
              type: 'take_from',
              label: 'Take from',
            }),
            target: expect.objectContaining({
              player_id: 31,
              character_name: 'Borin',
              player_name: 'Maya',
            }),
          }),
        }),
      ),
    )
  })

  it('sends admin mode with an admin passcode and typed admin intent', async () => {
    await renderLoadedApp()

    toggleAdminToolsViaComposerLabel()
    fireEvent.click(screen.getByRole('button', { name: 'Admin mode' }))
    expect(screen.getByLabelText('Admin passcode')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Admin passcode'), { target: { value: 'letmein' } })
    fireEvent.change(screen.getByLabelText(/Your Action/i), {
      target: { value: '[ADMIN] make the locked gate open now' },
    })
    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          admin_passcode: 'letmein',
          message: '[ADMIN] make the locked gate open now',
          action_intent: expect.objectContaining({
            kind: 'admin',
            text: '[ADMIN] make the locked gate open now',
          }),
        }),
      ),
    )
  })

  it('opens the dice roller from Roll options and sends the completed roll', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Roll' }))
    expect(screen.getByLabelText('Roll options')).toBeInTheDocument()
    expect(screen.queryByRole('dialog', { name: 'Dice Roller' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Roll dice' }))

    const dialog = await screen.findByRole('dialog', { name: 'Dice Roller' })
    expect(within(dialog).getByText('D20')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Your Action/i)).not.toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Complete roll' }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          session_id: 20,
          campaign_id: 10,
          player_id: 30,
          action_intent: expect.objectContaining({
            kind: 'roll',
            source: 'dice_roller',
            roll: expect.objectContaining({
              die: 'd20',
              result_visibility: 'hidden_until_landed',
            }),
          }),
        }),
      ),
    )
  })

  it('shows a party-visible roll wait indicator with the remaining character and check', async () => {
    appTestState.playersByCampaign[10] = [
      ...appTestState.playersByCampaign[10],
      {
        player_id: 31,
        workspace_id: 'owner',
        account_id: null,
        username: null,
        campaign_id: 10,
        name: 'Maya',
        character_name: 'Borin',
        race: 'Dwarf',
        sex: 'male',
        profile_image: '/profile-icons/dwarf_male.png',
        class_: 'Fighter',
        char_class: 'Fighter',
        level: 2,
        created_at: '2026-06-06T10:38:00.000Z',
        updated_at: '2026-06-06T10:39:00.000Z',
      },
    ]
    appTestState.sessionLogs[20] = [
      {
        id: 1,
        entry_type: 'player',
        message: 'Ember: I shove the warehouse door open.',
        metadata: { turn_id: 7, turn_number: 3, persistence_status: 'saved' },
        timestamp: '2026-06-06T10:40:00.000Z',
      },
      {
        id: 2,
        entry_type: 'dm',
        message: 'DM: The bandits draw steel. Everyone roll initiative.',
        metadata: {
          turn_id: 7,
          turn_number: 3,
          requires_roll: true,
          outcome_status: 'deferred',
          rule_type: 'initiative',
          remaining_player_ids: [30, 31],
          persistence_status: 'saved',
        },
        timestamp: '2026-06-06T10:41:00.000Z',
      },
      {
        id: 3,
        entry_type: 'system',
        message: '**Check Resolved**: turn 7 resolved with roll 12.',
        metadata: {
          turn_id: 8,
          turn_number: 4,
          resolved_turn_id: 7,
          roll_value: 12,
          remaining_player_ids: [31],
          persistence_status: 'saved',
        },
        timestamp: '2026-06-06T10:42:00.000Z',
      },
    ]

    await renderLoadedApp()

    const banner = await screen.findByRole('status', { name: 'Pending roll' })
    expect(within(banner).getByText('Waiting on Borin to roll')).toBeInTheDocument()
    expect(within(banner).getByText('Turn 3: initiative')).toBeInTheDocument()
    expect(within(banner).getByText('The bandits draw steel. Everyone roll initiative.')).toBeInTheDocument()
    expect(within(banner).getByText('Roll needed')).toBeInTheDocument()
  })

  it('rolls selected ability checks from the Roll selector', async () => {
    await renderLoadedApp()

    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'kick the door' } })
    fireEvent.click(screen.getByRole('button', { name: 'Roll' }))
    const rollOptions = screen.getByLabelText('Roll options')
    fireEvent.click(within(rollOptions).getByRole('button', { name: 'STR +3' }))
    fireEvent.click(within(rollOptions).getByRole('button', { name: '+PB +2' }))

    expect(screen.queryByLabelText(/Your Action/i)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Roll modifier')).toHaveValue(5)

    fireEvent.click(screen.getByRole('button', { name: 'Roll dice' }))
    const dialog = await screen.findByRole('dialog', { name: 'Dice Roller' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Complete roll' }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: expect.stringMatching(/^kick the door\nI roll a d20\+5 for STR check: \d+/),
          action_intent: expect.objectContaining({
            kind: 'roll',
            ability: {
              key: 'strength',
              label: 'STR',
              modifier: 3,
            },
            roll: expect.objectContaining({
              modifier: 5,
              reason: 'STR check',
            }),
          }),
        }),
      ),
    )
  })

  it('rolls initiative from the Roll selector using the dexterity modifier', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Roll' }))
    const rollOptions = screen.getByLabelText('Roll options')
    fireEvent.click(within(rollOptions).getByRole('button', { name: 'Initiative DEX +1' }))

    expect(screen.queryByLabelText(/Your Action/i)).not.toBeInTheDocument()
    expect(screen.getByLabelText('Roll modifier')).toHaveValue(1)
    expect(screen.getByLabelText('Roll reason')).toHaveValue('initiative')

    fireEvent.click(screen.getByRole('button', { name: 'Roll dice' }))
    const dialog = await screen.findByRole('dialog', { name: 'Dice Roller' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Complete roll' }))

    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'send_message',
        expect.objectContaining({
          message: expect.stringMatching(/^I roll for initiative: \d+/),
          action_intent: expect.objectContaining({
            kind: 'roll',
            ability: {
              key: 'dexterity',
              label: 'Initiative',
              modifier: 1,
            },
            roll: expect.objectContaining({
              modifier: 1,
              reason: 'initiative',
            }),
          }),
        }),
      ),
    )
  })

  it('shows active players from the session socket roster and clears them on disconnect', async () => {
    await renderLoadedApp()

    await act(async () => {
      socketHandler<
        Array<{
          id: number
          character_name: string
          name: string
          race?: string
          sex?: string
          profile_image?: string
          class_?: string
          char_class?: string
          is_typing?: boolean
        }>
      >('active_players')([
        {
          id: 30,
          character_name: 'Ember',
          name: 'Danny',
          race: 'Human',
          sex: 'female',
          profile_image: '/profile-icons/human_female.png',
          class_: 'Wizard',
          char_class: 'Wizard',
          is_typing: true,
        },
        {
          id: 31,
          character_name: 'Borin',
          name: 'Maya',
          race: 'Dwarf',
          sex: 'male',
          profile_image: '/profile-icons/dwarf_male.png',
          class_: 'Fighter',
          char_class: 'Fighter',
          is_typing: true,
        },
      ])
    })

    const roster = screen.getByLabelText('Active players in this session')
    expect(screen.getByText('Active Players (2)')).toBeInTheDocument()
    expect(within(roster).getByText('Borin')).toBeInTheDocument()
    expect(within(roster).getByText('Maya - Dwarf Fighter')).toBeInTheDocument()
    expect(within(roster).getByAltText('Borin character icon')).toHaveAttribute('src', '/profile-icons/dwarf_male.png')
    expect(within(roster).getByLabelText('Borin is typing')).toHaveTextContent('Typing...')
    expect(within(roster).queryByLabelText('Ember is typing')).not.toBeInTheDocument()
    expect(within(roster).getByText('You')).toBeInTheDocument()

    await act(async () => {
      socketHandler<void>('disconnect')()
    })

    expect(screen.getByText('Active Players (0)')).toBeInTheDocument()
    expect(screen.getByText('No active players connected.')).toBeInTheDocument()
  })

  it('shows health states on active player cards from the session snapshot', async () => {
    appTestState.sessionStates[20] = {
      ...appTestState.sessionStates[20],
      state_snapshot: {
        playerCharacters: [
          { playerId: 30, name: 'Ember', health: { currentHp: 16, maxHp: 16 } },
          { playerId: 31, name: 'Borin', health: { currentHp: 9, maxHp: 18 } },
          { playerId: 32, name: 'Kara', health: { currentHp: 3, maxHp: 18 } },
          { playerId: 33, name: 'Moss', health: { currentHp: 0, maxHp: 12 } },
        ],
      },
    }
    await renderLoadedApp()

    await act(async () => {
      socketHandler<
        Array<{
          id: number
          character_name: string
          name: string
          race?: string
          sex?: string
          profile_image?: string
          class_?: string
          char_class?: string
          is_typing?: boolean
        }>
      >('active_players')([
        {
          id: 30,
          character_name: 'Ember',
          name: 'Danny',
          race: 'Human',
          sex: 'female',
          profile_image: '/profile-icons/human_female.png',
          class_: 'Wizard',
          char_class: 'Wizard',
        },
        {
          id: 31,
          character_name: 'Borin',
          name: 'Maya',
          race: 'Dwarf',
          sex: 'male',
          profile_image: '/profile-icons/dwarf_male.png',
          class_: 'Fighter',
          char_class: 'Fighter',
        },
        {
          id: 32,
          character_name: 'Kara',
          name: 'Tess',
          race: 'Elf',
          sex: 'female',
          profile_image: '/profile-icons/elf_female.png',
          class_: 'Rogue',
          char_class: 'Rogue',
        },
        {
          id: 33,
          character_name: 'Moss',
          name: 'Ike',
          race: 'Gnome',
          sex: 'male',
          profile_image: '/profile-icons/gnome_male.png',
          class_: 'Cleric',
          char_class: 'Cleric',
        },
      ])
    })

    const roster = screen.getByLabelText('Active players in this session')
    const emberHealth = await within(roster).findByLabelText('Ember health: Uninjured')
    expect(emberHealth).toHaveTextContent('Uninjured')
    expect(emberHealth.closest('li')).toHaveClass('active-player-health-uninjured')
    const borinHealth = within(roster).getByLabelText('Borin health: Wounded')
    expect(borinHealth).toHaveTextContent('Wounded')
    expect(borinHealth.closest('li')).toHaveClass('active-player-health-wounded')
    const karaHealth = within(roster).getByLabelText('Kara health: Badly wounded')
    expect(karaHealth).toHaveTextContent('Badly wounded')
    expect(karaHealth.closest('li')).toHaveClass('active-player-health-badly-wounded')
    const mossHealth = within(roster).getByLabelText('Moss health: Dead')
    expect(mossHealth).toHaveTextContent('Dead')
    expect(mossHealth.closest('li')).toHaveClass('active-player-health-dead')
  })

  it('shows a compact active-player presence strip on mobile', async () => {
    installMatchMediaMock(true)
    await renderLoadedApp()

    await act(async () => {
      socketHandler<
        Array<{
          id: number
          character_name: string
          name: string
          race?: string
          sex?: string
          profile_image?: string
          class_?: string
          char_class?: string
          is_typing?: boolean
        }>
      >('active_players')([
        {
          id: 30,
          character_name: 'Ember',
          name: 'Danny',
          race: 'Human',
          sex: 'female',
          profile_image: '/profile-icons/human_female.png',
          class_: 'Wizard',
          char_class: 'Wizard',
          is_typing: true,
        },
        {
          id: 31,
          character_name: 'Borin',
          name: 'Maya',
          race: 'Dwarf',
          sex: 'male',
          profile_image: '/profile-icons/dwarf_male.png',
          class_: 'Fighter',
          char_class: 'Fighter',
          is_typing: true,
        },
      ])
    })

    const mobilePresence = screen.getByLabelText('Mobile active players')
    expect(within(mobilePresence).getByText('2 online')).toBeInTheDocument()
    expect(within(mobilePresence).getByText('Borin typing')).toBeInTheDocument()
    expect(within(mobilePresence).getByText('Ember')).toBeInTheDocument()
    expect(within(mobilePresence).getByText('You')).toBeInTheDocument()
    expect(within(mobilePresence).getByLabelText('Borin is typing')).toHaveTextContent('Typing')
    expect(within(mobilePresence).queryByLabelText('Ember is typing')).not.toBeInTheDocument()
  })

  it('mounts mobile layout with legacy MediaQueryList listeners', async () => {
    const legacyListeners = installLegacyMatchMediaMock(true)
    const rendered = await renderLoadedApp()

    expect(screen.getByRole('button', { name: 'Open table settings' })).toBeInTheDocument()
    expect(legacyListeners.addListener).toHaveBeenCalledWith(expect.any(Function))

    rendered.unmount()

    expect(legacyListeners.removeListener).toHaveBeenCalledWith(legacyListeners.addListener.mock.calls[0][0])
  })


  it('equips an inventory item from the sidebar', async () => {
    appTestState.playerDetails[30] = {
      ...appTestState.playerDetails[30],
      inventory: [
        { id: 'greataxe', name: 'Greataxe', quantity: 1, weight: 7 },
        { id: 'handaxe', name: 'Handaxe', quantity: 1, weight: 2, type: 'misc' },
      ],
    }
    await renderLoadedApp()

    expect(await screen.findByRole('button', { name: 'Equip Greataxe' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Equip Handaxe' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Equip Greataxe' }))

    await waitFor(() =>
      expect(appTestState.fetchCalls.some((call) => call.method === 'PATCH' && call.path === '/api/players/30/inventory/equipment')).toBe(true),
    )
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'PATCH',
          path: '/api/players/30/inventory/equipment',
          body: expect.objectContaining({ session_id: 20 }),
        }),
      ]),
    )
    expect(await screen.findByText(/Equipped - two hands/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Unequip Greataxe' })).toBeInTheDocument()
  })

  it('shows the selected character spellbook in the inspector', async () => {
    appTestState.playerDetails[30] = {
      ...appTestState.playerDetails[30],
      character_sheet: {
        ...(appTestState.playerDetails[30].character_sheet as Record<string, unknown>),
        spellbook: {
          knownSpells: [
            {
              id: 'spell-cobalt-charm',
              name: 'Cobalt Charm',
              level: 1,
              sourceType: 'class_catalog',
              sourceDetail: 'sorcerer',
              description: 'Tint a social moment with charged blue sparks.',
              catalog: 'aidm-original',
            },
            {
              id: 'spell-river-ward',
              name: 'River Ward',
              level: 1,
              sourceType: 'race_catalog',
              sourceDetail: 'riverborn',
              description: 'Raise a quick protective sign from moving water.',
              catalog: 'aidm-original',
            },
          ],
        },
      },
    }

    await renderLoadedApp()

    expect(screen.getByText('Spellbook (2)')).toBeInTheDocument()
    expect(screen.getByText('Cobalt Charm')).toBeInTheDocument()
    expect(screen.getByText(/Tint a social moment/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Magic' }))

    expect(screen.getByText('River Ward')).toBeInTheDocument()
    expect(screen.getByText(/Raise a quick protective sign/)).toBeInTheDocument()
  })

  it('shows custom race active abilities and passive traits in the magic inspector', async () => {
    appTestState.playerDetails[30] = {
      ...appTestState.playerDetails[30],
      race: 'Himeros',
      race_selection: {
        raceId: 'himeros',
        raceName: 'Himeros',
        source: 'custom',
        customRaceDefinition: {
          traits: [
            {
              id: 'himeros_aura_of_desire',
              name: 'Aura of Desire',
              category: 'active_ability',
              description: 'Creatures of your choice within 30 feet must make a Wisdom saving throw.',
              mechanics: {
                activeAbility: {
                  actionType: 'action',
                  cooldown: 'longRest',
                  effectType: 'charm',
                },
              },
            },
            {
              id: 'himeros_divine_beauty',
              name: 'Divine Beauty',
              category: 'skill',
              description: 'You have proficiency in the Persuasion skill. If already proficient, you gain expertise.',
              mechanics: {
                skillProficiency: { skill: 'Persuasion', expertiseIfProficient: true },
              },
            },
          ],
        } as Record<string, unknown>,
      } as PlayerDetail['race_selection'],
    }

    await renderLoadedApp()

    expect(await screen.findByText('Abilities & Traits (2)')).toBeInTheDocument()
    expect(screen.getByText('Aura of Desire')).toBeInTheDocument()
    expect(screen.getByText(/Race \/ Himeros \/ Action \/ Long Rest/)).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: 'Magic' }))

    expect(screen.getByText('Divine Beauty')).toBeInTheDocument()
    expect(screen.getByText(/Persuasion skill/)).toBeInTheDocument()
  })

  it('keeps turn mode overrides behind the hidden admin tools', async () => {
    await renderLoadedApp()
    socketMock.socket.emit.mockClear()

    expect(screen.queryByRole('button', { name: 'Auto' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Structured' })).not.toBeInTheDocument()

    const actionLabel = screen.getByText(/Your Action/i)
    for (let index = 0; index < 5; index += 1) {
      fireEvent.click(actionLabel)
    }

    fireEvent.click(screen.getByRole('button', { name: 'Structured' }))

    expect(socketMock.socket.emit).toHaveBeenCalledWith(
      'set_turn_control',
      expect.objectContaining({
        session_id: 20,
        player_id: 30,
        mode: 'structured',
        source: 'manual',
        active_player_id: 30,
      }),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Auto' }))

    expect(socketMock.socket.emit).toHaveBeenCalledWith(
      'set_turn_control',
      expect.objectContaining({
        session_id: 20,
        player_id: 30,
        mode: 'free',
        source: 'auto',
        active_player_id: null,
      }),
    )
  })

  it('lets an outside player send into spotlight so the conductor can judge joining', async () => {
    appTestState.sessionStates[20] = {
      ...appTestState.sessionStates[20],
      state_snapshot: {
        turnControl: {
          mode: 'spotlight',
          activePlayerId: 31,
          activePlayerName: 'Borin',
        },
      },
    }
    await renderLoadedApp()

    expect(await screen.findByText('Auto: Spotlight - Borin')).toBeInTheDocument()
    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'I step beside Borin and add my support.' } })
    socketMock.socket.emit.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    expect(socketMock.socket.emit).toHaveBeenCalledWith('send_message', expect.objectContaining({
      message: 'I step beside Borin and add my support.',
    }))
    expect(screen.queryByText('Queued draft')).not.toBeInTheDocument()
  })

  it('keeps structured out-of-turn actions as queued drafts instead of sending them', async () => {
    appTestState.sessionStates[20] = {
      ...appTestState.sessionStates[20],
      state_snapshot: {
        turnControl: {
          mode: 'structured',
          activePlayerId: 31,
          activePlayerName: 'Borin',
        },
      },
    }
    await renderLoadedApp()

    expect(await screen.findByText('Auto: Structured - Borin')).toBeInTheDocument()
    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'I kick open the side door.' } })
    socketMock.socket.emit.mockClear()

    fireEvent.click(screen.getByRole('button', { name: /Send/i }))

    expect(socketMock.socket.emit).not.toHaveBeenCalledWith('send_message', expect.anything())
    expect(actionInput).toHaveValue('I kick open the side door.')
    expect(screen.getByText('Queued draft')).toBeInTheDocument()
    expect(screen.getAllByText('I kick open the side door.').length).toBeGreaterThan(0)
  })

  it('renders Scene State from the live session state snapshot without a workspace reload', async () => {
    appTestState.sessionsByCampaign[10] = [
      {
        ...appTestState.sessionsByCampaign[10][0],
        state_snapshot: {},
      },
    ]
    appTestState.sessionStates[20] = {
      ...appTestState.sessionStates[20],
      state_snapshot: {
        currentScene: {
          name: 'Blackwake Tavern',
          locationId: 'blackwake_tavern',
          sceneType: 'social',
          mood: 'tense',
          dangerLevel: 2,
          activeQuestIds: [
            'find_missing_sailor',
            'question_captain_velra',
            'search_north_docks',
            'trace_lantern_bridge',
            'chart_ash_gate',
          ],
        },
        quests: [
          {
            id: 'find_missing_sailor',
            title: 'Find the Missing Sailor',
            status: 'active',
            stage: 'Investigate the docks',
          },
          {
            id: 'question_captain_velra',
            title: 'Question Captain Velra',
            status: 'active',
            stage: 'Ask about the missing crew',
          },
          {
            id: 'search_north_docks',
            title: 'Search North Docks',
            status: 'active',
            stage: 'Check the moorings',
          },
          {
            id: 'trace_lantern_bridge',
            title: 'Trace Lantern Bridge',
            status: 'active',
            stage: 'Follow the lantern ash',
          },
          {
            id: 'chart_ash_gate',
            title: 'Chart the Ash Gate',
            status: 'active',
            stage: 'Map the sealed entrance',
          },
        ],
        locations: [
          {
            id: 'blackwake_tavern',
            name: 'Blackwake Tavern',
            status: 'visited',
            type: 'tavern',
            lastVisitedTurn: 12,
          },
          {
            id: 'north_docks',
            name: 'North Docks',
            status: 'visited',
            type: 'road',
            lastVisitedTurn: 11,
          },
          {
            id: 'ash_gate',
            name: 'Ash Gate',
            status: 'visited',
            type: 'ruins',
            lastVisitedTurn: 10,
          },
          {
            id: 'lantern_bridge',
            name: 'Lantern Bridge',
            status: 'visited',
            type: 'road',
            lastVisitedTurn: 9,
          },
          {
            id: 'saltmarket',
            name: 'Saltmarket',
            status: 'visited',
            type: 'town',
            lastVisitedTurn: 8,
          },
          {
            id: 'old_lighthouse',
            name: 'Old Lighthouse',
            status: 'visited',
            type: 'ruins',
            lastVisitedTurn: 7,
          },
        ],
        knownNpcs: [
          {
            id: 'captain_velra',
            name: 'Captain Velra',
            race: 'Human',
            role: 'dock captain',
            disposition: 'friendly',
            status: 'met',
            lastSeenTurn: 12,
          },
          {
            id: 'marta_fenwick',
            name: 'Marta Fenwick',
            race: 'Halfling',
            role: 'shopkeeper',
            disposition: 'friendly',
            status: 'met',
            lastSeenTurn: 11,
          },
          {
            id: 'new_sentry',
            name: 'New Sentry',
            race: 'Elf',
            role: 'guard',
            disposition: 'neutral',
            status: 'known',
            lastSeenTurn: 10,
          },
          {
            id: 'dock_mage',
            name: 'Dock Mage',
            race: 'Tiefling',
            role: 'mage',
            disposition: 'suspicious',
            status: 'known',
            lastSeenTurn: 9,
          },
          {
            id: 'harbor_clerk',
            name: 'Harbor Clerk',
            race: 'Dwarf',
            role: 'clerk',
            disposition: 'neutral',
            status: 'known',
            lastSeenTurn: 8,
          },
          {
            id: 'old_hermit',
            name: 'Old Hermit',
            race: 'Gnome',
            role: 'witness',
            disposition: 'unknown',
            status: 'known',
            lastSeenTurn: 7,
          },
        ],
      },
    }

    await renderLoadedApp()

    expect(screen.getByText('Scene State')).toBeInTheDocument()
    await waitFor(() => {
      expect(screen.getAllByText('Blackwake Tavern').length).toBeGreaterThan(0)
    })
    expect(screen.getByText('Find the Missing Sailor')).toBeInTheDocument()
    expect(screen.getByText('Chart the Ash Gate')).toBeInTheDocument()
    expect(screen.getByText('Captain Velra (Human)')).toBeInTheDocument()
    expect(screen.queryByText('Old Hermit (Gnome)')).not.toBeInTheDocument()
    expect(screen.queryByText('Old Lighthouse')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Show 1 older NPC/i }))
    expect(screen.getByText('Old Hermit (Gnome)')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Show 1 older place/i }))
    expect(screen.getByText('Old Lighthouse')).toBeInTheDocument()
  })

  it('emits typing presence while the composer text changes', async () => {
    await renderLoadedApp()

    socketMock.socket.emit.mockClear()
    const actionInput = screen.getByLabelText(/Your Action/i)
    fireEvent.change(actionInput, { target: { value: 'check the rune' } })

    expect(socketMock.socket.emit).toHaveBeenCalledWith('typing_status', {
      session_id: 20,
      player_id: 30,
      is_typing: true,
    })

    fireEvent.change(actionInput, { target: { value: '' } })
    expect(socketMock.socket.emit).toHaveBeenCalledWith('typing_status', {
      session_id: 20,
      player_id: 30,
      is_typing: false,
    })
  })

  it('keeps default chat text and persists reader font controls', async () => {
    await renderLoadedApp()

    const feed = document.querySelector<HTMLElement>('.turn-feed')
    expect(feed).toHaveClass('chat-text-size-default')
    expect(feed).toHaveClass('chat-text-font-default')

    fireEvent.click(screen.getByRole('button', { name: 'Chat text options' }))
    fireEvent.change(screen.getByLabelText('Chat text size'), { target: { value: 'large' } })
    fireEvent.change(screen.getByLabelText('Chat text font'), { target: { value: 'sans' } })

    expect(feed).toHaveClass('chat-text-size-large')
    expect(feed).toHaveClass('chat-text-font-sans')
    expect(localStorage.getItem('aidm:chatTextSettings')).toBe(
      JSON.stringify({ size: 'large', font: 'sans' }),
    )
  })

  it('keeps item clarification choices visible through log refresh and resolves by socket', async () => {
    await renderLoadedApp()

    const clarification: ClarificationRequest = {
      id: 'clarify_77_001',
      turnId: 77,
      sessionId: 20,
      playerId: 30,
      type: 'item_resolution',
      prompt: 'Which sword do you use?',
      originalPlayerMessage: 'I swing my sword at the goblin.',
      originalAction: {
        id: 'act_001',
        type: 'combat.attack',
        actorId: 'player_30',
        weaponName: 'sword',
        sourceText: 'I swing my sword at the goblin.',
        requiresDMResolution: true,
      },
      options: [
        { itemId: 'great', label: 'Greatsword', description: 'weapon' },
        { itemId: 'long', label: 'Longsword', description: 'weapon' },
      ],
    }

    await act(async () => {
      socketHandler<ClarificationRequest>('clarification_required')(clarification)
    })
    expect(screen.getByText('Which sword do you use?')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Greatsword/ })).toBeInTheDocument()

    await act(async () => {
      socketHandler<{ session_id?: number }>('session_log_update')({ session_id: 20 })
    })
    expect(screen.getByRole('button', { name: /Greatsword/ })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /Greatsword/ }))
    expect(socketMock.socket.emit).toHaveBeenCalledWith(
      'resolve_clarification',
      expect.objectContaining({
        session_id: 20,
        player_id: 30,
        turn_id: 77,
        selected_item_id: 'great',
      }),
    )
  })

  it('shows another player socket message in the turn feed immediately', async () => {
    await renderLoadedApp()

    await act(async () => {
      socketHandler<{
        message: string
        speaker: string
        turn_id: number
        requires_roll: boolean
        rules_hint: Record<string, unknown>
        context_version: string
        client_message_id: string
        action_intent: Record<string, unknown>
      }>('new_message')({
        message: 'Borin passes Ember the silver key.',
        speaker: 'Borin',
        turn_id: 44,
        requires_roll: false,
        rules_hint: { requires_roll: false },
        context_version: 'v2',
        client_message_id: 'borin-live-1',
        action_intent: {
          kind: 'message',
          source: 'composer',
          text: 'Borin passes Ember the silver key.',
          client_message_id: 'borin-live-1',
        },
      })
    })

    expect(screen.getByText('Borin passes Ember the silver key.')).toBeInTheDocument()
    expect(screen.getByText('Borin')).toBeInTheDocument()

    await act(async () => {
      socketHandler<{ message: string; speaker: string; turn_id: number }>('new_message')({
        message: 'Borin passes Ember the silver key.',
        speaker: 'Borin',
        turn_id: 44,
      })
    })

    expect(screen.getAllByText('Borin passes Ember the silver key.')).toHaveLength(1)
  })

  it('copies a share link with the active backend URL and session selection', async () => {
    localStorage.setItem('aidm:baseUrl', 'https://backend-tunnel.ngrok-free.app')
    const writeText = vi.fn((value: string) => Promise.resolve(value))
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    await renderLoadedApp()
    fireEvent.click(screen.getByRole('button', { name: 'Share' }))

    await waitFor(() => expect(writeText).toHaveBeenCalledOnce())
    const shareUrl = new URL(String(writeText.mock.calls[0]?.[0]))
    expect(shareUrl.searchParams.get('campaign')).toBe('10')
    expect(shareUrl.searchParams.get('session')).toBe('20')
    expect(shareUrl.searchParams.get('backend')).toBe('https://backend-tunnel.ngrok-free.app')
    expect(shareUrl.searchParams.has('player')).toBe(false)
  })

  it('copies a same-origin share link without a backend parameter by default', async () => {
    const writeText = vi.fn((value: string) => Promise.resolve(value))
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })

    await renderLoadedApp()
    fireEvent.click(screen.getByRole('button', { name: 'Share' }))

    await waitFor(() => expect(writeText).toHaveBeenCalledOnce())
    const shareUrl = new URL(String(writeText.mock.calls[0]?.[0]))
    expect(shareUrl.searchParams.get('campaign')).toBe('10')
    expect(shareUrl.searchParams.get('session')).toBe('20')
    expect(shareUrl.searchParams.has('backend')).toBe(false)
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/health',
          origin: 'http://localhost:3000',
        }),
      ]),
    )
  })


  it('does not join a session socket with a stale selected player', async () => {
    localStorage.setItem('aidm:selectedPlayerId', '999')

    await renderLoadedApp()

    await screen.findByRole('dialog', { name: 'Join Campaign' })
    await waitFor(() => expect(localStorage.getItem('aidm:selectedPlayerId')).toBeNull())
    expect(socketMock.socket.emit).not.toHaveBeenCalledWith(
      'join_session',
      expect.objectContaining({ player_id: 999 }),
    )
  })


  it('exposes character load, create, and edit actions in the inspector', async () => {
    await renderLoadedApp()

    const characterActions = screen.getByLabelText('Character actions')
    fireEvent.click(within(characterActions).getByRole('button', { name: 'Load' }))
    expect(await screen.findByRole('dialog', { name: 'Join Campaign' })).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Close character chooser'))

    fireEvent.click(within(characterActions).getByRole('button', { name: 'Edit' }))
    expect(await screen.findByRole('dialog', { name: 'Edit Character' })).toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Close character editor'))

    fireEvent.click(within(characterActions).getByRole('button', { name: 'New' }))
    expect(await screen.findByRole('dialog', { name: 'Create Character' })).toBeInTheDocument()
  })

  it('closes the character delete confirmation with Escape without deleting', async () => {
    await renderLoadedApp()

    const characterActions = screen.getByLabelText('Character actions')
    const deleteButton = within(characterActions).getByRole('button', { name: 'Delete' })
    deleteButton.focus()
    fireEvent.click(deleteButton)

    const dialog = await screen.findByRole('dialog', { name: 'Delete Character' })
    expect(dialog).toHaveAccessibleDescription(/This permanently removes the character/)
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus())

    fireEvent.keyDown(document, { key: 'Escape' })

    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Delete Character' })).not.toBeInTheDocument(),
    )
    expect(appTestState.fetchCalls.some((call) => call.method === 'DELETE' && call.path === '/api/players/30')).toBe(false)
    expect(deleteButton).toHaveFocus()
  })

  it('keeps the character delete confirmation open while deletion is pending', async () => {
    await renderLoadedApp()

    const originalFetch = globalThis.fetch
    let resolveDelete: (response: Response) => void = () => undefined
    const pendingDelete = new Promise<Response>((resolve) => {
      resolveDelete = resolve
    })
    vi.stubGlobal(
      'fetch',
      vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
        const url = new URL(String(input), 'http://localhost:3000')
        const method = init?.method ?? 'GET'
        if (method === 'DELETE' && url.pathname === '/api/players/30') {
          const headers = new Headers(init?.headers)
          appTestState.fetchCalls.push({
            method,
            path: url.pathname,
            origin: url.origin,
            body: init?.body ? JSON.parse(String(init.body)) : null,
            authorization: headers.get('Authorization'),
            workspaceToken: headers.get('X-AIDM-Workspace-Token'),
            workspaceIdHeader: headers.get('X-AIDM-Workspace-Id'),
          })
          return pendingDelete
        }
        return originalFetch(input, init)
      }),
    )

    const characterActions = screen.getByLabelText('Character actions')
    fireEvent.click(within(characterActions).getByRole('button', { name: 'Delete' }))

    const dialog = await screen.findByRole('dialog', { name: 'Delete Character' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Delete Character' }))
    await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Deleting...' })).toBeDisabled())

    fireEvent.keyDown(document, { key: 'Escape' })

    expect(screen.getByRole('dialog', { name: 'Delete Character' })).toBeInTheDocument()
    expect(appTestState.fetchCalls.filter((call) => call.method === 'DELETE' && call.path === '/api/players/30')).toHaveLength(1)

    await act(async () => {
      resolveDelete(jsonResponse({ deleted: true }))
    })
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Delete Character' })).not.toBeInTheDocument(),
    )
  })

  it('shows a manual share link when clipboard access is unavailable', async () => {
    localStorage.setItem('aidm:baseUrl', 'https://backend-tunnel.ngrok-free.app')

    await renderLoadedApp()
    const shareButton = screen.getByRole('button', { name: 'Share' })
    shareButton.focus()
    fireEvent.click(shareButton)

    const dialog = await screen.findByRole('dialog', { name: 'Share Session' })
    const shareInput = within(dialog).getByLabelText('Session share link')
    expect(dialog).toHaveAccessibleDescription(
      'Send this to someone who can open this frontend and reach this backend. They can choose or create their own character after it opens.',
    )
    await waitFor(() => expect(shareInput).toHaveFocus())

    const shareValue = (shareInput as HTMLInputElement).value
    expect(shareValue).toContain('backend=https%3A%2F%2Fbackend-tunnel.ngrok-free.app')
    expect(shareValue).toContain('campaign=10')
    expect(shareValue).toContain('session=20')

    const closeIconButton = within(dialog).getByLabelText('Close share session')
    const copyButton = within(dialog).getByRole('button', { name: 'Copy Link' })
    copyButton.focus()
    fireEvent.keyDown(document, { key: 'Tab' })
    expect(closeIconButton).toHaveFocus()

    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(copyButton).toHaveFocus()

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Share Session' })).not.toBeInTheDocument(),
    )
    expect(shareButton).toHaveFocus()
  })

  it('requires explicit origin confirmation before a share-link backend receives any request', async () => {
    sessionStorage.setItem('aidm:authToken', 'legacy-account-token')
    sessionStorage.setItem('aidm:workspaceToken', 'legacy-workspace-token')
    window.history.replaceState(
      null,
      '',
      '/?campaign=10&session=20&backend=https%3A%2F%2Fbackend-tunnel.ngrok-free.app',
    )

    render(<App />)

    const dialog = await screen.findByRole('dialog', { name: 'Connect to Shared Backend' })
    expect(dialog).toHaveAccessibleDescription(
      'Only continue if you recognize and trust this backend. AIDM will not contact it before you confirm.',
    )
    expect(within(dialog).getByText('https://backend-tunnel.ngrok-free.app')).toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Username')).not.toBeInTheDocument()
    expect(within(dialog).queryByLabelText('Password')).not.toBeInTheDocument()
    expect(appTestState.fetchCalls.some((call) => call.origin === 'https://backend-tunnel.ngrok-free.app')).toBe(false)
    expect(
      socketMock.io.mock.calls.some(([url]) => url === 'https://backend-tunnel.ngrok-free.app'),
    ).toBe(false)
    expect(window.location.search).toBe(
      '?campaign=10&session=20&backend=https%3A%2F%2Fbackend-tunnel.ngrok-free.app',
    )

    fireEvent.click(within(dialog).getByRole('button', { name: 'Trust and Connect' }))

    await screen.findByRole('heading', { name: /Session Alpha/i })
    await waitFor(() =>
      expect(appTestState.fetchCalls).toEqual(
        expect.arrayContaining([
          expect.objectContaining({
            method: 'GET',
            path: '/api/health',
            origin: 'https://backend-tunnel.ngrok-free.app',
          }),
        ]),
      ),
    )

    expect(localStorage.getItem('aidm:baseUrl')).toBe('https://backend-tunnel.ngrok-free.app')
    expect(sessionStorage.getItem(originScopedStorageKey('aidm:authToken', ''))).toBe(
      'legacy-account-token',
    )
    expect(sessionStorage.getItem(originScopedStorageKey('aidm:workspaceToken', ''))).toBe(
      'legacy-workspace-token',
    )
    expect(
      sessionStorage.getItem(
        originScopedStorageKey('aidm:authToken', 'https://backend-tunnel.ngrok-free.app'),
      ),
    ).toBeNull()
    expect(
      sessionStorage.getItem(
        originScopedStorageKey('aidm:workspaceToken', 'https://backend-tunnel.ngrok-free.app'),
      ),
    ).toBeNull()
    const confirmedBackendRequests = appTestState.fetchCalls.filter(
      (call) => call.origin === 'https://backend-tunnel.ngrok-free.app',
    )
    expect(confirmedBackendRequests.length).toBeGreaterThan(0)
    expect(
      confirmedBackendRequests.every(
        (call) => call.authorization === null && call.workspaceToken === null,
      ),
    ).toBe(true)
    await waitFor(() => {
      expect(window.location.search).toBe('?campaign=10&session=20')
    })
  })

  it('opens a previously trusted share-link backend without another confirmation', async () => {
    localStorage.setItem('aidm:baseUrl', 'https://backend-tunnel.ngrok-free.app')
    window.history.replaceState(
      null,
      '',
      '/?campaign=10&session=20&backend=https%3A%2F%2Fbackend-tunnel.ngrok-free.app',
    )

    await renderLoadedApp()

    expect(screen.queryByRole('dialog', { name: 'Connect to Shared Backend' })).not.toBeInTheDocument()
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/health',
          origin: 'https://backend-tunnel.ngrok-free.app',
        }),
      ]),
    )
  })

  it('lets first-time campaign visitors join as an existing character', async () => {
    localStorage.removeItem('aidm:selectedPlayerId')

    await renderLoadedApp()

    const dialog = await screen.findByRole('dialog', { name: 'Join Campaign' })
    expect(within(dialog).getByRole('button', { name: 'Join as Ember' })).toBeInTheDocument()

    fireEvent.click(within(dialog).getByRole('button', { name: 'Join as Ember' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Join Campaign' })).not.toBeInTheDocument())
    await waitFor(() => expect(socketMock.socket.on).toHaveBeenCalledWith('connect', expect.any(Function)))
    await act(async () => {
      socketHandler<void>('connect')()
    })
    await waitFor(() =>
      expect(socketMock.socket.emit).toHaveBeenCalledWith(
        'join_session',
        expect.objectContaining({
          session_id: 20,
          player_id: 30,
        }),
      ),
    )
  })

  it('refreshes the selected player when inventory state is applied before canon finishes', async () => {
    await renderLoadedApp()
    const sessionStateFetchesBefore = appTestState.fetchCalls.filter(
      (call) => call.method === 'GET' && call.path === '/api/sessions/20/state',
    ).length

    appTestState.playerDetails[30] = {
      ...appTestState.playerDetails[30],
      inventory: [
        { name: 'Healing Potion', quantity: 2, weight: 0.5 },
        { name: 'Stick', quantity: 1 },
      ],
    }

    await act(async () => {
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: { player_id: number; inventory_changes_applied: Array<{ item_name: string; quantity: number }> }
      }>('turn_status')({
        session_id: 20,
        turn_id: 4,
        status: 'state_applied',
        details: {
          player_id: 30,
          inventory_changes_applied: [{ item_name: 'Stick', quantity: 1 }],
        },
      })
    })

    await screen.findByText('Stick')
    expect(
      appTestState.fetchCalls.filter((call) => call.method === 'GET' && call.path === '/api/sessions/20/state'),
    ).toHaveLength(sessionStateFetchesBefore)
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/players/30',
        }),
      ]),
    )
  })

  it('shows refreshed level and XP when state-applied XP crosses a level threshold', async () => {
    const { container } = await renderLoadedApp()
    expect(container.querySelector('.level-stack strong')).toHaveTextContent('2')

    appTestState.playerDetails[30] = {
      ...appTestState.playerDetails[30],
      level: 3,
      stats: {
        ...(appTestState.playerDetails[30].stats as Record<string, unknown>),
        xp: 1700,
        experience: 1700,
        next_level_at: 2700,
        nextLevelAt: 2700,
      },
    }

    await act(async () => {
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: {
          player_id: number
          character_state_changes_applied: Array<{ change_type: string; xp_delta: number }>
        }
      }>('turn_status')({
        session_id: 20,
        turn_id: 9,
        status: 'state_applied',
        details: {
          player_id: 30,
          character_state_changes_applied: [{ change_type: 'xp.add', xp_delta: 1400 }],
        },
      })
    })

    await waitFor(() => {
      expect(container.querySelector('.level-stack strong')).toHaveTextContent('3')
    })
    expect(screen.getByText('1.7K / 2.7K XP')).toBeInTheDocument()
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/players/30',
        }),
      ]),
    )
  })

  it('refreshes session state when a state_applied turn reports world snapshot changes', async () => {
    await renderLoadedApp()
    const sessionStateFetchesBefore = appTestState.fetchCalls.filter(
      (call) => call.method === 'GET' && call.path === '/api/sessions/20/state',
    ).length

    appTestState.sessionStates[20] = {
      ...appTestState.sessionStates[20],
      state_snapshot: {
        currentScene: {
          name: 'Moonlit Harbor',
          locationId: 'moonlit_harbor',
          sceneType: 'exploration',
          dangerLevel: 1,
          activeQuestIds: ['find_missing_sailor'],
        },
        quests: [
          {
            id: 'find_missing_sailor',
            title: 'Find the Missing Sailor',
            status: 'active',
            stage: 'Search the moonlit harbor',
          },
        ],
      },
    }

    await act(async () => {
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: { player_id: number; world_state_changed: boolean; snapshot_changed: boolean }
      }>('turn_status')({
        session_id: 20,
        turn_id: 7,
        status: 'state_applied',
        details: {
          player_id: 30,
          world_state_changed: true,
          snapshot_changed: true,
        },
      })
    })

    await screen.findByText('Moonlit Harbor')
    expect(
      appTestState.fetchCalls.filter((call) => call.method === 'GET' && call.path === '/api/sessions/20/state'),
    ).toHaveLength(sessionStateFetchesBefore + 1)
  })

  it('does not reload session state twice for matching state_applied and canon_applied world flags', async () => {
    await renderLoadedApp()
    const sessionStateFetchesBefore = appTestState.fetchCalls.filter(
      (call) => call.method === 'GET' && call.path === '/api/sessions/20/state',
    ).length

    appTestState.sessionStates[20] = {
      ...appTestState.sessionStates[20],
      state_snapshot: {
        currentScene: {
          name: 'Old Bell Tower',
          locationId: 'old_bell_tower',
          sceneType: 'exploration',
          dangerLevel: 2,
          activeQuestIds: [],
        },
      },
    }

    await act(async () => {
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: { player_id: number; world_state_changed: boolean; snapshot_changed: boolean }
      }>('turn_status')({
        session_id: 20,
        turn_id: 8,
        status: 'state_applied',
        details: {
          player_id: 30,
          world_state_changed: true,
          snapshot_changed: true,
        },
      })
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: { player_id: number; state_applied: boolean; world_state_changed: boolean; snapshot_changed: boolean }
      }>('turn_status')({
        session_id: 20,
        turn_id: 8,
        status: 'canon_applied',
        details: {
          player_id: 30,
          state_applied: true,
          world_state_changed: true,
          snapshot_changed: true,
        },
      })
    })

    await screen.findByText('Old Bell Tower')
    expect(
      appTestState.fetchCalls.filter((call) => call.method === 'GET' && call.path === '/api/sessions/20/state'),
    ).toHaveLength(sessionStateFetchesBefore + 1)
  })

  it('refreshes the selected player when a transfer affects them from another player turn', async () => {
    await renderLoadedApp()

    appTestState.playerDetails[30] = {
      ...appTestState.playerDetails[30],
      inventory: [
        { name: 'Healing Potion', quantity: 2, weight: 0.5 },
        { name: 'Small Roll', quantity: 1 },
      ],
    }

    await act(async () => {
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: {
          player_id: number
          affected_player_ids: number[]
          inventory_changes_applied: Array<{ player_id: number; item_name: string; quantity: number }>
        }
      }>('turn_status')({
        session_id: 20,
        turn_id: 6,
        status: 'state_applied',
        details: {
          player_id: 31,
          affected_player_ids: [31, 30],
          inventory_changes_applied: [{ player_id: 30, item_name: 'Small Roll', quantity: 1 }],
        },
      })
    })

    await screen.findByText('Small Roll')
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/players/30',
        }),
      ]),
    )
  })

  it('refreshes the selected player when immediate inventory state arrives as canon_applied', async () => {
    await renderLoadedApp()

    appTestState.playerDetails[30] = {
      ...appTestState.playerDetails[30],
      inventory: [
        { name: 'Healing Potion', quantity: 2, weight: 0.5 },
        { name: 'Rope', quantity: 1 },
      ],
    }

    await act(async () => {
      socketHandler<{
        session_id: number
        turn_id: number
        status: string
        details: {
          player_id: number
          state_applied: boolean
          inventory_changes_applied: Array<{ item_name: string; quantity: number; already_applied: boolean }>
        }
      }>('turn_status')({
        session_id: 20,
        turn_id: 5,
        status: 'canon_applied',
        details: {
          player_id: 30,
          state_applied: true,
          inventory_changes_applied: [{ item_name: 'Rope', quantity: 1, already_applied: true }],
        },
      })
    })

    await screen.findByText('Rope')
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/players/30',
        }),
      ]),
    )
  })

  it('lets first-time campaign visitors create a character before joining as a player', async () => {
    localStorage.removeItem('aidm:selectedPlayerId')

    await renderLoadedApp()

    const chooser = await screen.findByRole('dialog', { name: 'Join Campaign' })
    fireEvent.click(within(chooser).getByRole('button', { name: 'Create Character' }))

    const creator = await screen.findByRole('dialog', { name: 'Create Character' })
    fireEvent.change(within(creator).getByLabelText('Character Name'), {
      target: { value: 'Borin' },
    })
    fireEvent.click(within(creator).getByRole('button', { name: 'View Dwarf details' }))
    const dwarfDetails = await screen.findByRole('dialog', { name: 'Dwarf' })
    expect(within(dwarfDetails).getByText(/Dwarves are stone-wise, craft-proud/)).toBeInTheDocument()
    expect(within(dwarfDetails).getByText('Common, Dwarvish')).toBeInTheDocument()
    expect(within(dwarfDetails).getByText(/Average height:/)).toBeInTheDocument()
    fireEvent.click(within(dwarfDetails).getByRole('button', { name: 'Select Dwarf' }))
    fireEvent.click(within(creator).getByRole('button', { name: 'Male Dwarf' }))
    fireEvent.click(within(creator).getByRole('button', { name: 'Preview Cleric class' }))
    const clericDetails = await screen.findByRole('dialog', { name: 'Cleric' })
    expect(within(clericDetails).getByText(/Clerics heal, protect/)).toBeInTheDocument()
    expect(within(clericDetails).getByText('Life')).toBeInTheDocument()
    fireEvent.click(within(clericDetails).getByRole('button', { name: 'Select Cleric - Life' }))
    fireEvent.click(within(creator).getByRole('button', { name: 'Create Character' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Create Character' })).not.toBeInTheDocument())
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'POST',
          path: '/api/players/campaigns/10/players',
          body: expect.objectContaining({
            character_name: 'Borin',
            race: 'Dwarf',
            sex: 'male',
            char_class: 'Cleric - Life',
          }),
        }),
      ]),
    )
    const createCall = appTestState.fetchCalls.find(
      (call) => call.method === 'POST' && call.path === '/api/players/campaigns/10/players',
    )
    expect(createCall?.body).not.toHaveProperty('name')
    expect(await screen.findByText('Borin')).toBeInTheDocument()
  })

  it('keeps focus in the edited character field instead of snapping back to player name', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Account' }))
    fireEvent.click(within(screen.getByRole('menu', { name: 'Account options' })).getByRole('menuitem', {
      name: 'Profile settings',
    }))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit character' }))

    const dialog = await screen.findByRole('dialog', { name: 'Edit Character' })
    const raceInput = within(dialog).getByLabelText('Search races')
    raceInput.focus()
    fireEvent.change(raceInput, { target: { value: 'Elf' } })

    expect(document.activeElement).toBe(raceInput)
  })

  it('opens create campaign and submits through world plus campaign endpoints', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Add campaign' }))
    const dialog = await screen.findByRole('dialog', { name: 'Create New Campaign' })
    fireEvent.change(within(dialog).getByLabelText('Campaign Name'), {
      target: { value: 'Crystal Road' },
    })
    fireEvent.change(within(dialog).getByLabelText('Description'), {
      target: { value: 'Find the lantern city.' },
    })
    fireEvent.change(within(dialog).getByLabelText('New World Name'), {
      target: { value: 'Crystal Reach' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create Campaign' }))

    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Create New Campaign' })).not.toBeInTheDocument())
    await waitFor(() => expect(screen.getAllByText('Crystal Road').length).toBeGreaterThan(0))
    expect(await screen.findByRole('dialog', { name: 'Join Campaign' })).toBeInTheDocument()
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'POST', path: '/api/worlds' }),
        expect.objectContaining({ method: 'POST', path: '/api/campaigns' }),
      ]),
    )
  })

  it('can create a campaign from an existing world without creating a duplicate world', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Add campaign' }))
    const dialog = await screen.findByRole('dialog', { name: 'Create New Campaign' })
    fireEvent.change(within(dialog).getByLabelText('Campaign Name'), {
      target: { value: 'Lantern Annex' },
    })
    fireEvent.change(within(dialog).getByLabelText('Description'), {
      target: { value: 'A side story in the smoke world.' },
    })
    fireEvent.change(within(dialog).getByLabelText('World'), {
      target: { value: '5' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Create Campaign' }))

    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Create New Campaign' })).not.toBeInTheDocument(),
    )
    const worldCreates = appTestState.fetchCalls.filter(
      (call) => call.method === 'POST' && call.path === '/api/worlds',
    )
    const campaignCreate = appTestState.fetchCalls.find(
      (call) => call.method === 'POST' && call.path === '/api/campaigns',
    )
    expect(worldCreates).toHaveLength(0)
    expect(campaignCreate?.body).toEqual(
      expect.objectContaining({ title: 'Lantern Annex', world_id: 5 }),
    )
  })

  it('can create a campaign from a bundled example campaign pack', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Add campaign' }))
    const dialog = await screen.findByRole('dialog', { name: 'Create New Campaign' })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Campaign Pack' }))
    fireEvent.click(await within(dialog).findByRole('option', { name: /Shadow Under Eryn Luin/ }))

    expect(within(dialog).getAllByText('Shadow Under Eryn Luin').length).toBeGreaterThan(0)
    expect(
      within(dialog).getAllByText(
        'An original Lord of the Rings-inspired campaign in Middle-earth. The company is drawn into a quiet borderland crisis where old Dwarf-roads beneath the Blue Mountains have awakened, a forgotten oath is being exploited, and a remnant servant of the Shadow seeks a buried seeing-stone shard before the Free Peoples can seal it away.',
      ).length,
    ).toBeGreaterThan(0)
    expect(within(dialog).getAllByText('Medium campaign / 4-6 sessions').length).toBeGreaterThan(0)
    expect(within(dialog).getAllByText('12-18 hours / 6 checkpoints').length).toBeGreaterThan(0)

    fireEvent.click(within(dialog).getByRole('button', { name: 'Create Campaign' }))

    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: 'Create New Campaign' })).not.toBeInTheDocument(),
    )
    await waitFor(() => expect(screen.getAllByText('Shadow Under Eryn Luin').length).toBeGreaterThan(0))
    const importCall = appTestState.fetchCalls.find(
      (call) =>
        call.method === 'POST'
        && call.path === '/api/campaigns/example-packs/middle_earth.shadow_under_eryn_luin/import',
    )
    expect(importCall?.body).toEqual({})
    expect(
      appTestState.fetchCalls.some((call) => call.method === 'POST' && call.path === '/api/campaigns'),
    ).toBe(false)
  })

  it('opens the session menu and supports rename and delete actions', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Session menu' }))
    const sessionMenu = await screen.findByRole('menu', { name: 'Session menu' })
    fireEvent.click(within(sessionMenu).getByRole('menuitem', { name: 'Rename session' }))
    const renameDialog = await screen.findByRole('dialog', { name: 'Rename Session' })
    fireEvent.change(within(renameDialog).getByLabelText('Session Name'), {
      target: { value: 'Session Beta' },
    })
    fireEvent.click(within(renameDialog).getByRole('button', { name: 'Rename Session' }))

    await screen.findByRole('heading', { name: /Session Beta/i })
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'PATCH', path: '/api/sessions/20' }),
      ]),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Session menu' }))
    const reopenedSessionMenu = await screen.findByRole('menu', { name: 'Session menu' })
    fireEvent.click(within(reopenedSessionMenu).getByRole('menuitem', { name: 'Delete session' }))
    const deleteDialog = await screen.findByRole('dialog', { name: 'Delete Session' })
    fireEvent.click(within(deleteDialog).getByRole('button', { name: 'Delete Session' }))

    await screen.findByText('No sessions yet.')
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'DELETE', path: '/api/sessions/20' }),
      ]),
    )
  })

  it('imports an exported session JSON file and selects the restored session', async () => {
    await renderLoadedApp()

    const importPayload = {
      exportedAt: fixedNow.toISOString(),
      selectedIds: {
        campaignId: 10,
        sessionId: 20,
        playerId: 10,
      },
      selectedSession: {
        session_id: 20,
        display_name: 'Restored Trial',
        state_snapshot: {},
      },
      sessionState: {
        current_location: 'Restored Hall',
        current_quest: 'Check import flow',
        rolling_summary: 'Imported summary appears after restore.',
        active_segments: [],
        memory_snippets: [],
      },
      logEntries: [
        {
          id: 1,
          message: 'Imported log entry',
          entry_type: 'dm',
          metadata: {},
          timestamp: fixedNow.toISOString(),
        },
      ],
      turnEvents: [],
    }
    const file = new File([JSON.stringify(importPayload)], 'aidm-session-20.json', {
      type: 'application/json',
    })

    fireEvent.click(screen.getByRole('button', { name: 'Import' }))
    fireEvent.change(screen.getByLabelText('Import session file'), {
      target: { files: [file] },
    })

    expect(await screen.findByRole('heading', { name: /Restored Trial/i })).toBeInTheDocument()
    expect(await screen.findByText('Imported log entry')).toBeInTheDocument()
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'POST',
          path: '/api/sessions/import',
          body: expect.objectContaining({
            selectedSession: expect.objectContaining({ display_name: 'Restored Trial' }),
          }),
        }),
      ]),
    )
  })

  it('keeps long DM responses visible in the current response and full response views', async () => {
    await renderLoadedApp()

    await waitFor(() => expect(screen.getAllByText(/Full narrator ending remains visible/i).length).toBeGreaterThan(0))

    fireEvent.click(screen.getByRole('tab', { name: 'DM Response' }))
    await waitFor(() => expect(screen.getAllByText(/Full narrator ending remains visible/i).length).toBeGreaterThan(0))
  })

  it('keeps the latest DM response expanded when a state update arrives after it', async () => {
    appTestState.sessionLogs[20] = [
      ...appTestState.sessionLogs[20],
      {
        id: 4,
        entry_type: 'system',
        message: 'State updated: thunderer took 8 damage.',
        metadata: { source: 'state_update' },
        timestamp: '2026-06-06T10:43:00.000Z',
      },
    ]

    const rendered = await renderLoadedApp()

    expect(screen.getByText(/State updated: thunderer took 8 damage/i)).toBeInTheDocument()
    const currentResponse = rendered.container.querySelector<HTMLElement>('.turn-row.current .dm-response-card')
    expect(currentResponse).not.toBeNull()
    expect(currentResponse as HTMLElement).toHaveTextContent(/Latest Response/i)
    expect(currentResponse as HTMLElement).toHaveTextContent(/Full narrator ending remains visible/i)
  })

  it('expands prior turns so long historical responses can be read', async () => {
    await renderLoadedApp()

    expect(screen.queryByText(/Hidden tail for expansion verification/i)).not.toBeInTheDocument()
    const expandButtons = await screen.findAllByRole('button', { name: 'Expand turn' })
    fireEvent.click(expandButtons[1])

    expect(screen.getByText(/Hidden tail for expansion verification/i)).toBeInTheDocument()
    expect(expandButtons[1]).toHaveAttribute('aria-expanded', 'true')
  })

  it('updates the campaign session count after starting a new session', async () => {
    await renderLoadedApp()

    fireEvent.click(screen.getByRole('button', { name: 'Start session' }))

    expect(await screen.findByRole('heading', { name: /Session Beta/i })).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByText(/2 Sessions/i).length).toBeGreaterThan(0))
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'POST', path: '/api/sessions/start' }),
      ]),
    )
  })

  it('opens all canon facts from the View All Canon control', async () => {
    await renderLoadedApp()

    expect(screen.queryByText(/first canon fact/i)).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /View All Canon/i }))

    await waitFor(() => expect(screen.getAllByText(/first canon fact/i).length).toBeGreaterThan(0))
    expect(screen.getByText('Session State')).toBeInTheDocument()
  })

  it('manages map details and campaign segments from the map tab', async () => {
    await renderLoadedApp()

    const inspectorPanels = screen.getByRole('tablist', { name: 'Inspector panels' })
    fireEvent.click(within(inspectorPanels).getByRole('tab', { name: 'Map' }))

    fireEvent.change(screen.getByLabelText('Map title'), {
      target: { value: 'Ash Gate Map' },
    })
    fireEvent.change(screen.getByLabelText('Map description'), {
      target: { value: 'The ruined gate and reservoir crossing.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Create map details' }))

    await screen.findByText('Ash Gate Map')
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'POST', path: '/api/maps' }),
      ]),
    )

    fireEvent.change(screen.getByLabelText('Segment title'), {
      target: { value: 'Ash Gate' },
    })
    fireEvent.change(screen.getByLabelText('Segment description'), {
      target: { value: 'The first dangerous crossing.' },
    })
    fireEvent.change(screen.getByLabelText('Trigger condition'), {
      target: { value: 'When the party approaches the gate.' },
    })
    fireEvent.change(screen.getByLabelText('Tags'), {
      target: { value: 'danger, gate' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add segment' }))

    await screen.findByText('Ash Gate')
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'POST', path: '/api/segments' }),
      ]),
    )

    const activeCheckbox = screen.getByLabelText('Start as active segment')
    fireEvent.click(activeCheckbox)
    fireEvent.change(screen.getByLabelText('Segment title'), {
      target: { value: 'Hidden Bridge' },
    })
    fireEvent.change(screen.getByLabelText('Segment description'), {
      target: { value: 'A quiet route around the reservoir.' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Add segment' }))

    await screen.findByText('Hidden Bridge')
    const hiddenBridgeArticle = screen.getByText('Hidden Bridge').closest('article')
    expect(hiddenBridgeArticle).not.toBeNull()
    if (!hiddenBridgeArticle) return
    fireEvent.click(within(hiddenBridgeArticle).getByRole('button', { name: 'Set active' }))

    await waitFor(() =>
      expect(within(hiddenBridgeArticle).getByRole('button', { name: 'Set active' })).toBeDisabled(),
    )
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'POST',
          path: '/api/segments/activate',
          body: expect.objectContaining({
            campaign_id: 10,
            exclusive: true,
            segment_id: 51,
          }),
        }),
      ]),
    )

    const ashGateArticle = screen.getByText('Ash Gate').closest('article')
    expect(ashGateArticle).not.toBeNull()
    if (!ashGateArticle) return
    fireEvent.click(within(ashGateArticle).getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(screen.queryByText('Ash Gate')).not.toBeInTheDocument())
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ method: 'DELETE', path: '/api/segments/50' }),
      ]),
    )
  })

  it('shows beta incidents in an operator-only inspector tab', async () => {
    await renderLoadedApp()

    const inspectorPanels = screen.getByRole('tablist', { name: 'Inspector panels' })
    fireEvent.click(within(inspectorPanels).getByRole('tab', { name: 'Ops' }))

    expect(await screen.findByRole('heading', { name: 'Beta Incidents' })).toBeInTheDocument()
    expect(await screen.findByText('DM turn failed before completion.')).toBeInTheDocument()
    expect(screen.getByText('Tester reported broken continuity.')).toBeInTheDocument()
    expect(screen.getByText('Canon extraction job failed.')).toBeInTheDocument()
    expect(screen.getByText('socket.dm_persist_failed recorded 1 time.')).toBeInTheDocument()
    expect(screen.getAllByText('deepseek / deepseek-v4-pro').length).toBeGreaterThan(0)
    const qualityCard = await screen.findByRole('region', { name: 'Selected session quality' })
    expect(within(qualityCard).getByRole('heading', { name: 'Session Quality' })).toBeInTheDocument()
    expect(within(qualityCard).getByText('Review')).toBeInTheDocument()
    expect(within(qualityCard).getByText('Session Alpha')).toBeInTheDocument()
    expect(within(qualityCard).getByText('1800 ms')).toBeInTheDocument()
    expect(within(qualityCard).getByText('Provider/model: deepseek / deepseek-v4-pro (2 turns)')).toBeInTheDocument()
    expect(
      within(qualityCard).getByText('Review recommended: 1 failed turn, 1 failed canon job, 1 bad-turn report.'),
    ).toBeInTheDocument()
    expect(
      within(qualityCard).getByText('Latency: 1800 ms p95, 1800 ms avg across 2 samples.'),
    ).toBeInTheDocument()

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

    fireEvent.click(screen.getAllByRole('button', { name: 'Export support bundle for session 20' })[0])

    await waitFor(() => expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob)))
    expect(downloadClick).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:support-bundle')
    expect(appTestState.fetchCalls).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          method: 'GET',
          path: '/api/beta/incidents',
        }),
        expect.objectContaining({
          method: 'GET',
          path: '/api/beta/session-quality',
        }),
        expect.objectContaining({
          method: 'GET',
          path: '/api/beta/support-bundle',
        }),
      ]),
    )
  })

  it('shows and clears the jump-to-latest control based on turn feed scroll position', async () => {
    const { container } = await renderLoadedApp()
    const feed = container.querySelector<HTMLElement>('.turn-feed')
    expect(feed).not.toBeNull()
    if (!feed) return

    Object.defineProperty(feed, 'scrollHeight', { configurable: true, value: 1200 })
    Object.defineProperty(feed, 'clientHeight', { configurable: true, value: 300 })
    Object.defineProperty(feed, 'scrollTo', {
      configurable: true,
      value: vi.fn(({ top }: ScrollToOptions) => {
        feed.scrollTop = Number(top)
      }),
    })
    feed.scrollTop = 0

    fireEvent.scroll(feed)
    const latestButton = await screen.findByRole('button', { name: /Latest/i })
    expect(latestButton).toBeInTheDocument()

    fireEvent.click(latestButton)
    expect(feed.scrollTo).toHaveBeenCalledWith({ top: 1200, behavior: 'smooth' })
    expect(screen.queryByRole('button', { name: /Latest/i })).not.toBeInTheDocument()
  })

  it('exposes selected navigation, tab, and menu states to assistive tech', async () => {
    await renderLoadedApp()

    expect(screen.getByRole('button', { name: /Smoke Campaign/i })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: /Session Alpha/i })).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('button', { name: 'Turns' })).toHaveAttribute('aria-current', 'page')

    const sessionViews = screen.getByRole('tablist', { name: 'Session views' })
    expect(within(sessionViews).getByRole('tab', { name: 'Turns' })).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(within(sessionViews).getByRole('tab', { name: 'DM Response' }))
    expect(within(sessionViews).getByRole('tab', { name: 'DM Response' })).toHaveAttribute('aria-selected', 'true')

    const inspectorPanels = screen.getByRole('tablist', { name: 'Inspector panels' })
    fireEvent.click(within(inspectorPanels).getByRole('tab', { name: 'Canon' }))
    expect(within(inspectorPanels).getByRole('tab', { name: 'Canon' })).toHaveAttribute('aria-selected', 'true')

    const accountButton = screen.getByRole('button', { name: 'Account' })
    fireEvent.click(accountButton)
    expect(accountButton).toHaveAttribute('aria-expanded', 'true')
    expect(within(screen.getByRole('menu', { name: 'Account options' })).getByRole('menuitem', {
      name: 'Profile settings',
    })).toBeInTheDocument()

    const sessionMenuButton = screen.getByRole('button', { name: 'Session menu' })
    fireEvent.click(sessionMenuButton)
    expect(sessionMenuButton).toHaveAttribute('aria-expanded', 'true')
    expect(within(screen.getByRole('menu', { name: 'Session menu' })).getByRole('menuitem', {
      name: 'Rename session',
    })).toBeInTheDocument()
  })

  it('keeps icon-only controls named and light theme contrast readable', async () => {
    const { container } = await renderLoadedApp()

    const iconOnlyButtons = [...container.querySelectorAll<HTMLButtonElement>('button')].filter((button) => {
      const visibleText = button.textContent?.trim() ?? ''
      return visibleText.length === 0 && button.querySelector('svg')
    })
    expect(iconOnlyButtons.length).toBeGreaterThan(0)
    iconOnlyButtons.forEach((button) => {
      expect(button.getAttribute('aria-label') || button.getAttribute('title')).toBeTruthy()
    })

    const colors = lightThemeColors()
    for (const foreground of lightThemeContrastForegrounds) {
      for (const background of lightThemeContrastBackgrounds) {
        expect(colors[foreground]).toMatch(/^#[0-9a-fA-F]{6}$/)
        expect(colors[background]).toMatch(/^#[0-9a-fA-F]{6}$/)
        expect(contrastRatio(colors[foreground], colors[background])).toBeGreaterThanOrEqual(4.5)
      }
    }
  })

  it('traps modal focus and returns focus to the opener when closed', async () => {
    await renderLoadedApp()

    const addCampaignButton = screen.getByRole('button', { name: 'Add campaign' })
    addCampaignButton.focus()
    fireEvent.click(addCampaignButton)

    const dialog = await screen.findByRole('dialog', { name: 'Create New Campaign' })
    const campaignNameInput = within(dialog).getByLabelText('Campaign Name')
    await waitFor(() => expect(document.activeElement).toBe(campaignNameInput))

    const closeButton = within(dialog).getByRole('button', { name: 'Close create campaign' })
    const submitButton = within(dialog).getByRole('button', { name: 'Create Campaign' })
    closeButton.focus()
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true })
    expect(document.activeElement).toBe(submitButton)

    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Create New Campaign' })).not.toBeInTheDocument())
    expect(document.activeElement).toBe(addCampaignButton)
  })

  it('toggles TTS and falls back when browser fullscreen is blocked', async () => {
    await renderLoadedApp()

    const ttsButton = screen.getByRole('button', { name: 'Turn TTS on' })
    fireEvent.click(ttsButton)
    expect(await screen.findByRole('button', { name: 'Turn TTS off' })).toHaveAttribute('aria-pressed', 'true')

    fireEvent.click(screen.getByRole('button', { name: 'Enter fullscreen' }))
    expect(await screen.findByRole('button', { name: 'Exit fullscreen' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getAllByText(/Native fullscreen was blocked/i).length).toBeGreaterThan(0)
  })

  it('connects streamed socket chunks to TTS before the response ends', async () => {
    await renderLoadedApp()
    appTestState.ttsFetchHandler = vi.fn(async () => jsonResponse({ error: 'stream probe' }, { status: 400 }))

    fireEvent.click(screen.getByRole('button', { name: 'Turn TTS on' }))
    await screen.findByRole('button', { name: 'Turn TTS off' })

    await act(async () => {
      socketHandler<{ turn_id: number }>('dm_response_start')({ turn_id: 76 })
      socketHandler<{ turn_id: number; chunk: string }>('dm_chunk')({
        turn_id: 76,
        chunk: 'The first torch gutters out, and a cold draft rolls over the stone.',
      })
    })

    await waitFor(() => expect(appTestState.ttsFetchHandler).toHaveBeenCalledOnce())
    expect(appTestState.fetchCalls.filter((call) => call.method === 'POST' && call.path === '/api/tts/stream')).toEqual([
      expect.objectContaining({
        body: { text: 'The first torch gutters out, and a cold draft rolls over the stone.' },
      }),
    ])
  })
})
