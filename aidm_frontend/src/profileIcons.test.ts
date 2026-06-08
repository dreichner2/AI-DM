import { describe, expect, it } from 'vitest'
import { profileIconRaceForCharacter, profileIconSrcForCharacter } from './profileIcons'

describe('profile icon race matching', () => {
  it('maps common race descriptions to the closest available DnD portrait', () => {
    expect(profileIconRaceForCharacter('demon')).toBe('tiefling')
    expect(profileIconRaceForCharacter('Half Demon/ Half Human')).toBe('tiefling')
    expect(profileIconRaceForCharacter('half elf')).toBe('elf')
    expect(profileIconRaceForCharacter('bunny person')).toBe('harengon')
    expect(profileIconRaceForCharacter('robot warrior')).toBe('warforged')
  })

  it('selects the sex-specific portrait when one is available', () => {
    expect(profileIconSrcForCharacter({ race: 'demon', sex: 'female', seed: 'Mira' })).toBe(
      '/profile-icons/tiefling_female.png',
    )
    expect(profileIconSrcForCharacter({ race: 'dwarf', sex: 'male', seed: 'Borin' })).toBe(
      '/profile-icons/dwarf_male.png',
    )
    expect(profileIconSrcForCharacter({ race: 'demon', sex: '', seed: 'Mira' })).toBe(
      '/profile-icons/tiefling_male.png',
    )
  })
})
