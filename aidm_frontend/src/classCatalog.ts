export const CLASS_FILTERS = [
  'Beginner Friendly',
  'Martial',
  'Arcane',
  'Divine',
  'Primal',
  'Occult',
  'Expert',
  'Hybrid',
  'Science Fantasy',
  'Frontline',
  'Striker',
  'Support',
  'Scout',
  'Social',
  'Controller',
  'Summoner',
  'Crafting',
  'Mounted',
  'Dark',
  'Elemental',
] as const

export type ClassCategory = (typeof CLASS_FILTERS)[number]
export type ClassDifficulty = 'Easy' | 'Medium' | 'Advanced'
export type MagicLevel = 'None' | 'Low' | 'Half' | 'Full' | 'Variable'

export type PlayableSubclass = {
  key: string
  name: string
  tagline: string
  tags: string[]
}

export type PlayableClass = {
  key: string
  name: string
  tagline: string
  shortDescription: string
  longDescription: string
  powerSource: string
  combatRole: string
  magicLevel: MagicLevel
  difficulty: ClassDifficulty
  categories: ClassCategory[]
  aliases: string[]
  subclasses: PlayableSubclass[]
}

export type ClassSelection = {
  classEntry: PlayableClass
  subclass?: PlayableSubclass
}

type ClassInput = Omit<PlayableClass, 'key' | 'subclasses'> & {
  key?: string
  subclasses: Array<[string, string, string[]?]>
}

function slug(value: string) {
  return value
    .toLowerCase()
    .replace(/['’]/g, '')
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
}

function path(name: string, tagline: string, tags: string[] = []): PlayableSubclass {
  return { key: slug(name), name, tagline, tags }
}

function cls(entry: ClassInput): PlayableClass {
  return {
    ...entry,
    key: entry.key ?? slug(entry.name),
    subclasses: entry.subclasses.map(([name, tagline, tags]) => path(name, tagline, tags)),
  }
}

export const STARTER_CLASS_NAMES = [
  'Fighter',
  'Rogue',
  'Wizard',
  'Cleric',
  'Ranger',
  'Barbarian',
  'Bard',
  'Druid',
  'Paladin',
  'Sorcerer',
  'Warlock',
  'Monk',
] as const

const BASE_PLAYABLE_CLASSES: PlayableClass[] = [
  cls({
    name: 'Fighter',
    tagline: 'Weapon expert for almost any battlefield style.',
    shortDescription: 'Fighters are direct, flexible martial heroes who work for nearly any party.',
    longDescription:
      'Use Fighter for a character whose identity is training, discipline, weapons, armor, and tactical presence. It supports simple champions, technical duelists, archers, knights, and magic-tinged warriors.',
    powerSource: 'Martial',
    combatRole: 'Frontline or striker',
    magicLevel: 'None',
    difficulty: 'Easy',
    categories: ['Beginner Friendly', 'Martial', 'Frontline', 'Striker'],
    aliases: ['warrior', 'soldier', 'weapon master', 'knight'],
    subclasses: [
      ['Champion', 'Clean, reliable weapon excellence.', ['beginner']],
      ['Battle Master', 'Tactical maneuvers and battlefield control.', ['tactics']],
      ['Weapon Master', 'Specializes deeply in one weapon family.', ['specialist']],
      ['Shieldbearer', 'Protects allies through shields and formation fighting.', ['protector']],
      ['Duelist', 'Precise one-on-one blade work.', ['duel']],
      ['Archer', 'Ranged pressure, overwatch, and trick shots.', ['ranged']],
      ['Cavalier', 'Mounted charges, challenges, and protection.', ['mounted']],
      ['Samurai', 'Resolve, honor, focus, and devastating bursts.', ['resolve']],
      ['Gladiator', 'Arena tricks, showmanship, and brutal crowd work.', ['social']],
      ['Hoplite', 'Spear, shield, phalanx, and defensive discipline.', ['formation']],
      ['Rune Knight', 'Giant runes, growth, and carved magic.', ['runes']],
      ['Echo Warrior', 'Fights through a mirrored battlefield duplicate.', ['mobility']],
      ['Psi Warrior', 'Telekinetic strikes and mental defense.', ['psionic']],
      ['Eldritch Knight', 'A weapon fighter with defensive arcane magic.', ['arcane']],
    ],
  }),
  cls({
    name: 'Barbarian',
    tagline: 'Primal fury, survival instinct, and unstoppable force.',
    shortDescription: 'Barbarians are durable melee threats who turn emotion, ancestry, or instinct into power.',
    longDescription:
      'Use Barbarian for characters who solve danger by enduring it and hitting back harder. The fantasy can be wild, spiritual, giant-blooded, bestial, volcanic, or holy.',
    powerSource: 'Martial or primal',
    combatRole: 'Frontline striker',
    magicLevel: 'Low',
    difficulty: 'Easy',
    categories: ['Beginner Friendly', 'Martial', 'Primal', 'Frontline', 'Striker'],
    aliases: ['berserker', 'rager', 'raider', 'brute'],
    subclasses: [
      ['Berserker', 'Raw rage and relentless offense.', ['beginner']],
      ['Totem Warrior', 'Animal spirits shape rage and defense.', ['spirit']],
      ['Ancestral Guardian', 'Protective ancestors punish enemies.', ['protector']],
      ['Storm Raider', 'Rage carries storm, sea, or desert power.', ['elemental']],
      ['Zealot', 'Holy or profane fury that refuses death.', ['divine']],
      ['Beast Rager', 'Claws, fangs, and predatory mutation.', ['beast']],
      ['Giant Blooded', 'Huge strength, reach, and thrown devastation.', ['giant']],
      ['Dragon Fury', 'Draconic wrath, resistance, and breath.', ['dragon']],
      ['Juggernaut', 'Momentum, impact, and unstoppable movement.', ['charge']],
      ['Wild Soul', 'Unstable magic erupts through rage.', ['chaos']],
      ['Ravager', 'Fear, intimidation, and merciless pressure.', ['dark']],
    ],
  }),
  cls({
    name: 'Ranger',
    tagline: 'Tracker, scout, hunter, and wilderness problem solver.',
    shortDescription: 'Rangers blend martial skill, exploration, survival, and focused prey hunting.',
    longDescription:
      'Use Ranger for characters who know the land, read tracks, ambush threats, guide allies, and fight with practical nature-touched magic.',
    powerSource: 'Martial and primal',
    combatRole: 'Scout striker',
    magicLevel: 'Half',
    difficulty: 'Easy',
    categories: ['Beginner Friendly', 'Martial', 'Primal', 'Scout', 'Striker'],
    aliases: ['hunter', 'tracker', 'warden', 'woodsman'],
    subclasses: [
      ['Hunter', 'Classic monster and battlefield hunting.', ['beginner']],
      ['Beast Master', 'Bonded animal companion and teamwork.', ['companion']],
      ['Gloom Stalker', 'Ambush predator for darkness and first strikes.', ['stealth']],
      ['Monster Slayer', 'Studies and dismantles dangerous creatures.', ['monster']],
      ['Horizon Walker', 'Planar travel, portals, and extraplanar prey.', ['planar']],
      ['Fey Wanderer', 'Fey charm, social magic, and strange grace.', ['fey']],
      ['Swarmkeeper', 'A living swarm aids movement and attacks.', ['swarm']],
      ['Drakewarden', 'Raises and fights beside a drake companion.', ['dragon']],
      ['Urban Ranger', 'Tracks targets through cities and crowds.', ['urban']],
      ['Trapper', 'Controls ground with snares and prepared terrain.', ['traps']],
      ['Warden', 'Protects a territory with primal authority.', ['protector']],
    ],
  }),
  cls({
    name: 'Rogue',
    tagline: 'Skill expert, infiltrator, trickster, and precision striker.',
    shortDescription: 'Rogues excel at stealth, tools, social leverage, and sharp single-target damage.',
    longDescription:
      'Use Rogue for thieves, assassins, scouts, detectives, social masterminds, shadow agents, and any hero who wins by timing instead of brute force.',
    powerSource: 'Martial',
    combatRole: 'Scout striker',
    magicLevel: 'None',
    difficulty: 'Easy',
    categories: ['Beginner Friendly', 'Martial', 'Expert', 'Scout', 'Striker', 'Social'],
    aliases: ['thief', 'scoundrel', 'assassin', 'sneak'],
    subclasses: [
      ['Thief', 'Classic burglary, mobility, and clever hands.', ['beginner']],
      ['Assassin', 'Disguise, poison, and lethal openings.', ['stealth']],
      ['Scout', 'Skirmishing, survival, and battlefield movement.', ['wilderness']],
      ['Swashbuckler', 'Dashing duels and social confidence.', ['duel']],
      ['Arcane Trickster', 'Illusions, mage hand tricks, and misdirection.', ['arcane']],
      ['Mastermind', 'Planning, help actions, and social manipulation.', ['social']],
      ['Inquisitive', 'Insight, clues, and reading lies.', ['detective']],
      ['Phantom', 'Ghostly skills and death-touched strikes.', ['dark']],
      ['Soulknife', 'Psychic blades and silent communication.', ['psionic']],
      ['Ninja', 'Smoke, silence, climbing, and sudden kills.', ['stealth']],
      ['Poisoner', 'Venoms, toxins, and patient preparation.', ['poison']],
      ['Saboteur', 'Breaks devices, plans, doors, and morale.', ['tools']],
      ['Burglar', 'Locks, rooftops, and clean getaways.', ['tools']],
    ],
  }),
  cls({
    name: 'Monk',
    tagline: 'Disciplined body, focused spirit, and impossible movement.',
    shortDescription: 'Monks are mobile martial artists who use ki, speed, and precise control.',
    longDescription:
      'Use Monk for characters defined by training, inner power, acrobatics, pressure points, mystical discipline, or weaponless mastery.',
    powerSource: 'Martial and spiritual',
    combatRole: 'Mobile striker',
    magicLevel: 'Low',
    difficulty: 'Medium',
    categories: ['Beginner Friendly', 'Martial', 'Striker', 'Scout'],
    aliases: ['martial artist', 'ki adept', 'ascetic', 'brawler'],
    subclasses: [
      ['Open Hand', 'Classic control, strikes, throws, and stuns.', ['beginner']],
      ['Shadow', 'Stealth, darkness, and teleporting between shadows.', ['dark']],
      ['Four Elements', 'Bends fire, water, air, and earth through ki.', ['elemental']],
      ['Drunken Master', 'Erratic footwork and deceptive momentum.', ['mobility']],
      ['Kensei', 'Weapon discipline and graceful martial forms.', ['weapon']],
      ['Sun Soul', 'Radiant blasts and inner light.', ['radiant']],
      ['Mercy', 'Healing hands and harmful pressure points.', ['support']],
      ['Astral Self', 'Manifested spirit limbs and expanded presence.', ['spirit']],
      ['Dragon Disciple', 'Draconic breath, movement, and presence.', ['dragon']],
      ['Iron Body', 'Extreme resilience through physical discipline.', ['defense']],
      ['Ki Mystic', 'Broader mystical techniques and inner senses.', ['mystic']],
    ],
  }),
  cls({
    name: 'Gunslinger',
    tagline: 'Firearm specialist with nerve, precision, and risk.',
    shortDescription: 'Gunslingers bring black powder, trick shots, standoffs, and ranged burst damage.',
    longDescription:
      'Use Gunslinger where firearms fit the setting. The class works for western duelists, fantasy musketeers, snipers, gun tanks, and spell-infused shooters.',
    powerSource: 'Martial or tech',
    combatRole: 'Ranged striker',
    magicLevel: 'None',
    difficulty: 'Medium',
    categories: ['Martial', 'Expert', 'Striker', 'Science Fantasy'],
    aliases: ['sharpshooter', 'pistolero', 'musketeer', 'marksman'],
    subclasses: [
      ['Pistolero', 'Close-range swagger and quick-draw sidearms.', ['duel']],
      ['Sniper', 'Long-range patience and lethal precision.', ['ranged']],
      ['Musket Master', 'Reload discipline and heavy firearm mastery.', ['ranged']],
      ['Trick Shooter', 'Called shots, ricochets, and stylish utility.', ['trick']],
      ['Gun Tank', 'Armor, recoil, and holding the firing line.', ['frontline']],
      ['Spellshot', 'Bullets carry cantrips, runes, or arcane payloads.', ['arcane']],
      ['Bombardier', 'Explosives, grenades, and siege pressure.', ['explosive']],
      ['Outlaw', 'Dirty fighting, intimidation, and survival.', ['social']],
      ['Dueling Marksman', 'One-on-one firearm standoffs.', ['duel']],
    ],
  }),
  cls({
    name: 'Swashbuckler',
    tagline: 'Panache, daring, speed, and stylish dueling.',
    shortDescription: 'Swashbucklers are agile melee specialists who win with movement and confidence.',
    longDescription:
      'Use Swashbuckler for pirates, fencers, musketeers, witty duelists, and acrobatic heroes who make combat look theatrical.',
    powerSource: 'Martial',
    combatRole: 'Mobile striker',
    magicLevel: 'None',
    difficulty: 'Medium',
    categories: ['Martial', 'Expert', 'Striker', 'Social'],
    aliases: ['fencer', 'duelist', 'pirate', 'musketeer'],
    subclasses: [
      ['Fencer', 'Precise blade work and counterattacks.', ['duel']],
      ['Pirate', 'Boarding actions, dirty tricks, and sea swagger.', ['sea']],
      ['Musketeer', 'Rapier, pistol, and courtly confidence.', ['ranged']],
      ['Braggart', 'Taunts and bravado open enemies up.', ['social']],
      ['Wit', 'Words, timing, and social leverage become weapons.', ['social']],
      ['Gymnast', 'Grapples, flips, and physical stunts.', ['mobility']],
      ['Battledancer', 'Performance and movement blend into attacks.', ['performance']],
      ['Daring Duelist', 'Risky one-on-one excellence.', ['duel']],
    ],
  }),
  cls({
    name: 'Cavalier',
    tagline: 'Oathbound mounted warrior, banner bearer, or battlefield knight.',
    shortDescription: 'Cavaliers challenge enemies, protect allies, and command attention from horseback or foot.',
    longDescription:
      'Use Cavalier for knights, lancers, beast riders, banner champions, and warriors defined by service, order, or a personal code.',
    powerSource: 'Martial',
    combatRole: 'Frontline defender',
    magicLevel: 'None',
    difficulty: 'Medium',
    categories: ['Martial', 'Frontline', 'Mounted', 'Support'],
    aliases: ['knight', 'lancer', 'banner knight', 'mounted warrior'],
    subclasses: [
      ['Banner Knight', 'Inspires allies through standards and presence.', ['support']],
      ['Lancer', 'Charge attacks and reach weapon mastery.', ['mounted']],
      ['Beast Rider', 'Bonds with unusual mounts and companions.', ['companion']],
      ['Dragon Knight', 'Rides or channels draconic power.', ['dragon']],
      ['Green Knight', 'Nature-bound chivalry and old oaths.', ['primal']],
      ['Hell Knight', 'Fear, law, armor, and harsh discipline.', ['dark']],
      ['Shield Champion', 'Defensive challenges and ally protection.', ['protector']],
      ['Ronin', 'Masterless warrior with personal honor.', ['wanderer']],
    ],
  }),
  cls({
    name: 'Guardian',
    tagline: 'Dedicated protector built around defense and ally safety.',
    shortDescription: 'Guardians absorb pressure, lock down enemies, and make others harder to hurt.',
    longDescription:
      'Use Guardian for bodyguards, shield saints, stone wardens, spellbreakers, and characters whose heroism is standing in the way.',
    powerSource: 'Martial or mystical',
    combatRole: 'Defender support',
    magicLevel: 'Low',
    difficulty: 'Easy',
    categories: ['Martial', 'Frontline', 'Support'],
    aliases: ['defender', 'tank', 'sentinel', 'bodyguard'],
    subclasses: [
      ['Sentinel', 'Locks enemies down and punishes movement.', ['defender']],
      ['Bulwark', 'Heavy armor, shields, and pure staying power.', ['defender']],
      ['Bodyguard', 'Interposes against attacks on allies.', ['protector']],
      ['Spellbreaker', 'Disrupts enemy casters and magical effects.', ['anti-magic']],
      ['Stone Warden', 'Earthy resilience and unmoving defense.', ['elemental']],
      ['Spirit Shield', 'Protective spirits soften harm.', ['spirit']],
      ['Iron Wall', 'Formation defense and impossible durability.', ['formation']],
      ['Oathbound Defender', 'A vow empowers protection.', ['oath']],
    ],
  }),
  cls({
    name: 'Marshal',
    tagline: 'Battlefield leader who turns party coordination into power.',
    shortDescription: 'Marshals direct allies, set tempo, and make the whole party more effective.',
    longDescription:
      'Use Marshal for warlords, tacticians, banner lords, field commanders, and non-magical support characters.',
    powerSource: 'Martial',
    combatRole: 'Support leader',
    magicLevel: 'None',
    difficulty: 'Medium',
    categories: ['Martial', 'Support', 'Social'],
    aliases: ['warlord', 'commander', 'tactician', 'leader'],
    subclasses: [
      ['Tactician', 'Positions allies and opens planned strikes.', ['tactics']],
      ['Inspiring Leader', 'Bolsters morale and recovery.', ['support']],
      ['Dread Commander', 'Uses fear and pressure to control enemies.', ['dark']],
      ['Banner Lord', 'Battle standards anchor the party.', ['banner']],
      ['Field Medic', 'Practical healing under fire.', ['healing']],
      ['Siege Captain', 'Engines, formations, and battlefield objectives.', ['siege']],
      ['Pack Leader', 'Coordinates allies as a hunting unit.', ['teamwork']],
      ['Warlord', 'Classic command, orders, and martial support.', ['leader']],
    ],
  }),
  cls({
    name: 'Cleric',
    tagline: 'Divine caster shaped by faith, domain, and sacred duty.',
    shortDescription: 'Clerics heal, protect, curse, bless, and channel a deity, ideal, or cosmic force.',
    longDescription:
      'Use Cleric for priests, battle saints, miracle workers, cult defectors, divine scholars, and any character whose magic comes from devotion.',
    powerSource: 'Divine',
    combatRole: 'Support caster',
    magicLevel: 'Full',
    difficulty: 'Easy',
    categories: ['Beginner Friendly', 'Divine', 'Support', 'Controller'],
    aliases: ['priest', 'healer', 'holy caster', 'divine caster'],
    subclasses: [
      ['Life', 'Healing, restoration, and keeping allies alive.', ['beginner']],
      ['Light', 'Radiance, fire, revelation, and anti-darkness.', ['radiant']],
      ['War', 'Armor, weapons, and battlefield blessings.', ['frontline']],
      ['Tempest', 'Storms, thunder, lightning, and sea wrath.', ['elemental']],
      ['Trickery', 'Illusions, misdirection, and sacred deception.', ['trick']],
      ['Knowledge', 'Lore, divination, languages, and hidden truth.', ['lore']],
      ['Nature', 'Plants, beasts, weather, and old faith.', ['primal']],
      ['Death', 'Necrotic force and the boundary of mortality.', ['dark']],
      ['Grave', 'Protects the dying and punishes stolen life.', ['death']],
      ['Forge', 'Craft, armor, fire, and sacred making.', ['craft']],
      ['Order', 'Command, law, hierarchy, and coordinated strikes.', ['law']],
      ['Peace', 'Bonds, protection, and conflict softening.', ['support']],
      ['Twilight', 'Night, shelter, dreams, and liminal safety.', ['dark']],
      ['Travel', 'Roads, speed, luck, and safe passage.', ['travel']],
      ['Luck', 'Fortune, rerolls, and improbable timing.', ['luck']],
      ['Protection', 'Wards, shields, and defensive miracles.', ['protector']],
    ],
  }),
  cls({
    name: 'Paladin',
    tagline: 'Oathbound champion with martial skill and divine force.',
    shortDescription: 'Paladins combine weapons, armor, healing, smites, auras, and strong ideals.',
    longDescription:
      'Use Paladin for holy knights, oathbreakers, redeemers, conquerors, watchers, liberation heroes, and characters whose promise has power.',
    powerSource: 'Divine and martial',
    combatRole: 'Frontline support',
    magicLevel: 'Half',
    difficulty: 'Easy',
    categories: ['Beginner Friendly', 'Martial', 'Divine', 'Frontline', 'Support'],
    aliases: ['holy knight', 'crusader', 'oath knight', 'templar'],
    subclasses: [
      ['Devotion', 'Classic honor, light, courage, and protection.', ['beginner']],
      ['Ancients', 'Fey, nature, hope, and anti-magic resilience.', ['primal']],
      ['Vengeance', 'Pursuit, judgment, and single-target pressure.', ['striker']],
      ['Conquest', 'Fear, domination, and iron rule.', ['dark']],
      ['Redemption', 'Mercy, restraint, and second chances.', ['support']],
      ['Glory', 'Heroic athleticism and public legend.', ['social']],
      ['Crown', 'Law, loyalty, and protecting civilization.', ['law']],
      ['Watchers', 'Guards the world from planar threats.', ['planar']],
      ['Liberation', 'Freedom, escape, and breaking chains.', ['freedom']],
      ['Tyranny', 'Cruel authority and enforced obedience.', ['dark']],
      ['Blackguard', 'Fallen or anti-paladin dark champion.', ['dark']],
      ['Dragon Oath', 'Draconic ideal, breath, and presence.', ['dragon']],
    ],
  }),
  cls({
    name: 'Oracle',
    tagline: 'Divine mystery bearer blessed and cursed by revelation.',
    shortDescription: 'Oracles cast divine magic through a mystery that also marks their life with a curse.',
    longDescription:
      'Use Oracle for prophetic, haunted, fate-touched, elemental, ancestral, or cosmic characters who did not simply choose their power.',
    powerSource: 'Divine',
    combatRole: 'Support or controller',
    magicLevel: 'Full',
    difficulty: 'Medium',
    categories: ['Divine', 'Occult', 'Support', 'Controller'],
    aliases: ['prophet', 'seer', 'mystery caster', 'curse bearer'],
    subclasses: [
      ['Life Mystery', 'Healing power with a life-draining burden.', ['healing']],
      ['Battle Mystery', 'Visions of war and martial revelation.', ['frontline']],
      ['Flame Mystery', 'Fire signs, burning omens, and ash.', ['elemental']],
      ['Waves Mystery', 'Water, cold, tides, and drowning prophecy.', ['elemental']],
      ['Wind Mystery', 'Air, storms, whispers, and open roads.', ['elemental']],
      ['Stone Mystery', 'Earth, endurance, and buried secrets.', ['elemental']],
      ['Bones Mystery', 'Death, undead, and mortality signs.', ['dark']],
      ['Cosmos Mystery', 'Stars, gravity, and impossible distance.', ['cosmic']],
      ['Ancestors Mystery', 'Family spirits speak and fight through you.', ['spirit']],
      ['Time Mystery', 'Premonitions, echoes, and age.', ['time']],
      ['Cursebearer', 'The curse itself is the main engine of power.', ['curse']],
    ],
  }),
  cls({
    name: 'Inquisitor',
    tagline: 'Divine investigator, hunter, judge, and threat detector.',
    shortDescription: 'Inquisitors mix skills, judgment, divine magic, and relentless pursuit.',
    longDescription:
      'Use Inquisitor for witch hunters, demon hunters, truth seekers, exorcists, lawkeepers, and morally sharp investigators.',
    powerSource: 'Divine and expert',
    combatRole: 'Striker scout',
    magicLevel: 'Half',
    difficulty: 'Medium',
    categories: ['Divine', 'Expert', 'Scout', 'Striker'],
    aliases: ['witch hunter', 'judge', 'exorcist', 'heresy hunter'],
    subclasses: [
      ['Heretic Hunter', 'Tracks forbidden faiths and dangerous cults.', ['investigation']],
      ['Demon Hunter', 'Specializes in fiends and corruption.', ['fiend']],
      ['Truth Seeker', 'Detects lies, secrets, and hidden motives.', ['social']],
      ['Witchfinder', 'Counters curses, hexes, and occult magic.', ['anti-magic']],
      ['Sanctified Stalker', 'Stealthy sacred hunter.', ['stealth']],
      ['Exorcist', 'Banishes spirits, possession, and hauntings.', ['spirit']],
      ['Lawkeeper', 'Judgment, contracts, and civil authority.', ['law']],
      ['Zealot', 'Fanatical pursuit and dangerous conviction.', ['dark']],
    ],
  }),
  cls({
    name: 'Warpriest',
    tagline: 'Battle cleric who blesses weapons and survives the front line.',
    shortDescription: 'Warpriests are divine martial casters with sacred weapons, armor, and battlefield rites.',
    longDescription:
      'Use Warpriest for combat chaplains, crusaders, relic bearers, holy brawlers, and priests whose prayers are spoken in melee.',
    powerSource: 'Divine and martial',
    combatRole: 'Frontline support',
    magicLevel: 'Half',
    difficulty: 'Medium',
    categories: ['Divine', 'Martial', 'Frontline', 'Support'],
    aliases: ['battle priest', 'chaplain', 'crusader', 'holy warrior'],
    subclasses: [
      ['Crusader', 'Armor, weapons, and relentless holy advance.', ['frontline']],
      ['Sacred Fist', 'Unarmed divine strikes and disciplined body.', ['monk']],
      ['Shield Priest', 'Defensive blessings and shield work.', ['protector']],
      ['Battle Chaplain', 'Bolsters allies in the thick of combat.', ['support']],
      ['Relic Bearer', 'Sacred item channels blessings and wrath.', ['relic']],
      ['Hospitaler', 'Combat medic with divine recovery.', ['healing']],
      ['Doom Priest', 'Curses, judgment, and grim omens.', ['dark']],
    ],
  }),
  cls({
    name: 'Shaman',
    tagline: 'Spirit negotiator who blends primal, divine, and occult themes.',
    shortDescription: 'Shamans speak with spirits, channel totems, heal, curse, and mediate unseen forces.',
    longDescription:
      'Use Shaman for ancestor callers, spirit doctors, elemental mediums, and wild mystics whose magic comes from relationships with the unseen.',
    powerSource: 'Primal, divine, or occult',
    combatRole: 'Support controller',
    magicLevel: 'Full',
    difficulty: 'Medium',
    categories: ['Primal', 'Divine', 'Occult', 'Support', 'Controller'],
    aliases: ['spirit guide', 'witch doctor', 'totem speaker', 'medium'],
    subclasses: [
      ['Spirit Guide', 'Flexible spirit bargains and guidance.', ['spirit']],
      ['Totem Speaker', 'Animal or clan totems shape magic.', ['totem']],
      ['Ancestor Caller', 'Family dead advise, protect, and judge.', ['ancestor']],
      ['Elemental Medium', 'Channels fire, water, air, or earth spirits.', ['elemental']],
      ['Witch Doctor', 'Cures, curses, masks, and village medicine.', ['healing']],
      ['Speaker for Dead', 'Deals with ghosts and unfinished business.', ['spirit']],
      ['Spirit Warden', 'Protects places from hostile unseen things.', ['protector']],
    ],
  }),
  cls({
    name: 'Wizard',
    tagline: 'Studied arcane caster with the broadest magical toolkit.',
    shortDescription: 'Wizards solve problems through spells, preparation, research, and arcane theory.',
    longDescription:
      'Use Wizard for scholars, tower mages, battlefield casters, ritualists, necromancers, illusionists, chronomancers, and anyone who learned magic through study.',
    powerSource: 'Arcane',
    combatRole: 'Controller caster',
    magicLevel: 'Full',
    difficulty: 'Medium',
    categories: ['Beginner Friendly', 'Arcane', 'Controller', 'Support'],
    aliases: ['mage', 'arcanist', 'spellcaster', 'scholar mage'],
    subclasses: [
      ['Abjurer', 'Wards, shields, counters, and protection.', ['defense']],
      ['Conjurer', 'Summons creatures, objects, and portals.', ['summon']],
      ['Diviner', 'Omens, probability, and seeing what matters.', ['seer']],
      ['Enchanter', 'Charm, compulsion, and social magic.', ['social']],
      ['Evoker', 'Direct damage with controlled spell blasts.', ['beginner']],
      ['Illusionist', 'False senses, tricks, and impossible images.', ['illusion']],
      ['Necromancer', 'Death magic, undead, and life-force theft.', ['dark']],
      ['Transmuter', 'Changes matter, bodies, and properties.', ['change']],
      ['Bladesinger', 'Swordplay and defensive song magic.', ['hybrid']],
      ['War Mage', 'Battlefield defense, blasts, and counterplay.', ['battle']],
      ['Scribe', 'Living spellbook, flexible damage, and scholarship.', ['lore']],
      ['Chronomancer', 'Time, rerolls, pauses, and future echoes.', ['time']],
      ['Graviturgist', 'Gravity, weight, motion, and crushing force.', ['gravity']],
      ['Rune Sage', 'Written symbols and ancient magical grammar.', ['runes']],
    ],
  }),
  cls({
    name: 'Sorcerer',
    tagline: 'Innate caster powered by bloodline, soul, or magical accident.',
    shortDescription: 'Sorcerers are natural magic users with intense theme and flexible spell expression.',
    longDescription:
      'Use Sorcerer for characters whose magic is inherited, awakened, unstable, cosmic, draconic, shadowed, storm-touched, or built into their soul.',
    powerSource: 'Arcane or innate',
    combatRole: 'Striker caster',
    magicLevel: 'Full',
    difficulty: 'Easy',
    categories: ['Beginner Friendly', 'Arcane', 'Striker', 'Elemental'],
    aliases: ['innate mage', 'bloodline caster', 'wild mage'],
    subclasses: [
      ['Draconic Bloodline', 'Dragon ancestry, scales, breath, and element.', ['dragon']],
      ['Wild Magic', 'Chaotic surges and unpredictable effects.', ['chaos']],
      ['Divine Soul', 'Innate magic touched by gods or celestials.', ['divine']],
      ['Shadow Magic', 'Darkness, survival, and undead-adjacent power.', ['dark']],
      ['Storm Soul', 'Thunder, lightning, wind, and weather.', ['elemental']],
      ['Aberrant Mind', 'Telepathy, alien thoughts, and psychic spells.', ['psionic']],
      ['Clockwork Soul', 'Order, gears, balance, and probability repair.', ['order']],
      ['Phoenix Blooded', 'Fire, rebirth, and blazing resilience.', ['fire']],
      ['Giant Blooded', 'Huge ancestral force and elemental marks.', ['giant']],
      ['Fey Blooded', 'Charm, glamour, and strange fey instincts.', ['fey']],
      ['Elemental Soul', 'A focused element defines the caster.', ['elemental']],
    ],
  }),
  cls({
    name: 'Warlock',
    tagline: 'Pact caster empowered by a patron, bargain, or forbidden bond.',
    shortDescription: 'Warlocks have compact, flavorful magic and strong story hooks through patrons.',
    longDescription:
      'Use Warlock for characters bound to fiends, fey, celestials, dead gods, ocean things, genies, dragons, sentient weapons, or unknowable powers.',
    powerSource: 'Pact magic',
    combatRole: 'Striker controller',
    magicLevel: 'Full',
    difficulty: 'Easy',
    categories: ['Beginner Friendly', 'Arcane', 'Occult', 'Striker', 'Dark'],
    aliases: ['pact mage', 'hexer', 'patron caster', 'occult pact'],
    subclasses: [
      ['Fiend Pact', 'Hellfire, bargains, and infernal durability.', ['fiend']],
      ['Archfey Pact', 'Charm, fear, glamour, and fey escape.', ['fey']],
      ['Great Old One Pact', 'Telepathy and alien cosmic horror.', ['cosmic']],
      ['Celestial Pact', 'Radiant support through a holy patron.', ['divine']],
      ['Hexblade', 'Cursed weapon, armor, and shadowed melee.', ['weapon']],
      ['Undead Pact', 'Fear, undeath, and grave resilience.', ['dark']],
      ['Genie Pact', 'Elemental patron, vessel, and wish flavor.', ['elemental']],
      ['Fathomless Pact', 'Deep water, tentacles, cold, and pressure.', ['sea']],
      ['Dragon Pact', 'Draconic bargain and elemental authority.', ['dragon']],
      ['Vestige Pact', 'Forgotten spirits and dead powers lend magic.', ['spirit']],
    ],
  }),
  cls({
    name: 'Witch',
    tagline: 'Patron-taught hex caster with familiar, curses, and strange magic.',
    shortDescription: 'Witches use hexes, familiars, rituals, folk magic, and occult bargains.',
    longDescription:
      'Use Witch for hedge mages, coven members, curse weavers, winter witches, green witches, grave witches, and characters who learned magic through a patron or taboo tradition.',
    powerSource: 'Occult or arcane',
    combatRole: 'Controller support',
    magicLevel: 'Full',
    difficulty: 'Medium',
    categories: ['Arcane', 'Occult', 'Controller', 'Support', 'Dark'],
    aliases: ['hexer', 'coven mage', 'hag student', 'curse caster'],
    subclasses: [
      ['Familiar Patron', 'Familiar acts as conduit, teacher, and spy.', ['familiar']],
      ['Hagbound', 'Hag influence, bargains, and eerie transformation.', ['dark']],
      ['Coven Witch', 'Group magic, rites, and shared rituals.', ['coven']],
      ['Hex Doctor', 'Curses and cures from folk medicine.', ['healing']],
      ['Winter Witch', 'Cold, snow, silence, and preservation.', ['ice']],
      ['Green Witch', 'Plants, herbs, animals, and old growth.', ['primal']],
      ['Grave Witch', 'Ghosts, bones, and deathbed magic.', ['dark']],
      ['Star Witch', 'Omens, night skies, and distant lights.', ['cosmic']],
      ['Curseweaver', 'Advanced hexes and layered misfortune.', ['curse']],
    ],
  }),
  cls({
    name: 'Magus',
    tagline: 'Arcane warrior who channels spells through weapons.',
    shortDescription: 'Magi blend melee or ranged combat with spell delivery and magical defenses.',
    longDescription:
      'Use Magus for spellblades, sword saints, arcane archers, staff fighters, mounted spell knights, and warriors whose blade is part of the spell.',
    powerSource: 'Arcane and martial',
    combatRole: 'Hybrid striker',
    magicLevel: 'Half',
    difficulty: 'Advanced',
    categories: ['Arcane', 'Martial', 'Hybrid', 'Striker'],
    aliases: ['spellblade', 'eldritch knight', 'battle mage', 'arcane warrior'],
    subclasses: [
      ['Spellblade', 'Classic weapon strikes loaded with spells.', ['weapon']],
      ['Eldritch Archer', 'Delivers magic through arrows and bolts.', ['ranged']],
      ['Sword Saint', 'One blade, perfect focus, high skill ceiling.', ['duel']],
      ['Hexcrafter', 'Adds curses and hexes to weapon magic.', ['dark']],
      ['Staff Magus', 'Staff forms, reach, and defensive casting.', ['staff']],
      ['Spell Dancer', 'Mobility and performance with arcane strikes.', ['mobility']],
      ['Arcane Rider', 'Mounted combat with spell delivery.', ['mounted']],
      ['Elemental Blade', 'Weapon channels one or more elements.', ['elemental']],
      ['Aegis Knight', 'Defensive wards and armored spell combat.', ['defense']],
    ],
  }),
  cls({
    name: 'Summoner',
    tagline: 'Bonded caller whose companion is a major part of the character.',
    shortDescription: 'Summoners fight beside an eidolon, spirit, construct, dragon, or other powerful ally.',
    longDescription:
      'Use Summoner when the character concept is built around a signature companion, shared soul, or called creature.',
    powerSource: 'Arcane, occult, or primal',
    combatRole: 'Summoner support',
    magicLevel: 'Full',
    difficulty: 'Advanced',
    categories: ['Arcane', 'Occult', 'Summoner', 'Support'],
    aliases: ['caller', 'eidolon master', 'conjurer', 'pet class'],
    subclasses: [
      ['Beast Eidolon', 'Animalistic companion with claws and senses.', ['beast']],
      ['Dragon Eidolon', 'Draconic companion with elemental presence.', ['dragon']],
      ['Angel Eidolon', 'Celestial companion and radiant support.', ['divine']],
      ['Demon Eidolon', 'Fiendish companion with dangerous instincts.', ['fiend']],
      ['Fey Eidolon', 'Tricky fey ally with glamour and movement.', ['fey']],
      ['Construct Eidolon', 'Built companion of metal, clay, or wood.', ['construct']],
      ['Shadow Eidolon', 'Umbral companion that hunts from darkness.', ['dark']],
      ['Swarm Caller', 'Commands many small creatures as one presence.', ['swarm']],
    ],
  }),
  cls({
    name: 'Elementalist',
    tagline: 'Focused caster or martial adept built around elemental power.',
    shortDescription: 'Elementalists make a single element, or a small cycle of elements, the core identity.',
    longDescription:
      'Use Elementalist for fire slingers, water shapers, earth defenders, storm callers, void adepts, metal benders, and other elemental specialists.',
    powerSource: 'Elemental',
    combatRole: 'Striker controller',
    magicLevel: 'Variable',
    difficulty: 'Medium',
    categories: ['Arcane', 'Primal', 'Elemental', 'Controller', 'Striker'],
    aliases: ['kineticist', 'element bender', 'storm caller', 'pyromancer'],
    subclasses: [
      ['Fire', 'Flames, heat, ash, and explosive damage.', ['fire']],
      ['Water', 'Waves, healing flow, ice, and control.', ['water']],
      ['Air', 'Wind, flight, speed, and lightning setup.', ['air']],
      ['Earth', 'Stone, metal, defense, and tremors.', ['earth']],
      ['Wood', 'Plants, growth, roots, and renewal.', ['wood']],
      ['Metal', 'Blades, armor, magnetism, and forged force.', ['metal']],
      ['Lightning', 'Speed, shocks, chains, and storm pressure.', ['lightning']],
      ['Ice', 'Cold, slow, preservation, and brittle terrain.', ['ice']],
      ['Lava', 'Molten earth and destructive area control.', ['fire']],
      ['Storm', 'Wind, rain, thunder, and battlefield weather.', ['storm']],
      ['Void', 'Absence, gravity, silence, and negative space.', ['void']],
      ['Aether', 'Telekinetic force and invisible pressure.', ['force']],
    ],
  }),
  cls({
    name: 'Druid',
    tagline: 'Primal caster tied to nature, beasts, land, stars, or decay.',
    shortDescription: 'Druids use shapeshifting, nature magic, healing, weather, beasts, and terrain control.',
    longDescription:
      'Use Druid for forest guardians, moon shapeshifters, star prophets, wildfire renewers, spore mystics, sea priests, and wild hermits.',
    powerSource: 'Primal',
    combatRole: 'Support controller',
    magicLevel: 'Full',
    difficulty: 'Medium',
    categories: ['Beginner Friendly', 'Primal', 'Support', 'Controller', 'Summoner'],
    aliases: ['nature priest', 'shapeshifter', 'wild mage', 'green mage'],
    subclasses: [
      ['Land', 'Terrain-focused spellcasting and local knowledge.', ['beginner']],
      ['Moon', 'Powerful animal forms and melee shapeshifting.', ['beast']],
      ['Stars', 'Constellations, omens, healing, and radiant shots.', ['cosmic']],
      ['Wildfire', 'Fire companion, destruction, and renewal.', ['fire']],
      ['Spores', 'Fungus, decay, undeath, and close-range aura.', ['dark']],
      ['Shepherd', 'Animal spirits, summons, and party support.', ['summon']],
      ['Dreams', 'Fey healing, shelter, and dream roads.', ['fey']],
      ['Sea', 'Tides, storms, currents, and ocean creatures.', ['sea']],
      ['Storm', 'Weather control, lightning, and sky wrath.', ['storm']],
      ['Blight', 'Rot, corrupted nature, and parasitic power.', ['dark']],
      ['Shapeshifter', 'Form-changing is the main identity.', ['beast']],
      ['Greenwarden', 'Plants, guardianship, and ancient groves.', ['protector']],
    ],
  }),
  cls({
    name: 'Warden',
    tagline: 'Primal defender bound to a place, season, or natural force.',
    shortDescription: 'Wardens protect land and allies with tough bodies and nature-infused defense.',
    longDescription:
      'Use Warden for forest sentinels, stone guardians, winter knights, beast wardens, and primal protectors.',
    powerSource: 'Primal and martial',
    combatRole: 'Frontline defender',
    magicLevel: 'Low',
    difficulty: 'Easy',
    categories: ['Primal', 'Martial', 'Frontline', 'Support'],
    aliases: ['nature guardian', 'primal defender', 'green knight'],
    subclasses: [
      ['Forest Warden', 'Plants, roots, and woodland protection.', ['plant']],
      ['Stone Warden', 'Earth armor and immovable defense.', ['earth']],
      ['Storm Warden', 'Lightning, thunder, and punishing movement.', ['storm']],
      ['Beast Warden', 'Animal aspects and feral defense.', ['beast']],
      ['Winter Warden', 'Cold, endurance, and slowing enemies.', ['ice']],
      ['Spirit Warden', 'Primal spirits shield the party.', ['spirit']],
      ['Guardian of the Wild', 'Classic wilderness protector.', ['protector']],
    ],
  }),
  cls({
    name: 'Beastmaster',
    tagline: 'Companion-focused adventurer whose bond defines the playstyle.',
    shortDescription: 'Beastmasters fight, scout, travel, and survive through a trusted creature companion.',
    longDescription:
      'Use Beastmaster when the animal or drake ally is central, not just a small feature. It supports scouts, hunters, falconers, riders, and pack leaders.',
    powerSource: 'Martial and primal',
    combatRole: 'Scout support',
    magicLevel: 'Low',
    difficulty: 'Medium',
    categories: ['Primal', 'Martial', 'Scout', 'Support', 'Summoner', 'Mounted'],
    aliases: ['animal handler', 'falconer', 'pack leader', 'companion class'],
    subclasses: [
      ['Wolf Bonded', 'Pack tactics, tracking, and loyal pressure.', ['wolf']],
      ['Hawk Bonded', 'Aerial scouting and precision openings.', ['bird']],
      ['Bear Bonded', 'Durable companion and protective force.', ['bear']],
      ['Drake Bonded', 'Small dragon ally and elemental growth.', ['dragon']],
      ['Swarm Friend', 'Many small companions as scouts and distractions.', ['swarm']],
      ['Mounted Hunter', 'Companion doubles as a mount.', ['mounted']],
      ['Primal Companion', 'Spirit-touched beast with magical traits.', ['spirit']],
    ],
  }),
  cls({
    name: 'Shapeshifter',
    tagline: 'Identity built around transforming the body.',
    shortDescription: 'Shapeshifters become beasts, hybrids, monsters, or many forms to adapt and fight.',
    longDescription:
      'Use Shapeshifter for werebeasts, skinwalkers, moon-changers, chimera souls, predator forms, and characters whose body is mutable.',
    powerSource: 'Primal or occult',
    combatRole: 'Frontline scout',
    magicLevel: 'Variable',
    difficulty: 'Medium',
    categories: ['Primal', 'Occult', 'Hybrid', 'Frontline', 'Scout'],
    aliases: ['werebeast', 'skinwalker', 'changer', 'lycan'],
    subclasses: [
      ['Werebeast', 'Hybrid curse or gift with bestial strength.', ['beast']],
      ['Skinwalker', 'Wears or borrows forms through old rites.', ['ritual']],
      ['Moon Changer', 'Lunar phases guide transformations.', ['moon']],
      ['Chimera Soul', 'Combines traits from many creatures.', ['monster']],
      ['Beast Adept', 'Disciplined partial shifts and senses.', ['beast']],
      ['Predator Form', 'Stalking, pouncing, and apex hunting.', ['stealth']],
      ['Many Forms', 'Broad transformation utility.', ['utility']],
    ],
  }),
  cls({
    name: 'Psychic',
    tagline: 'Mind-powered occult caster with emotion, thought, and force.',
    shortDescription: 'Psychics use telepathy, telekinesis, empathy, dreams, and mental attacks.',
    longDescription:
      'Use Psychic for seers, dreamwalkers, telepaths, aura readers, mindbreakers, and characters whose power comes from consciousness itself.',
    powerSource: 'Occult or psionic',
    combatRole: 'Controller support',
    magicLevel: 'Full',
    difficulty: 'Medium',
    categories: ['Occult', 'Controller', 'Support'],
    aliases: ['mentalist', 'telepath', 'psionic caster', 'mind mage'],
    subclasses: [
      ['Telepath', 'Reads, sends, and pressures thoughts.', ['mind']],
      ['Telekinetic', 'Moves objects and enemies with mental force.', ['force']],
      ['Seer', 'Premonitions, remote sight, and hidden truth.', ['seer']],
      ['Empath', 'Emotion reading, calming, and psychic support.', ['support']],
      ['Dreamwalker', 'Dreams, sleep, nightmares, and surreal travel.', ['dream']],
      ['Mindbreaker', 'Psychic damage, fear, and mental collapse.', ['dark']],
      ['Aura Reader', 'Reads emotional, magical, and spiritual traces.', ['investigation']],
      ['Thoughtform Summoner', 'Creates mental constructs or allies.', ['summon']],
    ],
  }),
  cls({
    name: 'Psion',
    tagline: 'Disciplined psionic specialist with focused mental powers.',
    shortDescription: 'Psions are structured mind adepts, often more technical than mystical psychics.',
    longDescription:
      'Use Psion for teleporters, kineticists, self-shapers, creators, seers, telepaths, and soulknife-adjacent mental warriors.',
    powerSource: 'Psionic',
    combatRole: 'Variable specialist',
    magicLevel: 'Variable',
    difficulty: 'Advanced',
    categories: ['Occult', 'Hybrid', 'Controller', 'Striker'],
    aliases: ['psionicist', 'mind adept', 'discipline user'],
    subclasses: [
      ['Nomad', 'Space, teleportation, and impossible movement.', ['travel']],
      ['Kineticist', 'Raw force, energy, and destructive projection.', ['force']],
      ['Egoist', 'Self-transformation and body control.', ['body']],
      ['Shaper', 'Creates objects, constructs, and thoughtforms.', ['summon']],
      ['Seer', 'Information, future glimpses, and perception.', ['seer']],
      ['Telepath', 'Communication, control, and mental intrusion.', ['mind']],
      ['Metamind', 'Pure power reserves and psionic amplification.', ['power']],
      ['Soulknife', 'Psychic weaponry and silent strikes.', ['weapon']],
    ],
  }),
  cls({
    name: 'Medium',
    tagline: 'Channeler of spirits, ghosts, ancestors, and legendary echoes.',
    shortDescription: 'Mediums borrow power, skills, and personality traces from the dead or mythic spirits.',
    longDescription:
      'Use Medium for haunted vessels, ancestor mediums, ghost negotiators, and heroes who become more than one self.',
    powerSource: 'Occult',
    combatRole: 'Flexible support',
    magicLevel: 'Variable',
    difficulty: 'Advanced',
    categories: ['Occult', 'Support', 'Social'],
    aliases: ['spirit medium', 'channeler', 'haunted vessel'],
    subclasses: [
      ['Ancestor Medium', 'Family dead lend guidance and skills.', ['ancestor']],
      ['Ghost Channeler', 'Deals directly with ghosts and haunts.', ['spirit']],
      ['Hero Spirit', 'Legendary archetypes grant shifting roles.', ['legend']],
      ['Oracle Medium', 'Prophecy through spirits and omens.', ['divine']],
      ['Haunted Vessel', 'Possession risk and borrowed power.', ['dark']],
      ['Spirit Negotiator', 'Social broker for the living and dead.', ['social']],
    ],
  }),
  cls({
    name: 'Occultist',
    tagline: 'Relic binder and implement mage who draws power from objects.',
    shortDescription: 'Occultists use significant items, symbols, and artifacts as magical engines.',
    longDescription:
      'Use Occultist for museum mages, relic hunters, implement casters, rune collectors, and planar scholars whose tools remember power.',
    powerSource: 'Occult',
    combatRole: 'Utility support',
    magicLevel: 'Full',
    difficulty: 'Advanced',
    categories: ['Occult', 'Expert', 'Support', 'Crafting'],
    aliases: ['relic mage', 'implement caster', 'artifact binder'],
    subclasses: [
      ['Relic Binder', 'Binds and awakens old objects.', ['relic']],
      ['Implement Mage', 'Uses chosen tools as spell conduits.', ['tool']],
      ['Rune Collector', 'Stores occult meaning in carved signs.', ['runes']],
      ['Planar Scholar', 'Objects tied to planes and portals.', ['planar']],
      ['Artifact Channeler', 'Risky power through legendary items.', ['artifact']],
    ],
  }),
  cls({
    name: 'Mesmerist',
    tagline: 'Hypnotic occult manipulator of attention, fear, and belief.',
    shortDescription: 'Mesmerists use gaze, suggestion, illusion, and psychic influence.',
    longDescription:
      'Use Mesmerist for hypnotists, eerie performers, fearmongers, charmers, and nightmare weavers.',
    powerSource: 'Occult',
    combatRole: 'Controller social',
    magicLevel: 'Half',
    difficulty: 'Medium',
    categories: ['Occult', 'Controller', 'Social'],
    aliases: ['hypnotist', 'mind charmer', 'illusionist'],
    subclasses: [
      ['Hypnotist', 'Trance, suggestions, and attention control.', ['social']],
      ['Fearmonger', 'Stacks dread and breaks morale.', ['fear']],
      ['Charmer', 'Social influence and emotional pressure.', ['social']],
      ['Illusionist', 'False sensory cues and misdirection.', ['illusion']],
      ['Psychic Duelist', 'Mental contests and focused psychic combat.', ['psionic']],
      ['Nightmare Weaver', 'Sleep, terror, and dream imagery.', ['dark']],
    ],
  }),
  cls({
    name: 'Necromancer',
    tagline: 'Death mage focused on bones, spirits, blood, plague, or undeath.',
    shortDescription: 'Necromancers control life-force, speak with the dead, raise minions, and wield decay.',
    longDescription:
      'Use Necromancer for dark wizards, grave priests, bone lords, plague doctors, spirit binders, or characters seeking to redeem the dead.',
    powerSource: 'Arcane, divine, or occult',
    combatRole: 'Controller summoner',
    magicLevel: 'Full',
    difficulty: 'Advanced',
    categories: ['Arcane', 'Occult', 'Dark', 'Controller', 'Summoner'],
    aliases: ['death mage', 'bone mage', 'grave caster'],
    subclasses: [
      ['Bone Lord', 'Bones, skeletons, armor, and brittle control.', ['bone']],
      ['Plague Master', 'Disease, rot, and attrition.', ['plague']],
      ['Spirit Binder', 'Ghosts, souls, and bargains with the dead.', ['spirit']],
      ['Grave Knight', 'Armored necromantic warrior.', ['frontline']],
      ['Blood Mage', 'Life-force, sacrifice, and blood rites.', ['blood']],
      ['Lich Aspirant', 'Forbidden immortality and arcane undeath.', ['dark']],
      ['Redeemer of Dead', 'Rest, forgiveness, and gentle necromancy.', ['support']],
    ],
  }),
  cls({
    name: 'Bard',
    tagline: 'Performer, storyteller, skill expert, and flexible support caster.',
    shortDescription: 'Bards inspire allies, manipulate scenes, collect lore, and solve problems socially.',
    longDescription:
      'Use Bard for musicians, poets, duelists, spies, lorekeepers, jests, dirge singers, spirits speakers, and charismatic adventurers.',
    powerSource: 'Arcane and expert',
    combatRole: 'Support social',
    magicLevel: 'Full',
    difficulty: 'Easy',
    categories: ['Beginner Friendly', 'Arcane', 'Expert', 'Support', 'Social'],
    aliases: ['performer', 'skald', 'minstrel', 'storyteller'],
    subclasses: [
      ['Lore', 'Skills, secrets, and magical flexibility.', ['beginner']],
      ['Valor', 'Battlefield inspiration and weapon competence.', ['martial']],
      ['Swords', 'Blade flourishes and stylish melee.', ['duel']],
      ['Glamour', 'Fey beauty, charm, and repositioning allies.', ['fey']],
      ['Whispers', 'Fear, secrets, and social infiltration.', ['dark']],
      ['Spirits', 'Tales from ghosts and legendary echoes.', ['spirit']],
      ['Creation', 'Animates objects and shapes inspiration into matter.', ['craft']],
      ['Eloquence', 'Perfect words, debate, and persuasion.', ['social']],
      ['Skald', 'War chants and martial song.', ['battle']],
      ['Dirge Singer', 'Death songs, fear, and grief magic.', ['dark']],
      ['Jester', 'Chaos, mockery, and impossible timing.', ['trick']],
      ['Court Poet', 'Politics, etiquette, and refined influence.', ['social']],
    ],
  }),
  cls({
    name: 'Artificer',
    tagline: 'Magical engineer who turns craft into spellcasting.',
    shortDescription: 'Artificers build tools, armor, weapons, constructs, elixirs, and battlefield devices.',
    longDescription:
      'Use Artificer for alchemical inventors, armor builders, gadgeteers, rune smiths, wand makers, trap crafters, and magic item specialists.',
    powerSource: 'Arcane craft',
    combatRole: 'Support crafter',
    magicLevel: 'Half',
    difficulty: 'Medium',
    categories: ['Beginner Friendly', 'Arcane', 'Expert', 'Support', 'Crafting'],
    aliases: ['inventor', 'engineer mage', 'tinkerer', 'magitech'],
    subclasses: [
      ['Alchemist', 'Elixirs, bombs, healing mixtures, and reactions.', ['alchemy']],
      ['Armorer', 'Magical armor suit and defensive tech.', ['frontline']],
      ['Artillerist', 'Cannons, turrets, blasts, and shields.', ['ranged']],
      ['Battle Smith', 'Weapon magic and construct companion.', ['companion']],
      ['Gadgeteer', 'Tools, devices, and clever utility.', ['tools']],
      ['Runesmith', 'Inscribes magic into gear and surfaces.', ['runes']],
      ['Clockwork Engineer', 'Gears, automatons, and precision devices.', ['construct']],
      ['Trapmaker', 'Prepared hazards and battlefield setup.', ['traps']],
      ['Wandsmith', 'Specialized wands and spell-trigger items.', ['wand']],
    ],
  }),
  cls({
    name: 'Alchemist',
    tagline: 'Potion maker, bomber, mutagen crafter, and chemical problem solver.',
    shortDescription: 'Alchemists use bombs, elixirs, poisons, mutagens, healing, and practical science.',
    longDescription:
      'Use Alchemist for grenadiers, plague doctors, poisoners, philosophers, homunculus makers, and anyone who fights from a satchel of volatile answers.',
    powerSource: 'Craft and science',
    combatRole: 'Utility striker',
    magicLevel: 'Low',
    difficulty: 'Medium',
    categories: ['Expert', 'Crafting', 'Striker', 'Support'],
    aliases: ['chemist', 'bomber', 'potion maker', 'mutagenist'],
    subclasses: [
      ['Bomber', 'Explosive damage and splash control.', ['bomb']],
      ['Mutagenist', 'Body-altering brews and risky boosts.', ['body']],
      ['Chirurgeon', 'Medical mixtures and field treatment.', ['healing']],
      ['Toxicologist', 'Poisons, venoms, and antidotes.', ['poison']],
      ['Plague Doctor', 'Disease, masks, and emergency medicine.', ['plague']],
      ['Homunculist', 'Tiny artificial helper and lab companion.', ['construct']],
      ['Philosopher', 'Transmutation theory and universal reagents.', ['lore']],
      ['Grenadier', 'Battlefield thrown weapons and demolition.', ['explosive']],
    ],
  }),
  cls({
    name: 'Investigator',
    tagline: 'Clue-driven expert who wins through analysis and preparation.',
    shortDescription: 'Investigators find facts, read people, exploit weaknesses, and turn knowledge into action.',
    longDescription:
      'Use Investigator for detectives, forensic medics, spies, profilers, archaeologists, occult sleuths, and tactical analysts.',
    powerSource: 'Expert',
    combatRole: 'Scout support',
    magicLevel: 'None',
    difficulty: 'Medium',
    categories: ['Expert', 'Scout', 'Social', 'Support'],
    aliases: ['detective', 'sleuth', 'profiler', 'analyst'],
    subclasses: [
      ['Detective', 'Clues, deduction, and social questioning.', ['investigation']],
      ['Forensic Medic', 'Bodies, wounds, medicine, and evidence.', ['healing']],
      ['Alchemical Sleuth', 'Reagents, bombs, and chemical clues.', ['alchemy']],
      ['Occult Detective', 'Haunts, symbols, curses, and secret cults.', ['occult']],
      ['Spy', 'Cover identities and intelligence networks.', ['social']],
      ['Archaeologist', 'Ruins, traps, relics, and old languages.', ['relic']],
      ['Profiler', 'Reads patterns, motives, and future behavior.', ['social']],
      ['Strategist', 'Planning and enemy weakness exploitation.', ['tactics']],
    ],
  }),
  cls({
    name: 'Scholar',
    tagline: 'Knowledge expert, ritualist, diplomat, and expedition brain.',
    shortDescription: 'Scholars bring lore, languages, maps, research, and noncombat expertise.',
    longDescription:
      'Use Scholar for archivists, cartographers, diplomats, ritualists, linguists, natural philosophers, and relic experts.',
    powerSource: 'Expert',
    combatRole: 'Support utility',
    magicLevel: 'Variable',
    difficulty: 'Medium',
    categories: ['Expert', 'Support', 'Social'],
    aliases: ['lorekeeper', 'sage', 'academic', 'researcher'],
    subclasses: [
      ['Lorekeeper', 'History, legends, and monster knowledge.', ['lore']],
      ['Archivist', 'Documents, records, and hidden references.', ['lore']],
      ['Cartographer', 'Maps, routes, travel, and terrain.', ['travel']],
      ['Ritualist', 'Slow magic, ceremonies, and preparation.', ['ritual']],
      ['Diplomat', 'Negotiation, etiquette, and political memory.', ['social']],
      ['Natural Philosopher', 'Science, nature, and field observation.', ['nature']],
      ['Linguist', 'Languages, codes, translation, and names.', ['language']],
      ['Relic Expert', 'Identifies, handles, and contextualizes artifacts.', ['relic']],
    ],
  }),
  cls({
    name: 'Noble',
    tagline: 'Status, influence, command, and social leverage as a class fantasy.',
    shortDescription: 'Nobles use resources, etiquette, networks, followers, and personal authority.',
    longDescription:
      'Use Noble for heirs, patrons, court duelists, envoys, spymasters, commanders, and social manipulators.',
    powerSource: 'Expert and social',
    combatRole: 'Social support',
    magicLevel: 'None',
    difficulty: 'Medium',
    categories: ['Expert', 'Social', 'Support'],
    aliases: ['aristocrat', 'heir', 'courtier', 'lord'],
    subclasses: [
      ['Diplomat', 'Peace, treaties, and careful language.', ['social']],
      ['Spymaster', 'Information networks and hidden agents.', ['spy']],
      ['Court Duelist', 'Status duels and refined blade work.', ['duel']],
      ['Patron', 'Money, favors, contacts, and sponsorship.', ['resource']],
      ['Commander', 'Authority, orders, and loyal retainers.', ['leader']],
      ['Envoy', 'Traveling representative and negotiator.', ['travel']],
      ['Manipulator', 'Blackmail, pressure, and court games.', ['dark']],
      ['Heir', 'Inheritance, expectation, and family politics.', ['story']],
    ],
  }),
  cls({
    name: 'Merchant',
    tagline: 'Trade, logistics, favors, and resource mastery.',
    shortDescription: 'Merchants turn money, supply, contacts, and negotiation into adventure power.',
    longDescription:
      'Use Merchant for guild factors, smugglers, quartermasters, fences, caravan masters, appraisers, and hard bargainers.',
    powerSource: 'Expert and social',
    combatRole: 'Support social',
    magicLevel: 'None',
    difficulty: 'Medium',
    categories: ['Expert', 'Social', 'Support'],
    aliases: ['trader', 'smuggler', 'quartermaster', 'fence'],
    subclasses: [
      ['Guild Factor', 'Guild authority and commercial networks.', ['guild']],
      ['Smuggler', 'Hidden routes, contraband, and fast talk.', ['stealth']],
      ['Quartermaster', 'Supplies, gear, and expedition readiness.', ['support']],
      ['Fence', 'Black market contacts and item laundering.', ['dark']],
      ['Caravan Master', 'Travel, guards, animals, and logistics.', ['travel']],
      ['Appraiser', 'Value, authenticity, and market knowledge.', ['lore']],
      ['Negotiator', 'Deals, contracts, and pressure reading.', ['social']],
    ],
  }),
  cls({
    name: 'Blood Hunter',
    tagline: 'Monster hunter who uses blood rites, curses, and dangerous tools.',
    shortDescription: 'Blood Hunters pay costs for power while hunting curses, monsters, undead, and fiends.',
    longDescription:
      'Use Blood Hunter for witcher-like monster slayers, mutants, lycan hunters, ghost slayers, relic hunters, and grim antiheroes.',
    powerSource: 'Martial and occult',
    combatRole: 'Striker scout',
    magicLevel: 'Low',
    difficulty: 'Advanced',
    categories: ['Martial', 'Occult', 'Hybrid', 'Striker', 'Dark'],
    aliases: ['monster hunter', 'witcher', 'hemocraft hunter', 'curse hunter'],
    subclasses: [
      ['Ghostslayer', 'Undead hunting and radiant blood rites.', ['undead']],
      ['Mutant', 'Alchemical body changes and risky formulas.', ['alchemy']],
      ['Lycan', 'Controlled werebeast transformation.', ['beast']],
      ['Profane Soul', 'Warlock-like pact magic added to hunting.', ['pact']],
      ['Witcher', 'Signs, potions, monster lore, and blades.', ['monster']],
      ['Vampire Stalker', 'Bloodsucker tracking and anti-charm tools.', ['vampire']],
      ['Demon Slayer', 'Fiend marks, banishment, and harsh rites.', ['fiend']],
      ['Relic Hunter', 'Cursed items and monster-slaying artifacts.', ['relic']],
    ],
  }),
  cls({
    name: 'Mystic Theurge',
    tagline: 'Dual-tradition caster blending divine, arcane, pact, or planar magic.',
    shortDescription: 'Mystic Theurges combine two magical traditions into a broad but complex identity.',
    longDescription:
      'Use Mystic Theurge for divine-arcane hybrids, pact priests, elemental savants, planar pilgrims, star prophets, and rune binders.',
    powerSource: 'Hybrid magic',
    combatRole: 'Flexible caster',
    magicLevel: 'Full',
    difficulty: 'Advanced',
    categories: ['Arcane', 'Divine', 'Hybrid', 'Support', 'Controller'],
    aliases: ['dual caster', 'hybrid caster', 'theurge'],
    subclasses: [
      ['Divine Arcane Hybrid', 'Classic cleric-wizard blend.', ['arcane']],
      ['Pact Priest', 'Patron bargain mixed with worship or oath.', ['pact']],
      ['Star Prophet', 'Cosmic omens and sacred astronomy.', ['cosmic']],
      ['Elemental Savant', 'Elemental magic bridged across traditions.', ['elemental']],
      ['Planar Pilgrim', 'Planar rites, travel, and extraplanar contacts.', ['planar']],
      ['Runebinder', 'Rune magic ties multiple sources together.', ['runes']],
      ['Spirit Scholar', 'Academic magic and spirit bargains.', ['spirit']],
    ],
  }),
  cls({
    name: 'Skald',
    tagline: 'War poet who inspires battle through song, saga, and fury.',
    shortDescription: 'Skalds are martial bards with war chants, battle memory, and fierce inspiration.',
    longDescription:
      'Use Skald for saga singers, berserker poets, tribal loremasters, storm singers, and battlefield performers.',
    powerSource: 'Martial and arcane',
    combatRole: 'Support striker',
    magicLevel: 'Half',
    difficulty: 'Medium',
    categories: ['Martial', 'Arcane', 'Hybrid', 'Support', 'Social'],
    aliases: ['war bard', 'saga singer', 'battle chanter'],
    subclasses: [
      ['War Chanter', 'Direct martial inspiration and combat songs.', ['battle']],
      ['Saga Singer', 'Legendary stories empower allies.', ['lore']],
      ['Berserker Poet', 'Rage, rhythm, and fierce verse.', ['rage']],
      ['Dirge Skald', 'Death songs and grim morale.', ['dark']],
      ['Storm Singer', 'Weather, thunder, and battlefield rhythm.', ['storm']],
      ['Tribal Loremaster', 'Clan memory and ancestral performance.', ['ancestor']],
    ],
  }),
  cls({
    name: 'Shadowblade',
    tagline: 'Stealthy hybrid of assassin, monk, rogue, and dark magic.',
    shortDescription: 'Shadowblades use darkness, mobility, curses, and precise melee strikes.',
    longDescription:
      'Use Shadowblade for umbral duelists, phantom thieves, hexblade stalkers, shadow monks, and night assassins.',
    powerSource: 'Martial and occult',
    combatRole: 'Scout striker',
    magicLevel: 'Low',
    difficulty: 'Advanced',
    categories: ['Martial', 'Occult', 'Hybrid', 'Scout', 'Striker', 'Dark'],
    aliases: ['shadow assassin', 'nightblade', 'umbral rogue'],
    subclasses: [
      ['Night Assassin', 'Darkness, silence, and lethal openings.', ['stealth']],
      ['Umbral Duelist', 'One-on-one blade work with shadow tricks.', ['duel']],
      ['Shadow Monk', 'Ki movement and darkness techniques.', ['monk']],
      ['Hexblade Stalker', 'Curses and pact weapon ambushes.', ['pact']],
      ['Phantom Thief', 'Ghostly infiltration and impossible theft.', ['spirit']],
    ],
  }),
  cls({
    name: 'Dragon Disciple',
    tagline: 'Character transformed by draconic blood, training, or pact.',
    shortDescription: 'Dragon Disciples grow scales, claws, breath, presence, and elemental identity.',
    longDescription:
      'Use Dragon Disciple as a full class for campaigns where becoming dragonlike is the central arc.',
    powerSource: 'Draconic magic',
    combatRole: 'Hybrid striker',
    magicLevel: 'Variable',
    difficulty: 'Medium',
    categories: ['Arcane', 'Primal', 'Hybrid', 'Elemental', 'Frontline'],
    aliases: ['dragon adept', 'dragon knight', 'draconic disciple'],
    subclasses: [
      ['Red Dragon', 'Fire, pride, intimidation, and destruction.', ['fire']],
      ['Blue Dragon', 'Lightning, order, desert, and dominance.', ['lightning']],
      ['Green Dragon', 'Poison, intrigue, forest, and manipulation.', ['poison']],
      ['Black Dragon', 'Acid, swamps, cruelty, and corrosion.', ['acid']],
      ['White Dragon', 'Cold, hunger, survival, and feral instinct.', ['ice']],
      ['Metallic Dragon', 'Protective or noble draconic ideals.', ['protector']],
      ['Gem Dragon', 'Psionics, crystals, and strange breath.', ['psionic']],
    ],
  }),
  cls({
    name: 'Rune Knight',
    tagline: 'Warrior or caster who channels power through carved symbols.',
    shortDescription: 'Rune Knights bind giant, elemental, ancestral, warding, war, or fate runes.',
    longDescription:
      'Use Rune Knight for characters with carved oaths, tattooed magic, giant lore, ancient alphabets, or sigil-bearing weapons.',
    powerSource: 'Runic magic and martial',
    combatRole: 'Frontline support',
    magicLevel: 'Low',
    difficulty: 'Medium',
    categories: ['Martial', 'Arcane', 'Hybrid', 'Frontline', 'Support'],
    aliases: ['rune warrior', 'sigil knight', 'giant rune fighter'],
    subclasses: [
      ['Giant Runes', 'Size, strength, and giant traditions.', ['giant']],
      ['Elemental Runes', 'Fire, frost, storm, stone, and similar marks.', ['elemental']],
      ['Ancestral Runes', 'Family marks, old names, and inherited power.', ['ancestor']],
      ['Warding Runes', 'Protection, seals, and defensive symbols.', ['defense']],
      ['War Runes', 'Battle sigils and weapon empowerment.', ['battle']],
      ['Fate Runes', 'Luck, omens, and probability marks.', ['fate']],
    ],
  }),
  cls({
    name: 'Technomancer',
    tagline: 'Science-fantasy caster who hacks, signals, and machines magic.',
    shortDescription: 'Technomancers blend spell logic with devices, networks, drones, and code.',
    longDescription:
      'Use Technomancer only where the setting supports science-fantasy, magitech, computers, starships, or signal magic.',
    powerSource: 'Arcane technology',
    combatRole: 'Controller support',
    magicLevel: 'Full',
    difficulty: 'Advanced',
    categories: ['Science Fantasy', 'Arcane', 'Controller', 'Crafting'],
    aliases: ['spell hacker', 'machine mage', 'cyber arcanist'],
    subclasses: [
      ['Spell Hacker', 'Rewrites spells like programs.', ['hacking']],
      ['Machine Mage', 'Talks to engines, locks, and constructs.', ['machine']],
      ['Signal Witch', 'Uses broadcasts, static, and remote curses.', ['signal']],
      ['Drone Mystic', 'Magic flows through drone companions.', ['drone']],
      ['Cyber Arcanist', 'Implants, code, and spellware.', ['cyber']],
    ],
  }),
  cls({
    name: 'Engineer',
    tagline: 'Builder, mechanic, demolitions expert, and device specialist.',
    shortDescription: 'Engineers solve problems with construction, repair, drones, vehicles, and explosives.',
    longDescription:
      'Use Engineer in magitech or science-fantasy games for characters who build the party out of impossible situations.',
    powerSource: 'Technology and craft',
    combatRole: 'Support utility',
    magicLevel: 'None',
    difficulty: 'Medium',
    categories: ['Science Fantasy', 'Expert', 'Crafting', 'Support'],
    aliases: ['mechanic', 'builder', 'rigger', 'technician'],
    subclasses: [
      ['Mechanic', 'Repairs, modifies, and understands machines.', ['repair']],
      ['Demolitions Expert', 'Explosives, charges, and structural weakness.', ['explosive']],
      ['Drone Rigger', 'Controls drones and remote tools.', ['drone']],
      ['Vehicle Builder', 'Improves mounts, vehicles, and engines.', ['vehicle']],
      ['Siege Engineer', 'Large weapons, fortifications, and breaches.', ['siege']],
      ['Prostheticist', 'Builds and tunes replacement limbs and assistive tech.', ['medicine']],
    ],
  }),
  cls({
    name: 'Medic',
    tagline: 'Practical healer for battlefield, plague, cybernetics, or biomancy.',
    shortDescription: 'Medics keep people alive through skill, tools, science, or body magic.',
    longDescription:
      'Use Medic for grounded healers, field surgeons, plague doctors, cyber surgeons, biomancers, and triage specialists.',
    powerSource: 'Expert, science, or magic',
    combatRole: 'Support healer',
    magicLevel: 'Variable',
    difficulty: 'Easy',
    categories: ['Science Fantasy', 'Expert', 'Support'],
    aliases: ['doctor', 'surgeon', 'healer', 'field medic'],
    subclasses: [
      ['Combat Medic', 'Triage, stabilizing, and moving under fire.', ['healing']],
      ['Plague Doctor', 'Disease, masks, quarantine, and antidotes.', ['plague']],
      ['Cyber Surgeon', 'Implants, trauma repair, and tech medicine.', ['cyber']],
      ['Field Chirurgeon', 'Surgery and practical medicine in rough conditions.', ['medicine']],
      ['Biomancer', 'Body shaping, regeneration, and organic control.', ['magic']],
    ],
  }),
  cls({
    name: 'Operative',
    tagline: 'Science-fantasy rogue built for missions, infiltration, and precision.',
    shortDescription: 'Operatives hack, snipe, sneak, talk, sabotage, and extract targets.',
    longDescription:
      'Use Operative in modern, sci-fi, or magitech settings when the rogue fantasy needs gadgets, data, or missions.',
    powerSource: 'Expert and technology',
    combatRole: 'Scout striker',
    magicLevel: 'None',
    difficulty: 'Medium',
    categories: ['Science Fantasy', 'Expert', 'Scout', 'Striker', 'Social'],
    aliases: ['agent', 'spy', 'infiltrator', 'hacker'],
    subclasses: [
      ['Spy', 'Cover identities, social pressure, and intelligence.', ['social']],
      ['Infiltrator', 'Stealth entry and objective extraction.', ['stealth']],
      ['Sniper', 'Long-range patience and precision kills.', ['ranged']],
      ['Hacker', 'Systems, security, and data control.', ['hacking']],
      ['Face', 'Talks past guards and reads rooms.', ['social']],
      ['Saboteur', 'Breaks systems, machines, and plans.', ['sabotage']],
      ['Smuggler', 'Moves illegal goods and people quietly.', ['stealth']],
    ],
  }),
  cls({
    name: 'Pilot',
    tagline: 'Vehicle, starship, or mech specialist.',
    shortDescription: 'Pilots are defined by movement, vehicles, machines, travel, and high-risk maneuvers.',
    longDescription:
      'Use Pilot when vehicles, airships, mechs, mounts, spelljammers, or starships are part of the campaign.',
    powerSource: 'Expert and technology',
    combatRole: 'Mobility support',
    magicLevel: 'None',
    difficulty: 'Medium',
    categories: ['Science Fantasy', 'Expert', 'Scout', 'Mounted', 'Support'],
    aliases: ['driver', 'ace', 'captain', 'mech pilot'],
    subclasses: [
      ['Ace Pilot', 'Dogfights, stunts, and impossible maneuvers.', ['vehicle']],
      ['Starship Captain', 'Command, navigation, and ship combat.', ['leader']],
      ['Mech Pilot', 'Powered armor or mech-scale battlefield action.', ['mech']],
      ['Courier', 'Speed, routes, smuggling, and pursuit.', ['travel']],
      ['Racer', 'Velocity, reflexes, and performance driving.', ['speed']],
    ],
  }),
]

export const PLAYABLE_CLASSES = BASE_PLAYABLE_CLASSES

const CLASSES_BY_NORMALIZED_NAME = new Map<string, ClassSelection>()

for (const classEntry of PLAYABLE_CLASSES) {
  CLASSES_BY_NORMALIZED_NAME.set(normalizeClassSearchText(classEntry.name), { classEntry })
  CLASSES_BY_NORMALIZED_NAME.set(normalizeClassSearchText(classEntry.key), { classEntry })
  for (const alias of classEntry.aliases) {
    CLASSES_BY_NORMALIZED_NAME.set(normalizeClassSearchText(alias), { classEntry })
  }
  for (const subclass of classEntry.subclasses) {
    const selection = { classEntry, subclass }
    CLASSES_BY_NORMALIZED_NAME.set(normalizeClassSearchText(classChoiceLabel(classEntry, subclass)), selection)
    CLASSES_BY_NORMALIZED_NAME.set(normalizeClassSearchText(`${classEntry.name} ${subclass.name}`), selection)
    if (!CLASSES_BY_NORMALIZED_NAME.has(normalizeClassSearchText(subclass.name))) {
      CLASSES_BY_NORMALIZED_NAME.set(normalizeClassSearchText(subclass.name), selection)
    }
  }
}

export function normalizeClassSearchText(value: string) {
  return value
    .toLowerCase()
    .replace(/['’]/g, '')
    .replace(/[^a-z0-9]+/g, ' ')
    .trim()
}

function compactClassSearchText(value: string) {
  return normalizeClassSearchText(value).replace(/\s+/g, '')
}

export function classChoiceLabel(classEntry: PlayableClass, subclass?: PlayableSubclass) {
  return subclass ? `${classEntry.name} - ${subclass.name}` : classEntry.name
}

export function classSelectionFromValue(value?: string | null): ClassSelection | null {
  const normalized = normalizeClassSearchText(value ?? '')
  if (!normalized) return null
  const exact = CLASSES_BY_NORMALIZED_NAME.get(normalized)
  if (exact) return exact

  const compactValue = compactClassSearchText(normalized)
  for (const classEntry of PLAYABLE_CLASSES) {
    if (compactClassSearchText(classEntry.name) === compactValue) return { classEntry }
    for (const subclass of classEntry.subclasses) {
      if (compactClassSearchText(classChoiceLabel(classEntry, subclass)) === compactValue) {
        return { classEntry, subclass }
      }
    }
  }

  return null
}

function classSearchFields(classEntry: PlayableClass) {
  return [
    classEntry.name,
    classEntry.key,
    classEntry.tagline,
    classEntry.shortDescription,
    classEntry.longDescription,
    classEntry.powerSource,
    classEntry.combatRole,
    classEntry.magicLevel,
    classEntry.difficulty,
    ...classEntry.categories,
    ...classEntry.aliases,
    ...classEntry.subclasses.flatMap((subclass) => [
      subclass.name,
      subclass.tagline,
      ...subclass.tags,
      classChoiceLabel(classEntry, subclass),
    ]),
  ]
}

export function classMatchesSearch(classEntry: PlayableClass, query: string) {
  const normalizedQuery = normalizeClassSearchText(query)
  if (!normalizedQuery) return true
  const compactQuery = compactClassSearchText(normalizedQuery)
  return classSearchFields(classEntry).some((field) => {
    const normalizedField = normalizeClassSearchText(field)
    return normalizedField.includes(normalizedQuery) || compactClassSearchText(normalizedField).includes(compactQuery)
  })
}

export function filterPlayableClasses({
  query,
  category,
}: {
  query: string
  category: ClassCategory | 'All'
}) {
  return PLAYABLE_CLASSES.filter((classEntry) => {
    const categoryMatch = category === 'All' || classEntry.categories.includes(category)
    return categoryMatch && classMatchesSearch(classEntry, query)
  })
}
