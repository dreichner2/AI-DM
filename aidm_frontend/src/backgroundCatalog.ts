export type PlayableBackground = {
  id: string
  name: string
  skillProficiencies: string[]
  toolProficiencies: string[]
  languages: string[]
}

export const PLAYABLE_BACKGROUNDS: PlayableBackground[] = [
  {
    id: 'acolyte',
    name: 'Acolyte',
    skillProficiencies: ['insight', 'religion'],
    toolProficiencies: ['herbalism_kit'],
    languages: ['Celestial', 'Infernal'],
  },
  {
    id: 'criminal',
    name: 'Criminal',
    skillProficiencies: ['deception', 'stealth'],
    toolProficiencies: ['thieves_tools', 'gaming_set'],
    languages: ["Thieves' Cant"],
  },
  {
    id: 'folk_hero',
    name: 'Folk Hero',
    skillProficiencies: ['animal_handling', 'survival'],
    toolProficiencies: ['artisan_tools', 'land_vehicles'],
    languages: [],
  },
  {
    id: 'guild_artisan',
    name: 'Guild Artisan',
    skillProficiencies: ['insight', 'persuasion'],
    toolProficiencies: ['artisan_tools'],
    languages: ['Dwarvish'],
  },
  {
    id: 'sage',
    name: 'Sage',
    skillProficiencies: ['arcana', 'history'],
    toolProficiencies: ['calligraphers_supplies'],
    languages: ['Draconic', 'Elvish'],
  },
  {
    id: 'soldier',
    name: 'Soldier',
    skillProficiencies: ['athletics', 'intimidation'],
    toolProficiencies: ['gaming_set', 'land_vehicles'],
    languages: [],
  },
]

function key(value: unknown) {
  return String(value ?? '')
    .trim()
    .toLowerCase()
    .replace(/['’]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

export function backgroundFromValue(value: unknown) {
  const lookup = key(value)
  return (
    PLAYABLE_BACKGROUNDS.find(
      (background) => lookup === key(background.id) || lookup === key(background.name),
    ) ?? null
  )
}

export function proficiencyLabel(value: string) {
  return value
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
}
