from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from aidm_server.canon_text import int_or_default
from aidm_server.game_state.models import stable_slug


CREATURE_SOURCES = {
    'core_bestiary',
    'campaign_pack',
    'region_bestiary',
    'generated',
    'generated_variant',
    'user_custom',
    'evolved',
}
BESTIARY_SCOPES = {'core', 'campaign', 'region', 'session'}
PERSISTENCE_VALUES = {'global', 'campaign', 'region', 'session', 'temporary'}
CREATURE_TYPES = {
    'humanoid',
    'beast',
    'undead',
    'construct',
    'fiend',
    'celestial',
    'fey',
    'elemental',
    'dragon',
    'monstrosity',
    'ooze',
    'plant',
    'aberration',
    'giant',
    'swarm',
    'custom',
}
CHALLENGE_TIERS = {'trivial', 'easy', 'standard', 'hard', 'deadly', 'boss'}
CREATURE_SIZES = {'tiny', 'small', 'medium', 'large', 'huge', 'gargantuan'}
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
ABILITY_TYPES = {'attack', 'spell', 'special', 'reaction', 'passive', 'legendary', 'lair'}
ACTION_COSTS = {'action', 'bonus_action', 'reaction', 'free', 'legendary_action'}
RANGE_BANDS = {'self', 'touch', 'melee', 'near', 'far', 'distant', 'area'}
TARGET_TYPES = {'single', 'multiple', 'area', 'self'}
COOLDOWNS = {'none', 'turn', 'short_rest', 'long_rest', 'recharge_5_6', 'once_per_combat'}
INTELLIGENCE_PROFILES = {'mindless', 'animal', 'low_cunning', 'average', 'trained', 'tactical', 'genius', 'alien'}
COMBAT_ROLES = {
    'brute',
    'skirmisher',
    'sniper',
    'controller',
    'support',
    'leader',
    'assassin',
    'tank',
    'summoner',
    'ambusher',
    'beast',
    'boss',
    'minion',
}
PRIMARY_GOALS = {
    'kill_party',
    'survive',
    'protect_leader',
    'protect_location',
    'delay_party',
    'steal_item',
    'complete_ritual',
    'capture_target',
    'feed',
    'escape',
    'test_party',
    'negotiate',
    'defend_young',
    'unknown',
    'custom',
}
TARGET_PRIORITY_ALIASES = {
    'nearest_target': 'nearest',
    'nearest': 'nearest',
    'highest_damage_dealer': 'highest_damage_dealer',
    'lowest_hp': 'wounded',
    'wounded': 'wounded',
    'lowest_armor': 'lowest_armor',
    'spellcaster': 'spellcaster',
    'healer': 'healer',
    'isolated_target': 'isolated',
    'isolated': 'isolated',
    'restrained_target': 'restrained',
    'restrained': 'restrained',
    'leader': 'leader',
    'last_attacker': 'last_damaged_by',
    'last_damaged_by': 'last_damaged_by',
    'carrying_desired_item': 'carrying_desired_item',
    'blocking_escape': 'blocking_escape',
    'personal_grudge_target': 'personal_grudge_target',
    'random': 'random',
}


def _text(value: Any, default: str = '') -> str:
    text = str(value or '').strip()
    return text or default


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int_or_default(value, default=default)))


def _string_list(value: Any, *, limit: int = 30) -> list[str]:
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, str):
        raw_values = re.split(r'[,;]', value)
    else:
        raw_values = []
    result: list[str] = []
    for item in raw_values:
        text = _text(item)
        if text and text not in result:
            result.append(text[:80])
        if len(result) >= limit:
            break
    return result


def _enum(value: Any, allowed: set[str], default: str) -> str:
    normalized = _text(value, default).lower().replace(' ', '_').replace('-', '_')
    return normalized if normalized in allowed else default


def _ability_score(value: Any, default: int) -> int:
    return _bounded_int(value, default=default, minimum=1, maximum=30)


def ability_modifier(score: int) -> int:
    return (int(score) - 10) // 2


def normalize_stats(value: Any, *, level: int = 1, tier: str = 'standard') -> dict[str, int]:
    stats = value if isinstance(value, dict) else {}
    base_hp = {'trivial': 4, 'easy': 7, 'standard': 10, 'hard': 14, 'deadly': 18, 'boss': 28}.get(tier, 10)
    return {
        'maxHp': _bounded_int(stats.get('maxHp', stats.get('max_hp')), default=max(1, level * base_hp), minimum=1, maximum=999),
        'armorClass': _bounded_int(
            stats.get('armorClass', stats.get('armor_class', stats.get('ac'))),
            default=11 + min(7, level // 2),
            minimum=5,
            maximum=30,
        ),
        'strength': _ability_score(stats.get('strength', stats.get('str')), 10),
        'dexterity': _ability_score(stats.get('dexterity', stats.get('dex')), 10),
        'constitution': _ability_score(stats.get('constitution', stats.get('con')), 10),
        'intelligence': _ability_score(stats.get('intelligence', stats.get('int')), 8),
        'wisdom': _ability_score(stats.get('wisdom', stats.get('wis')), 10),
        'charisma': _ability_score(stats.get('charisma', stats.get('cha')), 8),
    }


def normalize_movement(value: Any) -> dict[str, int]:
    raw = value if isinstance(value, dict) else {}
    movement = {}
    for key in ('walk', 'fly', 'swim', 'climb', 'burrow'):
        amount = int_or_default(raw.get(key), default=0)
        if amount > 0:
            movement[key] = max(5, min(240, amount))
    if not movement:
        movement['walk'] = 30
    return movement


def normalize_senses(value: Any, stats: dict[str, int] | None = None) -> dict[str, int]:
    raw = value if isinstance(value, dict) else {}
    senses = {}
    for key in ('darkvision', 'blindsight', 'tremorsense'):
        amount = int_or_default(raw.get(key), default=0)
        if amount > 0:
            senses[key] = max(5, min(240, amount))
    passive = int_or_default(raw.get('passivePerception', raw.get('passive_perception')), default=10 + ability_modifier((stats or {}).get('wisdom', 10)))
    senses['passivePerception'] = max(1, min(35, passive))
    return senses


def normalize_damage_types(value: Any) -> list[str]:
    return [item for item in _string_list(value, limit=12) if item in DAMAGE_TYPES]


def normalize_survival_rules(value: Any, *, behavior: dict[str, Any]) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    notes = _string_list(value if not isinstance(value, dict) else raw.get('notes'), limit=10)
    intelligence = str(behavior.get('intelligenceProfile') or 'average')
    self_preservation = int_or_default(behavior.get('selfPreservation'), default=50)
    surrender_threshold = int_or_default(behavior.get('surrenderThreshold'), default=15)
    flee_threshold = int_or_default(behavior.get('fleeThreshold'), default=25)
    fight_to_death_default = intelligence == 'mindless' or 'fanatical' in behavior.get('personalityTags', [])
    return {
        'fightToDeath': bool(raw.get('fightToDeath', raw.get('fight_to_death', fight_to_death_default))),
        'fleeBelowHpPercent': _bounded_int(
            raw.get('fleeBelowHpPercent', raw.get('flee_below_hp_percent', flee_threshold)),
            default=flee_threshold,
            minimum=0,
            maximum=100,
        ),
        'surrenderBelowMorale': _bounded_int(
            raw.get('surrenderBelowMorale', raw.get('surrender_below_morale', surrender_threshold)),
            default=surrender_threshold,
            minimum=0,
            maximum=100,
        ),
        'surrenderBelowHpPercent': _bounded_int(
            raw.get('surrenderBelowHpPercent', raw.get('surrender_below_hp_percent', 0)),
            default=0,
            minimum=0,
            maximum=100,
        ),
        'negotiateBelowMorale': _bounded_int(
            raw.get('negotiateBelowMorale', raw.get('negotiate_below_morale', surrender_threshold + 10)),
            default=surrender_threshold + 10,
            minimum=0,
            maximum=100,
        ),
        'negotiateBelowHpPercent': _bounded_int(
            raw.get('negotiateBelowHpPercent', raw.get('negotiate_below_hp_percent', 0)),
            default=0,
            minimum=0,
            maximum=100,
        ),
        'fleeIfLeaderDies': bool(raw.get('fleeIfLeaderDies', raw.get('flee_if_leader_dies', intelligence != 'mindless' and self_preservation >= 35))),
        'fleeIfOutnumbered': bool(raw.get('fleeIfOutnumbered', raw.get('flee_if_outnumbered', intelligence != 'mindless' and self_preservation >= 45))),
        'fleeIfAlone': bool(raw.get('fleeIfAlone', raw.get('flee_if_alone', intelligence != 'mindless' and self_preservation >= 65))),
        'fleeIfMoraleBelow': _bounded_int(
            raw.get('fleeIfMoraleBelow', raw.get('flee_if_morale_below', 0)),
            default=0,
            minimum=0,
            maximum=100,
        ),
        'callForHelpBelowHpPercent': _bounded_int(
            raw.get('callForHelpBelowHpPercent', raw.get('call_for_help_below_hp_percent', 0)),
            default=0,
            minimum=0,
            maximum=100,
        ),
        'protectSelfIfBloodied': bool(raw.get('protectSelfIfBloodied', raw.get('protect_self_if_bloodied', self_preservation >= 55))),
        'ignorePain': bool(raw.get('ignorePain', raw.get('ignore_pain', intelligence == 'mindless'))),
        'mindlessNoRetreat': bool(raw.get('mindlessNoRetreat', raw.get('mindless_no_retreat', intelligence == 'mindless'))),
        'notes': notes,
    }


def normalize_ability(value: Any, *, creature_id: str, index: int) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    name = _text(value.get('name'), f'Ability {index + 1}')
    ability_id = _text(value.get('id')) or stable_slug(f'{creature_id}_{name}')
    ability = {
        'id': ability_id,
        'name': name[:80],
        'type': _enum(value.get('type'), ABILITY_TYPES, 'attack'),
        'description': _text(value.get('description'), name)[:500],
        'actionCost': _enum(value.get('actionCost', value.get('action_cost')), ACTION_COSTS, 'action'),
        'range': _enum(value.get('range'), RANGE_BANDS, 'melee'),
        'targetType': _enum(value.get('targetType', value.get('target_type')), TARGET_TYPES, 'single'),
        'cooldown': _enum(value.get('cooldown'), COOLDOWNS, 'none'),
        'aiUseWhen': _string_list(value.get('aiUseWhen', value.get('ai_use_when')), limit=8),
    }
    attack_bonus = value.get('attackBonus', value.get('attack_bonus'))
    if attack_bonus is not None:
        ability['attackBonus'] = _bounded_int(attack_bonus, default=0, minimum=-5, maximum=20)
    damage = value.get('damage') if isinstance(value.get('damage'), dict) else {}
    dice = _text(damage.get('dice') or value.get('damageDice') or value.get('damage_dice'))
    damage_type = _enum(damage.get('type') or value.get('damageType') or value.get('damage_type'), DAMAGE_TYPES, 'slashing')
    if dice:
        ability['damage'] = {'dice': dice[:40], 'type': damage_type}
    healing = value.get('healing') if isinstance(value.get('healing'), dict) else {}
    healing_dice = _text(healing.get('dice') or value.get('healingDice') or value.get('healing_dice'))
    if healing_dice:
        ability['healing'] = {'dice': healing_dice[:40]}
    save = value.get('save') if isinstance(value.get('save'), dict) else {}
    if save:
        ability['save'] = {
            'ability': _enum(save.get('ability'), {'str', 'dex', 'con', 'int', 'wis', 'cha'}, 'dex'),
            'dc': _bounded_int(save.get('dc'), default=12, minimum=5, maximum=30),
            'effectOnSuccess': _enum(
                save.get('effectOnSuccess', save.get('effect_on_success')),
                {'none', 'half_damage', 'reduced_effect'},
                'half_damage',
            ),
        }
    conditions = _string_list(value.get('conditionsApplied', value.get('conditions_applied')), limit=8)
    if conditions:
        ability['conditionsApplied'] = conditions
    uses_remaining = value.get('usesRemaining', value.get('uses_remaining'))
    if uses_remaining is not None:
        ability['usesRemaining'] = _bounded_int(uses_remaining, default=1, minimum=0, maximum=10)
    return ability


def normalize_target_priority(value: Any) -> list[str]:
    priorities: list[str] = []
    for item in _string_list(value, limit=12):
        normalized = item.strip().lower().replace(' ', '_').replace('-', '_')
        mapped = TARGET_PRIORITY_ALIASES.get(normalized, normalized)
        if mapped and mapped not in priorities:
            priorities.append(mapped)
    return priorities[:8]


def normalize_behavior(value: Any, *, creature_type: str, challenge_tier: str) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    intelligence_default = 'animal' if creature_type == 'beast' else 'mindless' if creature_type in {'undead', 'ooze', 'construct'} else 'average'
    role_default = 'boss' if challenge_tier == 'boss' else 'beast' if creature_type == 'beast' else 'brute'
    primary_goal = raw.get('primaryGoal', raw.get('primary_goal'))
    behavior = {
        'intelligenceProfile': _enum(raw.get('intelligenceProfile', raw.get('intelligence_profile')), INTELLIGENCE_PROFILES, intelligence_default),
        'combatRole': _enum(raw.get('combatRole', raw.get('combat_role')), COMBAT_ROLES, role_default),
        'primaryGoal': _enum(primary_goal, PRIMARY_GOALS, 'survive' if creature_type == 'beast' else 'kill_party'),
        'secondaryGoals': _string_list(raw.get('secondaryGoals', raw.get('secondary_goals')), limit=8),
        'aggression': _bounded_int(raw.get('aggression'), default=55, minimum=0, maximum=100),
        'selfPreservation': _bounded_int(raw.get('selfPreservation', raw.get('self_preservation')), default=50, minimum=0, maximum=100),
        'morale': _bounded_int(raw.get('morale'), default=50, minimum=0, maximum=100),
        'discipline': _bounded_int(raw.get('discipline'), default=50, minimum=0, maximum=100),
        'cruelty': _bounded_int(raw.get('cruelty'), default=20, minimum=0, maximum=100),
        'loyalty': _bounded_int(raw.get('loyalty'), default=40, minimum=0, maximum=100),
        'fleeThreshold': _bounded_int(raw.get('fleeThreshold', raw.get('flee_threshold')), default=25, minimum=0, maximum=100),
        'surrenderThreshold': _bounded_int(raw.get('surrenderThreshold', raw.get('surrender_threshold')), default=15, minimum=0, maximum=100),
        'targetPriority': normalize_target_priority(raw.get('targetPriority', raw.get('target_priority'))),
        'tactics': _string_list(raw.get('tactics'), limit=10),
        'personalityTags': _string_list(raw.get('personalityTags', raw.get('personality_tags')), limit=8),
        'speechStyle': _text(raw.get('speechStyle', raw.get('speech_style')))[:160],
        'specialBehaviorNotes': _string_list(raw.get('specialBehaviorNotes', raw.get('special_behavior_notes')), limit=10),
    }
    if behavior['intelligenceProfile'] == 'mindless':
        behavior['selfPreservation'] = 0
        behavior['fleeThreshold'] = 0
        behavior['surrenderThreshold'] = 0
        behavior['targetPriority'] = behavior['targetPriority'] or ['nearest', 'last_damaged_by']
    if not behavior['targetPriority']:
        behavior['targetPriority'] = ['wounded', 'isolated', 'nearest'] if creature_type == 'beast' else ['lowest_armor', 'spellcaster', 'wounded']
    if not behavior['tactics']:
        behavior['tactics'] = ['Use the strongest available attack against the best target.']
    behavior['survivalRules'] = normalize_survival_rules(raw.get('survivalRules', raw.get('survival_rules')), behavior=behavior)
    if not behavior['survivalRules']['notes'] and behavior['selfPreservation'] > 0:
        behavior['survivalRules']['notes'] = ['Retreat, surrender, or negotiate when morale collapses and escape is possible.']
    return behavior


def normalize_balance(value: Any, *, tier: str, max_hp: int) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    return {
        'estimatedTier': _enum(raw.get('estimatedTier', raw.get('estimated_tier')), {*CHALLENGE_TIERS, 'overpowered'}, tier),
        'targetTier': _enum(raw.get('targetTier', raw.get('target_tier')), CHALLENGE_TIERS, tier),
        'estimatedDamagePerRound': max(0, int_or_default(raw.get('estimatedDamagePerRound', raw.get('estimated_damage_per_round')), default=0)),
        'estimatedDurability': max(1, int_or_default(raw.get('estimatedDurability', raw.get('estimated_durability')), default=max_hp)),
        'estimatedControlStrength': max(0, int_or_default(raw.get('estimatedControlStrength', raw.get('estimated_control_strength')), default=0)),
        'warnings': _string_list(raw.get('warnings'), limit=12),
        'balanceAdjustments': _string_list(raw.get('balanceAdjustments', raw.get('balance_adjustments')), limit=12),
        'reviewed': bool(raw.get('reviewed')),
    }


def normalize_creature_definition(value: Any, *, source: str | None = None) -> dict[str, Any]:
    if not isinstance(value, dict):
        value = {}
    raw = deepcopy(value)
    name = _text(raw.get('name'), 'Unknown Creature')
    creature_id = _text(raw.get('id')) or stable_slug(name)
    challenge_tier = _enum(raw.get('challengeTier', raw.get('challenge_tier')), CHALLENGE_TIERS, 'standard')
    level = _bounded_int(raw.get('level'), default=1, minimum=0, maximum=30)
    creature_type = _enum(raw.get('creatureType', raw.get('creature_type')), CREATURE_TYPES, 'custom')
    stats = normalize_stats(raw.get('stats'), level=max(1, level), tier=challenge_tier)
    abilities = []
    for index, raw_ability in enumerate(raw.get('abilities') if isinstance(raw.get('abilities'), list) else []):
        ability = normalize_ability(raw_ability, creature_id=creature_id, index=index)
        if ability is not None:
            abilities.append(ability)
    if not abilities:
        abilities = [
            normalize_ability(
                {
                    'id': f'{creature_id}_strike',
                    'name': 'Strike',
                    'type': 'attack',
                    'description': f'{name} makes a basic attack.',
                    'damage': {'dice': '1d6', 'type': 'slashing'},
                    'attackBonus': 2 + level // 2,
                },
                creature_id=creature_id,
                index=0,
            )
        ]
    normalized = {
        'id': creature_id,
        'version': _bounded_int(raw.get('version'), default=1, minimum=1, maximum=999),
        'name': name[:100],
        'source': _enum(source or raw.get('source'), CREATURE_SOURCES, 'generated'),
        'descriptionShort': _text(raw.get('descriptionShort', raw.get('description_short')), name)[:240],
        'descriptionLong': _text(raw.get('descriptionLong', raw.get('description_long')), raw.get('descriptionShort') or name)[:2000],
        'creatureType': creature_type,
        'creatureTypeName': _text(raw.get('creatureTypeName', raw.get('creature_type_name')))[:100],
        'npcBinding': deepcopy(raw.get('npcBinding', raw.get('npc_binding'))) if isinstance(raw.get('npcBinding', raw.get('npc_binding')), dict) else {},
        'aliases': _string_list(raw.get('aliases'), limit=12),
        'visualTags': _string_list(raw.get('visualTags', raw.get('visual_tags', raw.get('tags'))), limit=16),
        'level': level,
        'challengeTier': challenge_tier,
        'size': _enum(raw.get('size'), CREATURE_SIZES, 'medium'),
        'stats': stats,
        'movement': normalize_movement(raw.get('movement')),
        'senses': normalize_senses(raw.get('senses'), stats),
        'resistances': normalize_damage_types(raw.get('resistances')),
        'vulnerabilities': normalize_damage_types(raw.get('vulnerabilities')),
        'immunities': normalize_damage_types(raw.get('immunities')),
        'abilities': abilities,
        'behavior': normalize_behavior(raw.get('behavior'), creature_type=creature_type, challenge_tier=challenge_tier),
        'lootTable': raw.get('lootTable', raw.get('loot_table')) if isinstance(raw.get('lootTable', raw.get('loot_table')), dict) else {},
        'xpReward': max(0, int_or_default(raw.get('xpReward', raw.get('xp_reward')), default=0)),
        'aiNarrationHints': _string_list(raw.get('aiNarrationHints', raw.get('ai_narration_hints')), limit=10),
        'variantHooks': raw.get('variantHooks', raw.get('variant_hooks')) if isinstance(raw.get('variantHooks', raw.get('variant_hooks')), list) else [],
        'balance': normalize_balance(raw.get('balance'), tier=challenge_tier, max_hp=stats['maxHp']),
    }
    if not normalized['visualTags']:
        normalized['visualTags'] = [creature_type, normalized['behavior']['combatRole']]
    if not normalized['aiNarrationHints']:
        normalized['aiNarrationHints'] = [f'Portray {name} according to its {normalized["behavior"]["intelligenceProfile"]} instincts and {normalized["behavior"]["combatRole"]} role.']
    return normalized
