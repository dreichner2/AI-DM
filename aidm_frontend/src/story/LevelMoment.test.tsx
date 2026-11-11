// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest'
import { cleanup, render, screen, within } from '@testing-library/react'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import type { SessionBoardProps } from '../SessionBoard'
import type { JsonRecord, TimelineEntry } from '../types'
import { makeBoardFixture } from './preview/fixtures'
import { StoryFeed } from './StoryFeed'

const FULL_LEVEL = {
  hero: 'Seren Ashgrove',
  level: 5,
  maxHp: { from: 24, to: 31 },
  proficiencyBonus: { from: 2, to: 3 },
  features: [
    { name: 'Destroy Undead', text: 'Turned undead of low challenge are destroyed outright.' },
    { name: 'Spirit Ward' },
  ],
  spellSlots: [
    { level: 2, from: 3, to: 3 },
    { level: 3, from: 0, to: 2 },
  ],
  spells: ['Spirit Guardians', 'Revivify', 'Daylight', 'Mass Healing Word', 'Beacon of Hope', 'Dispel Magic', 'Remove Curse', 'Animate Dead'],
  more: 2,
}

function dmEntry(beats: JsonRecord[]): TimelineEntry {
  return {
    id: 'log-9',
    role: 'dm',
    speaker: 'DM',
    text: 'The last lantern gutters out, and the road is quiet.',
    timestamp: null,
    metadata: { turn_id: 9, storyBeats: beats },
  }
}

function board(entries: TimelineEntry[]): SessionBoardProps {
  return {
    ...makeBoardFixture('exploration'),
    sessionLogHasMore: false,
    turnRows: entries,
    currentResponseEntry: null,
    sendPending: false,
    streamingTurnActive: false,
  }
}

function renderFeed(beats: JsonRecord[]) {
  return render(<StoryFeed board={board([dmEntry(beats)])} textSize="default" onExitToTitle={vi.fn()} onOpenBeatTarget={vi.fn()} />)
}

function lines(moment: HTMLElement) {
  return [...moment.querySelectorAll('.story-level-line')].map((line) => line.textContent)
}

beforeAll(() => {
  // jsdom has no scrolling; the feed's reading behaviour is exercised in the preview.
  Element.prototype.scrollTo = () => {}
})

afterEach(() => {
  cleanup()
})

describe('LevelMoment', () => {
  it('shows a level reached as a moment between hairlines: the level, the hero, then what they gained in order', async () => {
    const { container } = renderFeed([
      { kind: 'xp', text: 'Seren Ashgrove +300 XP' },
      { kind: 'level', text: 'Seren Ashgrove reached level 5', level: FULL_LEVEL },
    ])

    const hero = await screen.findByText('Seren Ashgrove')
    const moment = hero.closest('li') as HTMLElement
    expect(moment).toHaveClass('story-level-moment')
    expect(within(moment).getByText('Level 5')).toHaveClass('story-label', 'story-level-label')
    expect(hero).toHaveClass('story-level-hero')
    expect(lines(moment)).toEqual([
      'Max HP 24 → 31',
      'Proficiency bonus +2 → +3',
      'Destroy Undead — Turned undead of low challenge are destroyed outright.',
      'Spirit Ward',
      'Spell slots — 2nd level 3 → 3, 3rd level 0 → 2',
      'New spells — Spirit Guardians, Revivify, Daylight, Mass Healing Word, Beacon of Hope, Dispel Magic, Remove Curse, Animate Dead and 2 more',
    ])
    expect(moment.querySelectorAll('hr.story-rule')).toHaveLength(2)
    expect(moment.firstElementChild).toHaveClass('story-rule')
    expect(moment.lastElementChild).toHaveClass('story-rule')
    // No box, no icons, no button: the moment is part of the log, read like the rest of it.
    expect(within(moment).queryByRole('button')).toBeNull()
    expect(moment.querySelector('svg')).toBeNull()
    // The turn's other beats stay chips beside it; the old one-line level chip is gone.
    expect(container.querySelector('.story-chip[data-kind="xp"]')).toHaveTextContent('Seren Ashgrove +300 XP')
    expect(container.querySelector('.story-chip[data-kind="level"]')).toBeNull()
  })

  it('shows only the maximum hit points when that is all the level gave', async () => {
    renderFeed([{ kind: 'level', text: 'Level 4 reached', level: { hero: 'Arden Vale', level: 4, maxHp: { from: 28, to: 36 } } }])

    const moment = (await screen.findByText('Arden Vale')).closest('li') as HTMLElement
    expect(within(moment).getByText('Level 4')).toBeInTheDocument()
    expect(lines(moment)).toEqual(['Max HP 28 → 36'])
  })

  it('keeps the one-line level beat for a level saved before beats carried what was gained', async () => {
    const { container } = renderFeed([
      { kind: 'level', text: 'Arden Vale reached level 2' },
      { kind: 'level', text: 'Seren Ashgrove reached level 3', level: { hero: 'Seren Ashgrove', level: 3, maxHp: { from: 17, to: 24 } } },
    ])

    // Once the moment's code has arrived (Seren's moment shows), the old beat is still its one chip.
    await screen.findByText('Seren Ashgrove')
    expect(container.querySelectorAll('.story-level-moment')).toHaveLength(1)
    expect(container.querySelector('.story-chip[data-kind="level"]')).toHaveTextContent('Arden Vale reached level 2')
  })
})
