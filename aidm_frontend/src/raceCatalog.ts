import {
  RACE_ALIASES,
  profileIconSrcForCharacter,
  type RaceKey,
  type SexKey,
} from './profileIcons'
import type { CharacterRaceSelection } from './types'

export const RACE_FILTERS = [
  'Beginner Friendly',
  'Frontline',
  'Magic',
  'Scout',
  'Face/Social',
  'Wilderness',
  'Durable',
  'Small',
  'Tall/Bulky',
  'Flying',
  'Aquatic',
  'Darkvision',
  'Beastfolk',
  'Monstrous',
  'Fey',
  'Elemental',
  'Draconic',
  'Construct',
] as const

export type RaceCategory = (typeof RACE_FILTERS)[number]
export type RaceDifficulty = 'Easy' | 'Medium' | 'Advanced'

export type RacePhysicalProfile = {
  averageHeight: string
  averageWeight: string
}

export type RaceRelationshipProfile = {
  friendlyWith: string[]
  waryOf: string[]
}

export type RaceProfileDetails = RacePhysicalProfile &
  RaceRelationshipProfile & {
    originStory: string
    languages: string[]
    commonProficiencies: string[]
  }

export type PlayableRace = {
  key: RaceKey
  name: string
  tagline: string
  shortDescription: string
  longDescription: string
  traits: string[]
  mechanicalEffects: string[]
  narrativeFlavor: string
  recommendedClasses: string[]
  recommendedStyles: string[]
  difficulty: RaceDifficulty
  warnings: string[]
  categories: RaceCategory[]
  aliases: string[]
} & RaceProfileDetails

type BasePlayableRace = Omit<PlayableRace, keyof RaceProfileDetails | 'categories'> & {
  categories: string[]
}

type RaceMetadataUpdate = RaceProfileDetails &
  Pick<PlayableRace, 'categories'> &
  Partial<Pick<PlayableRace, 'shortDescription' | 'longDescription'>>

const race = (entry: BasePlayableRace) => entry

const BASE_PLAYABLE_RACES: BasePlayableRace[] = [
  race({
    key: 'aarakocra',
    name: 'Aarakocra',
    tagline: 'Winged avian wanderers born for the open sky.',
    shortDescription: 'Aarakocra are swift birdfolk who scout, snipe, and escape danger from above.',
    longDescription:
      'Aarakocra carry the instincts of high cliffs and open winds. They fit stories about freedom, distance, watchfulness, and sudden dives from impossible angles.',
    traits: ['Flight', 'Keen Sight', 'Lightweight'],
    mechanicalEffects: ['Fly in open spaces when armor and the scene allow it.', 'Strong scouting and vertical mobility.', 'Fragile positioning if grounded or confined.'],
    narrativeFlavor: 'Describe feathers, talons, sharp head movements, wind habits, and discomfort in cramped rooms.',
    recommendedClasses: ['Ranger', 'Rogue', 'Monk'],
    recommendedStyles: ['Scout', 'Skirmisher', 'Archer'],
    difficulty: 'Medium',
    warnings: ['Flight can change encounter balance in open areas.'],
    categories: ['Flying', 'Nature', 'Exotic', 'Stealthy'],
    aliases: ['birdfolk', 'avian', 'eaglefolk', 'hawkfolk', 'flyer'],
  }),
  race({
    key: 'aasimar',
    name: 'Aasimar',
    tagline: 'Mortals touched by celestial light and impossible expectations.',
    shortDescription: 'Aasimar blend divine presence, radiant power, and a strong heroic or tragic tone.',
    longDescription:
      'Aasimar are ideal for characters marked by omens, guardians, prophecy, or a burden to be better than they feel. Their power can feel holy, eerie, or painfully visible.',
    traits: ['Radiant Soul', 'Celestial Resistance', 'Healing Hands'],
    mechanicalEffects: ['Resist radiant and necrotic themes.', 'Gain a small emergency heal.', 'Can lean into radiant burst moments.'],
    narrativeFlavor: 'Mention luminous eyes, faint halos, soft heat, angelic signs, and pressure from divine attention.',
    recommendedClasses: ['Paladin', 'Cleric', 'Warlock'],
    recommendedStyles: ['Face', 'Protector', 'Divine caster'],
    difficulty: 'Medium',
    warnings: ['The celestial theme can pull the story toward destiny or morality.'],
    categories: ['Magical', 'Social', 'Durable', 'Exotic'],
    aliases: ['angelborn', 'celestial', 'divine'],
  }),
  race({
    key: 'bugbear',
    name: 'Bugbear',
    tagline: 'Long-limbed ambushers with brutal reach and quiet menace.',
    shortDescription: 'Bugbears are large, stealthy bruisers who hit hard from unexpected places.',
    longDescription:
      'Bugbears are built for players who want a dangerous physical presence without giving up sneaking, patience, or cunning. They are excellent for ambush stories.',
    traits: ['Long-Limbed', 'Ambusher', 'Powerful Build'],
    mechanicalEffects: ['Better reach and surprise pressure.', 'Strong melee opening turns.', 'Useful carrying and grappling presence.'],
    narrativeFlavor: 'Describe heavy shoulders, silent footfalls, long arms, and a predator calm that unnerves people.',
    recommendedClasses: ['Fighter', 'Rogue', 'Barbarian'],
    recommendedStyles: ['Ambusher', 'Bruiser', 'Grappler'],
    difficulty: 'Medium',
    warnings: ['Some settlements may react with suspicion or fear.'],
    categories: ['Martial', 'Stealthy', 'Monstrous', 'Large', 'Darkvision'],
    aliases: ['large goblin', 'hairy goblin', 'ambusher'],
  }),
  race({
    key: 'changeling',
    name: 'Changeling',
    tagline: 'Shapeshifting social ghosts with borrowed faces.',
    shortDescription: 'Changelings thrive in intrigue, disguise, deception, and identity-driven stories.',
    longDescription:
      'Changelings are strongest when the campaign has secrets, social pressure, false identities, or mystery. They let a player explore who a character is when any face is possible.',
    traits: ['Shapechanger', 'Silver Tongue', 'Identity Craft'],
    mechanicalEffects: ['Change appearance for infiltration and performance.', 'Excellent social flexibility.', 'Can complicate recognition and trust.'],
    narrativeFlavor: 'Describe features settling like wet clay, careful posture copying, and names treated like masks.',
    recommendedClasses: ['Bard', 'Rogue', 'Warlock'],
    recommendedStyles: ['Face', 'Infiltrator', 'Mystery lead'],
    difficulty: 'Advanced',
    warnings: ['Needs table comfort with disguise, identity, and deception themes.'],
    categories: ['Social', 'Stealthy', 'Magical', 'Exotic'],
    aliases: ['shapechanger', 'shapeshifter', 'doppelganger'],
  }),
  race({
    key: 'dragonborn',
    name: 'Dragonborn',
    tagline: 'Proud draconic warriors with elemental breath.',
    shortDescription: 'Dragonborn carry draconic ancestry, elemental resistance, and commanding presence.',
    longDescription:
      'Dragonborn suit bold characters with ancestry, honor, rivalry, and elemental spectacle at the center of their identity. Their heritage is visible and hard to ignore.',
    traits: ['Breath Weapon', 'Elemental Resistance', 'Commanding'],
    mechanicalEffects: ['Choose an ancestry element.', 'Resist that element.', 'Use a breath weapon once per rest.'],
    narrativeFlavor: 'Describe scales, draconic eyes, proud posture, breath gathering in the chest, and ancestral pressure.',
    recommendedClasses: ['Fighter', 'Paladin', 'Sorcerer'],
    recommendedStyles: ['Frontliner', 'Battle leader', 'Elemental caster'],
    difficulty: 'Medium',
    warnings: ['Element choice matters for theme and resistance.'],
    categories: ['Martial', 'Elemental', 'Durable', 'Social', 'Exotic'],
    aliases: ['dragon', 'draconic', 'dragon person', 'scales'],
  }),
  race({
    key: 'dwarf',
    name: 'Dwarf',
    tagline: 'Stout folk of stone, craft, memory, and stubborn courage.',
    shortDescription: 'Dwarves are durable, practical, tradition-rich characters who endure pressure well.',
    longDescription:
      'Dwarves fit grounded adventurers with clan ties, craft pride, grudges, discipline, and a hard-earned sense of loyalty.',
    traits: ['Darkvision', 'Poison Resilience', 'Stonewise'],
    mechanicalEffects: ['Strong durability themes.', 'Comfortable in underground and crafted places.', 'Good resistance to poison or hardship.'],
    narrativeFlavor: 'Mention careful craft, old songs, stone metaphors, compact strength, and inherited obligations.',
    recommendedClasses: ['Cleric', 'Fighter', 'Artificer'],
    recommendedStyles: ['Tank', 'Crafter', 'Guardian'],
    difficulty: 'Easy',
    warnings: [],
    categories: ['Beginner Friendly', 'Martial', 'Darkvision', 'Durable'],
    aliases: ['dwarven', 'mountain dwarf', 'hill dwarf'],
  }),
  race({
    key: 'elf',
    name: 'Elf',
    tagline: 'Graceful long-lived wanderers shaped by magic and memory.',
    shortDescription: 'Elves are elegant, perceptive, and versatile, with strong magical or woodland themes.',
    longDescription:
      'Elves work for characters who feel ancient, refined, restless, or slightly apart from the world around them. They can be bright, shadowed, wild, or scholarly.',
    traits: ['Darkvision', 'Keen Senses', 'Fey Ancestry'],
    mechanicalEffects: ['Good perception and charm resistance.', 'Flexible magical or martial flavor.', 'Strong night and wilderness play.'],
    narrativeFlavor: 'Describe precise movement, old references, watchful stillness, and beauty that feels slightly unreal.',
    recommendedClasses: ['Wizard', 'Ranger', 'Rogue'],
    recommendedStyles: ['Archer', 'Scholar', 'Skirmisher'],
    difficulty: 'Easy',
    warnings: [],
    categories: ['Beginner Friendly', 'Magical', 'Stealthy', 'Nature', 'Darkvision'],
    aliases: ['elven', 'drow', 'dark elf', 'wood elf', 'high elf', 'half elf', 'half-elf'],
  }),
  race({
    key: 'fairy',
    name: 'Fairy',
    tagline: 'Tiny fey tricksters with wings and impossible sparkle.',
    shortDescription: 'Fairies bring flight, whimsy, magic, and social weirdness into the party.',
    longDescription:
      'Fairies fit playful, eerie, or chaotic characters touched by the Feywild. They are great when the player wants magic to feel bright, strange, and personal.',
    traits: ['Flight', 'Fey Magic', 'Tiny Frame'],
    mechanicalEffects: ['Fly in permissive scenes.', 'Carry minor innate magic.', 'Small size changes movement, cover, and tone.'],
    narrativeFlavor: 'Mention wings, glittering dust, fey logic, sudden moods, and delicate but unnerving confidence.',
    recommendedClasses: ['Bard', 'Druid', 'Sorcerer'],
    recommendedStyles: ['Trickster', 'Support caster', 'Scout'],
    difficulty: 'Medium',
    warnings: ['Small flying characters can need rulings in tight or dangerous terrain.'],
    categories: ['Flying', 'Magical', 'Small', 'Nature', 'Exotic'],
    aliases: ['fae', 'fey', 'pixie', 'sprite', 'winged'],
  }),
  race({
    key: 'firbolg',
    name: 'Firbolg',
    tagline: 'Gentle forest giantkin with quiet magic and old patience.',
    shortDescription: 'Firbolgs are nature-bound protectors with subtle magic and a calm, oversized presence.',
    longDescription:
      'Firbolgs are excellent for soft-spoken guardians, hermits, druids, and characters who would rather redirect danger than dominate it.',
    traits: ['Hidden Step', 'Powerful Build', 'Nature Speech'],
    mechanicalEffects: ['Brief magical disappearance.', 'Large carrying presence.', 'Natural fit for beasts and wilderness scenes.'],
    narrativeFlavor: 'Describe deep voices, mossy colors, careful hands, forest manners, and discomfort with greed.',
    recommendedClasses: ['Druid', 'Cleric', 'Ranger'],
    recommendedStyles: ['Protector', 'Healer', 'Wilderness guide'],
    difficulty: 'Medium',
    warnings: [],
    categories: ['Nature', 'Large', 'Magical', 'Durable'],
    aliases: ['forest giant', 'nature giant', 'gentle giant'],
  }),
  race({
    key: 'genasi',
    name: 'Genasi',
    tagline: 'Element-touched wanderers with fire, water, earth, or air in their blood.',
    shortDescription: 'Genasi are expressive elemental characters with visible magic in their bodies.',
    longDescription:
      'Genasi work when a player wants ancestry to feel like living weather, flame, stone, tide, or breath. They are strong visual characters with flexible class options.',
    traits: ['Elemental Legacy', 'Innate Magic', 'Striking Presence'],
    mechanicalEffects: ['Choose an elemental lineage.', 'Gain small element-themed powers.', 'Useful environmental flavor.'],
    narrativeFlavor: 'Describe unusual skin, hair like flame or water, stone calm, drifting dust, or air that stirs around them.',
    recommendedClasses: ['Sorcerer', 'Druid', 'Fighter'],
    recommendedStyles: ['Elemental caster', 'Explorer', 'Duelist'],
    difficulty: 'Medium',
    warnings: ['Element selection shapes the character fantasy strongly.'],
    categories: ['Elemental', 'Magical', 'Exotic', 'Social'],
    aliases: ['elemental', 'fire genasi', 'water genasi', 'earth genasi', 'air genasi'],
  }),
  race({
    key: 'gnome',
    name: 'Gnome',
    tagline: 'Small bright minds full of tricks, craft, and stubborn curiosity.',
    shortDescription: 'Gnomes are clever, magical, and inventive, with a knack for outthinking problems.',
    longDescription:
      'Gnomes fit tinkerers, illusionists, scholars, and eccentric problem-solvers. They bring a compact but intense energy to the party.',
    traits: ['Small', 'Gnome Cunning', 'Inventive'],
    mechanicalEffects: ['Strong mental resilience.', 'Small size helps with cover and tight spaces.', 'Great illusion or craft flavor.'],
    narrativeFlavor: 'Mention fast hands, bright eyes, odd tools, packed notes, and delight at strange mechanisms.',
    recommendedClasses: ['Wizard', 'Artificer', 'Rogue'],
    recommendedStyles: ['Inventor', 'Illusionist', 'Skill expert'],
    difficulty: 'Easy',
    warnings: [],
    categories: ['Beginner Friendly', 'Small', 'Magical', 'Stealthy'],
    aliases: ['gnomish', 'deep gnome', 'rock gnome', 'forest gnome'],
  }),
  race({
    key: 'goblin',
    name: 'Goblin',
    tagline: 'Small survivors with sharp instincts and sudden bursts of nerve.',
    shortDescription: 'Goblins are nimble opportunists who dart, hide, improvise, and survive.',
    longDescription:
      'Goblins are good for scrappy characters, underdogs, clever cowards, or troublemakers who learned to live by speed and nerve.',
    traits: ['Nimble Escape', 'Small', 'Survivor'],
    mechanicalEffects: ['Excellent bonus movement and hiding flavor.', 'Strong hit-and-run play.', 'Small size changes positioning.'],
    narrativeFlavor: 'Describe quick glances, restless hands, bargain instincts, and pride in surviving impossible odds.',
    recommendedClasses: ['Rogue', 'Ranger', 'Artificer'],
    recommendedStyles: ['Skirmisher', 'Trickster', 'Trap expert'],
    difficulty: 'Medium',
    warnings: ['Some NPCs may bring prejudice from old monster stories.'],
    categories: ['Small', 'Stealthy', 'Monstrous', 'Darkvision'],
    aliases: ['gobbo', 'small survivor'],
  }),
  race({
    key: 'goliath',
    name: 'Goliath',
    tagline: 'Mountain-born giants of endurance, competition, and grit.',
    shortDescription: 'Goliaths are powerful, durable, and built for hard terrain and harder fights.',
    longDescription:
      'Goliaths work for players who want a physically impressive character with a culture of challenge, honor, and survival against brutal conditions.',
    traits: ['Stone Endurance', 'Powerful Build', 'Mountain Born'],
    mechanicalEffects: ['Reduce a burst of damage.', 'Strong carrying and athletic presence.', 'Natural fit for harsh environments.'],
    narrativeFlavor: 'Mention towering height, weathered skin, calm pain tolerance, and a habit of measuring worth by trials.',
    recommendedClasses: ['Barbarian', 'Fighter', 'Paladin'],
    recommendedStyles: ['Tank', 'Grappler', 'Frontliner'],
    difficulty: 'Easy',
    warnings: [],
    categories: ['Beginner Friendly', 'Martial', 'Large', 'Durable'],
    aliases: ['giantkin', 'mountain giant', 'big strong'],
  }),
  race({
    key: 'halfling',
    name: 'Halfling',
    tagline: 'Small warm-hearted adventurers with uncanny luck.',
    shortDescription: 'Halflings are brave, lucky, and easy to fit into almost any campaign tone.',
    longDescription:
      'Halflings are ideal for players who want courage without grandeur, charm without arrogance, and luck that makes small heroes feel larger than life.',
    traits: ['Lucky', 'Brave', 'Small'],
    mechanicalEffects: ['Reroll disaster moments in many rule sets.', 'Resist fear themes.', 'Small size helps with cover and mobility.'],
    narrativeFlavor: 'Describe practical comforts, quick smiles, steady courage, and surprising boldness from a small frame.',
    recommendedClasses: ['Rogue', 'Bard', 'Ranger'],
    recommendedStyles: ['Scout', 'Face', 'Lucky hero'],
    difficulty: 'Easy',
    warnings: [],
    categories: ['Beginner Friendly', 'Small', 'Stealthy', 'Social'],
    aliases: ['hobbit', 'small folk', 'little folk'],
  }),
  race({
    key: 'harengon',
    name: 'Harengon',
    tagline: 'Rabbitfolk wanderers with springing legs and quick nerves.',
    shortDescription: 'Harengon are mobile, alert, and energetic, built for players who like quick action.',
    longDescription:
      'Harengon characters feel lively and reactive. They are excellent for scouts, duelists, performers, and anyone who survives by moving first.',
    traits: ['Rabbit Hop', 'Lucky Footwork', 'Keen Hearing'],
    mechanicalEffects: ['Strong burst movement.', 'Good initiative and evasive flavor.', 'Useful in chase and scouting scenes.'],
    narrativeFlavor: 'Describe twitching ears, spring-loaded movement, nervous energy, and a habit of reading danger early.',
    recommendedClasses: ['Monk', 'Rogue', 'Ranger'],
    recommendedStyles: ['Scout', 'Skirmisher', 'Duelist'],
    difficulty: 'Easy',
    warnings: [],
    categories: ['Beginner Friendly', 'Beastlike', 'Small', 'Stealthy', 'Nature'],
    aliases: ['rabbitfolk', 'bunny', 'hare', 'rabbit'],
  }),
  race({
    key: 'hobgoblin',
    name: 'Hobgoblin',
    tagline: 'Disciplined tacticians who turn teamwork into force.',
    shortDescription: 'Hobgoblins are martial, organized, and good at coordinated party play.',
    longDescription:
      'Hobgoblins fit soldiers, strategists, captains, and outcasts from strict martial cultures. They shine when the party fights as a unit.',
    traits: ['Martial Training', 'Tactical Aid', 'Disciplined'],
    mechanicalEffects: ['Good teamwork and support flavor.', 'Natural weapon and armor comfort.', 'Strong battle command identity.'],
    narrativeFlavor: 'Describe clipped orders, polished kit, disciplined posture, and a careful eye for formation.',
    recommendedClasses: ['Fighter', 'Paladin', 'Bard'],
    recommendedStyles: ['Commander', 'Support martial', 'Tactician'],
    difficulty: 'Medium',
    warnings: ['Militaristic culture can shape NPC reactions and roleplay.'],
    categories: ['Martial', 'Social', 'Monstrous', 'Darkvision'],
    aliases: ['military goblin', 'hob goblin', 'tactician'],
  }),
  race({
    key: 'human',
    name: 'Human',
    tagline: 'Ambitious, adaptable, and at home in nearly any story.',
    shortDescription: 'Humans are flexible, familiar, and beginner friendly, with broad class support.',
    longDescription:
      'Humans work for nearly any concept, from farmhand hero to court spy to battle-scarred veteran. They are the cleanest choice when class and personality should lead.',
    traits: ['Adaptable', 'Versatile', 'Driven'],
    mechanicalEffects: ['Fits any class or background.', 'Easy to explain in most worlds.', 'Low rules and lore friction.'],
    narrativeFlavor: 'Describe ambition, varied cultures, short-lived urgency, and a talent for belonging anywhere.',
    recommendedClasses: ['Fighter', 'Wizard', 'Bard'],
    recommendedStyles: ['Any style', 'Flexible build', 'Story-first'],
    difficulty: 'Easy',
    warnings: [],
    categories: ['Beginner Friendly', 'Social', 'Martial', 'Magical'],
    aliases: ['humanoid', 'mortal'],
  }),
  race({
    key: 'kenku',
    name: 'Kenku',
    tagline: 'Corvid mimics with sharp memory and stranger voices.',
    shortDescription: 'Kenku are stealthy birdfolk with mimicry, memory, and a distinct roleplay hook.',
    longDescription:
      'Kenku are for players who enjoy unusual communication, precise memory, and shadowy movement. They can be funny, tragic, eerie, or brilliant.',
    traits: ['Mimicry', 'Expert Forgery', 'Keen Memory'],
    mechanicalEffects: ['Strong impersonation and sound tricks.', 'Useful for stealth and investigation.', 'Distinct social constraints if played strictly.'],
    narrativeFlavor: 'Describe repeated voices, glossy feathers, quick copying, and careful attention to sounds.',
    recommendedClasses: ['Rogue', 'Bard', 'Ranger'],
    recommendedStyles: ['Infiltrator', 'Scout', 'Impersonator'],
    difficulty: 'Advanced',
    warnings: ['Mimicry-heavy roleplay can be demanding if taken literally.'],
    categories: ['Stealthy', 'Beastlike', 'Darkvision', 'Exotic'],
    aliases: ['crowfolk', 'ravenfolk', 'corvid', 'flightless bird'],
  }),
  race({
    key: 'kobold',
    name: 'Kobold',
    tagline: 'Small draconic survivors with trapcraft and pack courage.',
    shortDescription: 'Kobolds are tiny dragon-adjacent tacticians who win through teamwork and tricks.',
    longDescription:
      'Kobolds fit clever underdogs, trapmakers, dragon worshippers, and characters who feel brave only when the plan is good enough.',
    traits: ['Pack Tactics', 'Trapwise', 'Draconic Spark'],
    mechanicalEffects: ['Strong teamwork identity.', 'Small size and trap flavor.', 'Natural link to dragons and tunnels.'],
    narrativeFlavor: 'Describe tiny horns, nervous bravery, shiny hoards, quick planning, and awe around dragons.',
    recommendedClasses: ['Rogue', 'Artificer', 'Sorcerer'],
    recommendedStyles: ['Trap expert', 'Skirmisher', 'Team tactician'],
    difficulty: 'Medium',
    warnings: ['Small size and monster reputation can matter in social scenes.'],
    categories: ['Small', 'Stealthy', 'Monstrous', 'Darkvision', 'Exotic'],
    aliases: ['little dragon', 'tiny dragon', 'small dragon', 'dragon-adjacent'],
  }),
  race({
    key: 'lizardfolk',
    name: 'Lizardfolk',
    tagline: 'Cold-eyed reptilian survivalists shaped by hunger and instinct.',
    shortDescription: 'Lizardfolk are durable, practical, and alien-minded wilderness survivors.',
    longDescription:
      'Lizardfolk are excellent for players who want a nonhuman perspective: practical, direct, survival-first, and hard to embarrass or intimidate.',
    traits: ['Natural Armor', 'Bite', 'Survival Instinct'],
    mechanicalEffects: ['Durable natural defenses.', 'Useful bite and crafting flavor.', 'Strong wilderness and water-adjacent survival.'],
    narrativeFlavor: 'Describe scales, stillness, measured speech, blunt practicality, and instinctive reading of danger.',
    recommendedClasses: ['Druid', 'Ranger', 'Barbarian'],
    recommendedStyles: ['Survivor', 'Frontliner', 'Wilderness guide'],
    difficulty: 'Advanced',
    warnings: ['Alien survival logic can feel blunt in social scenes.'],
    categories: ['Beastlike', 'Monstrous', 'Durable', 'Nature', 'Aquatic'],
    aliases: ['reptile', 'reptilian', 'lizard person', 'scaly', 'dragonlike'],
  }),
  race({
    key: 'minotaur',
    name: 'Minotaur',
    tagline: 'Horned maze-born chargers with strength and ferocious presence.',
    shortDescription: 'Minotaurs are large martial characters built for charges, intimidation, and force.',
    longDescription:
      'Minotaurs suit direct players who want a striking physical identity and a story about rage, labyrinths, honor, or breaking old expectations.',
    traits: ['Horns', 'Charge', 'Labyrinth Sense'],
    mechanicalEffects: ['Strong melee identity.', 'Good forced movement and charge flavor.', 'Powerful intimidation presence.'],
    narrativeFlavor: 'Describe horns, heavy breath, hoof beats, maze memories, and the tension between instinct and discipline.',
    recommendedClasses: ['Barbarian', 'Fighter', 'Paladin'],
    recommendedStyles: ['Charger', 'Tank', 'Bruiser'],
    difficulty: 'Medium',
    warnings: ['Large monstrous silhouette can affect stealth and social scenes.'],
    categories: ['Martial', 'Large', 'Monstrous', 'Durable'],
    aliases: ['bullfolk', 'bull man', 'horned'],
  }),
  race({
    key: 'orc',
    name: 'Orc',
    tagline: 'Fierce survivors with relentless drive and raw physical power.',
    shortDescription: 'Orcs are strong martial characters with endurance, intensity, and bold presence.',
    longDescription:
      'Orcs fit warriors, protectors, hunters, rebels, and anyone whose body says they were built to keep moving through pain.',
    traits: ['Relentless Endurance', 'Powerful Build', 'Aggressive'],
    mechanicalEffects: ['Excellent front-line flavor.', 'Survive hard hits in many rule sets.', 'Strong athletic and intimidation scenes.'],
    narrativeFlavor: 'Describe tusks, scarred strength, blunt honesty, clan memory, and a refusal to stay down.',
    recommendedClasses: ['Barbarian', 'Fighter', 'Ranger'],
    recommendedStyles: ['Frontliner', 'Hunter', 'Bruiser'],
    difficulty: 'Easy',
    warnings: [],
    categories: ['Beginner Friendly', 'Martial', 'Durable', 'Darkvision'],
    aliases: ['orcish', 'half-orc', 'half orc', 'greenskin'],
  }),
  race({
    key: 'satyr',
    name: 'Satyr',
    tagline: 'Fey revelers with music, mischief, and stubborn charm.',
    shortDescription: 'Satyrs are social, magical, and playful, with strong performance energy.',
    longDescription:
      'Satyrs work for charming troublemakers, wandering musicians, fey agents, and characters who hide sharp instincts under celebration.',
    traits: ['Magic Resistance', 'Mirthful Leaps', 'Reveler'],
    mechanicalEffects: ['Good magical resilience.', 'Strong social and performance flavor.', 'Mobile in rough movement scenes.'],
    narrativeFlavor: 'Describe hooves, laughter, sudden songs, fey confidence, and a habit of testing boundaries.',
    recommendedClasses: ['Bard', 'Warlock', 'Rogue'],
    recommendedStyles: ['Face', 'Trickster', 'Support caster'],
    difficulty: 'Medium',
    warnings: ['The revelry theme can pull scenes playful unless handled carefully.'],
    categories: ['Social', 'Magical', 'Nature', 'Exotic'],
    aliases: ['faun', 'goatfolk', 'goat person', 'fey reveler'],
  }),
  race({
    key: 'shifter',
    name: 'Shifter',
    tagline: 'Beast-touched wanderers balancing instinct and self-control.',
    shortDescription: 'Shifters are physical, instinctive characters who briefly reveal animal power.',
    longDescription:
      'Shifters fit hunters, outcasts, guardians, and anyone with a wild inheritance under the skin. Their best scenes contrast control with sudden transformation.',
    traits: ['Shifting', 'Bestial Senses', 'Primal Instinct'],
    mechanicalEffects: ['Temporary beastlike boost.', 'Strong tracking and perception flavor.', 'Flexible martial or stealth builds.'],
    narrativeFlavor: 'Describe sharpening teeth, changed eyes, raised hackles, scent memory, and control under pressure.',
    recommendedClasses: ['Ranger', 'Barbarian', 'Monk'],
    recommendedStyles: ['Hunter', 'Skirmisher', 'Bruiser'],
    difficulty: 'Medium',
    warnings: ['Transformation flavor should match the player comfort level.'],
    categories: ['Beastlike', 'Martial', 'Stealthy', 'Nature', 'Darkvision'],
    aliases: ['werefolk', 'lycan', 'werewolf', 'beastfolk'],
  }),
  race({
    key: 'tabaxi',
    name: 'Tabaxi',
    tagline: 'Feline wanderers driven by speed, curiosity, and stories.',
    shortDescription: 'Tabaxi are fast, stealthy explorers with strong curiosity and movement tools.',
    longDescription:
      'Tabaxi are great for players who like motion, discovery, impulsive curiosity, and a character who collects experiences as treasure.',
    traits: ['Feline Agility', 'Claws', 'Catlike Senses'],
    mechanicalEffects: ['Excellent burst speed.', 'Useful climbing and stealth flavor.', 'Strong scouting identity.'],
    narrativeFlavor: 'Describe tail movement, quiet steps, bright attention, sudden stillness, and curiosity that interrupts caution.',
    recommendedClasses: ['Rogue', 'Monk', 'Bard'],
    recommendedStyles: ['Scout', 'Duelist', 'Explorer'],
    difficulty: 'Easy',
    warnings: [],
    categories: ['Beginner Friendly', 'Beastlike', 'Stealthy', 'Nature'],
    aliases: ['catfolk', 'cat person', 'feline'],
  }),
  race({
    key: 'tiefling',
    name: 'Tiefling',
    tagline: 'Hell-touched mortals with infernal marks and sharp charisma.',
    shortDescription: 'Tieflings bring magic, social pressure, and striking infernal visuals.',
    longDescription:
      'Tieflings are strong for characters wrestling with reputation, temptation, power, prejudice, or a family story written in horns and fire.',
    traits: ['Infernal Legacy', 'Fire Resistance', 'Commanding Look'],
    mechanicalEffects: ['Resist fire themes.', 'Carry innate infernal magic.', 'Strong social and intimidation flavor.'],
    narrativeFlavor: 'Describe horns, tails, unusual eyes, warm skin, faint brimstone, and the weight of being judged on sight.',
    recommendedClasses: ['Warlock', 'Bard', 'Sorcerer'],
    recommendedStyles: ['Face', 'Blaster caster', 'Dark hero'],
    difficulty: 'Easy',
    warnings: ['Some worlds may include suspicion toward infernal heritage.'],
    categories: ['Beginner Friendly', 'Magical', 'Social', 'Darkvision', 'Exotic'],
    aliases: ['demon', 'devil', 'infernal', 'half demon', 'hellborn'],
  }),
  race({
    key: 'tortle',
    name: 'Tortle',
    tagline: 'Shell-backed travelers with patience, wisdom, and natural armor.',
    shortDescription: 'Tortles are durable wanderers who carry home, defense, and calm with them.',
    longDescription:
      'Tortles fit patient guardians, monks, druids, and old-soul travelers who treat the world as a road and their shell as a shield.',
    traits: ['Natural Armor', 'Shell Defense', 'Patient Traveler'],
    mechanicalEffects: ['Reliable natural defense.', 'Strong survival identity.', 'Less dependent on worn armor fantasy.'],
    narrativeFlavor: 'Describe shell markings, slow smiles, careful pacing, old stories, and calm under attack.',
    recommendedClasses: ['Druid', 'Monk', 'Cleric'],
    recommendedStyles: ['Tank', 'Hermit', 'Protector'],
    difficulty: 'Easy',
    warnings: [],
    categories: ['Beginner Friendly', 'Beastlike', 'Durable', 'Nature', 'Aquatic'],
    aliases: ['turtlefolk', 'turtle person', 'tortoise'],
  }),
  race({
    key: 'triton',
    name: 'Triton',
    tagline: 'Ocean-born guardians from the pressure and mystery of the deep.',
    shortDescription: 'Tritons are aquatic, noble, and magical, built for sea-linked adventures.',
    longDescription:
      'Tritons work best when the story can touch oceans, storms, ancient undersea duties, or a proud outsider learning surface customs.',
    traits: ['Amphibious', 'Ocean Magic', 'Deep Guardian'],
    mechanicalEffects: ['Breathe and move through water well.', 'Carry small ocean-themed magic.', 'Strong underwater and coastal utility.'],
    narrativeFlavor: 'Describe sea-colored skin, formal manners, saltwater scent, pressure-born calm, and ancient ocean obligations.',
    recommendedClasses: ['Paladin', 'Cleric', 'Sorcerer'],
    recommendedStyles: ['Guardian', 'Water caster', 'Diplomat'],
    difficulty: 'Medium',
    warnings: ['Most useful when aquatic scenes can appear.'],
    categories: ['Aquatic', 'Magical', 'Durable', 'Social', 'Exotic'],
    aliases: ['merfolk', 'sea elf', 'oceanborn', 'water person'],
  }),
  race({
    key: 'warforged',
    name: 'Warforged',
    tagline: 'Living constructs built for purpose and searching for self.',
    shortDescription: 'Warforged are durable artificial people with powerful identity and purpose themes.',
    longDescription:
      'Warforged fit stories about created life, duty after war, personhood, memory, and learning what choice means after being built to obey.',
    traits: ['Constructed Resilience', 'Integrated Protection', 'Sleepless'],
    mechanicalEffects: ['Excellent durability flavor.', 'Reduced dependence on ordinary biology.', 'Strong armor and endurance identity.'],
    narrativeFlavor: 'Describe metal, wood, stone, quiet servos, careful speech, and small choices that reveal personhood.',
    recommendedClasses: ['Fighter', 'Artificer', 'Paladin'],
    recommendedStyles: ['Tank', 'Crafter', 'Guardian'],
    difficulty: 'Medium',
    warnings: ['Construct identity can shift tone toward war, creation, or philosophy.'],
    categories: ['Durable', 'Martial', 'Exotic', 'Large'],
    aliases: ['robot', 'construct', 'machine', 'automaton'],
  }),
  race({
    key: 'yuan-ti',
    name: 'Yuan-ti',
    tagline: 'Serpentine schemers with poison, poise, and ancient secrets.',
    shortDescription: 'Yuan-ti are exotic serpentfolk suited to intrigue, magic, and cool menace.',
    longDescription:
      'Yuan-ti work for players who want controlled danger, old cultic secrets, snake imagery, and a character who can be elegant without feeling harmless.',
    traits: ['Poison Resilience', 'Serpentine Grace', 'Innate Guile'],
    mechanicalEffects: ['Strong poison resistance themes.', 'Natural social menace.', 'Useful stealth and magic flavor.'],
    narrativeFlavor: 'Describe slit pupils, measured movements, cool skin, soft consonants, and unreadable expressions.',
    recommendedClasses: ['Warlock', 'Rogue', 'Sorcerer'],
    recommendedStyles: ['Infiltrator', 'Face', 'Dark caster'],
    difficulty: 'Advanced',
    warnings: ['Can carry villain-coded culture in some settings; use thoughtfully.'],
    categories: ['Monstrous', 'Magical', 'Stealthy', 'Social', 'Exotic'],
    aliases: ['yuan ti', 'snakefolk', 'snake person', 'serpent'],
  }),
]

const RACE_METADATA_UPDATES = {
  aarakocra: {
    shortDescription: 'Aarakocra are cliff-born avian scouts whose wings make distance, height, and weather part of every plan.',
    longDescription:
      'Aarakocra communities usually gather in aeries, sky temples, and high passes where the wind is a teacher as much as a road. Their stories often prize lookout duty, migration, and the freedom to leave danger below.',
    originStory:
      'Most Aarakocra grow up measuring the world from above: smoke columns, river bends, marching armies, and storm lines all read like tracks. On the ground they can seem restless, but in the air they become patient hunters and messengers who see the whole shape of a problem.',
    averageHeight: '5 to 6 feet',
    averageWeight: '80 to 120 lb',
    languages: ['Common', 'Auran'],
    commonProficiencies: ['Perception', 'Survival', 'Acrobatics'],
    friendlyWith: ['Rangers', 'Druids', 'mountain clans', 'air-aligned Genasi'],
    waryOf: ['underground cultures', 'cage-builders', 'heavy infantry commanders'],
    categories: ['Flying', 'Scout', 'Wilderness', 'Beastfolk'],
  },
  aasimar: {
    shortDescription: 'Aasimar are celestial-touched mortals marked by radiant signs, healing gifts, and impossible expectations.',
    longDescription:
      'Some Aasimar are born after omens, visions, or divine bargains; others only discover the light in them when danger forces it out. Their heritage can feel like comfort, duty, surveillance, or a crown they never asked to wear.',
    originStory:
      'An Aasimar may hear a guardian in dreams, carry a family prophecy, or simply glow when emotion runs too hot. People often look to them for certainty, but the best Aasimar stories ask whether the character chooses goodness or merely performs it.',
    averageHeight: '5 to 6.5 feet',
    averageWeight: '110 to 220 lb',
    languages: ['Common', 'Celestial'],
    commonProficiencies: ['Religion', 'Insight', 'Persuasion'],
    friendlyWith: ['Clerics', 'Paladins', 'good-aligned temples', 'Humans'],
    waryOf: ['fiendish cults', 'Tiefling prejudice', 'those who expect obedience'],
    categories: ['Magic', 'Face/Social', 'Durable'],
  },
  bugbear: {
    shortDescription: 'Bugbears are long-limbed ambushers: big enough to terrify, quiet enough to arrive unseen.',
    longDescription:
      'Bugbears are often raised around raiding bands, border tribes, or harsh places where patience and reach matter. A heroic Bugbear can feel like a shadow that chose discipline over cruelty.',
    originStory:
      'A Bugbear at rest can look almost lazy, but that stillness is part of the hunt. They know how to wait, how to strike from odd angles, and how to make size feel sudden rather than slow.',
    averageHeight: '6.5 to 8 feet',
    averageWeight: '250 to 350 lb',
    languages: ['Common', 'Goblin'],
    commonProficiencies: ['Stealth', 'Athletics', 'Intimidation'],
    friendlyWith: ['Goblins', 'Hobgoblins', 'mercenary companies', 'outcasts'],
    waryOf: ['city guards', 'Elven patrols', 'settlements with monster raids in living memory'],
    categories: ['Frontline', 'Scout', 'Monstrous', 'Tall/Bulky', 'Darkvision'],
  },
  changeling: {
    shortDescription: 'Changelings are living disguises, built for intrigue, reinvention, and stories about identity.',
    longDescription:
      'Changeling families may live openly as flexible artisans, hidden inside city crowds, or scattered through spy networks and theater troupes. Their gift is not only a new face, but the burden of deciding which face is true.',
    originStory:
      'Every Changeling learns that trust is more fragile when appearance can lie. Some use that truth kindly, becoming diplomats and performers; others become ghosts in the margins of courts, guilds, and criminal houses.',
    averageHeight: '5 to 6 feet',
    averageWeight: '100 to 180 lb',
    languages: ['Common', 'one local or social language'],
    commonProficiencies: ['Deception', 'Performance', 'Persuasion'],
    friendlyWith: ['Bards', 'Rogues', 'actors', 'cosmopolitan Humans'],
    waryOf: ['inquisitors', 'rigid noble houses', 'communities obsessed with bloodline'],
    categories: ['Face/Social', 'Scout', 'Magic'],
  },
  dragonborn: {
    shortDescription: 'Dragonborn are draconic humanoids whose scales, breath, and bearing make ancestry impossible to ignore.',
    longDescription:
      'Dragonborn often come from proud clans, martial lineages, or scattered families carrying the echo of ancient dragons. Their element is more than a weapon; it can shape ritual, temper, reputation, and the way strangers read their silhouette.',
    originStory:
      'A Dragonborn child learns early that people see the dragon first and the person second. Some answer with honor and command, some with rebellion, and some with a quiet need to prove that blood does not write destiny.',
    averageHeight: '6 to 7 feet',
    averageWeight: '220 to 320 lb',
    languages: ['Common', 'Draconic'],
    commonProficiencies: ['Intimidation', 'Athletics', 'History'],
    friendlyWith: ['Paladins', 'Sorcerers', 'Kobolds who revere dragons', 'martial orders'],
    waryOf: ['dragon hunters', 'rival draconic clans', 'fearful villages'],
    categories: ['Frontline', 'Magic', 'Elemental', 'Draconic', 'Durable', 'Face/Social'],
  },
  dwarf: {
    shortDescription: 'Dwarves are stone-wise, craft-proud, and built to endure hardship, debt, grief, and battle.',
    longDescription:
      'Dwarven holds, hill clans, and forge towns tend to value memory: names carved in stone, grudges kept honestly, and tools passed down with stories attached. A Dwarf usually knows what they owe and who they stand beside.',
    originStory:
      'Dwarves often treat craft as biography. A nick in a shield, a family mining song, or a cup hammered by a grandparent can matter as much as a royal decree, because survival is something they build together.',
    averageHeight: '4 to 5 feet',
    averageWeight: '150 to 220 lb',
    languages: ['Common', 'Dwarvish'],
    commonProficiencies: ['History', 'Smithing tools', 'Mason tools'],
    friendlyWith: ['Gnomes', 'Humans', 'lawful orders', 'craft guilds'],
    waryOf: ['Orc raiders', 'Goblin warbands', 'oathbreakers'],
    categories: ['Beginner Friendly', 'Frontline', 'Durable', 'Darkvision'],
  },
  elf: {
    shortDescription: 'Elves are long-lived, perceptive people shaped by magic, memory, beauty, and old grief.',
    longDescription:
      'Elven communities range from moonlit forest courts to high towers and hidden underground enclaves. They often carry history as a living thing, which can make them graceful, patient, haunted, or distant.',
    originStory:
      'An Elf may remember a song older than a kingdom, or come from a village where a single tree has more names than most humans have ancestors. Their adventures often begin when eternity becomes too still.',
    averageHeight: '5 to 6.5 feet',
    averageWeight: '90 to 170 lb',
    languages: ['Common', 'Elvish'],
    commonProficiencies: ['Perception', 'Arcana', 'Stealth'],
    friendlyWith: ['Druids', 'Rangers', 'Fey-touched people', 'scholars'],
    waryOf: ['Orc warbands', 'short-sighted rulers', 'those who despoil old forests'],
    categories: ['Beginner Friendly', 'Magic', 'Scout', 'Wilderness', 'Darkvision'],
  },
  fairy: {
    shortDescription: 'Fairies are tiny fey wanderers with wings, bright magic, and rules that rarely match mortal logic.',
    longDescription:
      'Fairies usually trace their roots to the Feywild, enchanted groves, moonlit courts, or bargains made near impossible flowers. Their magic is personal and strange, turning jokes, promises, and names into things with weight.',
    originStory:
      'A Fairy may have left a court of endless dances, escaped a cruel archfey, or followed a mortal song out of the trees. They look delicate, but they come from a world where beauty is sharp and laughter can be a warning.',
    averageHeight: '2 to 3 feet',
    averageWeight: '20 to 40 lb',
    languages: ['Common', 'Sylvan'],
    commonProficiencies: ['Arcana', 'Performance', 'Nature'],
    friendlyWith: ['Satyrs', 'Druids', 'Elves', 'other Fey-touched wanderers'],
    waryOf: ['iron-bound hunters', 'oathbreakers', 'coldly practical soldiers'],
    categories: ['Flying', 'Magic', 'Small', 'Fey', 'Scout'],
  },
  firbolg: {
    shortDescription: 'Firbolgs are gentle giantkin with forest magic, quiet strength, and a deep dislike of waste.',
    longDescription:
      'Firbolg clans often live far from roads, guarding old woods, hidden valleys, and places where beasts and spirits still speak. They tend to see ownership as temporary and stewardship as sacred.',
    originStory:
      'A Firbolg adventurer may be the one person sent to fix a wound in the world, or the odd soul who became curious about cities. Their power is rarely loud; it feels like shade arriving on a hot day.',
    averageHeight: '7 to 8 feet',
    averageWeight: '240 to 320 lb',
    languages: ['Common', 'Giant', 'Sylvan'],
    commonProficiencies: ['Nature', 'Animal Handling', 'Medicine'],
    friendlyWith: ['Druids', 'Rangers', 'forest villages', 'peaceful Giantkin'],
    waryOf: ['loggers without restraint', 'greedy nobles', 'fire-happy armies'],
    categories: ['Wilderness', 'Magic', 'Tall/Bulky', 'Durable'],
  },
  genasi: {
    shortDescription: 'Genasi are elemental heirs whose bodies carry fire, water, earth, or air as living heritage.',
    longDescription:
      'Genasi may be descended from genies, born near planar rifts, or marked by elemental disasters that left a bloodline changed. Their presence often makes ordinary rooms feel warmer, colder, wetter, dustier, or charged.',
    originStory:
      'A Genasi usually grows up being told they are too much: too bright, too still, too stormy, too strange. The best Genasi stories turn that visible difference into power, style, and a reason to wander.',
    averageHeight: '5 to 6.5 feet',
    averageWeight: '100 to 220 lb',
    languages: ['Common', 'Primordial'],
    commonProficiencies: ['Arcana', 'Survival', 'one element-themed tool or skill'],
    friendlyWith: ['Sorcerers', 'Druids', 'elemental cults', 'plane-touched communities'],
    waryOf: ['planar binders', 'people who fear uncontrolled magic', 'opposing elemental factions'],
    categories: ['Elemental', 'Magic', 'Face/Social'],
  },
  gnome: {
    shortDescription: 'Gnomes are small, sharp-minded makers and wonder-seekers with stubborn magical resilience.',
    longDescription:
      'Gnomish homes are often dense with workshops, illusions, burrows, gardens, jokes, and dangerous half-finished ideas. They tend to treat curiosity as a duty, not a flaw.',
    originStory:
      'A Gnome may leave home because a theory demands testing, a machine needs field data, or a mystery has become unbearable. Their courage often looks like delight right up until the trap goes off.',
    averageHeight: '3 to 4 feet',
    averageWeight: '35 to 60 lb',
    languages: ['Common', 'Gnomish'],
    commonProficiencies: ['Arcana', 'Investigation', 'Tinker tools'],
    friendlyWith: ['Dwarves', 'Halflings', 'Artificers', 'curious Elves'],
    waryOf: ['bullies', 'anti-magic tyrants', 'people who ban experiments'],
    categories: ['Beginner Friendly', 'Magic', 'Small', 'Scout'],
  },
  goblin: {
    shortDescription: 'Goblins are small, fast survivors who turn fear, clutter, and opportunity into tactics.',
    longDescription:
      'Goblin communities often grow in the cracks of stronger powers: ruins, alleys, caves, armies, and scrap markets. They learn early that bravery is useful, but running at the right time is wisdom.',
    originStory:
      'A Goblin hero can be scrappy, funny, nervous, brilliant, or all of those in a single minute. Their story often asks what happens when someone raised to be disposable decides they are not.',
    averageHeight: '3 to 4 feet',
    averageWeight: '40 to 80 lb',
    languages: ['Common', 'Goblin'],
    commonProficiencies: ['Stealth', 'Sleight of Hand', 'Tinker tools'],
    friendlyWith: ['Bugbears', 'Hobgoblins', 'Rogues', 'underdogs'],
    waryOf: ['city guards', 'Dwarven holds', 'anyone with a bounty board'],
    categories: ['Scout', 'Small', 'Monstrous', 'Darkvision'],
  },
  goliath: {
    shortDescription: 'Goliaths are mountain-born giantkin who value endurance, fair challenge, and visible deeds.',
    longDescription:
      'Goliath clans often survive above the tree line, where storms punish arrogance and every resource is earned. Their culture can be competitive, but at its best competition is how the clan discovers who needs help.',
    originStory:
      'A Goliath may carve victories into skin, gear, or story, not to boast but to remember the lesson. They often leave home to test themselves against a wider world and learn which challenges are worth winning.',
    averageHeight: '7 to 8 feet',
    averageWeight: '280 to 360 lb',
    languages: ['Common', 'Giant'],
    commonProficiencies: ['Athletics', 'Survival', 'Intimidation'],
    friendlyWith: ['Dwarves', 'Firbolgs', 'martial orders', 'mountain settlements'],
    waryOf: ['cowards who risk others', 'soft nobles', 'creatures that hunt the weak for sport'],
    categories: ['Beginner Friendly', 'Frontline', 'Tall/Bulky', 'Durable'],
  },
  halfling: {
    shortDescription: 'Halflings are small, warm, brave people whose luck often looks like courage arriving on time.',
    longDescription:
      'Halfling villages, caravans, and river communities usually prize comfort, kinship, gossip, and practical heroism. They are proof that a gentle life can still produce steel when danger comes knocking.',
    originStory:
      'A Halfling adventure often begins with a small promise: bring someone home, save a farm, repay a kindness, see the road beyond the hill. Somehow those promises keep becoming legends.',
    averageHeight: '3 to 3.5 feet',
    averageWeight: '35 to 60 lb',
    languages: ['Common', 'Halfling'],
    commonProficiencies: ['Stealth', 'Persuasion', 'Cook utensils'],
    friendlyWith: ['Humans', 'Gnomes', 'Dwarves', 'kindly travelers'],
    waryOf: ['bullies', 'warbands', 'people who mistake kindness for weakness'],
    categories: ['Beginner Friendly', 'Small', 'Scout', 'Face/Social'],
  },
  harengon: {
    shortDescription: 'Harengon are quick rabbitfolk with springing legs, sharp hearing, and restless luck.',
    longDescription:
      'Harengon often come from fey roads, meadow villages, wandering bands, or places where danger is survived by hearing it first. They can be cheerful, anxious, bold, or impossible to pin down.',
    originStory:
      'A Harengon rarely enters a room without already knowing the exits. Their bodies seem built around the next leap, and their stories move best when the road is uncertain and the clock is loud.',
    averageHeight: '3 to 5 feet',
    averageWeight: '35 to 100 lb',
    languages: ['Common', 'Sylvan or one local language'],
    commonProficiencies: ['Acrobatics', 'Perception', 'Survival'],
    friendlyWith: ['Fairies', 'Satyrs', 'Rangers', 'traveling performers'],
    waryOf: ['trappers', 'patient predators', 'slow bureaucracies'],
    categories: ['Beginner Friendly', 'Beastfolk', 'Small', 'Scout', 'Wilderness', 'Fey'],
  },
  hobgoblin: {
    shortDescription: 'Hobgoblins are disciplined tacticians who read battlefields, favors, and chains of command.',
    longDescription:
      'Hobgoblin societies are often organized around legions, households, academies, or strict mutual obligation. Even rebels from that world tend to understand logistics, rank, and the cost of disorder.',
    originStory:
      'A Hobgoblin adventurer may be an officer without an army, a deserter with principles, or a strategist trying to prove that discipline can protect instead of conquer.',
    averageHeight: '5.5 to 6.5 feet',
    averageWeight: '150 to 220 lb',
    languages: ['Common', 'Goblin'],
    commonProficiencies: ['History', 'Intimidation', 'one martial weapon or armor tradition'],
    friendlyWith: ['Fighters', 'Bards with discipline', 'Goblin clans', 'military orders'],
    waryOf: ['chaotic raiders', 'undisciplined commanders', 'old enemies of goblinoid legions'],
    categories: ['Frontline', 'Face/Social', 'Monstrous', 'Darkvision'],
  },
  human: {
    shortDescription: 'Humans are adaptable, ambitious, and easy to fit into almost any class, culture, or campaign premise.',
    longDescription:
      'Human kingdoms, tribes, free cities, and frontier towns vary wildly, but they are often united by urgency. Short lives make human heroes restless, inventive, political, and willing to bet everything on a decade.',
    originStory:
      'A Human does not need ancient blood to matter. Their story can come from a village oath, a failed apprenticeship, a family debt, a military posting, or the simple decision to step forward when older powers hesitated.',
    averageHeight: '5 to 6.5 feet',
    averageWeight: '110 to 250 lb',
    languages: ['Common', 'one regional language'],
    commonProficiencies: ['Any one skill', 'Any one tool', 'local culture knowledge'],
    friendlyWith: ['most cosmopolitan races', 'mixed settlements', 'trade guilds'],
    waryOf: ['ancient grudges they inherited without understanding', 'powers that see them as short-lived tools'],
    categories: ['Beginner Friendly', 'Face/Social', 'Frontline', 'Magic'],
  },
  kenku: {
    shortDescription: 'Kenku are corvid mimics with perfect ears, borrowed voices, and a gift for shadowed work.',
    longDescription:
      'Kenku often live among city roofs, criminal crews, messenger networks, monasteries, or any community where memory and mimicry are valuable. Their speech can make every conversation feel like a collage of past moments.',
    originStory:
      'A Kenku may speak in a dead mentor\'s warning, a tavern keeper\'s laugh, and a guard captain\'s order all in one scene. They remember sound like other people remember faces, which makes them strange and brilliant witnesses.',
    averageHeight: '4.5 to 5.5 feet',
    averageWeight: '90 to 140 lb',
    languages: ['Common', 'Auran or one local language'],
    commonProficiencies: ['Stealth', 'Deception', 'Forgery kit'],
    friendlyWith: ['Rogues', 'Bards', 'Aarakocra', 'urban outcasts'],
    waryOf: ['people who demand plain speech', 'law courts', 'those who fear mimicry'],
    categories: ['Scout', 'Beastfolk', 'Face/Social', 'Darkvision'],
  },
  kobold: {
    shortDescription: 'Kobolds are small draconic tunnelers who survive through traps, teamwork, and fierce little plans.',
    longDescription:
      'Kobold warrens are often built around mines, caves, ruins, dragon shrines, or whatever dangerous place stronger folk ignored. Their culture prizes clever preparation because courage alone rarely wins.',
    originStory:
      'A Kobold can turn a rope, bell, jar of oil, and three cousins into a battle plan. Away from the warren, that same mind becomes an adventurer who notices weak beams, loose stones, and every possible escape route.',
    averageHeight: '2.5 to 3.5 feet',
    averageWeight: '25 to 45 lb',
    languages: ['Common', 'Draconic'],
    commonProficiencies: ['Trap tools', 'Stealth', 'Sleight of Hand'],
    friendlyWith: ['Dragonborn', 'Goblins', 'Artificers', 'dragon cults'],
    waryOf: ['giant predators', 'Dwarven miners', 'adventurers with a history of clearing warrens'],
    categories: ['Small', 'Scout', 'Monstrous', 'Draconic', 'Darkvision'],
  },
  lizardfolk: {
    shortDescription: 'Lizardfolk are reptilian survivalists with natural armor, blunt instincts, and marsh-born practicality.',
    longDescription:
      'Lizardfolk villages often rise in swamps, deltas, humid ruins, and river mazes where sentiment matters less than whether a tool works. Their alien calm can make them unsettling, but not stupid or cruel by default.',
    originStory:
      'A Lizardfolk may ask why mourners waste food, why warriors name weapons, or why softskin laws ignore hunger. Their story is strongest when practicality slowly learns friendship without becoming less honest.',
    averageHeight: '5.5 to 7 feet',
    averageWeight: '180 to 280 lb',
    languages: ['Common', 'Draconic'],
    commonProficiencies: ['Survival', 'Nature', 'Leatherworker tools'],
    friendlyWith: ['Druids', 'Rangers', 'Tritons near wetlands', 'practical hunters'],
    waryOf: ['wasteful nobles', 'cold-climate cities', 'people who mistake bluntness for malice'],
    categories: ['Beastfolk', 'Monstrous', 'Durable', 'Wilderness', 'Aquatic'],
  },
  minotaur: {
    shortDescription: 'Minotaurs are horned, powerful maze-born warriors whose presence turns movement into threat.',
    longDescription:
      'Minotaur cultures may come from labyrinth cities, island clans, arena traditions, or old curses turned into identity. Their stories often circle rage, direction, honor, and the choice not to be a monster.',
    originStory:
      'A Minotaur remembers paths in the body: turns, doors, smells, the pressure before a charge. The best of them become guardians who know that strength is most meaningful when it has a purpose.',
    averageHeight: '6 to 7.5 feet',
    averageWeight: '250 to 350 lb',
    languages: ['Common', 'one of Giant, Minotaur, or a local tongue'],
    commonProficiencies: ['Athletics', 'Intimidation', 'Survival'],
    friendlyWith: ['Fighters', 'Goliaths', 'honor-bound orders', 'arena veterans'],
    waryOf: ['maze cults', 'civilized people who see only the horns', 'mind-control magic'],
    categories: ['Frontline', 'Tall/Bulky', 'Monstrous', 'Durable'],
  },
  orc: {
    shortDescription: 'Orcs are fierce, enduring people whose strength is tied to survival, clan, and forward motion.',
    longDescription:
      'Orc communities vary from nomadic hunters to fortified clans and city neighborhoods, but many prize directness, courage, and the ability to keep standing when the world says fall.',
    originStory:
      'An Orc hero may carry scars as family history, not shame. Their best scenes often show the difference between violence and protection, rage and conviction, reputation and truth.',
    averageHeight: '6 to 7 feet',
    averageWeight: '180 to 280 lb',
    languages: ['Common', 'Orc'],
    commonProficiencies: ['Athletics', 'Intimidation', 'Survival'],
    friendlyWith: ['Half-orcs', 'Goliaths', 'frontier communities', 'martial companions'],
    waryOf: ['old clan enemies', 'Elven border patrols', 'people who expect brutality'],
    categories: ['Beginner Friendly', 'Frontline', 'Durable', 'Darkvision'],
  },
  satyr: {
    shortDescription: 'Satyrs are fey revelers whose music, charm, and mischief hide surprisingly sharp instincts.',
    longDescription:
      'Satyrs often come from Feywild groves, festival roads, enchanted vineyards, or mortal communities touched by fey bargains. They treat joy as power, but not always as kindness.',
    originStory:
      'A Satyr can make a tavern feel like a holiday and a negotiation feel like a dare. Beneath the laughter is someone who knows that rules are real, but so are loopholes, songs, and invitations.',
    averageHeight: '4.5 to 5.5 feet',
    averageWeight: '100 to 160 lb',
    languages: ['Common', 'Sylvan'],
    commonProficiencies: ['Performance', 'Persuasion', 'one musical instrument'],
    friendlyWith: ['Fairies', 'Harengon', 'Bards', 'Druids'],
    waryOf: ['joyless tyrants', 'oath collectors', 'people who exploit hospitality'],
    categories: ['Face/Social', 'Magic', 'Wilderness', 'Fey'],
  },
  shifter: {
    shortDescription: 'Shifters are beast-touched people whose instincts surface in claws, senses, speed, or hide.',
    longDescription:
      'Shifter communities often live on the edges of settled lands, where old lycanthropic myths, hunter traditions, and family packs overlap. Their transformation is usually controlled, brief, and personal.',
    originStory:
      'A Shifter may smell fear before hearing a lie, or feel their teeth sharpen when friends are threatened. Their story often asks whether instinct is a danger, a compass, or both.',
    averageHeight: '5 to 6.5 feet',
    averageWeight: '100 to 220 lb',
    languages: ['Common', 'one regional or pack language'],
    commonProficiencies: ['Perception', 'Survival', 'Athletics'],
    friendlyWith: ['Rangers', 'Druids', 'Tabaxi', 'frontier communities'],
    waryOf: ['silvered hunters', 'superstitious villages', 'people who confuse them with cursed lycanthropes'],
    categories: ['Beastfolk', 'Frontline', 'Scout', 'Wilderness', 'Darkvision'],
  },
  tabaxi: {
    shortDescription: 'Tabaxi are feline wanderers of speed, climbing, stealth, and curiosity that refuses to sit still.',
    longDescription:
      'Tabaxi clans and traveling families often collect stories, routes, songs, and beautiful objects rather than territory. Curiosity is not a quirk for them; it is how the world stays alive.',
    originStory:
      'A Tabaxi adventurer may chase a rumor across countries because the question itself has claws. They can be playful one breath and perfectly still the next, watching the room like prey and puzzle at once.',
    averageHeight: '5 to 6.5 feet',
    averageWeight: '90 to 200 lb',
    languages: ['Common', 'one clan or trade language'],
    commonProficiencies: ['Stealth', 'Acrobatics', 'Perception'],
    friendlyWith: ['Bards', 'Rogues', 'Harengon', 'traveling merchants'],
    waryOf: ['slavers', 'those who cage performers', 'people who destroy stories'],
    categories: ['Beginner Friendly', 'Beastfolk', 'Scout', 'Wilderness'],
  },
  tiefling: {
    shortDescription: 'Tieflings are infernal-marked mortals with fire, charisma, and a reputation they may not deserve.',
    longDescription:
      'Tiefling heritage can come from old pacts, fiendish influence, cursed bloodlines, or planar accidents. Horns and tails make the story visible before the character speaks, which can be power or burden.',
    originStory:
      'A Tiefling often learns early how strangers look at a devil-shaped silhouette. Some answer with charm, some with defiance, and some with the exhausting work of being kinder than anyone expects.',
    averageHeight: '5 to 6.5 feet',
    averageWeight: '100 to 220 lb',
    languages: ['Common', 'Infernal'],
    commonProficiencies: ['Deception', 'Persuasion', 'Arcana'],
    friendlyWith: ['Warlocks', 'Bards', 'Changelings', 'other outsiders'],
    waryOf: ['celestial zealots', 'superstitious villages', 'fiends who claim ownership'],
    categories: ['Beginner Friendly', 'Magic', 'Face/Social', 'Darkvision'],
  },
  tortle: {
    shortDescription: 'Tortles are shell-backed wanderers with patient wisdom, natural armor, and road-worn calm.',
    longDescription:
      'Tortles often come from coastal villages, island routes, river monasteries, or slow pilgrim paths. Home is not only a place for them; it is something carried, remembered, and practiced.',
    originStory:
      'A Tortle may move slowly until danger proves speed necessary. Their shell makes them look self-contained, but many are generous travelers who trade stories, maps, and quiet advice.',
    averageHeight: '5 to 6 feet',
    averageWeight: '400 to 500 lb',
    languages: ['Common', 'Aquan or one coastal language'],
    commonProficiencies: ['Survival', 'Nature', 'Cartographer tools'],
    friendlyWith: ['Druids', 'Monks', 'Tritons', 'coastal communities'],
    waryOf: ['poachers', 'reckless sailors', 'people who mock patience'],
    categories: ['Beginner Friendly', 'Beastfolk', 'Durable', 'Wilderness', 'Aquatic'],
  },
  triton: {
    shortDescription: 'Tritons are ocean-born guardians with amphibious bodies, deep magic, and formal pride.',
    longDescription:
      'Triton enclaves often stand in reef citadels, abyssal watchposts, and undersea courts built to hold back things the surface never sees. On land, their manners can seem grand, old-fashioned, or alien.',
    originStory:
      'A Triton may come ashore because a deep-sea oath points upward. They carry pressure, salt, and ancient responsibility with them, even when they are confused by tavern customs and dry shoes.',
    averageHeight: '5 to 6 feet',
    averageWeight: '100 to 180 lb',
    languages: ['Common', 'Primordial', 'Aquan'],
    commonProficiencies: ['Athletics', 'History', 'Persuasion'],
    friendlyWith: ['Tortles', 'Lizardfolk near coasts', 'Paladins', 'sailors'],
    waryOf: ['sea raiders', 'surface polluters', 'aberrations from the deep'],
    categories: ['Aquatic', 'Magic', 'Durable', 'Face/Social'],
  },
  warforged: {
    shortDescription: 'Warforged are living constructs of metal, wood, and will, built for purpose but searching for self.',
    longDescription:
      'Warforged are often created in foundries, mage-forges, military programs, or ancient workshops whose original purpose may be lost. Their bodies remember design, but personhood begins when orders stop being enough.',
    originStory:
      'A Warforged might polish armor because it is maintenance, keep a flower because it is beauty, or ask whether memory is the same as a soul. Their story is about choosing what they are after being built for what they were.',
    averageHeight: '6 to 7 feet',
    averageWeight: '250 to 350 lb',
    languages: ['Common', 'one creator or military language'],
    commonProficiencies: ['Smithing tools', 'Athletics', 'History'],
    friendlyWith: ['Artificers', 'Fighters', 'Dwarven smiths', 'other created beings'],
    waryOf: ['former masters', 'anti-construct zealots', 'people who treat them as property'],
    categories: ['Durable', 'Frontline', 'Construct'],
  },
  'yuan-ti': {
    shortDescription: 'Yuan-ti are serpentine, poison-wise people with controlled poise and ancient secretive traditions.',
    longDescription:
      'Yuan-ti lineages often come from serpent cults, jungle temples, hidden noble houses, or old empires that valued transformation and control. A heroic Yuan-ti can keep the elegance while rejecting the cruelty.',
    originStory:
      'A Yuan-ti adventurer may speak softly because they are used to being feared, or because patience is how serpents survive. Their story works best when menace becomes a tool, not a moral sentence.',
    averageHeight: '5 to 6.5 feet',
    averageWeight: '100 to 220 lb',
    languages: ['Common', 'Draconic or Abyssal'],
    commonProficiencies: ['Deception', 'Stealth', 'Poisoner kit'],
    friendlyWith: ['Rogues', 'Warlocks', 'scholars of lost empires', 'Lizardfolk pragmatists'],
    waryOf: ['temple inquisitors', 'anti-cult militias', 'people who assume they are villains'],
    categories: ['Monstrous', 'Magic', 'Scout', 'Face/Social'],
  },
} satisfies Record<RaceKey, RaceMetadataUpdate>

const RACE_COPY_POLISH = {
  aarakocra: {
    longDescription:
      'Aarakocra are winged people of high aeries, storm-cut cliffs, sky temples, and wind-carved passes. Their homes are built around sightlines, migration paths, and communal watch duty, so they often think in terms of distance, weather, and safe routes before they think in terms of walls or roads. In play, they make excellent scouts, messengers, archers, and outsiders who struggle whenever a dungeon, crowd, or ceiling takes the sky away.',
    originStory:
      'Most Aarakocra grow up learning that the world is a pattern seen from above: smoke means settlement, circling birds mean carrion, and a dark line on the horizon means rain or war. A young Aarakocra is usually taught to serve the flock by watching for danger and carrying news faster than groundfolk can react. When one becomes an adventurer, it is often because something below has become too important to merely observe from the clouds.',
  },
  aasimar: {
    longDescription:
      'Aasimar are mortal people carrying a visible or hidden trace of celestial power. Some are raised by ordinary families who do not understand the omens around them, while others grow up inside temples, prophecies, or bloodlines that expect them to become symbols. Their light can heal and inspire, but it can also isolate them, because strangers may treat an Aasimar as proof, weapon, saint, or threat before they are treated as a person.',
    originStory:
      'An Aasimar\'s story often begins with expectation: a guardian voice in dreams, a birthmark like a star, a village that prayed over them, or a family that feared what they would become. The most interesting Aasimar are not simply good; they have to choose what goodness means when everyone is watching. Playing one should feel like carrying a lantern through a dark room while wondering whether the light is yours or something using you.',
  },
  bugbear: {
    longDescription:
      'Bugbears are large goblinoids with long reach, quiet movement, and a reputation built from ambush stories. Many grow up in rough borderlands, raiding bands, mercenary camps, or mixed goblinoid communities where survival rewards patience more than noise. A Bugbear character can be frightening without being stupid, lazy without being weak, and gentle in ways that surprise people who only see the size and teeth.',
    originStory:
      'A Bugbear often learns to wait before they learn to charge. Stillness is a tool: wait for the guard to turn, wait for the fire to burn low, wait for the enemy to decide the room is empty. An adventuring Bugbear may be trying to escape old monster stories, profit from them, or prove that the same body built for ambush can also shield a friend.',
  },
  changeling: {
    longDescription:
      'Changelings are people whose faces, voices, and bodies can shift, making identity a living choice rather than a fixed fact. They fit naturally into cities, courts, theaters, spy networks, criminal crews, and traveling communities where names and appearances already carry social power. A Changeling is not just a disguise machine; they are someone who knows how fragile trust can be when the world believes a face is proof.',
    originStory:
      'Many Changelings grow up with rules about which faces are safe, which names belong to family, and when it is dangerous to be seen changing. Some become artists of empathy, learning another person\'s posture and voice to understand them better; others become ghosts who survive by never being known completely. Playing one should raise questions: which identity is comfort, which is armor, and who gets to see the face underneath?',
  },
  dragonborn: {
    longDescription:
      'Dragonborn are draconic humanoids whose scales, breath, and stature make ancestry impossible to hide. Many come from clan-based societies, martial households, temple lineages, or scattered families trying to define themselves apart from true dragons. Their elemental breath is not just an attack; it is a sign of inheritance, ritual, temper, reputation, and the way strangers decide whether to fear or respect them.',
    originStory:
      'A Dragonborn child usually learns early that people see the dragon before they see the person. In some communities that brings honor, in others suspicion, and in many places both at once. A Dragonborn adventurer might be chasing clan glory, rejecting a bloodline, seeking the source of their element, or trying to prove that ancestry is a beginning rather than a command.',
  },
  dwarf: {
    longDescription:
      'Dwarves are sturdy, tradition-rich people shaped by stone halls, forge smoke, clan memory, and practical endurance. Many live in mountain holds, hill towns, mining cities, or craft districts where reputation is built over years and a tool can carry as much history as a noble title. Dwarves tend to make excellent guardians, priests, smiths, soldiers, and stubborn problem-solvers because they are taught that good work and good promises should survive pressure.',
    originStory:
      'A Dwarf often knows the story of a family hammer, an old tunnel collapse, a feud no outsider understands, or a song sung when the hold doors close. They are not only short and tough; they are people raised around memory made physical. A Dwarf adventurer may leave home to repay a debt, recover a lost craft, test themselves beyond the hold, or decide which traditions deserve to be carried forward.',
  },
  elf: {
    longDescription:
      'Elves are long-lived, perceptive people whose lives are shaped by magic, memory, beauty, and distance from ordinary time. Their communities might be forest courts, high spires, wandering enclaves, moonlit villages, or hidden underground houses, and each can produce a very different kind of Elf. Because they often outlive kingdoms and friendships, Elves can seem graceful, patient, haunted, arrogant, careful, or painfully sentimental depending on what they have survived.',
    originStory:
      'An Elf may remember a border before it was a kingdom, a tree before it was sacred, or a lover whose grandchildren are now old. That long memory can be a gift, but it can also make the present feel fragile and brief. An Elf adventurer often leaves home when beauty becomes stillness, when grief becomes too familiar, or when the younger world does something surprising enough to deserve attention.',
  },
  fairy: {
    longDescription:
      'Fairies are small fey people with wings, bright magic, and instincts shaped by a world where promises, names, seasons, and jokes can carry real power. Many come from Feywild courts, enchanted groves, moonlit markets, flower kingdoms, or strange mortal families touched by fey bargains. They are whimsical, but not harmless; a Fairy may be playful, eerie, vain, generous, cruelly literal, or deeply loyal according to rules no one else knows.',
    originStory:
      'A Fairy might have fled an endless dance, been exiled for breaking a ridiculous law, followed a mortal song through a ring of mushrooms, or been sent to collect a debt no one remembers making. Their tiny size and glittering wings make people underestimate them, which is often a mistake. Playing a Fairy should feel like carrying a piece of a beautiful, dangerous dream into a world that insists on being sensible.',
  },
  firbolg: {
    longDescription:
      'Firbolgs are gentle giantkin tied to old forests, hidden valleys, quiet magic, and the idea that strength exists to protect what cannot protect itself. Their communities often live far from roads, sharing land with animals, spirits, and ancient trees rather than claiming it as property. They make natural druids, clerics, guides, and guardians, but they can also be awkward travelers when city life treats greed and haste as normal.',
    originStory:
      'A Firbolg is often raised to ask what a place needs before asking what they want from it. They may know which stream floods first, which deer is sick, and which old stone should never be moved. A Firbolg adventurer usually leaves because balance has been broken, a forest sent them, or curiosity finally overcame the comfort of being useful at home.',
  },
  genasi: {
    longDescription:
      'Genasi are people whose blood, body, or soul has been marked by elemental power. Fire Genasi may glow with banked heat, Water Genasi may move like tides, Earth Genasi may seem carved from patience, and Air Genasi may never feel fully still. They often come from genie bloodlines, planar accidents, elemental shrines, storm-touched families, or places where the boundary between the world and the elements wore thin.',
    originStory:
      'Most Genasi learn that their emotions and bodies make them visible: hair drifting like smoke, skin cooling a room, footprints dusty with stone, or laughter arriving with a breeze. Some are celebrated as omens, while others are treated like accidents that never stopped happening. A Genasi adventurer often wants to understand whether they are a person with elemental power or an element learning to be a person.',
  },
  gnome: {
    longDescription:
      'Gnomes are small, bright-minded people known for curiosity, invention, illusion, and stubborn mental resilience. Their homes may be burrows full of books, forest workshops hidden under roots, clockwork neighborhoods, or lively academic enclaves where jokes and experiments are both serious business. A Gnome character usually brings cleverness, wonder, and a willingness to ask why not at exactly the wrong or right moment.',
    originStory:
      'A Gnome often grows up surrounded by unfinished projects, family theories, prank traditions, and tools that are only dangerous if used as labeled. Curiosity is not treated as childish; it is a social responsibility. A Gnome adventurer may be testing a device, chasing a mystery, documenting the impossible, or proving that being small does not mean thinking small.',
  },
  goblin: {
    longDescription:
      'Goblins are small, quick survivors who thrive in ruins, alleys, caves, scrap towns, war camps, and other places stronger powers overlook. Their cultures often reward improvisation, alertness, humor under pressure, and the ability to make something useful out of garbage, fear, and bad odds. A Goblin hero can be cowardly and brave in the same scene, because they know bravery without an exit plan is just volunteering to be dead.',
    originStory:
      'A Goblin may have grown up being told they were expendable by bosses, warlords, adventurers, or the world in general. That produces sharp eyes, quick hands, and a talent for measuring danger faster than pride. An adventuring Goblin is often someone who decided survival was not enough; they want respect, treasure, revenge, family, or proof that the smallest person in the room can still change the ending.',
  },
  goliath: {
    longDescription:
      'Goliaths are tall mountain-born people shaped by thin air, brutal weather, clan trials, and a culture of visible deeds. Many grow up where food, shelter, and mistakes all matter, so competition is not only pride but a way to discover who is ready, who needs help, and who can be trusted when the storm closes in. They make powerful warriors and guardians, but their best stories are about endurance, fairness, and learning that not every challenge is solved by winning.',
    originStory:
      'A Goliath may carry carved marks, trophies, scars, or spoken titles that remember important trials. Those marks are not just boasting; they are lessons made visible. A Goliath adventurer might leave the mountain to test themselves, redeem a failure, find a challenge worthy of their name, or learn why lowland people fight so fiercely over things that cannot survive a winter.',
  },
  halfling: {
    longDescription:
      'Halflings are small, warm, brave people often rooted in villages, caravans, river communities, farms, and close families where comfort and courage are not opposites. They value meals, stories, practical kindness, gossip, and the sort of bravery that shows up because someone has to help. A Halfling fits nearly any campaign because they turn ordinary decency into an adventuring strength.',
    originStory:
      'A Halfling adventure rarely begins with a hunger for glory. It begins with a cousin missing, a farm threatened, a promise made, a road calling, or a simple refusal to let larger people decide what matters. Their luck feels less like destiny and more like the world making room for someone who keeps stepping forward despite every sensible reason not to.',
  },
  harengon: {
    longDescription:
      'Harengon are rabbitfolk with sharp hearing, springing movement, and a quickness that feels partly physical and partly fey. Many come from meadow villages, traveling bands, fey roads, seasonal courts, or borderlands where danger is survived by hearing it early and moving before fear can root you. They are energetic scouts, duelists, messengers, performers, and survivors who make stillness feel suspicious.',
    originStory:
      'A Harengon often knows the exits before the introductions are done. Their ears betray mood, their feet want the next leap, and their instincts treat hesitation as a luxury. A Harengon adventurer may be following a lucky road, fleeing a prophecy, chasing a festival, or proving that nervous energy can become heroism when pointed in the right direction.',
  },
  hobgoblin: {
    longDescription:
      'Hobgoblins are goblinoid people shaped by discipline, tactics, obligation, and the belief that a group survives when each member knows their role. Their societies are often legions, fortress towns, martial academies, or strict households where honor is measured through service and competence. A Hobgoblin can be a soldier, strategist, bodyguard, officer, rebel, or reformer trying to decide what discipline is for.',
    originStory:
      'A Hobgoblin usually understands command before freedom. They know how supplies move, why formations break, which insult starts a duel, and how much chaos one frightened recruit can cause. An adventuring Hobgoblin may be seeking a new unit, escaping a cruel one, proving loyalty to chosen companions, or trying to build a life where order protects instead of dominates.',
  },
  human: {
    longDescription:
      'Humans are adaptable, ambitious, and culturally varied enough to fit almost any class, region, or story. Their kingdoms, tribes, free cities, nomad bands, guilds, and frontier towns can differ more from each other than some entirely different ancestries do. Because human lives are comparatively brief, their stories often carry urgency: build now, love now, conquer now, fix it before the chance is gone.',
    originStory:
      'A Human character does not need ancient blood or obvious magic to matter. They can come from a fishing village, noble court, failed apprenticeship, army camp, crime family, temple school, or farm at the edge of a haunted wood. The heart of a Human story is choice under time pressure: what will they become with one short life and too many possible roads?',
  },
  kenku: {
    longDescription:
      'Kenku are corvid-like people known for mimicry, precise memory, stealth, and voices made from echoes. They often live on city roofs, in messenger guilds, criminal crews, monasteries, docks, theaters, or anywhere sound and secrecy have value. A Kenku character is not only a talks funny gimmick; they are someone who records the world through sound and may understand truth by replaying what others missed.',
    originStory:
      'A Kenku may speak with a dead mentor\'s warning, a tavern keeper\'s laugh, and a guard captain\'s command all in one conversation. Every borrowed phrase carries history. A Kenku adventurer might be searching for an original voice, escaping a life of imitation, using mimicry as art, or proving that a person assembled from echoes is still a person.',
  },
  kobold: {
    longDescription:
      'Kobolds are small draconic tunnelers who survive through traps, teamwork, alertness, and fierce respect for anything bigger than them. Their warrens often form in mines, caves, ruins, sewers, dragon shrines, and dangerous places no one sensible would choose without a very good plan. A Kobold character is excellent for players who enjoy clever problem-solving, underdog courage, and a draconic spark without the size or certainty of a Dragonborn.',
    originStory:
      'A Kobold grows up knowing the ceiling height, the loose stones, the alarm bells, and which tunnel floods first. Alone they may be frightened; with a plan they can be terrifying. A Kobold adventurer might be seeking a dragon, fleeing a collapsed warren, collecting treasures for a chosen family, or proving that bravery is not the absence of fear but refusing to let fear make all the decisions.',
  },
  lizardfolk: {
    longDescription:
      'Lizardfolk are reptilian survivalists whose cultures often grow around marshes, deltas, warm ruins, river mazes, and hard wilderness. They are practical, direct, and sometimes unsettling to softer societies because they tend to judge customs by whether they help anyone survive. A Lizardfolk character works best when played as genuinely different rather than merely rude: emotions exist, but hunger, weather, injury, and danger are harder to ignore.',
    originStory:
      'A Lizardfolk may not understand why a warrior names a sword, why mourners waste good food, or why nobles value silk more than a sharp knife. That does not make them heartless; it means their heart was trained by a world where sentiment without usefulness can get a tribe killed. Their adventuring story often becomes the slow discovery that friendship, art, and memory can be useful in ways teeth cannot measure.',
  },
  minotaur: {
    longDescription:
      'Minotaurs are horned, powerful people tied to labyrinths, charges, physical presence, and the struggle to direct instinct rather than be ruled by it. Their cultures may come from maze cities, island clans, arena traditions, war herds, temple guardians, or old curses transformed into identity. A Minotaur character can be brutal, noble, spiritual, tactical, or surprisingly gentle, but they are rarely easy to ignore.',
    originStory:
      'A Minotaur remembers space in the body: the turn of a corridor, the smell of old stone, the breath before a charge, the anger that wants a straight line through every problem. Some are raised to be monsters and spend their lives refusing the role; others are guardians who know a maze is not a prison if you are protecting what waits at the center.',
  },
  orc: {
    longDescription:
      'Orcs are strong, enduring people whose cultures often value survival, directness, clan memory, physical courage, and the refusal to stay down. They may come from nomadic hunting bands, fortified clans, city neighborhoods, mercenary companies, or frontier settlements where reputation is earned in visible ways. A good Orc character is not simply angry; they are someone whose body and culture have been shaped by pressure, loyalty, and the need to act when words fail.',
    originStory:
      'An Orc may carry scars as family history, not shame. They may speak bluntly because soft lies waste time, or fight fiercely because hesitation once cost someone they loved. An Orc adventurer might be defending a clan, escaping a reputation, seeking worthy rivals, or showing the world that strength can be an instrument of care rather than cruelty.',
  },
  satyr: {
    longDescription:
      'Satyrs are fey-touched revelers with music, charm, mischief, and a dangerous understanding of invitation. They often come from Feywild groves, festival roads, enchanted vineyards, traveling troupes, or mortal villages that made old bargains with laughing powers. A Satyr is playful, but play is not the same as harmless; songs, dares, hospitality, and broken promises all matter deeply to them.',
    originStory:
      'A Satyr can turn a room into a celebration before anyone realizes the celebration has rules. They may test boundaries because boundaries reveal desire, fear, and hypocrisy. A Satyr adventurer might be chasing the perfect song, fleeing a fey debt, protecting joy from tyrants, or learning that not every wound can be danced around.',
  },
  shifter: {
    longDescription:
      'Shifters are beast-touched people whose bodies can briefly reveal claws, fangs, hide, speed, heightened senses, or other animal inheritance. Many live among frontier families, hidden packs, hunter lodges, wandering clans, or urban communities that keep old instincts under polite clothing. They are not necessarily cursed lycanthropes; their shifting is usually identity, ancestry, and survival rather than uncontrolled monstrosity.',
    originStory:
      'A Shifter may smell fear before hearing a lie, feel their teeth sharpen when a friend is threatened, or wake from dreams of running on four feet. Some are taught to hide those signs, while others are taught to honor them. A Shifter adventurer often wrestles with whether instinct is a danger to master, a truth to trust, or a language their civilized life forgot how to speak.',
  },
  tabaxi: {
    longDescription:
      'Tabaxi are feline wanderers known for speed, climbing, stealth, curiosity, and a love of stories, routes, and beautiful things. Many travel in clans, caravans, merchant families, or loose networks that value experience as treasure. A Tabaxi character is excellent for players who want motion, sensory detail, impulsive investigation, and a reason to ask what is over there even when over there is clearly dangerous.',
    originStory:
      'A Tabaxi may remember places by smell, collect rumors like gems, and become fascinated by a locked door simply because someone locked it. Their curiosity is not random; it is how the world stays bright. A Tabaxi adventurer might chase a half-heard legend, repay a clan debt, hunt a stolen heirloom, or gather enough stories to return home as someone worth listening to.',
  },
  tiefling: {
    longDescription:
      'Tieflings are mortals with infernal or fiendish marks: horns, tails, unusual eyes, warm skin, old magic, and a reputation that often arrives before they do. Their heritage may come from pacts, curses, planar accidents, family secrets, or ancestors who dealt with powers they did not fully understand. A Tiefling can be charming, bitter, heroic, secretive, theatrical, or kind, but they usually know what it means to be judged by shape before action.',
    originStory:
      'A Tiefling child often learns the difference between being seen and being known. Some lean into the fear, using style and sharp smiles as armor; others spend years being gentler than anyone expected just to be given a fair chance. A Tiefling adventurer may be trying to escape a family bargain, reclaim a condemned name, master inherited magic, or prove that damnation is not contagious.',
  },
  tortle: {
    longDescription:
      'Tortles are shell-backed wanderers whose lives often revolve around patience, travel, natural armor, coastal roads, and the idea that home can be carried rather than owned. They may come from island villages, river monasteries, fishing routes, desert pilgrim trails, or old mapmaking traditions. A Tortle character is usually calm under pressure, but that calm can hide deep curiosity, old grief, or a surprisingly firm moral center.',
    originStory:
      'A Tortle may spend years walking a route their grandparents walked, adding new stories to an old shell pattern or map case. Their shell makes them look self-contained, but many are generous travelers who trade advice, warnings, and quiet jokes. A Tortle adventurer might be on pilgrimage, recording a changing world, protecting a coastline, or finally moving because patience has run out.',
  },
  triton: {
    longDescription:
      'Tritons are ocean-born people shaped by pressure, salt, duty, and the deep places surface folk rarely understand. Their enclaves may be reef citadels, abyssal watchposts, undersea courts, storm temples, or military colonies built to guard against things rising from below. On land they can seem formal, proud, alien, or old-fashioned because they come from a world where ceremony and survival are often the same thing.',
    originStory:
      'A Triton may have grown up hearing that the surface is loud, dry, temporary, and dangerously ignorant of what the depths contain. When they come ashore, they bring ancient oaths into taverns, courts, and muddy roads that do not know how to honor them. A Triton adventurer might be hunting an escaped horror, seeking allies for an undersea war, studying surface customs, or discovering who they are when duty no longer has walls of water around it.',
  },
  warforged: {
    longDescription:
      'Warforged are living constructs of metal, wood, stone, leather, crystal, or stranger materials, created for purpose but capable of becoming people beyond that purpose. They may come from mage-forges, military foundries, ancient workshops, experimental temples, or forgotten machines that kept building long after their makers vanished. A Warforged character is about identity, memory, embodiment, and the difference between being useful and being alive.',
    originStory:
      'A Warforged might polish armor because it is maintenance, keep a flower because it is beauty, and ask whether either act proves they have a soul. Some remember war commands more clearly than childhood because they never had a childhood at all. A Warforged adventurer may be searching for their maker, fleeing ownership, building a self from chosen habits, or learning that freedom is not just the absence of orders but the presence of desire.',
  },
  'yuan-ti': {
    longDescription:
      'Yuan-ti are serpentine people associated with poison, composure, old empires, hidden temples, and controlled emotion. In many worlds their cultures carry sinister histories of transformation, cult power, or cold ambition, but an individual Yuan-ti does not have to be trapped inside that reputation. They are excellent for intrigue, forbidden scholarship, poison themes, social menace, and characters who know the value of patience.',
    originStory:
      'A Yuan-ti may have been raised to treat warmth as weakness, secrets as currency, and the body as something that can be improved through ritual or discipline. Leaving that world can feel like shedding skin: painful, necessary, and never quite complete. A Yuan-ti adventurer might reject an old cult, seek a lost serpent empire, master poison for healing instead of murder, or prove that calm does not mean cruelty.',
  },
} satisfies Record<RaceKey, Pick<PlayableRace, 'longDescription' | 'originStory'>>

const RACE_RELATIONSHIP_POLISH = {
  aarakocra: {
    friendlyWith: ['Air Genasi', 'Fairy', 'Harengon', 'Elf', 'Triton'],
    waryOf: ['Kobold dragon-cultists', 'Goblin raiders', 'Hobgoblin legions', 'Warforged siege-forces', 'cultures that cage wings'],
  },
  aasimar: {
    friendlyWith: ['Human', 'Elf', 'Dragonborn', 'Dwarf', 'Halfling'],
    waryOf: ['Yuan-ti cults', 'fiend-serving Tiefling houses', 'undead factions', 'false prophets', 'zealots who demand obedience'],
  },
  bugbear: {
    friendlyWith: ['Goblin', 'Hobgoblin', 'Orc', 'Minotaur', 'Kobold'],
    waryOf: ['Elf patrols', 'Dwarf holds', 'Human frontier guards', 'Aasimar monster-hunters', 'Gnome trap-makers'],
  },
  changeling: {
    friendlyWith: ['Human cities', 'Tiefling', 'Shifter', 'Kenku', 'Goblin'],
    waryOf: ['Aasimar inquisitors', 'Dwarf oathkeepers', 'Hobgoblin officers', 'Yuan-ti manipulators', 'bloodline-obsessed nobles'],
  },
  dragonborn: {
    friendlyWith: ['Dwarf', 'Goliath', 'Aasimar', 'Genasi', 'honorable Kobold clans'],
    waryOf: ['Yuan-ti', 'Dragon cults', 'rival Dragonborn clans', 'Changeling impostors', 'dragon hunters'],
  },
  dwarf: {
    friendlyWith: ['Gnome', 'Human', 'Halfling', 'Goliath', 'Warforged'],
    waryOf: ['Orc warbands', 'Goblin raiders', 'Bugbear ambushers', 'Kobold tunnelers', 'Giant-kin enemies'],
  },
  elf: {
    friendlyWith: ['Fairy', 'Firbolg', 'Halfling', 'Aasimar', 'Genasi'],
    waryOf: ['Dwarf', 'Orc', 'Hobgoblin', 'Goblin', 'Yuan-ti'],
  },
  fairy: {
    friendlyWith: ['Satyr', 'Harengon', 'Elf', 'Firbolg', 'Gnome'],
    waryOf: ['Hobgoblin courts', 'Warforged machines', 'Dwarf iron-miners', 'Yuan-ti', 'cold-iron hunters'],
  },
  firbolg: {
    friendlyWith: ['Elf', 'Fairy', 'Halfling', 'Tortle', 'Lizardfolk'],
    waryOf: ['Human expansionists', 'Dwarf logging/mining guilds', 'Hobgoblin legions', 'Goblin raiders', 'Yuan-ti'],
  },
  genasi: {
    friendlyWith: ['Aarakocra', 'Triton', 'Dragonborn', 'Dwarf', 'Tiefling'],
    waryOf: ['Elemental binders', 'Yuan-ti ritualists', 'Gnome experimenters', 'Hobgoblin battlemages', 'Aasimar absolutists'],
  },
  gnome: {
    friendlyWith: ['Dwarf', 'Halfling', 'Human', 'Warforged', 'Fairy'],
    waryOf: ['Kobold trap-rivals', 'Goblin raiders', 'Bugbear ambushers', 'Hobgoblin officers', 'Yuan-ti'],
  },
  goblin: {
    friendlyWith: ['Bugbear', 'Hobgoblin', 'Kobold', 'Tabaxi', 'Kenku'],
    waryOf: ['Dwarf holds', 'Gnome tinkerers', 'Elf patrols', 'Human guards', 'Aasimar monster-hunters'],
  },
  goliath: {
    friendlyWith: ['Dwarf', 'Orc', 'Dragonborn', 'Minotaur', 'Tortle'],
    waryOf: ['Goblin tricksters', 'Kobold trap-makers', 'Yuan-ti', 'Changeling deceivers', 'soft lowland nobles'],
  },
  halfling: {
    friendlyWith: ['Human', 'Gnome', 'Dwarf', 'Elf', 'Harengon'],
    waryOf: ['Bugbear ambushers', 'Hobgoblin conquerors', 'Yuan-ti', 'Minotaur raiders', 'anyone who overlooks small folk'],
  },
  harengon: {
    friendlyWith: ['Fairy', 'Satyr', 'Halfling', 'Tabaxi', 'Aarakocra'],
    waryOf: ['Bugbear hunters', 'Hobgoblin press-gangs', 'Kobold trappers', 'Yuan-ti', 'predatory wilderness clans'],
  },
  hobgoblin: {
    friendlyWith: ['Goblin', 'Bugbear', 'Orc', 'Dragonborn', 'Warforged'],
    waryOf: ['Elf', 'Dwarf', 'Changeling', 'Satyr', 'Fairy'],
  },
  human: {
    friendlyWith: ['Halfling', 'Dwarf', 'Elf', 'Gnome', 'Tiefling'],
    waryOf: ['Yuan-ti infiltrators', 'Bugbear raiders', 'Hobgoblin armies', 'Changeling impostors', 'Minotaur pirates'],
  },
  kenku: {
    friendlyWith: ['Changeling', 'Goblin', 'Tabaxi', 'Gnome', 'Aarakocra'],
    waryOf: ['Aasimar', 'Hobgoblin', 'Dwarf', 'Yuan-ti', 'Human courts'],
  },
  kobold: {
    friendlyWith: ['Dragonborn', 'Goblin', 'Lizardfolk', 'Yuan-ti', 'Bugbear'],
    waryOf: ['Dwarf miners', 'Gnome trap-rivals', 'Goliath giantslayers', 'Aarakocra sky-hunters', 'Aasimar crusaders'],
  },
  lizardfolk: {
    friendlyWith: ['Tortle', 'Triton', 'Kobold', 'Firbolg', 'Yuan-ti'],
    waryOf: ['Human nobles', 'Fairy tricksters', 'Aasimar moralists', 'Halfling sentimentalists', 'Warforged despoilers'],
  },
  minotaur: {
    friendlyWith: ['Goliath', 'Orc', 'Dragonborn', 'Hobgoblin', 'Tortle'],
    waryOf: ['Fairy tricksters', 'Changeling deceivers', 'Yuan-ti manipulators', 'Goblin cowards', 'Aarakocra skirmishers'],
  },
  orc: {
    friendlyWith: ['Goliath', 'Minotaur', 'Dragonborn', 'Hobgoblin', 'Human frontier clans'],
    waryOf: ['Elf war-parties', 'Dwarf strongholds', 'Aasimar crusaders', 'Yuan-ti', 'Goblin war-bosses'],
  },
  satyr: {
    friendlyWith: ['Fairy', 'Harengon', 'Elf', 'Tiefling', 'Tabaxi'],
    waryOf: ['Aasimar moralizers', 'Hobgoblin disciplinarians', 'Warforged enforcers', 'Dwarf traditionalists', 'Yuan-ti'],
  },
  shifter: {
    friendlyWith: ['Tabaxi', 'Orc', 'Firbolg', 'Harengon', 'Lizardfolk'],
    waryOf: ['Human hunters', 'Aasimar purifiers', 'Hobgoblin trackers', 'Yuan-ti', 'Warforged bounty-forces'],
  },
  tabaxi: {
    friendlyWith: ['Kenku', 'Harengon', 'Satyr', 'Goblin', 'Shifter'],
    waryOf: ['Hobgoblin authorities', 'Yuan-ti', 'Warforged enforcers', 'Aasimar judges', 'Dwarf vault-keepers'],
  },
  tiefling: {
    friendlyWith: ['Changeling', 'Satyr', 'Human', 'Genasi', 'Aasimar outcasts'],
    waryOf: ['Aasimar zealots', 'Human city guards', 'Dwarf traditionalists', 'Dragonborn honor-clans', 'infernal recruiters'],
  },
  tortle: {
    friendlyWith: ['Triton', 'Lizardfolk', 'Firbolg', 'Halfling', 'Goliath'],
    waryOf: ['Goblin raiders', 'Bugbear ambushers', 'Yuan-ti', 'Hobgoblin conquerors', 'Human pirates'],
  },
  triton: {
    friendlyWith: ['Tortle', 'Water Genasi', 'Aarakocra', 'Dragonborn', 'Lizardfolk'],
    waryOf: ['Yuan-ti', 'Goblin sea raiders', 'Human polluters', 'Warforged dredgers', 'Tiefling pact-sailors'],
  },
  warforged: {
    friendlyWith: ['Dwarf', 'Gnome', 'Human', 'Dragonborn', 'Hobgoblin'],
    waryOf: ['Fairy wild-magic', 'Satyr chaos', 'Yuan-ti mind-magic', 'Aasimar soul-judges', 'Orc warlords'],
  },
  'yuan-ti': {
    friendlyWith: ['Lizardfolk', 'Kobold', 'Tiefling', 'Changeling', 'Dragonborn cultists'],
    waryOf: ['Aasimar', 'Dwarf', 'Human', 'Triton', 'Elf'],
  },
} satisfies Record<RaceKey, Pick<RaceProfileDetails, 'friendlyWith' | 'waryOf'>>

export const PLAYABLE_RACES: PlayableRace[] = BASE_PLAYABLE_RACES.map((entry) => ({
  ...entry,
  ...RACE_METADATA_UPDATES[entry.key],
  ...RACE_COPY_POLISH[entry.key],
  ...RACE_RELATIONSHIP_POLISH[entry.key],
}))

const RACES_BY_NORMALIZED_NAME = new Map(
  PLAYABLE_RACES.flatMap((entry) => [
    [normalizeSearchText(entry.name), entry] as const,
    [normalizeSearchText(entry.key), entry] as const,
  ]),
)

export function normalizeSearchText(value: string) {
  return value
    .toLowerCase()
    .replace(/['’]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

function compactSearchText(value: string) {
  return normalizeSearchText(value).replace(/\s+/g, '')
}

export function profileIconSrcForRace(raceKey: RaceKey, sex: SexKey) {
  return profileIconSrcForCharacter({ race: raceKey, sex }) ?? '/profile-icons/human_male.png'
}

export function playableRaceFromValue(value?: string | null) {
  const normalized = normalizeSearchText(value ?? '')
  if (!normalized) return null
  const exact = RACES_BY_NORMALIZED_NAME.get(normalized)
  if (exact) return exact
  const inputTokens = new Set(normalized.split(/\s+/).filter(Boolean))
  for (const raceEntry of PLAYABLE_RACES) {
    const aliases = [...raceEntry.aliases, ...(RACE_ALIASES[raceEntry.key] ?? [])]
    for (const alias of aliases) {
      const normalizedAlias = normalizeSearchText(alias)
      if (!normalizedAlias) continue
      if (normalized === normalizedAlias) return raceEntry
      if (normalizedAlias.includes(' ') && normalized.includes(normalizedAlias)) return raceEntry
      if (!normalizedAlias.includes(' ') && normalizedAlias.length >= 4 && inputTokens.has(normalizedAlias)) {
        return raceEntry
      }
    }
  }
  return null
}

export function playableRaceLabel(value?: string | null) {
  return playableRaceFromValue(value)?.name ?? ''
}

export function raceSelectionFromPlayableRace(raceEntry: PlayableRace): CharacterRaceSelection {
  return {
    raceId: raceEntry.key,
    raceName: raceEntry.name,
    source: 'curated',
    selectedOptions: {},
  }
}

function raceSearchFields(raceEntry: PlayableRace) {
  return [
    raceEntry.name,
    raceEntry.key,
    raceEntry.tagline,
    raceEntry.shortDescription,
    raceEntry.longDescription,
    raceEntry.narrativeFlavor,
    raceEntry.originStory,
    raceEntry.averageHeight,
    raceEntry.averageWeight,
    ...raceEntry.traits,
    ...raceEntry.mechanicalEffects,
    ...raceEntry.recommendedClasses,
    ...raceEntry.recommendedStyles,
    ...raceEntry.categories,
    ...raceEntry.aliases,
    ...raceEntry.languages,
    ...raceEntry.commonProficiencies,
    ...raceEntry.friendlyWith,
    ...raceEntry.waryOf,
    ...(RACE_ALIASES[raceEntry.key] ?? []),
  ]
}

export function raceMatchesSearch(raceEntry: PlayableRace, query: string) {
  const normalizedQuery = normalizeSearchText(query)
  if (!normalizedQuery) return true
  const compactQuery = compactSearchText(normalizedQuery)
  return raceSearchFields(raceEntry).some((field) => {
    const normalizedField = normalizeSearchText(field)
    return normalizedField.includes(normalizedQuery) || compactSearchText(normalizedField).includes(compactQuery)
  })
}

export function filterPlayableRaces({
  query,
  category,
}: {
  query: string
  category: RaceCategory | 'All'
}) {
  return PLAYABLE_RACES.filter((raceEntry) => {
    const categoryMatch = category === 'All' || raceEntry.categories.includes(category)
    return categoryMatch && raceMatchesSearch(raceEntry, query)
  })
}
