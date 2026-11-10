// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { SessionBoardProps } from '../SessionBoard'
import type { JsonRecord, TimelineEntry } from '../types'
import { makeBoardFixture } from './preview/fixtures'
import { StoryFeed } from './StoryFeed'

function entry(
  id: string,
  role: TimelineEntry['role'],
  speaker: string,
  text: string,
  metadata: JsonRecord = {},
  streaming?: boolean,
): TimelineEntry {
  return { id, role, speaker, text, timestamp: null, metadata, ...(streaming ? { streaming } : {}) }
}

const longNarration =
  'The sealed door vibrates as old glyphs wake one by one across the frame, each symbol answering Ember with a thin blue pulse. The first hinge groans, the second hinge clicks, and the stone remembers the handprint of a forgotten keeper. Hidden tail for the story reader.'

function board(overrides: Partial<SessionBoardProps> = {}): SessionBoardProps {
  return {
    ...makeBoardFixture('exploration'),
    sessionLogHasMore: false,
    turnRows: [],
    currentResponseEntry: null,
    sendPending: false,
    streamingTurnActive: false,
    ...overrides,
  }
}

beforeAll(() => {
  // jsdom has no scrolling; the feed's reading behaviour is exercised in the preview.
  Element.prototype.scrollTo = () => {}
})

afterEach(() => {
  cleanup()
})

describe('StoryFeed', () => {
  it('renders the full narration untruncated even when a system entry follows it', () => {
    const { container } = render(
      <StoryFeed
        board={board({
          turnRows: [
            entry('log-1', 'dm', 'DM', longNarration, { turn_id: 1 }),
            entry('log-2', 'system', 'System', 'Check Resolved: turn 1 resolved with roll 14.', { turn_id: 2 }),
          ],
        })}
        textSize="default"
      />,
    )
    expect(container.querySelector('.story-narration')).toHaveTextContent(longNarration)
    expect(screen.getByRole('log', { name: 'Session timeline' })).toHaveAttribute('aria-live', 'off')
  })

  it('sets narration as a book: curly dialogue, a drop cap on the opening and a caret while streaming', () => {
    const { container, rerender } = render(
      <StoryFeed
        board={board({
          turnRows: [entry('log-1', 'system', 'System', 'Welcome to the table.', { kind: 'session_welcome' })],
          currentResponseEntry: entry('stream-1', 'dm', 'DM', '"Stay close," Mira says. It\'s cold', { turn_id: 1 }, true),
          sendPending: true,
        })}
        textSize="large"
      />,
    )
    expect(container.querySelector('.narrative-dialogue')).toHaveTextContent('“Stay close,”')
    expect(container.querySelector('.story-narration')).toHaveTextContent('It’s cold')
    expect(container.querySelector('.story-narration')).toHaveAttribute('data-opening')
    expect(container.querySelector('.story-caret')).toBeInTheDocument()
    expect(screen.getByRole('log')).toHaveAttribute('data-text-size', 'large')
    expect(screen.getByRole('status')).toHaveTextContent('Dungeon Master response in progress.')

    rerender(
      <StoryFeed
        board={board({
          turnRows: [entry('log-1', 'system', 'System', 'Welcome to the table.', { kind: 'session_welcome' })],
          currentResponseEntry: entry('log-2', 'dm', 'DM', '"Stay close," Mira says. It\'s cold.', { turn_id: 1 }),
        })}
        textSize="large"
      />,
    )
    expect(container.querySelector('.story-caret')).toBeNull()
    expect(container.querySelectorAll('.story-narration')).toHaveLength(1)
    expect(screen.getByRole('status')).toHaveTextContent('Dungeon Master response complete.')
  })

  it('renders change chips, rolls and notices but no bookkeeping', () => {
    render(
      <StoryFeed
        board={board({
          turnRows: [
            entry('log-1', 'dm', 'DM', 'Rain.', { turn_id: 1 }),
            entry('log-2', 'system', 'System', 'State updated:', {
              turn_id: 1,
              state_log: {
                lines: [
                  { status: 'applied', message: 'Took 4 damage.', changeType: 'health.damage' },
                  { status: 'applied', message: 'Added 12 gp.', changeType: 'currency.add' },
                ],
              },
            }),
            entry('log-3', 'player', 'Wren', 'I roll a d20+5 for investigation: 14 = 19', { turn_id: 2, dc_hint: '15' }),
            entry('log-4', 'system', 'System', 'Canon applied'),
            entry('log-5', 'system', 'System', 'Brannoc joined the table.'),
          ],
        })}
        textSize="default"
      />,
    )
    const changes = screen.getByRole('list', { name: 'What changed' })
    expect(within(changes).getByText('Took 4 damage').closest('.story-chip')).toHaveAttribute('data-tone', 'fail')
    expect(within(changes).getByText('Added 12 gp').closest('.story-chip')).toHaveAttribute('data-tone', 'gold')
    expect(screen.getByText('Wren · Investigation')).toBeInTheDocument()
    expect(screen.getByText('19')).toBeInTheDocument()
    expect(screen.getByText('success')).toBeInTheDocument()
    expect(screen.getByText('vs DC 15')).toBeInTheDocument()
    expect(screen.getByText('Brannoc joined the table.')).toBeInTheDocument()
    expect(screen.queryByText(/Canon applied/)).toBeNull()
  })

  it('shows pending and failed actions and removes a failed one', () => {
    const dismissTimelineEntry = vi.fn()
    render(
      <StoryFeed
        board={board({
          dismissTimelineEntry,
          turnRows: [
            entry('local-1', 'player', 'Ysolde', 'I bar the door.', { client_message_id: 'c-1', persistence_status: 'pending' }),
            entry('local-2', 'player', 'Ysolde', 'I bar the window.', { client_message_id: 'c-2', persistence_status: 'failed' }),
          ],
        })}
        textSize="default"
      />,
    )
    expect(screen.getByText('sending…')).toBeInTheDocument()
    expect(screen.getByText('not sent')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Remove' }))
    expect(dismissTimelineEntry).toHaveBeenCalledWith('local-2')
  })

  it('marks narration whose save failed', () => {
    render(
      <StoryFeed
        board={board({ turnRows: [entry('log-1', 'dm', 'DM', 'Rain.', { turn_id: 1, persistence_status: 'failed' })] })}
        textSize="default"
      />,
    )
    expect(screen.getByText('not saved')).toBeInTheDocument()
  })

  it('rates and reports the latest completed narration with the beta feedback names', () => {
    const latest = entry('log-3', 'dm', 'DM', 'The bell tolls.', { turn_id: 3 })
    const submitTurnQuality = vi.fn()
    const reportBadTurn = vi.fn()
    const props = board({
      turnRows: [entry('log-1', 'dm', 'DM', 'Rain.', { turn_id: 1 }), entry('log-2', 'player', 'Wren', 'I listen.', { turn_id: 3 })],
      currentResponseEntry: latest,
      submitTurnQuality,
      reportBadTurn,
    })
    const { rerender } = render(<StoryFeed board={props} textSize="default" />)

    expect(screen.getAllByRole('button', { name: 'Rate this turn' })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: 'Rate this turn' }))
    const form = screen.getByRole('form', { name: 'Beta turn feedback' })
    expect(within(form).getByRole('button', { name: 'Coherence 4' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(form).getByRole('button', { name: 'Fun 4' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(within(form).getByRole('button', { name: 'Rules 2' }))
    fireEvent.click(within(form).getByRole('button', { name: 'Record' }))
    expect(submitTurnQuality).toHaveBeenCalledWith(latest, { coherence: 4, fun: 4, rules: 2 })

    fireEvent.click(screen.getByRole('button', { name: 'Report bad turn' }))
    expect(reportBadTurn).toHaveBeenCalledWith(latest)

    rerender(
      <StoryFeed
        board={{ ...props, ratedTurnQualityIds: new Set([3]), reportedBadTurnIds: new Set([3]) }}
        textSize="default"
      />,
    )
    expect(screen.getByText('Feedback sent.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Bad turn reported' })).toBeDisabled()
  })

  it('offers the empty session a way to begin, then waits for the story', () => {
    const onStartAdventure = vi.fn()
    const turnRows = [entry('log-1', 'system', 'System', 'Welcome to the table.', { kind: 'session_welcome' })]
    const { rerender } = render(<StoryFeed board={board({ turnRows, onStartAdventure })} textSize="default" />)
    expect(screen.getByText('The Lantern Road')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Begin the adventure' }))
    expect(onStartAdventure).toHaveBeenCalledTimes(1)

    rerender(<StoryFeed board={board({ turnRows, onStartAdventure, sendPending: true })} textSize="default" />)
    expect(screen.queryByRole('button', { name: 'Begin the adventure' })).toBeNull()
    expect(screen.getByText('The story is beginning')).toBeInTheDocument()
  })

  it('shows shimmering lines while the session loads', () => {
    const { container } = render(<StoryFeed board={board({ sessionLoading: true })} textSize="default" />)
    expect(screen.getByLabelText('Loading the story')).toBeInTheDocument()
    expect(container.querySelector('.story-empty')).toBeNull()
  })

  it('loads earlier pages of the story on request', () => {
    const loadOlderSessionLog = vi.fn(async () => undefined)
    render(
      <StoryFeed
        board={board({
          sessionLogHasMore: true,
          loadOlderSessionLog,
          turnRows: [entry('log-9', 'dm', 'DM', 'Rain.', { turn_id: 9 })],
        })}
        textSize="default"
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Earlier in the story' }))
    expect(loadOlderSessionLog).toHaveBeenCalledTimes(1)
    expect(document.querySelector('.story-narration')).not.toHaveAttribute('data-opening')
  })

  it('opens the chapter with its number, or the session title, and a recap', () => {
    const { rerender } = render(
      <StoryFeed
        board={board({
          sessionRecap: 'The party crossed the ford.',
          turnRows: [
            entry('log-1', 'system', 'System', 'Welcome to the table.', { kind: 'session_welcome' }),
            entry('log-2', 'dm', 'DM', 'Rain.', { turn_id: 1 }),
          ],
        })}
        textSize="default"
      />,
    )
    expect(screen.getByText('Chapter 3')).toBeInTheDocument()
    expect(screen.getByText('Previously')).toBeInTheDocument()
    expect(screen.getByText(/The party crossed the ford\./)).toBeInTheDocument()

    rerender(
      <StoryFeed
        board={board({
          activeSession: null,
          sessionRecap: '',
          turnRows: [
            entry('log-1', 'system', 'System', 'Welcome to the table.', { kind: 'session_welcome' }),
            entry('log-2', 'dm', 'DM', 'Rain.', { turn_id: 1 }),
          ],
        })}
        textSize="default"
      />,
    )
    expect(screen.getByText('The Drowned Lantern')).toBeInTheDocument()
    expect(screen.queryByText('Previously')).toBeNull()
  })
})
