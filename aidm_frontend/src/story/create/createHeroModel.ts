import { backgroundFromValue } from '../../backgroundCatalog'
import {
  POINT_BUY_ABILITIES,
  POINT_BUY_BUDGET,
  hitDieForClass,
  pointBuySpent,
  pointBuyStatsPayload,
  type PointBuyAbilityKey,
  type PointBuyScores,
} from '../../characterStats'
import { profileIconSrcForCharacter, type SexKey } from '../../profileIcons'
import type { CharacterRaceSelection } from '../../types'

/*
 * The rules behind the four-step hero creator: what each ancestry and core class
 * gives a level-1 character in this game's rules engine, the recommended ability
 * spread, the live derived numbers and the creation payload. Everything here is
 * pure so it can be tested without rendering.
 *
 * The class data mirrors the server: features from aidm_server/class_features.py,
 * class skills from character_state.CLASS_SKILL_CHOICES, spell lists from
 * spellbook.class_spells_for_level and starting kits from starting_inventory.py.
 */

export const CREATE_STEPS = ['who', 'calling', 'shape', 'review'] as const
export type CreateStep = (typeof CREATE_STEPS)[number]
export const STEP_NAMES: Record<CreateStep, string> = {
  who: 'Who',
  calling: 'Calling',
  shape: 'Shape',
  review: 'Review',
}

export type AbilityKey = PointBuyAbilityKey
export const ABILITY_LABELS = Object.fromEntries(
  POINT_BUY_ABILITIES.map((ability) => [ability.key, ability.label]),
) as Record<AbilityKey, string>

// ---- skills ----------------------------------------------------------------

const SKILLS = {
  acrobatics: { name: 'Acrobatics', ability: 'dexterity' },
  animal_handling: { name: 'Animal Handling', ability: 'wisdom' },
  arcana: { name: 'Arcana', ability: 'intelligence' },
  athletics: { name: 'Athletics', ability: 'strength' },
  deception: { name: 'Deception', ability: 'charisma' },
  history: { name: 'History', ability: 'intelligence' },
  insight: { name: 'Insight', ability: 'wisdom' },
  intimidation: { name: 'Intimidation', ability: 'charisma' },
  investigation: { name: 'Investigation', ability: 'intelligence' },
  medicine: { name: 'Medicine', ability: 'wisdom' },
  nature: { name: 'Nature', ability: 'intelligence' },
  perception: { name: 'Perception', ability: 'wisdom' },
  performance: { name: 'Performance', ability: 'charisma' },
  persuasion: { name: 'Persuasion', ability: 'charisma' },
  religion: { name: 'Religion', ability: 'intelligence' },
  sleight_of_hand: { name: 'Sleight of Hand', ability: 'dexterity' },
  stealth: { name: 'Stealth', ability: 'dexterity' },
  survival: { name: 'Survival', ability: 'wisdom' },
} as const satisfies Record<string, { name: string; ability: AbilityKey }>

export type SkillId = keyof typeof SKILLS
const SKILL_IDS = Object.keys(SKILLS) as SkillId[]

export function skillName(skill: string) {
  return skill in SKILLS ? SKILLS[skill as SkillId].name : skill.replace(/_/g, ' ')
}

// ---- ancestries ------------------------------------------------------------

export type Ancestry = {
  id: string
  name: string
  /** The race the server stores (`race_selection`); Half-Elf and Half-Orc use the catalog's elf and orc. */
  selection: CharacterRaceSelection
  summary: string
  /** Traits the rules engine applies to play (at most three). */
  traits: string[]
  /** Skill proficiencies the ancestry grants. */
  skills: SkillId[]
  languages: string[]
  /** Portrait art per sex, or null when the ancestry has none. */
  portraits: Record<SexKey, string> | null
  names: Record<SexKey, string[]>
}

function curated(raceId: string, raceName: string): CharacterRaceSelection {
  return { raceId, raceName, source: 'curated', selectedOptions: {} }
}

function portraitsFor(race: string): Record<SexKey, string> | null {
  const male = profileIconSrcForCharacter({ race, sex: 'male' })
  const female = profileIconSrcForCharacter({ race, sex: 'female' })
  return male && female ? { male, female } : null
}

function speaks(languages: string[]) {
  return `Speaks ${languages.join(' and ')}`
}

function ancestry(entry: Omit<Ancestry, 'portraits' | 'traits'> & { traits?: string[] }): Ancestry {
  return {
    ...entry,
    traits: [...(entry.traits ?? []), speaks(entry.languages)],
    portraits: portraitsFor(entry.selection.raceId),
  }
}

/** The nine recommended ancestries, in the order the portrait row shows them. */
export const ANCESTRIES: Ancestry[] = [
  ancestry({
    id: 'human',
    name: 'Human',
    selection: curated('human', 'Human'),
    summary:
      'Humans are the restless, short-lived folk of every road and market town. They fit any calling and any kind of story.',
    skills: [],
    languages: ['Common', 'one regional language'],
    names: {
      male: ['Arden Vale', 'Tomas Reed', 'Edric Hale', 'Corin Ashby'],
      female: ['Mira Holt', 'Elena Marsh', 'Sera Wren', 'Ada Thorne'],
    },
  }),
  ancestry({
    id: 'elf',
    name: 'Elf',
    selection: curated('elf', 'Elf'),
    summary:
      'Elves are graceful and long-lived, shaped by old magic and older memories. They notice what others walk past.',
    traits: ['Keen Senses: proficient in Perception'],
    skills: ['perception'],
    languages: ['Common', 'Elvish'],
    names: {
      male: ['Thalion Duskwind', 'Erevan Moonbrook', 'Aelar Silverleaf', 'Faelan Nightbloom'],
      female: ['Liora Quill', 'Naivara Starwhisper', 'Sariel Brightwater', 'Ilyra Fernsong'],
    },
  }),
  ancestry({
    id: 'dwarf',
    name: 'Dwarf',
    selection: curated('dwarf', 'Dwarf'),
    summary:
      'Dwarves are stout, stubborn and stone-wise, proud of their craft and their kin. They outlast almost anything.',
    traits: ['Stonecunning: proficient in History'],
    skills: ['history'],
    languages: ['Common', 'Dwarvish'],
    names: {
      male: ['Tovan Ember', 'Durgan Stonehelm', 'Harbek Coalbeard', 'Rurik Ironfoot'],
      female: ['Hilde Frostbeard', 'Vistra Anvilmar', 'Eldra Gorunn', 'Brenna Hammerfall'],
    },
  }),
  ancestry({
    id: 'halfling',
    name: 'Halfling',
    selection: curated('halfling', 'Halfling'),
    summary:
      'Halflings are small, warm-hearted and far braver than they look. Luck has a way of finding them.',
    skills: [],
    languages: ['Common', 'Halfling'],
    names: {
      male: ['Pip Tealeaf', 'Milo Brushgather', 'Cade Goodbarrel', 'Wellby Underbough'],
      female: ['Mara Fen', 'Rosie Thorngage', 'Lidda Hilltop', 'Callie Greenbottle'],
    },
  }),
  ancestry({
    id: 'half-elf',
    name: 'Half-Elf',
    selection: curated('elf', 'Elf'),
    summary:
      'Half-elves walk between two peoples and belong fully to neither. At the table they play with elven senses.',
    traits: ['Keen Senses: proficient in Perception'],
    skills: ['perception'],
    languages: ['Common', 'Elvish'],
    names: {
      male: ['Kael Ashgrove', 'Rowan Hale', 'Theren Marsh', 'Lucan Brightmoor'],
      female: ['Isolde Reed', 'Elowen Vale', 'Seren Ashdown', 'Maren Lightfoot'],
    },
  }),
  ancestry({
    id: 'half-orc',
    name: 'Half-Orc',
    selection: curated('orc', 'Orc'),
    summary:
      'Half-orcs carry orcish strength and a hard-won place among humans. They refuse to stay down.',
    skills: [],
    languages: ['Common', 'Orc'],
    names: {
      male: ['Thokk Ironjaw', 'Grumbar Ashborn', 'Dench Holgarr', 'Krusk Redmane'],
      female: ['Shautha Redtusk', 'Ovak Stonefist', 'Baggi Emberhide', 'Yevelda Grimm'],
    },
  }),
  ancestry({
    id: 'gnome',
    name: 'Gnome',
    selection: curated('gnome', 'Gnome'),
    summary:
      'Gnomes are curious tinkerers with quick minds and a love of clever tricks. Every puzzle is an invitation.',
    traits: ['Inventive: proficient in Arcana and Investigation'],
    skills: ['arcana', 'investigation'],
    languages: ['Common', 'Gnomish'],
    names: {
      male: ['Orryn Glim', 'Fonkin Tinkerwick', 'Wrenn Pocketgear', 'Alston Fizzle'],
      female: ['Nissa Sparkwheel', 'Bimpna Quickcog', 'Carlin Murnig', 'Tana Brasslock'],
    },
  }),
  ancestry({
    id: 'dragonborn',
    name: 'Dragonborn',
    selection: curated('dragonborn', 'Dragonborn'),
    summary:
      'Dragonborn carry the pride and fire of dragons in scaled, towering frames. Their honour is their name.',
    skills: [],
    languages: ['Common', 'Draconic'],
    names: {
      male: ['Arjhan Kerrhyl', 'Torinn Yarjeth', 'Medrash Vorn', 'Balasar Kethend'],
      female: ['Sora Delmirev', 'Kava Thurnask', 'Harann Myast', 'Nala Ashscale'],
    },
  }),
  ancestry({
    id: 'tiefling',
    name: 'Tiefling',
    selection: curated('tiefling', 'Tiefling'),
    summary:
      'Tieflings bear an infernal legacy in their horns, tails and ember eyes. Most have learned to meet suspicion head on.',
    skills: [],
    languages: ['Common', 'Infernal'],
    names: {
      male: ['Damakos Ash', 'Mordai Vex', 'Skamos Thorn', 'Kairon Cinder'],
      female: ['Nemeia Grief', 'Orianna Hope', 'Kallista Ember', 'Lerissa Vale'],
    },
  }),
]

export function ancestryById(id: string | null | undefined) {
  return ANCESTRIES.find((entry) => entry.id === id) ?? null
}

type CatalogRace = {
  key: string
  name: string
  tagline: string
  shortDescription: string
  languages: string[]
}

/** An ancestry picked from the full race selector: one of the nine when it is one, else built from the catalog. */
export function ancestryFromSelection(selection: CharacterRaceSelection, catalogRace: CatalogRace | null): Ancestry {
  const recommended = ANCESTRIES.find(
    (entry) => selection.source !== 'custom' && entry.selection.raceId === selection.raceId && entry.name === selection.raceName,
  )
  if (recommended) return recommended
  const custom = selection.source === 'custom' ? selection.customRaceDefinition : undefined
  const languages = catalogRace?.languages ?? custom?.languages ?? []
  const summary = catalogRace
    ? `${catalogRace.shortDescription} ${catalogRace.tagline}`
    : custom?.descriptionShort || 'A custom ancestry. The Dungeon Master brings its traits into the story.'
  const portraitKey = typeof custom?.visual?.portraitKey === 'string' ? custom.visual.portraitKey : selection.raceName
  return {
    id: `catalog:${selection.raceId}`,
    name: selection.raceName,
    selection,
    summary,
    traits: languages.length ? [speaks(languages)] : [],
    skills: [],
    languages,
    portraits: portraitsFor(catalogRace?.key ?? portraitKey),
    names: { male: [], female: [] },
  }
}

const FALLBACK_NAMES: Record<SexKey, string[]> = ANCESTRIES[0].names

/** The next suggested name for the ancestry, cycling past the current one. */
export function suggestName(ancestryEntry: Ancestry | null, sex: SexKey, current: string) {
  const names = ancestryEntry?.names[sex].length ? ancestryEntry.names[sex] : FALLBACK_NAMES[sex]
  const index = names.indexOf(current.trim())
  return names[(index + 1) % names.length]
}

export function portraitSrc(ancestryEntry: Ancestry | null, sex: SexKey) {
  return ancestryEntry?.portraits?.[sex] ?? profileIconSrcForCharacter({ race: 'human', sex }) ?? ''
}

// ---- classes ---------------------------------------------------------------

export type FightingStyleId = 'archery' | 'defense' | 'dueling' | 'great_weapon'

export const FIGHTING_STYLES: Record<FightingStyleId, { name: string; effect: string }> = {
  archery: { name: 'Archery', effect: '+2 to ranged weapon attack rolls.' },
  defense: { name: 'Defense', effect: '+1 AC while you wear armour.' },
  dueling: { name: 'Dueling', effect: '+2 damage with a one-handed melee weapon and no other weapon.' },
  great_weapon: {
    name: 'Great Weapon Fighting',
    effect: 'Reroll 1s and 2s on the damage dice of two-handed melee weapons, once.',
  },
}

type Feature = { name: string; text: string }
type LaterFeature = Feature & { level: number }
type WeaponGrip = 'strength' | 'finesse' | 'ranged'

type Spellcasting = {
  ability: AbilityKey
  cantrips: string[]
  spells: string[]
  examples: [string, string, string]
}

export type CoreClass = {
  id: string
  name: string
  feel: string
  hitDie: number
  saves: [AbilityKey, AbilityKey]
  /** Abilities from most to least important; the recommended spread follows it. */
  priority: AbilityKey[]
  skillCount: number
  skills: SkillId[]
  start: Feature[]
  later: LaterFeature[]
  note?: string
  spellcasting?: Spellcasting
  fightingStyle?: { options: FightingStyleId[]; fromLevel: number; default: FightingStyleId }
  suggestedBackground: string
  kit: {
    items: string[]
    armor: 'none' | 'leather' | 'scale' | 'chain'
    shield: boolean
    weapon: { name: string; grip: WeaponGrip }
  }
}

const ALL_SKILLS = SKILL_IDS

export const CORE_CLASSES: CoreClass[] = [
  {
    id: 'barbarian',
    name: 'Barbarian',
    feel: 'Wade in, rage, and shrug off blows that would fell anyone else.',
    hitDie: 12,
    saves: ['strength', 'constitution'],
    priority: ['strength', 'constitution', 'dexterity', 'wisdom', 'charisma', 'intelligence'],
    skillCount: 2,
    skills: ['animal_handling', 'athletics', 'intimidation', 'nature', 'perception', 'survival'],
    start: [
      {
        name: 'Rage',
        text: 'Twice per long rest, as a bonus action: +2 damage on Strength melee attacks, resistance to bludgeoning, piercing and slashing, advantage on Strength checks and saves.',
      },
      { name: 'Unarmored Defense', text: 'Without armour your AC is 10 + Dexterity + Constitution.' },
    ],
    later: [
      { level: 2, name: 'Reckless Attack', text: 'Advantage on your Strength attacks; foes gain it against you.' },
      { level: 2, name: 'Danger Sense', text: 'Advantage on Dexterity saves against what you can see.' },
      { level: 3, name: 'More Rage', text: 'Rage three times per long rest.' },
      { level: 5, name: 'Extra Attack', text: 'Two attacks with every Attack action.' },
    ],
    suggestedBackground: 'folk_hero',
    kit: {
      items: ['Greataxe', '2 Handaxes', '4 Javelins', "Explorer's Pack", '5 Rations', 'Potion of Healing'],
      armor: 'none',
      shield: false,
      weapon: { name: 'Greataxe', grip: 'strength' },
    },
  },
  {
    id: 'fighter',
    name: 'Fighter',
    feel: 'Steady steel and good armour. You win the fight in front of you.',
    hitDie: 10,
    saves: ['strength', 'constitution'],
    priority: ['strength', 'constitution', 'dexterity', 'wisdom', 'charisma', 'intelligence'],
    skillCount: 2,
    skills: ['acrobatics', 'animal_handling', 'athletics', 'history', 'insight', 'intimidation', 'perception', 'survival'],
    start: [
      { name: 'Fighting Style', text: 'A trained way of fighting, chosen below, that the rules apply in every fight.' },
      { name: 'Second Wind', text: 'Once per short rest, as a bonus action, regain 1d10 + your level in hit points.' },
    ],
    later: [
      { level: 2, name: 'Action Surge', text: 'Once per short rest, take one more action on your turn.' },
      { level: 3, name: 'Improved Critical', text: 'As a Champion, you score critical hits on 19 and 20.' },
      { level: 5, name: 'Extra Attack', text: 'Two attacks with every Attack action.' },
    ],
    fightingStyle: { options: ['defense', 'dueling', 'great_weapon', 'archery'], fromLevel: 1, default: 'dueling' },
    suggestedBackground: 'soldier',
    kit: {
      items: ['Longsword', 'Shield', 'Chain Mail', "Explorer's Pack", '5 Rations', '5 Torches', 'Potion of Healing'],
      armor: 'chain',
      shield: true,
      weapon: { name: 'Longsword', grip: 'strength' },
    },
  },
  {
    id: 'monk',
    name: 'Monk',
    feel: 'Quick hands and quicker feet. Your body is the weapon.',
    hitDie: 8,
    saves: ['strength', 'dexterity'],
    priority: ['dexterity', 'wisdom', 'constitution', 'strength', 'intelligence', 'charisma'],
    skillCount: 2,
    skills: ['acrobatics', 'athletics', 'history', 'insight', 'religion', 'stealth'],
    start: [
      {
        name: 'Martial Arts',
        text: 'Unarmed strikes and monk weapons use Dexterity when it is higher, and after the Attack action you strike again as a bonus action.',
      },
      { name: 'Unarmored Defense', text: 'Without armour or a shield your AC is 10 + Dexterity + Wisdom.' },
    ],
    later: [
      { level: 2, name: 'Ki', text: 'Flurry of Blows, Patient Defense and Step of the Wind, refreshed on a short rest.' },
      { level: 5, name: 'Stunning Strike', text: 'Spend ki on a hit to stun the target until your next turn ends.' },
      { level: 5, name: 'Extra Attack', text: 'Two attacks with every Attack action.' },
    ],
    suggestedBackground: 'acolyte',
    kit: {
      items: ['Quarterstaff', '10 Darts', "Explorer's Pack", '5 Rations', 'Potion of Healing'],
      armor: 'none',
      shield: false,
      weapon: { name: 'Quarterstaff', grip: 'finesse' },
    },
  },
  {
    id: 'paladin',
    name: 'Paladin',
    feel: 'A sworn knight who heals with a touch and holds the line.',
    hitDie: 10,
    saves: ['wisdom', 'charisma'],
    priority: ['strength', 'charisma', 'constitution', 'wisdom', 'dexterity', 'intelligence'],
    skillCount: 2,
    skills: ['athletics', 'insight', 'intimidation', 'medicine', 'persuasion', 'religion'],
    start: [
      { name: 'Lay on Hands', text: 'A pool of 5 hit points per level to heal by touch, or 5 points to cure poison.' },
    ],
    later: [
      { level: 2, name: 'Divine Smite', text: 'Spend a spell slot on a hit for 2d8 radiant damage or more.' },
      { level: 2, name: 'Fighting Style', text: 'The style you choose now starts to apply.' },
      { level: 2, name: 'Spellcasting', text: 'Bless, Cure Wounds, Shield of Faith and other oaths in prayer.' },
      { level: 5, name: 'Extra Attack', text: 'Two attacks with every Attack action.' },
    ],
    fightingStyle: { options: ['defense', 'dueling', 'great_weapon'], fromLevel: 2, default: 'dueling' },
    suggestedBackground: 'soldier',
    kit: {
      items: ['Longsword', 'Shield', 'Chain Mail', 'Holy Symbol', "Priest's Pack", 'Potion of Healing'],
      armor: 'chain',
      shield: true,
      weapon: { name: 'Longsword', grip: 'strength' },
    },
  },
  {
    id: 'ranger',
    name: 'Ranger',
    feel: 'A watchful hunter of the wild places, deadly at a distance.',
    hitDie: 10,
    saves: ['strength', 'dexterity'],
    priority: ['dexterity', 'wisdom', 'constitution', 'strength', 'intelligence', 'charisma'],
    skillCount: 3,
    skills: ['animal_handling', 'athletics', 'insight', 'investigation', 'nature', 'perception', 'stealth', 'survival'],
    start: [
      { name: 'Wilderness training', text: 'Three class skills instead of two, and martial weapons from the start.' },
    ],
    later: [
      { level: 2, name: 'Fighting Style', text: 'The style you choose now starts to apply.' },
      { level: 2, name: 'Spellcasting', text: "Hunter's Mark, Cure Wounds, Entangle and other wild magic." },
      { level: 3, name: 'Colossus Slayer', text: 'As a Hunter, once per turn +1d8 damage to a wounded foe.' },
      { level: 5, name: 'Extra Attack', text: 'Two attacks with every Attack action.' },
    ],
    note: 'Favored Enemy and Natural Explorer are narrated by the Dungeon Master.',
    fightingStyle: { options: ['archery', 'defense', 'dueling'], fromLevel: 2, default: 'archery' },
    suggestedBackground: 'folk_hero',
    kit: {
      items: ['Longbow', 'Shortsword', 'Leather Armor', '20 Arrows', "Explorer's Pack", 'Potion of Healing'],
      armor: 'leather',
      shield: false,
      weapon: { name: 'Longbow', grip: 'ranged' },
    },
  },
  {
    id: 'rogue',
    name: 'Rogue',
    feel: 'Strike from the shadows, pick the lock, talk your way out.',
    hitDie: 8,
    saves: ['dexterity', 'intelligence'],
    priority: ['dexterity', 'constitution', 'wisdom', 'charisma', 'intelligence', 'strength'],
    skillCount: 4,
    skills: [
      'acrobatics', 'athletics', 'deception', 'insight', 'intimidation', 'investigation', 'perception',
      'performance', 'persuasion', 'sleight_of_hand', 'stealth',
    ],
    start: [
      {
        name: 'Sneak Attack',
        text: 'Once per turn, +1d6 damage with a finesse or ranged weapon when you have advantage or an ally is beside your target.',
      },
      { name: 'Expertise', text: 'Double proficiency in two skills you choose.' },
    ],
    later: [
      { level: 2, name: 'Cunning Action', text: 'Dash, Disengage or Hide as a bonus action.' },
      { level: 3, name: 'Sneak Attack 2d6', text: 'Your hidden strikes grow deadlier.' },
      { level: 5, name: 'Uncanny Dodge', text: 'Your reaction halves the damage of an attack that hits you.' },
    ],
    suggestedBackground: 'criminal',
    kit: {
      items: ['Rapier', 'Dagger', 'Leather Armor', "Thieves' Tools", "Burglar's Pack", 'Potion of Healing'],
      armor: 'leather',
      shield: false,
      weapon: { name: 'Rapier', grip: 'finesse' },
    },
  },
  {
    id: 'bard',
    name: 'Bard',
    feel: 'Words, music and a little magic that make everyone better.',
    hitDie: 8,
    saves: ['dexterity', 'charisma'],
    priority: ['charisma', 'dexterity', 'constitution', 'wisdom', 'intelligence', 'strength'],
    skillCount: 3,
    skills: ALL_SKILLS,
    start: [
      {
        name: 'Bardic Inspiration',
        text: 'As a bonus action, give an ally a d6 for a roll that would fail; Charisma-many uses per long rest.',
      },
    ],
    later: [
      { level: 2, name: 'Jack of All Trades', text: 'Half your proficiency bonus on checks you are untrained in.' },
      { level: 3, name: '2nd-level spells', text: 'Invisibility, Hold Person, Shatter and more.' },
      { level: 5, name: 'Font of Inspiration', text: 'Inspiration becomes a d8 and returns on a short rest.' },
    ],
    spellcasting: {
      ability: 'charisma',
      cantrips: ['Dancing Lights', 'Light', 'Mage Hand', 'Message', 'Minor Illusion', 'Vicious Mockery'],
      spells: [
        'Bane', 'Charm Person', 'Cure Wounds', 'Dissonant Whispers', 'Faerie Fire', 'Healing Word', 'Heroism',
        'Sleep', "Tasha's Hideous Laughter", 'Thunderwave', 'Silver Charm', 'Frostlace Veil', 'Ivory Lens',
      ],
      examples: ['Vicious Mockery', 'Healing Word', 'Dissonant Whispers'],
    },
    suggestedBackground: 'guild_artisan',
    kit: {
      items: ['Rapier', 'Dagger', 'Leather Armor', 'Lute', "Diplomat's Pack", 'Potion of Healing'],
      armor: 'leather',
      shield: false,
      weapon: { name: 'Rapier', grip: 'finesse' },
    },
  },
  {
    id: 'cleric',
    name: 'Cleric',
    feel: "A god's chosen hand: heal friends, smite the dark, stand fast.",
    hitDie: 8,
    saves: ['wisdom', 'charisma'],
    priority: ['wisdom', 'constitution', 'strength', 'dexterity', 'charisma', 'intelligence'],
    skillCount: 2,
    skills: ['history', 'insight', 'medicine', 'persuasion', 'religion'],
    start: [{ name: 'Spellcasting', text: 'Divine magic prepared each day from your god.' }],
    later: [
      { level: 2, name: 'Turn Undead', text: 'Once per short rest, undead that fail a Wisdom save flee from you.' },
      { level: 3, name: '2nd-level spells', text: 'Spiritual Weapon, Aid, Lesser Restoration and more.' },
      { level: 5, name: 'Destroy Undead', text: 'Weak undead you turn are destroyed outright.' },
    ],
    spellcasting: {
      ability: 'wisdom',
      cantrips: [
        'Guidance', 'Light', 'Sacred Flame', 'Spare the Dying', 'Thaumaturgy', 'Toll the Dead', 'Ravenous Knot',
        'Saffron Spark', 'Scarab Mote',
      ],
      spells: [
        'Bane', 'Bless', 'Command', 'Cure Wounds', 'Detect Magic', 'Guiding Bolt', 'Healing Word', 'Inflict Wounds',
        'Shield of Faith',
      ],
      examples: ['Sacred Flame', 'Guiding Bolt', 'Cure Wounds'],
    },
    suggestedBackground: 'acolyte',
    kit: {
      items: ['Mace', 'Shield', 'Scale Mail', 'Holy Symbol', "Priest's Pack", 'Potion of Healing', "Healer's Kit"],
      armor: 'scale',
      shield: true,
      weapon: { name: 'Mace', grip: 'strength' },
    },
  },
  {
    id: 'druid',
    name: 'Druid',
    feel: "Old nature magic: grasping vines, healing herbs, the beasts' trust.",
    hitDie: 8,
    saves: ['intelligence', 'wisdom'],
    priority: ['wisdom', 'dexterity', 'constitution', 'intelligence', 'charisma', 'strength'],
    skillCount: 2,
    skills: ['animal_handling', 'arcana', 'insight', 'medicine', 'nature', 'perception', 'religion', 'survival'],
    start: [{ name: 'Spellcasting', text: 'Primal magic prepared each day from the living world.' }],
    later: [
      { level: 2, name: 'Wild Shape', text: 'Take the shape of a beast you have seen.' },
      { level: 3, name: '2nd-level spells', text: 'Moonbeam, Barkskin, Pass without Trace and more.' },
      { level: 5, name: '3rd-level spells', text: 'Call Lightning and Dispel Magic.' },
    ],
    note: 'Wild Shape is narrated by the Dungeon Master.',
    spellcasting: {
      ability: 'wisdom',
      cantrips: ['Druidcraft', 'Guidance', 'Poison Spray', 'Produce Flame', 'Thorn Whip', 'Mire Knot', 'Mire Palm', 'Quartz Mote'],
      spells: [
        'Cure Wounds', 'Entangle', 'Faerie Fire', 'Goodberry', 'Healing Word', 'Primal Shift', 'Speak with Animals',
        'Thunderwave',
      ],
      examples: ['Produce Flame', 'Entangle', 'Healing Word'],
    },
    suggestedBackground: 'folk_hero',
    kit: {
      items: ['Scimitar', 'Wooden Shield', 'Leather Armor', 'Druidic Focus', "Explorer's Pack", 'Potion of Healing', "Healer's Kit"],
      armor: 'leather',
      shield: true,
      weapon: { name: 'Scimitar', grip: 'finesse' },
    },
  },
  {
    id: 'sorcerer',
    name: 'Sorcerer',
    feel: 'Magic born in your blood, raw and bright, bent to your will.',
    hitDie: 6,
    saves: ['constitution', 'charisma'],
    priority: ['charisma', 'constitution', 'dexterity', 'wisdom', 'intelligence', 'strength'],
    skillCount: 2,
    skills: ['arcana', 'deception', 'insight', 'intimidation', 'persuasion', 'religion'],
    start: [{ name: 'Spellcasting', text: 'Innate magic you always know, cast on instinct.' }],
    later: [
      { level: 2, name: 'Sorcery Points', text: 'Turn spell slots into points and points back into slots.' },
      { level: 3, name: 'Metamagic', text: 'Quicken a spell to a bonus action, or twin it onto a second target.' },
      { level: 5, name: '3rd-level spells', text: 'Fireball, Lightning Bolt, Haste and more.' },
    ],
    spellcasting: {
      ability: 'charisma',
      cantrips: [
        'Chill Touch', 'Dancing Lights', 'Fire Bolt', 'Light', 'Mage Hand', 'Message', 'Minor Illusion',
        'Poison Spray', 'Ray of Frost', 'Shocking Grasp', 'Brine Glimmer',
      ],
      spells: [
        'Burning Hands', 'Chaos Bolt', 'Chromatic Orb', 'False Life', 'Mage Armor', 'Magic Missile', 'Shield', 'Sleep',
        'Thunderwave', 'Crow Ward', 'Candle Ward',
      ],
      examples: ['Fire Bolt', 'Chaos Bolt', 'Shield'],
    },
    suggestedBackground: 'sage',
    kit: {
      items: ['Dagger', 'Light Crossbow', 'Arcane Focus', "Explorer's Pack", 'Potion of Healing'],
      armor: 'none',
      shield: false,
      weapon: { name: 'Dagger', grip: 'finesse' },
    },
  },
  {
    id: 'warlock',
    name: 'Warlock',
    feel: 'A pact with something vast: eldritch blasts and dangerous favours.',
    hitDie: 8,
    saves: ['wisdom', 'charisma'],
    priority: ['charisma', 'constitution', 'dexterity', 'wisdom', 'intelligence', 'strength'],
    skillCount: 2,
    skills: ['arcana', 'deception', 'history', 'intimidation', 'investigation', 'nature', 'religion'],
    start: [{ name: 'Pact Magic', text: 'A few spells, cast from slots that return on a short rest.' }],
    later: [
      { level: 2, name: 'Agonizing Blast', text: 'Add your Charisma to every Eldritch Blast beam.' },
      { level: 3, name: '2nd-level spells', text: 'Misty Step, Mirror Image, Hold Person and more.' },
      { level: 5, name: '3rd-level spells', text: 'Counterspell, Fly, Hypnotic Pattern and more.' },
    ],
    spellcasting: {
      ability: 'charisma',
      cantrips: ['Chill Touch', 'Eldritch Blast', 'Mage Hand', 'Minor Illusion', 'Poison Spray', 'Scarab Glimmer', 'Moon Palm'],
      spells: ['Armor of Agathys', 'Charm Person', 'Hellish Rebuke', 'Hex', 'Lilac Step'],
      examples: ['Eldritch Blast', 'Hex', 'Hellish Rebuke'],
    },
    suggestedBackground: 'sage',
    kit: {
      items: ['Dagger', 'Light Crossbow', 'Leather Armor', 'Arcane Focus', "Scholar's Pack", 'Potion of Healing'],
      armor: 'leather',
      shield: false,
      weapon: { name: 'Dagger', grip: 'finesse' },
    },
  },
  {
    id: 'wizard',
    name: 'Wizard',
    feel: 'Learned magic from a spellbook: the right spell for every problem.',
    hitDie: 6,
    saves: ['intelligence', 'wisdom'],
    priority: ['intelligence', 'constitution', 'dexterity', 'wisdom', 'charisma', 'strength'],
    skillCount: 2,
    skills: ['arcana', 'history', 'insight', 'investigation', 'medicine', 'religion'],
    start: [
      { name: 'Spellcasting', text: 'Arcane magic prepared each day from your spellbook.' },
      {
        name: 'Arcane Recovery',
        text: 'Once per long rest, after a short rest, recover spent spell slots.',
      },
    ],
    later: [
      { level: 3, name: '2nd-level spells', text: 'Misty Step, Scorching Ray, Web and more.' },
      { level: 5, name: '3rd-level spells', text: 'Fireball, Counterspell, Fly and more.' },
    ],
    spellcasting: {
      ability: 'intelligence',
      cantrips: [
        'Chill Touch', 'Dancing Lights', 'Fire Bolt', 'Light', 'Mage Hand', 'Message', 'Prestidigitation',
        'Ray of Frost', 'Shocking Grasp', 'Frostlace Palm',
      ],
      spells: [
        'Burning Hands', 'Chromatic Orb', 'Detect Magic', 'False Life', 'Mage Armor', 'Magic Missile', 'Shield',
        'Sleep', 'Thunderwave', 'Copper Charm', 'Verdigris Brand',
      ],
      examples: ['Fire Bolt', 'Magic Missile', 'Shield'],
    },
    suggestedBackground: 'sage',
    kit: {
      items: ['Quarterstaff', 'Dagger', 'Spellbook', 'Component Pouch', "Scholar's Pack", 'Potion of Healing'],
      armor: 'none',
      shield: false,
      weapon: { name: 'Quarterstaff', grip: 'strength' },
    },
  },
]

export const NARRATED_CLASS_NOTE = 'Narrated class — the Dungeon Master adjudicates its features.'

function baseClassKey(label: string) {
  return label.split('-', 1)[0].trim().toLowerCase()
}

/** The core class behind a calling label such as `Fighter` or `Fighter - Champion`; null for narrated classes. */
export function coreClassFor(label: string | null | undefined) {
  if (!label) return null
  const key = baseClassKey(label)
  return CORE_CLASSES.find((entry) => entry.id === key) ?? null
}

export function isCoreClassName(name: string) {
  return CORE_CLASSES.some((entry) => entry.name === name)
}

/** How a calling reads in prose: `Fighter`, or `Fighter (Champion)` for a subclass. */
export function callingName(label: string) {
  const [base, subclass] = label.split(' - ', 2).map((part) => part.trim())
  return subclass ? `${base} (${subclass})` : base
}

export function savesLabel(entry: CoreClass) {
  return entry.saves.map((ability) => ABILITY_LABELS[ability]).join(', ')
}

// ---- abilities -------------------------------------------------------------

export const STANDARD_ARRAY = [15, 14, 13, 12, 10, 8] as const
const NARRATED_PRIORITY: AbilityKey[] = ['constitution', 'dexterity', 'wisdom', 'strength', 'charisma', 'intelligence']

/** The standard array laid out by the calling's priority order. */
export function recommendedScores(label: string | null): PointBuyScores {
  const scores = {} as PointBuyScores
  const priority = coreClassFor(label)?.priority ?? NARRATED_PRIORITY
  priority.forEach((ability, index) => {
    scores[ability] = STANDARD_ARRAY[index]
  })
  return scores
}

export const MIN_SCORE = 8
export const MAX_SCORE = 15

export function pointsRemaining(scores: PointBuyScores) {
  return POINT_BUY_BUDGET - pointBuySpent(scores)
}

export function pointBuyValid(scores: PointBuyScores) {
  return (
    POINT_BUY_ABILITIES.every(({ key }) => scores[key] >= MIN_SCORE && scores[key] <= MAX_SCORE) &&
    pointsRemaining(scores) >= 0
  )
}

export function modifier(score: number) {
  return Math.floor((score - 10) / 2)
}

export function signed(value: number) {
  return value >= 0 ? `+${value}` : `−${Math.abs(value)}`
}

// ---- the draft --------------------------------------------------------------

export type AbilityMode = 'recommended' | 'adjust'

export type HeroDraft = {
  name: string
  ancestry: Ancestry | null
  sex: SexKey
  calling: string | null
  fightingStyle: FightingStyleId | null
  abilityMode: AbilityMode
  /** The point-buy scores while adjusting. */
  scores: PointBuyScores
  backgroundId: string | null
  skills: SkillId[]
  expertise: SkillId[]
}

export function emptyDraft(): HeroDraft {
  return {
    name: '',
    ancestry: null,
    sex: 'male',
    calling: null,
    fightingStyle: null,
    abilityMode: 'recommended',
    scores: recommendedScores(null),
    backgroundId: null,
    skills: [],
    expertise: [],
  }
}

export function heroScores(draft: HeroDraft): PointBuyScores {
  return draft.abilityMode === 'adjust' ? draft.scores : recommendedScores(draft.calling)
}

/** The background in effect: the player's choice, else the one that suits the calling. */
export function heroBackground(draft: HeroDraft) {
  return backgroundFromValue(draft.backgroundId ?? coreClassFor(draft.calling)?.suggestedBackground ?? 'folk_hero')
}

export function heroFightingStyle(draft: HeroDraft): FightingStyleId | null {
  const options = coreClassFor(draft.calling)?.fightingStyle
  if (!options) return null
  return draft.fightingStyle && options.options.includes(draft.fightingStyle) ? draft.fightingStyle : options.default
}

/** Class skills the player may choose: the class list minus what the background already grants. */
export function classSkillOptions(draft: HeroDraft): SkillId[] {
  const entry = coreClassFor(draft.calling)
  if (!entry) return []
  const granted = new Set(heroBackground(draft)?.skillProficiencies ?? [])
  return entry.skills.filter((skill) => !granted.has(skill))
}

export function proficientSkills(draft: HeroDraft): SkillId[] {
  const owned = new Set<string>([
    ...(heroBackground(draft)?.skillProficiencies ?? []),
    ...(draft.ancestry?.skills ?? []),
    ...draft.skills,
  ])
  return SKILL_IDS.filter((skill) => owned.has(skill))
}

export function needsExpertise(draft: HeroDraft) {
  return coreClassFor(draft.calling)?.id === 'rogue'
}

export const EXPERTISE_COUNT = 2

function withoutLostChoices(draft: HeroDraft): HeroDraft {
  const options = new Set(classSkillOptions(draft))
  const skills = draft.skills.filter((skill) => options.has(skill))
  const next = { ...draft, skills }
  const proficient = new Set(proficientSkills(next))
  return { ...next, expertise: needsExpertise(next) ? draft.expertise.filter((skill) => proficient.has(skill)) : [] }
}

export function withCalling(draft: HeroDraft, calling: string): HeroDraft {
  if (draft.calling === calling) return draft
  const sameClass = coreClassFor(draft.calling) === coreClassFor(calling) && coreClassFor(calling) !== null
  return withoutLostChoices({
    ...draft,
    calling,
    fightingStyle: sameClass ? draft.fightingStyle : null,
    skills: sameClass ? draft.skills : [],
    expertise: sameClass ? draft.expertise : [],
  })
}

export function withBackground(draft: HeroDraft, backgroundId: string): HeroDraft {
  return withoutLostChoices({ ...draft, backgroundId })
}

export function withAncestry(draft: HeroDraft, ancestryEntry: Ancestry): HeroDraft {
  return withoutLostChoices({ ...draft, ancestry: ancestryEntry })
}

export function toggleSkill(draft: HeroDraft, skill: SkillId): HeroDraft {
  const chosen = draft.skills.includes(skill)
  const count = coreClassFor(draft.calling)?.skillCount ?? 0
  if (!chosen && (draft.skills.length >= count || !classSkillOptions(draft).includes(skill))) return draft
  const skills = chosen ? draft.skills.filter((entry) => entry !== skill) : [...draft.skills, skill]
  return withoutLostChoices({ ...draft, skills })
}

export function toggleExpertise(draft: HeroDraft, skill: SkillId): HeroDraft {
  const chosen = draft.expertise.includes(skill)
  if (!chosen && (draft.expertise.length >= EXPERTISE_COUNT || !proficientSkills(draft).includes(skill))) return draft
  return { ...draft, expertise: chosen ? draft.expertise.filter((entry) => entry !== skill) : [...draft.expertise, skill] }
}

export function withAbilityMode(draft: HeroDraft, abilityMode: AbilityMode): HeroDraft {
  if (draft.abilityMode === abilityMode) return draft
  return { ...draft, abilityMode, scores: abilityMode === 'adjust' ? heroScores(draft) : draft.scores }
}

export function stepScore(draft: HeroDraft, ability: AbilityKey, delta: 1 | -1): HeroDraft {
  const score = draft.scores[ability] + delta
  if (score < MIN_SCORE || score > MAX_SCORE) return draft
  return { ...draft, scores: { ...draft.scores, [ability]: score } }
}

// ---- derived numbers -------------------------------------------------------

export type HeroNumbers = {
  hp: number
  armorClass: number | null
  attack: { weapon: string; bonus: number } | null
  spellSaveDc: number | null
  proficiencyBonus: number
}

const PROFICIENCY_BONUS = 2

function armorClass(entry: CoreClass, scores: PointBuyScores, style: FightingStyleId | null, styleActive: boolean) {
  const dex = modifier(scores.dexterity)
  const { armor, shield } = entry.kit
  let base: number
  if (armor === 'chain') base = 16
  else if (armor === 'scale') base = 14 + Math.min(dex, 2)
  else if (armor === 'leather') base = 11 + dex
  else if (entry.id === 'barbarian') base = 10 + dex + modifier(scores.constitution)
  else if (entry.id === 'monk' && !shield) base = 10 + dex + modifier(scores.wisdom)
  else base = 10 + dex
  const defense = styleActive && style === 'defense' && armor !== 'none' ? 1 : 0
  return base + (shield ? 2 : 0) + defense
}

function attackBonus(entry: CoreClass, scores: PointBuyScores, style: FightingStyleId | null, styleActive: boolean) {
  const strength = modifier(scores.strength)
  const dexterity = modifier(scores.dexterity)
  const { grip } = entry.kit.weapon
  const ability = grip === 'ranged' ? dexterity : grip === 'finesse' ? Math.max(strength, dexterity) : strength
  const archery = styleActive && style === 'archery' && grip === 'ranged' ? 2 : 0
  return ability + PROFICIENCY_BONUS + archery
}

/** HP, AC with the starting kit, attack with the main weapon and spell save DC at level 1. */
export function heroNumbers(draft: HeroDraft): HeroNumbers {
  const scores = heroScores(draft)
  const entry = coreClassFor(draft.calling)
  const hitDie = entry?.hitDie ?? hitDieForClass(draft.calling ?? '')
  const hp = Math.max(1, hitDie + modifier(scores.constitution))
  if (!entry) return { hp, armorClass: null, attack: null, spellSaveDc: null, proficiencyBonus: PROFICIENCY_BONUS }
  const style = heroFightingStyle(draft)
  const styleActive = (entry.fightingStyle?.fromLevel ?? 99) <= 1
  return {
    hp,
    armorClass: armorClass(entry, scores, style, styleActive),
    attack: { weapon: entry.kit.weapon.name, bonus: attackBonus(entry, scores, style, styleActive) },
    spellSaveDc: entry.spellcasting ? 8 + PROFICIENCY_BONUS + modifier(scores[entry.spellcasting.ability]) : null,
    proficiencyBonus: PROFICIENCY_BONUS,
  }
}

// ---- review ----------------------------------------------------------------

export function heroLanguages(draft: HeroDraft) {
  const languages = [...(draft.ancestry?.languages ?? []), ...(heroBackground(draft)?.languages ?? [])]
  return languages.filter((language, index) => languages.indexOf(language) === index)
}

const BACKGROUND_HOOKS: Record<string, string> = {
  acolyte: 'served a temple until a vision sent them out onto the road',
  criminal: 'knows every back door in town and owes favours to people best avoided',
  folk_hero: 'once stood between their village and ruin, and has not been allowed to forget it',
  guild_artisan: 'left the guild workshop with skilled hands and an old debt to settle',
  sage: 'left the library to find the answers no book would give',
  soldier: 'marched with an army and still wakes at the sound of distant drums',
}

function article(phrase: string) {
  return /^[aeiou]/i.test(phrase) ? 'An' : 'A'
}

/** `A sage elf wizard who left the library…`, built from the choices. */
export function heroSentence(draft: HeroDraft) {
  const background = heroBackground(draft)
  const parts = [background?.name, draft.ancestry?.name, draft.calling ? callingName(draft.calling) : null]
    .filter((part): part is string => Boolean(part))
    .map((part) => part.toLowerCase())
  const subject = parts.join(' ')
  const hook = background ? BACKGROUND_HOOKS[background.id] : null
  return `${article(subject)} ${subject}${hook ? ` who ${hook}` : ''}.`
}

export function heroDescriptor(draft: HeroDraft) {
  return ['Level 1', draft.ancestry?.name, draft.calling ? callingName(draft.calling) : null].filter(Boolean).join(' ')
}

// ---- validity and the payload -------------------------------------------------

export function stepComplete(draft: HeroDraft, step: CreateStep): boolean {
  if (step === 'who') return Boolean(draft.name.trim() && draft.ancestry)
  if (step === 'calling') return Boolean(draft.calling)
  if (step === 'shape') {
    const entry = coreClassFor(draft.calling)
    return (
      pointBuyValid(heroScores(draft)) &&
      Boolean(heroBackground(draft)) &&
      (!entry || draft.skills.length === entry.skillCount) &&
      (!needsExpertise(draft) || draft.expertise.length === EXPERTISE_COUNT)
    )
  }
  return CREATE_STEPS.slice(0, -1).every((earlier) => stepComplete(draft, earlier))
}

/** What still stands between this step and Continue, in one short sentence. */
export function stepHint(draft: HeroDraft, step: CreateStep): string {
  if (stepComplete(draft, step)) return ''
  if (step === 'who') return draft.ancestry ? 'Name your hero to continue.' : 'Choose an ancestry to continue.'
  if (step === 'calling') return 'Choose a calling to continue.'
  if (step === 'shape') {
    const entry = coreClassFor(draft.calling)
    if (!pointBuyValid(heroScores(draft))) return 'Spend no more than 27 points to continue.'
    if (entry && draft.skills.length !== entry.skillCount) {
      return `Choose ${entry.skillCount} class skills to continue.`
    }
    return 'Mark two skills for expertise to continue.'
  }
  return 'Finish the earlier steps to begin.'
}

export type CreateHeroPayload = {
  character_name: string
  race: string
  race_selection: CharacterRaceSelection
  sex: SexKey
  char_class: string
  level: 1
  background: string
  stats: ReturnType<typeof pointBuyStatsPayload>
  skill_choices?: SkillId[]
  expertise?: SkillId[]
  fighting_style?: FightingStyleId
}

export function createPayload(draft: HeroDraft): CreateHeroPayload {
  const background = heroBackground(draft)
  if (!stepComplete(draft, 'review') || !draft.ancestry || !draft.calling || !background) {
    throw new Error('The hero is not finished yet.')
  }
  const entry = coreClassFor(draft.calling)
  const style = heroFightingStyle(draft)
  const payload: CreateHeroPayload = {
    character_name: draft.name.trim(),
    race: draft.ancestry.selection.raceName,
    race_selection: draft.ancestry.selection,
    sex: draft.sex,
    char_class: draft.calling,
    level: 1,
    background: background.id,
    stats: pointBuyStatsPayload(heroScores(draft), 1, draft.calling),
  }
  if (entry) payload.skill_choices = [...draft.skills]
  if (needsExpertise(draft)) payload.expertise = [...draft.expertise]
  if (style) payload.fighting_style = style
  return payload
}
