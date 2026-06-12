from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

from aidm_server.creatures.core_bestiary import core_bestiary
from aidm_server.creatures.generator import generate_new_creature
from aidm_server.creatures.repository import list_bestiary_entries, save_bestiary_entry, should_save_generated_creature
from aidm_server.creatures.schemas import normalize_creature_definition
from aidm_server.creatures.variants import create_creature_variant
from aidm_server.game_state.models import normalize_item_name, stable_slug
from aidm_server.models import Campaign, Session


PURPOSE_GOALS = {
    'ambush': 'kill_party',
    'guard': 'protect_location',
    'boss': 'kill_party',
    'patrol': 'protect_location',
    'ritual': 'complete_ritual',
    'random_encounter': 'survive',
    'predator': 'feed',
    'social_threat': 'negotiate',
    'custom': 'custom',
}

ENCOUNTER_MATCH_THRESHOLD = 0.6
SCOPED_BESTIARY_MATCH_THRESHOLD = 0.72
CORE_BESTIARY_MATCH_THRESHOLD = 0.6
MAX_ENCOUNTER_GROUPS = 8
MAX_ENCOUNTER_ENEMIES = 24


def _text(value: Any) -> str:
    return str(value or '').strip()


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip().lower().replace(' ', '_').replace('-', '_') for item in value if str(item or '').strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip().lower().replace(' ', '_').replace('-', '_')]
    return []


def _reference_terms(value: Any) -> set[str]:
    text = _text(value)
    if not text:
        return set()
    normalized = re.sub(r'[^a-z0-9]+', ' ', text.lower()).strip()
    slug = stable_slug(text)
    terms = {term for term in {normalized, slug, text.lower().strip()} if term}
    for term in list(terms):
        for article in ('the ', 'a ', 'an '):
            if term.startswith(article):
                terms.add(term[len(article) :].strip())
    return {term for term in terms if term}


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _enabled(value: Any, *, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {'0', 'false', 'no', 'off', 'disabled'}


def normalize_creature_request(request: dict[str, Any] | None) -> dict[str, Any]:
    request = request if isinstance(request, dict) else {}
    return {
        'campaignId': request.get('campaignId', request.get('campaign_id')),
        'sessionId': request.get('sessionId', request.get('session_id')),
        'regionId': _text(request.get('regionId', request.get('region_id'))),
        'locationId': _text(request.get('locationId', request.get('location_id'))),
        'encounterPurpose': _text(request.get('encounterPurpose', request.get('encounter_purpose')) or 'custom').lower(),
        'desiredRole': _text(request.get('desiredRole', request.get('desired_role'))),
        'desiredCreatureType': _text(request.get('desiredCreatureType', request.get('desired_creature_type'))),
        'themeTags': _list(request.get('themeTags', request.get('theme_tags'))),
        'partyLevel': max(1, int(request.get('partyLevel', request.get('party_level')) or 1)),
        'partySize': max(1, int(request.get('partySize', request.get('party_size')) or 4)),
        'difficulty': _text(request.get('difficulty') or 'standard').lower(),
        'descriptionHint': _text(request.get('descriptionHint', request.get('description_hint'))),
        'allowGeneration': _enabled(request.get('allowGeneration', request.get('allow_generation')), default=True),
        'allowVariants': _enabled(request.get('allowVariants', request.get('allow_variants')), default=True),
        'encounterDefinedCreatures': request.get('encounterDefinedCreatures', request.get('encounter_defined_creatures')) if isinstance(request.get('encounterDefinedCreatures', request.get('encounter_defined_creatures')), list) else [],
        'saveGenerated': _enabled(request.get('saveGenerated', request.get('save_generated')), default=True),
        'enemyCount': _bounded_int(request.get('enemyCount', request.get('enemy_count')), default=1, minimum=1, maximum=MAX_ENCOUNTER_ENEMIES),
    }


def _tag_overlap(left: list[str], right: list[str]) -> int:
    return len(set(_list(left)) & set(_list(right)))


def _entry_creature(entry: dict[str, Any]) -> dict[str, Any]:
    return normalize_creature_definition(entry.get('creature') if isinstance(entry, dict) else {}, source=entry.get('source') if isinstance(entry, dict) else None)


def score_creature_match(creature: dict[str, Any], entry: dict[str, Any], request: dict[str, Any]) -> float:
    score = 0.0
    if request.get('desiredCreatureType') and creature.get('creatureType') == request['desiredCreatureType']:
        score += 0.2
    if request.get('desiredRole') and (creature.get('behavior') or {}).get('combatRole') == request['desiredRole']:
        score += 0.2
    score += min(0.25, _tag_overlap(creature.get('visualTags') or [], request.get('themeTags') or []) * 0.05)
    if creature.get('challengeTier') == request.get('difficulty'):
        score += 0.15
    expected_goal = PURPOSE_GOALS.get(request.get('encounterPurpose'), 'custom')
    if (creature.get('behavior') or {}).get('primaryGoal') == expected_goal:
        score += 0.15
    if entry.get('campaign_id') and request.get('campaignId') and int(entry.get('campaign_id')) == int(request.get('campaignId')):
        score += 0.1
    if entry.get('region_id') and request.get('regionId') and entry.get('region_id') == request.get('regionId'):
        score += 0.1
    if request.get('locationId') and request.get('locationId') in (entry.get('location_ids') or []):
        score += 0.05
    name_blob = f"{creature.get('name')} {creature.get('descriptionShort')} {creature.get('descriptionLong')}".lower()
    for tag in request.get('themeTags') or []:
        if tag.replace('_', ' ') in name_blob:
            score += 0.03
    return min(1.0, round(score, 4))


def _theme_signal(creature: dict[str, Any], request: dict[str, Any]) -> bool:
    theme_tags = request.get('themeTags') or []
    if not theme_tags:
        return True
    if _tag_overlap(creature.get('visualTags') or [], theme_tags):
        return True
    name_blob = f"{creature.get('name')} {creature.get('descriptionShort')} {creature.get('descriptionLong')}".lower()
    return any(str(tag or '').replace('_', ' ') in name_blob for tag in theme_tags)


def _core_entries() -> list[dict[str, Any]]:
    entries = []
    for creature in core_bestiary():
        entries.append(
            {
                'scope': 'core',
                'source': 'core_bestiary',
                'campaign_id': None,
                'session_id': None,
                'region_id': None,
                'location_ids': [],
                'faction_ids': [],
                'tags': creature.get('visualTags') or [],
                'creature': creature,
            }
        )
    return entries


def _rank(entries: list[dict[str, Any]], request: dict[str, Any]) -> list[dict[str, Any]]:
    ranked = []
    for entry in entries:
        creature = _entry_creature(entry)
        ranked.append({'entry': entry, 'creature': creature, 'score': score_creature_match(creature, entry, request)})
    ranked.sort(key=lambda item: item['score'], reverse=True)
    return ranked


def _result(creature: dict[str, Any], *, source: str, method: str, score: float | None = None, generated: bool = False, saved: bool = False, notes: list[str] | None = None, debug: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        'creature': normalize_creature_definition(creature, source=source),
        'source': source,
        'resolutionMethod': method,
        'matchScore': score,
        'generated': generated,
        'savedToBestiary': saved,
        'notes': notes or [],
        'debug': debug or {},
    }


def resolve_creature_for_encounter(
    request_payload: dict[str, Any],
    *,
    workspace_id: str = 'owner',
) -> dict[str, Any]:
    request = normalize_creature_request(request_payload)
    campaign_id = int(request['campaignId']) if request.get('campaignId') else None
    session_id = int(request['sessionId']) if request.get('sessionId') else None
    debug: dict[str, Any] = {'request': request, 'rankings': {}}

    encounter_defined = []
    for raw_creature in request.get('encounterDefinedCreatures') or []:
        if isinstance(raw_creature, dict):
            creature = normalize_creature_definition(raw_creature, source=raw_creature.get('source') or 'campaign_pack')
            encounter_defined.append({'scope': 'encounter', 'source': creature['source'], 'creature': creature, 'tags': creature.get('visualTags') or []})
    ranked_encounter = _rank(encounter_defined, request)
    debug['rankings']['encounter'] = [{'id': item['creature']['id'], 'score': item['score']} for item in ranked_encounter[:5]]
    if ranked_encounter and ranked_encounter[0]['score'] >= ENCOUNTER_MATCH_THRESHOLD:
        top = ranked_encounter[0]
        return _result(top['creature'], source=top['creature']['source'], method='encounter_defined', score=top['score'], notes=['Encounter-defined creature matched.'], debug=debug)

    campaign_entries = (
        list_bestiary_entries(workspace_id=workspace_id, campaign_id=campaign_id, scope='campaign') if campaign_id else []
    )
    ranked_campaign = _rank(campaign_entries, request)
    debug['rankings']['campaign'] = [{'id': item['creature']['id'], 'score': item['score']} for item in ranked_campaign[:5]]
    if ranked_campaign and ranked_campaign[0]['score'] >= SCOPED_BESTIARY_MATCH_THRESHOLD:
        top = ranked_campaign[0]
        return _result(top['creature'], source=top['entry'].get('source') or top['creature']['source'], method='campaign_bestiary_match', score=top['score'], notes=['Campaign bestiary matched.'], debug=debug)

    region_entries = (
        list_bestiary_entries(workspace_id=workspace_id, campaign_id=campaign_id, scope='region', region_id=request.get('regionId')) if campaign_id and request.get('regionId') else []
    )
    ranked_region = _rank(region_entries, request)
    debug['rankings']['region'] = [{'id': item['creature']['id'], 'score': item['score']} for item in ranked_region[:5]]
    if ranked_region and ranked_region[0]['score'] >= SCOPED_BESTIARY_MATCH_THRESHOLD:
        top = ranked_region[0]
        return _result(top['creature'], source=top['entry'].get('source') or top['creature']['source'], method='region_bestiary_match', score=top['score'], notes=['Region bestiary matched.'], debug=debug)

    ranked_core = _rank(_core_entries(), request)
    debug['rankings']['core'] = [{'id': item['creature']['id'], 'score': item['score']} for item in ranked_core[:5]]
    if ranked_core and ranked_core[0]['score'] >= CORE_BESTIARY_MATCH_THRESHOLD and _theme_signal(ranked_core[0]['creature'], request):
        top = ranked_core[0]
        return _result(top['creature'], source='core_bestiary', method='core_bestiary_match', score=top['score'], notes=['Core bestiary matched.'], debug=debug)

    variant_candidates = [*ranked_campaign[:3], *ranked_region[:3], *ranked_core[:5]]
    variant_candidates.sort(key=lambda item: item['score'], reverse=True)
    if request.get('allowVariants') and variant_candidates and variant_candidates[0]['score'] >= 0.45:
        base = variant_candidates[0]
        variant = create_creature_variant(
            base['creature'],
            request,
            party_level=request['partyLevel'],
            party_size=request['partySize'],
        )
        saved = False
        if request.get('saveGenerated') and campaign_id and should_save_generated_creature(
            variant,
            {
                'region_id': request.get('regionId'),
                'encounter_purpose': request.get('encounterPurpose'),
            },
        ):
            save_bestiary_entry(
                workspace_id=workspace_id,
                campaign_id=campaign_id,
                session_id=session_id,
                region_id=request.get('regionId') or None,
                scope='region' if request.get('regionId') else 'session',
                source='generated_variant',
                persistence='region' if request.get('regionId') else 'session',
                creature=variant,
                tags=variant.get('visualTags') or [],
                location_ids=[request['locationId']] if request.get('locationId') else [],
                created_because=request.get('descriptionHint') or 'Resolver created a close-match variant.',
                base_creature_id=base['creature'].get('id'),
                variant_reason=variant.get('variantReason'),
            )
            saved = True
        return _result(
            variant,
            source='generated_variant',
            method='generated_variant',
            score=base['score'],
            generated=True,
            saved=saved,
            notes=[f"Variant generated from {base['creature'].get('name')}."],
            debug=debug,
        )

    if request.get('allowGeneration'):
        existing_names = [item['creature']['name'] for item in [*ranked_campaign, *ranked_region, *ranked_core[:8]] if item.get('creature')]
        generation_input = {
            **request,
            'existingBestiaryNames': existing_names,
            'creatureConcept': request.get('descriptionHint') or ' '.join(request.get('themeTags') or []) or 'appropriate encounter creature',
        }
        generated, model_name = generate_new_creature(generation_input)
        saved = False
        if request.get('saveGenerated') and campaign_id and should_save_generated_creature(
            generated,
            {
                'region_id': request.get('regionId'),
                'encounter_purpose': request.get('encounterPurpose'),
            },
        ):
            save_bestiary_entry(
                workspace_id=workspace_id,
                campaign_id=campaign_id,
                session_id=session_id,
                region_id=request.get('regionId') or None,
                scope='region' if request.get('regionId') else 'session',
                source='generated',
                persistence='region' if request.get('regionId') else 'session',
                creature=generated,
                tags=generated.get('visualTags') or [],
                location_ids=[request['locationId']] if request.get('locationId') else [],
                created_because=request.get('descriptionHint') or 'Resolver generated a new creature.',
                created_by_model=model_name,
            )
            saved = True
        debug['generatedModel'] = model_name
        return _result(
            generated,
            source='generated',
            method='generated_new',
            generated=True,
            saved=saved,
            notes=[f"New creature generated by {model_name}."],
            debug=debug,
        )

    fallback = ranked_core[0] if ranked_core else {'creature': core_bestiary()[0], 'score': 0.0}
    return _result(
        fallback['creature'],
        source='core_bestiary',
        method='core_bestiary_match',
        score=fallback.get('score', 0.0),
        notes=['Generation disabled; resolver fell back to closest core creature.'],
        debug=debug,
    )


def _encounter_group_payloads(request_payload: dict[str, Any], request: dict[str, Any]) -> list[dict[str, Any]]:
    raw_groups = (
        request_payload.get('enemyGroups')
        or request_payload.get('enemy_groups')
        or request_payload.get('encounterGroups')
        or request_payload.get('encounter_groups')
    )
    if isinstance(raw_groups, list) and raw_groups:
        return [group for group in raw_groups[:MAX_ENCOUNTER_GROUPS] if isinstance(group, dict)]

    explicit_creatures = request.get('encounterDefinedCreatures') or []
    if explicit_creatures:
        return [
            {
                'creature': creature,
                'count': 1,
                'label': f'encounter_defined_{index + 1}',
            }
            for index, creature in enumerate(explicit_creatures[:MAX_ENCOUNTER_GROUPS])
            if isinstance(creature, dict)
        ]

    purpose = request.get('encounterPurpose') or 'custom'
    difficulty = request.get('difficulty') or 'standard'
    party_size = _bounded_int(request.get('partySize'), default=4, minimum=1, maximum=10)
    requested_count = _bounded_int(request.get('enemyCount'), default=1, minimum=1, maximum=MAX_ENCOUNTER_ENEMIES)
    if requested_count > 1:
        return [{'count': requested_count, 'label': 'requested_group'}]

    if difficulty == 'boss' or purpose == 'boss':
        groups = [
            {
                'count': 1,
                'difficulty': 'boss',
                'desiredRole': 'boss',
                'encounterPurpose': 'boss',
                'label': 'boss',
            }
        ]
        if party_size >= 3 and request.get('allowVariants'):
            groups.append(
                {
                    'count': min(4, max(1, party_size - 2)),
                    'difficulty': 'easy',
                    'desiredRole': 'minion',
                    'encounterPurpose': 'guard',
                    'label': 'support_minions',
                }
            )
        return groups

    if purpose in {'ambush', 'patrol', 'guard'} and party_size >= 4 and difficulty in {'standard', 'hard', 'deadly'}:
        return [
            {
                'count': 2,
                'desiredRole': request.get('desiredRole') or 'skirmisher',
                'label': 'pressure_pair',
            },
            {
                'count': 1,
                'desiredRole': 'brute' if purpose != 'guard' else 'leader',
                'difficulty': 'easy' if difficulty == 'standard' else difficulty,
                'label': 'anchor_enemy',
            },
        ]

    return [{'count': 1, 'label': 'single_threat'}]


def _merge_group_request(base_request: dict[str, Any], raw_group: dict[str, Any]) -> dict[str, Any]:
    allowed_keys = {
        'campaignId',
        'campaign_id',
        'sessionId',
        'session_id',
        'regionId',
        'region_id',
        'locationId',
        'location_id',
        'encounterPurpose',
        'encounter_purpose',
        'desiredRole',
        'desired_role',
        'desiredCreatureType',
        'desired_creature_type',
        'themeTags',
        'theme_tags',
        'partyLevel',
        'party_level',
        'partySize',
        'party_size',
        'difficulty',
        'descriptionHint',
        'description_hint',
        'allowGeneration',
        'allow_generation',
        'allowVariants',
        'allow_variants',
        'saveGenerated',
        'save_generated',
        'encounterDefinedCreatures',
        'encounter_defined_creatures',
        'boundNpc',
        'bound_npc',
    }
    merged = dict(base_request)
    for key, value in raw_group.items():
        if key in allowed_keys and value not in (None, '', [], {}):
            merged[key] = value
    group_tags = _list(raw_group.get('themeTags', raw_group.get('theme_tags')))
    if group_tags:
        merged['themeTags'] = [*base_request.get('themeTags', []), *group_tags]
    if raw_group.get('creature') and isinstance(raw_group.get('creature'), dict):
        merged['encounterDefinedCreatures'] = [raw_group['creature']]
    bound_npc = raw_group.get('boundNpc', raw_group.get('bound_npc'))
    if isinstance(bound_npc, dict):
        merged['boundNpc'] = bound_npc
    return merged


def _group_result_from_resolution(
    *,
    resolution: dict[str, Any] | None,
    raw_group: dict[str, Any],
    group_request: dict[str, Any],
    index: int,
) -> dict[str, Any]:
    if not isinstance(resolution, dict):
        resolution = _result(
            core_bestiary()[0],
            source='core_bestiary',
            method='resolver_fallback',
            score=0.0,
            notes=['Resolver returned no creature; fallback core creature used.'],
            debug={'request': group_request},
        )
    creature = normalize_creature_definition(resolution.get('creature') if isinstance(resolution, dict) else {}, source=resolution.get('source'))
    creature = _apply_npc_binding_to_creature(creature, group_request.get('boundNpc') if isinstance(group_request.get('boundNpc'), dict) else None)
    count = _bounded_int(raw_group.get('count', raw_group.get('enemyCount', raw_group.get('enemy_count'))), default=1, minimum=1, maximum=MAX_ENCOUNTER_ENEMIES)
    return {
        'id': str(raw_group.get('id') or raw_group.get('label') or f'group_{index + 1}'),
        'label': str(raw_group.get('label') or creature.get('name') or f'Group {index + 1}'),
        'count': count,
        'creature': creature,
        'source': resolution.get('source') or creature.get('source'),
        'resolutionMethod': resolution.get('resolutionMethod'),
        'matchScore': resolution.get('matchScore'),
        'generated': bool(resolution.get('generated')),
        'savedToBestiary': bool(resolution.get('savedToBestiary')),
        'notes': resolution.get('notes') or [],
        'request': group_request,
        'debug': resolution.get('debug') or {},
        'boundNpc': creature.get('npcBinding') or None,
    }


def _encounter_goal_type(purpose: str) -> str:
    return {
        'ambush': 'kill_all_enemies',
        'guard': 'defend_location',
        'boss': 'kill_all_enemies',
        'patrol': 'defend_location',
        'ritual': 'stop_ritual',
        'random_encounter': 'kill_all_enemies',
        'predator': 'feed',
        'social_threat': 'negotiate',
    }.get(purpose, 'custom')


def _scene_npc_reference_ids(scene: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for raw_id in scene.get('activeNpcIds', scene.get('active_npc_ids')) or []:
        ids.update(_reference_terms(raw_id))
    for source_key in ('characterPositions', 'character_positions', 'characterZones', 'character_zones'):
        source = scene.get(source_key)
        if isinstance(source, dict):
            for raw_id in source.keys():
                ids.update(_reference_terms(raw_id))
    return ids


def _state_scene_npcs(state: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    npcs: list[dict[str, Any]] = []
    for key in ('npcs', 'knownNpcs', 'known_npcs', 'partyNpcs', 'party_npcs'):
        values = state.get(key)
        if not isinstance(values, list):
            continue
        for npc in values:
            if not isinstance(npc, dict):
                continue
            identity = _text(npc.get('id') or npc.get('npcId') or npc.get('name') or npc.get('npcName')).lower()
            if identity and identity in seen:
                continue
            if identity:
                seen.add(identity)
            npcs.append(npc)
    return npcs


def _npc_is_hostile(npc: dict[str, Any]) -> bool:
    disposition = str(npc.get('disposition') or '').strip().lower()
    status = str(npc.get('status') or '').strip().lower()
    return disposition in {'hostile', 'enemy', 'aggressive'} and status not in {'dead', 'defeated', 'fled'}


def _npc_is_unavailable(npc: dict[str, Any]) -> bool:
    status = str(npc.get('status') or '').strip().lower()
    return status in {'dead', 'defeated', 'fled', 'hidden', 'missing', 'offscreen'}


def _npc_can_be_combat_target(
    npc: dict[str, Any],
    *,
    scene: dict[str, Any],
    player_message: str,
    message_terms: set[str],
    scene_terms: set[str],
) -> bool:
    if _npc_is_unavailable(npc):
        return False
    disposition = str(npc.get('disposition') or '').strip().lower()
    if disposition in {'friendly', 'ally', 'allied', 'companion'}:
        return False
    if _npc_is_hostile(npc):
        return True
    npc_terms = _npc_reference_terms(npc)
    if message_terms.intersection(npc_terms):
        return True
    scene_combat_state = str(scene.get('combatState') or '').strip().lower()
    scene_type = str(scene.get('sceneType') or '').strip().lower()
    try:
        danger_level = int(scene.get('dangerLevel') or 0)
    except (TypeError, ValueError):
        danger_level = 0
    active_scene_target = bool(scene_terms.intersection(npc_terms))
    combatish_scene = scene_combat_state in {'pending', 'active'} or scene_type == 'combat' or danger_level >= 5
    return active_scene_target and combatish_scene and _message_requests_single_target(player_message)


def _npc_reference_terms(npc: dict[str, Any]) -> set[str]:
    terms: set[str] = set()
    for value in (
        npc.get('id'),
        npc.get('npcId'),
        npc.get('name'),
        npc.get('npcName'),
        npc.get('role'),
        npc.get('title'),
    ):
        terms.update(_reference_terms(value))
    for alias in npc.get('aliases') or []:
        terms.update(_reference_terms(alias))
    return terms


def _direction_terms(value: Any) -> set[str]:
    normalized = normalize_item_name(value)
    terms: set[str] = set()
    if re.search(r'\bright(?:\s*(?:hand|flank|slope|side))?\b', normalized):
        terms.add('right')
    if re.search(r'\bleft(?:\s*(?:hand|flank|slope|side))?\b', normalized):
        terms.add('left')
    if re.search(r'\b(?:middle|center|centre)\b', normalized):
        terms.add('middle')
    if re.search(r'\b(?:front|nearest|closest)\b', normalized):
        terms.add('front')
    if re.search(r'\b(?:back|rear|far|farthest)\b', normalized):
        terms.add('back')
    return terms


def _npc_direction_terms(npc: dict[str, Any]) -> set[str]:
    values = [
        npc.get('id'),
        npc.get('npcId'),
        npc.get('name'),
        npc.get('npcName'),
        npc.get('role'),
        npc.get('title'),
        npc.get('locationId'),
        _npc_memory_text(npc),
    ]
    values.extend(npc.get('aliases') or [])
    terms: set[str] = set()
    for value in values:
        terms.update(_direction_terms(value))
    return terms


def _npc_memory_text(npc: dict[str, Any]) -> str:
    memory = npc.get('memory')
    if isinstance(memory, list):
        return ' '.join(str(item or '') for item in memory)
    return str(memory or '')


def _generic_placeholder_npc(npc: dict[str, Any]) -> bool:
    name = _text(npc.get('name') or npc.get('npcName') or npc.get('id') or npc.get('npcId')).lower()
    return bool(re.match(r'^(?:captor|guard|bandit|raider|cultist|enemy|scout|sentry|orc|goblin)\s*#?\d+$', name.replace('_', ' ')))


def _npc_binding_payload(npc: dict[str, Any]) -> dict[str, Any]:
    npc_id = _text(npc.get('id') or npc.get('npcId') or npc.get('name'))
    npc_name = _display_name(npc.get('name') or npc.get('npcName'), npc_id)
    return {
        'npcId': npc_id,
        'npcName': npc_name,
        'disposition': _text(npc.get('disposition')),
        'status': _text(npc.get('status')),
        'locationId': _text(npc.get('locationId')),
    }


def _apply_npc_binding_to_creature(creature: dict[str, Any], binding: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(binding, dict) or not _text(binding.get('npcId') or binding.get('npcName')):
        return creature
    bound = deepcopy(creature)
    npc_id = _text(binding.get('npcId') or binding.get('npcName'))
    npc_name = _display_name(binding.get('npcName'), npc_id)
    archetype_name = _text(bound.get('creatureTypeName') or bound.get('name') or 'Creature')
    bound['creatureTypeName'] = archetype_name
    if npc_name and npc_name.lower() not in str(bound.get('name') or '').lower():
        bound['name'] = f'{archetype_name} ({npc_name})'
    bound['npcBinding'] = {
        'npcId': npc_id,
        'npcName': npc_name,
        'creatureTypeName': archetype_name,
        **{key: value for key, value in binding.items() if value not in (None, '')},
    }
    aliases = [*(bound.get('aliases') or []), npc_id, npc_name, archetype_name, f'{archetype_name} {npc_name}']
    bound['aliases'] = list(dict.fromkeys(str(alias).strip() for alias in aliases if str(alias or '').strip()))[:12]
    hints = list(bound.get('aiNarrationHints') or [])
    hints.insert(0, f'This combatant is the known NPC {npc_name}; preserve that identity while using the {archetype_name} mechanics.')
    bound['aiNarrationHints'] = list(dict.fromkeys(hints))[:10]
    return bound


def _message_requests_single_target(player_message: str) -> bool:
    normalized = str(player_message or '').lower()
    if re.search(r'\b(?:one of|figure|shape|target|head|him|her|it|its|that one|the one)\b', normalized):
        return True
    return bool(re.search(r'\b(?:shoot|stab|slash|strike|attack|kill|hit|cut|lunge)\s+(?:the|a|an|its|their|his|her)\b', normalized))


def _npc_encounter_score(npc: dict[str, Any], *, player_message: str, message_terms: set[str], scene_terms: set[str]) -> int:
    npc_terms = _npc_reference_terms(npc)
    if not npc_terms:
        return 0
    score = 0
    if message_terms.intersection(npc_terms):
        score += 12
    if scene_terms.intersection(npc_terms):
        score += 4
    memory_text = _npc_memory_text(npc)
    memory_terms = _reference_terms(memory_text)
    if message_terms.intersection(memory_terms):
        score += 6
    direction_terms = _direction_terms(player_message)
    if direction_terms and direction_terms.intersection(_npc_direction_terms(npc)):
        score += 10
    normalized_message = str(player_message or '').lower()
    normalized_identity = normalize_item_name(f"{npc.get('id') or ''} {npc.get('name') or ''}")
    shelter_words = {'shelter', 'tent', 'inside', 'within'}
    if any(word in normalized_message for word in shelter_words):
        memory_norm = normalize_item_name(memory_text)
        if any(word in memory_norm for word in shelter_words):
            score += 8
        elif re.search(r'\b(?:captor|enemy|guard|figure)\s*3\b', normalized_identity):
            score += 6
    if 'one of' in normalized_message and scene_terms.intersection(npc_terms):
        score += 2
    return score


def _selected_hostile_scene_npcs(
    *,
    state: dict[str, Any],
    player_message: str,
) -> list[dict[str, Any]]:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    npcs = _state_scene_npcs(state)
    scene_terms = _scene_npc_reference_ids(scene)
    message_terms = _reference_terms(player_message)
    scored: list[tuple[int, int, dict[str, Any]]] = []
    for index, npc in enumerate(npcs):
        if not isinstance(npc, dict) or not _npc_can_be_combat_target(
            npc,
            scene=scene,
            player_message=player_message,
            message_terms=message_terms,
            scene_terms=scene_terms,
        ):
            continue
        score = _npc_encounter_score(npc, player_message=player_message, message_terms=message_terms, scene_terms=scene_terms)
        if score > 0:
            scored.append((score, index, npc))
    if not scored:
        return []
    scored.sort(key=lambda item: (-item[0], item[1]))
    direct = [npc for score, _, npc in scored if score >= 12]
    if direct:
        return direct[:MAX_ENCOUNTER_GROUPS]
    top_score = scored[0][0]
    top = [npc for score, _, npc in scored if score == top_score]
    if len(top) == 1 and top_score >= 6:
        return top
    if _message_requests_single_target(player_message):
        return [scored[0][2]]
    scene_matches = [npc for _, _, npc in scored if _npc_reference_terms(npc).intersection(scene_terms)]
    if len(scene_matches) == 1:
        return scene_matches
    return [npc for _, _, npc in scored[:MAX_ENCOUNTER_GROUPS]] if len(scored) == 1 else []


def _npc_has_combat_definition(npc: dict[str, Any]) -> bool:
    if isinstance(npc.get('abilities'), list) and npc.get('abilities'):
        return True
    if isinstance(npc.get('stats'), dict) and npc.get('stats'):
        return True
    if isinstance(npc.get('health'), dict) and npc.get('health'):
        return True
    return False


def _message_directly_references_npc(npc: dict[str, Any], message_terms: set[str]) -> bool:
    return bool(message_terms.intersection(_npc_reference_terms(npc)))


def _npc_should_use_generated_binding(npc: dict[str, Any], *, player_message: str) -> bool:
    if _generic_placeholder_npc(npc):
        return True
    message_terms = _reference_terms(player_message)
    if _message_directly_references_npc(npc, message_terms):
        return False
    if _npc_has_combat_definition(npc):
        return False
    status = str(npc.get('status') or '').strip().lower()
    if status in {'met', 'introduced', 'present'} and not _npc_direction_terms(npc):
        return False
    return True


def _display_name(value: Any, fallback: str) -> str:
    text = _text(value)
    if not text:
        text = fallback
    if text.islower() or '_' in text:
        return text.replace('_', ' ').title()
    return text


def _npc_creature_definition(npc: dict[str, Any], *, party_level: int, difficulty: str) -> dict[str, Any]:
    npc_id = _text(npc.get('id') or npc.get('npcId') or npc.get('name')) or 'npc_enemy'
    name = _display_name(npc.get('name'), npc_id)
    health = npc.get('health') if isinstance(npc.get('health'), dict) else {}
    max_hp = _bounded_int(
        health.get('maxHp', health.get('max', health.get('currentHp', health.get('current')))),
        default=max(1, party_level) * 10,
        minimum=1,
        maximum=999,
    )
    aliases = sorted(_npc_reference_terms(npc) | {f'the {name.lower()}'})
    return {
        'id': stable_slug(npc_id or name),
        'name': name,
        'source': 'user_custom',
        'descriptionShort': _text(npc.get('memory') or npc.get('role')) or f'{name} is a known hostile NPC.',
        'descriptionLong': _text(npc.get('memory') or npc.get('role')) or f'{name} is a known hostile NPC in the current scene.',
        'creatureType': 'custom',
        'aliases': aliases,
        'level': max(1, party_level),
        'challengeTier': 'boss' if difficulty == 'boss' else 'standard',
        'stats': {
            'maxHp': max_hp,
            'armorClass': _bounded_int((npc.get('stats') or {}).get('armorClass') if isinstance(npc.get('stats'), dict) else None, default=12, minimum=5, maximum=30),
        },
        'behavior': {
            'intelligenceProfile': 'tactical',
            'combatRole': 'boss' if difficulty == 'boss' else 'brute',
            'primaryGoal': 'test_party',
            'selfPreservation': 10,
            'morale': 80,
            'fleeThreshold': 0,
            'surrenderThreshold': 0,
            'targetPriority': ['last_damaged_by', 'nearest'],
            'tactics': ['Fight as the established NPC from the scene; do not turn into a weapon-derived creature.'],
            'personalityTags': ['named_npc'],
            'survivalRules': {
                'fightToDeath': True,
                'fleeBelowHpPercent': 0,
                'surrenderBelowHpPercent': 0,
                'fleeIfLeaderDies': False,
                'fleeIfAlone': False,
                'notes': ['Named hostile NPC remains present unless narration explicitly ends the encounter.'],
            },
        },
        'visualTags': ['named_npc', stable_slug(name)],
        'aiNarrationHints': [f'Portray {name} as the established NPC already present in the scene.'],
    }


def _encounter_defined_creatures_from_scene_npcs(
    *,
    state: dict[str, Any],
    player_message: str,
    party_level: int,
    difficulty: str,
) -> list[dict[str, Any]]:
    selected = [
        npc
        for npc in _selected_hostile_scene_npcs(state=state, player_message=player_message)
        if not _npc_should_use_generated_binding(npc, player_message=player_message)
    ]
    return [
        _npc_creature_definition(npc, party_level=party_level, difficulty=difficulty)
        for npc in selected[:MAX_ENCOUNTER_GROUPS]
    ]


def _bound_generated_npc_groups(
    *,
    state: dict[str, Any],
    player_message: str,
) -> list[dict[str, Any]]:
    selected = [
        npc
        for npc in _selected_hostile_scene_npcs(state=state, player_message=player_message)
        if _npc_should_use_generated_binding(npc, player_message=player_message)
    ]
    groups: list[dict[str, Any]] = []
    for index, npc in enumerate(selected[:MAX_ENCOUNTER_GROUPS]):
        binding = _npc_binding_payload(npc)
        memory = _npc_memory_text(npc)
        groups.append(
            {
                'count': 1,
                'label': f"bound_npc_{binding['npcId'] or index + 1}",
                'boundNpc': binding,
                'descriptionHint': ' '.join(
                    part
                    for part in [
                        player_message,
                        f"Known NPC {binding.get('npcName') or binding.get('npcId')}.",
                        memory,
                    ]
                    if part
                ),
            }
        )
    return groups


def resolve_creatures_for_encounter(
    request_payload: dict[str, Any],
    *,
    workspace_id: str = 'owner',
) -> dict[str, Any]:
    request = normalize_creature_request(request_payload)
    raw_groups = _encounter_group_payloads(request_payload if isinstance(request_payload, dict) else {}, request)
    groups: list[dict[str, Any]] = []
    total_enemies = 0
    for index, raw_group in enumerate(raw_groups):
        if total_enemies >= MAX_ENCOUNTER_ENEMIES:
            break
        group_request = _merge_group_request(request, raw_group)
        group_request['enemyCount'] = 1
        if isinstance(raw_group.get('creature'), dict):
            explicit_creature = normalize_creature_definition(
                raw_group['creature'],
                source=raw_group['creature'].get('source') or 'campaign_pack',
            )
            resolution = _result(
                explicit_creature,
                source=explicit_creature.get('source') or 'campaign_pack',
                method='encounter_defined',
                score=1.0,
                notes=['Encounter group supplied an explicit creature.'],
                debug={'request': group_request, 'rankings': {'encounter': [{'id': explicit_creature['id'], 'score': 1.0}]}},
            )
        else:
            resolution = resolve_creature_for_encounter(group_request, workspace_id=workspace_id)
        group = _group_result_from_resolution(
            resolution=resolution,
            raw_group=raw_group,
            group_request=group_request,
            index=index,
        )
        remaining = MAX_ENCOUNTER_ENEMIES - total_enemies
        group['count'] = min(group['count'], remaining)
        total_enemies += group['count']
        groups.append(group)

    if not groups:
        resolution = resolve_creature_for_encounter(request, workspace_id=workspace_id)
        groups.append(
            _group_result_from_resolution(
                resolution=resolution,
                raw_group={'count': 1, 'label': 'fallback'},
                group_request=request,
                index=0,
            )
        )
        total_enemies = 1

    methods = sorted({str(group.get('resolutionMethod') or '') for group in groups if group.get('resolutionMethod')})
    sources = sorted({str(group.get('source') or '') for group in groups if group.get('source')})
    purpose = request.get('encounterPurpose') or 'custom'
    return {
        'groups': groups,
        'totalEnemies': total_enemies,
        'resolutionMethod': 'encounter_composed' if len(groups) > 1 or total_enemies > 1 else groups[0].get('resolutionMethod'),
        'resolutionMethods': methods,
        'sources': sources,
        'generated': any(group.get('generated') for group in groups),
        'savedToBestiary': any(group.get('savedToBestiary') for group in groups),
        'encounterGoal': {
            'type': _encounter_goal_type(purpose),
            'description': f"Resolve a {purpose.replace('_', ' ')} encounter with {total_enemies} hostile participant{'s' if total_enemies != 1 else ''}.",
            'enemyObjective': ', '.join(
                sorted(
                    {
                        str((group.get('creature') or {}).get('behavior', {}).get('primaryGoal') or '')
                        for group in groups
                        if isinstance((group.get('creature') or {}).get('behavior'), dict)
                    }
                    - {''}
                )
            )
            or PURPOSE_GOALS.get(purpose, 'survive'),
            'playerObjective': 'Survive, protect allies, and resolve the threat.',
            'successConditions': ['Enemies are defeated, flee, surrender, negotiate, or their objective is stopped.'],
            'failureConditions': ['Enemies achieve their objective or incapacitate the party.'],
        },
        'notes': [note for group in groups for note in (group.get('notes') or [])],
        'debug': {
            'request': request,
            'groupCount': len(groups),
            'totalEnemies': total_enemies,
            'groups': [
                {
                    'id': group.get('id'),
                    'label': group.get('label'),
                    'count': group.get('count'),
                    'creatureId': (group.get('creature') or {}).get('id'),
                    'source': group.get('source'),
                    'resolutionMethod': group.get('resolutionMethod'),
                    'matchScore': group.get('matchScore'),
                }
                for group in groups
            ],
        },
    }


def default_request_from_session(
    *,
    session_obj: Session,
    campaign: Campaign,
    state: dict[str, Any],
    player_message: str,
) -> dict[str, Any]:
    scene = state.get('currentScene') if isinstance(state.get('currentScene'), dict) else {}
    players = state.get('playerCharacters') if isinstance(state.get('playerCharacters'), list) else []
    levels = [int(player.get('level') or 1) for player in players if isinstance(player, dict)]
    message = str(player_message or '').lower()
    purpose = 'ambush' if any(word in message for word in ('ambush', 'attack', 'fight', 'enemy', 'monster')) else 'random_encounter'
    plural_signal = any(
        word in message
        for word in ('enemies', 'monsters', 'bandits', 'goblins', 'wolves', 'skeletons', 'zombies', 'cultists', 'guards')
    )
    party_size = max(1, len(players))
    tags = []
    for value in [scene.get('sceneType'), scene.get('mood'), scene.get('name'), campaign.title if campaign else None]:
        for token in str(value or '').lower().replace('-', ' ').split():
            if len(token) > 3:
                tags.append(token)
    party_level = round(sum(levels) / len(levels)) if levels else 1
    difficulty = 'standard'
    encounter_defined_creatures = _encounter_defined_creatures_from_scene_npcs(
        state=state,
        player_message=player_message,
        party_level=party_level,
        difficulty=difficulty,
    )
    bound_generated_npc_groups = _bound_generated_npc_groups(
        state=state,
        player_message=player_message,
    )
    request = {
        'campaignId': campaign.campaign_id,
        'sessionId': session_obj.session_id,
        'regionId': scene.get('regionId') or scene.get('locationId'),
        'locationId': scene.get('locationId'),
        'encounterPurpose': purpose,
        'themeTags': tags[:8],
        'partyLevel': party_level,
        'partySize': party_size,
        'difficulty': difficulty,
        'descriptionHint': player_message,
        'allowGeneration': True,
        'allowVariants': True,
        'enemyCount': min(4, max(2, party_size)) if plural_signal else 1,
    }
    if bound_generated_npc_groups:
        request['enemyGroups'] = bound_generated_npc_groups
        request['enemyCount'] = len(bound_generated_npc_groups)
    elif encounter_defined_creatures:
        request['encounterDefinedCreatures'] = encounter_defined_creatures
        request['allowGeneration'] = False
        request['allowVariants'] = False
        request['enemyCount'] = len(encounter_defined_creatures)
    return request
