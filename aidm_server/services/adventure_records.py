"""Records kept across adventures: per account (or, with sign-in off, per local workspace) and pack, the endings
reached, the secrets found and how often it was finished; and the ``Your chronicle`` screen built from them.

An adventure is finished when a turn reaches its ending (the story beat ``ending``). Every hero at the table counts:
the record goes to their player's account, or to the workspace when the player has none, once per adventure.

Secrets (:func:`pack_secrets`): every scene object that starts hidden (``object:<id>``), every topic with a ``check``
or a ``requires.dispositionAtLeast`` (``topic:<npcId>:<topicId>``), every tie line (``tie:<id>``) and every clue the
pack marks ``"secret": true`` (``clue:<id>``). One is found (:func:`found_secrets`) once revealed, told, fired or
discovered.
"""

from __future__ import annotations

from typing import Any, Iterable

from aidm_server.database import db
from aidm_server.game_state.models import parse_actor_player_id
from aidm_server.models import AdventureRecord, Player, safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_visibility import ending_variant
from aidm_server.services.tie_lines import THREADS_KEY, TIE_LINES_KEY
from aidm_server.time_utils import utc_now

OBJECT_COLLECTIONS = ('interactables', 'hazards')
UNKNOWN_CLUE_STATUSES = frozenset({'hidden', 'undiscovered', 'unknown'})


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def owner_key(*, account_id: int | None, workspace_id: str | None) -> str:
    """``account:<id>`` for a signed-in account, else ``workspace:<id>`` (the local profile)."""

    return f'account:{account_id}' if account_id is not None else f"workspace:{_text(workspace_id) or 'owner'}"


# Secrets --------------------------------------------------------------------------------------------------------------


def _location_objects(location: dict[str, Any]) -> list[dict[str, Any]]:
    scene_state = _dict(location.get('sceneState'))
    return [entry for key in OBJECT_COLLECTIONS for entry in [*_dicts(location.get(key)), *_dicts(scene_state.get(key))]]


def pack_secrets(state: dict[str, Any]) -> set[str]:
    """Every secret the adventure's pack holds (see the module docstring)."""

    pack = _dict(state.get('campaignPack'))
    catalog = _dict(pack.get('catalog'))
    secrets = {
        f"object:{_text(entry.get('id'))}"
        for location in _dicts(catalog.get('locations'))
        for entry in _location_objects(location)
        if entry.get('hidden') is True and _text(entry.get('id'))
    }
    for npc in _dicts(catalog.get('npcs')):
        for topic in _dicts(npc.get('topics')):
            requires = _dict(topic.get('requires'))
            if isinstance(topic.get('check'), dict) or isinstance(requires.get('dispositionAtLeast'), int):
                secrets.add(f"topic:{_text(npc.get('id'))}:{_text(topic.get('id'))}")
    secrets.update(f"tie:{_text(line.get('id'))}" for line in _dicts(pack.get(TIE_LINES_KEY)) if _text(line.get('id')))
    secrets.update(f"clue:{_text(clue.get('id'))}" for clue in _dicts(catalog.get('clues')) if clue.get('secret') is True)
    return secrets


def found_secrets(state: dict[str, Any]) -> set[str]:
    """The pack's secrets this adventure has found."""

    secrets = pack_secrets(state)
    scene = _dict(state.get('currentScene'))
    objects = [
        *(entry for key in OBJECT_COLLECTIONS for entry in _dicts(scene.get(key))),
        *(entry for location in _dicts(state.get('locations')) for entry in _location_objects(location)),
    ]
    found = {f"object:{_text(entry.get('id'))}" for entry in objects if entry.get('hidden') is not True}
    for person in [*_dicts(state.get('knownNpcs')), *_dicts(state.get('partyNpcs'))]:
        told = _dict(person.get('talk')).get('told')
        found.update(f"topic:{_text(person.get('id'))}:{_text(topic_id)}" for topic_id in told or [])
    found.update(f"tie:{_text(record.get('lineId'))}" for record in _dicts(_dict(state.get('campaignPack')).get(THREADS_KEY)))
    found.update(
        f"clue:{_text(clue.get('id'))}" for clue in _dicts(state.get('clues')) if _text(clue.get('status')).lower() not in UNKNOWN_CLUE_STATUSES
    )
    return found & secrets


# Recording a finished adventure ---------------------------------------------------------------------------------------


def _reached_ending(state: dict[str, Any]) -> dict[str, Any] | None:
    """The ending the adventure reached: its terminal checkpoint, the variant's title and the flags that chose it."""

    pack = _dict(state.get('campaignPack'))
    checkpoint = next((record for record in _dicts(pack.get('checkpoints')) if _text(record.get('id')) == _text(pack.get('activeCheckpointId'))), None)
    if checkpoint is None:
        return None
    flags = _dict(state.get('flags'))
    variant = ending_variant(checkpoint, flags) or {}
    chosen = next(
        (entry for entry in _dicts(checkpoint.get('endingVariants')) if _text(entry.get('title')) == variant.get('title')),
        {},
    )
    when = _dict(chosen.get('when'))
    keys = [_text(entry.get('flagKey')) for entry in _dicts(when.get('anyOf'))] if isinstance(when.get('anyOf'), list) else [_text(when.get('flagKey'))]
    return {
        'checkpointId': _text(checkpoint.get('id')),
        'title': variant.get('title') or _text(checkpoint.get('playerTitle')) or _text(checkpoint.get('title')),
        'flags': [key for key in keys if key],
    }


def table_owner_keys(state: dict[str, Any], *, workspace_id: str | None) -> list[str]:
    """The records a finished adventure goes to: one per account at the table, the workspace for players without."""

    player_ids = [parse_actor_player_id(hero.get('id')) for hero in _dicts(state.get('playerCharacters'))]
    players = Player.query.filter(Player.player_id.in_([player_id for player_id in player_ids if player_id])).all() if player_ids else []
    return list(dict.fromkeys(owner_key(account_id=player.account_id, workspace_id=workspace_id) for player in players))


def record_completion(state: dict[str, Any], *, campaign_id: int, workspace_id: str | None) -> list[AdventureRecord]:
    """Write the finished adventure into each table owner's record for its pack, once per adventure."""

    pack = _dict(state.get('campaignPack'))
    pack_id = _text(pack.get('packId'))
    ending = _reached_ending(state)
    if not pack_id or ending is None:
        return []
    found = sorted(found_secrets(state))
    total = len(pack_secrets(state))
    records = []
    for key in table_owner_keys(state, workspace_id=workspace_id):
        record = AdventureRecord.query.filter_by(owner_key=key, pack_id=pack_id).first()
        if record is None:
            record = AdventureRecord(owner_key=key, pack_id=pack_id)
            db.session.add(record)
        completions = _dicts(safe_json_loads(record.completions_json, []))
        if any(entry.get('campaignId') == campaign_id for entry in completions):
            continue
        endings = _dicts(safe_json_loads(record.endings_json, []))
        if not any(entry.get('title') == ending['title'] for entry in endings):
            endings.append(ending)
        record.pack_title = _text(pack.get('title'))[:160]
        record.endings_json = safe_json_dumps(endings, [])
        record.secrets_found_json = safe_json_dumps(sorted({*safe_json_loads(record.secrets_found_json, []), *found}), [])
        record.secrets_total = total
        record.completions_json = safe_json_dumps([*completions, {'campaignId': campaign_id, 'ending': ending['title'], 'at': utc_now().isoformat()}], [])
        record.times_completed = len(completions) + 1
        records.append(record)
    db.session.flush()
    return records


# Your chronicle -------------------------------------------------------------------------------------------------------


def chronicle(owner_keys: Iterable[str]) -> list[dict[str, Any]]:
    """``Your chronicle``: each finished adventure (pack), newest first, with the endings reached and how many of its
    secrets were never found."""

    rows = AdventureRecord.query.filter(AdventureRecord.owner_key.in_(list(owner_keys)), AdventureRecord.times_completed > 0).all()
    rows.sort(key=lambda row: row.updated_at or row.created_at, reverse=True)
    return [
        {
            'packId': row.pack_id,
            'title': row.pack_title or row.pack_id,
            'endings': [_text(entry.get('title')) for entry in _dicts(safe_json_loads(row.endings_json, [])) if _text(entry.get('title'))],
            'timesCompleted': int(row.times_completed or 0),
            'neverFound': max(0, int(row.secrets_total or 0) - len(safe_json_loads(row.secrets_found_json, []))),
        }
        for row in rows
    ]

