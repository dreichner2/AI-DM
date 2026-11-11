// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { ComingBackBlock } from '../apiContract.generated'
import type { SessionBoardProps } from '../SessionBoard'
import { TitleScreen } from '../TitleScreen'
import type { JsonRecord, TimelineEntry } from '../types'
import { testBoard, testComposer, testInspector } from './dockTestFixtures'
import { JournalSection } from './drawer/JournalSection'
import { buildStoryBeats } from './storyBeats'
import { returnChunk } from './storyChunks'
import { StoryComposer } from './StoryComposer'
import { StoryFeed } from './StoryFeed'
import { useStoryReturn, type StoryReturnActions } from './useStoryReturn'

const CHAPTER_END = {
  number: 1,
  title: 'The Empty Caravan',
  checkpointId: 'cp_01_empty_caravan',
  decided: ['You gave Mara Reedwake your word.', 'You took the cutthroat alive.'],
  open: ['Mara Reedwake is still waiting for word.', 'They had horses waiting in the north lane.', 'Ask Osric about the letter'],
  teaser: 'Next: the old north road, where the lights are waiting.',
}

function dm(id: string, text: string, metadata: JsonRecord): TimelineEntry {
  return { id, role: 'dm', speaker: 'DM', text, timestamp: null, metadata }
}

function storyReturn(overrides: Partial<StoryReturnActions> = {}): StoryReturnActions {
  return {
    notePlan: vi.fn(async () => true),
    removePlan: vi.fn(async () => undefined),
    loadComingBack: vi.fn(async () => null),
    beginNextChapter: vi.fn(),
    playAgain: vi.fn(),
    leaving: null,
    ...overrides,
  }
}

function boardWith(overrides: Partial<SessionBoardProps> = {}, snapshot: JsonRecord = {}): SessionBoardProps {
  const board = testBoard()
  if (!board.sessionState) throw new Error('fixture has a session state')
  return {
    ...board,
    sessionLogHasMore: false,
    sessionState: { ...board.sessionState, state_snapshot: snapshot },
    ...overrides,
  }
}

const noChrome = { connection: 'ready' as const, canUseOperatorTools: false, endedChapter: null }

beforeAll(async () => {
  Element.prototype.scrollTo = () => {}
  // The sections are a lazy chunk: load it first, so a busy machine never outwaits a query.
  returnChunk.preload()
  await import('./StoryReturn')
})

afterEach(() => {
  cleanup()
  window.sessionStorage.clear()
})

describe('the end of a chapter', () => {
  const ended = dm('log-9', 'The road north opens under the rain.', {
    turn_id: 9,
    storyBeats: [
      { kind: 'place', text: 'New place on the map — The Old North Road', refId: 'loc_old_north_road' },
      { kind: 'plan', text: 'Noted as a plan — write to my sister' },
      { kind: 'chapter_end', text: 'End of chapter 1 · The Empty Caravan', chapterEnd: CHAPTER_END },
    ],
  })

  it('is its own beat after the turn’s chips, and a noted plan is a chip that opens the journal', () => {
    const beats = buildStoryBeats([ended])
    expect(beats.map((beat) => beat.kind)).toEqual(['narration', 'turnBeats', 'chapterEnd'])
    const chips = beats[1].kind === 'turnBeats' ? beats[1].beats : []
    expect(chips.map((beat) => [beat.kind, beat.target])).toEqual([
      ['place', 'map'],
      ['plan', 'journal'],
    ])
    expect(beats[2]).toMatchObject({ kind: 'chapterEnd', gap: 'turn', text: 'End of chapter 1 · The Empty Caravan', chapterEnd: CHAPTER_END })
  })

  it('reads as label lines: what you decided, what is still open, and the teaser', async () => {
    render(<StoryFeed board={boardWith({ turnRows: [ended] })} textSize="default" onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()} />)

    const entry = await screen.findByRole('region', { name: 'End of chapter 1 · The Empty Caravan' })
    expect(within(entry).getByText('End of chapter 1 · The Empty Caravan')).toHaveClass('story-label')
    expect(within(entry).getByText('What you decided')).toHaveClass('story-label')
    expect(within(entry).getByText('Still open')).toHaveClass('story-label')
    expect(within(entry).getAllByRole('listitem').map((item) => item.textContent)).toEqual([...CHAPTER_END.decided, ...CHAPTER_END.open])
    expect(within(entry).getByText(CHAPTER_END.teaser)).toHaveClass('story-return-teaser')
  })
})

describe('the ending card', () => {
  const snapshot = {
    campaignPack: {
      ending: { title: 'The Road Remembers', text: 'Every erased name is spoken again.' },
      afterword: {
        replay: 'the_road_of_unremembered_kings',
        becameOfThem: [
          { npcId: 'npc_mara', name: 'Mara Reedwake', text: 'Mara Reedwake keeps a lamp in the north window now.' },
          { npcId: 'npc_tovin', name: 'Tovin Bell', text: 'Tovin Bell still has something in his pocket.' },
        ],
        yourWord: [{ id: 'promised_mara_word', status: 'left open', line: 'Mara Reedwake is still waiting for word.' }],
      },
    },
  }

  it('tells what became of them and how each word stood, with Play it again before Start another adventure', async () => {
    const actions = storyReturn()
    const board = boardWith(
      {
        turnRows: [dm('log-2', 'The names return to the road.', { turn_id: 9 })],
        worldState: { ...testBoard().worldState, ending: { title: 'The Road Remembers', summary: 'Every erased name is spoken again.' } },
        storyReturn: actions,
      },
      snapshot,
    )
    render(<StoryFeed board={board} textSize="default" onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()} />)

    const card = screen.getByRole('region', { name: 'The Road Remembers' })
    expect(await within(card).findByText('What became of them')).toHaveClass('story-label')
    expect(within(card).getByText('Your word')).toHaveClass('story-label')
    expect(within(card).getAllByRole('listitem').map((item) => item.textContent)).toEqual([
      'Mara Reedwake keeps a lamp in the north window now.',
      'Tovin Bell still has something in his pocket.',
      'Mara Reedwake is still waiting for word.',
    ])
    expect(card).not.toHaveTextContent(/ENDINGS|SECRETS| of \d/)
    expect(within(card).getAllByRole('button').map((button) => button.textContent)).toEqual([
      'Download the Chronicle',
      'Play it again',
      'Start another adventure',
      'Keep playing',
    ])
    fireEvent.click(within(card).getByRole('button', { name: 'Play it again' }))
    expect(actions.playAgain).toHaveBeenCalledWith('the_road_of_unremembered_kings')
  })
})

describe('coming back and leaving', () => {
  const block: ComingBackBlock = {
    where: 'You are in the common room at the Lantern Post Inn, set to investigate the empty caravan.',
    people: ['Osric Hale believes you act with authority.'],
    plans: ['Ask Osric about the letter'],
  }

  it('shows where you stand above the action box until the first action', async () => {
    const actions = storyReturn({ loadComingBack: vi.fn(async () => block) })
    const board = boardWith({ storyReturn: actions, actionComposerProps: testComposer({ actionText: 'I look around.', submitAction: vi.fn(() => true) }) })
    render(<StoryComposer board={board} chrome={noChrome} onOpenDrawer={vi.fn()} />)

    const greeting = await screen.findByRole('region', { name: 'Where you stand' })
    expect(within(greeting).getByText('Where you stand')).toHaveClass('story-label')
    expect(within(greeting).getByText('You meant to')).toHaveClass('story-label')
    expect(within(greeting).getAllByRole('listitem').map((item) => item.textContent)).toEqual([block.where, ...block.people, ...block.plans])
    expect(greeting).not.toHaveTextContent('Next:')

    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    expect(screen.queryByRole('region', { name: 'Where you stand' })).not.toBeInTheDocument()
  })

  it('asks a player leaving what they mean to do next, with Skip', async () => {
    const note = vi.fn()
    const skip = vi.fn()
    render(<StoryComposer board={boardWith({ storyReturn: storyReturn({ leaving: { note, skip } }) })} chrome={noChrome} onOpenDrawer={vi.fn()} />)

    const answer = await screen.findByLabelText('Before you go — what do you mean to do next?')
    fireEvent.change(answer, { target: { value: 'Find the man in grey' } })
    fireEvent.click(screen.getByRole('button', { name: 'Note it and leave' }))
    expect(note).toHaveBeenCalledWith('Find the man in grey')
    fireEvent.click(screen.getByRole('button', { name: 'Skip' }))
    expect(skip).toHaveBeenCalledTimes(1)
  })
})

describe('the leaving question’s rule', () => {
  function hook(plans: unknown, ended = false) {
    const exitToTitle = vi.fn()
    const options = {
      baseUrl: '',
      auth: '',
      sessionId: 4,
      playerId: 30,
      ready: true,
      ended,
      plans,
      reload: vi.fn(async () => undefined),
      follow: vi.fn(),
      submit: vi.fn(() => true),
      exitToTitle,
      playNow: vi.fn(),
    }
    return { exitToTitle, ...renderHook(() => useStoryReturn(options)) }
  }

  it('asks only a player with no plan noted this sitting, and never after the ending', () => {
    const asked = hook([{ id: 'p1', text: 'Old plan', playerId: 30, createdAt: '2020-01-01T00:00:00+00:00' }])
    act(() => asked.result.current.askBeforeLeaving())
    expect(asked.result.current.storyReturn.leaving).not.toBeNull()
    expect(asked.exitToTitle).not.toHaveBeenCalled()
    act(() => asked.result.current.storyReturn.leaving?.skip())
    expect(asked.exitToTitle).toHaveBeenCalledTimes(1)
    expect(asked.result.current.storyReturn.leaving).toBeNull()

    const noted = hook([{ id: 'p2', text: 'Fresh plan', playerId: 30, createdAt: new Date(Date.now() + 1000).toISOString() }])
    act(() => noted.result.current.askBeforeLeaving())
    expect(noted.exitToTitle).toHaveBeenCalledTimes(1)

    const finished = hook([], true)
    act(() => finished.result.current.askBeforeLeaving())
    expect(finished.exitToTitle).toHaveBeenCalledTimes(1)
  })
})

describe('the journal’s plans', () => {
  it('lists the plans, each with Done', () => {
    const actions = storyReturn()
    const board = boardWith({ storyReturn: actions }, { journal: { plans: [{ id: 'plan_1', text: 'Ask Osric about the letter', playerId: 30 }] } })
    render(<JournalSection board={board} inspector={testInspector()} />)

    const plans = screen.getByRole('region', { name: 'Plans' })
    expect(within(plans).getByText('Ask Osric about the letter')).toHaveClass('story-journal-name')
    fireEvent.click(within(plans).getByRole('button', { name: 'Done: Ask Osric about the letter' }))
    expect(actions.removePlan).toHaveBeenCalledWith('plan_1')
  })
})

describe('Your chronicle', () => {
  const props = {
    pending: false,
    accountReady: true,
    heroes: [],
    heroesStatus: 'ready' as const,
    selectedHeroId: null,
    onSelectHero: vi.fn(),
    resume: null,
    safeMode: false,
    onPlayNow: vi.fn(),
    onContinue: vi.fn(),
    onCreateCampaign: vi.fn(),
    onOpenAdventures: vi.fn(),
    onLogIn: vi.fn(),
    onCreateAccount: vi.fn(),
  }

  it('is offered only once an adventure is finished, and lists its endings and what was never found', async () => {
    const { rerender } = render(<TitleScreen {...props} />)
    expect(screen.queryByRole('button', { name: 'Your chronicle' })).not.toBeInTheDocument()

    rerender(
      <TitleScreen
        {...props}
        chronicle={[{ packId: 'road', title: 'The Road of Unremembered Kings', endings: ['The Road Remembers', 'Only a Road'], timesCompleted: 2, neverFound: 7 }]}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: 'Your chronicle' }))

    const screenRegion = await screen.findByRole('region', { name: 'Your chronicle' })
    expect(within(screenRegion).getByText('The Road of Unremembered Kings')).toBeInTheDocument()
    expect(within(screenRegion).getByText('The Road Remembers · Only a Road')).toBeInTheDocument()
    expect(within(screenRegion).getByText('Things you never found: 7')).toBeInTheDocument()
    fireEvent.click(within(screenRegion).getByRole('button', { name: 'Back' }))
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Your chronicle' })).not.toBeInTheDocument())
  })
})
