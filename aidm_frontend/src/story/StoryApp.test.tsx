// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { JsonRecord, TimelineEntry } from '../types'
import { testBoard, testCombat, testControls, VIEWER_ID } from './dockTestFixtures'
import { StoryClarification } from './StoryClarification'
import { StoryCombatRibbon } from './StoryCombatRibbon'
import { StoryComposer } from './StoryComposer'
import { StoryFeed } from './StoryFeed'
import { StoryNotices, type StoryNotice } from './StoryNotices'
import { rollFromEntry } from './storyBeats'

function playerRoll(id: string, metadata: JsonRecord): TimelineEntry {
  return { id, role: 'player', speaker: 'Ysolde', text: '', timestamp: null, metadata }
}

const recoveryGate = {
  status: 'required',
  reason: 'post_dm_state_application_failed',
  turnId: 9,
  narrationSaved: true,
  mechanicsApplied: false,
  mechanicsStatus: 'none',
  preDmMechanicsApplied: false,
  preDmAppliedChangeCount: 0,
  postDmMechanicsApplied: false,
  createdAt: '2026-11-10T00:00:00Z',
} as const

beforeAll(() => {
  Element.prototype.scrollTo = () => {}
})

afterEach(() => cleanup())

describe('StoryClarification', () => {
  it('asks the question in prose and resolves the chosen answer', () => {
    const resolveClarification = vi.fn()
    const { rerender } = render(
      <StoryClarification board={{ clarificationRequest: null, resolveClarification }} />,
    )
    expect(screen.queryByRole('region')).not.toBeInTheDocument()

    rerender(
      <StoryClarification
        board={{
          resolveClarification,
          clarificationRequest: {
            id: 'clarify-1',
            turnId: 7,
            sessionId: 20,
            playerId: VIEWER_ID,
            type: 'item_resolution',
            prompt: 'Which key do you try?',
            originalPlayerMessage: 'I unlock the door.',
            originalAction: {},
            options: [
              { itemId: 'iron', label: 'Iron key', description: 'Found in the cellar' },
              { itemId: 'glyph', label: 'Glyph key' },
            ],
          },
        }}
      />,
    )

    const question = screen.getByRole('region', { name: 'The Dungeon Master asks' })
    expect(within(question).getByText('Which key do you try?')).toHaveClass('story-clarification-prompt')
    const answer = within(question).getByRole('button', { name: 'Iron key' })
    expect(answer).toHaveClass('story-chip')
    expect(answer).toHaveAttribute('title', 'Found in the cellar')
    fireEvent.click(within(question).getByRole('button', { name: 'Glyph key' }))
    expect(resolveClarification).toHaveBeenCalledWith('glyph')
  })
})

describe('StoryNotices', () => {
  const roll: StoryNotice = { key: 'roll-1', text: 'Brannoc: I roll a d20: 12' }
  const chapter: StoryNotice = { key: 'chapter-1', text: 'That chapter has ended — you are now in the current chapter.' }

  it('shows one line, the most recent notice to arrive', () => {
    const onSelect = vi.fn()
    const error: StoryNotice = {
      key: 'error-1',
      text: 'Socket connection failed: timeout',
      tone: 'fail',
      action: { label: 'Refresh and reconnect', onSelect },
    }
    const { container, rerender } = render(<StoryNotices notices={[roll]} />)
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('Brannoc: I roll a d20: 12')

    rerender(<StoryNotices notices={[roll, chapter]} />)
    expect(status).toHaveTextContent(chapter.text)
    expect(container.querySelectorAll('.story-notice')).toHaveLength(1)

    // Newest wins whatever order the notices are listed in.
    rerender(<StoryNotices notices={[error, roll, chapter]} />)
    expect(status).toHaveTextContent('Socket connection failed: timeout')
    expect(container.querySelector('.story-notice')).toHaveAttribute('data-tone', 'fail')
    fireEvent.click(within(status).getByRole('button', { name: 'Refresh and reconnect' }))
    expect(onSelect).toHaveBeenCalledTimes(1)

    rerender(<StoryNotices notices={[roll, chapter]} />)
    expect(status).toHaveTextContent(chapter.text)

    rerender(<StoryNotices notices={[{ key: 'roll-2', text: 'Wren: I roll a d20: 3' }, chapter]} />)
    expect(status).toHaveTextContent('Wren: I roll a d20: 3')

    rerender(<StoryNotices notices={[]} />)
    expect(status).toBeEmptyDOMElement()
  })
})

describe('Story fixes for the integrated app', () => {
  it('reads an attack roll as hit or miss against AC', () => {
    const hit = rollFromEntry(
      playerRoll('log-1', {
        turn_id: 4,
        task_dc: 13,
        authoritative_roll: { rule_type: 'attack', die: 'd20', rolls: [20], kept: 20, modifier: 6, total: 26 },
      }),
    )
    const miss = rollFromEntry(
      playerRoll('log-2', {
        turn_id: 5,
        rule_type: 'spell_attack',
        task_dc: 15,
        authoritative_roll: { die: 'd20', rolls: [4], kept: 4, modifier: 5, total: 9 },
      }),
    )
    const save = rollFromEntry(
      playerRoll('log-3', {
        turn_id: 6,
        task_dc: 12,
        authoritative_roll: { rule_type: 'saving_throw', die: 'd20', rolls: [14], kept: 14, modifier: 1, total: 15 },
      }),
    )
    expect(hit).toMatchObject({ attack: true, outcome: 'success', dc: 13 })
    expect(miss).toMatchObject({ attack: true, outcome: 'failure', dc: 15 })
    expect(save).toMatchObject({ attack: false, outcome: 'success', dc: 12 })

    const entries = [
      playerRoll('log-1', {
        turn_id: 4,
        task_dc: 13,
        authoritative_roll: { rule_type: 'attack', die: 'd20', rolls: [20], kept: 20, modifier: 6, total: 26 },
      }),
      playerRoll('log-3', {
        turn_id: 6,
        task_dc: 12,
        authoritative_roll: { rule_type: 'saving_throw', die: 'd20', rolls: [14], kept: 14, modifier: 1, total: 15 },
      }),
    ]
    const { container } = render(
      <StoryFeed board={testBoard({ turnRows: entries, currentResponseEntry: null, sessionLogHasMore: false })} textSize="default" onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()} />,
    )
    const outcomes = [...container.querySelectorAll('.story-roll-outcome')].map((element) => element.textContent)
    expect(outcomes).toEqual(['hit vs AC 13', 'success vs DC 12'])
  })

  it('names every combatant in full in a tooltip, however short the label', () => {
    render(
      <StoryCombatRibbon
        combat={testCombat()}
        playerId={VIEWER_ID}
        activePlayers={[]}
        controls={testControls()}
        recentSpellNames={[]}
        disabled={false}
        submitAction={vi.fn(() => true)}
        onOpenTray={vi.fn()}
      />,
    )
    const combatants = within(screen.getByRole('list', { name: 'Combatants' })).getAllByRole('listitem')
    expect(combatants.map((item) => item.getAttribute('title'))).toEqual([
      'Ysolde Marrow',
      'Goblin Sentry',
      'Distant Archer',
    ])
  })

  it('offers operators the table tools from the turn-recovery line', () => {
    const onOpenConsole = vi.fn()
    const board = testBoard({ turnRecoveryGate: recoveryGate })
    const { rerender } = render(
      <StoryComposer
        board={board}
        chrome={{ connection: 'ready', canUseOperatorTools: true, endedChapter: null, onOpenConsole }}
        onOpenDrawer={vi.fn()}
      />,
    )
    const status = screen.getByRole('status')
    expect(status).toHaveTextContent('The Dungeon Master is reviewing the last turn.')
    const tools = within(status).getByRole('button', { name: 'Open table tools' })
    expect(tools).toHaveClass('story-link')
    fireEvent.click(tools)
    expect(onOpenConsole).toHaveBeenCalledTimes(1)

    rerender(
      <StoryComposer
        board={board}
        chrome={{ connection: 'ready', canUseOperatorTools: false, endedChapter: null, onOpenConsole }}
        onOpenDrawer={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: 'Open table tools' })).not.toBeInTheDocument()
  })
})
