import { describe, expect, it } from 'vitest'
import { PLAYABLE_RACES, filterPlayableRaces, playableRaceFromValue } from './raceCatalog'

describe('race catalog', () => {
  it('uses the playable profile-icon race list for matching saved values', () => {
    expect(playableRaceFromValue('Half-Elf')?.name).toBe('Elf')
    expect(playableRaceFromValue('robot warrior')?.name).toBe('Warforged')
    expect(playableRaceFromValue('custom shadow person')).toBeNull()
  })

  it('searches race names, aliases, traits, tags, and descriptions', () => {
    const dragonResults = filterPlayableRaces({ query: 'dragon', category: 'All' }).map((race) => race.name)
    expect(dragonResults).toEqual(expect.arrayContaining(['Dragonborn', 'Kobold', 'Lizardfolk']))

    const flightResults = filterPlayableRaces({ query: 'fly', category: 'All' }).map((race) => race.name)
    expect(flightResults).toEqual(expect.arrayContaining(['Aarakocra', 'Fairy']))

    const traitResults = filterPlayableRaces({ query: 'breath weapon', category: 'All' }).map((race) => race.name)
    expect(traitResults).toContain('Dragonborn')

    const languageResults = filterPlayableRaces({ query: 'sylvan', category: 'All' }).map((race) => race.name)
    expect(languageResults).toEqual(expect.arrayContaining(['Fairy', 'Satyr']))
  })

  it('filters races by category', () => {
    const flyingResults = filterPlayableRaces({ query: '', category: 'Flying' }).map((race) => race.name)
    expect(flyingResults).toEqual(expect.arrayContaining(['Aarakocra', 'Fairy']))
    expect(flyingResults).not.toContain('Dwarf')

    const constructResults = filterPlayableRaces({ query: '', category: 'Construct' }).map((race) => race.name)
    expect(constructResults).toEqual(['Warforged'])

    const bulkyResults = filterPlayableRaces({ query: '', category: 'Tall/Bulky' }).map((race) => race.name)
    expect(bulkyResults).toEqual(expect.arrayContaining(['Bugbear', 'Goliath', 'Minotaur']))
    expect(bulkyResults).not.toContain('Warforged')
  })

  it('keeps relationship dynamics mostly tied to catalog races', () => {
    const catalogNames = PLAYABLE_RACES.map((race) => race.name.toLowerCase())
    const catalogReferenceCount = (values: string[]) =>
      values.filter((value) => catalogNames.some((name) => value.toLowerCase().includes(name))).length

    for (const race of PLAYABLE_RACES) {
      expect(race.friendlyWith, `${race.name} friendlyWith`).toHaveLength(5)
      expect(race.waryOf, `${race.name} waryOf`).toHaveLength(5)
      expect(catalogReferenceCount(race.friendlyWith), `${race.name} friendlyWith`).toBeGreaterThanOrEqual(2)
      expect(catalogReferenceCount(race.waryOf), `${race.name} waryOf`).toBeGreaterThanOrEqual(2)
    }
  })
})
