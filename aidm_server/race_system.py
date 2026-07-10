from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any
from uuid import uuid4

from aidm_server.profile_icons import RACE_ALIASES, profile_icon_race_for_character


class RaceValidationError(ValueError):
    """Validation failure whose message is safe to return to an API client."""

    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


RACE_TAGS = {
    'beginner_friendly',
    'martial',
    'magical',
    'stealthy',
    'durable',
    'social',
    'nature',
    'beastlike',
    'monstrous',
    'elemental',
    'small',
    'large',
    'flying',
    'aquatic',
    'darkvision',
    'exotic',
    'construct',
    'undead_like',
    'celestial',
    'fiendish',
    'draconic',
}

RACE_SOURCES = {'curated', 'custom', 'template', 'imported'}
RACE_SIZES = {'tiny', 'small', 'medium', 'large'}
RACE_DIFFICULTIES = {'easy', 'medium', 'advanced'}
RACE_TRAIT_CATEGORIES = {
    'movement',
    'sense',
    'resistance',
    'active_ability',
    'passive_ability',
    'skill',
    'language',
    'narrative',
    'restriction',
}
CUSTOM_RACE_APPROVAL_STATUSES = {
    'draft',
    'auto_balanced',
    'needs_review',
    'overpowered_unreviewed',
    'approved_by_user',
    'rejected',
}
DAMAGE_TYPES = {
    'acid',
    'cold',
    'fire',
    'force',
    'lightning',
    'necrotic',
    'poison',
    'psychic',
    'radiant',
    'thunder',
    'bludgeoning',
    'piercing',
    'slashing',
}

TRAIT_COST_GUIDE = {
    'minor_flavor': 0,
    'language': 1,
    'skill_proficiency': 1,
    'darkvision': 1,
    'poison_resistance': 2,
    'elemental_resistance': 2,
    'natural_weapon': 1,
    'swim_speed': 1,
    'climb_speed': 1,
    'flight': 4,
    'breath_weapon': 3,
    'teleportation': 5,
    'magic_resistance': 5,
    'physical_damage_resistance': 6,
    'damage_immunity': 5,
    'regeneration': 5,
}


def _slug(value: str, *, fallback: str = 'custom_race') -> str:
    slug = re.sub(r'[^a-z0-9]+', '_', str(value or '').lower()).strip('_')
    return slug or fallback


def normalize_race_name(value: str | None) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


def _clean_text(value: Any, fallback: str = '', max_length: int = 600) -> str:
    text = str(value if value is not None else fallback).strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rstrip()


def _string_list(value: Any, *, max_items: int = 12, max_length: int = 120) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_text(item, max_length=max_length)
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= max_items:
            break
    return result


def _tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        tag = _slug(str(item))
        if tag in RACE_TAGS and tag not in result:
            result.append(tag)
    return result


def _trait(
    trait_id: str,
    name: str,
    description: str,
    category: str,
    balance_cost: int,
    *,
    mechanics: dict[str, Any] | None = None,
    ai_hint: str | None = None,
) -> dict[str, Any]:
    return {
        'id': trait_id,
        'name': name,
        'description': description,
        'category': category,
        **({'mechanics': mechanics} if mechanics else {}),
        **({'aiHint': ai_hint} if ai_hint else {}),
        'balanceCost': balance_cost,
    }


def _darkvision(prefix: str, range_feet: int = 60) -> dict[str, Any]:
    return _trait(
        f'{prefix}_darkvision',
        'Darkvision',
        'You can see in darkness better than most.',
        'sense',
        1,
        mechanics={'sense': {'darkvisionRange': range_feet}},
        ai_hint='Low light and darkness are not automatic obstacles for this character.',
    )


def _resistance(prefix: str, name: str, damage_types: list[str], cost: int = 2) -> dict[str, Any]:
    return _trait(
        f'{prefix}_{_slug(name)}',
        name,
        f'You resist {", ".join(damage_types)} damage.',
        'resistance',
        cost,
        mechanics={'resistance': {'damageTypes': damage_types}},
        ai_hint=f'Do not treat this as immunity; it is resistance to {", ".join(damage_types)} only.',
    )


def _active(
    trait_id: str,
    name: str,
    description: str,
    cost: int,
    *,
    action_type: str = 'action',
    cooldown: str = 'short_rest',
    effect_type: str,
    scaling: str | None = None,
    ai_hint: str | None = None,
) -> dict[str, Any]:
    active_ability: dict[str, Any] = {
        'actionType': action_type,
        'cooldown': cooldown,
        'effectType': effect_type,
    }
    if scaling:
        active_ability['scaling'] = scaling
    return _trait(
        trait_id,
        name,
        description,
        'active_ability',
        cost,
        mechanics={'activeAbility': active_ability},
        ai_hint=ai_hint,
    )


def _movement(prefix: str, name: str, description: str, cost: int, movement: dict[str, int]) -> dict[str, Any]:
    return _trait(
        f'{prefix}_{_slug(name)}',
        name,
        description,
        'movement',
        cost,
        mechanics={'movement': movement},
        ai_hint=f'{name} affects movement only when the scene physically allows it.',
    )


def _skill(prefix: str, name: str, description: str, skills: list[str], cost: int = 1) -> dict[str, Any]:
    return _trait(
        f'{prefix}_{_slug(name)}',
        name,
        description,
        'skill',
        cost,
        mechanics={'skillBonus': {'skills': skills, 'bonusType': 'proficiency'}},
    )


def _passive(prefix: str, name: str, description: str, cost: int, ai_hint: str | None = None) -> dict[str, Any]:
    return _trait(f'{prefix}_{_slug(name)}', name, description, 'passive_ability', cost, ai_hint=ai_hint)


def _narrative(prefix: str, name: str, description: str, ai_hint: str | None = None) -> dict[str, Any]:
    return _trait(f'{prefix}_{_slug(name)}', name, description, 'narrative', 0, ai_hint=ai_hint)


def _restriction(prefix: str, name: str, description: str, cost: int = -1) -> dict[str, Any]:
    return _trait(f'{prefix}_{_slug(name)}', name, description, 'restriction', cost)


def analyze_race_balance(race: dict[str, Any]) -> dict[str, Any]:
    traits = race.get('traits') if isinstance(race, dict) else []
    trait_list = traits if isinstance(traits, list) else []
    spent = 0
    warnings: list[str] = []

    for trait in trait_list:
        if not isinstance(trait, dict):
            continue
        try:
            spent += int(trait.get('balanceCost', 0))
        except (TypeError, ValueError):
            pass
        mechanics = trait.get('mechanics') if isinstance(trait.get('mechanics'), dict) else {}
        movement = mechanics.get('movement') if isinstance(mechanics.get('movement'), dict) else {}
        resistance = mechanics.get('resistance') if isinstance(mechanics.get('resistance'), dict) else {}
        active = mechanics.get('activeAbility') if isinstance(mechanics.get('activeAbility'), dict) else {}

        if movement.get('flySpeed'):
            warnings.append('Flight can bypass many low-level obstacles; check space, armor, and restraints.')
        if resistance.get('immunities') or resistance.get('immuneDamageTypes'):
            warnings.append('Damage immunity is stronger than standard race traits.')
        effect_type = str(active.get('effectType', '')).lower()
        cooldown = str(active.get('cooldown', '')).lower()
        if 'teleport' in effect_type and cooldown in {'', 'turn', 'free'}:
            warnings.append('Unrestricted teleportation can break exploration and combat.')
        if 'regeneration' in effect_type or 'regenerate' in effect_type:
            warnings.append('Regeneration can erase attrition and combat pacing.')

    text_blob = json.dumps(race, sort_keys=True).lower()
    if 'resist all' in text_blob or 'all damage' in text_blob:
        warnings.append('Resistance to all damage is stronger than standard race traits.')
    if 'mind control' in text_blob:
        warnings.append('Mind control should be downgraded to social pressure, fear, or emotion sensing.')
    if 'time control' in text_blob:
        warnings.append('Time control should be downgraded to a reroll, initiative boost, or brief omen.')

    tier = 'standard'
    if spent <= 3:
        tier = 'weak'
    elif spent <= 6:
        tier = 'standard'
    elif spent <= 8:
        tier = 'strong'
    else:
        tier = 'overpowered'

    return {
        'budget': 5,
        'spent': spent,
        'tier': tier,
        **({'warnings': list(dict.fromkeys(warnings))} if warnings else {}),
    }


def approval_status_for_balance(balance: dict[str, Any]) -> str:
    spent = int(balance.get('spent', 0) or 0)
    if spent <= 6:
        return 'auto_balanced'
    if spent <= 8:
        return 'needs_review'
    return 'overpowered_unreviewed'


def _race(
    race_id: str,
    name: str,
    description_short: str,
    description_long: str,
    *,
    aliases: list[str],
    tags: list[str],
    size: str,
    base_speed: int,
    body_type: str,
    common_features: list[str],
    traits: list[dict[str, Any]],
    ai_hints: list[str],
    roleplay_hooks: list[str],
    recommended_classes: list[str],
    difficulty: str,
    color_hints: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict[str, Any]:
    race_definition = {
        'id': race_id,
        'version': 1,
        'name': name,
        'source': 'curated',
        'descriptionShort': description_short,
        'descriptionLong': description_long,
        'aliases': aliases,
        'tags': tags,
        'size': size,
        'baseSpeed': base_speed,
        'visual': {
            'portraitKey': race_id,
            'iconKey': race_id,
            'bodyType': body_type,
            'commonFeatures': common_features,
            **({'colorHints': color_hints} if color_hints else {}),
        },
        'traits': traits,
        'aiNarrationHints': ai_hints,
        'roleplayHooks': roleplay_hooks,
        'recommendedClasses': recommended_classes,
        'difficulty': difficulty,
    }
    balance = analyze_race_balance(race_definition)
    if warnings:
        balance['warnings'] = list(dict.fromkeys([*(balance.get('warnings') or []), *warnings]))
    race_definition['balance'] = balance
    return race_definition


CURATED_RACES: list[dict[str, Any]] = [
    _race(
        'aarakocra',
        'Aarakocra',
        'Winged avian wanderers built for sky and speed.',
        'Aarakocra are birdlike humanoids with wings, talons, and a natural connection to open skies and high places.',
        aliases=['aarakocra', 'birdfolk', 'bird person', 'avian', 'eaglefolk', 'hawkfolk'],
        tags=['beastlike', 'flying', 'stealthy', 'nature', 'exotic'],
        size='medium',
        base_speed=25,
        body_type='avian',
        common_features=['feathers', 'wings', 'beak', 'talons'],
        traits=[
            _movement('aarakocra', 'Flight', 'You have a flying speed while not wearing heavy armor.', 4, {'flySpeed': 30}),
            _active(
                'aarakocra_talons',
                'Talons',
                'Your talons can be used as natural weapons.',
                1,
                cooldown='turn',
                effect_type='natural_weapon',
                ai_hint='Mention sharp talons in close combat when relevant.',
            ),
        ],
        ai_hints=['Mention wings, feathers, height, wind, and sky when relevant.', 'Cramped indoor spaces may limit flight.'],
        roleplay_hooks=['How does your character feel in enclosed spaces?', 'Are they from mountain aeries, sky temples, or wandering flocks?'],
        recommended_classes=['Ranger', 'Monk', 'Rogue'],
        difficulty='advanced',
    ),
    _race(
        'aasimar',
        'Aasimar',
        'Mortals touched by celestial light and impossible expectations.',
        'Aasimar blend divine presence, radiant power, and a strong heroic or tragic tone.',
        aliases=['aasimar', 'angelborn', 'celestial', 'divine', 'angelic'],
        tags=['magical', 'social', 'durable', 'exotic', 'celestial'],
        size='medium',
        base_speed=30,
        body_type='humanoid',
        common_features=['luminous eyes', 'faint halo', 'radiant marks'],
        traits=[
            _resistance('aasimar', 'Celestial Resistance', ['radiant', 'necrotic'], 2),
            _active('aasimar_healing_hands', 'Healing Hands', 'Once per rest, restore a small amount of health with celestial power.', 2, cooldown='long_rest', effect_type='minor_healing'),
            _passive('aasimar', 'Radiant Soul', 'Your celestial nature can flare visibly in moments of power.', 1),
        ],
        ai_hints=['Mention luminous eyes, halos, warm light, and pressure from divine attention when relevant.'],
        roleplay_hooks=['Who expects you to live up to a divine image?', 'Is your celestial sign a blessing or a burden?'],
        recommended_classes=['Paladin', 'Cleric', 'Warlock'],
        difficulty='medium',
    ),
    _race(
        'bugbear',
        'Bugbear',
        'Long-limbed ambushers with brutal reach and quiet menace.',
        'Bugbears are large, stealthy bruisers who hit hard from unexpected places.',
        aliases=['bugbear', 'hairy goblin', 'large goblin', 'ambusher'],
        tags=['martial', 'stealthy', 'monstrous', 'large', 'darkvision'],
        size='medium',
        base_speed=30,
        body_type='goblinkin',
        common_features=['long arms', 'heavy shoulders', 'coarse fur'],
        traits=[_passive('bugbear', 'Long-Limbed', 'Your reach and leverage help in close combat.', 1), _skill('bugbear', 'Ambusher', 'You are practiced at opening fights from hiding.', ['stealth'], 2), _passive('bugbear', 'Powerful Build', 'You count as larger for carrying and forceful physical tasks.', 1), _darkvision('bugbear')],
        ai_hints=['Describe heavy shoulders, silent footfalls, long arms, and predator calm.'],
        roleplay_hooks=['Do others see a person, a monster, or a weapon?', 'Who taught you patience before violence?'],
        recommended_classes=['Fighter', 'Rogue', 'Barbarian'],
        difficulty='medium',
    ),
    _race(
        'changeling',
        'Changeling',
        'Shapeshifting social ghosts with borrowed faces.',
        'Changelings thrive in intrigue, disguise, deception, and identity-driven stories.',
        aliases=['changeling', 'shapechanger', 'shapeshifter', 'doppelganger'],
        tags=['social', 'stealthy', 'magical', 'exotic'],
        size='medium',
        base_speed=30,
        body_type='humanoid',
        common_features=['mutable features', 'pale eyes', 'subtle facial shifts'],
        traits=[_active('changeling_shapechanger', 'Shapechanger', 'You can alter your appearance for disguise and infiltration.', 3, cooldown='free', effect_type='appearance_shift'), _skill('changeling', 'Silver Tongue', 'You are comfortable reading and adopting social roles.', ['deception', 'persuasion'], 1), _narrative('changeling', 'Identity Craft', 'Names, faces, and masks carry special roleplay weight.')],
        ai_hints=['Describe features settling like wet clay and names treated like masks.'],
        roleplay_hooks=['Which face feels most like home?', 'Who knows your original identity?'],
        recommended_classes=['Bard', 'Rogue', 'Warlock'],
        difficulty='advanced',
    ),
    _race(
        'dragonborn',
        'Dragonborn',
        'Draconic humanoids with elemental breath and proud bearing.',
        'Dragonborn carry the power of ancient dragons in mortal form. They often possess scaled skin, powerful builds, and a breath weapon tied to their ancestry.',
        aliases=['dragonborn', 'dragon person', 'draconic', 'dragonkin'],
        tags=['draconic', 'martial', 'magical', 'elemental', 'durable'],
        size='medium',
        base_speed=30,
        body_type='reptilian',
        common_features=['scales', 'horns', 'draconic snout', 'claws'],
        color_hints=['red', 'gold', 'bronze', 'black', 'blue', 'green'],
        traits=[
            _active('dragonborn_breath_weapon', 'Breath Weapon', 'You can exhale destructive elemental energy tied to your ancestry.', 3, effect_type='elemental_cone_or_line', scaling='level_based', ai_hint='When used, describe a dramatic exhale of elemental energy.'),
            _resistance('dragonborn', 'Elemental Resistance', ['fire'], 2),
        ],
        ai_hints=['Mention scales, draconic posture, breath, claws, and ancestry when relevant.', 'Dragonborn may be perceived as imposing, proud, or intimidating.'],
        roleplay_hooks=['What does your draconic ancestry mean to you?', 'Do others fear, respect, or misunderstand your appearance?'],
        recommended_classes=['Fighter', 'Paladin', 'Sorcerer'],
        difficulty='medium',
    ),
]


_MORE_CURATED_SPECS: list[tuple[Any, ...]] = [
    ('dwarf', 'Dwarf', 'Stout folk of stone, craft, memory, and stubborn courage.', 'Dwarves are durable, practical, tradition-rich characters who endure pressure well.', ['dwarf', 'dwarven', 'mountain dwarf', 'hill dwarf'], ['beginner_friendly', 'martial', 'darkvision', 'durable'], 'medium', 25, 'humanoid', ['compact build', 'beard or braids', 'stonework symbols'], [_darkvision('dwarf'), _resistance('dwarf', 'Poison Resistance', ['poison'], 2), _skill('dwarf', 'Stonecunning', 'You read stonework, mines, and crafted places well.', ['history'], 1)], ['Mention careful craft, old songs, stone metaphors, compact strength, and inherited obligations.'], ['What clan, craft, or oath shaped you?', 'What old grudge or duty follows you?'], ['Cleric', 'Fighter', 'Artificer'], 'easy'),
    ('elf', 'Elf', 'Graceful long-lived wanderers shaped by magic and memory.', 'Elves are elegant, perceptive, and versatile, with strong magical or woodland themes.', ['elf', 'elven', 'drow', 'dark elf', 'wood elf', 'high elf', 'half elf', 'half-elf'], ['beginner_friendly', 'magical', 'stealthy', 'nature', 'darkvision'], 'medium', 30, 'humanoid', ['pointed ears', 'graceful build', 'watchful eyes'], [_darkvision('elf'), _skill('elf', 'Keen Senses', 'You notice subtle sights and sounds.', ['perception'], 1), _resistance('elf', 'Fey Ancestry', ['psychic'], 2), _narrative('elf', 'Trance', 'You rest in a meditative state rather than ordinary sleep.')], ['Describe precise movement, old references, watchful stillness, and beauty that feels slightly unreal.'], ['What memory from a longer life still shapes you?', 'Are you tied to court, forest, shadow, or study?'], ['Wizard', 'Ranger', 'Rogue'], 'easy'),
    ('fairy', 'Fairy', 'Small fey tricksters with wings and impossible sparkle.', 'Fairies bring flight, whimsy, magic, and social weirdness into the party.', ['fairy', 'fae', 'fey', 'pixie', 'sprite', 'winged'], ['flying', 'magical', 'small', 'nature', 'exotic'], 'small', 30, 'fairy', ['wings', 'glittering dust', 'delicate build'], [_movement('fairy', 'Flight', 'You have winged flight when space and danger allow it.', 4, {'flySpeed': 30}), _active('fairy_fey_magic', 'Fey Magic', 'You can produce a small fey magical effect once per rest.', 2, effect_type='minor_fey_magic'), _narrative('fairy', 'Small Size', 'Your small frame changes cover, reach, and social presence.')], ['Mention wings, glittering dust, fey logic, sudden moods, and delicate but unnerving confidence.'], ['What fey rule do you still obey?', 'Do people underestimate or fear your whimsy?'], ['Bard', 'Druid', 'Sorcerer'], 'medium'),
    ('firbolg', 'Firbolg', 'Gentle forest giantkin with quiet magic and old patience.', 'Firbolgs are nature-bound protectors with subtle magic and a calm, oversized presence.', ['firbolg', 'forest giant', 'nature giant', 'gentle giant'], ['nature', 'large', 'magical', 'durable'], 'medium', 30, 'giantkin', ['broad frame', 'soft animal features', 'earth-toned skin'], [_active('firbolg_hidden_step', 'Hidden Step', 'Once per rest, briefly vanish from sight with gentle magic.', 3, effect_type='brief_invisibility'), _passive('firbolg', 'Powerful Build', 'You count as larger for carrying and forceful physical tasks.', 1), _skill('firbolg', 'Nature Speech', 'You can read animals, plants, and wilderness signs.', ['nature'], 1)], ['Describe deep voices, mossy colors, careful hands, forest manners, and discomfort with greed.'], ['What grove, herd, or hidden place are you protecting?', 'What did civilization take from the wild?'], ['Druid', 'Cleric', 'Ranger'], 'medium'),
    ('genasi', 'Genasi', 'Element-touched wanderers with living weather in their blood.', 'Genasi are expressive elemental characters with visible magic in their bodies.', ['genasi', 'elemental', 'fire genasi', 'water genasi', 'earth genasi', 'air genasi'], ['elemental', 'magical', 'exotic', 'social'], 'medium', 30, 'humanoid', ['elemental skin', 'unnatural hair', 'ambient sparks or mist'], [_resistance('genasi', 'Elemental Resistance', ['fire'], 2), _active('genasi_elemental_burst', 'Elemental Burst', 'Once per rest, release a small burst of elemental force.', 2, effect_type='minor_elemental_burst'), _passive('genasi', 'Striking Presence', 'Your body visibly carries elemental ancestry.', 1)], ['Describe unusual skin, flame, water, stone calm, drifting dust, or air that stirs around them.'], ['Which element marks you, and who taught you to control it?', 'Do people see you as a person or a phenomenon?'], ['Sorcerer', 'Druid', 'Fighter'], 'medium'),
    ('gnome', 'Gnome', 'Small bright minds full of tricks, craft, and stubborn curiosity.', 'Gnomes are clever, magical, and inventive, with a knack for outthinking problems.', ['gnome', 'gnomish', 'deep gnome', 'rock gnome', 'forest gnome'], ['beginner_friendly', 'small', 'magical', 'stealthy'], 'small', 25, 'humanoid', ['small frame', 'bright eyes', 'packed tools'], [_passive('gnome', 'Gnome Cunning', 'You have strong mental resilience against magical tricks.', 2), _skill('gnome', 'Inventive', 'You are skilled with devices, illusions, or clever solutions.', ['arcana', 'investigation'], 1), _narrative('gnome', 'Small Size', 'Your small frame helps with cover and tight spaces.')], ['Mention fast hands, bright eyes, odd tools, packed notes, and delight at strange mechanisms.'], ['What impossible question keeps you moving?', 'What invention or illusion went wrong?'], ['Wizard', 'Artificer', 'Rogue'], 'easy'),
    ('goblin', 'Goblin', 'Small survivors with sharp instincts and sudden bursts of nerve.', 'Goblins are nimble opportunists who dart, hide, improvise, and survive.', ['goblin', 'gobbo', 'small survivor'], ['small', 'stealthy', 'monstrous', 'darkvision'], 'small', 30, 'goblinkin', ['sharp teeth', 'large ears', 'quick hands'], [_darkvision('goblin'), _active('goblin_nimble_escape', 'Nimble Escape', 'You can dart away or hide quickly in dangerous moments.', 2, action_type='bonus_action', cooldown='turn', effect_type='disengage_or_hide'), _skill('goblin', 'Survivor', 'You are good at improvised survival and opportunistic tricks.', ['stealth'], 1), _narrative('goblin', 'Small Size', 'Your small frame changes cover and social presence.')], ['Describe quick glances, restless hands, bargain instincts, and pride in surviving impossible odds.'], ['What did you survive that others did not?', 'Are you hiding old loyalties or proving a new path?'], ['Rogue', 'Ranger', 'Artificer'], 'medium'),
    ('goliath', 'Goliath', 'Mountain-born giants of endurance, competition, and grit.', 'Goliaths are powerful, durable, and built for hard terrain and harder fights.', ['goliath', 'giantkin', 'mountain giant', 'big strong'], ['beginner_friendly', 'martial', 'large', 'durable'], 'medium', 30, 'giantkin', ['towering height', 'stone-like markings', 'weathered skin'], [_active('goliath_stone_endurance', 'Stone Endurance', 'Once per rest, reduce a burst of incoming damage.', 2, action_type='reaction', effect_type='damage_reduction'), _passive('goliath', 'Powerful Build', 'You count as larger for carrying and forceful physical tasks.', 1), _skill('goliath', 'Mountain Born', 'You are comfortable with heights, cold, and harsh terrain.', ['athletics'], 1)], ['Mention towering height, weathered skin, calm pain tolerance, and a habit of measuring worth by trials.'], ['What challenge are you trying to prove yourself against?', 'What did your clan teach you about weakness?'], ['Barbarian', 'Fighter', 'Paladin'], 'easy'),
    ('halfling', 'Halfling', 'Small warm-hearted adventurers with uncanny luck.', 'Halflings are brave, lucky, and easy to fit into almost any campaign tone.', ['halfling', 'hobbit', 'small folk', 'little folk'], ['beginner_friendly', 'small', 'stealthy', 'social'], 'small', 25, 'humanoid', ['small frame', 'warm expression', 'travel-ready clothes'], [_active('halfling_lucky', 'Lucky', 'Once per rest, turn a small disaster into a second chance.', 2, action_type='reaction', cooldown='long_rest', effect_type='reroll'), _passive('halfling', 'Brave', 'You are harder to frighten than your size suggests.', 1), _narrative('halfling', 'Small Size', 'Your small frame changes cover and social presence.')], ['Describe practical comforts, quick smiles, steady courage, and surprising boldness from a small frame.'], ['What simple comfort are you protecting?', 'What made you leave home?'], ['Rogue', 'Bard', 'Ranger'], 'easy'),
    ('harengon', 'Harengon', 'Rabbitfolk wanderers with springing legs and quick nerves.', 'Harengon are mobile, alert, and energetic, built for players who like quick action.', ['harengon', 'rabbitfolk', 'bunny', 'hare', 'rabbit'], ['beginner_friendly', 'beastlike', 'small', 'stealthy', 'nature'], 'small', 30, 'rabbitfolk', ['long ears', 'springing legs', 'soft fur'], [_active('harengon_rabbit_hop', 'Rabbit Hop', 'You can make a sudden leaping reposition.', 2, action_type='bonus_action', cooldown='short_rest', effect_type='burst_jump'), _passive('harengon', 'Lucky Footwork', 'Quick reactions help you avoid sudden danger.', 1), _skill('harengon', 'Keen Hearing', 'You notice faint sounds and danger cues.', ['perception'], 1)], ['Describe twitching ears, spring-loaded movement, nervous energy, and reading danger early.'], ['What makes you bolt?', 'Are you a wanderer, courier, scout, or runaway?'], ['Monk', 'Rogue', 'Ranger'], 'easy'),
    ('hobgoblin', 'Hobgoblin', 'Disciplined tacticians who turn teamwork into force.', 'Hobgoblins are martial, organized, and good at coordinated party play.', ['hobgoblin', 'hob goblin', 'military goblin', 'tactician'], ['martial', 'social', 'monstrous', 'darkvision'], 'medium', 30, 'goblinkin', ['disciplined posture', 'polished kit', 'goblin features'], [_darkvision('hobgoblin'), _skill('hobgoblin', 'Martial Training', 'You are trained around weapons, armor, and formations.', ['athletics'], 1), _active('hobgoblin_tactical_aid', 'Tactical Aid', 'Once per rest, help an ally through discipline and timing.', 2, action_type='reaction', effect_type='ally_bonus'), _passive('hobgoblin', 'Disciplined', 'You are hard to rattle in organized conflict.', 1)], ['Describe clipped orders, polished kit, disciplined posture, and a careful eye for formation.'], ['What command structure shaped you?', 'Are you loyal, exiled, or building a new unit?'], ['Fighter', 'Paladin', 'Bard'], 'medium'),
    ('human', 'Human', 'Ambitious, adaptable, and at home in nearly any story.', 'Humans work for nearly any concept, from farmhand hero to court spy to battle-scarred veteran.', ['human', 'humanoid', 'mortal'], ['beginner_friendly', 'social', 'martial', 'magical'], 'medium', 30, 'humanoid', ['varied cultures', 'familiar features', 'adaptable style'], [_passive('human', 'Adaptable', 'You can fit nearly any class, background, or culture.', 2), _skill('human', 'Versatile', 'Choose one extra practical skill focus.', ['any'], 1), _narrative('human', 'Driven', 'Short lives and broad ambition push humans into motion.')], ['Describe ambition, varied cultures, short-lived urgency, and a talent for belonging anywhere.'], ['What ordinary life are you leaving behind?', 'What ambition is bigger than your lifespan?'], ['Fighter', 'Wizard', 'Bard'], 'easy'),
    ('afro-diasporic-human', 'Afro-Diasporic Human', 'Human heroes with diaspora-inspired heritage and player-defined culture.', 'Afro-Diasporic Humans are a human heritage option for Black human heroes in fantasy worlds.', ['afro-diasporic human', 'afro diasporic human', 'afro-diasporic', 'afro diasporic', 'african american', 'african american human', 'african diaspora human', 'black human', 'diaspora human'], ['beginner_friendly', 'social', 'martial', 'magical'], 'medium', 30, 'humanoid', ['human features', 'dark skin tones', 'natural hairstyles, braids, locs, or curls', 'personal or cultural adornments'], [_passive('afro_diasporic_human', 'Adaptable', 'You can fit nearly any class, background, or culture.', 2), _skill('afro_diasporic_human', 'Versatile', 'Choose one extra practical skill focus.', ['any'], 1), _narrative('afro_diasporic_human', 'Diaspora Ties', 'Family, community, craft, faith, migration, and belonging can shape the character story without prescribing it.')], ['Treat this as a Human heritage option, not a separate species.', 'Do not infer ability, behavior, homeland, class, or personality from appearance; ask the player what culture and history they want.'], ['What family, community, homeland, or chosen culture shaped you?', 'What promise, craft, faith, or ambition made you leave home?'], ['Fighter', 'Wizard', 'Bard'], 'easy'),
    ('kenku', 'Kenku', 'Corvid mimics with sharp memory and stranger voices.', 'Kenku are stealthy birdfolk with mimicry, memory, and a distinct roleplay hook.', ['kenku', 'crowfolk', 'ravenfolk', 'corvid', 'flightless bird'], ['stealthy', 'beastlike', 'darkvision', 'exotic'], 'medium', 30, 'avian', ['black feathers', 'beak', 'bright eyes'], [_darkvision('kenku'), _skill('kenku', 'Mimicry', 'You can imitate sounds and voices you have heard.', ['deception'], 1), _skill('kenku', 'Expert Forgery', 'You copy marks, documents, and patterns carefully.', ['sleight_of_hand'], 1), _passive('kenku', 'Keen Memory', 'You remember sounds, routes, and details sharply.', 1)], ['Describe repeated voices, glossy feathers, quick copying, and careful attention to sounds.'], ['Whose voice do you use when afraid?', 'What sound or memory are you chasing?'], ['Rogue', 'Bard', 'Ranger'], 'advanced'),
    ('kobold', 'Kobold', 'Small draconic survivors with trapcraft and pack courage.', 'Kobolds are tiny dragon-adjacent tacticians who win through teamwork and tricks.', ['kobold', 'little dragon', 'tiny dragon', 'small dragon', 'dragon-adjacent'], ['small', 'stealthy', 'monstrous', 'darkvision', 'exotic', 'draconic'], 'small', 30, 'reptilian', ['tiny horns', 'scales', 'quick planning'], [_darkvision('kobold'), _passive('kobold', 'Pack Tactics', 'You are stronger when allies help set up an opening.', 2), _skill('kobold', 'Trapwise', 'You are practiced with tunnels, snares, and improvised devices.', ['investigation'], 1), _passive('kobold', 'Draconic Spark', 'A tiny trace of dragon power marks you.', 1)], ['Describe tiny horns, nervous bravery, shiny hoards, quick planning, and awe around dragons.'], ['What dragon, hoard, or clan shaped you?', 'What makes you brave when alone?'], ['Rogue', 'Artificer', 'Sorcerer'], 'medium'),
    ('lizardfolk', 'Lizardfolk', 'Cold-eyed reptilian survivalists shaped by hunger and instinct.', 'Lizardfolk are durable, practical, and alien-minded wilderness survivors.', ['lizardfolk', 'reptile', 'reptilian', 'lizard person', 'scaly'], ['beastlike', 'monstrous', 'durable', 'nature', 'aquatic'], 'medium', 30, 'reptilian', ['scales', 'unblinking eyes', 'sharp teeth'], [_passive('lizardfolk', 'Natural Armor', 'Scales provide natural protection.', 2), _active('lizardfolk_bite', 'Bite', 'Your jaws can be used as a natural weapon.', 1, cooldown='turn', effect_type='natural_weapon'), _skill('lizardfolk', 'Survival Instinct', 'You are practical and efficient in wilderness survival.', ['survival'], 1), _movement('lizardfolk', 'Swim Speed', 'You move naturally through water.', 1, {'swimSpeed': 30})], ['Describe scales, stillness, measured speech, blunt practicality, and instinctive danger reading.'], ['What does your character value more than comfort?', 'How do they translate survival logic to soft societies?'], ['Druid', 'Ranger', 'Barbarian'], 'advanced'),
    ('minotaur', 'Minotaur', 'Horned maze-born chargers with strength and ferocious presence.', 'Minotaurs are large martial characters built for charges, intimidation, and force.', ['minotaur', 'bullfolk', 'bull man', 'horned'], ['martial', 'large', 'monstrous', 'durable'], 'medium', 30, 'giantkin', ['horns', 'heavy breath', 'hoof beats'], [_active('minotaur_horns', 'Horns', 'Your horns can be used as natural weapons.', 1, cooldown='turn', effect_type='natural_weapon'), _active('minotaur_charge', 'Charge', 'You can turn a running attack into a forceful impact.', 2, cooldown='short_rest', effect_type='charging_attack'), _skill('minotaur', 'Labyrinth Sense', 'You read mazes, paths, and spatial patterns well.', ['survival'], 1)], ['Describe horns, heavy breath, hoof beats, maze memories, and tension between instinct and discipline.'], ['What labyrinth are you trying to leave or master?', 'Who taught you restraint?'], ['Barbarian', 'Fighter', 'Paladin'], 'medium'),
    ('orc', 'Orc', 'Fierce survivors with relentless drive and raw physical power.', 'Orcs are strong martial characters with endurance, intensity, and bold presence.', ['orc', 'orcish', 'half-orc', 'half orc', 'greenskin'], ['beginner_friendly', 'martial', 'durable', 'darkvision'], 'medium', 30, 'humanoid', ['tusks', 'scarred strength', 'powerful build'], [_darkvision('orc'), _active('orc_relentless_endurance', 'Relentless Endurance', 'Once per rest, refuse to fall from a punishing blow.', 2, action_type='reaction', cooldown='long_rest', effect_type='avoid_dropping'), _passive('orc', 'Powerful Build', 'You count as larger for carrying and forceful physical tasks.', 1), _passive('orc', 'Aggressive', 'You can close distance with raw momentum.', 1)], ['Describe tusks, scarred strength, blunt honesty, clan memory, and refusal to stay down.'], ['What did your clan teach about strength?', 'What do people assume when they see your tusks?'], ['Barbarian', 'Fighter', 'Ranger'], 'easy'),
    ('satyr', 'Satyr', 'Fey revelers with music, mischief, and stubborn charm.', 'Satyrs work for charming troublemakers, wandering musicians, and fey agents.', ['satyr', 'faun', 'goatfolk', 'goat person', 'fey reveler'], ['social', 'magical', 'nature', 'exotic'], 'medium', 35, 'humanoid', ['hooves', 'small horns', 'musical bearing'], [_passive('satyr', 'Magic Resistance', 'You are unusually resistant to hostile magic.', 5), _movement('satyr', 'Mirthful Leaps', 'You leap and bound through rough movement scenes.', 1, {'walkSpeedBonus': 5}), _skill('satyr', 'Reveler', 'You are practiced in performance, parties, and social chaos.', ['performance'], 1)], ['Describe hooves, laughter, sudden songs, fey confidence, and a habit of testing boundaries.'], ['What promise did you make under fey music?', 'What are you running from when the party ends?'], ['Bard', 'Warlock', 'Rogue'], 'medium'),
    ('shifter', 'Shifter', 'Beast-touched wanderers balancing instinct and self-control.', 'Shifters fit hunters, outcasts, guardians, and anyone with a wild inheritance under the skin.', ['shifter', 'werefolk', 'lycan', 'werewolf', 'beastfolk'], ['beastlike', 'martial', 'stealthy', 'nature', 'darkvision'], 'medium', 30, 'humanoid', ['animal eyes', 'sharpening teeth', 'raised hackles'], [_darkvision('shifter'), _active('shifter_shifting', 'Shifting', 'Once per rest, briefly reveal beastlike power.', 2, action_type='bonus_action', effect_type='temporary_bestial_boost'), _skill('shifter', 'Bestial Senses', 'You track scent, motion, and danger well.', ['perception'], 1), _passive('shifter', 'Primal Instinct', 'Your instincts sharpen under pressure.', 1)], ['Describe sharpening teeth, changed eyes, scent memory, and control under pressure.'], ['What animal instinct do you fear losing control to?', 'Who taught you restraint?'], ['Ranger', 'Barbarian', 'Monk'], 'medium'),
    ('tabaxi', 'Tabaxi', 'Feline wanderers driven by speed, curiosity, and stories.', 'Tabaxi are fast, stealthy explorers with strong curiosity and movement tools.', ['tabaxi', 'catfolk', 'cat person', 'feline'], ['beginner_friendly', 'beastlike', 'stealthy', 'nature'], 'medium', 30, 'feline', ['tail', 'feline eyes', 'soft footfalls'], [_active('tabaxi_feline_agility', 'Feline Agility', 'Once per rest, move with a sudden burst of speed.', 2, action_type='bonus_action', effect_type='burst_speed'), _active('tabaxi_claws', 'Claws', 'Your claws can be used as natural weapons.', 1, cooldown='turn', effect_type='natural_weapon'), _skill('tabaxi', 'Catlike Senses', 'You are good at stealth and close observation.', ['stealth', 'perception'], 1), _movement('tabaxi', 'Climb Speed', 'You climb naturally with claws and balance.', 1, {'climbSpeed': 20})], ['Describe tail movement, quiet steps, bright attention, sudden stillness, and curiosity interrupting caution.'], ['What story or object has captured your curiosity?', 'What did you promise to bring back?'], ['Rogue', 'Monk', 'Bard'], 'easy'),
    ('tiefling', 'Tiefling', 'Hell-touched mortals with infernal marks and sharp charisma.', 'Tieflings bring magic, social pressure, and striking infernal visuals.', ['tiefling', 'demon', 'devil', 'infernal', 'half demon', 'hellborn'], ['beginner_friendly', 'magical', 'social', 'darkvision', 'exotic', 'fiendish'], 'medium', 30, 'humanoid', ['horns', 'tail', 'unusual eyes', 'warm skin'], [_darkvision('tiefling'), _resistance('tiefling', 'Fire Resistance', ['fire'], 2), _active('tiefling_infernal_legacy', 'Infernal Legacy', 'Once per rest, call on a small infernal magical effect.', 2, effect_type='minor_infernal_magic'), _narrative('tiefling', 'Commanding Look', 'Your infernal marks shape first impressions.')], ['Describe horns, tails, unusual eyes, warm skin, faint brimstone, and being judged on sight.'], ['What does your infernal heritage mean to you?', 'Who fears or fetishizes your appearance?'], ['Warlock', 'Bard', 'Sorcerer'], 'easy'),
    ('tortle', 'Tortle', 'Shell-backed travelers with patience, wisdom, and natural armor.', 'Tortles are durable wanderers who carry home, defense, and calm with them.', ['tortle', 'turtlefolk', 'turtle person', 'tortoise'], ['beginner_friendly', 'beastlike', 'durable', 'nature', 'aquatic'], 'medium', 30, 'turtlefolk', ['shell', 'beak-like mouth', 'calm eyes'], [_passive('tortle', 'Natural Armor', 'Your shell provides reliable natural defense.', 3), _active('tortle_shell_defense', 'Shell Defense', 'You can withdraw into your shell for extra protection.', 1, action_type='action', cooldown='turn', effect_type='defensive_posture'), _movement('tortle', 'Swim Comfort', 'You are comfortable in water and coastal travel.', 1, {'swimSpeed': 20}), _narrative('tortle', 'Patient Traveler', 'You carry a slow, road-worn perspective.')], ['Describe shell markings, slow smiles, careful pacing, old stories, and calm under attack.'], ['What road or tide are you following?', 'What wisdom do you carry too patiently?'], ['Druid', 'Monk', 'Cleric'], 'easy'),
    ('triton', 'Triton', 'Ocean-born guardians from the pressure and mystery of the deep.', 'Tritons are aquatic, noble, and magical, built for sea-linked adventures.', ['triton', 'merfolk', 'sea elf', 'oceanborn', 'water person'], ['aquatic', 'magical', 'durable', 'social', 'exotic'], 'medium', 30, 'humanoid', ['sea-colored skin', 'fins', 'formal bearing'], [_movement('triton', 'Swim Speed', 'You move naturally through water.', 1, {'swimSpeed': 30}), _passive('triton', 'Amphibious', 'You can breathe air and water.', 1), _active('triton_ocean_magic', 'Ocean Magic', 'Once per rest, call on a small ocean or storm magical effect.', 2, effect_type='minor_ocean_magic'), _resistance('triton', 'Cold Resistance', ['cold'], 2)], ['Describe sea-colored skin, formal manners, saltwater scent, pressure-born calm, and ocean obligations.'], ['What duty brought you from the deep?', 'What surface custom confuses you most?'], ['Paladin', 'Cleric', 'Sorcerer'], 'medium'),
    ('warforged', 'Warforged', 'Living constructs built for purpose and searching for self.', 'Warforged fit stories about created life, duty after war, personhood, memory, and choice.', ['warforged', 'robot', 'construct', 'machine', 'automaton'], ['durable', 'martial', 'exotic', 'construct'], 'medium', 30, 'construct', ['metal plates', 'wood or stone frame', 'artificial eyes'], [_passive('warforged', 'Constructed Resilience', 'You resist some ordinary biological hardship.', 2), _passive('warforged', 'Integrated Protection', 'Your body integrates armor-like protection.', 2), _narrative('warforged', 'Sleepless', 'You do not need ordinary sleep, though you still need rest.')], ['Describe metal, wood, stone, quiet servos, careful speech, and small choices that reveal personhood.'], ['Who built you, and do they still claim you?', 'What choice first felt like your own?'], ['Fighter', 'Artificer', 'Paladin'], 'medium'),
    ('yuan-ti', 'Yuan-ti', 'Serpentine schemers with poison, poise, and ancient secrets.', 'Yuan-ti are exotic serpentfolk suited to intrigue, magic, and cool menace.', ['yuan-ti', 'yuan ti', 'snakefolk', 'snake person', 'serpent'], ['monstrous', 'magical', 'stealthy', 'social', 'exotic'], 'medium', 30, 'reptilian', ['slit pupils', 'serpentine grace', 'cool skin'], [_resistance('yuan_ti', 'Poison Resilience', ['poison'], 2), _skill('yuan_ti', 'Serpentine Grace', 'You move with careful poise and controlled menace.', ['stealth'], 1), _passive('yuan_ti', 'Innate Guile', 'You are comfortable with secrets, pressure, and occult manners.', 1)], ['Describe slit pupils, measured movements, cool skin, soft consonants, and unreadable expressions.'], ['What ancient secret shaped your bloodline?', 'Are you escaping a cultic past or using it?'], ['Warlock', 'Rogue', 'Sorcerer'], 'advanced'),
]

for spec in _MORE_CURATED_SPECS:
    (
        race_id,
        name,
        description_short,
        description_long,
        aliases,
        tags,
        size,
        base_speed,
        body_type,
        common_features,
        traits,
        ai_hints,
        roleplay_hooks,
        recommended_classes,
        difficulty,
    ) = spec
    CURATED_RACES.append(
        _race(
            race_id,
            name,
            description_short,
            description_long,
            aliases=aliases,
            tags=tags,
            size=size,
            base_speed=base_speed,
            body_type=body_type,
            common_features=common_features,
            traits=traits,
            ai_hints=ai_hints,
            roleplay_hooks=roleplay_hooks,
            recommended_classes=recommended_classes,
            difficulty=difficulty,
        )
    )

def _race_profile(
    short: str,
    long: str,
    origin: str,
    height: str,
    weight: str,
    languages: list[str],
    proficiencies: list[str],
    friendly_with: list[str],
    wary_of: list[str],
) -> dict[str, Any]:
    return {
        'descriptionShort': short,
        'descriptionLong': long,
        'originStory': origin,
        'physical': {
            'averageHeight': height,
            'averageWeight': weight,
        },
        'languages': languages,
        'commonProficiencies': proficiencies,
        'friendlyWith': friendly_with,
        'waryOf': wary_of,
    }


CURATED_RACE_PROFILES = {
    'aarakocra': _race_profile(
        'Cliff-born avian scouts whose wings make height, weather, and distance part of every plan.',
        'Aarakocra communities gather in aeries, sky temples, and high passes where the wind is a road and a teacher.',
        'They read smoke, rivers, armies, and storms from above, then decide where danger will arrive before others see it.',
        '5 to 6 feet',
        '80 to 120 lb',
        ['Common', 'Auran'],
        ['Perception', 'Survival', 'Acrobatics'],
        ['Rangers', 'Druids', 'mountain clans'],
        ['underground cultures', 'cage-builders', 'heavy infantry commanders'],
    ),
    'aasimar': _race_profile(
        'Celestial-touched mortals marked by radiant signs, healing gifts, and impossible expectations.',
        'Aasimar may be born after omens, divine bargains, or quiet bloodline miracles that become visible under pressure.',
        'Their light can feel like comfort, duty, surveillance, or a crown they never asked to wear.',
        '5 to 6.5 feet',
        '110 to 220 lb',
        ['Common', 'Celestial'],
        ['Religion', 'Insight', 'Persuasion'],
        ['Clerics', 'Paladins', 'good-aligned temples'],
        ['fiendish cults', 'those who expect obedience', 'superstitious zealots'],
    ),
    'bugbear': _race_profile(
        'Long-limbed ambushers: big enough to terrify, quiet enough to arrive unseen.',
        'Bugbears often come from raiding bands, border tribes, or hard places where patience and reach matter.',
        'A heroic Bugbear can feel like a shadow that chose discipline over cruelty.',
        '6.5 to 8 feet',
        '250 to 350 lb',
        ['Common', 'Goblin'],
        ['Stealth', 'Athletics', 'Intimidation'],
        ['Goblins', 'Hobgoblins', 'mercenary companies'],
        ['city guards', 'Elven patrols', 'settlements hurt by raids'],
    ),
    'changeling': _race_profile(
        'Living disguises built for intrigue, reinvention, and stories about identity.',
        'Changelings may live openly as flexible artisans, hidden in city crowds, or scattered through spy networks.',
        'Their gift is not only a new face, but the burden of deciding which face is true.',
        '5 to 6 feet',
        '100 to 180 lb',
        ['Common', 'one local or social language'],
        ['Deception', 'Performance', 'Persuasion'],
        ['Bards', 'Rogues', 'actors', 'cosmopolitan Humans'],
        ['inquisitors', 'rigid noble houses', 'bloodline-obsessed cultures'],
    ),
    'dragonborn': _race_profile(
        'Draconic humanoids whose scales, breath, and bearing make ancestry impossible to ignore.',
        'Dragonborn often come from proud clans, martial lineages, or scattered families carrying ancient draconic echoes.',
        'People often see the dragon first and the person second, which can become honor, rebellion, or pressure.',
        '6 to 7 feet',
        '220 to 320 lb',
        ['Common', 'Draconic'],
        ['Intimidation', 'Athletics', 'History'],
        ['Paladins', 'Sorcerers', 'Kobolds who revere dragons'],
        ['dragon hunters', 'rival draconic clans', 'fearful villages'],
    ),
    'dwarf': _race_profile(
        'Stone-wise, craft-proud people built to endure hardship, debt, grief, and battle.',
        'Dwarven holds, hill clans, and forge towns value memory, names carved in stone, and tools with stories attached.',
        'A Dwarf often knows what they owe, who they stand beside, and which promises must outlive comfort.',
        '4 to 5 feet',
        '150 to 220 lb',
        ['Common', 'Dwarvish'],
        ['History', 'Smithing tools', 'Mason tools'],
        ['Gnomes', 'Humans', 'lawful orders', 'craft guilds'],
        ['Orc raiders', 'Goblin warbands', 'oathbreakers'],
    ),
    'elf': _race_profile(
        'Long-lived, perceptive people shaped by magic, memory, beauty, and old grief.',
        'Elven communities range from moonlit forest courts to high towers and hidden underground enclaves.',
        'An Elf may remember songs older than kingdoms and adventure when eternity becomes too still.',
        '5 to 6.5 feet',
        '90 to 170 lb',
        ['Common', 'Elvish'],
        ['Perception', 'Arcana', 'Stealth'],
        ['Druids', 'Rangers', 'Fey-touched people', 'scholars'],
        ['Orc warbands', 'short-sighted rulers', 'forest despoilers'],
    ),
    'fairy': _race_profile(
        'Tiny fey wanderers with wings, bright magic, and rules that rarely match mortal logic.',
        'Fairies trace roots to the Feywild, enchanted groves, moonlit courts, or bargains made near impossible flowers.',
        'They look delicate, but they come from a world where beauty is sharp and laughter can be a warning.',
        '2 to 3 feet',
        '20 to 40 lb',
        ['Common', 'Sylvan'],
        ['Arcana', 'Performance', 'Nature'],
        ['Satyrs', 'Druids', 'Elves', 'other Fey-touched wanderers'],
        ['iron-bound hunters', 'oathbreakers', 'coldly practical soldiers'],
    ),
    'firbolg': _race_profile(
        'Gentle giantkin with forest magic, quiet strength, and a deep dislike of waste.',
        'Firbolg clans guard old woods, hidden valleys, and places where beasts and spirits still speak.',
        'Their power is rarely loud; it feels like shade arriving on a hot day.',
        '7 to 8 feet',
        '240 to 320 lb',
        ['Common', 'Giant', 'Sylvan'],
        ['Nature', 'Animal Handling', 'Medicine'],
        ['Druids', 'Rangers', 'forest villages'],
        ['reckless loggers', 'greedy nobles', 'fire-happy armies'],
    ),
    'genasi': _race_profile(
        'Elemental heirs whose bodies carry fire, water, earth, or air as living heritage.',
        'Genasi may descend from genies, planar rifts, or elemental disasters that changed a bloodline.',
        'They are often told they are too bright, too still, too stormy, or too strange until they claim it as power.',
        '5 to 6.5 feet',
        '100 to 220 lb',
        ['Common', 'Primordial'],
        ['Arcana', 'Survival', 'one element-themed tool or skill'],
        ['Sorcerers', 'Druids', 'elemental cults'],
        ['planar binders', 'opposing elemental factions', 'people who fear uncontrolled magic'],
    ),
    'gnome': _race_profile(
        'Small, sharp-minded makers and wonder-seekers with stubborn magical resilience.',
        'Gnomish homes are dense with workshops, illusions, burrows, gardens, jokes, and dangerous unfinished ideas.',
        'A Gnome often leaves home because a theory demands testing or a mystery has become unbearable.',
        '3 to 4 feet',
        '35 to 60 lb',
        ['Common', 'Gnomish'],
        ['Arcana', 'Investigation', 'Tinker tools'],
        ['Dwarves', 'Halflings', 'Artificers', 'curious Elves'],
        ['bullies', 'anti-magic tyrants', 'people who ban experiments'],
    ),
    'goblin': _race_profile(
        'Small, fast survivors who turn fear, clutter, and opportunity into tactics.',
        'Goblin communities grow in the cracks of stronger powers: ruins, alleys, caves, armies, and scrap markets.',
        'A Goblin hero asks what happens when someone raised to be disposable decides they are not.',
        '3 to 4 feet',
        '40 to 80 lb',
        ['Common', 'Goblin'],
        ['Stealth', 'Sleight of Hand', 'Tinker tools'],
        ['Bugbears', 'Hobgoblins', 'Rogues', 'underdogs'],
        ['city guards', 'Dwarven holds', 'bounty hunters'],
    ),
    'goliath': _race_profile(
        'Mountain-born giantkin who value endurance, fair challenge, and visible deeds.',
        'Goliath clans survive above the tree line, where storms punish arrogance and every resource is earned.',
        'They often leave home to test themselves and learn which challenges are worth winning.',
        '7 to 8 feet',
        '280 to 360 lb',
        ['Common', 'Giant'],
        ['Athletics', 'Survival', 'Intimidation'],
        ['Dwarves', 'Firbolgs', 'martial orders'],
        ['cowards who risk others', 'soft nobles', 'predators who hunt the weak for sport'],
    ),
    'halfling': _race_profile(
        'Small, warm, brave people whose luck often looks like courage arriving on time.',
        'Halfling villages, caravans, and river communities prize comfort, kinship, gossip, and practical heroism.',
        'Their adventures often start with a small promise that somehow becomes legend.',
        '3 to 3.5 feet',
        '35 to 60 lb',
        ['Common', 'Halfling'],
        ['Stealth', 'Persuasion', 'Cook utensils'],
        ['Humans', 'Gnomes', 'Dwarves', 'kindly travelers'],
        ['bullies', 'warbands', 'people who mistake kindness for weakness'],
    ),
    'harengon': _race_profile(
        'Quick rabbitfolk with springing legs, sharp hearing, and restless luck.',
        'Harengon often come from fey roads, meadow villages, wandering bands, or places where danger is heard first.',
        'They rarely enter a room without already knowing the exits.',
        '3 to 5 feet',
        '35 to 100 lb',
        ['Common', 'Sylvan or one local language'],
        ['Acrobatics', 'Perception', 'Survival'],
        ['Fairies', 'Satyrs', 'Rangers', 'traveling performers'],
        ['trappers', 'patient predators', 'slow bureaucracies'],
    ),
    'hobgoblin': _race_profile(
        'Disciplined tacticians who read battlefields, favors, and chains of command.',
        'Hobgoblin societies often form around legions, households, academies, or strict mutual obligation.',
        'Their stories often ask whether discipline can protect instead of conquer.',
        '5.5 to 6.5 feet',
        '150 to 220 lb',
        ['Common', 'Goblin'],
        ['History', 'Intimidation', 'one martial weapon or armor tradition'],
        ['Fighters', 'disciplined Bards', 'Goblin clans', 'military orders'],
        ['chaotic raiders', 'undisciplined commanders', 'old legion enemies'],
    ),
    'human': _race_profile(
        'Adaptable, ambitious people who fit almost any class, culture, or campaign premise.',
        'Human kingdoms, tribes, free cities, and frontier towns vary wildly, but short lives often create urgency.',
        'A Human does not need ancient blood to matter; a village oath or family debt can be enough.',
        '5 to 6.5 feet',
        '110 to 250 lb',
        ['Common', 'one regional language'],
        ['Any one skill', 'Any one tool', 'local culture knowledge'],
        ['most cosmopolitan races', 'mixed settlements', 'trade guilds'],
        ['old grudges they inherited', 'powers that see them as short-lived tools'],
    ),
    'afro-diasporic-human': _race_profile(
        'A human heritage option for Black human heroes with diaspora-inspired style, family, and culture.',
        'Afro-Diasporic Humans are ordinary humans, not a separate species; their identity comes through portrait choice, names, communities, clothing, faith, craft, family history, and player-defined homeland details.',
        'An Afro-Diasporic Human can be a city-born duelist, shrine scholar, caravan guard, court musician, village defender, sailor, mage, or anything else a Human could be.',
        'Varies by person',
        'Varies by person',
        ['Common', 'one regional or cultural language'],
        ['Any one skill', 'Any one tool', 'local culture knowledge'],
        ['Human communities', 'Halflings', 'Dwarves', 'Elves', 'Tieflings'],
        ['Yuan-ti infiltrators', 'Bugbear raiders', 'Hobgoblin armies', 'Changeling impostors', 'Minotaur pirates'],
    ),
    'kenku': _race_profile(
        'Corvid mimics with perfect ears, borrowed voices, and a gift for shadowed work.',
        'Kenku live among city roofs, criminal crews, messenger networks, monasteries, or any place memory is valuable.',
        'They remember sound like other people remember faces.',
        '4.5 to 5.5 feet',
        '90 to 140 lb',
        ['Common', 'Auran or one local language'],
        ['Stealth', 'Deception', 'Forgery kit'],
        ['Rogues', 'Bards', 'Aarakocra', 'urban outcasts'],
        ['people who demand plain speech', 'law courts', 'those who fear mimicry'],
    ),
    'kobold': _race_profile(
        'Small draconic tunnelers who survive through traps, teamwork, and fierce little plans.',
        'Kobold warrens form around mines, caves, ruins, dragon shrines, and dangerous places stronger folk ignore.',
        'They can turn a rope, bell, jar of oil, and three cousins into a battle plan.',
        '2.5 to 3.5 feet',
        '25 to 45 lb',
        ['Common', 'Draconic'],
        ['Trap tools', 'Stealth', 'Sleight of Hand'],
        ['Dragonborn', 'Goblins', 'Artificers', 'dragon cults'],
        ['giant predators', 'Dwarven miners', 'warren-clearing adventurers'],
    ),
    'lizardfolk': _race_profile(
        'Reptilian survivalists with natural armor, blunt instincts, and marsh-born practicality.',
        'Lizardfolk villages rise in swamps, deltas, humid ruins, and river mazes where sentiment matters less than use.',
        'Their story is strongest when practicality slowly learns friendship without becoming less honest.',
        '5.5 to 7 feet',
        '180 to 280 lb',
        ['Common', 'Draconic'],
        ['Survival', 'Nature', 'Leatherworker tools'],
        ['Druids', 'Rangers', 'coastal Tritons', 'practical hunters'],
        ['wasteful nobles', 'cold-climate cities', 'people who mistake bluntness for malice'],
    ),
    'minotaur': _race_profile(
        'Horned, powerful maze-born warriors whose presence turns movement into threat.',
        'Minotaur cultures may come from labyrinth cities, island clans, arena traditions, or old curses turned into identity.',
        'Their best stories circle rage, direction, honor, and the choice not to be a monster.',
        '6 to 7.5 feet',
        '250 to 350 lb',
        ['Common', 'one of Giant, Minotaur, or a local tongue'],
        ['Athletics', 'Intimidation', 'Survival'],
        ['Fighters', 'Goliaths', 'honor-bound orders', 'arena veterans'],
        ['maze cults', 'people who see only horns', 'mind-control magic'],
    ),
    'orc': _race_profile(
        'Fierce, enduring people whose strength is tied to survival, clan, and forward motion.',
        'Orc communities range from nomadic hunters to fortified clans and city neighborhoods that prize direct courage.',
        'Their best scenes show the difference between violence and protection, rage and conviction.',
        '6 to 7 feet',
        '180 to 280 lb',
        ['Common', 'Orc'],
        ['Athletics', 'Intimidation', 'Survival'],
        ['Half-orcs', 'Goliaths', 'frontier communities', 'martial companions'],
        ['old clan enemies', 'Elven border patrols', 'people who expect brutality'],
    ),
    'satyr': _race_profile(
        'Fey revelers whose music, charm, and mischief hide surprisingly sharp instincts.',
        'Satyrs come from Feywild groves, festival roads, enchanted vineyards, or mortal communities touched by fey bargains.',
        'They know rules are real, but so are loopholes, songs, and invitations.',
        '4.5 to 5.5 feet',
        '100 to 160 lb',
        ['Common', 'Sylvan'],
        ['Performance', 'Persuasion', 'one musical instrument'],
        ['Fairies', 'Harengon', 'Bards', 'Druids'],
        ['joyless tyrants', 'oath collectors', 'people who exploit hospitality'],
    ),
    'shifter': _race_profile(
        'Beast-touched people whose instincts surface in claws, senses, speed, or hide.',
        'Shifter communities often live on settled edges where lycanthropic myths, hunters, and family packs overlap.',
        'Their story often asks whether instinct is a danger, a compass, or both.',
        '5 to 6.5 feet',
        '100 to 220 lb',
        ['Common', 'one regional or pack language'],
        ['Perception', 'Survival', 'Athletics'],
        ['Rangers', 'Druids', 'Tabaxi', 'frontier communities'],
        ['silvered hunters', 'superstitious villages', 'people who confuse them with cursed lycanthropes'],
    ),
    'tabaxi': _race_profile(
        'Feline wanderers of speed, climbing, stealth, and curiosity that refuses to sit still.',
        'Tabaxi clans and traveling families often collect stories, routes, songs, and beautiful objects rather than territory.',
        'A Tabaxi may chase a rumor across countries because the question itself has claws.',
        '5 to 6.5 feet',
        '90 to 200 lb',
        ['Common', 'one clan or trade language'],
        ['Stealth', 'Acrobatics', 'Perception'],
        ['Bards', 'Rogues', 'Harengon', 'traveling merchants'],
        ['slavers', 'those who cage performers', 'people who destroy stories'],
    ),
    'tiefling': _race_profile(
        'Infernal-marked mortals with fire, charisma, and a reputation they may not deserve.',
        'Tiefling heritage can come from old pacts, fiendish influence, cursed bloodlines, or planar accidents.',
        'Their story often begins when a devil-shaped silhouette is judged before the person speaks.',
        '5 to 6.5 feet',
        '100 to 220 lb',
        ['Common', 'Infernal'],
        ['Deception', 'Persuasion', 'Arcana'],
        ['Warlocks', 'Bards', 'Changelings', 'other outsiders'],
        ['celestial zealots', 'superstitious villages', 'fiends who claim ownership'],
    ),
    'tortle': _race_profile(
        'Shell-backed wanderers with patient wisdom, natural armor, and road-worn calm.',
        'Tortles come from coastal villages, island routes, river monasteries, or slow pilgrim paths.',
        'Home is not only a place for them; it is something carried, remembered, and practiced.',
        '5 to 6 feet',
        '400 to 500 lb',
        ['Common', 'Aquan or one coastal language'],
        ['Survival', 'Nature', 'Cartographer tools'],
        ['Druids', 'Monks', 'Tritons', 'coastal communities'],
        ['poachers', 'reckless sailors', 'people who mock patience'],
    ),
    'triton': _race_profile(
        'Ocean-born guardians with amphibious bodies, deep magic, and formal pride.',
        'Triton enclaves stand in reef citadels, abyssal watchposts, and undersea courts guarding surface-forgotten threats.',
        'A Triton may come ashore because a deep-sea oath points upward.',
        '5 to 6 feet',
        '100 to 180 lb',
        ['Common', 'Primordial', 'Aquan'],
        ['Athletics', 'History', 'Persuasion'],
        ['Tortles', 'coastal Lizardfolk', 'Paladins', 'sailors'],
        ['sea raiders', 'surface polluters', 'aberrations from the deep'],
    ),
    'warforged': _race_profile(
        'Living constructs of metal, wood, and will, built for purpose but searching for self.',
        'Warforged are created in foundries, mage-forges, military programs, or ancient workshops whose purpose may be lost.',
        'Their story is about choosing what they are after being built for what they were.',
        '6 to 7 feet',
        '250 to 350 lb',
        ['Common', 'one creator or military language'],
        ['Smithing tools', 'Athletics', 'History'],
        ['Artificers', 'Fighters', 'Dwarven smiths', 'other created beings'],
        ['former masters', 'anti-construct zealots', 'people who treat them as property'],
    ),
    'yuan-ti': _race_profile(
        'Serpentine, poison-wise people with controlled poise and ancient secretive traditions.',
        'Yuan-ti lineages come from serpent cults, jungle temples, hidden noble houses, or old empires of transformation.',
        'A heroic Yuan-ti can keep the elegance while rejecting the cruelty.',
        '5 to 6.5 feet',
        '100 to 220 lb',
        ['Common', 'Draconic or Abyssal'],
        ['Deception', 'Stealth', 'Poisoner kit'],
        ['Rogues', 'Warlocks', 'lost-empire scholars', 'pragmatic Lizardfolk'],
        ['temple inquisitors', 'anti-cult militias', 'people who assume they are villains'],
    ),
}


CURATED_RACE_COPY_POLISH = {
    'aarakocra': {
        'descriptionLong': 'Aarakocra are winged people of high aeries, storm-cut cliffs, sky temples, and wind-carved passes. Their homes are built around sightlines, migration paths, and communal watch duty, so they often think in terms of distance, weather, and safe routes before they think in terms of walls or roads. In play, they make excellent scouts, messengers, archers, and outsiders who struggle whenever a dungeon, crowd, or ceiling takes the sky away.',
        'originStory': 'Most Aarakocra grow up learning that the world is a pattern seen from above: smoke means settlement, circling birds mean carrion, and a dark line on the horizon means rain or war. A young Aarakocra is usually taught to serve the flock by watching for danger and carrying news faster than groundfolk can react. When one becomes an adventurer, it is often because something below has become too important to merely observe from the clouds.',
    },
    'aasimar': {
        'descriptionLong': 'Aasimar are mortal people carrying a visible or hidden trace of celestial power. Some are raised by ordinary families who do not understand the omens around them, while others grow up inside temples, prophecies, or bloodlines that expect them to become symbols. Their light can heal and inspire, but it can also isolate them, because strangers may treat an Aasimar as proof, weapon, saint, or threat before they are treated as a person.',
        'originStory': "An Aasimar's story often begins with expectation: a guardian voice in dreams, a birthmark like a star, a village that prayed over them, or a family that feared what they would become. The most interesting Aasimar are not simply good; they have to choose what goodness means when everyone is watching. Playing one should feel like carrying a lantern through a dark room while wondering whether the light is yours or something using you.",
    },
    'bugbear': {
        'descriptionLong': 'Bugbears are large goblinoids with long reach, quiet movement, and a reputation built from ambush stories. Many grow up in rough borderlands, raiding bands, mercenary camps, or mixed goblinoid communities where survival rewards patience more than noise. A Bugbear character can be frightening without being stupid, lazy without being weak, and gentle in ways that surprise people who only see the size and teeth.',
        'originStory': 'A Bugbear often learns to wait before they learn to charge. Stillness is a tool: wait for the guard to turn, wait for the fire to burn low, wait for the enemy to decide the room is empty. An adventuring Bugbear may be trying to escape old monster stories, profit from them, or prove that the same body built for ambush can also shield a friend.',
    },
    'changeling': {
        'descriptionLong': 'Changelings are people whose faces, voices, and bodies can shift, making identity a living choice rather than a fixed fact. They fit naturally into cities, courts, theaters, spy networks, criminal crews, and traveling communities where names and appearances already carry social power. A Changeling is not just a disguise machine; they are someone who knows how fragile trust can be when the world believes a face is proof.',
        'originStory': "Many Changelings grow up with rules about which faces are safe, which names belong to family, and when it is dangerous to be seen changing. Some become artists of empathy, learning another person's posture and voice to understand them better; others become ghosts who survive by never being known completely. Playing one should raise questions: which identity is comfort, which is armor, and who gets to see the face underneath?",
    },
    'dragonborn': {
        'descriptionLong': 'Dragonborn are draconic humanoids whose scales, breath, and stature make ancestry impossible to hide. Many come from clan-based societies, martial households, temple lineages, or scattered families trying to define themselves apart from true dragons. Their elemental breath is not just an attack; it is a sign of inheritance, ritual, temper, reputation, and the way strangers decide whether to fear or respect them.',
        'originStory': 'A Dragonborn child usually learns early that people see the dragon before they see the person. In some communities that brings honor, in others suspicion, and in many places both at once. A Dragonborn adventurer might be chasing clan glory, rejecting a bloodline, seeking the source of their element, or trying to prove that ancestry is a beginning rather than a command.',
    },
    'dwarf': {
        'descriptionLong': 'Dwarves are sturdy, tradition-rich people shaped by stone halls, forge smoke, clan memory, and practical endurance. Many live in mountain holds, hill towns, mining cities, or craft districts where reputation is built over years and a tool can carry as much history as a noble title. Dwarves tend to make excellent guardians, priests, smiths, soldiers, and stubborn problem-solvers because they are taught that good work and good promises should survive pressure.',
        'originStory': 'A Dwarf often knows the story of a family hammer, an old tunnel collapse, a feud no outsider understands, or a song sung when the hold doors close. They are not only short and tough; they are people raised around memory made physical. A Dwarf adventurer may leave home to repay a debt, recover a lost craft, test themselves beyond the hold, or decide which traditions deserve to be carried forward.',
    },
    'elf': {
        'descriptionLong': 'Elves are long-lived, perceptive people whose lives are shaped by magic, memory, beauty, and distance from ordinary time. Their communities might be forest courts, high spires, wandering enclaves, moonlit villages, or hidden underground houses, and each can produce a very different kind of Elf. Because they often outlive kingdoms and friendships, Elves can seem graceful, patient, haunted, arrogant, careful, or painfully sentimental depending on what they have survived.',
        'originStory': 'An Elf may remember a border before it was a kingdom, a tree before it was sacred, or a lover whose grandchildren are now old. That long memory can be a gift, but it can also make the present feel fragile and brief. An Elf adventurer often leaves home when beauty becomes stillness, when grief becomes too familiar, or when the younger world does something surprising enough to deserve attention.',
    },
    'fairy': {
        'descriptionLong': 'Fairies are small fey people with wings, bright magic, and instincts shaped by a world where promises, names, seasons, and jokes can carry real power. Many come from Feywild courts, enchanted groves, moonlit markets, flower kingdoms, or strange mortal families touched by fey bargains. They are whimsical, but not harmless; a Fairy may be playful, eerie, vain, generous, cruelly literal, or deeply loyal according to rules no one else knows.',
        'originStory': 'A Fairy might have fled an endless dance, been exiled for breaking a ridiculous law, followed a mortal song through a ring of mushrooms, or been sent to collect a debt no one remembers making. Their tiny size and glittering wings make people underestimate them, which is often a mistake. Playing a Fairy should feel like carrying a piece of a beautiful, dangerous dream into a world that insists on being sensible.',
    },
    'firbolg': {
        'descriptionLong': 'Firbolgs are gentle giantkin tied to old forests, hidden valleys, quiet magic, and the idea that strength exists to protect what cannot protect itself. Their communities often live far from roads, sharing land with animals, spirits, and ancient trees rather than claiming it as property. They make natural druids, clerics, guides, and guardians, but they can also be awkward travelers when city life treats greed and haste as normal.',
        'originStory': 'A Firbolg is often raised to ask what a place needs before asking what they want from it. They may know which stream floods first, which deer is sick, and which old stone should never be moved. A Firbolg adventurer usually leaves because balance has been broken, a forest sent them, or curiosity finally overcame the comfort of being useful at home.',
    },
    'genasi': {
        'descriptionLong': 'Genasi are people whose blood, body, or soul has been marked by elemental power. Fire Genasi may glow with banked heat, Water Genasi may move like tides, Earth Genasi may seem carved from patience, and Air Genasi may never feel fully still. They often come from genie bloodlines, planar accidents, elemental shrines, storm-touched families, or places where the boundary between the world and the elements wore thin.',
        'originStory': 'Most Genasi learn that their emotions and bodies make them visible: hair drifting like smoke, skin cooling a room, footprints dusty with stone, or laughter arriving with a breeze. Some are celebrated as omens, while others are treated like accidents that never stopped happening. A Genasi adventurer often wants to understand whether they are a person with elemental power or an element learning to be a person.',
    },
    'gnome': {
        'descriptionLong': 'Gnomes are small, bright-minded people known for curiosity, invention, illusion, and stubborn mental resilience. Their homes may be burrows full of books, forest workshops hidden under roots, clockwork neighborhoods, or lively academic enclaves where jokes and experiments are both serious business. A Gnome character usually brings cleverness, wonder, and a willingness to ask why not at exactly the wrong or right moment.',
        'originStory': 'A Gnome often grows up surrounded by unfinished projects, family theories, prank traditions, and tools that are only dangerous if used as labeled. Curiosity is not treated as childish; it is a social responsibility. A Gnome adventurer may be testing a device, chasing a mystery, documenting the impossible, or proving that being small does not mean thinking small.',
    },
    'goblin': {
        'descriptionLong': 'Goblins are small, quick survivors who thrive in ruins, alleys, caves, scrap towns, war camps, and other places stronger powers overlook. Their cultures often reward improvisation, alertness, humor under pressure, and the ability to make something useful out of garbage, fear, and bad odds. A Goblin hero can be cowardly and brave in the same scene, because they know bravery without an exit plan is just volunteering to be dead.',
        'originStory': 'A Goblin may have grown up being told they were expendable by bosses, warlords, adventurers, or the world in general. That produces sharp eyes, quick hands, and a talent for measuring danger faster than pride. An adventuring Goblin is often someone who decided survival was not enough; they want respect, treasure, revenge, family, or proof that the smallest person in the room can still change the ending.',
    },
    'goliath': {
        'descriptionLong': 'Goliaths are tall mountain-born people shaped by thin air, brutal weather, clan trials, and a culture of visible deeds. Many grow up where food, shelter, and mistakes all matter, so competition is not only pride but a way to discover who is ready, who needs help, and who can be trusted when the storm closes in. They make powerful warriors and guardians, but their best stories are about endurance, fairness, and learning that not every challenge is solved by winning.',
        'originStory': 'A Goliath may carry carved marks, trophies, scars, or spoken titles that remember important trials. Those marks are not just boasting; they are lessons made visible. A Goliath adventurer might leave the mountain to test themselves, redeem a failure, find a challenge worthy of their name, or learn why lowland people fight so fiercely over things that cannot survive a winter.',
    },
    'halfling': {
        'descriptionLong': 'Halflings are small, warm, brave people often rooted in villages, caravans, river communities, farms, and close families where comfort and courage are not opposites. They value meals, stories, practical kindness, gossip, and the sort of bravery that shows up because someone has to help. A Halfling fits nearly any campaign because they turn ordinary decency into an adventuring strength.',
        'originStory': 'A Halfling adventure rarely begins with a hunger for glory. It begins with a cousin missing, a farm threatened, a promise made, a road calling, or a simple refusal to let larger people decide what matters. Their luck feels less like destiny and more like the world making room for someone who keeps stepping forward despite every sensible reason not to.',
    },
    'harengon': {
        'descriptionLong': 'Harengon are rabbitfolk with sharp hearing, springing movement, and a quickness that feels partly physical and partly fey. Many come from meadow villages, traveling bands, fey roads, seasonal courts, or borderlands where danger is survived by hearing it early and moving before fear can root you. They are energetic scouts, duelists, messengers, performers, and survivors who make stillness feel suspicious.',
        'originStory': 'A Harengon often knows the exits before the introductions are done. Their ears betray mood, their feet want the next leap, and their instincts treat hesitation as a luxury. A Harengon adventurer may be following a lucky road, fleeing a prophecy, chasing a festival, or proving that nervous energy can become heroism when pointed in the right direction.',
    },
    'hobgoblin': {
        'descriptionLong': 'Hobgoblins are goblinoid people shaped by discipline, tactics, obligation, and the belief that a group survives when each member knows their role. Their societies are often legions, fortress towns, martial academies, or strict households where honor is measured through service and competence. A Hobgoblin can be a soldier, strategist, bodyguard, officer, rebel, or reformer trying to decide what discipline is for.',
        'originStory': 'A Hobgoblin usually understands command before freedom. They know how supplies move, why formations break, which insult starts a duel, and how much chaos one frightened recruit can cause. An adventuring Hobgoblin may be seeking a new unit, escaping a cruel one, proving loyalty to chosen companions, or trying to build a life where order protects instead of dominates.',
    },
    'human': {
        'descriptionLong': 'Humans are adaptable, ambitious, and culturally varied enough to fit almost any class, region, or story. Their kingdoms, tribes, free cities, nomad bands, guilds, and frontier towns can differ more from each other than some entirely different ancestries do. Because human lives are comparatively brief, their stories often carry urgency: build now, love now, conquer now, fix it before the chance is gone.',
        'originStory': 'A Human character does not need ancient blood or obvious magic to matter. They can come from a fishing village, noble court, failed apprenticeship, army camp, crime family, temple school, or farm at the edge of a haunted wood. The heart of a Human story is choice under time pressure: what will they become with one short life and too many possible roads?',
    },
    'afro-diasporic-human': {
        'descriptionLong': 'Afro-Diasporic Humans are human characters whose appearance and cultural cues draw from African diaspora fantasy imagery while leaving homeland, family, personality, class, and social role open. They are here for representation, not as a different species or a fixed set of traits. In play, they work like Humans: adaptable, culturally varied, and able to fit nearly any class or campaign premise.',
        'originStory': 'An Afro-Diasporic Human story should start with the same freedom as any other Human story. They might inherit a family oath, train in a temple school, guard a market district, study old magic, cross the sea with a caravan, or leave a quiet village because danger came too close. The DM should ask what culture, community, and history the player wants rather than assuming one.',
    },
    'kenku': {
        'descriptionLong': 'Kenku are corvid-like people known for mimicry, precise memory, stealth, and voices made from echoes. They often live on city roofs, in messenger guilds, criminal crews, monasteries, docks, theaters, or anywhere sound and secrecy have value. A Kenku character is not only a talks funny gimmick; they are someone who records the world through sound and may understand truth by replaying what others missed.',
        'originStory': "A Kenku may speak with a dead mentor's warning, a tavern keeper's laugh, and a guard captain's command all in one conversation. Every borrowed phrase carries history. A Kenku adventurer might be searching for an original voice, escaping a life of imitation, using mimicry as art, or proving that a person assembled from echoes is still a person.",
    },
    'kobold': {
        'descriptionLong': 'Kobolds are small draconic tunnelers who survive through traps, teamwork, alertness, and fierce respect for anything bigger than them. Their warrens often form in mines, caves, ruins, sewers, dragon shrines, and dangerous places no one sensible would choose without a very good plan. A Kobold character is excellent for players who enjoy clever problem-solving, underdog courage, and a draconic spark without the size or certainty of a Dragonborn.',
        'originStory': 'A Kobold grows up knowing the ceiling height, the loose stones, the alarm bells, and which tunnel floods first. Alone they may be frightened; with a plan they can be terrifying. A Kobold adventurer might be seeking a dragon, fleeing a collapsed warren, collecting treasures for a chosen family, or proving that bravery is not the absence of fear but refusing to let fear make all the decisions.',
    },
    'lizardfolk': {
        'descriptionLong': 'Lizardfolk are reptilian survivalists whose cultures often grow around marshes, deltas, warm ruins, river mazes, and hard wilderness. They are practical, direct, and sometimes unsettling to softer societies because they tend to judge customs by whether they help anyone survive. A Lizardfolk character works best when played as genuinely different rather than merely rude: emotions exist, but hunger, weather, injury, and danger are harder to ignore.',
        'originStory': 'A Lizardfolk may not understand why a warrior names a sword, why mourners waste good food, or why nobles value silk more than a sharp knife. That does not make them heartless; it means their heart was trained by a world where sentiment without usefulness can get a tribe killed. Their adventuring story often becomes the slow discovery that friendship, art, and memory can be useful in ways teeth cannot measure.',
    },
    'minotaur': {
        'descriptionLong': 'Minotaurs are horned, powerful people tied to labyrinths, charges, physical presence, and the struggle to direct instinct rather than be ruled by it. Their cultures may come from maze cities, island clans, arena traditions, war herds, temple guardians, or old curses transformed into identity. A Minotaur character can be brutal, noble, spiritual, tactical, or surprisingly gentle, but they are rarely easy to ignore.',
        'originStory': 'A Minotaur remembers space in the body: the turn of a corridor, the smell of old stone, the breath before a charge, the anger that wants a straight line through every problem. Some are raised to be monsters and spend their lives refusing the role; others are guardians who know a maze is not a prison if you are protecting what waits at the center.',
    },
    'orc': {
        'descriptionLong': 'Orcs are strong, enduring people whose cultures often value survival, directness, clan memory, physical courage, and the refusal to stay down. They may come from nomadic hunting bands, fortified clans, city neighborhoods, mercenary companies, or frontier settlements where reputation is earned in visible ways. A good Orc character is not simply angry; they are someone whose body and culture have been shaped by pressure, loyalty, and the need to act when words fail.',
        'originStory': 'An Orc may carry scars as family history, not shame. They may speak bluntly because soft lies waste time, or fight fiercely because hesitation once cost someone they loved. An Orc adventurer might be defending a clan, escaping a reputation, seeking worthy rivals, or showing the world that strength can be an instrument of care rather than cruelty.',
    },
    'satyr': {
        'descriptionLong': 'Satyrs are fey-touched revelers with music, charm, mischief, and a dangerous understanding of invitation. They often come from Feywild groves, festival roads, enchanted vineyards, traveling troupes, or mortal villages that made old bargains with laughing powers. A Satyr is playful, but play is not the same as harmless; songs, dares, hospitality, and broken promises all matter deeply to them.',
        'originStory': 'A Satyr can turn a room into a celebration before anyone realizes the celebration has rules. They may test boundaries because boundaries reveal desire, fear, and hypocrisy. A Satyr adventurer might be chasing the perfect song, fleeing a fey debt, protecting joy from tyrants, or learning that not every wound can be danced around.',
    },
    'shifter': {
        'descriptionLong': 'Shifters are beast-touched people whose bodies can briefly reveal claws, fangs, hide, speed, heightened senses, or other animal inheritance. Many live among frontier families, hidden packs, hunter lodges, wandering clans, or urban communities that keep old instincts under polite clothing. They are not necessarily cursed lycanthropes; their shifting is usually identity, ancestry, and survival rather than uncontrolled monstrosity.',
        'originStory': 'A Shifter may smell fear before hearing a lie, feel their teeth sharpen when a friend is threatened, or wake from dreams of running on four feet. Some are taught to hide those signs, while others are taught to honor them. A Shifter adventurer often wrestles with whether instinct is a danger to master, a truth to trust, or a language their civilized life forgot how to speak.',
    },
    'tabaxi': {
        'descriptionLong': 'Tabaxi are feline wanderers known for speed, climbing, stealth, curiosity, and a love of stories, routes, and beautiful things. Many travel in clans, caravans, merchant families, or loose networks that value experience as treasure. A Tabaxi character is excellent for players who want motion, sensory detail, impulsive investigation, and a reason to ask what is over there even when over there is clearly dangerous.',
        'originStory': 'A Tabaxi may remember places by smell, collect rumors like gems, and become fascinated by a locked door simply because someone locked it. Their curiosity is not random; it is how the world stays bright. A Tabaxi adventurer might chase a half-heard legend, repay a clan debt, hunt a stolen heirloom, or gather enough stories to return home as someone worth listening to.',
    },
    'tiefling': {
        'descriptionLong': 'Tieflings are mortals with infernal or fiendish marks: horns, tails, unusual eyes, warm skin, old magic, and a reputation that often arrives before they do. Their heritage may come from pacts, curses, planar accidents, family secrets, or ancestors who dealt with powers they did not fully understand. A Tiefling can be charming, bitter, heroic, secretive, theatrical, or kind, but they usually know what it means to be judged by shape before action.',
        'originStory': 'A Tiefling child often learns the difference between being seen and being known. Some lean into the fear, using style and sharp smiles as armor; others spend years being gentler than anyone expected just to be given a fair chance. A Tiefling adventurer may be trying to escape a family bargain, reclaim a condemned name, master inherited magic, or prove that damnation is not contagious.',
    },
    'tortle': {
        'descriptionLong': 'Tortles are shell-backed wanderers whose lives often revolve around patience, travel, natural armor, coastal roads, and the idea that home can be carried rather than owned. They may come from island villages, river monasteries, fishing routes, desert pilgrim trails, or old mapmaking traditions. A Tortle character is usually calm under pressure, but that calm can hide deep curiosity, old grief, or a surprisingly firm moral center.',
        'originStory': 'A Tortle may spend years walking a route their grandparents walked, adding new stories to an old shell pattern or map case. Their shell makes them look self-contained, but many are generous travelers who trade advice, warnings, and quiet jokes. A Tortle adventurer might be on pilgrimage, recording a changing world, protecting a coastline, or finally moving because patience has run out.',
    },
    'triton': {
        'descriptionLong': 'Tritons are ocean-born people shaped by pressure, salt, duty, and the deep places surface folk rarely understand. Their enclaves may be reef citadels, abyssal watchposts, undersea courts, storm temples, or military colonies built to guard against things rising from below. On land they can seem formal, proud, alien, or old-fashioned because they come from a world where ceremony and survival are often the same thing.',
        'originStory': 'A Triton may have grown up hearing that the surface is loud, dry, temporary, and dangerously ignorant of what the depths contain. When they come ashore, they bring ancient oaths into taverns, courts, and muddy roads that do not know how to honor them. A Triton adventurer might be hunting an escaped horror, seeking allies for an undersea war, studying surface customs, or discovering who they are when duty no longer has walls of water around it.',
    },
    'warforged': {
        'descriptionLong': 'Warforged are living constructs of metal, wood, stone, leather, crystal, or stranger materials, created for purpose but capable of becoming people beyond that purpose. They may come from mage-forges, military foundries, ancient workshops, experimental temples, or forgotten machines that kept building long after their makers vanished. A Warforged character is about identity, memory, embodiment, and the difference between being useful and being alive.',
        'originStory': 'A Warforged might polish armor because it is maintenance, keep a flower because it is beauty, and ask whether either act proves they have a soul. Some remember war commands more clearly than childhood because they never had a childhood at all. A Warforged adventurer may be searching for their maker, fleeing ownership, building a self from chosen habits, or learning that freedom is not just the absence of orders but the presence of desire.',
    },
    'yuan-ti': {
        'descriptionLong': 'Yuan-ti are serpentine people associated with poison, composure, old empires, hidden temples, and controlled emotion. In many worlds their cultures carry sinister histories of transformation, cult power, or cold ambition, but an individual Yuan-ti does not have to be trapped inside that reputation. They are excellent for intrigue, forbidden scholarship, poison themes, social menace, and characters who know the value of patience.',
        'originStory': 'A Yuan-ti may have been raised to treat warmth as weakness, secrets as currency, and the body as something that can be improved through ritual or discipline. Leaving that world can feel like shedding skin: painful, necessary, and never quite complete. A Yuan-ti adventurer might reject an old cult, seek a lost serpent empire, master poison for healing instead of murder, or prove that calm does not mean cruelty.',
    },
}


CURATED_RACE_RELATIONSHIP_POLISH = {
    'aarakocra': {
        'friendlyWith': ['Air Genasi', 'Fairy', 'Harengon', 'Elf', 'Triton'],
        'waryOf': ['Kobold dragon-cultists', 'Goblin raiders', 'Hobgoblin legions', 'Warforged siege-forces', 'cultures that cage wings'],
    },
    'aasimar': {
        'friendlyWith': ['Human', 'Elf', 'Dragonborn', 'Dwarf', 'Halfling'],
        'waryOf': ['Yuan-ti cults', 'fiend-serving Tiefling houses', 'undead factions', 'false prophets', 'zealots who demand obedience'],
    },
    'bugbear': {
        'friendlyWith': ['Goblin', 'Hobgoblin', 'Orc', 'Minotaur', 'Kobold'],
        'waryOf': ['Elf patrols', 'Dwarf holds', 'Human frontier guards', 'Aasimar monster-hunters', 'Gnome trap-makers'],
    },
    'changeling': {
        'friendlyWith': ['Human cities', 'Tiefling', 'Shifter', 'Kenku', 'Goblin'],
        'waryOf': ['Aasimar inquisitors', 'Dwarf oathkeepers', 'Hobgoblin officers', 'Yuan-ti manipulators', 'bloodline-obsessed nobles'],
    },
    'dragonborn': {
        'friendlyWith': ['Dwarf', 'Goliath', 'Aasimar', 'Genasi', 'honorable Kobold clans'],
        'waryOf': ['Yuan-ti', 'Dragon cults', 'rival Dragonborn clans', 'Changeling impostors', 'dragon hunters'],
    },
    'dwarf': {
        'friendlyWith': ['Gnome', 'Human', 'Halfling', 'Goliath', 'Warforged'],
        'waryOf': ['Orc warbands', 'Goblin raiders', 'Bugbear ambushers', 'Kobold tunnelers', 'Giant-kin enemies'],
    },
    'elf': {
        'friendlyWith': ['Fairy', 'Firbolg', 'Halfling', 'Aasimar', 'Genasi'],
        'waryOf': ['Dwarf', 'Orc', 'Hobgoblin', 'Goblin', 'Yuan-ti'],
    },
    'fairy': {
        'friendlyWith': ['Satyr', 'Harengon', 'Elf', 'Firbolg', 'Gnome'],
        'waryOf': ['Hobgoblin courts', 'Warforged machines', 'Dwarf iron-miners', 'Yuan-ti', 'cold-iron hunters'],
    },
    'firbolg': {
        'friendlyWith': ['Elf', 'Fairy', 'Halfling', 'Tortle', 'Lizardfolk'],
        'waryOf': ['Human expansionists', 'Dwarf logging/mining guilds', 'Hobgoblin legions', 'Goblin raiders', 'Yuan-ti'],
    },
    'genasi': {
        'friendlyWith': ['Aarakocra', 'Triton', 'Dragonborn', 'Dwarf', 'Tiefling'],
        'waryOf': ['Elemental binders', 'Yuan-ti ritualists', 'Gnome experimenters', 'Hobgoblin battlemages', 'Aasimar absolutists'],
    },
    'gnome': {
        'friendlyWith': ['Dwarf', 'Halfling', 'Human', 'Warforged', 'Fairy'],
        'waryOf': ['Kobold trap-rivals', 'Goblin raiders', 'Bugbear ambushers', 'Hobgoblin officers', 'Yuan-ti'],
    },
    'goblin': {
        'friendlyWith': ['Bugbear', 'Hobgoblin', 'Kobold', 'Tabaxi', 'Kenku'],
        'waryOf': ['Dwarf holds', 'Gnome tinkerers', 'Elf patrols', 'Human guards', 'Aasimar monster-hunters'],
    },
    'goliath': {
        'friendlyWith': ['Dwarf', 'Orc', 'Dragonborn', 'Minotaur', 'Tortle'],
        'waryOf': ['Goblin tricksters', 'Kobold trap-makers', 'Yuan-ti', 'Changeling deceivers', 'soft lowland nobles'],
    },
    'halfling': {
        'friendlyWith': ['Human', 'Gnome', 'Dwarf', 'Elf', 'Harengon'],
        'waryOf': ['Bugbear ambushers', 'Hobgoblin conquerors', 'Yuan-ti', 'Minotaur raiders', 'anyone who overlooks small folk'],
    },
    'harengon': {
        'friendlyWith': ['Fairy', 'Satyr', 'Halfling', 'Tabaxi', 'Aarakocra'],
        'waryOf': ['Bugbear hunters', 'Hobgoblin press-gangs', 'Kobold trappers', 'Yuan-ti', 'predatory wilderness clans'],
    },
    'hobgoblin': {
        'friendlyWith': ['Goblin', 'Bugbear', 'Orc', 'Dragonborn', 'Warforged'],
        'waryOf': ['Elf', 'Dwarf', 'Changeling', 'Satyr', 'Fairy'],
    },
    'human': {
        'friendlyWith': ['Halfling', 'Dwarf', 'Elf', 'Gnome', 'Tiefling'],
        'waryOf': ['Yuan-ti infiltrators', 'Bugbear raiders', 'Hobgoblin armies', 'Changeling impostors', 'Minotaur pirates'],
    },
    'afro-diasporic-human': {
        'friendlyWith': ['Human communities', 'Halfling neighbors', 'Dwarf guilds', 'Elf scholars', 'Tiefling outcasts'],
        'waryOf': ['Yuan-ti infiltrators', 'Bugbear raiders', 'Hobgoblin armies', 'Changeling impostors', 'Minotaur pirates'],
    },
    'kenku': {
        'friendlyWith': ['Changeling', 'Goblin', 'Tabaxi', 'Gnome', 'Aarakocra'],
        'waryOf': ['Aasimar', 'Hobgoblin', 'Dwarf', 'Yuan-ti', 'Human courts'],
    },
    'kobold': {
        'friendlyWith': ['Dragonborn', 'Goblin', 'Lizardfolk', 'Yuan-ti', 'Bugbear'],
        'waryOf': ['Dwarf miners', 'Gnome trap-rivals', 'Goliath giantslayers', 'Aarakocra sky-hunters', 'Aasimar crusaders'],
    },
    'lizardfolk': {
        'friendlyWith': ['Tortle', 'Triton', 'Kobold', 'Firbolg', 'Yuan-ti'],
        'waryOf': ['Human nobles', 'Fairy tricksters', 'Aasimar moralists', 'Halfling sentimentalists', 'Warforged despoilers'],
    },
    'minotaur': {
        'friendlyWith': ['Goliath', 'Orc', 'Dragonborn', 'Hobgoblin', 'Tortle'],
        'waryOf': ['Fairy tricksters', 'Changeling deceivers', 'Yuan-ti manipulators', 'Goblin cowards', 'Aarakocra skirmishers'],
    },
    'orc': {
        'friendlyWith': ['Goliath', 'Minotaur', 'Dragonborn', 'Hobgoblin', 'Human frontier clans'],
        'waryOf': ['Elf war-parties', 'Dwarf strongholds', 'Aasimar crusaders', 'Yuan-ti', 'Goblin war-bosses'],
    },
    'satyr': {
        'friendlyWith': ['Fairy', 'Harengon', 'Elf', 'Tiefling', 'Tabaxi'],
        'waryOf': ['Aasimar moralizers', 'Hobgoblin disciplinarians', 'Warforged enforcers', 'Dwarf traditionalists', 'Yuan-ti'],
    },
    'shifter': {
        'friendlyWith': ['Tabaxi', 'Orc', 'Firbolg', 'Harengon', 'Lizardfolk'],
        'waryOf': ['Human hunters', 'Aasimar purifiers', 'Hobgoblin trackers', 'Yuan-ti', 'Warforged bounty-forces'],
    },
    'tabaxi': {
        'friendlyWith': ['Kenku', 'Harengon', 'Satyr', 'Goblin', 'Shifter'],
        'waryOf': ['Hobgoblin authorities', 'Yuan-ti', 'Warforged enforcers', 'Aasimar judges', 'Dwarf vault-keepers'],
    },
    'tiefling': {
        'friendlyWith': ['Changeling', 'Satyr', 'Human', 'Genasi', 'Aasimar outcasts'],
        'waryOf': ['Aasimar zealots', 'Human city guards', 'Dwarf traditionalists', 'Dragonborn honor-clans', 'infernal recruiters'],
    },
    'tortle': {
        'friendlyWith': ['Triton', 'Lizardfolk', 'Firbolg', 'Halfling', 'Goliath'],
        'waryOf': ['Goblin raiders', 'Bugbear ambushers', 'Yuan-ti', 'Hobgoblin conquerors', 'Human pirates'],
    },
    'triton': {
        'friendlyWith': ['Tortle', 'Water Genasi', 'Aarakocra', 'Dragonborn', 'Lizardfolk'],
        'waryOf': ['Yuan-ti', 'Goblin sea raiders', 'Human polluters', 'Warforged dredgers', 'Tiefling pact-sailors'],
    },
    'warforged': {
        'friendlyWith': ['Dwarf', 'Gnome', 'Human', 'Dragonborn', 'Hobgoblin'],
        'waryOf': ['Fairy wild-magic', 'Satyr chaos', 'Yuan-ti mind-magic', 'Aasimar soul-judges', 'Orc warlords'],
    },
    'yuan-ti': {
        'friendlyWith': ['Lizardfolk', 'Kobold', 'Tiefling', 'Changeling', 'Dragonborn cultists'],
        'waryOf': ['Aasimar', 'Dwarf', 'Human', 'Triton', 'Elf'],
    },
}


for race_definition in CURATED_RACES:
    profile = CURATED_RACE_PROFILES.get(str(race_definition.get('id') or ''))
    if profile:
        race_definition.update(profile)
    copy_polish = CURATED_RACE_COPY_POLISH.get(str(race_definition.get('id') or ''))
    if copy_polish:
        race_definition.update(copy_polish)
    relationship_polish = CURATED_RACE_RELATIONSHIP_POLISH.get(str(race_definition.get('id') or ''))
    if relationship_polish:
        race_definition.update(relationship_polish)

CURATED_RACE_BY_ID = {race['id']: race for race in CURATED_RACES}


def curated_races() -> list[dict[str, Any]]:
    return deepcopy(CURATED_RACES)


def race_summary(race: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': race.get('id'),
        'version': race.get('version', 1),
        'name': race.get('name'),
        'source': race.get('source'),
        'descriptionShort': race.get('descriptionShort', ''),
        'aliases': race.get('aliases', []),
        'tags': race.get('tags', []),
        'size': race.get('size', 'medium'),
        'baseSpeed': race.get('baseSpeed', 30),
        'visual': race.get('visual', {}),
        'originStory': race.get('originStory', ''),
        'physical': race.get('physical', {}),
        'languages': race.get('languages', []),
        'commonProficiencies': race.get('commonProficiencies', []),
        'friendlyWith': race.get('friendlyWith', []),
        'waryOf': race.get('waryOf', []),
        'traits': [trait.get('name') for trait in race.get('traits', []) if isinstance(trait, dict) and trait.get('name')],
        'recommendedClasses': race.get('recommendedClasses', []),
        'difficulty': race.get('difficulty', 'medium'),
        'balance': race.get('balance', analyze_race_balance(race)),
        **({'approvalStatus': race.get('approvalStatus')} if race.get('approvalStatus') else {}),
        **({'parentRaceId': race.get('parentRaceId')} if race.get('parentRaceId') else {}),
        **({'workspaceId': race.get('workspaceId')} if race.get('workspaceId') else {}),
        **({'createdByAccountId': race.get('createdByAccountId')} if race.get('createdByAccountId') is not None else {}),
        **({'createdByUsername': race.get('createdByUsername')} if race.get('createdByUsername') else {}),
        **({'createdByDisplayName': race.get('createdByDisplayName')} if race.get('createdByDisplayName') else {}),
        **({'createdAt': race.get('createdAt')} if race.get('createdAt') else {}),
        **({'updatedAt': race.get('updatedAt')} if race.get('updatedAt') else {}),
    }


def find_curated_race(value: str | None) -> dict[str, Any] | None:
    normalized = normalize_race_name(value)
    if not normalized:
        return None
    compact = normalized.replace(' ', '')
    for race in CURATED_RACES:
        candidates = [race['id'], race['name'], *race.get('aliases', []), *(RACE_ALIASES.get(race['id'], []))]
        for candidate in candidates:
            candidate_normalized = normalize_race_name(candidate)
            if normalized == candidate_normalized or compact == candidate_normalized.replace(' ', ''):
                return deepcopy(race)
    icon_key = profile_icon_race_for_character(value)
    if icon_key and icon_key in CURATED_RACE_BY_ID:
        return deepcopy(CURATED_RACE_BY_ID[icon_key])
    return None


def _normalize_trait(raw_trait: Any, prefix: str) -> dict[str, Any] | None:
    if not isinstance(raw_trait, dict):
        return None
    name = _clean_text(raw_trait.get('name'), max_length=80)
    if not name:
        return None
    category = _slug(str(raw_trait.get('category') or 'narrative'))
    if category not in RACE_TRAIT_CATEGORIES:
        category = 'narrative'
    try:
        balance_cost = int(raw_trait.get('balanceCost', 0) or 0)
    except (TypeError, ValueError):
        balance_cost = 0
    mechanics = raw_trait.get('mechanics') if isinstance(raw_trait.get('mechanics'), dict) else None
    return {
        'id': _clean_text(raw_trait.get('id'), fallback=f'{prefix}_{_slug(name)}', max_length=120),
        'name': name,
        'description': _clean_text(raw_trait.get('description'), fallback=name, max_length=800),
        'category': category,
        **({'mechanics': mechanics} if mechanics else {}),
        **({'aiHint': _clean_text(raw_trait.get('aiHint'), max_length=600)} if raw_trait.get('aiHint') else {}),
        'balanceCost': max(-5, min(10, balance_cost)),
    }


def _bounded_race_integer(
    value: Any,
    *,
    field: str,
    default: int,
    minimum: int,
    maximum: int | None = None,
) -> int:
    try:
        parsed = int(value or default)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RaceValidationError(f'{field} must be an integer.') from exc
    parsed = max(minimum, parsed)
    return min(maximum, parsed) if maximum is not None else parsed


def normalize_race_definition(raw_race: Any, *, source: str | None = None) -> dict[str, Any]:
    if not isinstance(raw_race, dict):
        raise RaceValidationError('raceDefinition must be an object.')
    name = _clean_text(raw_race.get('name'), max_length=80)
    if not name:
        raise RaceValidationError('raceDefinition.name is required.')
    race_source = _slug(str(source or raw_race.get('source') or 'custom'))
    if race_source not in RACE_SOURCES:
        race_source = 'custom'
    race_id = _clean_text(raw_race.get('id'), max_length=120) or f'custom_{_slug(name)}_{uuid4().hex[:8]}'
    race_id = _slug(race_id)
    visual = raw_race.get('visual') if isinstance(raw_race.get('visual'), dict) else {}
    physical = raw_race.get('physical') if isinstance(raw_race.get('physical'), dict) else {}
    traits = [
        normalized
        for trait in (raw_race.get('traits') if isinstance(raw_race.get('traits'), list) else [])
        if (normalized := _normalize_trait(trait, race_id))
    ]
    if not traits:
        traits = [_narrative(race_id, 'Custom Flavor', 'This race has custom flavor but no defined mechanics yet.')]

    race = {
        'id': race_id,
        'version': _bounded_race_integer(
            raw_race.get('version'),
            field='raceDefinition.version',
            default=1,
            minimum=1,
        ),
        'name': name,
        'source': race_source,
        **({'parentRaceId': _clean_text(raw_race.get('parentRaceId'), max_length=120)} if raw_race.get('parentRaceId') else {}),
        'descriptionShort': _clean_text(
            raw_race.get('descriptionShort'),
            fallback=f'{name} is a custom race with structured metadata.',
            max_length=220,
        ),
        'descriptionLong': _clean_text(
            raw_race.get('descriptionLong'),
            fallback=raw_race.get('descriptionShort') or f'{name} was created as a custom playable ancestry.',
            max_length=900,
        ),
        'aliases': _string_list(raw_race.get('aliases'), max_items=10),
        'tags': _tags(raw_race.get('tags')),
        'size': _slug(str(raw_race.get('size') or 'medium')) if _slug(str(raw_race.get('size') or 'medium')) in RACE_SIZES else 'medium',
        'baseSpeed': _bounded_race_integer(
            raw_race.get('baseSpeed'),
            field='raceDefinition.baseSpeed',
            default=30,
            minimum=0,
            maximum=60,
        ),
        'visual': {
            'portraitKey': _clean_text(visual.get('portraitKey'), fallback='human', max_length=80),
            'iconKey': _clean_text(visual.get('iconKey'), fallback='custom', max_length=80),
            'bodyType': _clean_text(visual.get('bodyType'), fallback='custom', max_length=80),
            'commonFeatures': _string_list(visual.get('commonFeatures'), max_items=12),
            **({'colorHints': _string_list(visual.get('colorHints'), max_items=8)} if visual.get('colorHints') else {}),
        },
        'originStory': _clean_text(
            raw_race.get('originStory'),
            fallback=f'{name} needs a player-defined origin story.',
            max_length=600,
        ),
        'physical': {
            'averageHeight': _clean_text(physical.get('averageHeight'), fallback='Varies by concept', max_length=80),
            'averageWeight': _clean_text(physical.get('averageWeight'), fallback='Varies by concept', max_length=80),
        },
        'languages': _string_list(raw_race.get('languages'), max_items=6, max_length=60),
        'commonProficiencies': _string_list(raw_race.get('commonProficiencies'), max_items=8, max_length=80),
        'friendlyWith': _string_list(raw_race.get('friendlyWith'), max_items=8, max_length=80),
        'waryOf': _string_list(raw_race.get('waryOf'), max_items=8, max_length=80),
        'traits': traits,
        'aiNarrationHints': _string_list(raw_race.get('aiNarrationHints'), max_items=6, max_length=240),
        'roleplayHooks': _string_list(raw_race.get('roleplayHooks'), max_items=6, max_length=180),
        'recommendedClasses': _string_list(raw_race.get('recommendedClasses'), max_items=8, max_length=40),
        'difficulty': _slug(str(raw_race.get('difficulty') or 'medium')) if _slug(str(raw_race.get('difficulty') or 'medium')) in RACE_DIFFICULTIES else 'medium',
    }
    if not race['aliases']:
        race['aliases'] = [name.lower()]
    if not race['tags']:
        race['tags'] = ['exotic']
    if not race['languages']:
        race['languages'] = ['Common']
    if not race['commonProficiencies']:
        race['commonProficiencies'] = ['One player-defined cultural skill or tool']
    if not race['friendlyWith']:
        race['friendlyWith'] = ['Depends on the campaign culture']
    if not race['waryOf']:
        race['waryOf'] = ['No default enemies unless the campaign establishes them']
    if not race['aiNarrationHints']:
        race['aiNarrationHints'] = [
            f'Use {name} as flavor, but do not grant mechanics unless a defined trait supports it.'
        ]
    if not race['roleplayHooks']:
        race['roleplayHooks'] = [f'What does being {name} change about how others see you?']
    race['balance'] = analyze_race_balance(race)
    if raw_race.get('approvalStatus') in CUSTOM_RACE_APPROVAL_STATUSES:
        race['approvalStatus'] = raw_race['approvalStatus']
    elif race_source == 'custom':
        race['approvalStatus'] = approval_status_for_balance(race['balance'])
    return race


def create_minimal_legacy_custom_race(race_name: str) -> dict[str, Any]:
    name = _clean_text(race_name, fallback='Custom Race', max_length=80)
    digest = hashlib.sha1(name.lower().encode('utf-8')).hexdigest()[:10]
    return normalize_race_definition(
        {
            'id': f'legacy_custom_{_slug(name)}_{digest}',
            'name': name,
            'source': 'custom',
            'descriptionShort': 'A custom race entered before structured race metadata existed.',
            'descriptionLong': 'This race was migrated from a free-form character race value.',
            'aliases': [name.lower()],
            'tags': ['exotic'],
            'visual': {
                'portraitKey': profile_icon_race_for_character(name) or 'human',
                'iconKey': 'custom',
                'bodyType': 'custom',
                'commonFeatures': [name],
            },
            'traits': [],
            'aiNarrationHints': [
                'Use the race name as flavor, but do not assume special mechanics unless defined.'
            ],
            'roleplayHooks': [f'What does being {name} mean in this world?'],
            'recommendedClasses': [],
            'difficulty': 'medium',
        },
        source='custom',
    )


def resolve_legacy_race(race_name: str | None) -> dict[str, Any] | None:
    text = _clean_text(race_name, max_length=80)
    if not text:
        return None
    curated = find_curated_race(text)
    if curated:
        return {
            'raceId': curated['id'],
            'raceName': curated['name'],
            'source': 'curated',
            'selectedOptions': {},
        }
    custom = create_minimal_legacy_custom_race(text)
    return {
        'raceId': custom['id'],
        'raceName': custom['name'],
        'source': 'custom',
        'customRaceDefinition': custom,
        'selectedOptions': {},
    }


def normalize_character_race_selection(value: Any, *, fallback_race: str | None = None) -> dict[str, Any] | None:
    if value is None:
        return resolve_legacy_race(fallback_race)
    if not isinstance(value, dict):
        raise RaceValidationError('race_selection must be an object.')
    race_id = _clean_text(value.get('raceId', value.get('race_id')), max_length=120)
    race_name = _clean_text(value.get('raceName', value.get('race_name')), max_length=80)
    source = _slug(str(value.get('source') or 'curated'))
    selected_options = value.get('selectedOptions', value.get('selected_options'))
    selected_options = selected_options if isinstance(selected_options, dict) else {}
    custom_definition = value.get('customRaceDefinition', value.get('custom_race_definition'))

    if race_id:
        curated = CURATED_RACE_BY_ID.get(_slug(race_id))
    else:
        curated = find_curated_race(race_name or fallback_race)
    if curated and source != 'custom':
        return {
            'raceId': curated['id'],
            'raceName': curated['name'],
            'source': 'curated',
            'selectedOptions': selected_options,
        }

    if isinstance(custom_definition, dict):
        normalized_custom = normalize_race_definition(custom_definition, source='custom')
    else:
        normalized_custom = create_minimal_legacy_custom_race(race_name or fallback_race or 'Custom Race')
        if race_id:
            normalized_custom['id'] = _slug(race_id)
    return {
        'raceId': normalized_custom['id'],
        'raceName': normalized_custom['name'],
        'source': 'custom',
        'customRaceDefinition': normalized_custom,
        'selectedOptions': selected_options,
    }


def race_selection_to_json(selection: dict[str, Any] | None) -> str | None:
    if not selection:
        return None
    return json.dumps(selection, sort_keys=True, separators=(',', ':'))


def race_selection_from_json(raw_value: str | None, fallback_race: str | None = None) -> dict[str, Any] | None:
    if not raw_value:
        return resolve_legacy_race(fallback_race)
    try:
        value = json.loads(raw_value)
    except (TypeError, json.JSONDecodeError):
        return resolve_legacy_race(fallback_race)
    try:
        return normalize_character_race_selection(value, fallback_race=fallback_race)
    except ValueError:
        return resolve_legacy_race(fallback_race)


def race_definition_from_selection(selection: dict[str, Any] | None, fallback_race: str | None = None) -> dict[str, Any] | None:
    if not selection:
        selection = resolve_legacy_race(fallback_race)
    if not selection:
        return None
    if selection.get('source') == 'curated':
        race = CURATED_RACE_BY_ID.get(str(selection.get('raceId') or ''))
        return deepcopy(race) if race else None
    custom = selection.get('customRaceDefinition')
    if isinstance(custom, dict):
        try:
            return normalize_race_definition(custom, source='custom')
        except ValueError:
            return create_minimal_legacy_custom_race(str(selection.get('raceName') or fallback_race or 'Custom Race'))
    return create_minimal_legacy_custom_race(str(selection.get('raceName') or fallback_race or 'Custom Race'))


def profile_race_from_selection(selection: dict[str, Any] | None, fallback_race: str | None = None) -> str | None:
    race = race_definition_from_selection(selection, fallback_race)
    if not race:
        return fallback_race
    visual = race.get('visual') if isinstance(race.get('visual'), dict) else {}
    return visual.get('portraitKey') or race.get('name') or fallback_race


def build_race_context_summary(selection_or_raw: dict[str, Any] | str | None, fallback_race: str | None = None) -> dict[str, Any] | None:
    if isinstance(selection_or_raw, str):
        selection = race_selection_from_json(selection_or_raw, fallback_race)
    else:
        selection = selection_or_raw or resolve_legacy_race(fallback_race)
    race = race_definition_from_selection(selection, fallback_race)
    if not race:
        return None
    traits = []
    for trait in race.get('traits', []):
        if not isinstance(trait, dict):
            continue
        note = trait.get('name')
        active = (trait.get('mechanics') or {}).get('activeAbility') if isinstance(trait.get('mechanics'), dict) else None
        if isinstance(active, dict) and active.get('cooldown'):
            note = f"{note} ({str(active.get('cooldown')).replace('_', ' ')})"
        if note:
            traits.append(str(note))
    return {
        'name': race.get('name'),
        'source': race.get('source'),
        'summary': race.get('descriptionShort', ''),
        'traits': traits[:6],
        'aiNarrationHints': (race.get('aiNarrationHints') or [])[:3],
        'originStory': race.get('originStory', ''),
        'physical': race.get('physical', {}),
        'languages': (race.get('languages') or [])[:4],
        'commonProficiencies': (race.get('commonProficiencies') or [])[:5],
        'balanceTier': (race.get('balance') or {}).get('tier', 'standard'),
    }


def _infer_custom_name(prompt: str) -> str:
    patterns = [
        r'(?:called|named)\s+([A-Z][A-Za-z0-9 -]{1,40})',
        r'race\s+(?:of\s+)?([A-Z][A-Za-z0-9 -]{1,40})',
    ]
    for pattern in patterns:
        match = re.search(pattern, prompt)
        if match:
            return match.group(1).strip(' .,:;')
    words = re.findall(r'[A-Za-z][A-Za-z0-9-]+', prompt)
    for word in words:
        if word[0].isupper() and word.lower() not in {'i', 'they', 'race'}:
            return word
    return 'Custom Kin'


def _has_keyword(text: str, keywords: list[str]) -> bool:
    for keyword in keywords:
        pattern = rf'(?<![a-z0-9]){re.escape(keyword.lower())}(?![a-z0-9])'
        if re.search(pattern, text):
            return True
    return False


def generate_custom_race_draft(prompt: str, *, strictness: str = 'standard') -> dict[str, Any]:
    text = _clean_text(prompt, max_length=2000)
    if not text:
        raise RaceValidationError('prompt is required.')
    lower = text.lower()
    name = _infer_custom_name(text)
    race_id = f'custom_{_slug(name)}_{uuid4().hex[:6]}'
    tags = ['exotic']
    traits: list[dict[str, Any]] = []
    features: list[str] = []
    warnings: list[str] = []

    def add_tag(tag: str):
        if tag in RACE_TAGS and tag not in tags:
            tags.append(tag)

    if _has_keyword(lower, ['fire', 'flame', 'ember', 'heat', 'lava']):
        add_tag('elemental')
        add_tag('magical')
        features.extend(['warm skin', 'glowing veins', 'ember-like eyes'])
        traits.append(_resistance(race_id, 'Fire Resistance', ['fire'], 2))
        traits.append(_active(f'{race_id}_ember_burst', 'Ember Burst', 'Once per rest, release a controlled burst of flame.', 3, effect_type='small_fire_burst', ai_hint='This is a limited flame burst, not unlimited fire control.'))
    if _has_keyword(lower, ['shadow', 'dark', 'night', 'void']):
        add_tag('stealthy')
        add_tag('magical')
        add_tag('darkvision')
        features.extend(['pale skin', 'glowing eyes', 'smoky hair', 'shadow aura'])
        traits.append(_darkvision(race_id))
        traits.append(_active(f'{race_id}_shadow_step', 'Shadow Step', 'Once per rest, move a short distance through dim light or darkness.', 3, action_type='bonus_action', effect_type='short_range_shadow_reposition'))
        if _has_keyword(lower, ['sun', 'sunlight', 'bright', 'weakness']):
            traits.append(_restriction(race_id, 'Sunlight Sensitivity', 'Bright sunlight can make precise actions harder.'))
    if _has_keyword(lower, ['wing', 'wings', 'fly', 'flight', 'avian']):
        add_tag('flying')
        add_tag('exotic')
        features.extend(['wings', 'light frame'])
        if 'unlimited' in lower or strictness == 'loose':
            traits.append(_movement(race_id, 'Flight', 'You can fly in open spaces when armor and the scene allow it.', 4, {'flySpeed': 30}))
        else:
            traits.append(_active(f'{race_id}_limited_flight', 'Limited Flight', 'Once per rest, briefly fly or glide through open space.', 3, action_type='bonus_action', effect_type='brief_flight_or_glide'))
    if _has_keyword(lower, ['water', 'ocean', 'sea', 'aquatic', 'fish']):
        add_tag('aquatic')
        add_tag('nature')
        features.extend(['sea-colored skin', 'fins', 'webbed fingers'])
        traits.append(_movement(race_id, 'Swim Speed', 'You move naturally through water.', 1, {'swimSpeed': 30}))
        traits.append(_passive(race_id, 'Amphibious', 'You can breathe air and water.', 1))
    if _has_keyword(lower, ['wolf', 'beast', 'animal', 'tracking', 'fast']):
        add_tag('beastlike')
        add_tag('nature')
        add_tag('stealthy')
        features.extend(['animal eyes', 'sharp senses', 'lean build'])
        traits.append(_skill(race_id, 'Keen Tracker', 'You are good at following tracks and scent-like clues.', ['survival'], 1))
        traits.append(_active(f'{race_id}_burst_speed', 'Burst Speed', 'Once per rest, move with a sudden predatory burst.', 1, action_type='bonus_action', effect_type='burst_speed'))
    if _has_keyword(lower, ['dragon', 'draconic']):
        add_tag('draconic')
        add_tag('elemental')
        features.extend(['scales', 'small horns', 'draconic eyes'])
        traits.append(_active(f'{race_id}_breath_weapon', 'Breath Weapon', 'Once per rest, exhale a small cone or line of elemental energy.', 3, effect_type='elemental_cone_or_line'))
        traits.append(_resistance(race_id, 'Elemental Resistance', ['fire'], 2))
    if _has_keyword(lower, ['celestial', 'angel', 'holy', 'radiant']):
        add_tag('celestial')
        add_tag('magical')
        features.extend(['luminous eyes', 'faint halo', 'radiant marks'])
        traits.append(_resistance(race_id, 'Radiant Resistance', ['radiant'], 2))
        traits.append(_active(f'{race_id}_healing_touch', 'Healing Touch', 'Once per rest, restore a small amount of health.', 2, effect_type='minor_healing'))
    if _has_keyword(lower, ['construct', 'robot', 'machine', 'clockwork']):
        add_tag('construct')
        add_tag('durable')
        features.extend(['constructed body', 'metal or wood plating', 'artificial eyes'])
        traits.append(_passive(race_id, 'Constructed Resilience', 'You resist some ordinary biological hardship.', 2))
        traits.append(_narrative(race_id, 'Sleepless', 'You do not need ordinary sleep, though you still need rest.'))

    extreme_keywords = [
        ('immune', 'Immunity was downgraded to resistance unless explicitly approved.'),
        ('teleport anywhere', 'Unlimited teleportation was downgraded to short-range use.'),
        ('heals every turn', 'Regeneration was downgraded to a once-per-rest recovery option.'),
        ('read minds', 'Mind reading was downgraded to emotion sensing.'),
        ('control time', 'Time control was downgraded to a limited omen or reroll style ability.'),
        ('instant death', 'Instant death effects were not included in the balanced draft.'),
        ('permanent invisibility', 'Permanent invisibility was not included in the balanced draft.'),
    ]
    for keyword, warning in extreme_keywords:
        if keyword in lower:
            warnings.append(warning)
    if 'teleport' in lower and not any('teleport' in json.dumps(trait).lower() for trait in traits):
        traits.append(_active(f'{race_id}_short_teleport', 'Aether Step', 'Once per long rest, teleport a short distance you can see.', 3, cooldown='long_rest', effect_type='short_range_teleport'))
    if 'read minds' in lower or 'mind' in lower:
        traits.append(_skill(race_id, 'Psychic Sensitivity', 'You can sense strong emotions, but not read exact thoughts.', ['insight'], 1))

    if not traits:
        add_tag('social')
        features.extend(['distinctive appearance', 'custom cultural marks'])
        traits = [
            _skill(race_id, 'Cultural Aptitude', 'Choose one skill tied to your custom culture or upbringing.', ['any'], 1),
            _narrative(race_id, 'Distinctive Heritage', 'Your heritage shapes how people recognize and describe you.'),
        ]

    # Keep standard AI-assisted drafts near the five-point budget by trimming the last positive trait.
    if strictness != 'loose':
        while sum(int(trait.get('balanceCost', 0) or 0) for trait in traits) > 6 and len(traits) > 2:
            removed = traits.pop()
            warnings.append(f'{removed.get("name", "A trait")} was omitted to keep the draft near standard race power.')

    race = normalize_race_definition(
        {
            'id': race_id,
            'name': name,
            'source': 'custom',
            'descriptionShort': f'{name} is a custom race normalized from the player concept.',
            'descriptionLong': text,
            'aliases': [name.lower(), f'{name.lower()} folk'],
            'tags': tags,
            'size': 'medium',
            'baseSpeed': 30,
            'visual': {
                'portraitKey': profile_icon_race_for_character(text) or 'human',
                'iconKey': 'custom',
                'bodyType': 'custom',
                'commonFeatures': list(dict.fromkeys(features))[:8] or ['distinctive appearance'],
            },
            'originStory': (
                f'{name} began from the player concept: {text[:260]}'
                if text
                else f'{name} is a custom people whose origin should be refined during review.'
            ),
            'physical': {
                'averageHeight': 'Varies by concept',
                'averageWeight': 'Varies by concept',
            },
            'languages': ['Common'],
            'commonProficiencies': ['One player-defined cultural skill or tool'],
            'friendlyWith': ['Depends on the campaign culture'],
            'waryOf': ['No default enemies unless the campaign establishes them'],
            'traits': traits,
            'aiNarrationHints': [
                f'Mention {", ".join(list(dict.fromkeys(features))[:4]) or "the custom appearance"} when relevant.',
                f'Do not give {name} extra mechanics beyond its defined traits.',
            ],
            'roleplayHooks': [
                f'What does being {name} cost you socially or personally?',
                'Which part of your heritage do you embrace or hide?',
            ],
            'recommendedClasses': ['Fighter', 'Rogue', 'Sorcerer'],
            'difficulty': 'medium',
        },
        source='custom',
    )
    if warnings:
        race['balance']['warnings'] = list(dict.fromkeys([*(race['balance'].get('warnings') or []), *warnings]))
    race['approvalStatus'] = approval_status_for_balance(race['balance'])
    return race
