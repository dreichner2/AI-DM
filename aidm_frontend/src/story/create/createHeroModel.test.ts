import { describe, expect, it } from 'vitest'
import {
  ANCESTRIES,
  CORE_CLASSES,
  STANDARD_ARRAY,
  ancestryById,
  ancestryFromSelection,
  classSkillOptions,
  createPayload,
  emptyDraft,
  heroBackground,
  heroNumbers,
  heroSentence,
  pointBuyValid,
  pointsRemaining,
  proficientSkills,
  recommendedScores,
  stepComplete,
  stepHint,
  stepScore,
  suggestName,
  toggleExpertise,
  toggleSkill,
  withAbilityMode,
  withAncestry,
  withBackground,
  withCalling,
  type HeroDraft,
  type SkillId,
} from './createHeroModel'

function hero(calling: string, ancestry = 'human', name = 'Test Hero'): HeroDraft {
  return withCalling(withAncestry({ ...emptyDraft(), name }, ancestryById(ancestry)!), calling)
}

function withSkills(draft: HeroDraft, skills: SkillId[]) {
  return skills.reduce(toggleSkill, draft)
}

describe('createHeroModel', () => {
  it('offers the nine recommended ancestries with catalog ids and at most three rules-backed traits', () => {
    expect(ANCESTRIES.map((entry) => entry.name)).toEqual([
      'Human', 'Elf', 'Dwarf', 'Halfling', 'Half-Elf', 'Half-Orc', 'Gnome', 'Dragonborn', 'Tiefling',
    ])
    expect(ancestryById('half-elf')?.selection).toEqual({ raceId: 'elf', raceName: 'Elf', source: 'curated', selectedOptions: {} })
    expect(ancestryById('half-orc')?.selection.raceId).toBe('orc')
    expect(ancestryById('gnome')?.skills).toEqual(['arcana', 'investigation'])
    expect(ANCESTRIES.every((entry) => entry.traits.length >= 1 && entry.traits.length <= 3)).toBe(true)
    expect(ancestryById('dwarf')?.portraits).toEqual({
      male: '/profile-icons/dwarf_male.png',
      female: '/profile-icons/dwarf_female.png',
    })
  })

  it('maps a full-selector pick back to a recommended ancestry or builds one from the catalog', () => {
    const elf = ancestryFromSelection({ raceId: 'elf', raceName: 'Elf', source: 'curated' }, null)
    const tabaxi = ancestryFromSelection(
      { raceId: 'tabaxi', raceName: 'Tabaxi', source: 'curated', selectedOptions: {} },
      { key: 'tabaxi', name: 'Tabaxi', tagline: 'Curious cats.', shortDescription: 'Quick feline wanderers.', languages: ['Common', 'Feline'] },
    )

    expect(elf).toBe(ancestryById('elf'))
    expect(tabaxi).toMatchObject({
      id: 'catalog:tabaxi',
      name: 'Tabaxi',
      summary: 'Quick feline wanderers. Curious cats.',
      traits: ['Speaks Common and Feline'],
      skills: [],
    })
    expect(tabaxi.portraits?.female).toContain('Tabaxi')
  })

  it('suggests names that fit the ancestry and portrait, cycling on each press', () => {
    const dwarf = ancestryById('dwarf')
    const first = suggestName(dwarf, 'female', '')
    expect(dwarf?.names.female).toContain(first)
    expect(suggestName(dwarf, 'female', first)).not.toBe(first)
    expect(ancestryById('human')?.names.male).toContain(suggestName(null, 'male', ''))
  })

  it('lays the standard array out by each core class priority', () => {
    expect(CORE_CLASSES.map((entry) => entry.name).sort()).toEqual([
      'Barbarian', 'Bard', 'Cleric', 'Druid', 'Fighter', 'Monk', 'Paladin', 'Ranger', 'Rogue', 'Sorcerer', 'Warlock', 'Wizard',
    ])
    for (const entry of CORE_CLASSES) {
      const scores = recommendedScores(entry.name)
      expect(entry.priority.map((ability) => scores[ability])).toEqual([...STANDARD_ARRAY])
      expect(pointsRemaining(scores)).toBe(0)
    }
    expect(recommendedScores('Fighter - Champion')).toEqual({
      strength: 15, constitution: 14, dexterity: 13, wisdom: 12, charisma: 10, intelligence: 8,
    })
    expect(recommendedScores('Wizard').intelligence).toBe(15)
    expect(recommendedScores('Rogue').dexterity).toBe(15)
    expect(Object.values(recommendedScores('Dream Knight')).sort((a, b) => a - b)).toEqual([8, 10, 12, 13, 14, 15])
  })

  it('keeps point buy within 8 to 15 and flags spending over 27 points', () => {
    let draft = withAbilityMode(hero('Fighter'), 'adjust')
    expect(draft.scores).toEqual(recommendedScores('Fighter'))
    expect(stepScore(draft, 'strength', 1)).toBe(draft)
    expect(stepScore(draft, 'intelligence', -1)).toBe(draft)

    draft = stepScore(draft, 'intelligence', 1)
    expect(pointsRemaining(draft.scores)).toBe(-1)
    expect(pointBuyValid(draft.scores)).toBe(false)
    draft = withSkills(withBackground(draft, 'soldier'), ['perception', 'survival'])
    expect(stepComplete(draft, 'shape')).toBe(false)
    expect(stepHint(draft, 'shape')).toBe('Spend no more than 27 points to continue.')

    draft = stepScore(draft, 'charisma', -1)
    expect(pointBuyValid(draft.scores)).toBe(true)
    expect(stepComplete(draft, 'shape')).toBe(true)
  })

  it('derives HP, AC with the starting kit, the main weapon attack and the spell save DC', () => {
    expect(heroNumbers(hero('Fighter'))).toEqual({
      hp: 12, armorClass: 18, attack: { weapon: 'Longsword', bonus: 4 }, spellSaveDc: null, proficiencyBonus: 2,
    })
    expect(heroNumbers({ ...hero('Fighter'), fightingStyle: 'defense' }).armorClass).toBe(19)
    // A paladin's style waits for level 2.
    expect(heroNumbers({ ...hero('Paladin'), fightingStyle: 'defense' }).armorClass).toBe(18)
    expect(heroNumbers(hero('Barbarian'))).toMatchObject({ hp: 14, armorClass: 13, attack: { weapon: 'Greataxe', bonus: 4 } })
    expect(heroNumbers(hero('Monk'))).toMatchObject({ hp: 9, armorClass: 14, attack: { weapon: 'Quarterstaff', bonus: 4 } })
    expect(heroNumbers(hero('Cleric'))).toMatchObject({ hp: 10, armorClass: 17, attack: { weapon: 'Mace', bonus: 3 }, spellSaveDc: 12 })
    expect(heroNumbers(hero('Wizard'))).toMatchObject({ hp: 8, armorClass: 11, attack: { weapon: 'Quarterstaff', bonus: 1 }, spellSaveDc: 12 })
    expect(heroNumbers(hero('Ranger'))).toMatchObject({ hp: 11, armorClass: 13, attack: { weapon: 'Longbow', bonus: 4 } })
    expect(heroNumbers(hero('Gunslinger'))).toEqual({
      hp: 12, armorClass: null, attack: null, spellSaveDc: null, proficiencyBonus: 2,
    })
  })

  it('chooses class skills from the class list minus the background, exactly the class count', () => {
    let fighter = withBackground(hero('Fighter'), 'soldier')
    expect(classSkillOptions(fighter)).toEqual(['acrobatics', 'animal_handling', 'history', 'insight', 'perception', 'survival'])
    fighter = withSkills(fighter, ['perception', 'athletics', 'history', 'insight'])
    expect(fighter.skills).toEqual(['perception', 'history'])
    expect(stepComplete(fighter, 'shape')).toBe(true)

    // A background that already grants a chosen skill takes it back.
    fighter = withBackground(fighter, 'sage')
    expect(fighter.skills).toEqual(['perception'])
    expect(stepHint(fighter, 'shape')).toBe('Choose 2 class skills to continue.')

    expect(CORE_CLASSES.map((entry) => [entry.id, entry.skillCount])).toEqual(
      expect.arrayContaining([['fighter', 2], ['rogue', 4], ['bard', 3], ['ranger', 3], ['wizard', 2]]),
    )
    expect(classSkillOptions(withBackground(hero('Bard'), 'sage'))).toHaveLength(16)
    expect(classSkillOptions(hero('Dream Knight'))).toEqual([])
    expect(stepComplete(hero('Dream Knight'), 'shape')).toBe(true)
  })

  it('suggests a background for the calling until the player picks one', () => {
    expect(heroBackground(hero('Rogue'))?.id).toBe('criminal')
    expect(heroBackground(hero('Wizard'))?.id).toBe('sage')
    expect(heroBackground(withBackground(hero('Wizard'), 'soldier'))?.id).toBe('soldier')
  })

  it('lets a Rogue mark two proficient skills for expertise and forgets choices on a class change', () => {
    let rogue = withSkills(hero('Rogue', 'elf'), ['acrobatics', 'insight', 'investigation', 'sleight_of_hand'])
    expect(proficientSkills(rogue)).toEqual(['acrobatics', 'deception', 'insight', 'investigation', 'perception', 'sleight_of_hand', 'stealth'])
    expect(stepHint(rogue, 'shape')).toBe('Mark two skills for expertise to continue.')

    rogue = ['perception', 'arcana', 'stealth', 'insight'].reduce((draft, skill) => toggleExpertise(draft, skill as SkillId), rogue)
    expect(rogue.expertise).toEqual(['perception', 'stealth'])
    expect(stepComplete(rogue, 'shape')).toBe(true)

    // Dropping a class skill drops the expertise that depended on it.
    rogue = toggleExpertise(toggleExpertise(rogue, 'perception'), 'insight')
    rogue = toggleSkill(rogue, 'insight')
    expect(rogue.expertise).toEqual(['stealth'])

    const fighter = withCalling(rogue, 'Fighter')
    expect([fighter.skills, fighter.expertise]).toEqual([[], []])
  })

  it('builds the creation payload and the review sentence from the choices', () => {
    let draft = withSkills(withBackground(hero('Fighter', 'half-orc', '  Grosh  '), 'soldier'), ['perception', 'survival'])
    draft = { ...draft, sex: 'female', fightingStyle: 'great_weapon' }

    expect(createPayload(draft)).toEqual({
      character_name: 'Grosh',
      race: 'Orc',
      race_selection: { raceId: 'orc', raceName: 'Orc', source: 'curated', selectedOptions: {} },
      sex: 'female',
      char_class: 'Fighter',
      level: 1,
      background: 'soldier',
      stats: expect.objectContaining({
        ability_scores: { strength: 15, constitution: 14, dexterity: 13, wisdom: 12, charisma: 10, intelligence: 8 },
        point_buy: { budget: 27, spent: 27, remaining: 0 },
      }),
      skill_choices: ['perception', 'survival'],
      fighting_style: 'great_weapon',
    })
    expect(heroSentence(draft)).toBe(
      'A soldier half-orc fighter who marched with an army and still wakes at the sound of distant drums.',
    )

    const acolyte = withBackground(hero('Cleric', 'dwarf'), 'acolyte')
    expect(heroSentence(acolyte)).toMatch(/^An acolyte dwarf cleric who served a temple/)

    const narrated = withBackground(hero('Dream Knight - Mirror Oath', 'elf'), 'sage')
    const payload = createPayload(narrated)
    expect(payload.char_class).toBe('Dream Knight - Mirror Oath')
    expect(payload).not.toHaveProperty('skill_choices')
    expect(payload).not.toHaveProperty('fighting_style')
    expect(heroSentence(narrated)).toMatch(/^A sage elf dream knight \(mirror oath\) who/)

    expect(() => createPayload(hero('Rogue'))).toThrow('The hero is not finished yet.')
  })

  it('completes each step only when its choices are made', () => {
    expect(stepComplete(emptyDraft(), 'who')).toBe(false)
    expect(stepHint(emptyDraft(), 'who')).toBe('Choose an ancestry to continue.')
    const named = withAncestry(emptyDraft(), ANCESTRIES[0])
    expect(stepHint(named, 'who')).toBe('Name your hero to continue.')
    expect(stepComplete({ ...named, name: 'Ada' }, 'who')).toBe(true)
    expect(stepComplete({ ...named, name: 'Ada' }, 'calling')).toBe(false)
    expect(stepComplete(hero('Wizard'), 'review')).toBe(false)
  })
})
