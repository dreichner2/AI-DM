from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.creatures.core_bestiary import core_bestiary
from aidm_server.creatures.generator import generate_new_creature
from aidm_server.creatures.repository import list_bestiary_entries, save_bestiary_entry, should_save_generated_creature
from aidm_server.creatures.schemas import normalize_creature_definition
from aidm_server.creatures.variants import create_creature_variant
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


def _text(value: Any) -> str:
    return str(value or '').strip()


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip().lower().replace(' ', '_').replace('-', '_') for item in value if str(item or '').strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip().lower().replace(' ', '_').replace('-', '_')]
    return []


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
        'allowGeneration': bool(request.get('allowGeneration', request.get('allow_generation', True))),
        'allowVariants': bool(request.get('allowVariants', request.get('allow_variants', True))),
        'encounterDefinedCreatures': request.get('encounterDefinedCreatures', request.get('encounter_defined_creatures')) if isinstance(request.get('encounterDefinedCreatures', request.get('encounter_defined_creatures')), list) else [],
        'saveGenerated': request.get('saveGenerated', request.get('save_generated', True)) is not False,
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
    tags = []
    for value in [scene.get('sceneType'), scene.get('mood'), scene.get('name'), campaign.title if campaign else None]:
        for token in str(value or '').lower().replace('-', ' ').split():
            if len(token) > 3:
                tags.append(token)
    return {
        'campaignId': campaign.campaign_id,
        'sessionId': session_obj.session_id,
        'regionId': scene.get('regionId') or scene.get('locationId'),
        'locationId': scene.get('locationId'),
        'encounterPurpose': purpose,
        'themeTags': tags[:8],
        'partyLevel': round(sum(levels) / len(levels)) if levels else 1,
        'partySize': max(1, len(players)),
        'difficulty': 'standard',
        'descriptionHint': player_message,
        'allowGeneration': True,
        'allowVariants': True,
    }
