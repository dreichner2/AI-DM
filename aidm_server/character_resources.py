"""Authoritative, JSON-safe spell resource and concentration helpers.

This module is deliberately storage-agnostic.  Callers persist the returned
``spellResources`` object inside the existing character-sheet JSON, avoiding a
second database source of truth while legacy sheets are migrated lazily.
"""

from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any, Mapping

from aidm_server.spellbook import (
    character_sheet_record,
    class_spell_archetype,
    known_spell,
    normalize_spellbook,
    spell_is_prepared,
)


RESOURCE_SCHEMA_VERSION = 1

FULL_CASTERS = frozenset({'bard', 'cleric', 'druid', 'sorcerer', 'wizard'})
HALF_CASTERS_ROUND_DOWN = frozenset({'paladin', 'ranger'})
HALF_CASTERS_ROUND_UP = frozenset({'artificer'})
PACT_CASTERS = frozenset({'warlock'})

# Standard multiclass spell-slot table, indexed by effective caster level.
STANDARD_SLOT_TABLE: dict[int, tuple[int, ...]] = {
    1: (2,),
    2: (3,),
    3: (4, 2),
    4: (4, 3),
    5: (4, 3, 2),
    6: (4, 3, 3),
    7: (4, 3, 3, 1),
    8: (4, 3, 3, 2),
    9: (4, 3, 3, 3, 1),
    10: (4, 3, 3, 3, 2),
    11: (4, 3, 3, 3, 2, 1),
    12: (4, 3, 3, 3, 2, 1),
    13: (4, 3, 3, 3, 2, 1, 1),
    14: (4, 3, 3, 3, 2, 1, 1),
    15: (4, 3, 3, 3, 2, 1, 1, 1),
    16: (4, 3, 3, 3, 2, 1, 1, 1),
    17: (4, 3, 3, 3, 2, 1, 1, 1, 1),
    18: (4, 3, 3, 3, 3, 1, 1, 1, 1),
    19: (4, 3, 3, 3, 3, 2, 1, 1, 1),
    20: (4, 3, 3, 3, 3, 2, 2, 1, 1),
}


def _bounded_level(value: Any, *, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(20, parsed))


def _nonnegative_int(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, parsed)


def _name_key(value: Any) -> str:
    return re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')


def _explicit_class_levels(raw_levels: Any) -> dict[str, int]:
    entries: list[tuple[Any, Any]] = []
    if isinstance(raw_levels, Mapping):
        entries = list(raw_levels.items())
    elif isinstance(raw_levels, list):
        for entry in raw_levels:
            if not isinstance(entry, Mapping):
                continue
            entries.append(
                (
                    entry.get('class') or entry.get('className') or entry.get('class_name') or entry.get('archetype'),
                    entry.get('level'),
                )
            )

    levels: dict[str, int] = {}
    for raw_class, raw_level in entries:
        archetype = class_spell_archetype(str(raw_class or ''))
        if not archetype:
            continue
        parsed = _nonnegative_int(raw_level)
        if parsed <= 0:
            continue
        levels[archetype] = min(20, levels.get(archetype, 0) + parsed)
    return levels


def _cap_class_levels(levels: Mapping[str, int], character_level: int) -> tuple[dict[str, int], bool]:
    """Cap malformed class splits to the authoritative total character level."""

    if sum(levels.values()) <= character_level:
        return dict(levels), False
    remaining = character_level
    capped: dict[str, int] = {}
    for archetype in sorted(levels):
        granted = min(_nonnegative_int(levels.get(archetype)), remaining)
        if granted > 0:
            capped[archetype] = granted
            remaining -= granted
        if remaining <= 0:
            break
    return capped, True


def normalize_spellcasting_class_levels(
    class_name: str | None,
    level: int = 1,
    *,
    class_levels: Any = None,
) -> tuple[dict[str, int], str]:
    """Resolve caster levels without over-granting ambiguous multiclasses.

    Structured ``class_levels`` is authoritative. A normal single class uses
    the character level. If a legacy free-text label appears multiclassed but
    has no level split, each recognized caster receives only one level; callers
    can remove that conservative cap by persisting an explicit mapping.
    """

    character_level = _bounded_level(level)
    explicit = _explicit_class_levels(class_levels)
    if explicit:
        capped, was_capped = _cap_class_levels(explicit, character_level)
        return capped, 'explicit_capped' if was_capped else 'explicit'

    label = str(class_name or '').strip()
    multiclass_parts = [
        part.strip()
        for part in re.split(r'\s*(?:/|\+|&|,|\band\b)\s*', label, flags=re.IGNORECASE)
        if part.strip()
    ]
    if len(multiclass_parts) > 1:
        parsed_levels: dict[str, int] = {}
        has_explicit_split = True
        for part in multiclass_parts:
            level_match = re.search(r'(?:\blevel\s*)?(\d{1,2})\s*$', part, flags=re.IGNORECASE)
            archetype = class_spell_archetype(re.sub(r'(?:\blevel\s*)?\d{1,2}\s*$', '', part, flags=re.IGNORECASE))
            if not archetype:
                continue
            if not level_match:
                has_explicit_split = False
                continue
            parsed_levels[archetype] = parsed_levels.get(archetype, 0) + _bounded_level(level_match.group(1))
        if parsed_levels and has_explicit_split:
            capped, was_capped = _cap_class_levels(parsed_levels, character_level)
            return capped, 'parsed_multiclass_capped' if was_capped else 'parsed_multiclass'

        conservative: dict[str, int] = {}
        for part in multiclass_parts:
            archetype = class_spell_archetype(part)
            if archetype and sum(conservative.values()) < character_level:
                conservative.setdefault(archetype, 1)
        return conservative, 'conservative_multiclass'

    archetype = class_spell_archetype(label)
    return ({archetype: character_level} if archetype else {}), ('single_class' if archetype else 'none')


def _standard_slot_maxima(effective_caster_level: int) -> dict[str, int]:
    if effective_caster_level <= 0:
        return {}
    row = STANDARD_SLOT_TABLE[min(20, effective_caster_level)]
    return {str(slot_level): maximum for slot_level, maximum in enumerate(row, start=1) if maximum > 0}


def _pact_slot_maxima(warlock_level: int) -> dict[str, int]:
    if warlock_level <= 0:
        return {'slotLevel': 0, 'max': 0}
    if warlock_level == 1:
        return {'slotLevel': 1, 'max': 1}
    if warlock_level <= 4:
        return {'slotLevel': 1 if warlock_level == 2 else 2, 'max': 2}
    if warlock_level <= 6:
        return {'slotLevel': 3, 'max': 2}
    if warlock_level <= 8:
        return {'slotLevel': 4, 'max': 2}
    if warlock_level <= 10:
        return {'slotLevel': 5, 'max': 2}
    return {'slotLevel': 5, 'max': 3 if warlock_level <= 16 else 4}


def _mystic_arcanum_maxima(warlock_level: int) -> dict[str, int]:
    return {
        str(spell_level): 1
        for spell_level, unlock_level in ((6, 11), (7, 13), (8, 15), (9, 17))
        if warlock_level >= unlock_level
    }


def derive_spell_slot_maxima(
    class_name: str | None,
    level: int = 1,
    *,
    class_levels: Any = None,
) -> dict[str, Any]:
    levels, inference = normalize_spellcasting_class_levels(
        class_name,
        level,
        class_levels=class_levels,
    )
    effective_caster_level = sum(levels.get(name, 0) for name in FULL_CASTERS)
    if inference == 'single_class' and any(levels.get(name, 0) for name in HALF_CASTERS_ROUND_DOWN):
        # A single-class paladin or ranger uses its own slot table. That table
        # matches ceil(level / 2), except neither class casts at level 1. The
        # floor rule applies only once those classes participate in multiclass
        # spell-slot derivation.
        half_level = max(levels.get(name, 0) for name in HALF_CASTERS_ROUND_DOWN)
        effective_caster_level += (half_level + 1) // 2 if half_level >= 2 else 0
    else:
        effective_caster_level += sum(levels.get(name, 0) // 2 for name in HALF_CASTERS_ROUND_DOWN)
    effective_caster_level += sum((levels.get(name, 0) + 1) // 2 for name in HALF_CASTERS_ROUND_UP)
    warlock_level = levels.get('warlock', 0)
    standard = _standard_slot_maxima(effective_caster_level)
    pact = _pact_slot_maxima(warlock_level)
    arcanum = _mystic_arcanum_maxima(warlock_level)
    pools = int(bool(standard)) + int(pact['max'] > 0)
    casting_mode = 'hybrid' if pools > 1 else 'standard' if standard else 'pact' if pact['max'] > 0 else 'none'
    return {
        'schemaVersion': RESOURCE_SCHEMA_VERSION,
        'classLevels': levels,
        'classLevelInference': inference,
        'effectiveCasterLevel': effective_caster_level,
        'castingMode': casting_mode,
        'standard': standard,
        'pact': pact,
        'mysticArcanum': arcanum,
    }


def _resource_payload(raw_resources: Any) -> tuple[dict[str, Any], bool]:
    if not isinstance(raw_resources, dict):
        return {}, False
    for key in ('spellResources', 'spell_resources'):
        nested = raw_resources.get(key)
        if isinstance(nested, dict):
            return deepcopy(nested), True
    return deepcopy(raw_resources), True


def _raw_slot_map(raw_slots: Any) -> dict[str, Any]:
    if isinstance(raw_slots, list):
        return {str(index): value for index, value in enumerate(raw_slots, start=1)}
    if isinstance(raw_slots, dict):
        return {str(key): value for key, value in raw_slots.items()}
    return {}


def _slot_current(raw_entry: Any, maximum: int, *, initialize_full: bool) -> int:
    if isinstance(raw_entry, dict):
        if 'current' in raw_entry:
            current = _nonnegative_int(raw_entry.get('current'))
        elif 'remaining' in raw_entry:
            current = _nonnegative_int(raw_entry.get('remaining'))
        elif 'available' in raw_entry:
            current = _nonnegative_int(raw_entry.get('available'))
        elif 'used' in raw_entry:
            current = maximum - _nonnegative_int(raw_entry.get('used'))
        else:
            current = maximum if initialize_full else 0
    elif raw_entry is None:
        current = maximum if initialize_full else 0
    else:
        current = _nonnegative_int(raw_entry)
    return max(0, min(maximum, current))


def normalize_concentration(raw_concentration: Any) -> dict[str, Any] | None:
    if not isinstance(raw_concentration, dict) or raw_concentration.get('active') is False:
        return None
    spell_name = str(
        raw_concentration.get('spellName')
        or raw_concentration.get('spell_name')
        or raw_concentration.get('name')
        or ''
    ).strip()
    spell_id = str(
        raw_concentration.get('spellId')
        or raw_concentration.get('spell_id')
        or ''
    ).strip()
    if not spell_name and not spell_id:
        return None
    payload: dict[str, Any] = {
        'active': True,
        'spellId': spell_id or f'spell_{_name_key(spell_name)}',
        'spellName': spell_name or spell_id,
    }
    actor_id = str(raw_concentration.get('casterActorId') or raw_concentration.get('caster_actor_id') or '').strip()
    if actor_id:
        payload['casterActorId'] = actor_id
    started_at_turn = _nonnegative_int(
        raw_concentration.get('startedAtTurn', raw_concentration.get('started_at_turn')),
        default=0,
    )
    if started_at_turn > 0:
        payload['startedAtTurn'] = started_at_turn
    raw_targets = raw_concentration.get('targetIds') or raw_concentration.get('target_ids') or []
    if isinstance(raw_targets, list):
        target_ids = list(dict.fromkeys(str(value).strip() for value in raw_targets if str(value or '').strip()))
        if target_ids:
            payload['targetIds'] = target_ids
    return payload


def normalize_spell_resources(
    raw_resources: Any,
    *,
    class_name: str | None,
    level: int = 1,
    class_levels: Any = None,
) -> dict[str, Any]:
    raw, raw_present = _resource_payload(raw_resources)
    resolved_class_levels = class_levels
    if resolved_class_levels is None:
        _, label_inference = normalize_spellcasting_class_levels(class_name, level)
        persisted_levels = raw.get('classLevels', raw.get('class_levels'))
        if label_inference == 'conservative_multiclass' and _explicit_class_levels(persisted_levels):
            # An ambiguous legacy label cannot reproduce an exact multiclass
            # split after reload. Preserve a previously authoritative split;
            # new sheets without one still receive the conservative fallback.
            resolved_class_levels = persisted_levels
    maxima = derive_spell_slot_maxima(class_name, level, class_levels=resolved_class_levels)
    raw_slots = _raw_slot_map(raw.get('slots', raw.get('spellSlots', raw.get('spell_slots'))))
    slots = {
        slot_level: {
            'current': _slot_current(raw_slots.get(slot_level), maximum, initialize_full=True),
            'max': maximum,
        }
        for slot_level, maximum in maxima['standard'].items()
    }

    raw_pact = raw.get('pactSlots', raw.get('pact_slots'))
    pact_max = maxima['pact']['max']
    pact_level = maxima['pact']['slotLevel']
    pact_current = _slot_current(raw_pact, pact_max, initialize_full=True) if pact_max else 0

    raw_arcanum = _raw_slot_map(raw.get('mysticArcanum', raw.get('mystic_arcanum')))
    arcanum = {
        spell_level: {
            'current': _slot_current(raw_arcanum.get(spell_level), maximum, initialize_full=True),
            'max': maximum,
        }
        for spell_level, maximum in maxima['mysticArcanum'].items()
    }

    concentration = normalize_concentration(raw.get('concentration'))
    return {
        'schemaVersion': RESOURCE_SCHEMA_VERSION,
        'revision': _nonnegative_int(raw.get('revision')) if raw_present else 0,
        'classLevels': maxima['classLevels'],
        'classLevelInference': maxima['classLevelInference'],
        'effectiveCasterLevel': maxima['effectiveCasterLevel'],
        'castingMode': maxima['castingMode'],
        'slots': slots,
        'pactSlots': {
            'current': pact_current,
            'max': pact_max,
            'slotLevel': pact_level,
        },
        'mysticArcanum': arcanum,
        'concentration': concentration,
    }


def spell_resources_from_character_sheet(
    raw_sheet: Any,
    *,
    class_name: str | None,
    level: int = 1,
    class_levels: Any = None,
) -> dict[str, Any]:
    sheet = character_sheet_record(raw_sheet)
    raw_resources = sheet.get('spellResources')
    if raw_resources is None:
        raw_resources = sheet.get('spell_resources')
    if raw_resources is None:
        raw_resources = {'spellSlots': sheet.get('spellSlots', sheet.get('spell_slots'))}
    return normalize_spell_resources(
        raw_resources,
        class_name=class_name,
        level=level,
        class_levels=class_levels,
    )


def ensure_character_sheet_spell_resources(
    raw_sheet: Any,
    *,
    class_name: str | None,
    level: int = 1,
    class_levels: Any = None,
) -> tuple[dict[str, Any], bool]:
    sheet = character_sheet_record(raw_sheet)
    before = json.dumps(sheet, sort_keys=True, default=str)
    raw_spellbook = sheet.get('spellbook')
    if raw_spellbook is None:
        raw_spellbook = sheet.get('knownSpells') or sheet.get('known_spells') or sheet.get('spells') or []
    sheet['spellbook'] = normalize_spellbook(raw_spellbook, class_name=class_name)
    sheet['spells'] = [spell['name'] for spell in sheet['spellbook']['knownSpells']]
    sheet['spellResources'] = spell_resources_from_character_sheet(
        sheet,
        class_name=class_name,
        level=level,
        class_levels=class_levels,
    )
    for legacy_key in ('spell_resources', 'spellSlots', 'spell_slots'):
        sheet.pop(legacy_key, None)
    after = json.dumps(sheet, sort_keys=True, default=str)
    return sheet, before != after


def _failed_cast(
    code: str,
    reason: str,
    *,
    spell: dict[str, Any] | None,
    resources: dict[str, Any],
) -> dict[str, Any]:
    return {
        'legal': False,
        'errorCode': code,
        'reason': reason,
        'spell': spell,
        'castLevel': None,
        'resource': None,
        'resources': resources,
    }


def spell_cast_legality(
    raw_spellbook: Any,
    raw_resources: Any,
    *,
    spell_name_or_id: Any,
    class_name: str | None,
    level: int = 1,
    cast_level: int | None = None,
    class_levels: Any = None,
    resource_pool: str = 'auto',
) -> dict[str, Any]:
    spellbook = normalize_spellbook(raw_spellbook, class_name=class_name)
    resources = normalize_spell_resources(
        raw_resources,
        class_name=class_name,
        level=level,
        class_levels=class_levels,
    )
    spell = known_spell(spellbook, spell_name_or_id)
    if not spell:
        return _failed_cast(
            'spell_not_known',
            'The spell is not in the character\'s known spellbook.',
            spell=None,
            resources=resources,
        )
    if not spell_is_prepared(spellbook, spell.get('id') or spell.get('name')):
        return _failed_cast(
            'spell_not_prepared',
            'The spell is known but is not currently prepared.',
            spell=spell,
            resources=resources,
        )

    spell_level = _nonnegative_int(spell.get('level'))
    if spell_level == 0:
        return {
            'legal': True,
            'errorCode': None,
            'reason': 'Cantrips do not consume spell slots.',
            'spell': spell,
            'castLevel': 0,
            'resource': {'pool': 'cantrip', 'slotLevel': 0},
            'resources': resources,
        }

    requested_level = spell_level if cast_level is None else _nonnegative_int(cast_level)
    if requested_level < spell_level or requested_level > 9:
        return _failed_cast(
            'invalid_cast_level',
            f'{spell["name"]} must be cast at level {spell_level} or higher.',
            spell=spell,
            resources=resources,
        )
    normalized_pool = str(resource_pool or 'auto').strip().lower()
    if normalized_pool not in {'auto', 'standard', 'pact', 'arcanum'}:
        return _failed_cast(
            'invalid_resource_pool',
            'Spell resource pool must be auto, standard, pact, or arcanum.',
            spell=spell,
            resources=resources,
        )

    if normalized_pool in {'auto', 'standard'}:
        standard_levels = sorted(
            int(slot_level)
            for slot_level, slot in resources['slots'].items()
            if int(slot_level) >= requested_level and _nonnegative_int(slot.get('current')) > 0
        )
        if cast_level is not None:
            standard_levels = [slot_level for slot_level in standard_levels if slot_level == requested_level]
        if standard_levels:
            selected_level = standard_levels[0]
            return {
                'legal': True,
                'errorCode': None,
                'reason': f'Consume one level {selected_level} spell slot.',
                'spell': spell,
                'castLevel': selected_level,
                'resource': {'pool': 'standard', 'slotLevel': selected_level},
                'resources': resources,
            }

    pact = resources['pactSlots']
    pact_level_matches = cast_level is None or pact['slotLevel'] == requested_level
    if (
        normalized_pool in {'auto', 'pact'}
        and pact['current'] > 0
        and pact['slotLevel'] >= requested_level
        and pact_level_matches
    ):
        return {
            'legal': True,
            'errorCode': None,
            'reason': f'Consume one level {pact["slotLevel"]} pact slot.',
            'spell': spell,
            'castLevel': pact['slotLevel'],
            'resource': {'pool': 'pact', 'slotLevel': pact['slotLevel']},
            'resources': resources,
        }

    arcanum_entry = resources['mysticArcanum'].get(str(requested_level))
    if normalized_pool in {'auto', 'arcanum'} and arcanum_entry and arcanum_entry['current'] > 0:
        return {
            'legal': True,
            'errorCode': None,
            'reason': f'Consume the level {requested_level} Mystic Arcanum use.',
            'spell': spell,
            'castLevel': requested_level,
            'resource': {'pool': 'arcanum', 'slotLevel': requested_level},
            'resources': resources,
        }

    return _failed_cast(
        'spell_slot_exhausted',
        f'No legal level {requested_level} or higher spell resource remains for {spell["name"]}.',
        spell=spell,
        resources=resources,
    )


def set_concentration(
    raw_resources: Any,
    *,
    spell: Mapping[str, Any] | str,
    class_name: str | None,
    level: int = 1,
    class_levels: Any = None,
    caster_actor_id: str | None = None,
    target_ids: list[str] | tuple[str, ...] | None = None,
    started_at_turn: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    resources = normalize_spell_resources(
        raw_resources,
        class_name=class_name,
        level=level,
        class_levels=class_levels,
    )
    previous = deepcopy(resources.get('concentration'))
    if isinstance(spell, Mapping):
        spell_name = str(spell.get('name') or spell.get('spellName') or '').strip()
        spell_id = str(spell.get('id') or spell.get('spellId') or '').strip()
    else:
        spell_name = str(spell or '').strip()
        spell_id = ''
    if not spell_name and not spell_id:
        raise ValueError('Concentration requires a spell name or spell id.')
    concentration: dict[str, Any] = {
        'active': True,
        'spellId': spell_id or f'spell_{_name_key(spell_name)}',
        'spellName': spell_name or spell_id,
    }
    if caster_actor_id:
        concentration['casterActorId'] = str(caster_actor_id)
    if target_ids:
        concentration['targetIds'] = list(dict.fromkeys(str(target).strip() for target in target_ids if str(target).strip()))
    if started_at_turn is not None and _nonnegative_int(started_at_turn) > 0:
        concentration['startedAtTurn'] = _nonnegative_int(started_at_turn)
    resources['concentration'] = concentration
    resources['revision'] += 1
    return resources, previous


def clear_concentration(
    raw_resources: Any,
    *,
    class_name: str | None,
    level: int = 1,
    class_levels: Any = None,
    expected_spell_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    resources = normalize_spell_resources(
        raw_resources,
        class_name=class_name,
        level=level,
        class_levels=class_levels,
    )
    previous = deepcopy(resources.get('concentration'))
    if expected_spell_id and previous and _name_key(previous.get('spellId')) != _name_key(expected_spell_id):
        return resources, None
    if previous is not None:
        resources['concentration'] = None
        resources['revision'] += 1
    return resources, previous


def consume_spell_cast(
    raw_spellbook: Any,
    raw_resources: Any,
    *,
    spell_name_or_id: Any,
    class_name: str | None,
    level: int = 1,
    cast_level: int | None = None,
    class_levels: Any = None,
    resource_pool: str = 'auto',
    concentration: bool | None = None,
    caster_actor_id: str | None = None,
    target_ids: list[str] | tuple[str, ...] | None = None,
    started_at_turn: int | None = None,
) -> dict[str, Any]:
    legality = spell_cast_legality(
        raw_spellbook,
        raw_resources,
        spell_name_or_id=spell_name_or_id,
        class_name=class_name,
        level=level,
        cast_level=cast_level,
        class_levels=class_levels,
        resource_pool=resource_pool,
    )
    resources = deepcopy(legality['resources'])
    if not legality['legal']:
        return {'ok': False, 'legality': legality, 'resources': resources, 'consumed': None, 'replacedConcentration': None}

    resource = legality['resource']
    consumed = None
    if resource['pool'] == 'standard':
        slot = resources['slots'][str(resource['slotLevel'])]
        slot['current'] -= 1
        consumed = deepcopy(resource)
    elif resource['pool'] == 'pact':
        resources['pactSlots']['current'] -= 1
        consumed = deepcopy(resource)
    elif resource['pool'] == 'arcanum':
        resources['mysticArcanum'][str(resource['slotLevel'])]['current'] -= 1
        consumed = deepcopy(resource)

    spell = legality['spell']
    inferred_concentration = bool(
        spell.get('concentration') is True
        or 'concentration' in {str(tag).strip().lower() for tag in spell.get('tags') or []}
    )
    replaced_concentration = None
    if concentration is True or (concentration is None and inferred_concentration):
        resources, replaced_concentration = set_concentration(
            resources,
            spell=spell,
            class_name=class_name,
            level=level,
            class_levels=class_levels,
            caster_actor_id=caster_actor_id,
            target_ids=target_ids,
            started_at_turn=started_at_turn,
        )
    else:
        resources['revision'] += 1
    return {
        'ok': True,
        'legality': {**legality, 'resources': deepcopy(resources)},
        'resources': resources,
        'consumed': consumed,
        'replacedConcentration': replaced_concentration,
    }


def restore_spell_resources(
    raw_resources: Any,
    *,
    rest_type: str,
    class_name: str | None,
    level: int = 1,
    class_levels: Any = None,
) -> dict[str, Any]:
    normalized_rest = str(rest_type or '').strip().lower().replace('-', '_').replace(' ', '_')
    if normalized_rest not in {'short', 'short_rest', 'long', 'long_rest'}:
        raise ValueError('rest_type must be short_rest or long_rest.')
    is_long_rest = normalized_rest in {'long', 'long_rest'}
    resources = normalize_spell_resources(
        raw_resources,
        class_name=class_name,
        level=level,
        class_levels=class_levels,
    )
    before = deepcopy(resources)
    if is_long_rest:
        for slot in resources['slots'].values():
            slot['current'] = slot['max']
        for arcanum in resources['mysticArcanum'].values():
            arcanum['current'] = arcanum['max']
        resources['concentration'] = None
    # Pact magic is the deliberate exception: it refreshes on either rest.
    resources['pactSlots']['current'] = resources['pactSlots']['max']
    if resources != before:
        resources['revision'] = before['revision'] + 1
    return resources
