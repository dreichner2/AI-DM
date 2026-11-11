import { apiFetch } from '../../api'
import type { CreationOptionsResponse } from '../../apiContract.generated'
import type { PointBuyAbilityKey } from '../../characterStats'

/*
 * The rules a level-1 hero is made under, owned by the server and read from
 * GET /api/rules/creation-options: class saves, hit dice, class skills and how
 * many to choose, fighting styles, expertise, starting kits, level-1 spells,
 * features, and the skills each background grants.
 *
 * The tables below are only the offline fallback. creationOptions.fixture.json
 * records the server's answer (re-record it whenever the server's tables change)
 * and creationRules.test.ts keeps the two equal.
 * The answer is typed by the API contract (CreationOptionsResponse) but still
 * read defensively, field by field, so an older server never breaks creation.
 */

export const CREATION_OPTIONS_PATH = '/api/rules/creation-options'

type AbilityKey = PointBuyAbilityKey

const ABILITY_KEYS: Record<string, AbilityKey> = {
  str: 'strength',
  strength: 'strength',
  dex: 'dexterity',
  dexterity: 'dexterity',
  con: 'constitution',
  constitution: 'constitution',
  int: 'intelligence',
  intelligence: 'intelligence',
  wis: 'wisdom',
  wisdom: 'wisdom',
  cha: 'charisma',
  charisma: 'charisma',
}

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
export const SKILL_IDS = Object.keys(SKILLS) as SkillId[]

function isSkillId(value: string): value is SkillId {
  return value in SKILLS
}

export function skillName(skill: string) {
  return isSkillId(skill) ? SKILLS[skill].name : skill.replace(/_/g, ' ')
}

// ---- the rules -------------------------------------------------------------

const FIGHTING_STYLE_IDS = ['archery', 'defense', 'dueling', 'great_weapon'] as const
export type FightingStyleId = (typeof FIGHTING_STYLE_IDS)[number]

function isFightingStyleId(value: string): value is FightingStyleId {
  return (FIGHTING_STYLE_IDS as readonly string[]).includes(value)
}

export type RuleFeature = { name: string; text: string }
export type LaterRuleFeature = RuleFeature & { level: number }

export type ClassRules = {
  id: string
  name: string
  hitDie: number
  saves: AbilityKey[]
  skillCount: number
  skills: SkillId[]
  /** Only for classes that choose one. */
  fightingStyle?: { fromLevel: number; options: FightingStyleId[] }
  /** How many proficient skills get expertise at level 1; 0 for none. */
  expertise: number
  kit: string[]
  cantrips: string[]
  spells: string[]
  start: RuleFeature[]
  later: LaterRuleFeature[]
}

export type BackgroundRules = { id: string; skills: SkillId[] }

export type CreationRules = {
  /** The twelve core classes, in the order the Calling step lists them. */
  classes: ClassRules[]
  backgrounds: BackgroundRules[]
}

const ALL_SKILLS = SKILL_IDS
const EXTRA_ATTACK: LaterRuleFeature = { level: 5, name: 'Extra Attack', text: 'Two attacks with every Attack action.' }

export const FALLBACK_RULES: CreationRules = {
  classes: [
    {
      id: 'barbarian',
      name: 'Barbarian',
      hitDie: 12,
      saves: ['strength', 'constitution'],
      skillCount: 2,
      skills: ['animal_handling', 'athletics', 'intimidation', 'nature', 'perception', 'survival'],
      expertise: 0,
      kit: ['Greataxe', 'Handaxe', 'Javelin', "Explorer's Pack", 'Ration', 'Potion of Healing'],
      cantrips: [],
      spells: [],
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
        EXTRA_ATTACK,
      ],
    },
    {
      id: 'fighter',
      name: 'Fighter',
      hitDie: 10,
      saves: ['strength', 'constitution'],
      skillCount: 2,
      skills: ['acrobatics', 'animal_handling', 'athletics', 'history', 'insight', 'intimidation', 'perception', 'survival'],
      fightingStyle: { fromLevel: 1, options: ['defense', 'dueling', 'great_weapon', 'archery'] },
      expertise: 0,
      kit: ['Longsword', 'Shield', 'Chain Mail', "Explorer's Pack", 'Ration', 'Torch', 'Potion of Healing'],
      cantrips: [],
      spells: [],
      start: [
        { name: 'Second Wind', text: 'Once per short rest, as a bonus action, regain 1d10 + your level in hit points.' },
        { name: 'Fighting Style', text: 'A trained way of fighting, chosen below, that the rules apply in every fight.' },
      ],
      later: [
        { level: 2, name: 'Action Surge', text: 'Once per short rest, take one more action on your turn.' },
        { level: 3, name: 'Improved Critical', text: 'As a Champion, you score critical hits on 19 and 20.' },
        EXTRA_ATTACK,
      ],
    },
    {
      id: 'monk',
      name: 'Monk',
      hitDie: 8,
      saves: ['strength', 'dexterity'],
      skillCount: 2,
      skills: ['acrobatics', 'athletics', 'history', 'insight', 'religion', 'stealth'],
      expertise: 0,
      kit: ['Quarterstaff', 'Dart', "Explorer's Pack", 'Ration', 'Potion of Healing'],
      cantrips: [],
      spells: [],
      start: [
        { name: 'Martial Arts Strike', text: 'After you take the Attack action, strike again unarmed as a bonus action.' },
        { name: 'Unarmored Defense', text: 'Without armour or a shield your AC is 10 + Dexterity + Wisdom.' },
      ],
      later: [
        { level: 2, name: 'Flurry of Blows', text: 'Spend 1 ki after the Attack action for two unarmed strikes as a bonus action.' },
        { level: 2, name: 'Patient Defense', text: 'Spend 1 ki to Dodge as a bonus action.' },
        { level: 2, name: 'Step of the Wind', text: 'Spend 1 ki to Dash or Disengage as a bonus action.' },
        { level: 5, name: 'Stunning Strike', text: 'Spend ki on a hit to stun the target until your next turn ends.' },
        EXTRA_ATTACK,
      ],
    },
    {
      id: 'paladin',
      name: 'Paladin',
      hitDie: 10,
      saves: ['wisdom', 'charisma'],
      skillCount: 2,
      skills: ['athletics', 'insight', 'intimidation', 'medicine', 'persuasion', 'religion'],
      fightingStyle: { fromLevel: 2, options: ['defense', 'dueling', 'great_weapon'] },
      expertise: 0,
      kit: ['Longsword', 'Shield', 'Chain Mail', 'Holy Symbol', "Priest's Pack", 'Potion of Healing'],
      cantrips: ['Amber Knot'],
      spells: ['Obsidian Lash', 'Silver Charm'],
      start: [
        { name: 'Lay on Hands', text: 'A pool of 5 hit points per level to heal by touch, or 5 points to cure poison.' },
      ],
      later: [
        { level: 2, name: 'Divine Smite', text: 'Spend a spell slot on a hit for 2d8 radiant damage or more.' },
        { level: 2, name: 'Fighting Style', text: 'The style you choose now starts to apply.' },
        { level: 2, name: 'Spellcasting', text: 'Bless, Cure Wounds, Shield of Faith and other oaths in prayer.' },
        EXTRA_ATTACK,
        { level: 5, name: '2nd-level spells', text: 'Aid and Lesser Restoration.' },
      ],
    },
    {
      id: 'ranger',
      name: 'Ranger',
      hitDie: 10,
      saves: ['strength', 'dexterity'],
      skillCount: 3,
      skills: ['animal_handling', 'athletics', 'insight', 'investigation', 'nature', 'perception', 'stealth', 'survival'],
      fightingStyle: { fromLevel: 2, options: ['archery', 'defense', 'dueling'] },
      expertise: 0,
      kit: ['Longbow', 'Shortsword', 'Leather Armor', 'Arrow', "Explorer's Pack", 'Potion of Healing'],
      cantrips: ['Crystal Mote', 'Marble Whisper'],
      spells: ['Sapphire Lash'],
      start: [],
      later: [
        { level: 2, name: 'Fighting Style', text: 'The style you choose now starts to apply.' },
        { level: 2, name: 'Spellcasting', text: "Hunter's Mark, Cure Wounds, Entangle and other wild magic." },
        { level: 3, name: 'Colossus Slayer', text: 'As a Hunter, once per turn +1d8 damage to a wounded foe.' },
        EXTRA_ATTACK,
        { level: 5, name: '2nd-level spells', text: 'Barkskin, Lesser Restoration and Pass without Trace.' },
      ],
    },
    {
      id: 'rogue',
      name: 'Rogue',
      hitDie: 8,
      saves: ['dexterity', 'intelligence'],
      skillCount: 4,
      skills: [
        'acrobatics', 'athletics', 'deception', 'insight', 'intimidation', 'investigation', 'perception',
        'performance', 'persuasion', 'sleight_of_hand', 'stealth',
      ],
      expertise: 2,
      kit: ['Rapier', 'Dagger', 'Leather Armor', "Thieves' Tools", "Burglar's Pack", 'Potion of Healing'],
      cantrips: [],
      spells: [],
      start: [
        {
          name: 'Sneak Attack',
          text: 'Once per turn, +1d6 damage with a finesse or ranged weapon when you have advantage or an ally is beside your target.',
        },
        { name: 'Expertise', text: 'Double proficiency in two skills you choose.' },
      ],
      later: [
        { level: 2, name: 'Cunning Action', text: 'Dash, Disengage or Hide as a bonus action.' },
        { level: 5, name: 'Uncanny Dodge', text: 'Your reaction halves the damage of an attack that hits you.' },
      ],
    },
    {
      id: 'bard',
      name: 'Bard',
      hitDie: 8,
      saves: ['dexterity', 'charisma'],
      skillCount: 3,
      skills: ALL_SKILLS,
      expertise: 0,
      kit: ['Rapier', 'Dagger', 'Leather Armor', 'Lute', "Diplomat's Pack", 'Potion of Healing'],
      cantrips: ['Dancing Lights', 'Light', 'Mage Hand', 'Message', 'Minor Illusion', 'Vicious Mockery'],
      spells: [
        'Bane', 'Charm Person', 'Cure Wounds', 'Dissonant Whispers', 'Faerie Fire', 'Healing Word', 'Heroism',
        'Sleep', "Tasha's Hideous Laughter", 'Thunderwave', 'Silver Charm', 'Frostlace Veil', 'Ivory Lens',
      ],
      start: [
        {
          name: 'Bardic Inspiration',
          text: 'As a bonus action, give an ally a d6 for a roll that would fail; Charisma-many uses per long rest.',
        },
        { name: 'Spellcasting', text: 'Charming, healing and baffling magic, sung or spoken.' },
      ],
      later: [
        { level: 2, name: 'Jack of All Trades', text: 'Half your proficiency bonus on checks you are untrained in.' },
        { level: 3, name: '2nd-level spells', text: 'Invisibility, Hold Person, Shatter and more.' },
        { level: 5, name: '3rd-level spells', text: 'Hypnotic Pattern, Fear, Dispel Magic and more.' },
      ],
    },
    {
      id: 'cleric',
      name: 'Cleric',
      hitDie: 8,
      saves: ['wisdom', 'charisma'],
      skillCount: 2,
      skills: ['history', 'insight', 'medicine', 'persuasion', 'religion'],
      expertise: 0,
      kit: ['Mace', 'Shield', 'Scale Mail', 'Holy Symbol', "Priest's Pack", 'Potion of Healing', "Healer's Kit"],
      cantrips: [
        'Guidance', 'Light', 'Sacred Flame', 'Spare the Dying', 'Thaumaturgy', 'Toll the Dead', 'Ravenous Knot',
        'Saffron Spark', 'Scarab Mote',
      ],
      spells: [
        'Bane', 'Bless', 'Command', 'Cure Wounds', 'Detect Magic', 'Guiding Bolt', 'Healing Word', 'Inflict Wounds',
        'Shield of Faith',
      ],
      start: [{ name: 'Spellcasting', text: 'Divine magic prepared each day from your god.' }],
      later: [
        { level: 2, name: 'Turn Undead', text: 'Once per short rest, undead that fail a Wisdom save flee from you.' },
        { level: 3, name: '2nd-level spells', text: 'Spiritual Weapon, Aid, Lesser Restoration and more.' },
        { level: 5, name: '3rd-level spells', text: 'Spirit Guardians, Revivify, Mass Healing Word and more.' },
      ],
    },
    {
      id: 'druid',
      name: 'Druid',
      hitDie: 8,
      saves: ['intelligence', 'wisdom'],
      skillCount: 2,
      skills: ['animal_handling', 'arcana', 'insight', 'medicine', 'nature', 'perception', 'religion', 'survival'],
      expertise: 0,
      kit: ['Scimitar', 'Wooden Shield', 'Leather Armor', 'Druidic Focus', "Explorer's Pack", 'Potion of Healing', "Healer's Kit"],
      cantrips: ['Druidcraft', 'Guidance', 'Poison Spray', 'Produce Flame', 'Thorn Whip', 'Mire Knot', 'Mire Palm', 'Quartz Mote'],
      spells: [
        'Cure Wounds', 'Entangle', 'Faerie Fire', 'Goodberry', 'Healing Word', 'Primal Shift', 'Speak with Animals',
        'Thunderwave',
      ],
      start: [{ name: 'Spellcasting', text: 'Primal magic prepared each day from the living world.' }],
      later: [
        { level: 3, name: '2nd-level spells', text: 'Moonbeam, Barkskin, Pass without Trace and more.' },
        { level: 5, name: '3rd-level spells', text: 'Call Lightning and Dispel Magic.' },
      ],
    },
    {
      id: 'sorcerer',
      name: 'Sorcerer',
      hitDie: 6,
      saves: ['constitution', 'charisma'],
      skillCount: 2,
      skills: ['arcana', 'deception', 'insight', 'intimidation', 'persuasion', 'religion'],
      expertise: 0,
      kit: ['Dagger', 'Light Crossbow', 'Arcane Focus', "Explorer's Pack", 'Potion of Healing'],
      cantrips: [
        'Chill Touch', 'Dancing Lights', 'Fire Bolt', 'Light', 'Mage Hand', 'Message', 'Minor Illusion',
        'Poison Spray', 'Ray of Frost', 'Shocking Grasp', 'Brine Glimmer',
      ],
      spells: [
        'Burning Hands', 'Chaos Bolt', 'Chromatic Orb', 'False Life', 'Mage Armor', 'Magic Missile', 'Shield', 'Sleep',
        'Thunderwave', 'Crow Ward', 'Candle Ward',
      ],
      start: [{ name: 'Spellcasting', text: 'Innate magic you always know, cast on instinct.' }],
      later: [
        { level: 2, name: 'Flexible Casting', text: 'Turn spell slots into sorcery points and points back into slots.' },
        { level: 3, name: 'Quickened Spell', text: 'Spend 2 sorcery points to cast an action spell as a bonus action.' },
        { level: 3, name: 'Twinned Spell', text: 'Spend sorcery points to aim a single-target spell at a second target.' },
        { level: 3, name: '2nd-level spells', text: 'Misty Step, Scorching Ray, Hold Person and more.' },
        { level: 5, name: '3rd-level spells', text: 'Fireball, Lightning Bolt, Haste and more.' },
      ],
    },
    {
      id: 'warlock',
      name: 'Warlock',
      hitDie: 8,
      saves: ['wisdom', 'charisma'],
      skillCount: 2,
      skills: ['arcana', 'deception', 'history', 'intimidation', 'investigation', 'nature', 'religion'],
      expertise: 0,
      kit: ['Dagger', 'Light Crossbow', 'Leather Armor', 'Arcane Focus', "Scholar's Pack", 'Potion of Healing'],
      cantrips: ['Chill Touch', 'Eldritch Blast', 'Mage Hand', 'Minor Illusion', 'Poison Spray', 'Scarab Glimmer', 'Moon Palm'],
      spells: ['Armor of Agathys', 'Charm Person', 'Hellish Rebuke', 'Hex', 'Lilac Step'],
      start: [{ name: 'Pact Magic', text: 'A few spells, cast from slots that return on a short rest.' }],
      later: [
        { level: 2, name: 'Agonizing Blast', text: 'Add your Charisma to every Eldritch Blast beam.' },
        { level: 3, name: '2nd-level spells', text: 'Misty Step, Mirror Image, Hold Person and more.' },
        { level: 5, name: '3rd-level spells', text: 'Counterspell, Fly, Hypnotic Pattern and more.' },
      ],
    },
    {
      id: 'wizard',
      name: 'Wizard',
      hitDie: 6,
      saves: ['intelligence', 'wisdom'],
      skillCount: 2,
      skills: ['arcana', 'history', 'insight', 'investigation', 'medicine', 'religion'],
      expertise: 0,
      kit: ['Quarterstaff', 'Dagger', 'Spellbook', 'Component Pouch', "Scholar's Pack", 'Potion of Healing'],
      cantrips: [
        'Chill Touch', 'Dancing Lights', 'Fire Bolt', 'Light', 'Mage Hand', 'Message', 'Prestidigitation',
        'Ray of Frost', 'Shocking Grasp', 'Frostlace Palm',
      ],
      spells: [
        'Burning Hands', 'Chromatic Orb', 'Detect Magic', 'False Life', 'Mage Armor', 'Magic Missile', 'Shield',
        'Sleep', 'Thunderwave', 'Copper Charm', 'Verdigris Brand',
      ],
      start: [
        { name: 'Arcane Recovery', text: 'Once per long rest, after a short rest, recover spent spell slots.' },
        { name: 'Spellcasting', text: 'Arcane magic prepared each day from your spellbook.' },
      ],
      later: [
        { level: 3, name: '2nd-level spells', text: 'Misty Step, Scorching Ray, Web and more.' },
        { level: 5, name: '3rd-level spells', text: 'Fireball, Counterspell, Fly and more.' },
      ],
    },
  ],
  backgrounds: [
    { id: 'acolyte', skills: ['insight', 'religion'] },
    { id: 'criminal', skills: ['deception', 'stealth'] },
    { id: 'folk_hero', skills: ['animal_handling', 'survival'] },
    { id: 'guild_artisan', skills: ['insight', 'persuasion'] },
    { id: 'sage', skills: ['arcana', 'history'] },
    { id: 'soldier', skills: ['athletics', 'intimidation'] },
  ],
}

// ---- reading the server's answer -------------------------------------------

type RawRecord = Record<string, unknown>

function isRecord(value: unknown): value is RawRecord {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown) {
  return typeof value === 'string' ? value.trim() : ''
}

function idKey(value: unknown) {
  return text(value).toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')
}

function wholeNumber(value: unknown, min: number, max: number) {
  return typeof value === 'number' && Number.isInteger(value) && value >= min && value <= max ? value : null
}

function records(value: unknown) {
  return Array.isArray(value) ? value.filter(isRecord) : []
}

/** Item or spell names: plain strings, or records with a name (and a count above one). */
function names(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null
  return value.flatMap((entry) => {
    if (typeof entry === 'string') return entry.trim() ? [entry.trim()] : []
    if (!isRecord(entry) || !text(entry.name)) return []
    const quantity = wholeNumber(entry.quantity, 2, 999)
    return [quantity ? `${text(entry.name)} ×${quantity}` : text(entry.name)]
  })
}

function skillList(value: unknown): SkillId[] | null {
  if (!Array.isArray(value)) return null
  return value.map(idKey).filter(isSkillId)
}

function abilityList(value: unknown): AbilityKey[] | null {
  if (!Array.isArray(value)) return null
  const abilities = value.map((entry) => ABILITY_KEYS[idKey(entry)]).filter((ability): ability is AbilityKey => Boolean(ability))
  return abilities.length ? abilities : null
}

function startFeatures(value: unknown): RuleFeature[] | null {
  if (!Array.isArray(value)) return null
  return records(value)
    .filter((entry) => text(entry.name))
    .map((entry) => ({ name: text(entry.name), text: text(entry.summary) || text(entry.text) }))
}

function laterFeatures(value: unknown): LaterRuleFeature[] | null {
  if (!Array.isArray(value)) return null
  return records(value).flatMap((entry) => {
    const level = wholeNumber(entry.level, 2, 20)
    if (level === null || !text(entry.name)) return []
    return [{ level, name: text(entry.name), text: text(entry.summary) || text(entry.text) }]
  })
}

/** A class without the field has no fighting style; an unreadable one keeps the fallback. */
function fightingStyleFrom(value: unknown, fallback: ClassRules['fightingStyle']): ClassRules['fightingStyle'] {
  if (value === undefined || value === null) return undefined
  const record = isRecord(value) ? value : { options: value }
  const options = Array.isArray(record.options) ? record.options.map(idKey).filter(isFightingStyleId) : []
  if (!options.length) return fallback
  return { fromLevel: wholeNumber(record.level ?? record.fromLevel, 1, 20) ?? fallback?.fromLevel ?? 1, options }
}

/** A class without the field has no expertise; an unreadable one keeps the fallback. */
function expertiseFrom(value: unknown, fallback: number) {
  if (value === undefined || value === null) return 0
  return wholeNumber(isRecord(value) ? value.count : value, 0, 18) ?? fallback
}

function classFromResponse(raw: RawRecord, base: ClassRules): ClassRules {
  const choices = isRecord(raw.skillChoices) ? raw.skillChoices : {}
  const options = skillList(choices.options)
  const count = wholeNumber(choices.count, 0, SKILL_IDS.length)
  const skillsReadable = options !== null && count !== null && count <= options.length
  const next: ClassRules = {
    id: base.id,
    name: text(raw.name) || base.name,
    hitDie: wholeNumber(raw.hitDie, 4, 20) ?? base.hitDie,
    saves: abilityList(raw.savingThrows) ?? base.saves,
    skillCount: skillsReadable ? count : base.skillCount,
    skills: skillsReadable ? options : base.skills,
    expertise: expertiseFrom(raw.expertise, base.expertise),
    kit: names(raw.startingKit) ?? base.kit,
    cantrips: names(raw.cantrips) ?? base.cantrips,
    spells: names(raw.spells) ?? base.spells,
    start: startFeatures(raw.levelOneFeatures) ?? base.start,
    later: laterFeatures(raw.laterFeatures) ?? base.later,
  }
  const fightingStyle = fightingStyleFrom(raw.fightingStyles, base.fightingStyle)
  return fightingStyle ? { ...next, fightingStyle } : next
}

/**
 * The rules with the server's answer laid over `base`, field by field: what the
 * server sends replaces the table, what it omits or garbles keeps it, and
 * classes or backgrounds the client cannot present are ignored. Null when the
 * answer holds nothing usable.
 */
export function rulesFromResponse(payload: unknown, base: CreationRules = FALLBACK_RULES): CreationRules | null {
  if (!isRecord(payload)) return null
  const rawClasses = new Map(records(payload.classes).map((entry) => [idKey(entry.id), entry]))
  const rawBackgrounds = new Map(records(payload.backgrounds).map((entry) => [idKey(entry.id), entry]))
  let used = false
  const classes = base.classes.map((entry) => {
    const raw = rawClasses.get(entry.id)
    if (!raw) return entry
    used = true
    return classFromResponse(raw, entry)
  })
  const backgrounds = base.backgrounds.map((entry) => {
    const raw = rawBackgrounds.get(entry.id)
    const skills = raw ? skillList(raw.skills ?? raw.skillProficiencies ?? raw.grantedSkills) : null
    if (!skills) return entry
    used = true
    return { ...entry, skills }
  })
  return used ? { classes, backgrounds } : null
}

/** The server's creation rules, or null when it cannot be reached or answers with nothing usable. */
export async function loadCreationRules(baseUrl: string, auth: string): Promise<CreationRules | null> {
  try {
    return rulesFromResponse(await apiFetch<CreationOptionsResponse>(baseUrl, CREATION_OPTIONS_PATH, auth))
  } catch {
    return null
  }
}
