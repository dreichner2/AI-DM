from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from aidm_server.creatures.repository import save_bestiary_entry
from aidm_server.database import db
from aidm_server.game_state.models import stable_slug
from aidm_server.models import (
    Campaign,
    CampaignSegment,
    InstalledCampaignPack,
    Session,
    SessionState,
    World,
    safe_json_dumps,
)
from aidm_server.operator_audit import record_operator_action
from aidm_server.response_dtos import campaign_payload, session_payload
from aidm_server.services.campaign_pack_storage import sync_campaign_pack_progress, upsert_campaign_pack_definition
from aidm_server.time_utils import utc_now
from aidm_server.validation import coerce_int


MAX_PACK_RECORDS = 250
MAX_PACK_CHECKPOINTS = 250
MAX_PACK_ENEMIES = 150
MAX_ID_LENGTH = 120
MAX_NAME_LENGTH = 160
MAX_TITLE_LENGTH = 120
MAX_SESSION_NAME_LENGTH = 80
MAX_TEXT_LENGTH = 4_000
MAX_NESTED_TEXT_LENGTH = 2_000
MAX_DICT_KEYS = 80
MAX_LIST_ITEMS = 150
MAX_NESTED_DEPTH = 5
CAMPAIGN_PACK_SCHEMA_PATH = Path(__file__).resolve().parents[2] / 'docs' / 'campaign_pack.schema.json'


class CampaignPackImportError(ValueError):
    def __init__(self, message: str, *, error_code: str = 'validation_error', status_code: int = 400):
        super().__init__(message)
        self.public_message = message
        self.error_code = error_code
        self.status_code = status_code


@dataclass(frozen=True)
class CampaignPackImportResult:
    payload: dict


SUPPORTED_SCHEMA_VERSIONS = {'1', '1.0', '1.0.0'}


def import_campaign_pack(
    payload: dict[str, Any],
    *,
    workspace_id: str,
    dry_run: bool = False,
    imported_by_account_id: int | None = None,
) -> CampaignPackImportResult:
    if not isinstance(payload, dict):
        raise CampaignPackImportError('Expected JSON request body.')

    pack = _pack_manifest(payload)
    pack_id = _required_slug(_first(pack, 'packId', 'pack_id'), field='packId')
    title = _required_text(_first(pack, 'title', 'name'), field='title', max_length=MAX_TITLE_LENGTH)
    schema_version = _schema_version(_first(pack, 'schemaVersion', 'schema_version'))
    _validate_pack_schema_contract(pack)
    version = _optional_text(_first(pack, 'version'), max_length=80) or '1.0.0'
    description = _optional_text(_first(pack, 'description', 'summary'), max_length=MAX_TEXT_LENGTH) or ''
    pack_hash = _pack_hash(pack)
    source_filename = _optional_text(_first(payload, 'sourceFilename', 'source_filename') or _first(pack, 'sourceFilename', 'source_filename'), max_length=255)

    starting_state = _record(_first(pack, 'startingState', 'starting_state', 'start'))
    locations = _pack_records(
        _first(pack, 'locations'),
        field='locations',
        pack_id=pack_id,
        fallback_prefix='location',
        required_name=False,
    )
    npcs = _pack_records(
        _first(pack, 'npcs', 'knownNpcs', 'known_npcs'),
        field='npcs',
        pack_id=pack_id,
        fallback_prefix='npc',
        required_name=True,
    )
    quests = _pack_records(
        _first(pack, 'quests'),
        field='quests',
        pack_id=pack_id,
        fallback_prefix='quest',
        required_name=False,
    )
    checkpoints = _pack_records(
        _first(pack, 'checkpoints'),
        field='checkpoints',
        pack_id=pack_id,
        fallback_prefix='checkpoint',
        required_name=False,
        limit=MAX_PACK_CHECKPOINTS,
    )
    segments = _segment_records(_first(pack, 'segments'), pack_id=pack_id)
    enemies = _enemy_records(_first(pack, 'enemies', 'bestiary'), pack_id=pack_id)
    encounters = _pack_records(
        _first(pack, 'encounters'),
        field='encounters',
        pack_id=pack_id,
        fallback_prefix='encounter',
        required_name=False,
    )
    future_records_by_type = _future_records_by_type(pack, pack_id=pack_id)
    director_rules = _bounded_json_value(_record(_first(pack, 'directorRules', 'director_rules')), depth=0)
    multi_session_group_key = _optional_text(
        _first(pack, 'multiSessionGroupKey', 'multi_session_group_key'),
        max_length=MAX_ID_LENGTH,
    )
    gm_notes = _optional_bounded_json(_first(pack, 'gmNotes', 'gm_notes', 'hiddenNotes', 'hidden_notes'))
    hidden_scene_notes = _optional_bounded_json(_first(pack, 'hiddenSceneNotes', 'hidden_scene_notes'))
    dependencies = _record_list(_first(pack, 'dependencies'))
    mods = _record_list(_first(pack, 'mods'))
    marketplace = _optional_bounded_json(_first(pack, 'marketplace', 'library'))
    _validate_pack_references(
        starting_state=starting_state,
        locations=locations,
        npcs=npcs,
        quests=quests,
        segments=segments,
        enemies=enemies,
        encounters=encounters,
        checkpoints=checkpoints,
    )

    starting_location_id = _clean_id(
        _first(starting_state, 'locationId', 'location_id', 'startingLocationId', 'starting_location_id')
        or _first(pack, 'startingLocationId', 'starting_location_id')
    )
    if not starting_location_id and locations:
        starting_location_id = locations[0]['id']

    starting_quest_id = _clean_id(
        _first(starting_state, 'questId', 'quest_id', 'startingQuestId', 'starting_quest_id')
        or _first(pack, 'startingQuestId', 'starting_quest_id')
    )
    if not starting_quest_id and quests:
        starting_quest_id = quests[0]['id']

    starting_location = _record_by_id(locations, starting_location_id)
    starting_quest = _record_by_id(quests, starting_quest_id)
    if starting_location_id and locations and starting_location is None:
        raise CampaignPackImportError('startingState.locationId must reference an imported location.')
    if starting_quest_id and quests and starting_quest is None:
        raise CampaignPackImportError('startingState.questId must reference an imported quest.')

    starting_visibility = _starting_visibility(
        starting_state=starting_state,
        starting_location_id=starting_location_id,
        starting_quest_id=starting_quest_id,
        locations=locations,
        npcs=npcs,
        quests=quests,
    )
    current_location = _location_label(starting_location, starting_location_id)
    current_quest = _quest_label(starting_quest, starting_quest_id)

    if dry_run:
        return CampaignPackImportResult(
            payload={
                'dry_run': True,
                'imported': False,
                'pack_id': pack_id,
                'schema_version': schema_version,
                'pack_version': version,
                'pack_hash': pack_hash,
                'counts': _counts_payload(
                    locations=locations,
                    npcs=npcs,
                    quests=quests,
                    segments=segments,
                    checkpoints=checkpoints,
                    encounters=encounters,
                    enemies=enemies,
                ),
                'preview': {
                    'title': title,
                    'description': description,
                    'world': _world_preview(payload, pack, workspace_id=workspace_id, title=title, description=description),
                    'starting_location_id': starting_location_id,
                    'starting_location': current_location,
                    'starting_quest_id': starting_quest_id,
                    'starting_quest': current_quest,
                    'director_rules': director_rules,
                    'visible_at_start': {
                        'locations': [record.get('id') for record in starting_visibility['visible_locations']],
                        'npcs': [record.get('id') for record in starting_visibility['visible_npcs']],
                        'quests': [record.get('id') for record in starting_visibility['visible_quests']],
                    },
                },
            }
        )

    world = _resolve_or_create_world(payload, pack, workspace_id=workspace_id, title=title, description=description)
    now = utc_now()
    campaign = Campaign(
        workspace_id=workspace_id,
        title=title,
        description=description,
        world_id=world.world_id,
        status='active',
        current_quest=current_quest,
        location=current_location,
        plot_points=safe_json_dumps([_checkpoint_summary(item) for item in checkpoints], []),
        active_npcs=safe_json_dumps([npc.get('name') for npc in starting_visibility['visible_npcs'] if npc.get('name')][:50], []),
        created_at=now,
        updated_at=now,
    )
    db.session.add(campaign)
    db.session.flush()

    session_obj = Session(
        campaign_id=campaign.campaign_id,
        name=_session_name(payload, pack, title),
        status='active',
        state_snapshot=safe_json_dumps(
            _initial_snapshot(
                pack_id=pack_id,
                title=title,
                schema_version=schema_version,
                version=version,
                starting_state=starting_state,
                starting_location=starting_location,
                starting_location_id=starting_location_id,
                starting_quest=starting_quest,
                starting_quest_id=starting_quest_id,
                locations=locations,
                npcs=npcs,
                quests=quests,
                enemies=enemies,
                checkpoints=checkpoints,
                encounters=encounters,
                director_rules=director_rules,
                multi_session_group_key=multi_session_group_key,
                gm_notes=gm_notes,
                hidden_scene_notes=hidden_scene_notes,
                dependencies=dependencies,
                mods=mods,
                marketplace=marketplace,
                extra_catalog_records=future_records_by_type,
                session_id=None,
                campaign_id=campaign.campaign_id,
                imported_at=now.isoformat(),
            ),
            {},
        ),
        created_at=now,
        updated_at=now,
    )
    db.session.add(session_obj)
    db.session.flush()

    snapshot = _initial_snapshot(
        pack_id=pack_id,
        title=title,
        schema_version=schema_version,
        version=version,
        starting_state=starting_state,
        starting_location=starting_location,
        starting_location_id=starting_location_id,
        starting_quest=starting_quest,
        starting_quest_id=starting_quest_id,
        locations=locations,
        npcs=npcs,
        quests=quests,
        enemies=enemies,
        checkpoints=checkpoints,
        encounters=encounters,
        director_rules=director_rules,
        multi_session_group_key=multi_session_group_key,
        gm_notes=gm_notes,
        hidden_scene_notes=hidden_scene_notes,
        dependencies=dependencies,
        mods=mods,
        marketplace=marketplace,
        extra_catalog_records=future_records_by_type,
        session_id=session_obj.session_id,
        campaign_id=campaign.campaign_id,
        imported_at=now.isoformat(),
    )
    session_obj.state_snapshot = safe_json_dumps(snapshot, {})

    session_state = SessionState(
        session_id=session_obj.session_id,
        current_location=current_location,
        current_quest=current_quest,
        rolling_summary=f'Campaign pack "{title}" imported. Begin from the authored starting state.',
        active_segments=safe_json_dumps([], []),
        memory_snippets=safe_json_dumps([], []),
        updated_at=now,
    )
    db.session.add(session_state)

    installed_pack = _upsert_installed_campaign_pack(
        workspace_id=workspace_id,
        pack_id=pack_id,
        title=title,
        version=version,
        schema_version=schema_version,
        pack_hash=pack_hash,
        source_filename=source_filename,
        imported_by_account_id=imported_by_account_id,
        manifest=pack,
        validated_at=now,
    )
    campaign_pack_definition = upsert_campaign_pack_definition(
        workspace_id=workspace_id,
        installed_pack=installed_pack,
        pack_id=pack_id,
        title=title,
        version=version,
        schema_version=schema_version,
        pack_hash=pack_hash,
        manifest=pack,
        records_by_type=_definition_records_by_type(
            locations=locations,
            npcs=npcs,
            quests=quests,
            enemies=enemies,
            encounters=encounters,
            segments=segments,
            checkpoints=checkpoints,
            future_records_by_type=future_records_by_type,
        ),
        validated_at=now,
    )
    sync_campaign_pack_progress(
        session=session_obj,
        pack=snapshot['campaignPack'],
        checkpoints=checkpoints,
        active_checkpoint_id=snapshot['campaignPack'].get('activeCheckpointId'),
        completed_ids=[],
        skipped_ids=[],
        failed_ids=[],
        progress_revision=0,
        campaign_pack=campaign_pack_definition,
        installed_pack=installed_pack,
    )

    for segment in segments:
        db.session.add(
            CampaignSegment(
                campaign_id=campaign.campaign_id,
                title=segment['title'],
                description=segment.get('description'),
                trigger_condition=segment.get('trigger_condition'),
                tags=segment.get('tags'),
                external_id=segment.get('id'),
                source='campaign_pack',
                source_pack_id=pack_id,
                metadata_json=safe_json_dumps(
                    {
                        'packId': pack_id,
                        'packSegmentId': segment.get('id'),
                        'source': 'campaign_pack',
                    },
                    {},
                ),
                is_triggered=False,
                created_at=now,
                updated_at=now,
            )
        )

    bestiary_count = 0
    for enemy in enemies:
        save_bestiary_entry(
            workspace_id=workspace_id,
            campaign_id=campaign.campaign_id,
            scope='campaign',
            source='campaign_pack',
            persistence='campaign',
            creature=enemy,
            region_id=_optional_text(_first(enemy, 'regionId', 'region_id'), max_length=MAX_ID_LENGTH) or None,
            location_ids=_string_list(_first(enemy, 'locationIds', 'location_ids', 'locations')),
            faction_ids=_string_list(_first(enemy, 'factionIds', 'faction_ids', 'factions')),
            tags=_dedupe(['campaign_pack', f'pack:{pack_id}', *_string_list(_first(enemy, 'tags', 'visualTags', 'visual_tags'))]),
            created_because=f'Imported from campaign pack {pack_id}.',
        )
        bestiary_count += 1

    result_payload = {
        'imported': True,
        'pack_id': pack_id,
        'schema_version': schema_version,
        'pack_version': version,
        'pack_hash': pack_hash,
        'installed_campaign_pack': _installed_pack_payload(installed_pack),
        'campaign_id': campaign.campaign_id,
        'session_id': session_obj.session_id,
        'campaign': campaign_payload(campaign),
        'session': session_payload(session_obj),
        'counts': _counts_payload(
            locations=locations,
            npcs=npcs,
            quests=quests,
            segments=segments,
            checkpoints=checkpoints,
            encounters=encounters,
            enemies=enemies,
            bestiary_count=bestiary_count,
        ),
    }
    record_operator_action(
        action='campaign_pack.import',
        resource_type='campaign_pack',
        workspace_id=workspace_id,
        campaign_id=campaign.campaign_id,
        session_id=session_obj.session_id,
        resource_id=pack_id,
        details={
            'packId': pack_id,
            'packVersion': version,
            'packHash': pack_hash,
            'installedCampaignPackId': installed_pack.installed_pack_id,
            'bestiaryCount': bestiary_count,
            'counts': result_payload['counts'],
        },
    )
    return CampaignPackImportResult(payload=result_payload)


def _pack_manifest(payload: dict[str, Any]) -> dict[str, Any]:
    if 'pack' in payload:
        pack = payload.get('pack')
        if not isinstance(pack, dict):
            raise CampaignPackImportError('pack must be a JSON object.')
        return pack
    return payload


def _schema_version(value: Any) -> str:
    version = _optional_text(value, max_length=20) or '1'
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise CampaignPackImportError(
            f'schemaVersion "{version}" is not supported. Use schemaVersion "1".',
            error_code='unsupported_schema_version',
        )
    return '1'


def _pack_hash(pack: dict[str, Any]) -> str:
    encoded = json.dumps(pack, sort_keys=True, separators=(',', ':'), ensure_ascii=True)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


def _upsert_installed_campaign_pack(
    *,
    workspace_id: str,
    pack_id: str,
    title: str,
    version: str,
    schema_version: str,
    pack_hash: str,
    source_filename: str | None,
    imported_by_account_id: int | None,
    manifest: dict[str, Any],
    validated_at,
) -> InstalledCampaignPack:
    installed_pack = InstalledCampaignPack.query.filter_by(workspace_id=workspace_id, pack_hash=pack_hash).first()
    if installed_pack is None:
        installed_pack = InstalledCampaignPack(
            workspace_id=workspace_id,
            pack_hash=pack_hash,
            created_at=validated_at,
        )
        db.session.add(installed_pack)
    installed_pack.pack_id = pack_id
    installed_pack.title = title
    installed_pack.pack_version = version
    installed_pack.schema_version = schema_version
    installed_pack.source_filename = source_filename
    installed_pack.imported_by_account_id = imported_by_account_id
    installed_pack.manifest_json = safe_json_dumps(manifest, {})
    installed_pack.validated_at = validated_at
    installed_pack.updated_at = validated_at
    db.session.flush()
    return installed_pack


def _installed_pack_payload(installed_pack: InstalledCampaignPack) -> dict[str, Any]:
    return {
        'installed_pack_id': installed_pack.installed_pack_id,
        'workspace_id': installed_pack.workspace_id,
        'pack_id': installed_pack.pack_id,
        'title': installed_pack.title,
        'pack_version': installed_pack.pack_version,
        'schema_version': installed_pack.schema_version,
        'pack_hash': installed_pack.pack_hash,
        'source_filename': installed_pack.source_filename,
        'imported_by_account_id': installed_pack.imported_by_account_id,
        'validated_at': installed_pack.validated_at.isoformat() if installed_pack.validated_at else None,
    }


@lru_cache(maxsize=1)
def _campaign_pack_schema() -> dict[str, Any]:
    with CAMPAIGN_PACK_SCHEMA_PATH.open('r', encoding='utf-8') as schema_file:
        schema = json.load(schema_file)
    return schema if isinstance(schema, dict) else {}


def _validate_pack_schema_contract(pack: dict[str, Any]) -> None:
    schema = _campaign_pack_schema()
    error = _schema_validation_error(pack, schema, path='campaign pack', root=schema)
    if error:
        raise CampaignPackImportError(error, error_code='invalid_campaign_pack_schema')


def _schema_validation_error(value: Any, schema: dict[str, Any], *, path: str, root: dict[str, Any]) -> str | None:
    if not isinstance(schema, dict):
        return None

    if '$ref' in schema:
        return _schema_validation_error(value, _resolve_schema_ref(root, str(schema['$ref'])), path=path, root=root)

    for subschema in schema.get('allOf') or []:
        error = _schema_validation_error(value, subschema, path=path, root=root)
        if error:
            return error

    if schema.get('oneOf'):
        errors = [
            _schema_validation_error(value, subschema, path=path, root=root)
            for subschema in schema.get('oneOf') or []
            if isinstance(subschema, dict)
        ]
        if sum(error is None for error in errors) != 1:
            return f'{path} must match exactly one supported schema shape.'
        return None

    if 'type' in schema and not _schema_type_matches(value, schema['type']):
        return f'{path} must be {_schema_type_label(schema["type"])}.'

    if 'const' in schema and value != schema['const']:
        return f'{path} must be {schema["const"]!r}.'

    if 'enum' in schema and not any(value == allowed for allowed in schema['enum']):
        return f'{path} must be one of: {", ".join(str(allowed) for allowed in schema["enum"])}.'

    if isinstance(value, str):
        min_length = schema.get('minLength')
        if isinstance(min_length, int) and len(value) < min_length:
            return f'{path} must be at least {min_length} character{"s" if min_length != 1 else ""}.'
        max_length = schema.get('maxLength')
        if isinstance(max_length, int) and len(value) > max_length:
            return f'{path} must be {max_length} characters or fewer.'
        pattern = schema.get('pattern')
        if isinstance(pattern, str) and re.fullmatch(pattern, value) is None:
            return f'{path} contains unsupported characters.'

    if isinstance(value, int) and not isinstance(value, bool):
        minimum = schema.get('minimum')
        if isinstance(minimum, int | float) and value < minimum:
            return f'{path} must be at least {minimum}.'
        maximum = schema.get('maximum')
        if isinstance(maximum, int | float) and value > maximum:
            return f'{path} must be at most {maximum}.'

    if isinstance(value, list):
        max_items = schema.get('maxItems')
        if isinstance(max_items, int) and len(value) > max_items:
            return f'{path} may include at most {max_items} items.'
        if schema.get('uniqueItems') is True:
            seen_items: set[str] = set()
            for item in value:
                item_key = json.dumps(item, sort_keys=True, separators=(',', ':'))
                if item_key in seen_items:
                    return f'{path} must not contain duplicate values.'
                seen_items.add(item_key)
        item_schema = schema.get('items')
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                error = _schema_validation_error(item, item_schema, path=f'{path}[{index}]', root=root)
                if error:
                    return error

    if isinstance(value, dict):
        for required_key in schema.get('required') or []:
            if required_key not in value or value.get(required_key) in (None, ''):
                return f'{_schema_child_path(path, required_key)} is required.'
        properties = schema.get('properties') if isinstance(schema.get('properties'), dict) else {}
        for key, item_schema in properties.items():
            if key not in value:
                continue
            error = _schema_validation_error(value.get(key), item_schema, path=_schema_child_path(path, key), root=root)
            if error:
                return error

    return None


def _resolve_schema_ref(root: dict[str, Any], ref: str) -> dict[str, Any]:
    if not ref.startswith('#/'):
        return {}
    current: Any = root
    for raw_part in ref[2:].split('/'):
        part = raw_part.replace('~1', '/').replace('~0', '~')
        if not isinstance(current, dict):
            return {}
        current = current.get(part)
    return current if isinstance(current, dict) else {}


def _schema_type_matches(value: Any, schema_type: Any) -> bool:
    if isinstance(schema_type, list):
        return any(_schema_type_matches(value, item) for item in schema_type)
    if schema_type == 'object':
        return isinstance(value, dict)
    if schema_type == 'array':
        return isinstance(value, list)
    if schema_type == 'string':
        return isinstance(value, str)
    if schema_type == 'boolean':
        return isinstance(value, bool)
    if schema_type == 'integer':
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == 'number':
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return True


def _schema_type_label(schema_type: Any) -> str:
    if isinstance(schema_type, list):
        return ' or '.join(str(item) for item in schema_type)
    if schema_type == 'object':
        return 'a JSON object'
    if schema_type == 'array':
        return 'a list'
    if schema_type == 'string':
        return 'a string'
    if schema_type == 'boolean':
        return 'a boolean'
    if schema_type == 'integer':
        return 'an integer'
    if schema_type == 'number':
        return 'a number'
    return str(schema_type)


def _schema_child_path(path: str, key: str) -> str:
    return key if path == 'campaign pack' else f'{path}.{key}'


def _world_reference(payload: dict[str, Any], pack: dict[str, Any]) -> int | None:
    raw_world_id = (
        _first(payload, 'world_id', 'worldId')
        or _first(pack, 'world_id', 'worldId')
        or _first(_record(_first(pack, 'world')), 'world_id', 'worldId')
    )
    world_id = coerce_int(raw_world_id)
    return world_id if world_id is not None and world_id > 0 else None


def _world_manifest(pack: dict[str, Any], *, title: str, description: str) -> tuple[str, str]:
    world_record = _record(_first(pack, 'world', 'worldSettings', 'world_settings'))
    world_name = (
        _optional_text(_first(world_record, 'name', 'title'), max_length=MAX_TITLE_LENGTH)
        or _optional_text(_first(pack, 'worldName', 'world_name'), max_length=MAX_TITLE_LENGTH)
        or f'{title} World'
    )
    world_description = (
        _optional_text(_first(world_record, 'description', 'summary'), max_length=MAX_TEXT_LENGTH)
        or _optional_text(_first(pack, 'worldDescription', 'world_description'), max_length=MAX_TEXT_LENGTH)
        or description
    )
    return world_name, world_description


def _world_preview(
    payload: dict[str, Any],
    pack: dict[str, Any],
    *,
    workspace_id: str,
    title: str,
    description: str,
) -> dict[str, Any]:
    world_id = _world_reference(payload, pack)
    if world_id:
        world = db.session.get(World, world_id)
        if not world or (world.workspace_id or 'owner') != workspace_id:
            raise CampaignPackImportError('World not found.', error_code='world_not_found', status_code=404)
        return {
            'mode': 'existing',
            'world_id': world.world_id,
            'name': world.name,
            'description': world.description,
        }
    world_name, world_description = _world_manifest(pack, title=title, description=description)
    return {
        'mode': 'create',
        'world_id': None,
        'name': world_name,
        'description': world_description,
    }


def _resolve_or_create_world(
    payload: dict[str, Any],
    pack: dict[str, Any],
    *,
    workspace_id: str,
    title: str,
    description: str,
) -> World:
    world_id = _world_reference(payload, pack)
    if world_id:
        world = db.session.get(World, world_id)
        if not world or (world.workspace_id or 'owner') != workspace_id:
            raise CampaignPackImportError('World not found.', error_code='world_not_found', status_code=404)
        return world

    world_name, world_description = _world_manifest(pack, title=title, description=description)
    world = World(
        workspace_id=workspace_id,
        name=world_name,
        description=world_description,
        created_at=utc_now(),
    )
    db.session.add(world)
    db.session.flush()
    return world


def _pack_records(
    value: Any,
    *,
    field: str,
    pack_id: str,
    fallback_prefix: str,
    required_name: bool,
    limit: int = MAX_PACK_RECORDS,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CampaignPackImportError(f'{field} must be a list.')
    if len(value) > limit:
        raise CampaignPackImportError(f'{field} may include at most {limit} records.')

    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, dict):
            raise CampaignPackImportError(f'{field}[{index}] must be a JSON object.')
        item = _bounded_json_value(raw_item, depth=0)
        name = _optional_text(_first(item, 'name', 'title'), max_length=MAX_NAME_LENGTH) or ''
        if required_name and not name:
            raise CampaignPackImportError(f'{field}[{index}].name is required.')
        record_id = _clean_id(_first(item, 'id', f'{fallback_prefix}Id', f'{fallback_prefix}_id'))
        if not record_id:
            record_id = _clean_id(stable_slug(name or f'{fallback_prefix}_{index + 1}'))
        if record_id in seen_ids:
            raise CampaignPackImportError(f'{field} contains duplicate id "{record_id}".')
        seen_ids.add(record_id)
        item['id'] = record_id
        if name and not item.get('name'):
            item['name'] = name
        item['source'] = 'campaign_pack'
        item['packId'] = pack_id
        records.append(item)
    return records


def _segment_records(value: Any, *, pack_id: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CampaignPackImportError('segments must be a list.')
    if len(value) > MAX_PACK_RECORDS:
        raise CampaignPackImportError(f'segments may include at most {MAX_PACK_RECORDS} records.')

    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, dict):
            raise CampaignPackImportError(f'segments[{index}] must be a JSON object.')
        item = _bounded_json_value(raw_item, depth=0)
        title = _required_text(_first(item, 'title', 'name'), field=f'segments[{index}].title', max_length=MAX_TITLE_LENGTH)
        external_id = _clean_id(_first(item, 'id', 'segmentId', 'segment_id')) or stable_slug(title)
        if external_id in seen_ids:
            raise CampaignPackImportError(f'segments contains duplicate id "{external_id}".')
        seen_ids.add(external_id)
        trigger = _first(item, 'trigger', 'triggerCondition', 'trigger_condition')
        trigger_condition = _trigger_condition(trigger, pack_id=pack_id, external_id=external_id)
        tags = _segment_tags(_first(item, 'tags'), pack_id=pack_id, external_id=external_id)
        records.append(
            {
                'id': external_id,
                'title': title,
                'description': _optional_text(_first(item, 'description', 'summary'), max_length=MAX_TEXT_LENGTH),
                'trigger_condition': trigger_condition,
                'tags': tags,
            }
        )
    return records


def _enemy_records(value: Any, *, pack_id: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise CampaignPackImportError('enemies must be a list.')
    if len(value) > MAX_PACK_ENEMIES:
        raise CampaignPackImportError(f'enemies may include at most {MAX_PACK_ENEMIES} records.')

    records: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, raw_item in enumerate(value):
        if not isinstance(raw_item, dict):
            raise CampaignPackImportError(f'enemies[{index}] must be a JSON object.')
        item = _bounded_json_value(raw_item, depth=0)
        name = _required_text(_first(item, 'name', 'title'), field=f'enemies[{index}].name', max_length=MAX_NAME_LENGTH)
        creature_id = _clean_id(_first(item, 'id', 'creatureId', 'creature_id')) or stable_slug(name)
        if creature_id in seen_ids:
            raise CampaignPackImportError(f'enemies contains duplicate id "{creature_id}".')
        seen_ids.add(creature_id)
        item['id'] = creature_id
        item['name'] = name
        item['source'] = 'campaign_pack'
        item['packId'] = pack_id
        records.append(item)
    return records


def _future_records_by_type(pack: dict[str, Any], *, pack_id: str) -> dict[str, list[dict[str, Any]]]:
    domains = {
        'clue': ('clues', 'clue'),
        'faction': ('factions', 'faction'),
        'map': ('maps', 'map'),
        'handout': ('handouts', 'handout'),
        'lore': ('lore', 'lore'),
    }
    records_by_type: dict[str, list[dict[str, Any]]] = {}
    for record_type, (field, fallback_prefix) in domains.items():
        records = _pack_records(
            _first(pack, field),
            field=field,
            pack_id=pack_id,
            fallback_prefix=fallback_prefix,
            required_name=False,
        )
        if records:
            records_by_type[record_type] = records
    return records_by_type


def _definition_records_by_type(
    *,
    locations: list[dict],
    npcs: list[dict],
    quests: list[dict],
    enemies: list[dict],
    encounters: list[dict],
    segments: list[dict],
    checkpoints: list[dict],
    future_records_by_type: dict[str, list[dict]],
) -> dict[str, list[dict]]:
    records_by_type = {
        'location': locations,
        'npc': npcs,
        'quest': quests,
        'enemy': enemies,
        'encounter': encounters,
        'segment': segments,
        'checkpoint': checkpoints,
    }
    records_by_type.update(future_records_by_type)
    return records_by_type


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = _optional_text(value, max_length=20)
    return bool(text and text.lower() in {'1', 'true', 'yes', 'y', 'on', 'known', 'visible'})


def _record_metadata(record: dict) -> dict:
    return record.get('metadata') if isinstance(record.get('metadata'), dict) else {}


def _visible_at_start(record: dict) -> bool:
    metadata = _record_metadata(record)
    for key in (
        'visibleAtStart',
        'visible_at_start',
        'initiallyKnown',
        'initially_known',
        'knownAtStart',
        'known_at_start',
        'discoveredAtStart',
        'discovered_at_start',
    ):
        if _truthy(record.get(key)) or _truthy(metadata.get(key)):
            return True
    return False


def _ids_for(records: list[dict]) -> set[str]:
    return {record_id for record_id in (_clean_id(record.get('id')) for record in records) if record_id}


def _validate_reference(
    *,
    field: str,
    record_id: str,
    values: list[str],
    allowed: set[str],
    target: str,
) -> None:
    if not values or not allowed:
        return
    missing = [value for value in values if _clean_id(value) not in allowed]
    if missing:
        raise CampaignPackImportError(
            f'{field} on "{record_id}" references unknown {target} "{missing[0]}".',
            error_code='invalid_pack_reference',
        )


def _validate_checkpoint_graph(checkpoints: list[dict]) -> None:
    checkpoint_ids = _ids_for(checkpoints)
    if not checkpoint_ids:
        return

    edges: dict[str, list[str]] = {}
    for checkpoint in checkpoints:
        checkpoint_id = _clean_id(checkpoint.get('id'))
        if not checkpoint_id:
            continue
        next_ids = [_clean_id(value) for value in _string_list(_first(checkpoint, 'nextCheckpointIds', 'next_checkpoint_ids'))]
        next_ids = [value for value in next_ids if value]
        _validate_reference(
            field='nextCheckpointIds',
            record_id=checkpoint_id,
            values=next_ids,
            allowed=checkpoint_ids,
            target='checkpoint',
        )
        rejoin_id = _clean_id(_first(checkpoint, 'rejoinTargetCheckpointId', 'rejoin_target_checkpoint_id'))
        if rejoin_id:
            _validate_reference(
                field='rejoinTargetCheckpointId',
                record_id=checkpoint_id,
                values=[rejoin_id],
                allowed=checkpoint_ids,
                target='checkpoint',
            )
        for field, keys in {
            'alternateCheckpointIds': ('alternateCheckpointIds', 'alternate_checkpoint_ids', 'alternateRouteCheckpointIds', 'alternate_route_checkpoint_ids'),
            'prerequisiteCheckpointIds': ('prerequisiteCheckpointIds', 'prerequisite_checkpoint_ids', 'requiredCheckpointIds', 'required_checkpoint_ids', 'requiresCheckpointIds', 'requires_checkpoint_ids'),
            'failureCheckpointIds': ('failureCheckpointIds', 'failure_checkpoint_ids', 'failedCheckpointIds', 'failed_checkpoint_ids', 'onFailCheckpointIds', 'on_fail_checkpoint_ids'),
        }.items():
            _validate_reference(
                field=field,
                record_id=checkpoint_id,
                values=_string_list(_first(checkpoint, *keys)),
                allowed=checkpoint_ids,
                target='checkpoint',
            )
        edges[checkpoint_id] = next_ids

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(checkpoint_id: str, path: list[str]) -> None:
        if checkpoint_id in visited:
            return
        if checkpoint_id in visiting:
            cycle = ' -> '.join([*path, checkpoint_id])
            raise CampaignPackImportError(
                f'checkpoints contain a nextCheckpointIds cycle: {cycle}.',
                error_code='invalid_checkpoint_graph',
            )
        visiting.add(checkpoint_id)
        for next_id in edges.get(checkpoint_id, []):
            visit(next_id, [*path, checkpoint_id])
        visiting.remove(checkpoint_id)
        visited.add(checkpoint_id)

    for checkpoint_id in checkpoint_ids:
        visit(checkpoint_id, [])


def _validate_pack_references(
    *,
    starting_state: dict,
    locations: list[dict],
    npcs: list[dict],
    quests: list[dict],
    segments: list[dict],
    enemies: list[dict],
    encounters: list[dict],
    checkpoints: list[dict],
) -> None:
    location_ids = _ids_for(locations)
    npc_ids = _ids_for(npcs)
    quest_ids = _ids_for(quests)
    segment_ids = _ids_for(segments)
    enemy_ids = _ids_for(enemies)
    encounter_ids = _ids_for(encounters)
    checkpoint_ids = _ids_for(checkpoints)

    start_checkpoint_id = _clean_id(
        _first(starting_state, 'checkpointId', 'checkpoint_id', 'startingCheckpointId', 'starting_checkpoint_id')
    )
    if start_checkpoint_id:
        _validate_reference(
            field='startingState.checkpointId',
            record_id='startingState',
            values=[start_checkpoint_id],
            allowed=checkpoint_ids,
            target='checkpoint',
        )

    for npc in npcs:
        npc_id = _clean_id(npc.get('id')) or 'npc'
        location_id = _clean_id(_first(npc, 'locationId', 'location_id'))
        if location_id:
            _validate_reference(
                field='locationId',
                record_id=npc_id,
                values=[location_id],
                allowed=location_ids,
                target='location',
            )
        _validate_reference(
            field='questIds',
            record_id=npc_id,
            values=_string_list(_first(npc, 'questIds', 'quest_ids')),
            allowed=quest_ids,
            target='quest',
        )

    for encounter in encounters:
        encounter_id = _clean_id(encounter.get('id')) or 'encounter'
        _validate_reference(
            field='enemyIds',
            record_id=encounter_id,
            values=_string_list(_first(encounter, 'enemyIds', 'enemy_ids')),
            allowed=enemy_ids,
            target='enemy',
        )
        _validate_reference(
            field='locationIds',
            record_id=encounter_id,
            values=_string_list(_first(encounter, 'locationIds', 'location_ids')),
            allowed=location_ids,
            target='location',
        )
        _validate_reference(
            field='questIds',
            record_id=encounter_id,
            values=_string_list(_first(encounter, 'questIds', 'quest_ids')),
            allowed=quest_ids,
            target='quest',
        )
        _validate_reference(
            field='checkpointIds',
            record_id=encounter_id,
            values=_string_list(_first(encounter, 'checkpointIds', 'checkpoint_ids')),
            allowed=checkpoint_ids,
            target='checkpoint',
        )

    for checkpoint in checkpoints:
        checkpoint_id = _clean_id(checkpoint.get('id')) or 'checkpoint'
        _validate_reference(
            field='locationIds',
            record_id=checkpoint_id,
            values=_string_list(_first(checkpoint, 'locationIds', 'location_ids')),
            allowed=location_ids,
            target='location',
        )
        _validate_reference(
            field='npcIds',
            record_id=checkpoint_id,
            values=_string_list(_first(checkpoint, 'npcIds', 'npc_ids')),
            allowed=npc_ids,
            target='NPC',
        )
        _validate_reference(
            field='questIds',
            record_id=checkpoint_id,
            values=_string_list(_first(checkpoint, 'questIds', 'quest_ids')),
            allowed=quest_ids,
            target='quest',
        )
        _validate_reference(
            field='segmentIds',
            record_id=checkpoint_id,
            values=_string_list(_first(checkpoint, 'segmentIds', 'segment_ids')),
            allowed=segment_ids,
            target='segment',
        )
        _validate_reference(
            field='encounterIds',
            record_id=checkpoint_id,
            values=_string_list(_first(checkpoint, 'encounterIds', 'encounter_ids')),
            allowed=encounter_ids,
            target='encounter',
        )

    _validate_checkpoint_graph(checkpoints)


def _starting_state_ids(starting_state: dict, *keys: str) -> list[str]:
    ids: list[Any] = []
    current_scene = _record(_first(starting_state, 'currentScene', 'current_scene'))
    for key in keys:
        ids.extend(_string_list(_first(starting_state, key)))
        ids.extend(_string_list(_first(current_scene, key)))
    return [_clean_id(value) for value in _dedupe(ids) if _clean_id(value)]


def _records_by_ids_or_visibility(records: list[dict], ids: list[str], *, include_visible_marks: bool = True) -> list[dict]:
    wanted = {record_id for record_id in ids if record_id}
    selected: list[dict] = []
    for record in records:
        record_id = _clean_id(record.get('id'))
        if (record_id and record_id in wanted) or (include_visible_marks and _visible_at_start(record)):
            selected.append(record)
    return selected


def _starting_visibility(
    *,
    starting_state: dict,
    starting_location_id: str | None,
    starting_quest_id: str | None,
    locations: list[dict],
    npcs: list[dict],
    quests: list[dict],
) -> dict[str, list[dict] | list[str]]:
    current_scene = _record(_first(starting_state, 'currentScene', 'current_scene'))
    scene_location_id = starting_location_id or _clean_id(_first(current_scene, 'locationId', 'location_id'))
    active_npc_ids = _starting_state_ids(starting_state, 'activeNpcIds', 'active_npc_ids')
    known_npc_ids = _dedupe(
        [
            *active_npc_ids,
            *_starting_state_ids(starting_state, 'knownNpcIds', 'known_npc_ids', 'startingNpcIds', 'starting_npc_ids'),
        ]
    )
    active_quest_ids = _dedupe(
        [
            *_starting_state_ids(starting_state, 'activeQuestIds', 'active_quest_ids'),
            *([starting_quest_id] if starting_quest_id else []),
        ]
    )
    known_quest_ids = _dedupe(
        [
            *active_quest_ids,
            *_starting_state_ids(starting_state, 'knownQuestIds', 'known_quest_ids', 'startingQuestIds', 'starting_quest_ids'),
        ]
    )
    known_location_ids = _dedupe(
        [
            *([scene_location_id] if scene_location_id else []),
            *_starting_state_ids(
                starting_state,
                'knownLocationIds',
                'known_location_ids',
                'startingLocationIds',
                'starting_location_ids',
            ),
        ]
    )

    visible_locations = _records_by_ids_or_visibility(locations, known_location_ids)
    visible_quests = _records_by_ids_or_visibility(quests, known_quest_ids)
    visible_npcs = _records_by_ids_or_visibility(npcs, known_npc_ids)

    if not active_npc_ids and scene_location_id:
        active_npc_ids = _dedupe(
            [
                npc.get('id')
                for npc in visible_npcs
                if _clean_id(_first(npc, 'locationId', 'location_id')) == scene_location_id
            ]
        )

    visible_npc_ids = {npc.get('id') for npc in visible_npcs}
    visible_quest_ids = {quest.get('id') for quest in visible_quests}
    active_npc_ids = [npc_id for npc_id in active_npc_ids if npc_id in visible_npc_ids]
    active_quest_ids = [quest_id for quest_id in active_quest_ids if quest_id in visible_quest_ids]

    return {
        'visible_locations': visible_locations,
        'visible_npcs': visible_npcs,
        'visible_quests': visible_quests,
        'active_npc_ids': active_npc_ids,
        'active_quest_ids': active_quest_ids,
    }


def _initial_snapshot(
    *,
    pack_id: str,
    title: str,
    schema_version: str,
    version: str,
    starting_state: dict,
    starting_location: dict | None,
    starting_location_id: str | None,
    starting_quest: dict | None,
    starting_quest_id: str | None,
    locations: list[dict],
    npcs: list[dict],
    quests: list[dict],
    enemies: list[dict],
    checkpoints: list[dict],
    encounters: list[dict],
    director_rules: dict,
    multi_session_group_key: str | None,
    gm_notes: Any,
    hidden_scene_notes: Any,
    dependencies: list[dict],
    mods: list[dict],
    marketplace: Any,
    extra_catalog_records: dict[str, list[dict]],
    session_id: int | None,
    campaign_id: int,
    imported_at: str,
) -> dict[str, Any]:
    current_scene = _record(_first(starting_state, 'currentScene', 'current_scene'))
    scene_location_id = starting_location_id or _clean_id(_first(current_scene, 'locationId', 'location_id'))
    visibility = _starting_visibility(
        starting_state=starting_state,
        starting_location_id=starting_location_id,
        starting_quest_id=starting_quest_id,
        locations=locations,
        npcs=npcs,
        quests=quests,
    )
    visible_locations = visibility['visible_locations']
    visible_npcs = visibility['visible_npcs']
    visible_quests = visibility['visible_quests']
    active_npc_ids = visibility['active_npc_ids']
    active_quest_ids = visibility['active_quest_ids']
    scene_name = (
        _optional_text(_first(current_scene, 'name', 'title'), max_length=MAX_NAME_LENGTH)
        or _location_label(starting_location, scene_location_id)
    )
    scene_description = (
        _optional_text(_first(current_scene, 'description'), max_length=MAX_TEXT_LENGTH)
        or _optional_text(_first(starting_location or {}, 'description', 'summary'), max_length=MAX_TEXT_LENGTH)
        or ''
    )
    flags = _record(_first(starting_state, 'flags'))
    flags['campaignPackImported'] = True
    flags['campaignPackId'] = pack_id
    active_checkpoint_id = _initial_checkpoint_id(checkpoints, starting_state=starting_state)
    flags['campaignPackActiveCheckpointId'] = active_checkpoint_id
    flags['campaignPackCompletedCheckpointIds'] = []
    flags['campaignPackSkippedCheckpointIds'] = []
    flags['campaignPackFailedCheckpointIds'] = []
    flags['campaignPackProgressRevision'] = 0
    catalog = {
        'locations': locations,
        'npcs': npcs,
        'quests': quests,
        'enemies': enemies,
        'encounters': encounters,
    }
    for record_type, records in extra_catalog_records.items():
        catalog[f'{record_type}s'] = records
    return {
        'schemaVersion': 1,
        'sessionId': session_id,
        'campaignId': campaign_id,
        'currentScene': {
            'locationId': scene_location_id,
            'name': scene_name,
            'sceneType': _optional_text(_first(current_scene, 'sceneType', 'scene_type'), max_length=80) or 'exploration',
            'dangerLevel': max(0, min(10, coerce_int(_first(current_scene, 'dangerLevel', 'danger_level'), 0) or 0)),
            'mood': _optional_text(_first(current_scene, 'mood'), max_length=120) or None,
            'combatState': _optional_text(_first(current_scene, 'combatState', 'combat_state'), max_length=80) or 'none',
            'description': scene_description,
            'activeNpcIds': active_npc_ids,
            'activeQuestIds': active_quest_ids,
            'playerPositions': _record(_first(current_scene, 'playerPositions', 'player_positions')),
            'playerZones': _record(_first(current_scene, 'playerZones', 'player_zones')),
            'characterPositions': _record(_first(current_scene, 'characterPositions', 'character_positions')),
            'characterZones': _record(_first(current_scene, 'characterZones', 'character_zones')),
            'items': _record_list(_first(current_scene, 'items')),
            'musicTag': _optional_text(_first(current_scene, 'musicTag', 'music_tag'), max_length=120) or None,
            'updatedAtTurn': None,
        },
        'playerCharacters': [],
        'activePlayerIds': [],
        'partyNpcs': _record_list(_first(starting_state, 'partyNpcs', 'party_npcs')),
        'knownNpcs': visible_npcs,
        'quests': visible_quests,
        'locations': visible_locations,
        'combat': _record(_first(starting_state, 'combat')) or {
            'status': 'none',
            'round': 1,
            'participants': [],
            'battlefield': {},
            'flags': {},
        },
        'flags': flags,
        'campaignPack': {
            'packId': pack_id,
            'title': title,
            'snapshotSchemaVersion': 1,
            'schemaVersion': schema_version,
            'version': version,
            'source': 'campaign_pack',
            'importedAt': imported_at,
            'progressSchemaVersion': 1,
            'progressRevision': 0,
            'activeCheckpointId': active_checkpoint_id,
            'completedCheckpointIds': [],
            'skippedCheckpointIds': [],
            'failedCheckpointIds': [],
            'progressEventsVersion': 1,
            'startingLocationId': starting_location_id,
            'startingQuestId': starting_quest_id,
            'directorRules': director_rules,
            'multiSessionGroupKey': multi_session_group_key,
            'gmNotes': gm_notes,
            'hiddenSceneNotes': hidden_scene_notes,
            'dependencies': dependencies,
            'mods': mods,
            'marketplace': marketplace,
            'checkpoints': checkpoints,
            'encounters': encounters,
            'catalog': catalog,
        },
        'stateChangeLedger': [],
        'lastUpdatedAt': imported_at,
    }


def _initial_checkpoint_id(
    checkpoints: list[dict],
    *,
    starting_state: dict | None = None,
) -> str | None:
    requested_id = _clean_id(
        _first(
            starting_state or {},
            'checkpointId',
            'checkpoint_id',
            'startingCheckpointId',
            'starting_checkpoint_id',
        )
    )
    if requested_id:
        return requested_id
    for checkpoint in checkpoints:
        checkpoint_id = _clean_id(_first(checkpoint, 'id', 'checkpointId', 'checkpoint_id'))
        if checkpoint_id:
            return checkpoint_id
    return None


def _counts_payload(
    *,
    locations: list[dict],
    npcs: list[dict],
    quests: list[dict],
    segments: list[dict],
    checkpoints: list[dict],
    encounters: list[dict],
    enemies: list[dict],
    bestiary_count: int | None = None,
) -> dict[str, int]:
    return {
        'locations': len(locations),
        'npcs': len(npcs),
        'quests': len(quests),
        'segments': len(segments),
        'checkpoints': len(checkpoints),
        'encounters': len(encounters),
        'enemies': len(enemies),
        'bestiary_entries': len(enemies) if bestiary_count is None else bestiary_count,
    }


def _trigger_condition(trigger: Any, *, pack_id: str, external_id: str) -> str:
    if isinstance(trigger, str):
        text = trigger.strip()
        return text[:MAX_TEXT_LENGTH] if text else _manual_trigger(pack_id, external_id)
    if isinstance(trigger, dict):
        trigger_payload = _bounded_json_value(trigger, depth=0)
        trigger_payload.setdefault('type', 'manual')
        trigger_payload.setdefault('source', 'campaign_pack')
        trigger_payload.setdefault('packId', pack_id)
        trigger_payload.setdefault('packSegmentId', external_id)
        return safe_json_dumps(trigger_payload, {})
    return _manual_trigger(pack_id, external_id)


def _manual_trigger(pack_id: str, external_id: str) -> str:
    return safe_json_dumps(
        {
            'type': 'manual',
            'source': 'campaign_pack',
            'packId': pack_id,
            'packSegmentId': external_id,
        },
        {},
    )


def _segment_tags(value: Any, *, pack_id: str, external_id: str) -> str:
    tags = _dedupe(['campaign_pack', f'pack:{pack_id}', f'pack_segment:{external_id}', *_string_list(value)])
    text = ','.join(tags)
    return text[:500]


def _session_name(payload: dict[str, Any], pack: dict[str, Any], title: str) -> str:
    name = (
        _optional_text(_first(payload, 'session_name', 'sessionName'), max_length=MAX_SESSION_NAME_LENGTH)
        or _optional_text(_first(pack, 'sessionName', 'session_name'), max_length=MAX_SESSION_NAME_LENGTH)
        or f'{title[:61]} Opening'
    )
    return name[:MAX_SESSION_NAME_LENGTH]


def _checkpoint_summary(value: dict) -> dict:
    return {
        'id': value.get('id'),
        'title': value.get('title') or value.get('name'),
        'source': 'campaign_pack',
        'packId': value.get('packId'),
    }


def _record_by_id(records: list[dict], record_id: str | None) -> dict | None:
    if not record_id:
        return None
    return next((record for record in records if record.get('id') == record_id), None)


def _location_label(location: dict | None, fallback_id: str | None) -> str | None:
    if not location:
        return fallback_id
    return (
        _optional_text(_first(location, 'name', 'title'), max_length=MAX_NAME_LENGTH)
        or _optional_text(_first(location, 'id'), max_length=MAX_ID_LENGTH)
        or fallback_id
    )


def _quest_label(quest: dict | None, fallback_id: str | None) -> str | None:
    if not quest:
        return fallback_id
    title = _optional_text(_first(quest, 'title', 'name'), max_length=MAX_NAME_LENGTH)
    stage = _optional_text(_first(quest, 'stage'), max_length=MAX_NAME_LENGTH)
    if title and stage:
        return f'{title} - {stage}'
    return title or _optional_text(_first(quest, 'id'), max_length=MAX_ID_LENGTH) or fallback_id


def _first(record: dict | None, *keys: str) -> Any:
    if not isinstance(record, dict):
        return None
    for key in keys:
        if key in record:
            return record.get(key)
    return None


def _record(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _record_list(value: Any) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [_bounded_json_value(item, depth=0) for item in value[:MAX_LIST_ITEMS] if isinstance(item, dict)]


def _optional_bounded_json(value: Any) -> Any:
    if value in (None, ''):
        return None
    return _bounded_json_value(value, depth=0)


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = value.replace(';', ',').split(',')
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _optional_text(item, max_length=MAX_ID_LENGTH)
        if text:
            result.append(text)
    return _dedupe(result)


def _dedupe(values: list[Any]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _optional_text(value, max_length=MAX_ID_LENGTH)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _required_slug(value: Any, *, field: str) -> str:
    text = _clean_id(value)
    if not text:
        raise CampaignPackImportError(f'{field} is required.')
    return text


def _required_text(value: Any, *, field: str, max_length: int) -> str:
    text = _optional_text(value, max_length=max_length)
    if not text:
        raise CampaignPackImportError(f'{field} is required.')
    return text


def _optional_text(value: Any, *, max_length: int) -> str | None:
    if value in (None, ''):
        return None
    if not isinstance(value, str):
        value = str(value)
    text = value.strip()
    if not text:
        return None
    return text[:max_length]


def _clean_id(value: Any) -> str | None:
    text = _optional_text(value, max_length=MAX_ID_LENGTH)
    if not text:
        return None
    return stable_slug(text)[:MAX_ID_LENGTH]


def _bounded_json_value(value: Any, *, depth: int) -> Any:
    if depth >= MAX_NESTED_DEPTH:
        if isinstance(value, (dict, list)):
            return None
        return _scalar_value(value)
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in list(value.items())[:MAX_DICT_KEYS]:
            key_text = _optional_text(key, max_length=MAX_ID_LENGTH)
            if not key_text:
                continue
            result[key_text] = _bounded_json_value(item, depth=depth + 1)
        return result
    if isinstance(value, list):
        return [_bounded_json_value(item, depth=depth + 1) for item in value[:MAX_LIST_ITEMS]]
    return _scalar_value(value)


def _scalar_value(value: Any) -> Any:
    if isinstance(value, str):
        return value.strip()[:MAX_NESTED_TEXT_LENGTH]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value).strip()[:MAX_NESTED_TEXT_LENGTH]
