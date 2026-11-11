"""Story rolls: every roll the engine made or accepted in a turn, as the roll cards show them.

Built from the engine's own results, never from narration. The rolls known
before narration (the roll the player pressed, the turn's spells, items, class
features, rests and object checks, initiative when a fight starts, the hero's
fight action, attack and its damage, the enemy turns and the death saves due)
go into the turn's narration packet as ``storyRolls`` and are sent on
``turn_rolls`` before the narrator starts. When the turn's effects are applied
the rolls made after narration (engine turns settled then, death saves not yet
known, saves and ongoing damage at the turn boundaries) are appended and the
whole list is written on the turn, its persisted Dungeon Master log entry and
its turn event, like the story beats. Nothing is withheld from players.

One record per roll, in the order it happened::

    {"id": "t17-3", "kind": "attack",
     "actor": {"id", "name", "side"}, "target": {"id", "name"},
     "label": "Dust-Marked Pounce",
     "d20": {"faces": [14, 9], "kept": 14, "mode": "advantage"},
     "parts": [{"label": "attack bonus", "value": 5}],
     "total": 19, "against": {"kind": "AC", "value": 17},
     "outcome": "hit", "natural": null,
     "damage": {"dice": "1d8+3", "faces": [4], "bonus": 3, "total": 7,
                "type": "piercing", "critical": false, "note": null}}

``kind`` is ``attack``, ``save``, ``check``, ``death_save``, ``damage`` (no
d20), ``healing`` or ``initiative`` (one record per fight with ``entries`` in
turn order). A save's actor is whoever rolls it and ``source`` names what forced
it. ``parts`` are the labelled modifiers in the order applied; dice parts carry
``dice`` and ``faces``; a remainder the engine does not label is one ``bonus``
part, so the parts always add up to the total. A die other than the d20 is
named in ``die``. ``damage`` and ``healing`` totals are the engine's rolled
results (after resistance and party scaling, before hit points run out);
``damage.parts`` lists extra damage dice (Sneak Attack). ``note`` explains a
total the dice alone do not give: ``halved on a success``, ``resisted``,
``doubled dice (critical)``, ``scaled for the party``.
"""

from __future__ import annotations

from typing import Any, Iterable

from aidm_server.class_features import CLASS_CAPABILITY_CATALOG
from aidm_server.combat.modifiers import ability_key
from aidm_server.combat.vitality import DEATH_SAVE_DC, death_save_change_id
from aidm_server.damage_dice import parse_damage_dice_expression
from aidm_server.database import db
from aidm_server.game_state import STATE_PIPELINE_METADATA_KEY
from aidm_server.models import DmTurn, Session, SessionLogEntry, TurnEvent, safe_json_dumps, safe_json_loads
from aidm_server.turn_events import DM_RESPONSE_EVENT

STORY_ROLLS_KEY = 'storyRolls'
# The post-narration extraction's record of the engine turns settled after narration.
SETTLED_ACTIONS_KEY = 'engineResolvedActions'
ABILITY_NAMES = {
    'strength': 'Strength',
    'dexterity': 'Dexterity',
    'constitution': 'Constitution',
    'intelligence': 'Intelligence',
    'wisdom': 'Wisdom',
    'charisma': 'Charisma',
}
SIDES = {'player': 'hero', 'enemy': 'enemy'}
NOTE_HALVED = 'halved on a success'
NOTE_RESISTED = 'resisted'
NOTE_CRITICAL = 'doubled dice (critical)'
NOTE_SCALED = 'scaled for the party'
# Initiative written for old snapshots from roster order is not a roll.
UNROLLED_INITIATIVE = 'legacy_roster_order'


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ints(value: Any) -> list[int]:
    return [number for item in value if (number := _int(item)) is not None] if isinstance(value, list) else []


def _ability_name(value: Any) -> str:
    return ABILITY_NAMES.get(ability_key(value), '')


def _skill_name(value: Any) -> str:
    words = _text(value).replace('_', ' ').split()
    return ' '.join(word if word == 'of' else word.capitalize() for word in words)


def _check_label(skill: Any, ability: Any) -> str:
    """``Athletics check``, ``Strength check``; ``Check`` when neither is known."""

    name = _skill_name(skill) if _text(skill).lower() not in {'', 'check', 'ability'} else _ability_name(ability)
    return f'{name} check' if name else 'Check'


def _save_label(ability: Any) -> str:
    name = _ability_name(ability)
    return f'{name} save' if name else 'Saving throw'


def _natural(kept: int | None, *, die: str = 'd20') -> str | None:
    if die != 'd20' or kept is None:
        return None
    return 'twenty' if kept == 20 else 'one' if kept == 1 else None


def _dice_without_bonus(expression: Any) -> str:
    """``2d6`` of ``+2d6+1``; the expression as given when it does not parse."""

    parsed = parse_damage_dice_expression(_text(expression).lstrip('+-'))
    if not parsed or not int(parsed.get('count') or 0):
        return _text(expression)
    return f"{parsed['count']}d{parsed['sides']}"


def _with_bonus(dice: str, bonus: int) -> str:
    return f'{dice}{bonus:+d}' if bonus else dice


class People:
    """Names and sides of everyone a roll can name, from the turn's state."""

    def __init__(self, *states: dict[str, Any]):
        self.records: dict[str, dict[str, str]] = {}
        for state in states:
            for hero in _dicts(state.get('playerCharacters')):
                if _text(hero.get('id')):
                    self.records.setdefault(_text(hero.get('id')), {'name': _text(hero.get('name')), 'side': 'hero'})
            combat = _dict(state.get('combat'))
            for participant in _dicts(combat.get('participants')):
                participant_id = _text(participant.get('id'))
                if participant_id:
                    team = _text(participant.get('team')).lower()
                    self.records.setdefault(
                        participant_id,
                        {'name': _text(participant.get('name')), 'side': SIDES.get(team, 'ally')},
                    )
            scene = _dict(state.get('currentScene'))
            for collection in ('interactables', 'hazards'):
                for entry in _dicts(scene.get(collection)):
                    if _text(entry.get('id')):
                        self.records.setdefault(_text(entry.get('id')), {'name': _text(entry.get('name')), 'side': 'enemy'})

    def actor(self, actor_id: Any, name: Any = None, *, side: str | None = None) -> dict[str, str]:
        record = self.records.get(_text(actor_id), {})
        resolved_side = side or record.get('side') or ('hero' if _text(actor_id).startswith('player_') else 'enemy')
        return {'id': _text(actor_id), 'name': _text(name) or record.get('name') or _text(actor_id), 'side': resolved_side}

    def target(self, target_id: Any, name: Any = None) -> dict[str, str] | None:
        if not _text(target_id) and not _text(name):
            return None
        return {'id': _text(target_id), 'name': _text(name) or self.records.get(_text(target_id), {}).get('name') or _text(target_id)}


def _dice_part(rolled: dict[str, Any], label: str) -> dict[str, Any] | None:
    """A rolled bonus die (``{"dice": "+1d4", "rolls": [3], "total": 3}``) as a part."""

    value = _int(rolled.get('total'))
    if value is None:
        return None
    dice = _text(rolled.get('dice'))
    return {
        'label': label if not dice.startswith('-') else 'penalty die',
        'dice': _dice_without_bonus(dice),
        'faces': _ints(rolled.get('rolls')),
        'value': value,
    }


def _bonus_dice_parts(rolled_dice: Any) -> list[dict[str, Any]]:
    return [part for rolled in _dicts(rolled_dice) if (part := _dice_part(rolled, 'bonus die'))]


def _inspiration_part(inspiration: Any) -> list[dict[str, Any]]:
    inspiration = _dict(inspiration)
    roll = _int(inspiration.get('roll'))
    if roll is None:
        return []
    die = _text(inspiration.get('die')) or 'd6'
    return [{'label': 'Bardic Inspiration', 'dice': die if die[:1].isdigit() else f'1{die}', 'faces': [roll], 'value': roll}]


def _settled_parts(parts: list[dict[str, Any]], *, kept: int | None, total: int | None) -> list[dict[str, Any]]:
    """The non-zero parts, and one ``bonus`` part for what they leave of the total."""

    shown = [part for part in parts if part.get('dice') or part.get('value')]
    if kept is None or total is None:
        return shown
    remainder = total - kept - sum(int(part['value']) for part in shown)
    return [*shown, {'label': 'bonus', 'value': remainder}] if remainder else shown


def _d20(faces: Any, kept: Any, mode: Any) -> dict[str, Any] | None:
    kept_value = _int(kept)
    if kept_value is None:
        return None
    rolled = _ints(faces) or [kept_value]
    resolved_mode = _text(mode).lower()
    return {
        'faces': rolled,
        'kept': kept_value,
        'mode': resolved_mode if resolved_mode in {'advantage', 'disadvantage'} and len(rolled) > 1 else 'normal',
    }


def _against(kind: str, value: Any) -> dict[str, Any] | None:
    number = _int(value)
    return {'kind': kind, 'value': number} if number is not None else None


def _roll(
    kind: str,
    *,
    actor: dict[str, str] | None,
    label: str,
    d20: dict[str, Any] | None = None,
    parts: list[dict[str, Any]] | None = None,
    total: Any = None,
    against: dict[str, Any] | None = None,
    outcome: str | None = None,
    target: dict[str, str] | None = None,
    source: str | None = None,
    die: str = 'd20',
    **extra: Any,
) -> dict[str, Any]:
    record: dict[str, Any] = {'kind': kind}
    if actor is not None:
        record['actor'] = actor
    if target is not None:
        record['target'] = target
    record['label'] = label
    if source:
        record['source'] = source
    if kind not in {'damage', 'healing', 'initiative'}:
        record['d20'] = d20
        if die != 'd20':
            record['die'] = die
        record['parts'] = _settled_parts(parts or [], kept=(d20 or {}).get('kept'), total=_int(total))
        record['total'] = _int(total)
        record['against'] = against
        record['outcome'] = outcome
        record['natural'] = _natural((d20 or {}).get('kept'), die=die)
    record.update(extra)
    return record


def _damage(
    *,
    dice: Any,
    faces: Any,
    bonus: Any,
    total: Any,
    damage_type: Any,
    critical: bool = False,
    notes: Iterable[str] = (),
    parts: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    amount = _int(total)
    if amount is None or amount <= 0:
        return None
    flat = _int(bonus) or 0
    record = {
        'dice': _with_bonus(_dice_without_bonus(dice), flat) if _text(dice) else str(flat),
        'faces': _ints(faces),
        'bonus': flat,
        'total': amount,
        'type': _text(damage_type).lower() or None,
        'critical': critical,
        'note': ', '.join(dict.fromkeys(note for note in notes if note)) or None,
    }
    if parts:
        record['parts'] = parts
    return record


def _scaled(attacker: dict[str, Any]) -> bool:
    multiplier = attacker.get('partyDamageMultiplier')
    return isinstance(multiplier, (int, float)) and not isinstance(multiplier, bool) and multiplier != 1


def attack_damage(result: dict[str, Any], *, scaled: bool = False) -> dict[str, Any] | None:
    """The damage of a resolved attack (``resolve_attack`` / ``resolve_enemy_ability``) that hit."""

    if result.get('hit') is not True:
        return None
    critical = result.get('critical') is True
    multipliers = [reaction.get('damageMultiplier') for reaction in _dicts(result.get('reactions'))]
    notes = [
        NOTE_CRITICAL if critical else '',
        NOTE_RESISTED if any(isinstance(value, (int, float)) and not isinstance(value, bool) and value < 1 for value in multipliers) else '',
        NOTE_SCALED if scaled else '',
    ]
    components = _dicts(result.get('damageComponents'))
    if not components:
        return _damage(
            dice=result.get('damageDice'),
            faces=result.get('damageRolls'),
            bonus=result.get('damageBonus'),
            total=result.get('damageTotal'),
            damage_type=result.get('damageType'),
            critical=critical,
            notes=notes,
        )
    base, extras = components[0], components[1:]
    bonus = (_int(base.get('bonus')) or 0) + sum(_int(extra.get('bonus')) or 0 for extra in extras)
    parts = [
        {'label': _text(extra.get('source')) or 'extra damage', 'dice': _dice_without_bonus(extra.get('dice')), 'faces': _ints(extra.get('rolls')), 'value': sum(_ints(extra.get('rolls')))}
        for extra in extras
        if _ints(extra.get('rolls'))
    ]
    if any(float(component.get('multiplier') or 1) < 1 for component in components):
        notes.append(NOTE_RESISTED)
    return _damage(
        dice=base.get('dice'),
        faces=base.get('rolls'),
        bonus=bonus,
        total=result.get('damageTotal'),
        damage_type=result.get('damageType') or base.get('type'),
        critical=critical,
        notes=notes,
        parts=parts,
    )


def _save_from_result(result: dict[str, Any], *, people: People, saver_id: Any, saver_name: Any, source: str) -> dict[str, Any]:
    """A save rolled by ``resolve_save`` (an enemy ability's, Turn Undead's, Stunning Strike's)."""

    d20 = _d20(result.get('saveRolls'), result.get('saveRoll'), result.get('saveMode'))
    parts = [
        {'label': 'bonus', 'value': _int(result.get('saveModifier')) or 0},
        *_bonus_dice_parts(result.get('saveBonusDice')),
        *_inspiration_part(result.get('inspiration')),
    ]
    return _roll(
        'save',
        actor=people.actor(saver_id, saver_name),
        label=_save_label(result.get('saveAbility')),
        source=source,
        d20=d20,
        parts=parts,
        total=result.get('saveTotal'),
        against=_against('DC', result.get('saveDC')),
        outcome='success' if result.get('saveSucceeded') is True else 'failure',
    )


def _attack_from_result(
    result: dict[str, Any],
    *,
    actor: dict[str, str],
    target: dict[str, str] | None,
    label: str,
    parts: list[dict[str, Any]],
    scaled: bool = False,
) -> dict[str, Any]:
    record = _roll(
        'attack',
        actor=actor,
        target=target,
        label=label,
        d20=_d20(result.get('attackRolls'), result.get('attackRoll'), result.get('attackMode')),
        parts=[*parts, *_bonus_dice_parts(result.get('attackBonusDice')), *_inspiration_part(result.get('inspiration'))],
        total=result.get('attackTotal'),
        against=_against('AC', result.get('targetArmorClass')),
        outcome='hit' if result.get('hit') is True else 'miss',
    )
    damage = attack_damage(result, scaled=scaled)
    if damage:
        record['damage'] = damage
    return record


def _reaction_rolls(reactions: Any, *, people: People, mover_id: Any, attackers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Opportunity attacks: the reactor strikes the participant who moved."""

    rolls = []
    for reaction in _dicts(reactions):
        attack = _dict(reaction.get('attack'))
        if _int(attack.get('attackTotal')) is None:
            continue
        reactor_id = _text(reaction.get('reactorId'))
        actor = people.actor(reactor_id, attack.get('attackerName') or reaction.get('reactorName'))
        label = _text(attack.get('weaponName') or attack.get('abilityName')) or 'Opportunity attack'
        bonus = _int(attack.get('attackBonus'))
        rolls.append(
            _attack_from_result(
                attack,
                actor=actor,
                target=people.target(mover_id),
                label=label,
                parts=[{'label': 'attack bonus', 'value': bonus}] if bonus is not None else [],
                scaled=_scaled(attackers.get(reactor_id) or {}),
            )
        )
    return rolls


def _sheet_parts(roll: dict[str, Any]) -> list[dict[str, Any]]:
    """The hero's own modifiers of a server-rolled player roll: ability, proficiency, the rules that moved it."""

    breakdown = _dict(roll.get('modifier_breakdown'))
    ability = _dict(roll.get('ability'))
    parts = []
    ability_value = _int(breakdown.get('ability_modifier'))
    if ability_value:
        parts.append({'label': _ability_name(ability.get('key')) or 'ability', 'value': ability_value})
    proficiency = _int(breakdown.get('proficiency_bonus'))
    if proficiency:
        parts.append({'label': 'expertise' if _int(breakdown.get('proficiency_multiplier')) == 2 else 'proficiency', 'value': proficiency})
    modifiers = _dicts(roll.get('modifiers'))
    if modifiers:
        parts.extend({'label': _text(modifier.get('label')) or 'modifier', 'value': _int(modifier.get('value')) or 0} for modifier in modifiers)
    elif _int(breakdown.get('wound_penalty')):
        parts.append({'label': 'Wounded', 'value': -abs(_int(breakdown.get('wound_penalty')) or 0)})
    return parts


def _player_roll(roll: dict[str, Any], *, people: People, actor_id: str) -> dict[str, Any]:
    """A roll the player pressed in the roll prompt: a check, a save, or a roll of their own."""

    rule_type = _text(roll.get('rule_type')).lower()
    die = _text(roll.get('die')).lower() or 'd20'
    ability = _dict(roll.get('ability'))
    reason = _text(roll.get('reason'))
    if roll.get('free') is True:
        kind, label = 'check', _ability_name(ability.get('key')) or die
    elif rule_type.endswith('_saving_throw'):
        kind, label = 'save', _save_label(rule_type.removesuffix('_saving_throw'))
    else:
        skill = rule_type if rule_type not in {'check', 'ability', ''} else ''
        kind, label = 'check', _check_label(skill, ability.get('key'))
    source = reason if kind == 'save' and reason.lower() not in {'', rule_type.replace('_', ' ')} else None
    outcome = _text(roll.get('outcome')).lower()
    return _roll(
        kind,
        actor=people.actor(actor_id),
        label=label,
        source=source,
        d20=_d20(roll.get('rolls'), roll.get('kept'), roll.get('mode')),
        die=die,
        parts=[*_sheet_parts(roll), *_bonus_dice_parts(roll.get('bonus_dice')), *_inspiration_part(roll.get('inspiration'))],
        total=roll.get('total'),
        against=_against('DC', roll.get('dc')) if outcome else None,
        outcome=outcome if outcome in {'success', 'failure'} else None,
    )


def _hero_attack(roll: dict[str, Any] | None, resolved: dict[str, Any] | None, *, people: People, actor_id: str) -> dict[str, Any] | None:
    """The hero's attack: the server-rolled d20 the player pressed, resolved with its damage."""

    if isinstance(resolved, dict) and _int(resolved.get('attackTotal')) is not None:
        attack_bonus = _int(resolved.get('attackBonus')) or 0
        sheet = _sheet_parts(roll or {})
        parts = sheet if sum(int(part['value']) for part in sheet) == attack_bonus else [{'label': 'attack bonus', 'value': attack_bonus}]
        weapon = _text(resolved.get('weaponName')) or 'Attack'
        return _attack_from_result(
            resolved,
            actor=people.actor(resolved.get('sourceActorId') or actor_id, resolved.get('sourceActorName')),
            target=people.target(resolved.get('targetId'), resolved.get('targetName')),
            label=f'{weapon} (off-hand)' if resolved.get('offhand') is True else weapon,
            parts=parts,
        )
    if not isinstance(roll, dict):
        return None
    outcome = _text(roll.get('outcome')).lower()
    return _roll(
        'attack',
        actor=people.actor(actor_id),
        label='Attack',
        d20=_d20(roll.get('rolls'), roll.get('kept'), roll.get('mode')),
        parts=[*_sheet_parts(roll), *_bonus_dice_parts(roll.get('bonus_dice'))],
        total=roll.get('total'),
        against=_against('AC', roll.get('target_ac')),
        outcome='hit' if outcome == 'success' else 'miss' if outcome == 'failure' else None,
    )


def _engine_check(check: dict[str, Any], *, people: People, actor_id: Any, actor_name: Any = None, target: dict[str, str] | None = None) -> dict[str, Any]:
    """A check the engine rolled for a hero: a fight maneuver, Hide, Stabilize, an improvised action."""

    success = check.get('success') if isinstance(check.get('success'), bool) else _text(check.get('outcome')).lower() == 'success'
    return _roll(
        'check',
        actor=people.actor(actor_id, actor_name),
        target=target,
        label=_check_label(check.get('skill'), check.get('ability')),
        d20=_d20(check.get('rolls'), check.get('roll'), check.get('mode')),
        parts=[{'label': 'bonus', 'value': _int(check.get('modifier')) or 0}, *_bonus_dice_parts(check.get('bonusDice'))],
        total=check.get('total'),
        against=_against('DC', check.get('dc')),
        outcome='success' if success else 'failure',
    )


def _object_rolls(change: dict[str, Any], *, people: People) -> list[dict[str, Any]]:
    """An object action's check, and the saves and damage of what it set off."""

    event = _dict(change.get('event'))
    actor_id = _text(change.get('actorId') or event.get('actorId'))
    object_id = _text(change.get('targetId') or event.get('targetId'))
    object_name = _text(event.get('targetName')) or people.records.get(object_id, {}).get('name') or ''
    rolls = []
    check = _dict(event.get('check'))
    if _int(check.get('total')) is not None:
        take_time = check.get('takeTime') is True
        record = _engine_check(check, people=people, actor_id=actor_id, target=people.target(object_id, object_name))
        if take_time:
            # Taken slowly the check counts as a 20 on the die: no natural 20 was rolled.
            record['label'] = f"{record['label']} (taking time)"
            record['natural'] = None
        rolls.append(record)
    results = _dict(event.get('effectResults'))
    damage = _dict(results.get('damage'))
    for target in _dicts(results.get('targets')):
        dealt = _damage(
            dice=damage.get('dice'),
            faces=damage.get('rolls'),
            bonus=_bonus_of(damage.get('dice')),
            total=target.get('damage'),
            damage_type=damage.get('type'),
        )
        save = _dict(target.get('save'))
        if _int(save.get('total')) is not None or save.get('autoFail') is True:
            record = _roll(
                'save',
                actor=people.actor(target.get('actorId')),
                label=_save_label(save.get('ability')),
                source=object_name or None,
                d20=_d20(save.get('rolls'), save.get('roll'), save.get('mode')),
                parts=[{'label': 'bonus', 'value': _int(save.get('modifier')) or 0}, *_bonus_dice_parts(save.get('bonusDice'))],
                total=save.get('total'),
                against=_against('DC', save.get('dc')),
                outcome='success' if save.get('passed') is True else 'failure',
            )
            if dealt:
                if save.get('passed') is True:
                    dealt['note'] = NOTE_HALVED
                record['damage'] = dealt
            rolls.append(record)
        elif dealt:
            rolls.append(
                _roll(
                    'damage',
                    actor=people.actor(object_id, object_name, side='enemy'),
                    target=people.target(target.get('actorId')),
                    label=object_name or 'Hazard',
                    damage=dealt,
                )
            )
    return rolls


def _bonus_of(expression: Any) -> int:
    parsed = parse_damage_dice_expression(expression)
    return int(parsed['bonus']) if parsed else 0


def _effect_damage(effects: list[dict[str, Any]], *, critical: bool, saved: bool) -> dict[str, Any] | None:
    """A spell's damage on one target: its rolled damage effects together."""

    damage_effects = [effect for effect in effects if _text(effect.get('kind')) == 'damage' and isinstance(effect.get('roll'), dict)]
    if not damage_effects:
        return None
    base = damage_effects[0]
    roll = base['roll']
    notes = [
        NOTE_CRITICAL if critical or roll.get('criticalDice') is True else '',
        NOTE_HALVED if saved and any((_int(effect.get('afterSaveAmount')) or 0) < (_int(effect.get('rolledAmount')) or 0) for effect in damage_effects) else '',
        NOTE_RESISTED if any(_text(effect.get('defense')) in {'resistance', 'immunity'} for effect in damage_effects) else '',
    ]
    parts = [
        {
            'label': _text(effect.get('source')) or 'extra damage',
            'dice': _dice_without_bonus(effect['roll'].get('dice')),
            'faces': _ints(effect['roll'].get('rolls')),
            'value': sum(_ints(effect['roll'].get('rolls'))),
        }
        for effect in damage_effects[1:]
        if _ints(effect['roll'].get('rolls'))
    ]
    return _damage(
        dice=roll.get('dice'),
        faces=roll.get('rolls'),
        bonus=sum(_int(effect['roll'].get('bonus')) or 0 for effect in damage_effects),
        total=sum(_int(effect.get('postDefenseAmount', effect.get('amountApplied'))) or 0 for effect in damage_effects),
        damage_type=base.get('damageType'),
        critical=critical,
        notes=notes,
        parts=parts,
    )


def _healing(rolled: dict[str, Any], *, total: Any = None) -> dict[str, Any] | None:
    amount = _int(total if total is not None else rolled.get('total'))
    if amount is None:
        return None
    bonus = _int(rolled.get('bonus')) or 0
    return {
        'dice': _with_bonus(_dice_without_bonus(rolled.get('dice')), bonus),
        'faces': _ints(rolled.get('rolls')),
        'bonus': bonus,
        'total': amount,
    }


def _spell_rolls(change: dict[str, Any], *, people: People) -> list[dict[str, Any]]:
    resolution = _dict(change.get('spellResolution'))
    spell = _text(resolution.get('spellName') or change.get('spellName')) or 'Spell'
    caster_id = _text(resolution.get('casterId') or change.get('actorId'))
    caster = people.actor(caster_id)
    rolls = []
    for target in _dicts(resolution.get('targets')):
        delivery = _dict(target.get('delivery'))
        effects = _dicts(target.get('effects'))
        target_ref = people.target(target.get('targetId'), target.get('targetName'))
        delivery_type = _text(delivery.get('type'))
        critical = delivery.get('critical') is True
        if delivery_type == 'attack':
            record = _roll(
                'attack',
                actor=caster,
                target=target_ref,
                label=spell,
                d20=_d20(delivery.get('rolls'), delivery.get('naturalRoll'), delivery.get('mode')),
                parts=[
                    {'label': 'attack bonus', 'value': _int(delivery.get('modifier')) or 0},
                    *_bonus_dice_parts(delivery.get('bonusDice')),
                    *_inspiration_part(delivery.get('inspiration')),
                ],
                total=delivery.get('total'),
                against=_against('AC', delivery.get('targetArmorClass')),
                outcome='hit' if delivery.get('delivered') is True else 'miss',
            )
            damage = _effect_damage(effects, critical=critical, saved=False) if delivery.get('delivered') is True else None
        elif delivery_type == 'save':
            saved = delivery.get('saveSucceeded') is True
            record = _roll(
                'save',
                actor=people.actor(target.get('targetId'), target.get('targetName')),
                label=_save_label(delivery.get('ability')),
                source=spell,
                d20=_d20(delivery.get('rolls'), delivery.get('naturalRoll'), delivery.get('mode')),
                parts=[
                    {'label': 'bonus', 'value': _int(delivery.get('modifier')) or 0},
                    *_bonus_dice_parts(delivery.get('bonusDice')),
                    *_inspiration_part(delivery.get('inspiration')),
                ],
                total=delivery.get('total'),
                against=_against('DC', delivery.get('dc')),
                outcome='success' if saved else 'failure',
            )
            damage = _effect_damage(effects, critical=False, saved=saved)
        else:
            record = None
            damage = _effect_damage(effects, critical=critical, saved=False)
            if damage:
                rolls.append(_roll('damage', actor=caster, target=target_ref, label=spell, damage=damage))
        if record is not None:
            if damage:
                record['damage'] = damage
            rolls.append(record)
        for effect in effects:
            if _text(effect.get('kind')) == 'healing' and isinstance(effect.get('roll'), dict) and (healing := _healing(effect['roll'])):
                rolls.append(_roll('healing', actor=caster, target=target_ref, label=spell, healing=healing))
    return rolls


def _capability_dice(capability_id: Any) -> dict[str, Any]:
    base_id = _text(capability_id).split(':', 1)[0]
    for capabilities in CLASS_CAPABILITY_CATALOG.values():
        for capability in capabilities:
            if capability.get('id') == base_id:
                return _dict(_dict(capability.get('effect')).get('dice'))
    return {}


def _feature_rolls(change: dict[str, Any], *, people: People) -> list[dict[str, Any]]:
    """Second Wind's healing; the saves Stunning Strike and Turn Undead force."""

    resolution = _dict(change.get('resolution'))
    name = _text(change.get('capabilityName')) or 'Class feature'
    actor_id = _text(change.get('actorId'))
    rolls = []
    faces = _ints(resolution.get('rolls'))
    amount = _int(resolution.get('amount'))
    if _text(resolution.get('effectType')) == 'heal' and faces and amount is not None:
        sides = _int(_capability_dice(change.get('capabilityId') or resolution.get('capabilityId')).get('sides'))
        dice = f'{len(faces)}d{sides}' if sides else f'{len(faces)}d'
        rolls.append(
            _roll(
                'healing',
                actor=people.actor(actor_id),
                target=people.target(resolution.get('targetId') or actor_id),
                label=name,
                healing=_healing({'dice': dice, 'rolls': faces, 'bonus': amount - sum(faces)}, total=amount),
            )
        )
    save = _dict(resolution.get('save'))
    if save.get('saveAbility'):
        target_id = resolution.get('targetId') or change.get('targetId')
        rolls.append(_save_from_result(save, people=people, saver_id=target_id, saver_name=None, source=name))
    for turned in _dicts(resolution.get('turnUndead')):
        rolls.append(_save_from_result(turned, people=people, saver_id=turned.get('targetId'), saver_name=turned.get('targetName'), source=name))
    return rolls


def _item_rolls(change: dict[str, Any], *, people: People) -> list[dict[str, Any]]:
    """A potion's healing; a thrown item's attack and damage."""

    resolution = _dict(change.get('resolution'))
    item = _text(resolution.get('itemName')) or 'Item'
    actor = people.actor(change.get('actorId'))
    target = people.target(resolution.get('targetId'), resolution.get('targetName'))
    healing = _dict(resolution.get('healing'))
    if healing and (rolled := _healing(healing)):
        return [_roll('healing', actor=actor, target=target, label=item, healing=rolled)]
    attack = _dict(resolution.get('attack'))
    if _int(attack.get('total')) is None:
        return []
    record = _roll(
        'attack',
        actor=actor,
        target=target,
        label=item,
        d20=_d20(attack.get('d20'), attack.get('natural'), attack.get('mode')),
        parts=[{'label': 'attack bonus', 'value': _int(attack.get('bonus')) or 0}, *_bonus_dice_parts(attack.get('bonusDice'))],
        total=attack.get('total'),
        against=_against('AC', attack.get('targetArmorClass')),
        outcome='hit' if attack.get('hit') is True else 'miss',
    )
    damage = _dict(resolution.get('damage'))
    critical = attack.get('critical') is True
    multiplier = damage.get('multiplier')
    resisted = isinstance(multiplier, (int, float)) and not isinstance(multiplier, bool) and multiplier < 1
    dealt = _damage(
        dice=damage.get('dice'),
        faces=damage.get('rolls'),
        bonus=damage.get('bonus'),
        total=damage.get('total'),
        damage_type=damage.get('type'),
        critical=critical,
        notes=[NOTE_CRITICAL if critical else '', NOTE_RESISTED if resisted else ''],
    )
    if attack.get('hit') is True and dealt:
        record['damage'] = dealt
    return [record]


def _rest_rolls(change: dict[str, Any], *, people: People) -> list[dict[str, Any]]:
    """The hit dice spent on a short rest: each die plus the Constitution modifier (at least 1 a die)."""

    resolution = _dict(change.get('hitDiceResolution'))
    faces = _ints(resolution.get('rolls'))
    healed = _int(resolution.get('healing'))
    if not faces or healed is None:
        return []
    actor_id = change.get('actorId')
    rolled = {'dice': f"{len(faces)}{_text(resolution.get('die')) or 'd'}", 'rolls': faces, 'bonus': healed - sum(faces)}
    return [_roll('healing', actor=people.actor(actor_id), target=people.target(actor_id), label='Hit dice', healing=_healing(rolled, total=healed))]


def _initiative(combat: dict[str, Any], people: People) -> dict[str, Any] | None:
    entries = sorted(
        (entry for entry in _dicts(combat.get('initiative')) if _text(entry.get('source')) != UNROLLED_INITIATIVE),
        key=lambda entry: _int(entry.get('order')) or 0,
    )
    if not entries:
        return None
    return _roll(
        'initiative',
        actor=None,
        label='Initiative',
        entries=[
            {
                'name': _text(entry.get('name')) or people.actor(entry.get('participantId'))['name'],
                'side': people.actor(entry.get('participantId'))['side'],
                'faces': [_int(entry.get('roll')) or 0],
                'bonus': _int(entry.get('modifier')) or 0,
                'total': _int(entry.get('total')) or 0,
            }
            for entry in entries
        ],
    )


def _change_rolls(change: dict[str, Any], *, people: People, combat: dict[str, Any]) -> list[dict[str, Any]]:
    change_type = _text(change.get('type'))
    if change_type == 'spell.cast':
        return _spell_rolls(change, people=people)
    if change_type == 'class_feature.use':
        return _feature_rolls(change, people=people)
    if change_type == 'item.use':
        return _item_rolls(change, people=people)
    if change_type == 'rest.complete':
        return _rest_rolls(change, people=people)
    if change_type == 'scene.interactable.action':
        return _object_rolls(change, people=people)
    if change_type in {'npc.talk', 'scene.effect'} and _int(_dict(change.get('check')).get('total')) is not None:
        # A conversation's check (a topic, a threat, a claim) and a plan's: cards exactly like an object check.
        subject = (change.get('npcId'), change.get('npcName')) if change_type == 'npc.talk' else (
            change.get('subjectId') or change.get('objectId'),
            change.get('subjectName') or change.get('objectName'),
        )
        return [_engine_check(_dict(change['check']), people=people, actor_id=change.get('actorId'), target=people.target(*subject))]
    if change_type == 'combat.start':
        initiative = _initiative(combat, people)
        return [initiative] if initiative else []
    return []


def _enemy_action_rolls(action: dict[str, Any], *, people: People, attackers: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """One engine-resolved enemy turn: breaking a hold, an attack, a forced save, healing, and the
    opportunity attacks it provoked."""

    enemy_id = _text(action.get('enemyId'))
    enemy = people.actor(enemy_id, action.get('enemyName'), side=people.records.get(enemy_id, {}).get('side') or 'enemy')
    ability = _text(action.get('abilityName')) or 'Attack'
    rolls = _reaction_rolls(action.get('reactions'), people=people, mover_id=enemy_id, attackers=attackers)
    for check in _dicts(action.get('stakeChecks')):
        # What a fight is about (W9-SCENE): clearing a barred way, grabbing past a guard, wresting an item away.
        against = _dict(check.get('against'))
        rolls.append(
            _roll(
                'check',
                actor=enemy,
                target=people.target(against.get('heroId'), against.get('heroName')) if against else people.target(None, check.get('targetName')),
                label=_text(check.get('label')) or 'Check',
                d20=_d20([check.get('roll')], check.get('roll'), 'normal'),
                parts=[{'label': 'bonus', 'value': _int(check.get('modifier')) or 0}],
                total=check.get('total'),
                against={'kind': _text(against.get('kind')), 'value': _int(against.get('value'))} if against else _against('DC', check.get('dc')),
                outcome='success' if check.get('success') is True else 'failure',
            )
        )
    escape = _dict(action.get('breakFree'))
    if _int(escape.get('total')) is not None:
        rolls.append(
            _roll(
                'check',
                actor=enemy,
                target=people.target(action.get('grapplerId'), action.get('grapplerName')),
                label='Break free',
                d20=_d20([escape.get('roll')], escape.get('roll'), 'normal'),
                parts=[{'label': 'bonus', 'value': _int(escape.get('modifier')) or 0}],
                total=escape.get('total'),
                against=_against('DC', escape.get('dc')),
                outcome='success' if escape.get('success') is True else 'failure',
            )
        )
    mode = _text(action.get('resolutionMode'))
    target_id, target_name = action.get('targetId'), action.get('targetName')
    if mode == 'heal':
        healing = {'dice': action.get('healingDice'), 'rolls': action.get('healingRolls'), 'bonus': _bonus_of(action.get('healingDice'))}
        healed = sum(_ints(action.get('healingRolls'))) + _bonus_of(action.get('healingDice'))
        rolls.append(_roll('healing', actor=enemy, target=people.target(target_id, target_name), label=ability, healing=_healing(healing, total=healed)))
        return rolls
    scaled = _scaled(attackers.get(enemy_id) or {})
    if _int(action.get('attackTotal')) is not None:
        bonus = _int(action.get('attackBonus'))
        rolls.append(
            _attack_from_result(
                action,
                actor=enemy,
                target=people.target(target_id, target_name),
                label=ability,
                parts=[{'label': 'attack bonus', 'value': bonus}] if bonus is not None else [],
                scaled=scaled,
            )
        )
        if action.get('saveAbility') and (_int(action.get('saveTotal')) is not None or action.get('saveAutoFailed') is True):
            rolls.append(_save_from_result(action, people=people, saver_id=target_id, saver_name=target_name, source=ability))
    elif action.get('saveAbility') and (_int(action.get('saveTotal')) is not None or action.get('saveAutoFailed') is True):
        record = _save_from_result(action, people=people, saver_id=target_id, saver_name=target_name, source=ability)
        notes = [NOTE_HALVED if action.get('saveSucceeded') is True else '', NOTE_SCALED if scaled else '']
        damage = _damage(
            dice=action.get('damageDice'),
            faces=action.get('damageRolls'),
            bonus=action.get('damageBonus'),
            total=action.get('damageTotal'),
            damage_type=action.get('damageType'),
            notes=notes,
        )
        if damage:
            record['damage'] = damage
        rolls.append(record)
    return rolls


def _death_save(*, people: People, participant_id: Any, name: Any, roll: Any, result: Any) -> dict[str, Any]:
    natural = _int(roll)
    return _roll(
        'death_save',
        actor=people.actor(participant_id, name, side='hero'),
        label='Death save',
        d20=_d20([natural], natural, 'normal'),
        total=natural,
        against=_against('DC', DEATH_SAVE_DC),
        outcome='success' if 'success' in _text(result).lower() else 'failure',
    )


def _attackers(*states: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(participant.get('id')): participant
        for state in states
        for participant in _dicts(_dict(state.get('combat')).get('participants'))
        if _text(participant.get('id'))
    }


def story_rolls_before_narration(
    *,
    actor_id: str,
    state_before: dict[str, Any],
    state_before_dm: dict[str, Any],
    authoritative_roll: dict[str, Any] | None,
    applied_changes: list[dict[str, Any]],
    dm_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """The turn's rolls resolved before narration, in order, without ids (see :func:`numbered`)."""

    people = People(state_before_dm, state_before)
    attackers = _attackers(state_before, state_before_dm)
    combat_state = _dict(dm_context.get('combatState'))
    resolved_attack = combat_state.get('playerResolvedAction') if isinstance(combat_state.get('playerResolvedAction'), dict) else None
    hero_attack = isinstance(authoritative_roll, dict) and _text(authoritative_roll.get('rule_type')).lower() in {'attack', 'spell_attack'}
    rolls: list[dict[str, Any]] = []
    if isinstance(authoritative_roll, dict) and not hero_attack:
        rolls.append(_player_roll(authoritative_roll, people=people, actor_id=actor_id))
    combat_after = _dict(state_before_dm.get('combat'))
    for change in applied_changes:
        if isinstance(change, dict):
            rolls.extend(_change_rolls(change, people=people, combat=combat_after))
    fight_action = _dict(combat_state.get('playerResolvedCombatAction'))
    check = _dict(fight_action.get('check'))
    if _int(check.get('total')) is not None:
        rolls.append(
            _engine_check(
                check,
                people=people,
                actor_id=fight_action.get('actorId') or actor_id,
                actor_name=fight_action.get('actorName'),
                target=people.target(fight_action.get('targetId'), fight_action.get('targetName')),
            )
        )
    rolls.extend(_reaction_rolls(fight_action.get('reactions'), people=people, mover_id=fight_action.get('actorId') or actor_id, attackers=attackers))
    if hero_attack:
        attack = _hero_attack(authoritative_roll, resolved_attack, people=people, actor_id=actor_id)
        if attack:
            rolls.append(attack)
        if resolved_attack:
            rolls.extend(_reaction_rolls(resolved_attack.get('reactions'), people=people, mover_id=resolved_attack.get('targetId'), attackers=attackers))
    for action in _dicts(combat_state.get('enemyResolvedActions')):
        rolls.extend(_enemy_action_rolls(action, people=people, attackers=attackers))
    for save in _dicts(combat_state.get('deathSaves')):
        rolls.append(_death_save(people=people, participant_id=save.get('participantId'), name=save.get('participantName'), roll=save.get('roll'), result=save.get('result')))
    return rolls


def story_rolls_after_narration(
    *,
    turn_id: Any,
    pipeline: dict[str, Any],
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """The rolls made after narration: engine turns settled then, death saves the narration packet did
    not know, the turn's post-narration spells, items and features, and the saves and ongoing damage at
    the turn boundaries the turn passed."""

    state_before_dm = _dict(pipeline.get('stateBeforeDm'))
    people = People(state, state_before_dm)
    attackers = _attackers(state_before_dm, state)
    combat_state = _dict(_dict(pipeline.get('dmContextPacket')).get('combatState'))
    known_saves = {
        death_save_change_id(turn_id, save.get('participantId'), save.get('round'))
        for save in _dicts(combat_state.get('deathSaves'))
    }
    rolls: list[dict[str, Any]] = []
    for action in _dicts(_dict(pipeline.get('postDmExtraction')).get(SETTLED_ACTIONS_KEY)):
        rolls.extend(_enemy_action_rolls(action, people=people, attackers=attackers))
    for change in _dicts(pipeline.get('postAppliedChanges')):
        change_type = _text(change.get('type'))
        if change_type == 'combat.death_save' and _text(change.get('id')) not in known_saves:
            rolls.append(
                _death_save(people=people, participant_id=change.get('participantId'), name=change.get('participantName'), roll=change.get('roll'), result=change.get('result'))
            )
        elif change_type in {'spell.cast', 'class_feature.use', 'item.use', 'rest.complete', 'scene.interactable.action'}:
            rolls.extend(_change_rolls(change, people=people, combat={}))
        for event in _dicts(change.get('turnBoundaryEvents')):
            for ongoing in _dicts(event.get('ongoingDamage')):
                damage = _damage(
                    dice=ongoing.get('dice'),
                    faces=ongoing.get('rolls'),
                    bonus=_bonus_of(ongoing.get('dice')),
                    total=ongoing.get('damage'),
                    damage_type=ongoing.get('damageType'),
                    notes=[NOTE_RESISTED if _text(ongoing.get('defense')) in {'resistance', 'immunity'} else ''],
                )
                if damage:
                    rolls.append(_roll('damage', actor=None, target=people.target(event.get('actorId')), label='Ongoing damage', damage=damage))
            for result in _dicts(event.get('conditionSaves')):
                save = _dict(result.get('save'))
                rolls.append(_save_from_result(save, people=people, saver_id=event.get('actorId'), saver_name=None, source=_text(result.get('sourceName')) or 'Condition'))
    return rolls


def numbered(rolls: list[dict[str, Any]], *, turn_id: Any, start: int = 1) -> list[dict[str, Any]]:
    """Each roll with its id, ``t{turn}-{n}`` in turn order, first."""

    return [{'id': f't{turn_id}-{index}', **roll} for index, roll in enumerate(rolls, start=start)]


def turn_story_rolls(turn: DmTurn) -> list[dict[str, Any]]:
    """The rolls written on the turn, or those its narration packet holds while it is being narrated."""

    metadata = safe_json_loads(turn.metadata_json, {})
    metadata = metadata if isinstance(metadata, dict) else {}
    if isinstance(metadata.get(STORY_ROLLS_KEY), list):
        return metadata[STORY_ROLLS_KEY]
    pipeline = _dict(metadata.get(STATE_PIPELINE_METADATA_KEY))
    rolls = _dict(pipeline.get('dmContextPacket')).get(STORY_ROLLS_KEY)
    return rolls if isinstance(rolls, list) else []


def record_turn_story_rolls(turn: DmTurn, session_obj: Session) -> list[dict[str, Any]]:
    """Complete the turn's rolls with those made after narration and write them on the turn, its Dungeon
    Master log entry and its turn event."""

    metadata = safe_json_loads(turn.metadata_json, {})
    metadata = metadata if isinstance(metadata, dict) else {}
    pipeline = _dict(metadata.get(STATE_PIPELINE_METADATA_KEY))
    before = _dict(pipeline.get('dmContextPacket')).get(STORY_ROLLS_KEY)
    if not isinstance(before, list):
        return []
    state = safe_json_loads(session_obj.state_snapshot, {})
    after = story_rolls_after_narration(turn_id=turn.turn_id, pipeline=pipeline, state=state if isinstance(state, dict) else {})
    rolls = [*before, *numbered(after, turn_id=turn.turn_id, start=len(before) + 1)]
    metadata[STORY_ROLLS_KEY] = rolls
    turn.metadata_json = safe_json_dumps(metadata, {})
    event = TurnEvent.query.filter_by(turn_id=turn.turn_id, event_type=DM_RESPONSE_EVENT).order_by(TurnEvent.event_id.desc()).first()
    if event is not None:
        payload = safe_json_loads(event.payload_json, {})
        if isinstance(payload, dict) and isinstance(payload.get('metadata'), dict):
            payload['metadata'][STORY_ROLLS_KEY] = rolls
            event.payload_json = safe_json_dumps(payload, {})
    for entry in (
        SessionLogEntry.query.filter_by(session_id=turn.session_id, entry_type='dm')
        .order_by(SessionLogEntry.id.desc())
        .limit(20)
    ):
        entry_metadata = safe_json_loads(entry.metadata_json, {})
        if isinstance(entry_metadata, dict) and entry_metadata.get('turn_id') == turn.turn_id and entry.message.startswith('DM: '):
            entry_metadata[STORY_ROLLS_KEY] = rolls
            entry.metadata_json = safe_json_dumps(entry_metadata, {})
            break
    db.session.flush()
    return rolls
