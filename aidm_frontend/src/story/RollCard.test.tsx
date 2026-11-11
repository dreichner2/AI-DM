// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { StoryAudio } from './audio/storyAmbience'
import { setStoryAudio } from './audio/storySound'
import { RollCard } from './RollCard'
import { CARD_GAP_MS, D20_CYCLE_MS, DICE_CYCLE_MS, PLAY_LIMIT_MS, SETTLE_MS, playRolls, resetRollPlayback, rollPlayback } from './rollPlayback'
import { rollCardRecord, type RollCardRecord } from './storyRolls'

const NOW = new Date('2026-11-11T12:00:00.000Z').getTime()

const pounce: RollCardRecord = {
  id: 't17-3',
  kind: 'attack',
  actor: { id: 'enemy_wardbound_beasts_1', name: 'Wardbound Beast', side: 'enemy' },
  target: { id: 'player_2', name: 'Seren Ashgrove' },
  label: 'Dust-Marked Pounce',
  d20: { faces: [14, 9], kept: 14, mode: 'advantage' },
  parts: [{ label: 'attack bonus', value: 5 }],
  total: 19,
  against: { kind: 'AC', value: 17 },
  outcome: 'hit',
  natural: null,
  damage: { dice: '1d8+3', faces: [4], bonus: 3, total: 7, type: 'piercing', critical: false, note: null },
}

const swing: RollCardRecord = {
  id: 't18-1',
  kind: 'attack',
  actor: { id: 'player_1', name: 'Arden Vale', side: 'hero' },
  target: { id: 'enemy_pale_lantern_1', name: 'Pale Lantern' },
  label: 'Longsword',
  d20: { faces: [20], kept: 20, mode: 'normal' },
  parts: [
    { label: 'Strength', value: 3 },
    { label: 'proficiency', value: 2 },
    { label: 'Bless', dice: '1d4', faces: [3], value: 3 },
  ],
  total: 28,
  against: { kind: 'AC', value: 14 },
  outcome: 'hit',
  natural: 'twenty',
  damage: {
    dice: '2d8+3',
    faces: [6, 5],
    bonus: 3,
    total: 21,
    type: 'slashing',
    critical: true,
    note: 'doubled dice (critical)',
    parts: [{ label: 'Hunter’s Mark', dice: '2d6', faces: [4, 3], value: 7 }],
  },
}

const lure: RollCardRecord = {
  id: 't17-2',
  kind: 'save',
  actor: { id: 'player_1', name: 'Arden Vale', side: 'hero' },
  label: 'Wisdom save',
  source: 'Lantern-Lure',
  d20: { faces: [1], kept: 1, mode: 'normal' },
  parts: [{ label: 'bonus', value: 1 }],
  total: 2,
  against: { kind: 'DC', value: 12 },
  outcome: 'failure',
  natural: 'one',
}

const healing: RollCardRecord = {
  id: 't19-1',
  kind: 'healing',
  actor: { id: 'player_2', name: 'Seren Ashgrove', side: 'hero' },
  target: { id: 'player_1', name: 'Arden Vale' },
  label: 'Healing Word',
  healing: { dice: '1d4+3', faces: [3], bonus: 3, total: 6 },
}

const initiative: RollCardRecord = {
  id: 't17-1',
  kind: 'initiative',
  label: 'Initiative',
  entries: [
    { name: 'Wardbound Beast', side: 'enemy', faces: [20], bonus: 2, total: 22 },
    { name: 'Arden Vale', side: 'hero', faces: [18], bonus: 1, total: 19 },
  ],
}

const deathSave: RollCardRecord = {
  id: 't20-1',
  kind: 'death_save',
  actor: { id: 'player_2', name: 'Seren Ashgrove', side: 'hero' },
  label: 'Death save',
  d20: { faces: [12], kept: 12, mode: 'normal' },
  parts: [],
  total: 12,
  against: { kind: 'DC', value: 10 },
  outcome: 'success',
  natural: null,
}

function card(container: HTMLElement) {
  return container.querySelector('.roll-card') as HTMLElement
}

/** What a reader sees: the element's parts, laid out side by side. */
function text(container: HTMLElement, selector: string) {
  const element = container.querySelector(selector)
  if (!element) return ''
  return [...element.childNodes].map((node) => node.textContent ?? '').join(' ').replace(/\s+/g, ' ').trim()
}

/** Let fake time pass the way a browser does: each timer fires, then the card re-renders and sets its next one. */
function advance(ms: number) {
  for (let elapsed = 0; elapsed < ms; elapsed += 1) {
    act(() => {
      vi.advanceTimersByTime(1)
    })
  }
}

beforeEach(() => {
  resetRollPlayback()
})

afterEach(() => {
  cleanup()
  vi.useRealTimers()
})

describe('RollCard landed', () => {
  it('reads an enemy attack: who, what and whom, the total, the dice, the outcome and the damage', () => {
    const { container } = render(<RollCard roll={pounce} />)

    expect(card(container)).toHaveAttribute('data-side', 'enemy')
    expect(text(container, '.roll-card-label')).toBe('Wardbound Beast · Dust-Marked Pounce → Seren Ashgrove')
    expect(text(container, '.roll-card-total')).toBe('19')
    expect(text(container, '.roll-card-roll .roll-card-breakdown')).toBe('d20 14 9 + 5')
    // With advantage both faces show; the dropped one is struck through.
    expect(container.querySelector('.roll-card-dropped')).toHaveTextContent('9')
    expect(text(container, '.roll-card-outcome')).toBe('hit vs AC 17')
    expect(text(container, '.roll-card-dice')).toBe('7 piercing 1d8 4 + 3')
  })

  it('reads a hero hit with a bonus die, a natural 20 and critical damage with extra dice', () => {
    const { container } = render(<RollCard roll={swing} />)

    expect(card(container)).toHaveAttribute('data-side', 'hero')
    expect(card(container)).toHaveAttribute('data-natural', 'twenty')
    expect(text(container, '.roll-card-roll .roll-card-breakdown')).toBe('d20 20 + 5 + 1d4 3')
    expect(text(container, '.roll-card-outcome')).toBe('hit vs AC 14 · natural 20')
    expect(text(container, '.roll-card-dice')).toBe('21 slashing 2d8 6 5 + 2d6 4 3 + 3 doubled dice (critical)')
  })

  it('reads a save with what forced it, a natural 1, and shows its parts behind Details', () => {
    const { container } = render(<RollCard roll={lure} />)

    expect(text(container, '.roll-card-label')).toBe('Arden Vale · Wisdom save (Lantern-Lure)')
    expect(card(container)).toHaveAttribute('data-natural', 'one')
    expect(text(container, '.roll-card-outcome')).toBe('failure vs DC 12 · natural 1')
    fireEvent.click(screen.getByRole('button', { name: 'Details' }))
    expect(text(container, '.roll-card-parts')).toBe('bonus + 1')
  })

  it("reads a contest against the hero's own Athletics, and an unknown kind as a DC", () => {
    const grab = rollCardRecord({
      id: 't9-2',
      kind: 'check',
      actor: { id: 'enemy_cutthroat_1', name: 'Cutthroat 1', side: 'enemy' },
      target: { id: 'player_1', name: 'Arden Vale' },
      label: 'Grab past a guard',
      d20: { faces: [5], kept: 5, mode: 'normal' },
      parts: [{ label: 'bonus', value: 1 }],
      total: 6,
      against: { kind: 'Athletics', value: 19 },
      outcome: 'failure',
    })
    const { container } = render(<RollCard roll={grab as RollCardRecord} />)

    expect(text(container, '.roll-card-outcome')).toBe('failure vs Athletics 19')
    expect(rollCardRecord({ ...grab, against: { kind: 'Spite', value: 12 } })?.against).toEqual({ kind: 'DC', value: 12 })
  })

  it('reads healing, a death save and initiative highest first', () => {
    const { container: heal } = render(<RollCard roll={healing} />)
    expect(text(heal, '.roll-card-label')).toBe('Seren Ashgrove · Healing Word → Arden Vale')
    expect(text(heal, '.roll-card-dice')).toBe('+6 1d4 3 + 3')

    const { container: save } = render(<RollCard roll={deathSave} />)
    expect(text(save, '.roll-card-total')).toBe('12')
    expect(text(save, '.roll-card-outcome')).toBe('success vs DC 10')

    const { container: order } = render(<RollCard roll={{ ...initiative, entries: [...(initiative.entries ?? [])].reverse() }} />)
    const lines = [...order.querySelectorAll('.roll-card-initiative li')].map((line) =>
      [...line.childNodes].map((node) => node.textContent).join(' ').replace(/\s+/g, ' '),
    )
    expect(lines).toEqual(['Wardbound Beast 22 d20 20 + 2', 'Arden Vale 19 d20 18 + 1'])
  })
})

describe('RollCard playing', () => {
  it('cycles the d20 for 700 ms, lands on the server face, settles the total, then the outcome and the damage', () => {
    vi.useFakeTimers({ now: NOW })
    const random = vi.spyOn(Math, 'random').mockReturnValue(0.5)
    const { container } = render(<RollCard roll={pounce} playback={{ startAt: NOW, instant: false }} />)

    expect(card(container)).toHaveAttribute('data-phase', 'cycle')
    expect(text(container, '.roll-card-total')).toBe('11')
    expect(container.querySelector('.roll-card-outcome')).toBeNull()
    advance(D20_CYCLE_MS)
    expect(text(container, '.roll-card-total')).toBe('14')
    expect(container.querySelector('.roll-card-outcome')).toBeNull()
    advance(SETTLE_MS)
    expect(text(container, '.roll-card-total')).toBe('19')
    expect(text(container, '.roll-card-outcome')).toBe('hit vs AC 17')
    expect(text(container, '.roll-card-dice-total')).toBe('…')
    advance(DICE_CYCLE_MS)
    expect(card(container)).toHaveAttribute('data-phase', 'done')
    expect(text(container, '.roll-card-dice')).toBe('7 piercing 1d8 4 + 3')
    random.mockRestore()
  })

  it('never lands on anything but the record, whatever faces flickered past', () => {
    vi.useFakeTimers({ now: NOW })
    vi.spyOn(Math, 'random').mockReturnValue(0.999)
    const onPlayed = vi.fn()
    const { container } = render(<RollCard roll={swing} playback={{ startAt: NOW, instant: false }} onPlayed={onPlayed} />)
    advance(D20_CYCLE_MS + SETTLE_MS + DICE_CYCLE_MS)
    expect(text(container, '.roll-card-total')).toBe('28')
    expect(text(container, '.roll-card-dice')).toContain('21 slashing 2d8 6 5')
    expect(card(container)).toHaveAttribute('data-live')
    expect(onPlayed).toHaveBeenCalledTimes(1)
  })

  it('plays the cards of one turn one after another, and lands any not started 2.5 s after they arrived', () => {
    vi.useFakeTimers({ now: NOW })
    const cards = [pounce, lure, deathSave, swing].map((record, index) => ({ ...record, id: `t30-${index + 1}` }))
    playRolls(30, cards)

    const first = rollPlayback('t30-1')
    const second = rollPlayback('t30-2')
    const fourth = rollPlayback('t30-4')
    expect(first).toEqual({ startAt: NOW, instant: false })
    expect(second).toEqual({ startAt: NOW + D20_CYCLE_MS + SETTLE_MS + DICE_CYCLE_MS + CARD_GAP_MS, instant: false })
    expect(fourth).toEqual({ startAt: NOW + PLAY_LIMIT_MS, instant: true })

    const { container } = render(<RollCard roll={cards[3]} playback={fourth} />)
    expect(card(container)).toHaveAttribute('data-phase', 'waiting')
    advance(PLAY_LIMIT_MS)
    expect(card(container)).toHaveAttribute('data-phase', 'done')
    expect(card(container)).not.toHaveAttribute('data-live')
    expect(text(container, '.roll-card-total')).toBe('28')
  })

  it('shows a card read from history landed at once', () => {
    const { container } = render(<RollCard roll={pounce} playback={null} />)
    expect(card(container)).toHaveAttribute('data-phase', 'done')
    expect(card(container)).not.toHaveAttribute('data-live')
  })
})

describe('RollCard sounds', () => {
  function listen() {
    const playCue = vi.fn()
    setStoryAudio({ playCue } as unknown as StoryAudio)
    return playCue
  }

  afterEach(() => setStoryAudio(null))

  it('ticks on each flicker step, then sounds the natural 20 once when the card lands', () => {
    vi.useFakeTimers({ now: NOW })
    playRolls(18, [swing], NOW)
    const playCue = listen()
    render(<RollCard roll={swing} playback={rollPlayback(swing.id)} />)

    advance(D20_CYCLE_MS)
    const ticks = playCue.mock.calls.filter(([cue]) => cue === 'dice_tick').length
    // The d20 flickers faster than a step every 150 ms, slowing as it nears landing.
    expect(ticks).toBeGreaterThan(D20_CYCLE_MS / 150)
    expect(playCue).not.toHaveBeenCalledWith('nat20')

    advance(SETTLE_MS + DICE_CYCLE_MS + 50)
    expect(playCue.mock.calls.filter(([cue]) => cue === 'nat20')).toHaveLength(1)
    const afterLanding = playCue.mock.calls.length
    advance(500)
    expect(playCue).toHaveBeenCalledTimes(afterLanding)
    expect(playCue).not.toHaveBeenCalledWith('nat1')
  })

  it('sounds a natural 1 when its card lands, and nothing for a card read from history', () => {
    vi.useFakeTimers({ now: NOW })
    playRolls(17, [lure], NOW)
    const playCue = listen()
    render(<RollCard roll={lure} playback={rollPlayback(lure.id)} />)
    advance(D20_CYCLE_MS + SETTLE_MS + 50)
    expect(playCue).toHaveBeenCalledWith('nat1')
    cleanup()

    const quiet = listen()
    render(<RollCard roll={lure} />)
    advance(D20_CYCLE_MS + SETTLE_MS + 50)
    expect(quiet).not.toHaveBeenCalled()
  })
})
