"""Fight maneuvers: grapple, shove down, bind and demand surrender, the improvised action, and captives.

Grapple and shove down are Strength (Athletics) against DC 10 + the higher of
the enemy's Strength or Dexterity modifier. A grapple is an active effect on the
enemy (``sourceId`` :data:`GRAPPLE_SOURCE`, its ``casterId`` the hero) that
keeps the ``grappled`` condition and ``grappledBy``; it holds while the grappler
can act and stands in melee reach of the enemy, and it carries the DC the enemy
breaks free against (8 + the grappler's Strength modifier + proficiency bonus
when they are proficient in Athletics). A shove leaves the enemy ``prone`` until
the end of its next turn (an effect, so it does not stand up at that turn's
start).

Bind (the better of Athletics or Sleight of Hand against DC 10; no roll against
a yielded enemy) leaves the enemy ``captured``: out of the fight and alive.
Demand surrender is Charisma (Intimidation) against DC 12, DC 8 against an
enemy at a quarter of its hit points or less or grappled; it yields
(``surrendered``). The improvised action is the better of Athletics or
Acrobatics against DC 12; it leaves a named enemy ``off_balance`` (disadvantage
on its next attack) until the end of its next turn.

An enemy that ends a fight captured or yielded stays in the scene as a captive.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from aidm_server.canon_text import int_or_default
from aidm_server.combat.modifiers import active_effects, can_act, condition_keys
from aidm_server.combat.positioning import in_melee_reach, participant_by_id
from aidm_server.combat.state import participant_is_present

MANEUVER_TYPES = ('grapple', 'shove', 'subdue', 'demand_surrender')
IMPROVISE_TYPE = 'improvise'
GRAPPLE_SOURCE = 'combat_grapple'
SHOVE_SOURCE = 'combat_shove'
IMPROVISED_SOURCE = 'combat_improvised'
BIND_DC = 10
DEMAND_DC = 12
DEMAND_DC_BROKEN = 8
IMPROVISE_DC = 12
ESCAPE_DC_BASE = 8
# Conditions that take an enemy out of the fight alive; one that ends the fight with one is a captive.
CAPTIVE_CONDITIONS = frozenset({'captured', 'surrendered', 'yielded'})
YIELD_CONDITIONS = frozenset({'surrendered', 'yielded'})
PRONOUNS = {
    'it': {'subject': 'it', 'object': 'it', 'possessive': 'its', 'is': 'is', 's': 's'},
    'he': {'subject': 'he', 'object': 'him', 'possessive': 'his', 'is': 'is', 's': 's'},
    'she': {'subject': 'she', 'object': 'her', 'possessive': 'her', 'is': 'is', 's': 's'},
    'they': {'subject': 'they', 'object': 'them', 'possessive': 'their', 'is': 'are', 's': ''},
}
# Words an enemy's weapon may be called by, taken from its first melee attack's name ("Hooked Road-Knife").
WEAPON_NOUNS = frozenset(
    {
        'knife', 'blade', 'dagger', 'sword', 'longsword', 'shortsword', 'scimitar', 'sabre', 'saber', 'axe',
        'hatchet', 'club', 'mace', 'hammer', 'spear', 'staff', 'cudgel', 'maul', 'flail', 'pick', 'sickle',
        'scythe', 'whip', 'cleaver', 'hook', 'glaive', 'halberd', 'rapier',
    }
)


def _text(value: Any) -> str:
    return str(value or '').strip()


def ability_mod(participant: Mapping[str, Any] | None, ability: str) -> int:
    stats = participant.get('stats') if isinstance(participant, Mapping) and isinstance(participant.get('stats'), Mapping) else {}
    return (int_or_default(stats.get(ability), default=10) - 10) // 2


def contest_dc(enemy: Mapping[str, Any]) -> int:
    """Grapple and shove: 10 + the higher of the enemy's Strength or Dexterity modifier."""

    return 10 + max(ability_mod(enemy, 'strength'), ability_mod(enemy, 'dexterity'))


def _hp(participant: Mapping[str, Any]) -> tuple[int, int]:
    hp = participant.get('hp') if isinstance(participant.get('hp'), Mapping) else {}
    current = max(0, int_or_default(hp.get('current'), default=0))
    return current, max(current, int_or_default(hp.get('max'), default=current))


def at_most_quarter(participant: Mapping[str, Any]) -> bool:
    current, maximum = _hp(participant)
    return current * 4 <= maximum


def at_most_half(participant: Mapping[str, Any]) -> bool:
    current, maximum = _hp(participant)
    return current * 2 <= maximum


def is_boss(participant: Mapping[str, Any]) -> bool:
    return _text(participant.get('challengeTier')).lower() == 'boss'


def grapple_effect(enemy: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return next((effect for effect in active_effects(enemy) if effect['sourceId'] == GRAPPLE_SOURCE), None)


def grappler_id(enemy: Mapping[str, Any] | None) -> str:
    effect = grapple_effect(enemy)
    return effect['casterId'] if effect else ''


def grapple_holds(enemy: Mapping[str, Any] | None, combat: Mapping[str, Any]) -> bool:
    """The enemy is grappled and its grappler still holds it: present, able to act, in melee reach."""

    if 'grappled' not in condition_keys(enemy) or grapple_effect(enemy) is None:
        return False
    grappler = participant_by_id(combat, grappler_id(enemy))
    return bool(grappler) and participant_is_present(grappler) and can_act(grappler) and in_melee_reach(grappler, enemy)


def escape_dc(enemy: Mapping[str, Any] | None) -> int:
    effect = grapple_effect(enemy) or {}
    return int_or_default(effect.get('escapeDc'), default=ESCAPE_DC_BASE + 2)


def grapple_effect_payload(*, hero_id: str, hero_name: str, escape: int) -> dict[str, Any]:
    return {
        'id': f'{GRAPPLE_SOURCE}:{hero_id}',
        'sourceId': GRAPPLE_SOURCE,
        'sourceName': f'Grappled by {hero_name}',
        'casterId': hero_id,
        'condition': 'grappled',
        'escapeDc': escape,
        'modifiers': {},
    }


def shove_effect_payload(*, hero_id: str, turn_id: Any) -> dict[str, Any]:
    return {
        'id': f'{SHOVE_SOURCE}:{turn_id}:{hero_id}',
        'sourceId': SHOVE_SOURCE,
        'sourceName': 'Shoved down',
        'casterId': hero_id,
        'condition': 'prone',
        'modifiers': {},
        'duration': {'rounds': 1, 'tickOn': 'target_turn_end'},
    }


def off_balance_effect_payload(*, hero_id: str, turn_id: Any) -> dict[str, Any]:
    return {
        'id': f'{IMPROVISED_SOURCE}:{turn_id}:{hero_id}',
        'sourceId': IMPROVISED_SOURCE,
        'sourceName': 'Off balance',
        'casterId': hero_id,
        'condition': 'off_balance',
        'modifiers': {'attackMode': 'disadvantage'},
        'consumeOn': 'attack',
        'duration': {'rounds': 1, 'tickOn': 'target_turn_end'},
    }


def held_prone(participant: Mapping[str, Any] | None) -> bool:
    """Prone kept by a shove: the participant stays down until the end of its next turn."""

    return any(effect['sourceId'] == SHOVE_SOURCE and effect.get('condition') == 'prone' for effect in active_effects(participant))


def enemy_record(state: Mapping[str, Any], participant: Mapping[str, Any] | None) -> dict[str, Any]:
    """The pack enemy record a participant was made from, or ``{}``."""

    enemy_id = _text((participant or {}).get('campaignPackEnemyId'))
    if not enemy_id:
        return {}
    # Deferred import: the pack encounter module imports the combat modules this one is imported by.
    from aidm_server.game_state.campaign_pack_encounters import catalog_record

    return catalog_record(dict(state), 'enemies', enemy_id)


def pronoun_key(state: Mapping[str, Any], participant: Mapping[str, Any] | None) -> str:
    """``it`` unless the enemy record gives a pronoun; a proper-named enemy is ``they``."""

    record = enemy_record(state, participant)
    declared = _text(record.get('pronoun') or (participant or {}).get('pronoun')).lower()
    if declared in PRONOUNS:
        return declared
    return 'they' if record.get('proper') is True or (participant or {}).get('proper') is True else 'it'


def pronoun(key: Any) -> dict[str, str]:
    return PRONOUNS.get(_text(key).lower(), PRONOUNS['it'])


def weapon_noun(enemy: Mapping[str, Any] | None) -> str:
    """``knife`` for an enemy whose first melee attack is a "Hooked Road-Knife"; '' when none names a weapon."""

    for ability in (enemy or {}).get('abilities') or []:
        if not isinstance(ability, Mapping) or _text(ability.get('type')).lower() != 'attack':
            continue
        words = re.findall(r'[a-z]+', _text(ability.get('name')).lower())
        return next((word for word in reversed(words) if word in WEAPON_NOUNS), '')
    return ''


# An enemy that gave up of its own accord (the planner's negotiate intent) carries this condition.
GAVE_UP_CONDITIONS = frozenset({'negotiated'})


def captives_from_combat(
    combat: Mapping[str, Any],
    *,
    has_captive_lines: Callable[[str], bool] | None = None,
) -> list[dict[str, Any]]:
    """The enemies a fight leaves behind alive and captured or yielded: ``{"id", "name", "enemyId"}``.

    An enemy bound or made to yield is always a captive. One that gave up of its own accord
    ("throws down its weapon and yields") is a captive only when its record has something to say
    (``has_captive_lines(enemyId)``), so spirits and beasts that stand down are not held.
    """

    captives = []
    for participant in combat.get('participants') or []:
        if not isinstance(participant, Mapping) or _text(participant.get('team')).lower() != 'enemy':
            continue
        keys = condition_keys(participant)
        enemy_id = _text(participant.get('campaignPackEnemyId') or participant.get('definitionId'))
        held = bool(keys & CAPTIVE_CONDITIONS) or bool(
            keys & GAVE_UP_CONDITIONS and has_captive_lines is not None and has_captive_lines(enemy_id)
        )
        if not held or _hp(participant)[0] <= 0:
            continue
        captives.append(
            {
                'id': _text(participant.get('id')),
                'name': _text(participant.get('name')),
                'enemyId': _text(participant.get('campaignPackEnemyId') or participant.get('definitionId')),
            }
        )
    return captives
