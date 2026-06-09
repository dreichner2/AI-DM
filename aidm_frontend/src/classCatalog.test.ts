import { describe, expect, it } from 'vitest'
import {
  PLAYABLE_CLASSES,
  classSelectionFromValue,
  filterPlayableClasses,
} from './classCatalog'

describe('class catalog', () => {
  it('keeps the class and subclass catalog broad enough for character creation', () => {
    const subclassCount = PLAYABLE_CLASSES.reduce((total, classEntry) => total + classEntry.subclasses.length, 0)

    expect(PLAYABLE_CLASSES.length).toBeGreaterThanOrEqual(45)
    expect(subclassCount).toBeGreaterThanOrEqual(350)
  })

  it('matches saved class and subclass values', () => {
    expect(classSelectionFromValue('Wizard')?.classEntry.name).toBe('Wizard')
    const chronomancer = classSelectionFromValue('Wizard - Chronomancer')
    expect(chronomancer?.classEntry.name).toBe('Wizard')
    expect(chronomancer?.subclass?.name).toBe('Chronomancer')

    const hexblade = classSelectionFromValue('hexblade')
    expect(hexblade?.classEntry.name).toBe('Warlock')
    expect(hexblade?.subclass?.name).toBe('Hexblade')
  })

  it('searches class names, subclasses, roles, tags, and aliases', () => {
    const dragonResults = filterPlayableClasses({ query: 'dragon', category: 'All' }).map((entry) => entry.name)
    expect(dragonResults).toEqual(expect.arrayContaining(['Ranger', 'Sorcerer', 'Dragon Disciple']))

    const gunslingerResults = filterPlayableClasses({ query: 'pistolero', category: 'All' }).map(
      (entry) => entry.name,
    )
    expect(gunslingerResults).toContain('Gunslinger')

    const scienceFantasyResults = filterPlayableClasses({ query: '', category: 'Science Fantasy' }).map(
      (entry) => entry.name,
    )
    expect(scienceFantasyResults).toEqual(expect.arrayContaining(['Technomancer', 'Engineer', 'Operative']))
  })
})
