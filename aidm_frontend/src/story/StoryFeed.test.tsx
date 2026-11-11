// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
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
        onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()}
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
        onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()}
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
        onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()}
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
        onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()}
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

  it('shows Bardic Inspiration after the roll breakdown in the gold tone', () => {
    const { container } = render(
      <StoryFeed
        board={board({
          turnRows: [
            entry('log-1', 'player', 'Wren', 'I roll a d20+3 for stealth: 9 + Bardic Inspiration d6 (4) = 16', {
              turn_id: 2,
              authoritative_roll: {
                die: 'd20', mode: 'normal', rolls: [9], kept: 9, modifier: 3, total: 16, task_dc: 15,
                inspiration: { die: 'd6', roll: 4 },
              },
            }),
          ],
        })}
        textSize="default"
        onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()}
      />,
    )
    const breakdown = container.querySelector('.story-roll-breakdown')
    const inspiration = container.querySelector('.story-roll-inspiration')
    expect(breakdown).toHaveTextContent('d20 9 + 3')
    expect(inspiration).toHaveTextContent('+ d6 4 inspiration')
    expect(breakdown?.nextElementSibling).toBe(inspiration)
    expect(screen.getByText('16')).toBeInTheDocument()
    expect(screen.getByText('success')).toBeInTheDocument()
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
        onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()}
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
        onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()}
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
    const { rerender } = render(<StoryFeed board={props} textSize="default" onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()} />)

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
        onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()}
      />,
    )
    expect(screen.getByText('Feedback sent.')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Bad turn reported' })).toBeDisabled()
  })

  it('offers the empty session a way to begin, then waits for the story', () => {
    const onStartAdventure = vi.fn()
    const turnRows = [entry('log-1', 'system', 'System', 'Welcome to the table.', { kind: 'session_welcome' })]
    const { rerender } = render(<StoryFeed board={board({ turnRows, onStartAdventure })} textSize="default" onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()} />)
    expect(screen.getByText('The Lantern Road')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Begin the adventure' }))
    expect(onStartAdventure).toHaveBeenCalledTimes(1)

    rerender(<StoryFeed board={board({ turnRows, onStartAdventure, sendPending: true })} textSize="default" onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()} />)
    expect(screen.queryByRole('button', { name: 'Begin the adventure' })).toBeNull()
    expect(screen.getByText('The story is beginning')).toBeInTheDocument()
  })

  it('shows shimmering lines while the session loads', () => {
    const { container } = render(<StoryFeed board={board({ sessionLoading: true })} textSize="default" onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()} />)
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
        onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()}
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
        onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()}
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
        onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()}
      />,
    )
    expect(screen.getByText('The Drowned Lantern')).toBeInTheDocument()
    expect(screen.queryByText('Previously')).toBeNull()
  })

  it('ends the feed with the epilogue once the adventure reaches its ending, and can be dismissed for the session', () => {
    window.sessionStorage.clear()
    const onExitToTitle = vi.fn()
    const ended = board({
      turnRows: [entry('log-2', 'dm', 'DM', 'The names return to the road.', { turn_id: 9 })],
      worldState: {
        ...makeBoardFixture('exploration').worldState,
        ending: { title: 'The Road Remembers', summary: 'Every erased name is spoken again.' },
      },
      downloadSessionChronicle: vi.fn(async () => {}),
    })
    render(<StoryFeed board={ended} textSize="default" onExitToTitle={onExitToTitle} onOpenBeatTarget={vi.fn()} />)

    const epilogue = screen.getByRole('region', { name: 'The Road Remembers' })
    expect(within(epilogue).getByText('The end')).toHaveClass('story-label')
    expect(within(epilogue).getByRole('heading', { name: 'The Road Remembers' })).toHaveClass('story-epilogue-title')
    expect(within(epilogue).getByText('Every erased name is spoken again.')).toBeInTheDocument()
    fireEvent.click(within(epilogue).getByRole('button', { name: 'Download the Chronicle' }))
    expect(ended.downloadSessionChronicle).toHaveBeenCalledTimes(1)
    fireEvent.click(within(epilogue).getByRole('button', { name: 'Start another adventure' }))
    expect(onExitToTitle).toHaveBeenCalledTimes(1)

    fireEvent.click(within(epilogue).getByRole('button', { name: 'Keep playing' }))
    expect(screen.queryByRole('region', { name: 'The Road Remembers' })).not.toBeInTheDocument()
    cleanup()
    render(<StoryFeed board={ended} textSize="default" onExitToTitle={onExitToTitle} onOpenBeatTarget={vi.fn()} />)
    expect(screen.queryByRole('region', { name: 'The Road Remembers' })).not.toBeInTheDocument()
  })

  it('has no epilogue before the ending', () => {
    render(<StoryFeed board={board({ turnRows: [entry('log-2', 'dm', 'DM', 'Rain.', { turn_id: 1 })] })} textSize="default" onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()} />)
    expect(screen.queryByText('The end')).not.toBeInTheDocument()
  })
})

describe('StoryFeed beats from the server', () => {
  it('shows exactly the turn’s beats, opens the Journal, Map or Settings from them, and reads a check on its roll', () => {
    const onOpenBeatTarget = vi.fn()
    const { container } = render(
      <StoryFeed
        board={board({
          turnRows: [
            entry('log-1', 'player', 'Ysolde Marrow', 'I roll a d20+3 for investigation: 11 = 14', { turn_id: 4, dc_hint: '10' }),
            entry('log-2', 'dm', 'DM', 'The ledger lists erased names.', {
              turn_id: 4,
              storyBeats: [
                { kind: 'check', text: 'Investigation check: 14 — success' },
                { kind: 'clue', text: 'Clue — The erased caravan ledger' },
                { kind: 'place', text: 'New place on the map — The Old North Road' },
                { kind: 'item_gained', text: 'Taken — Caravan Strongbox Key' },
                { kind: 'hint', text: 'Scripted narrator — use the suggestions, or connect an AI narrator in Settings for free-form play' },
                { kind: 'omen', text: 'The bell rings again' },
              ],
            }),
            entry('log-3', 'system', 'System', 'State updated', {
              turn_id: 4,
              state_log: { lines: [{ status: 'applied', changeType: 'scene.interactable.action', message: 'scene.interactable.action update applied' }] },
            }),
          ],
        })}
        textSize="default"
        onExitToTitle={vi.fn()}
        onOpenBeatTarget={onOpenBeatTarget}
      />,
    )

    expect(container.querySelector('.story-roll-check')).toHaveTextContent('Investigation check: 14 — success')
    expect(container.querySelector('.story-roll-outcome')).toBeNull()
    const changes = screen.getByRole('list', { name: 'What changed' })
    expect(within(changes).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Clue — The erased caravan ledger',
      'New place on the map — The Old North Road',
      'Taken — Caravan Strongbox Key',
      'Scripted narrator — use the suggestions, or connect an AI narrator in Settings for free-form play',
      'The bell rings again',
    ])
    expect(screen.getByRole('log', { name: 'Session timeline' })).not.toHaveTextContent(/update applied|scene\.interactable/)

    fireEvent.click(within(changes).getByRole('button', { name: 'Clue — The erased caravan ledger' }))
    fireEvent.click(within(changes).getByRole('button', { name: 'New place on the map — The Old North Road' }))
    fireEvent.click(within(changes).getByRole('button', { name: /^Scripted narrator/ }))
    expect(onOpenBeatTarget.mock.calls).toEqual([['journal'], ['map'], ['settings']])
    expect(within(changes).queryByRole('button', { name: 'Taken — Caravan Strongbox Key' })).toBeNull()
    expect(within(changes).getByText('The bell rings again')).toHaveClass('story-turn-note')
    // The hint is one quiet line in the same secondary style, not a small-caps label.
    const hint = within(changes).getByRole('button', { name: /^Scripted narrator/ })
    expect(hint).toHaveClass('story-turn-note')
    expect(hint).not.toHaveClass('story-label')
  })

  it('shows an attack against the AC the roll itself carries, or just hit or miss without one', () => {
    const attack = (id: string, turnId: number, roll: JsonRecord) =>
      entry(id, 'player', 'Arden Vale', 'I roll a d20+4 for longsword attack: 9 = 13', {
        turn_id: turnId,
        dc_hint: 'AC 14',
        authoritative_roll: { rule_type: 'attack', die: 'd20', mode: 'normal', rolls: [9], kept: 9, modifier: 4, total: 13, task_dc: 14, ...roll },
      })
    const { container } = render(
      <StoryFeed
        board={board({
          turnRows: [
            attack('log-1', 4, { outcome: 'success', target_ac: 13 }),
            attack('log-2', 5, { outcome: 'failure' }),
          ],
        })}
        textSize="default"
        onExitToTitle={vi.fn()}
        onOpenBeatTarget={vi.fn()}
      />,
    )
    expect([...container.querySelectorAll('.story-roll-outcome')].map((outcome) => outcome.textContent)).toEqual([
      'hit vs AC 13',
      'miss',
    ])
  })

  it('without server beats, keeps the client’s change lines but never a raw change type', () => {
    render(
      <StoryFeed
        board={board({
          turnRows: [
            entry('log-1', 'dm', 'DM', 'You search the bench.', { turn_id: 4 }),
            entry('log-2', 'system', 'System', 'scene.interactable.action update applied', {
              turn_id: 4,
              state_log: { lines: [{ status: 'applied', changeType: 'scene.interactable.action', message: 'scene.interactable.action update applied' }] },
            }),
          ],
        })}
        textSize="default"
        onExitToTitle={vi.fn()}
        onOpenBeatTarget={vi.fn()}
      />,
    )
    expect(screen.getByRole('log', { name: 'Session timeline' })).not.toHaveTextContent(/update applied|scene\.interactable/)
    expect(screen.queryByRole('list', { name: 'What changed' })).toBeNull()
  })
})

/** Lays the feed out in jsdom: beats 100px tall in a 400px window, scrolled by scrollTop. */
function layOutFeed() {
  const LINE = 100
  const VIEW = 400
  const feed = () => document.querySelector<HTMLElement>('.story-feed')
  const rect = (top: number, height: number) =>
    ({ top, bottom: top + height, left: 0, right: 800, width: 800, height, x: 0, y: top, toJSON: () => ({}) }) as DOMRect
  const geometry = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const container = feed()
    if (this === container) return rect(0, VIEW)
    const beats = container ? [...container.querySelectorAll('[data-beat-key]')] : []
    const index = beats.indexOf(this)
    return index < 0 ? rect(0, 0) : rect(index * LINE - (container?.scrollTop ?? 0), LINE)
  })
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return this.classList.contains('story-feed') ? this.querySelectorAll('[data-beat-key]').length * LINE + 120 : 0
    },
  })
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
    configurable: true,
    get(this: HTMLElement) {
      return this.classList.contains('story-feed') ? VIEW : 0
    },
  })
  const scrollTo = vi.fn(function (this: HTMLElement, options?: ScrollToOptions | number) {
    if (typeof options === 'object') this.scrollTop = options.top ?? this.scrollTop
  })
  const originalScrollTo = Element.prototype.scrollTo
  Element.prototype.scrollTo = scrollTo as unknown as typeof Element.prototype.scrollTo
  return {
    scrollTo,
    restore() {
      geometry.mockRestore()
      delete (HTMLElement.prototype as { scrollHeight?: number }).scrollHeight
      delete (HTMLElement.prototype as { clientHeight?: number }).clientHeight
      Element.prototype.scrollTo = originalScrollTo
    },
  }
}

describe('StoryFeed following the reader', () => {
  const history = Array.from({ length: 8 }, (_, index) =>
    entry(`log-${index + 1}`, 'dm', 'DM', `Narration ${index + 1}.`, { turn_id: index + 1 }),
  )
  const feedFor = (turnRows: TimelineEntry[], currentResponseEntry: TimelineEntry | null = null) => (
    <StoryFeed
      board={board({ turnRows, currentResponseEntry })}
      textSize="default"
      onExitToTitle={vi.fn()}
      onOpenBeatTarget={vi.fn()}
    />
  )
  let layout: ReturnType<typeof layOutFeed>

  beforeEach(() => {
    layout = layOutFeed()
  })

  afterEach(() => layout.restore())

  it('pins the feed to the viewer’s own action from wherever they were, and follows its narration', () => {
    const { rerender } = render(feedFor(history))
    const log = screen.getByRole('log', { name: 'Session timeline' })
    fireEvent.wheel(log)
    log.scrollTop = 0
    fireEvent.scroll(log)
    layout.scrollTo.mockClear()

    const own = entry('pending-1', 'player', 'Ysolde Marrow', 'Read the caravan ledger.', { client_message_id: 'c-1', persistence_status: 'pending' })
    rerender(feedFor([...history, own]))
    // Nine beats: the bottom of the feed, where the action is the latest line.
    expect(layout.scrollTo).toHaveBeenLastCalledWith({ top: 9 * 100 + 120 - 400, behavior: 'smooth' })
    expect(screen.queryByRole('button', { name: 'New — jump to latest' })).toBeNull()

    rerender(feedFor([...history, own], entry('stream-9', 'dm', 'DM', 'The ledger…', { turn_id: 9 }, true)))
    // The narration streams in under the action, which settles just below the top.
    expect(layout.scrollTo).toHaveBeenLastCalledWith({ top: 8 * 100 - 96, behavior: 'smooth' })
    expect(screen.queryByRole('button', { name: 'New — jump to latest' })).toBeNull()
  })

  it('offers the jump to the latest line only to a reader who scrolled up by hand', () => {
    const { rerender } = render(feedFor(history))
    const log = screen.getByRole('log', { name: 'Session timeline' })
    log.scrollTop = 0
    fireEvent.scroll(log)

    rerender(feedFor([...history, entry('log-9', 'dm', 'DM', 'Someone else’s turn.', { turn_id: 9 })]))
    expect(screen.queryByRole('button', { name: 'New — jump to latest' })).toBeNull()

    fireEvent.wheel(log)
    rerender(feedFor([...history, entry('log-9', 'dm', 'DM', 'Someone else’s turn.', { turn_id: 9 }), entry('log-10', 'dm', 'DM', 'And another.', { turn_id: 10 })]))
    expect(screen.getByRole('button', { name: 'New — jump to latest' })).toBeInTheDocument()
  })
})

