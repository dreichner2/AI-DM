from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.combat.state import instantiate_creature, player_combat_participant
from aidm_server.game_state.models import stable_slug


def materialize_campaign_pack_combat_start(state: dict[str, Any], change: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(change, dict) or str(change.get('type') or '') != 'combat.start':
        return change
    pack = state.get('campaignPack') if isinstance(state.get('campaignPack'), dict) else {}
    pack_id = _text(pack.get('packId') or pack.get('pack_id'))
    if not pack_id:
        return change

    checkpoint = _active_checkpoint(state, pack)
    requested_encounter_id = _requested_encounter_id(change, checkpoint)
    if not requested_encounter_id:
        return change
    encounter = _pack_record(pack, 'encounters', requested_encounter_id)
    if not encounter:
        return change

    enemies = _instantiate_pack_enemies(pack, encounter, turn_id=_positive_int(change.get('turnId') or change.get('turn_id')))
    if not enemies:
        return change

    updated = deepcopy(change)
    combat = updated.get('combat') if isinstance(updated.get('combat'), dict) else {}
    participants = _player_participants(state, combat)
    participants.extend(enemies)
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    flags.update(
        {
            'source': 'campaign_pack',
            'packId': pack_id,
            'campaignPackId': pack_id,
            'campaignPackEncounterId': _record_id(encounter),
            'campaignPackCheckpointIds': _checkpoint_ids_for_encounter(checkpoint, encounter),
            'campaignPackEnemyIds': [_text(enemy.get('campaignPackEnemyId')) for enemy in enemies if enemy.get('campaignPackEnemyId')],
        }
    )
    completion = encounter.get('completion') if isinstance(encounter.get('completion'), dict) else {}
    allowed_outcomes = _string_list(
        completion.get('anyOf')
        or completion.get('any_of')
        or completion.get('outcomes')
        or completion.get('allowedOutcomes')
        or completion.get('allowed_outcomes')
    )
    if allowed_outcomes:
        flags['campaignPackAllowedOutcomes'] = allowed_outcomes

    combat.update(
        {
            'status': combat.get('status') or 'active',
            'round': combat.get('round') or 1,
            'participants': participants,
            'flags': flags,
        }
    )
    if not isinstance(combat.get('encounterGoal'), dict):
        combat['encounterGoal'] = {
            'type': 'campaign_pack',
            'encounterId': _record_id(encounter),
            'title': _text(encounter.get('title') or encounter.get('name')),
            'summary': _text(encounter.get('summary') or encounter.get('description')),
            'allowedOutcomes': allowed_outcomes,
        }
    updated['combat'] = combat
    updated['source'] = 'campaign_pack'
    updated['packId'] = pack_id
    updated.setdefault('campaignPackEncounterId', _record_id(encounter))
    updated.setdefault('encounterId', _record_id(encounter))
    return updated


def _active_checkpoint(state: dict[str, Any], pack: dict[str, Any]) -> dict[str, Any]:
    flags = state.get('flags') if isinstance(state.get('flags'), dict) else {}
    active_id = _text(
        pack.get('activeCheckpointId')
        or pack.get('active_checkpoint_id')
        or pack.get('currentCheckpointId')
        or flags.get('campaignPackActiveCheckpointId')
    )
    checkpoints = [checkpoint for checkpoint in (pack.get('checkpoints') or []) if isinstance(checkpoint, dict)]
    return next((checkpoint for checkpoint in checkpoints if _record_id(checkpoint) == active_id), {}) if active_id else {}


def _requested_encounter_id(change: dict[str, Any], checkpoint: dict[str, Any]) -> str:
    combat = change.get('combat') if isinstance(change.get('combat'), dict) else {}
    flags = combat.get('flags') if isinstance(combat.get('flags'), dict) else {}
    for value in (
        change.get('campaignPackEncounterId'),
        change.get('campaign_pack_encounter_id'),
        change.get('encounterId'),
        change.get('encounter_id'),
        combat.get('campaignPackEncounterId'),
        combat.get('encounterId'),
        flags.get('campaignPackEncounterId'),
        flags.get('encounterId'),
    ):
        if _text(value):
            return _text(value)
    has_explicit_enemy = any(
        isinstance(participant, dict) and participant.get('team') == 'enemy'
        for participant in (combat.get('participants') or change.get('participants') or [])
    )
    if has_explicit_enemy:
        return ''
    encounter_ids = _string_list(
        checkpoint.get('encounterIds')
        or checkpoint.get('encounter_ids')
        or checkpoint.get('encounters')
        or checkpoint.get('encounterId')
    )
    return encounter_ids[0] if encounter_ids else ''


def _pack_record(pack: dict[str, Any], key: str, record_id: str) -> dict[str, Any]:
    catalog = pack.get('catalog') if isinstance(pack.get('catalog'), dict) else {}
    records = catalog.get(key)
    if not isinstance(records, list):
        records = pack.get(key) if isinstance(pack.get(key), list) else []
    record_key = _text(record_id)
    for record in records:
        if isinstance(record, dict) and _record_id(record) == record_key:
            return record
    return {}


def _instantiate_pack_enemies(pack: dict[str, Any], encounter: dict[str, Any], *, turn_id: int | None) -> list[dict[str, Any]]:
    enemy_specs = _encounter_enemy_specs(encounter)
    participants: list[dict[str, Any]] = []
    sequence = 1
    for enemy_id, count in enemy_specs:
        enemy = _pack_record(pack, 'enemies', enemy_id)
        if not enemy:
            continue
        for _index in range(max(1, count)):
            participant = instantiate_creature(
                enemy,
                instance_id=f"enemy_{stable_slug(enemy_id)}_{sequence}",
                team='enemy',
                current_turn=turn_id,
            )
            participant['source'] = 'campaign_pack'
            participant['campaignPackEnemyId'] = _record_id(enemy)
            participant['campaignPackEncounterId'] = _record_id(encounter)
            participants.append(participant)
            sequence += 1
    return participants


def _encounter_enemy_specs(encounter: dict[str, Any]) -> list[tuple[str, int]]:
    specs: list[tuple[str, int]] = []
    for enemy_id in _string_list(encounter.get('enemyIds') or encounter.get('enemy_ids')):
        specs.append((enemy_id, 1))
    groups = encounter.get('enemyGroups') or encounter.get('enemy_groups') or encounter.get('enemies')
    if isinstance(groups, list):
        for group in groups:
            if isinstance(group, str):
                specs.append((group, 1))
            elif isinstance(group, dict):
                enemy_id = _text(group.get('enemyId') or group.get('enemy_id') or group.get('id') or group.get('creatureId'))
                if enemy_id:
                    specs.append((enemy_id, max(1, _positive_int(group.get('count')) or 1)))
    deduped: list[tuple[str, int]] = []
    seen: set[str] = set()
    for enemy_id, count in specs:
        key = _text(enemy_id)
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append((enemy_id, count))
    return deduped


def _player_participants(state: dict[str, Any], combat: dict[str, Any]) -> list[dict[str, Any]]:
    existing_players = [
        participant
        for participant in (combat.get('participants') or [])
        if isinstance(participant, dict) and participant.get('team') in {'player', 'ally'}
    ]
    if existing_players:
        return existing_players
    return [
        player_combat_participant(actor)
        for actor in (state.get('playerCharacters') or [])
        if isinstance(actor, dict)
    ]


def _checkpoint_ids_for_encounter(checkpoint: dict[str, Any], encounter: dict[str, Any]) -> list[str]:
    ids = _string_list(encounter.get('checkpointIds') or encounter.get('checkpoint_ids'))
    checkpoint_id = _record_id(checkpoint)
    if checkpoint_id and checkpoint_id not in ids:
        ids.insert(0, checkpoint_id)
    return ids


def _record_id(record: dict[str, Any]) -> str:
    return _text(record.get('id') or record.get('encounterId') or record.get('checkpointId') or record.get('creatureId'))


def _text(value: Any) -> str:
    return str(value or '').strip()


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_values = value
    elif isinstance(value, str):
        raw_values = [item.strip() for item in value.replace(';', ',').split(',')]
    elif value in (None, ''):
        raw_values = []
    else:
        raw_values = [value]
    result: list[str] = []
    seen: set[str] = set()
    for raw_value in raw_values:
        text = _text(raw_value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result
