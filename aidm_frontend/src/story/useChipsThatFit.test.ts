/// <reference types="node" />
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { chipsThatFit } from './useChipsThatFit'

describe('chipsThatFit', () => {
  it('shows every chip when they fit, with More… only when the list has one', () => {
    expect(chipsThatFit([100, 120], 40, 10, 230, false)).toEqual({ shown: [true, true], more: false })
    expect(chipsThatFit([100, 120], 40, 10, 280, true)).toEqual({ shown: [true, true], more: true })
  })

  it('moves the chips that do not fit behind More…, keeping later ones that still fit', () => {
    // More… (40) + 100 + 80 and two gaps fit 300; the 300 px chip does not.
    expect(chipsThatFit([100, 300, 80], 40, 10, 300, false)).toEqual({ shown: [true, false, true], more: true })
    expect(chipsThatFit([100, 120], 40, 10, 60, false)).toEqual({ shown: [false, false], more: true })
  })

  it('shows everything in a row that has not been laid out', () => {
    expect(chipsThatFit([100, 300], 40, 10, 0, true)).toEqual({ shown: [true, true], more: true })
  })
})

describe('chipsThatFit on two lines', () => {
  it('wraps onto a second line before anything waits behind More…', () => {
    // 144 + 144 fill the first line of 300; 160 starts the second.
    expect(chipsThatFit([144, 144, 160], 40, 0, 300, false, 2)).toEqual({ shown: [true, true, true], more: false })
    expect(chipsThatFit([144, 144, 160], 40, 0, 300, false, 1)).toEqual({ shown: [true, false, false], more: true })
  })

  it('keeps room for More… at the end of the last line, and never a third line', () => {
    // Line 1: 140 + 10 + 140; line 2: More… (60) + 10 + 200 leaves no room for the 120 px chip.
    expect(chipsThatFit([140, 140, 200, 120], 60, 10, 300, false, 2)).toEqual({
      shown: [true, true, true, false],
      more: true,
    })
    // A chip too wide for any line waits; a later one still takes the first line's room.
    expect(chipsThatFit([140, 400, 100, 280], 60, 10, 300, false, 2)).toEqual({
      shown: [true, false, true, false],
      more: true,
    })
  })

  it('gives narrow screens two lines of suggestions and keeps desktop to one, but lets a conversation wrap', () => {
    const dockCss = readFileSync(resolve(__dirname, '../styles/story-dock.css'), 'utf8')
    const narrow = dockCss.slice(dockCss.indexOf('@media (max-width: 899px)'))
    const narrowBlock = narrow.slice(0, narrow.indexOf('\n}\n'))
    expect(narrowBlock).toMatch(/\.story-suggestions \{[^}]*--story-chip-rows: 2;[^}]*flex-wrap: wrap;/)
    const talking = /\.story-suggestions\[data-talking\] \{[^}]*--story-chip-rows: 6;[^}]*flex-wrap: wrap;[^}]*\}/
    expect(dockCss).toMatch(talking)
    const outsideMedia = dockCss.replace(/@media[^{]+\{[\s\S]*?\n\}\n/g, '').replace(talking, '')
    expect(outsideMedia).not.toContain('--story-chip-rows')
  })
})
