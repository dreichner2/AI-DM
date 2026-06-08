type SexKey = 'female' | 'male'
type RaceKey =
  | 'aarakocra'
  | 'aasimar'
  | 'bugbear'
  | 'changeling'
  | 'dragonborn'
  | 'dwarf'
  | 'elf'
  | 'fairy'
  | 'firbolg'
  | 'genasi'
  | 'gnome'
  | 'goblin'
  | 'goliath'
  | 'halfling'
  | 'harengon'
  | 'hobgoblin'
  | 'human'
  | 'kenku'
  | 'kobold'
  | 'lizardfolk'
  | 'minotaur'
  | 'orc'
  | 'satyr'
  | 'shifter'
  | 'tabaxi'
  | 'tiefling'
  | 'tortle'
  | 'triton'
  | 'warforged'
  | 'yuan-ti'

type SexedIcons = Record<SexKey, string>

export type CharacterProfileIconInput = {
  race?: string | null
  sex?: string | null
  seed?: string | null
}

const PROFILE_ICON_FILES: Record<RaceKey, SexedIcons> = {
  aarakocra: {
    male: '19_Aarakocra_male.png',
    female: '20_Aarakocra_female.png',
  },
  aasimar: {
    male: '01 - Aasimar (Male).png',
    female: '02 - Aasimar (Female).png',
  },
  bugbear: {
    male: '15 - Bugbear (Male).png',
    female: '16 - Bugbear (Female).png',
  },
  changeling: {
    male: '09 - Changeling (Male).png',
    female: '10 - Changeling (Female).png',
  },
  dragonborn: {
    male: '03 - Dragonborn (Male).png',
    female: '04 - Dragonborn (Female).png',
  },
  dwarf: {
    male: 'dwarf_male.png',
    female: 'dwarf_female.png',
  },
  elf: {
    male: 'elf_male.png',
    female: 'elf_female.png',
  },
  fairy: {
    male: '19 - Fairy (Male).png',
    female: '20 - Fairy (Female).png',
  },
  firbolg: {
    male: '03_Firbolg_male.png',
    female: '04_Firbolg_female.png',
  },
  genasi: {
    male: '07 - Genasi (Male).png',
    female: '08 - Genasi (Female).png',
  },
  gnome: {
    male: '05 - Gnome (Male).png',
    female: '06 - Gnome (Female).png',
  },
  goblin: {
    male: '07_Goblin_male.png',
    female: '08_Goblin_female.png',
  },
  goliath: {
    male: '05_Goliath_male.png',
    female: '06_Goliath_female.png',
  },
  halfling: {
    male: 'halfling_male.png',
    female: 'halfling_female.png',
  },
  harengon: {
    male: '21 - Harengon (Male).png',
    female: '22 - Harengon (Female).png',
  },
  hobgoblin: {
    male: '17 - Hobgoblin (Male).png',
    female: '18 - Hobgoblin (Female).png',
  },
  human: {
    male: 'human_male.png',
    female: 'human_female.png',
  },
  kenku: {
    male: '11_Kenku_male.png',
    female: '12_Kenku_female.png',
  },
  kobold: {
    male: '09_Kobold_male.png',
    female: '10_Kobold_female.png',
  },
  lizardfolk: {
    male: '13 - Lizardfolk (Male).png',
    female: '14 - Lizardfolk (Female).png',
  },
  minotaur: {
    male: '23 - Minotaur (Male).png',
    female: '24 - Minotaur (Female).png',
  },
  orc: {
    male: 'orc_male.png',
    female: 'orc_female.png',
  },
  satyr: {
    male: '17_Satyr_male.png',
    female: '18_Satyr_female.png',
  },
  shifter: {
    male: '11 - Shifter (Male).png',
    female: '12 - Shifter (Female).png',
  },
  tabaxi: {
    male: '01_Tabaxi_male.png',
    female: '02_Tabaxi_female.png',
  },
  tiefling: {
    male: 'tiefling_male.png',
    female: 'tiefling_female.png',
  },
  tortle: {
    male: '21_Tortle_male.png',
    female: '22_Tortle_female.png',
  },
  triton: {
    male: '13_Triton_male.png',
    female: '14_Triton_female.png',
  },
  warforged: {
    male: '23_Warforged_male.png',
    female: '24_Warforged_female.png',
  },
  'yuan-ti': {
    male: '15_Yuan-ti_male.png',
    female: '16_Yuan-ti_female.png',
  },
}

const RACE_ALIASES: Record<RaceKey, string[]> = {
  aarakocra: ['aarakocra', 'bird', 'birdfolk', 'bird person', 'eagle', 'hawk', 'avian'],
  aasimar: ['aasimar', 'angel', 'angelic', 'celestial', 'divine', 'heavenborn'],
  bugbear: ['bugbear', 'hairy goblin', 'large goblin'],
  changeling: ['changeling', 'shapechanger', 'shapeshifter', 'doppelganger'],
  dragonborn: ['dragonborn', 'dragon', 'dragon person', 'draconic', 'draconian'],
  dwarf: ['dwarf', 'dwarven', 'mountain dwarf', 'hill dwarf'],
  elf: ['elf', 'elven', 'drow', 'dark elf', 'wood elf', 'high elf', 'half elf', 'half-elf'],
  fairy: ['fairy', 'fae', 'fey', 'pixie', 'sprite'],
  firbolg: ['firbolg', 'forest giant', 'nature giant'],
  genasi: ['genasi', 'elemental', 'fire genasi', 'water genasi', 'earth genasi', 'air genasi'],
  gnome: ['gnome', 'gnomish', 'deep gnome', 'rock gnome', 'forest gnome'],
  goblin: ['goblin', 'gobbo'],
  goliath: ['goliath', 'giant', 'giantkin', 'stone giant', 'big strong'],
  halfling: ['halfling', 'hobbit', 'small folk', 'little folk'],
  harengon: ['harengon', 'rabbit', 'rabbitfolk', 'bunny', 'hare'],
  hobgoblin: ['hobgoblin', 'hob goblin', 'military goblin'],
  human: ['human', 'humanoid', 'mortal'],
  kenku: ['kenku', 'crow', 'raven', 'corvid', 'flightless bird'],
  kobold: ['kobold', 'little dragon', 'tiny dragon', 'small dragon'],
  lizardfolk: ['lizardfolk', 'lizard', 'lizard person', 'reptile', 'reptilian'],
  minotaur: ['minotaur', 'bull', 'bull man', 'cow person'],
  orc: ['orc', 'orcish', 'half orc', 'half-orc', 'green skin', 'greenskin'],
  satyr: ['satyr', 'faun', 'goat', 'goat person'],
  shifter: ['shifter', 'werewolf', 'lycan', 'lycanthrope', 'beastfolk', 'beast folk'],
  tabaxi: ['tabaxi', 'cat', 'catfolk', 'cat folk', 'cat person', 'feline'],
  tiefling: [
    'tiefling',
    'demon',
    'half demon',
    'half-demon',
    'devil',
    'fiend',
    'infernal',
    'demonic',
    'hellborn',
    'hellspawn',
  ],
  tortle: ['tortle', 'turtle', 'turtlefolk', 'turtle person', 'tortoise'],
  triton: ['triton', 'merfolk', 'merman', 'mermaid', 'sea elf', 'ocean', 'sea person'],
  warforged: ['warforged', 'robot', 'construct', 'machine', 'automaton', 'metal person'],
  'yuan-ti': ['yuan-ti', 'yuan ti', 'snake', 'serpent', 'snake person'],
}

const FEMALE_ALIASES = new Set(['female', 'f', 'woman', 'girl', 'she', 'her', 'lady'])
const MALE_ALIASES = new Set(['male', 'm', 'man', 'boy', 'he', 'him', 'guy'])

function normalizeText(value: string) {
  return value
    .toLowerCase()
    .replace(/['’]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

function compactText(value: string) {
  return normalizeText(value).replace(/\s+/g, '')
}

function editDistance(left: string, right: string) {
  if (left === right) return 0
  if (!left) return right.length
  if (!right) return left.length
  const previous = Array.from({ length: right.length + 1 }, (_, index) => index)
  const current = Array.from({ length: right.length + 1 }, () => 0)
  for (let leftIndex = 1; leftIndex <= left.length; leftIndex += 1) {
    current[0] = leftIndex
    for (let rightIndex = 1; rightIndex <= right.length; rightIndex += 1) {
      const cost = left[leftIndex - 1] === right[rightIndex - 1] ? 0 : 1
      current[rightIndex] = Math.min(
        current[rightIndex - 1] + 1,
        previous[rightIndex] + 1,
        previous[rightIndex - 1] + cost,
      )
    }
    previous.splice(0, previous.length, ...current)
  }
  return previous[right.length]
}

function scoreAlias(input: string, alias: string) {
  const inputText = normalizeText(input)
  const aliasText = normalizeText(alias)
  const inputCompact = compactText(inputText)
  const aliasCompact = compactText(aliasText)
  if (!inputCompact || !aliasCompact) return 0
  if (inputCompact === aliasCompact) return 100
  if (inputCompact.includes(aliasCompact) || aliasCompact.includes(inputCompact)) {
    return Math.min(94, 68 + Math.min(inputCompact.length, aliasCompact.length) * 2)
  }

  const inputTokens = new Set(inputText.split(/\s+/).filter(Boolean))
  const aliasTokens = aliasText.split(/\s+/).filter(Boolean)
  const overlap = aliasTokens.filter((token) => inputTokens.has(token)).length
  const tokenScore = overlap > 0 ? 58 + overlap * 12 : 0
  if (inputCompact.length < 4 || aliasCompact.length < 4) return tokenScore

  const longest = Math.max(inputCompact.length, aliasCompact.length)
  const typoScore = Math.round(70 - (editDistance(inputCompact, aliasCompact) / longest) * 45)
  return Math.max(tokenScore, typoScore)
}

export function profileIconRaceForCharacter(race?: string | null): RaceKey | null {
  const input = race?.trim()
  if (!input) return null

  let best: { race: RaceKey; score: number } | null = null
  for (const [raceKey, aliases] of Object.entries(RACE_ALIASES) as [RaceKey, string[]][]) {
    for (const alias of aliases) {
      const score = scoreAlias(input, alias)
      if (!best || score > best.score) {
        best = { race: raceKey, score }
      }
    }
  }

  return best && best.score >= 58 ? best.race : null
}

function sexKeyForCharacter(sex: string | null | undefined) {
  const normalized = compactText(sex ?? '')
  if (FEMALE_ALIASES.has(normalized)) return 'female'
  if (MALE_ALIASES.has(normalized)) return 'male'
  return 'male'
}

export function profileIconSrcForCharacter({ race, sex }: CharacterProfileIconInput) {
  const iconRace = profileIconRaceForCharacter(race)
  if (!iconRace) return null
  const selectedSex = sexKeyForCharacter(sex)
  return `/profile-icons/${encodeURIComponent(PROFILE_ICON_FILES[iconRace][selectedSex])}`
}
