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
