import { PLAYABLE_BACKGROUNDS, backgroundFromValue, type PlayableBackground } from '../../backgroundCatalog'
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
import { tiesDraft, tiesPayload, tiesProblem, type HeroTies, type HeroTiesDraft } from '../heroTies'
import {
  FALLBACK_RULES,
  SKILL_IDS,
  type ClassRules,
  type CreationRules,
  type FightingStyleId,
  type SkillId,
} from './creationRules'

/*
 * The four-step hero creator: the recommended ancestries, how each core class is
 * presented, the recommended ability spread, the live derived numbers and the
 * creation payload. Everything here is pure so it can be tested without rendering.
 *
 * The rules themselves (class skills, saves, kits, spells, features, background
 * skills) are the server's and travel with the draft (`HeroDraft.rules`); see
 * creationRules.ts.
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

// ---- ancestries ------------------------------------------------------------

export type Ancestry = {
  id: string
  name: string
  /** The race the server stores (`race_selection`): the catalog entry of the same id. */
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

/**
 * The nine recommended ancestries, in the order the portrait row shows them.
 * Each is its own entry in the race catalog; Goliath and Harengon come from the
 * catalog's beginner-friendly list.
 */
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
      male: ['Rowan Pell', 'Tomas Reed', 'Edric Hale', 'Corin Ashby'],
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
      female: ['Shava Leafwhisper', 'Naivara Starwhisper', 'Sariel Brightwater', 'Ilyra Fernsong'],
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
      male: ['Brottor Deepdelve', 'Durgan Stonehelm', 'Harbek Coalbeard', 'Rurik Ironfoot'],
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
      female: ['Merla Applebrook', 'Rosie Thorngage', 'Lidda Hilltop', 'Callie Greenbottle'],
    },
  }),
  ancestry({
    id: 'goliath',
    name: 'Goliath',
    selection: curated('goliath', 'Goliath'),
    summary:
      'Goliaths are mountain-born giantkin who prize endurance and a fair contest. They measure themselves by the trials they meet.',
    traits: ['Mountain Born: proficient in Athletics'],
    skills: ['athletics'],
    languages: ['Common', 'Giant'],
    names: {
      male: ['Aukan Stonehand', 'Gauthak Highcrag', 'Keothi Windrunner', 'Thotham Ridgeborn'],
      female: ['Kuori Skywatcher', 'Nalla Frostpeak', 'Vaunea Steadfall', 'Orilo Longstride'],
    },
  }),
  ancestry({
    id: 'harengon',
    name: 'Harengon',
    selection: curated('harengon', 'Harengon'),
    summary:
      'Harengon are quick rabbitfolk with springing legs and restless luck. They hear trouble long before it arrives.',
    traits: ['Keen Hearing: proficient in Perception'],
    skills: ['perception'],
    languages: ['Common', 'Sylvan'],
    names: {
      male: ['Bramble Quickfoot', 'Thistle Longear', 'Hob Fernhop', 'Clover Swiftwhisker'],
      female: ['Hazel Brightleap', 'Wren Dewfoot', 'Poppy Thornhop', 'Sorrel Softpaw'],
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

/** An ancestry picked from the ancestry catalog: one of the nine when it is one, else built from the race catalog. */
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

function nameKey(name: string) {
  return name.trim().toLowerCase()
}

/** The ready-made heroes' names: a suggestion never hands out one of them. */
const READY_MADE_HERO_NAMES = new Set(['Arden Vale', 'Liora Quill', 'Mara Fen', 'Tovan Ember'].map(nameKey))

/**
 * The next suggested name for the ancestry, cycling past the current one
 * (however it is capitalised), so the same name never comes twice in a row.
 */
export function suggestName(ancestryEntry: Ancestry | null, sex: SexKey, current: string) {
  const names = (ancestryEntry?.names[sex].length ? ancestryEntry.names[sex] : FALLBACK_NAMES[sex]).filter(
    (name) => !READY_MADE_HERO_NAMES.has(nameKey(name)),
  )
  const index = names.findIndex((name) => nameKey(name) === nameKey(current))
  return names[(index + 1) % names.length]
}

export function portraitSrc(ancestryEntry: Ancestry | null, sex: SexKey) {
  return ancestryEntry?.portraits?.[sex] ?? profileIconSrcForCharacter({ race: 'human', sex }) ?? ''
}

// ---- classes ---------------------------------------------------------------

export const FIGHTING_STYLES: Record<FightingStyleId, { name: string; effect: string }> = {
  archery: { name: 'Archery', effect: '+2 to ranged weapon attack rolls.' },
  defense: { name: 'Defense', effect: '+1 AC while you wear armour.' },
  dueling: { name: 'Dueling', effect: '+2 damage with a one-handed melee weapon and no other weapon.' },
  great_weapon: {
    name: 'Great Weapon Fighting',
    effect: 'Reroll 1s and 2s on the damage dice of two-handed melee weapons, once.',
  },
}

type WeaponGrip = 'strength' | 'finesse' | 'ranged'

type ClassGear = {
  armor: 'none' | 'leather' | 'scale' | 'chain'
  shield: boolean
  weapon: { name: string; grip: WeaponGrip }
}

/** How the creator presents a core class; its rules come from `CreationRules`. */
type ClassPresentation = {
  feel: string
  /** Abilities from most to least important; the recommended spread follows it. */
  priority: AbilityKey[]
  suggestedBackground: string
  note?: string
  /** The fighting style in effect until the player picks one. */
  defaultStyle?: FightingStyleId
  /** The spellcasting ability and the spells to name first. */
  magic?: { ability: AbilityKey; examples: string[] }
  /** The starting kit's armour, shield and main weapon, for AC and the attack bonus. */
  gear: ClassGear
}

const CLASS_PRESENTATION: Record<string, ClassPresentation> = {
  barbarian: {
    feel: 'Wade in, rage, and shrug off blows that would fell anyone else.',
    priority: ['strength', 'constitution', 'dexterity', 'wisdom', 'charisma', 'intelligence'],
    suggestedBackground: 'folk_hero',
    gear: { armor: 'none', shield: false, weapon: { name: 'Greataxe', grip: 'strength' } },
  },
  fighter: {
    feel: 'Steady steel and good armour. You win the fight in front of you.',
    priority: ['strength', 'constitution', 'dexterity', 'wisdom', 'charisma', 'intelligence'],
    suggestedBackground: 'soldier',
    defaultStyle: 'dueling',
    gear: { armor: 'chain', shield: true, weapon: { name: 'Longsword', grip: 'strength' } },
  },
  monk: {
    feel: 'Quick hands and quicker feet. Your body is the weapon.',
    priority: ['dexterity', 'wisdom', 'constitution', 'strength', 'intelligence', 'charisma'],
    suggestedBackground: 'acolyte',
    gear: { armor: 'none', shield: false, weapon: { name: 'Quarterstaff', grip: 'finesse' } },
  },
  paladin: {
    feel: 'A sworn knight who heals with a touch and holds the line.',
    priority: ['strength', 'charisma', 'constitution', 'wisdom', 'dexterity', 'intelligence'],
    suggestedBackground: 'soldier',
    defaultStyle: 'dueling',
    magic: { ability: 'charisma', examples: [] },
    gear: { armor: 'chain', shield: true, weapon: { name: 'Longsword', grip: 'strength' } },
  },
  ranger: {
    feel: 'A watchful hunter of the wild places, deadly at a distance.',
    priority: ['dexterity', 'wisdom', 'constitution', 'strength', 'intelligence', 'charisma'],
    suggestedBackground: 'folk_hero',
    note: 'Favored Enemy and Natural Explorer are narrated by the Dungeon Master.',
    defaultStyle: 'archery',
    magic: { ability: 'wisdom', examples: [] },
    gear: { armor: 'leather', shield: false, weapon: { name: 'Longbow', grip: 'ranged' } },
  },
  rogue: {
    feel: 'Strike from the shadows, pick the lock, talk your way out.',
    priority: ['dexterity', 'constitution', 'wisdom', 'charisma', 'intelligence', 'strength'],
    suggestedBackground: 'criminal',
    gear: { armor: 'leather', shield: false, weapon: { name: 'Rapier', grip: 'finesse' } },
  },
  bard: {
    feel: 'Words, music and a little magic that make everyone better.',
    priority: ['charisma', 'dexterity', 'constitution', 'wisdom', 'intelligence', 'strength'],
    suggestedBackground: 'guild_artisan',
    magic: { ability: 'charisma', examples: ['Vicious Mockery', 'Healing Word', 'Dissonant Whispers'] },
    gear: { armor: 'leather', shield: false, weapon: { name: 'Rapier', grip: 'finesse' } },
  },
  cleric: {
    feel: "A god's chosen hand: heal friends, smite the dark, stand fast.",
    priority: ['wisdom', 'constitution', 'strength', 'dexterity', 'charisma', 'intelligence'],
    suggestedBackground: 'acolyte',
    magic: { ability: 'wisdom', examples: ['Sacred Flame', 'Guiding Bolt', 'Cure Wounds'] },
    gear: { armor: 'scale', shield: true, weapon: { name: 'Mace', grip: 'strength' } },
  },
  druid: {
    feel: "Old nature magic: grasping vines, healing herbs, the beasts' trust.",
    priority: ['wisdom', 'dexterity', 'constitution', 'intelligence', 'charisma', 'strength'],
    suggestedBackground: 'folk_hero',
    note: 'Wild Shape is narrated by the Dungeon Master.',
    magic: { ability: 'wisdom', examples: ['Produce Flame', 'Entangle', 'Healing Word'] },
    gear: { armor: 'leather', shield: true, weapon: { name: 'Scimitar', grip: 'finesse' } },
  },
  sorcerer: {
    feel: 'Magic born in your blood, raw and bright, bent to your will.',
    priority: ['charisma', 'constitution', 'dexterity', 'wisdom', 'intelligence', 'strength'],
    suggestedBackground: 'sage',
    magic: { ability: 'charisma', examples: ['Fire Bolt', 'Chaos Bolt', 'Shield'] },
    gear: { armor: 'none', shield: false, weapon: { name: 'Dagger', grip: 'finesse' } },
  },
  warlock: {
    feel: 'A pact with something vast: eldritch blasts and dangerous favours.',
    priority: ['charisma', 'constitution', 'dexterity', 'wisdom', 'intelligence', 'strength'],
    suggestedBackground: 'sage',
    magic: { ability: 'charisma', examples: ['Eldritch Blast', 'Hex', 'Hellish Rebuke'] },
    gear: { armor: 'leather', shield: false, weapon: { name: 'Dagger', grip: 'finesse' } },
  },
  wizard: {
    feel: 'Learned magic from a spellbook: the right spell for every problem.',
    priority: ['intelligence', 'constitution', 'dexterity', 'wisdom', 'charisma', 'strength'],
    suggestedBackground: 'sage',
    magic: { ability: 'intelligence', examples: ['Fire Bolt', 'Magic Missile', 'Shield'] },
    gear: { armor: 'none', shield: false, weapon: { name: 'Quarterstaff', grip: 'strength' } },
  },
}

/** A core class as the creator shows it: the server's rules with the client's presentation. */
export type CoreClass = Omit<ClassRules, 'fightingStyle'> & {
  feel: string
  priority: AbilityKey[]
  suggestedBackground: string
  note?: string
  gear: ClassGear
  fightingStyle?: { fromLevel: number; options: FightingStyleId[]; default: FightingStyleId }
  /** Present when the class starts with spells: the ability and up to three spells to name. */
  spellcasting?: { ability: AbilityKey; examples: string[] }
}

function presentedClass(rules: ClassRules): CoreClass | null {
  if (!Object.hasOwn(CLASS_PRESENTATION, rules.id)) return null
  const { defaultStyle, magic, ...presentation } = CLASS_PRESENTATION[rules.id]
  const { fightingStyle, ...classRules } = rules
  const entry: CoreClass = { ...classRules, ...presentation }
  if (fightingStyle) {
    const fallbackStyle = defaultStyle && fightingStyle.options.includes(defaultStyle) ? defaultStyle : fightingStyle.options[0]
    entry.fightingStyle = { ...fightingStyle, default: fallbackStyle }
  }
  const known = [...rules.cantrips, ...rules.spells]
  if (magic && known.length) {
    const named = magic.examples.filter((spell) => known.includes(spell))
    entry.spellcasting = { ability: magic.ability, examples: named.length ? named : known.slice(0, 3) }
  }
  return entry
}

const PRESENTED = new WeakMap<CreationRules, CoreClass[]>()

/** The core classes of a rule set, in the Calling step's order. */
export function coreClasses(rules: CreationRules): CoreClass[] {
  let classes = PRESENTED.get(rules)
  if (!classes) {
    classes = rules.classes.map(presentedClass).filter((entry): entry is CoreClass => entry !== null)
    PRESENTED.set(rules, classes)
  }
  return classes
}

export const NARRATED_CLASS_NOTE = 'Narrated class — the Dungeon Master adjudicates its features.'

function baseClassKey(label: string) {
  return label.split('-', 1)[0].trim().toLowerCase()
}

/** The core class behind a calling label such as `Fighter` or `Fighter - Champion`; null for narrated classes. */
export function coreClassFor(rules: CreationRules, label: string | null | undefined) {
  if (!label) return null
  const key = baseClassKey(label)
  return coreClasses(rules).find((entry) => entry.id === key) ?? null
}

/** The core class of the draft's calling under the draft's rules. */
export function heroClass(draft: HeroDraft) {
  return coreClassFor(draft.rules, draft.calling)
}

export function isCoreClassName(name: string) {
  return Object.hasOwn(CLASS_PRESENTATION, name.trim().toLowerCase())
}

/** How a calling reads in prose: `Fighter`, or `Fighter (Champion)` for a subclass. */
export function callingName(label: string) {
  const [base, subclass] = label.split(' - ', 2).map((part) => part.trim())
  return subclass ? `${base} (${subclass})` : base
}

export function savesLabel(entry: CoreClass) {
  return entry.saves.map((ability) => ABILITY_LABELS[ability]).join(', ')
}

const COUNT_WORDS = ['no', 'one', 'two', 'three', 'four', 'five', 'six']

/** `two` for 2; digits past the words. */
export function countWord(count: number) {
  return COUNT_WORDS[count] ?? String(count)
}

/** `one skill`, `two skills`. */
export function skillCountPhrase(count: number) {
  return `${countWord(count)} ${count === 1 ? 'skill' : 'skills'}`
}

// ---- backgrounds -----------------------------------------------------------

function withGrantedSkills(rules: CreationRules, background: PlayableBackground): PlayableBackground {
  const granted = rules.backgrounds.find((entry) => entry.id === background.id)
  return granted ? { ...background, skillProficiencies: granted.skills } : background
}

/** The six catalog backgrounds with the skills the rules grant. */
export function backgroundOptions(rules: CreationRules): PlayableBackground[] {
  return PLAYABLE_BACKGROUNDS.map((background) => withGrantedSkills(rules, background))
}

// ---- abilities -------------------------------------------------------------

export const STANDARD_ARRAY = [15, 14, 13, 12, 10, 8] as const
const NARRATED_PRIORITY: AbilityKey[] = ['constitution', 'dexterity', 'wisdom', 'strength', 'charisma', 'intelligence']

/** The standard array laid out by the calling's priority order. */
export function recommendedScores(label: string | null): PointBuyScores {
  const scores = {} as PointBuyScores
  const key = label ? baseClassKey(label) : ''
  const priority = Object.hasOwn(CLASS_PRESENTATION, key) ? CLASS_PRESENTATION[key].priority : NARRATED_PRIORITY
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
  /** The hero's own threads (`Your own threads` on the Review step), all optional. */
  ties: HeroTiesDraft
  /** The rules the hero is made under: the built-in tables until the server's arrive. */
  rules: CreationRules
}

export function emptyDraft(rules: CreationRules = FALLBACK_RULES): HeroDraft {
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
    ties: tiesDraft(),
    rules,
  }
}

export function heroScores(draft: HeroDraft): PointBuyScores {
  return draft.abilityMode === 'adjust' ? draft.scores : recommendedScores(draft.calling)
}

/** The background in effect: the player's choice, else the one that suits the calling. */
export function heroBackground(draft: HeroDraft) {
  const background = backgroundFromValue(draft.backgroundId ?? heroClass(draft)?.suggestedBackground ?? 'folk_hero')
  return background ? withGrantedSkills(draft.rules, background) : null
}

export function heroFightingStyle(draft: HeroDraft): FightingStyleId | null {
  const options = heroClass(draft)?.fightingStyle
  if (!options) return null
  return draft.fightingStyle && options.options.includes(draft.fightingStyle) ? draft.fightingStyle : options.default
}

/** Class skills the player may choose: the class list minus what the background already grants. */
export function classSkillOptions(draft: HeroDraft): SkillId[] {
  const entry = heroClass(draft)
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

/** How many skills the calling marks for expertise at level 1 (0 for none). */
export function expertiseCount(draft: HeroDraft) {
  return heroClass(draft)?.expertise ?? 0
}

export function needsExpertise(draft: HeroDraft) {
  return expertiseCount(draft) > 0
}

function withoutLostChoices(draft: HeroDraft): HeroDraft {
  const options = new Set(classSkillOptions(draft))
  const skills = draft.skills.filter((skill) => options.has(skill)).slice(0, heroClass(draft)?.skillCount ?? 0)
  const next = { ...draft, skills }
  const proficient = new Set(proficientSkills(next))
  const expertise = draft.expertise.filter((skill) => proficient.has(skill)).slice(0, expertiseCount(next))
  return { ...next, expertise }
}

/** The draft under another rule set (the server's, once they arrive); choices the new rules forbid are dropped. */
export function withRules(draft: HeroDraft, rules: CreationRules): HeroDraft {
  return draft.rules === rules ? draft : withoutLostChoices({ ...draft, rules })
}

export function withCalling(draft: HeroDraft, calling: string): HeroDraft {
  if (draft.calling === calling) return draft
  const previous = heroClass(draft)
  const sameClass = previous !== null && previous.id === coreClassFor(draft.rules, calling)?.id
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
  const count = heroClass(draft)?.skillCount ?? 0
  if (!chosen && (draft.skills.length >= count || !classSkillOptions(draft).includes(skill))) return draft
  const skills = chosen ? draft.skills.filter((entry) => entry !== skill) : [...draft.skills, skill]
  return withoutLostChoices({ ...draft, skills })
}

export function toggleExpertise(draft: HeroDraft, skill: SkillId): HeroDraft {
  const chosen = draft.expertise.includes(skill)
  if (!chosen && (draft.expertise.length >= expertiseCount(draft) || !proficientSkills(draft).includes(skill))) return draft
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
  const { armor, shield } = entry.gear
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
  const { grip } = entry.gear.weapon
  const ability = grip === 'ranged' ? dexterity : grip === 'finesse' ? Math.max(strength, dexterity) : strength
  const archery = styleActive && style === 'archery' && grip === 'ranged' ? 2 : 0
  return ability + PROFICIENCY_BONUS + archery
}

/** HP, AC with the starting kit, attack with the main weapon and spell save DC at level 1. */
export function heroNumbers(draft: HeroDraft): HeroNumbers {
  const scores = heroScores(draft)
  const entry = heroClass(draft)
  const hitDie = entry?.hitDie ?? hitDieForClass(draft.calling ?? '')
  const hp = Math.max(1, hitDie + modifier(scores.constitution))
  if (!entry) return { hp, armorClass: null, attack: null, spellSaveDc: null, proficiencyBonus: PROFICIENCY_BONUS }
  const style = heroFightingStyle(draft)
  const styleActive = (entry.fightingStyle?.fromLevel ?? 99) <= 1
  return {
    hp,
    armorClass: armorClass(entry, scores, style, styleActive),
    attack: { weapon: entry.gear.weapon.name, bonus: attackBonus(entry, scores, style, styleActive) },
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
    const entry = heroClass(draft)
    return (
      pointBuyValid(heroScores(draft)) &&
      Boolean(heroBackground(draft)) &&
      (!entry || draft.skills.length === entry.skillCount) &&
      draft.expertise.length === expertiseCount(draft)
    )
  }
  return CREATE_STEPS.slice(0, -1).every((earlier) => stepComplete(draft, earlier)) && !tiesProblem(draft.ties)
}

/** What still stands between this step and Continue, in one short sentence. */
export function stepHint(draft: HeroDraft, step: CreateStep): string {
  if (stepComplete(draft, step)) return ''
  if (step === 'who') return draft.ancestry ? 'Name your hero to continue.' : 'Choose an ancestry to continue.'
  if (step === 'calling') return 'Choose a calling to continue.'
  if (step === 'shape') {
    const entry = heroClass(draft)
    if (!pointBuyValid(heroScores(draft))) return 'Spend no more than 27 points to continue.'
    if (entry && draft.skills.length !== entry.skillCount) {
      return `Choose ${entry.skillCount} class skills to continue.`
    }
    return `Mark ${skillCountPhrase(expertiseCount(draft))} for expertise to continue.`
  }
  return tiesProblem(draft.ties) || 'Finish the earlier steps to begin.'
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
  ties?: HeroTies
}

export function createPayload(draft: HeroDraft): CreateHeroPayload {
  const background = heroBackground(draft)
  if (!stepComplete(draft, 'review') || !draft.ancestry || !draft.calling || !background) {
    throw new Error('The hero is not finished yet.')
  }
  const entry = heroClass(draft)
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
  const ties = tiesPayload(draft.ties)
  if (Object.keys(ties).length) payload.ties = ties
  return payload
}
