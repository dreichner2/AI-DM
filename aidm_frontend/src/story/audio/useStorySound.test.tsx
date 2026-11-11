// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { TimelineEntry } from '../../types'
import { setAmbienceEnabled, setEffectsEnabled } from './soundPreferences'
import { playCue } from './storySound'
import { useStorySound } from './useStorySound'

const engine = vi.hoisted(() => ({
  start: vi.fn(async () => true),
  stop: vi.fn(),
  setScene: vi.fn(),
  setDucked: vi.fn(),
  setLevels: vi.fn(),
  playCue: vi.fn(),
  setHeartbeat: vi.fn(),
}))
const createStoryAudio = vi.hoisted(() => vi.fn(() => engine))
vi.mock('./storyAmbience', () => ({ createStoryAudio }))

type Board = Parameters<typeof useStorySound>[0]

class FakeAudioContext {
  static made: FakeAudioContext[] = []
  resume = vi.fn(async () => undefined)
  close = vi.fn(async () => undefined)

  constructor() {
    FakeAudioContext.made.push(this)
  }
}

function entry(turnId: number, beats: Array<[string, string]> = []): TimelineEntry {
  return {
    id: `dm-${turnId}`,
    role: 'dm',
    speaker: 'DM',
    text: 'The story goes on.',
    timestamp: null,
    metadata: { turn_id: turnId, storyBeats: beats.map(([kind, text]) => ({ kind, text })) },
  }
}

function board(overrides: Partial<Board> = {}, snapshot: Record<string, unknown> = {}): Board {
  return {
    sessionId: 4,
    sessionState: {
      session_id: 4,
      state_snapshot: {
        currentScene: { locationId: 'inn' },
        locations: [{ id: 'inn', weather: 'rain' }],
        worldMap: { nodes: [{ id: 'inn', name: 'Inn', icon: 'inn', x: 20, y: 80, state: 'current' }], routes: [] },
        ...snapshot,
      },
    },
    turnRows: [entry(1, [['clue', 'Clue — Old ledger']])],
    currentResponseEntry: null,
    worldState: { clock: { day: 1, phase: 'evening', label: 'Day 1 · Evening' } },
    duckMusicForNarration: false,
    ...overrides,
  } as unknown as Board
}

function Harness({ value, inFight = false, viewerDying = false }: { value: Board; inFight?: boolean; viewerDying?: boolean }) {
  const place = useStorySound(value, { inFight, viewerDying })
  return <span data-weather={place.weather ?? 'none'} />
}

async function startSound(value = board()) {
  const view = render(<Harness value={value} />)
  fireEvent.pointerDown(window)
  await waitFor(() => expect(engine.setScene).toHaveBeenCalled())
  return view
}

describe('useStorySound', () => {
  beforeEach(() => {
    FakeAudioContext.made = []
    vi.stubGlobal('AudioContext', FakeAudioContext)
    // Both are off until the player turns them on.
    setAmbienceEnabled(true)
    setEffectsEnabled(true)
  })

  afterEach(() => {
    cleanup()
    localStorage.clear()
    vi.clearAllMocks()
    vi.unstubAllGlobals()
  })

  it("makes no sound and loads nothing before the player's first gesture, and returns the place's weather", () => {
    const { container } = render(<Harness value={board()} />)
    expect(FakeAudioContext.made).toEqual([])
    expect(createStoryAudio).not.toHaveBeenCalled()
    expect(container.querySelector('[data-weather]')).toHaveAttribute('data-weather', 'rain')
  })

  it('starts on the first gesture at the default volumes with the place: hearth at an inn, rain over it', async () => {
    await startSound()
    expect(FakeAudioContext.made).toHaveLength(1)
    expect(FakeAudioContext.made[0].resume).toHaveBeenCalled()
    expect(engine.start).toHaveBeenCalledOnce()
    expect(engine.setLevels).toHaveBeenLastCalledWith({ ambience: 0.35, effects: 0.5 })
    expect(engine.setScene).toHaveBeenLastCalledWith({ bed: 'hearth', rain: true })
    expect(engine.setDucked).toHaveBeenLastCalledWith(false)
    expect(engine.setHeartbeat).toHaveBeenLastCalledWith(false)
  })

  it('ducks for the narrator, hushes a fight, beats while the hero is dying and stops when the view leaves', async () => {
    const value = board()
    const view = await startSound(value)
    view.rerender(<Harness value={{ ...value, duckMusicForNarration: true }} inFight viewerDying />)
    expect(engine.setDucked).toHaveBeenLastCalledWith(true)
    expect(engine.setScene).toHaveBeenLastCalledWith({ bed: 'hush', rain: true })
    expect(engine.setHeartbeat).toHaveBeenLastCalledWith(true)
    expect(engine.playCue.mock.calls).toEqual([['fight_start']])

    view.unmount()
    expect(engine.stop).toHaveBeenCalledOnce()
    expect(FakeAudioContext.made[0].close).toHaveBeenCalledOnce()
    playCue('clue')
    expect(engine.playCue.mock.calls).toEqual([['fight_start']])
  })

  it('follows the Ambience and Effects settings', async () => {
    await startSound()
    act(() => setAmbienceEnabled(false))
    expect(engine.setScene).toHaveBeenLastCalledWith(null)
    expect(engine.setLevels).toHaveBeenLastCalledWith({ ambience: 0, effects: 0.5 })
    act(() => setEffectsEnabled(false))
    expect(engine.setLevels).toHaveBeenLastCalledWith({ ambience: 0, effects: 0 })
  })

  it('never starts while ambience and effects are off, as they are until the player turns them on', () => {
    localStorage.clear()
    render(<Harness value={board()} />)
    fireEvent.pointerDown(window)
    fireEvent.keyDown(window, { key: 'a' })
    expect(FakeAudioContext.made).toEqual([])
  })

  it('stays silent, without an error, when the browser has no audio context to give', () => {
    vi.stubGlobal('AudioContext', undefined)
    render(<Harness value={board()} />)
    expect(() => fireEvent.pointerDown(window)).not.toThrow()
    expect(createStoryAudio).not.toHaveBeenCalled()
  })

  it("cues each new turn's victory, defeat, level or clue once, never the history it loaded with", async () => {
    const history = [entry(5, [['clue', 'Clue — Old ledger'], ['level', 'Reached level 2']])]
    const value = board({ turnRows: [] })
    const view = await startSound(value)
    view.rerender(<Harness value={{ ...value, turnRows: history }} />)
    expect(engine.playCue).not.toHaveBeenCalled()

    const turnSix = entry(6, [['clue', 'Clue — Burnt map'], ['place', 'New place on the map — Ford']])
    view.rerender(<Harness value={{ ...value, turnRows: [...history, turnSix] }} />)
    expect(engine.playCue.mock.calls).toEqual([['clue']])

    // A turn with several beats plays the one that matters most; the same beats again play nothing.
    const turnSeven = entry(7, [['clue', 'Clue — Seal'], ['level', 'Reached level 3'], ['combat_end', 'Victory']])
    const live = [...history, turnSix, turnSeven]
    view.rerender(<Harness value={{ ...value, turnRows: live }} />)
    view.rerender(<Harness value={{ ...value, turnRows: live, currentResponseEntry: { ...turnSeven, id: 'live' } }} />)
    expect(engine.playCue.mock.calls).toEqual([['clue'], ['victory']])

    // An older page loaded later is history too.
    const olderPage = [entry(3, [['defeat', 'Defeated — you wake later with 1 HP']]), entry(4, [['clue', 'Clue — Map']])]
    view.rerender(<Harness value={{ ...value, turnRows: [...olderPage, ...live] }} />)
    expect(engine.playCue.mock.calls).toEqual([['clue'], ['victory']])

    const turnEight = entry(8, [['defeat', 'Defeated — you wake later with 1 HP'], ['combat_end', 'You got away']])
    view.rerender(<Harness value={{ ...value, turnRows: [...olderPage, ...live, turnEight] }} />)
    expect(engine.playCue.mock.calls).toEqual([['clue'], ['victory'], ['defeat']])
  })

  it('cues fight_start when a fight begins, not when the session loads into one', async () => {
    const value = board()
    const view = render(<Harness value={{ ...value, sessionState: null }} />)
    fireEvent.pointerDown(window)
    await waitFor(() => expect(engine.setScene).toHaveBeenCalled())
    view.rerender(<Harness value={value} inFight />)
    expect(engine.playCue).not.toHaveBeenCalled()
    view.rerender(<Harness value={value} />)
    view.rerender(<Harness value={value} inFight />)
    expect(engine.playCue.mock.calls).toEqual([['fight_start']])
  })
})
