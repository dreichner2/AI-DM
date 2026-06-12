from __future__ import annotations

import json
import os
from typing import Any

from flask import current_app, has_app_context

from aidm_server.contracts import ProviderRequest
from aidm_server.creatures.balance import analyze_creature_balance, auto_scale_creature
from aidm_server.creatures.schemas import normalize_creature_definition
from aidm_server.game_state.extraction.schemas import extract_json_object
from aidm_server.game_state.models import stable_slug
from aidm_server.llm_providers import get_helper_provider, helper_provider_name
from aidm_server.services.runtime_config import provider_configured
from aidm_server.telemetry import telemetry_event, telemetry_metric


CREATURE_HELPER_TASK = 'creature_generation'
CREATURE_HELPER_PREFIX = 'AIDM_CREATURE_HELPER'
CREATURE_SYSTEM_MESSAGE = (
    'You are a creature metadata generator for an AI fantasy RPG. '
    'Return balanced CreatureDefinition JSON only. Do not narrate.'
)


def _config_value(name: str) -> str:
    if has_app_context():
        value = current_app.config.get(name)
        if value not in (None, ''):
            return str(value)
    return os.getenv(name, '')


def _helper_provider_name() -> str:
    return helper_provider_name(CREATURE_HELPER_TASK)


def _helper_configured(provider_name: str) -> bool:
    if provider_name == 'deepseek':
        return bool(
            _config_value(f'{CREATURE_HELPER_PREFIX}_DEEPSEEK_API_KEY')
            or _config_value('AIDM_HELPER_DEEPSEEK_API_KEY')
            or provider_configured('deepseek')
        )
    if provider_name in {'nvidia', 'kimi'}:
        return bool(
            _config_value(f'{CREATURE_HELPER_PREFIX}_NVIDIA_API_KEY')
            or _config_value('AIDM_HELPER_NVIDIA_API_KEY')
            or provider_configured(provider_name)
        )
    if provider_name == 'gemini':
        return provider_configured('gemini')
    if provider_name == 'fallback':
        return True
    if provider_name in {'codex', 'codex_cli'}:
        return provider_configured(provider_name)
    return False


def creature_helper_enabled() -> bool:
    if has_app_context() and current_app.config.get('TESTING') and not current_app.config.get('AIDM_CREATURE_HELPER_IN_TESTS'):
        return False
    setting = str(_config_value('AIDM_CREATURE_HELPER_ENABLED') or 'auto').strip().lower()
    if setting in {'0', 'false', 'no', 'off', 'disabled'}:
        return False
    if setting in {'1', 'true', 'yes', 'on', 'enabled'}:
        return True
    return _helper_configured(_helper_provider_name())


def _list_text(value: Any) -> str:
    if isinstance(value, list):
        return ', '.join(str(item) for item in value if str(item or '').strip())
    return str(value or '').strip()


def build_creature_generation_prompt(input_payload: dict[str, Any]) -> str:
    return (
        'Convert the creature concept into one balanced CreatureDefinition JSON object.\n\n'
        'Required top-level fields: id, version, name, source, descriptionShort, descriptionLong, creatureType, visualTags, '
        'level, challengeTier, size, stats, movement, senses, resistances, vulnerabilities, immunities, abilities, behavior, '
        'lootTable, xpReward, aiNarrationHints, balance.\n\n'
        'Rules:\n'
        '- Return JSON only, with no markdown.\n'
        '- Source must be generated.\n'
        '- Prefer 1-3 core abilities for normal enemies; bosses may have more.\n'
        '- Every enemy needs a behavior profile with intelligenceProfile, combatRole, primaryGoal, aggression, selfPreservation, morale, fleeThreshold, surrenderThreshold, targetPriority, tactics, survivalRules, and personalityTags.\n'
        '- Mindless creatures never flee, negotiate, or surrender.\n'
        '- Animals have survival instincts and may retreat when badly hurt.\n'
        '- Intelligent enemies use cover, positioning, objectives, negotiation, surrender, and retreat when appropriate.\n'
        '- Avoid instant death, unlimited stun/paralysis, excessive immunity, and boss mechanics unless challengeTier is boss.\n'
        '- Prefer resistance over immunity.\n'
        '- If a concept is too strong, scale it down to target difficulty.\n\n'
        f"Campaign tone: {input_payload.get('campaignTone') or input_payload.get('campaign_tone') or 'adventurous'}\n"
        f"Campaign themes: {_list_text(input_payload.get('campaignThemes') or input_payload.get('campaign_themes'))}\n"
        f"Forbidden themes: {_list_text(input_payload.get('forbiddenThemes') or input_payload.get('forbidden_themes'))}\n"
        f"Party level: {input_payload.get('partyLevel') or input_payload.get('party_level') or 1}\n"
        f"Party size: {input_payload.get('partySize') or input_payload.get('party_size') or 4}\n"
        f"Location: {input_payload.get('location') or 'unknown'}\n"
        f"Region: {input_payload.get('region') or input_payload.get('regionId') or 'unknown'}\n"
        f"Encounter purpose: {input_payload.get('encounterPurpose') or input_payload.get('encounter_purpose') or 'custom'}\n"
        f"Desired difficulty: {input_payload.get('difficulty') or 'standard'}\n"
        f"Desired role: {input_payload.get('desiredRole') or input_payload.get('desired_role') or 'any'}\n"
        f"Desired creature type: {input_payload.get('desiredCreatureType') or input_payload.get('desired_creature_type') or 'any'}\n"
        f"Creature concept: {input_payload.get('creatureConcept') or input_payload.get('descriptionHint') or input_payload.get('description_hint') or 'appropriate local enemy'}\n"
        f"Existing bestiary names: {_list_text(input_payload.get('existingBestiaryNames') or input_payload.get('existing_bestiary_names'))}\n"
        f"World lore: {input_payload.get('existingWorldLore') or input_payload.get('existing_world_lore') or ''}\n"
        f"Max abilities: {input_payload.get('maxAbilities') or input_payload.get('max_abilities') or 3}\n"
        f"Allow flight: {bool(input_payload.get('allowFlight') or input_payload.get('allow_flight'))}\n"
        f"Allow hard control: {bool(input_payload.get('allowHardControl') or input_payload.get('allow_hard_control'))}\n"
        f"Allow instant death: {bool(input_payload.get('allowInstantDeath') or input_payload.get('allow_instant_death'))}\n"
    )


def deterministic_generated_creature(input_payload: dict[str, Any]) -> dict[str, Any]:
    theme_tags = [str(tag).strip() for tag in input_payload.get('themeTags') or input_payload.get('theme_tags') or [] if str(tag or '').strip()]
    concept = str(
        input_payload.get('creatureConcept')
        or input_payload.get('descriptionHint')
        or input_payload.get('description_hint')
        or 'Adaptive Foe'
    ).strip()
    difficulty = str(input_payload.get('difficulty') or 'standard').strip().lower()
    party_level = max(1, int(input_payload.get('partyLevel') or input_payload.get('party_level') or 1))
    role = str(input_payload.get('desiredRole') or input_payload.get('desired_role') or 'skirmisher').strip().lower()
    creature_type = str(input_payload.get('desiredCreatureType') or input_payload.get('desired_creature_type') or 'custom').strip().lower()
    name_seed = concept if len(concept.split()) <= 4 else ' '.join(concept.split()[:4])
    name = name_seed.title()
    hp_base = {'trivial': 5, 'easy': 8, 'standard': 12, 'hard': 17, 'deadly': 22, 'boss': 35}.get(difficulty, 12)
    damage_dice = {'trivial': '1d4', 'easy': '1d6', 'standard': '1d8+1', 'hard': '2d6+2', 'deadly': '2d8+3', 'boss': '3d8+4'}.get(difficulty, '1d8+1')
    creature = normalize_creature_definition(
        {
            'id': stable_slug(name),
            'version': 1,
            'name': name,
            'source': 'generated',
            'descriptionShort': f'A generated enemy shaped by {", ".join(theme_tags) if theme_tags else "the current scene"}.',
            'descriptionLong': f'{name} was generated because the current encounter needed a creature not covered by the existing bestiary.',
            'creatureType': creature_type,
            'visualTags': [*theme_tags, role, creature_type],
            'level': party_level,
            'challengeTier': difficulty,
            'size': 'medium',
            'stats': {
                'maxHp': max(3, hp_base * party_level),
                'armorClass': min(22, 11 + party_level // 3 + (2 if difficulty in {'hard', 'deadly', 'boss'} else 0)),
                'strength': 12,
                'dexterity': 12,
                'constitution': 12,
                'intelligence': 10,
                'wisdom': 10,
                'charisma': 9,
            },
            'movement': {'walk': 30},
            'senses': {'passivePerception': 10},
            'abilities': [
                {
                    'id': stable_slug(f'{name} strike'),
                    'name': 'Signature Strike',
                    'type': 'attack',
                    'description': f'{name} uses its signature attack.',
                    'actionCost': 'action',
                    'range': 'melee',
                    'targetType': 'single',
                    'damage': {'dice': damage_dice, 'type': 'slashing'},
                    'attackBonus': 2 + party_level // 2,
                    'cooldown': 'none',
                    'aiUseWhen': ['A target is exposed or blocking the creature objective.'],
                }
            ],
            'behavior': {
                'intelligenceProfile': 'average' if creature_type == 'humanoid' else 'animal' if creature_type == 'beast' else 'low_cunning',
                'combatRole': role,
                'primaryGoal': 'survive',
                'aggression': 55,
                'selfPreservation': 60,
                'morale': 50,
                'fleeThreshold': 25,
                'surrenderThreshold': 15,
                'targetPriority': ['wounded', 'isolated', 'nearest'],
                'tactics': ['Pursue the encounter objective before fighting to the death.'],
                'survivalRules': ['Retreat, surrender, or negotiate when the fight becomes unwinnable.'],
                'personalityTags': ['pragmatic'],
            },
            'aiNarrationHints': [f'Make {name} feel specific to the current location and objective.'],
        },
        source='generated',
    )
    analysis = analyze_creature_balance(
        creature,
        party_level=party_level,
        party_size=max(1, int(input_payload.get('partySize') or input_payload.get('party_size') or 4)),
        target_difficulty=difficulty,
    )
    creature['balance'] = analysis
    if analysis['estimatedTier'] == 'overpowered':
        creature = auto_scale_creature(
            creature,
            analysis,
            target_difficulty=difficulty,
            party_level=party_level,
            party_size=max(1, int(input_payload.get('partySize') or input_payload.get('party_size') or 4)),
        )
    return creature


def generate_new_creature(input_payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    fallback = deterministic_generated_creature(input_payload)
    if not creature_helper_enabled():
        return fallback, 'deterministic'
    try:
        response = get_helper_provider(task=CREATURE_HELPER_TASK).generate(
            ProviderRequest(
                prompt=build_creature_generation_prompt(input_payload),
                system_message=CREATURE_SYSTEM_MESSAGE,
            )
        )
        payload = extract_json_object(response.text)
        if not payload:
            raise ValueError('helper returned invalid JSON')
        creature = normalize_creature_definition(payload, source='generated')
        party_level = max(1, int(input_payload.get('partyLevel') or input_payload.get('party_level') or creature.get('level') or 1))
        party_size = max(1, int(input_payload.get('partySize') or input_payload.get('party_size') or 4))
        difficulty = str(input_payload.get('difficulty') or creature.get('challengeTier') or 'standard')
        analysis = analyze_creature_balance(creature, party_level=party_level, party_size=party_size, target_difficulty=difficulty)
        creature['balance'] = analysis
        if analysis['estimatedTier'] == 'overpowered' or analysis.get('warnings'):
            creature = auto_scale_creature(
                creature,
                analysis,
                target_difficulty=difficulty,
                party_level=party_level,
                party_size=party_size,
            )
        telemetry_metric('creature.helper.success_total', 1, tags={'model': response.model})
        return creature, response.model
    except Exception as exc:
        telemetry_event('creature.helper.failed', payload={'error': str(exc)[:300]}, severity='warning')
        return fallback, 'deterministic_fallback'
