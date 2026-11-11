// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import type { SessionBoardProps } from '../../SessionBoard'
import { testBoard, testInspector, testWorld } from '../dockTestFixtures'
import { JournalSection } from './JournalSection'

function withSnapshot(snapshot: Record<string, unknown>): SessionBoardProps {
  const board = testBoard()
  if (!board.sessionState) throw new Error('fixture has a session state')
  return { ...board, sessionState: { ...board.sessionState, state_snapshot: snapshot } }
}

const journal = {
  quest: {
    id: 'road',
    title: 'The Road of Unremembered Kings',
    summary: 'Find out who erased the caravan’s names.',
    status: 'active',
    stageTitle: 'The Caravan Without Names',
    stageSummary: 'Question the witnesses at the Lantern Post.',
  },
  objectives: [
    { id: 'ledger', title: 'Read the caravan ledger', optional: false, status: 'completed' },
    { id: 'bench', title: 'Search the driver’s bench', optional: true, status: 'active' },
  ],
  clues: [{ id: 'clue_ledger', title: 'The erased caravan ledger', text: 'Every name scratched away.' }],
  people: [
    { id: 'mara', name: 'Mara Reedwake', role: 'innkeeper_and_local_witness', summary: 'Runs the Lantern Post.', met: true },
    { id: 'scribe', name: 'The Cinder Scribe', role: 'villain', summary: 'Unseen.', met: false },
  ],
  places: [
    { id: 'post', name: 'The Lantern Post', state: 'current' },
    { id: 'road', name: 'The Old North Road', state: 'discovered' },
  ],
}

afterEach(() => cleanup())

describe('JournalSection', () => {
  it('reads the server’s journal: quest, stage, objectives so far, clues, people met and places', () => {
    const { container } = render(
      <JournalSection
        board={withSnapshot({ journal })}
        inspector={testInspector({ visibleRecentMemory: [['Mara swore the caravan came in empty.', 'S1E4']] })}
      />,
    )

    expect(screen.getByText('The Road of Unremembered Kings')).toHaveClass('story-quest-title')
    expect(screen.getByText('The Caravan Without Names')).toHaveClass('story-label')
    expect(screen.getByText('Question the witnesses at the Lantern Post.')).toBeInTheDocument()
    expect(screen.queryByText('The Grey Caravan')).not.toBeInTheDocument()
    expect(screen.queryByText(/No stage recorded/)).not.toBeInTheDocument()

    const objectives = container.querySelector('.story-objective-list') as HTMLElement
    expect(within(objectives).getAllByRole('img').map((glyph) => glyph.getAttribute('aria-label'))).toEqual([
      'Completed objective',
      'Open objective',
    ])
    expect(within(objectives).getByText(/Search the driver’s bench/)).toHaveTextContent('Search the driver’s bench optional')

    expect(screen.getByText('Every name scratched away.')).toBeInTheDocument()
    const people = screen.getByRole('heading', { name: 'People' }).parentElement as HTMLElement
    expect(within(people).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Mara Reedwake — innkeeper and local witnessRuns the Lantern Post.',
    ])
    const places = screen.getByRole('heading', { name: 'Places' }).parentElement as HTMLElement
    expect(within(places).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'The Lantern Post — current',
      'The Old North Road — discovered',
    ])
    const memory = screen.getByRole('heading', { name: 'Recent memory' }).parentElement as HTMLElement
    expect(within(memory).getByRole('listitem')).toHaveTextContent(/^Mara swore the caravan came in empty\.$/)
  })

  it('omits an empty stage line', () => {
    render(
      <JournalSection
        board={withSnapshot({ journal: { ...journal, quest: { ...journal.quest, stageTitle: '', stageSummary: '' } } })}
        inspector={testInspector()}
      />,
    )
    expect(screen.queryByText('The Caravan Without Names')).not.toBeInTheDocument()
    expect(document.querySelector('.story-quest-stage')).toBeNull()
  })

  it('without the journal, shows objective titles and plain roles, never dispositions or a missing stage', () => {
    render(
      <JournalSection
        board={testBoard()}
        inspector={testInspector({
          worldStatePanel: testWorld({
            activeQuests: [{
              id: 'road',
              title: 'The Road of Unremembered Kings',
              status: 'active',
              stage: '',
              objective: '',
              objectives: [{
                id: 'q1',
                title: 'Read the caravan ledger',
                description: 'Question witnesses, examine the wagons, and inspect the erased ledger.',
                status: 'open',
                optional: false,
              }],
            }],
            knownNpcs: [
              { id: 'mara', name: 'Mara Reedwake', race: '', role: 'innkeeper_and_local_witness', disposition: 'cautious_helpful', status: 'present' },
              { id: 'tovin', name: 'Tovin Bell', race: '', role: 'Role unknown', disposition: 'unknown', status: 'present' },
            ],
          }),
        })}
      />,
    )
    expect(screen.getByText('Read the caravan ledger')).toBeInTheDocument()
    expect(screen.queryByText(/Question witnesses/)).not.toBeInTheDocument()
    const people = screen.getByRole('heading', { name: 'People' }).parentElement as HTMLElement
    expect(within(people).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Mara Reedwake — innkeeper and local witness',
      'Tovin Bell',
    ])
    expect(people).not.toHaveTextContent(/cautious|unknown/)
  })
})
