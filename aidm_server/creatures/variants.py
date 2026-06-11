from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.creatures.balance import analyze_creature_balance, auto_scale_creature
from aidm_server.creatures.schemas import normalize_creature_definition
from aidm_server.game_state.models import stable_slug


def _theme_prefix(theme_tags: list[str]) -> str:
    for tag in theme_tags:
        text = str(tag or '').strip().replace('_', ' ').replace('-', ' ')
        if text:
            return text.title()
    return 'Altered'


def _damage_type_for_tags(tags: list[str]) -> str | None:
    normalized = {str(tag or '').strip().lower().replace(' ', '_').replace('-', '_') for tag in tags}
    if normalized & {'fire', 'ash', 'ember', 'cinder', 'volcanic'}:
        return 'fire'
    if normalized & {'ice', 'frost', 'winter'}:
        return 'cold'
    if normalized & {'shadow', 'grave', 'death', 'necrotic', 'undead'}:
        return 'necrotic'
    if normalized & {'storm', 'lightning', 'thunder'}:
        return 'lightning'
    if normalized & {'poison', 'venom', 'toxic'}:
        return 'poison'
    return None


def create_creature_variant(
    base_creature: dict[str, Any],
    request: dict[str, Any],
    *,
    party_level: int = 1,
    party_size: int = 4,
) -> dict[str, Any]:
    theme_tags = [str(tag).strip() for tag in request.get('themeTags') or request.get('theme_tags') or [] if str(tag or '').strip()]
    difficulty = str(request.get('difficulty') or base_creature.get('challengeTier') or 'standard')
    desired_role = str(request.get('desiredRole') or request.get('desired_role') or '').strip()
    variant = normalize_creature_definition(base_creature, source='generated_variant')
    prefix = _theme_prefix(theme_tags)
    base_name = variant['name']
    variant['id'] = stable_slug(f'{prefix} {base_name}')
    variant['name'] = f'{prefix} {base_name}'
    variant['source'] = 'generated_variant'
    variant['challengeTier'] = difficulty if difficulty else variant['challengeTier']
    variant['descriptionShort'] = f'{base_name} changed by {", ".join(theme_tags) if theme_tags else "local conditions"}.'
    variant['descriptionLong'] = (
        f'This generated variant keeps the combat identity of {base_name} but adapts its look, behavior, '
        f'and one signature pressure point to the current encounter.'
    )
    variant['visualTags'] = sorted(set([*variant.get('visualTags', []), *theme_tags, 'variant']))
    variant['behavior'] = dict(variant.get('behavior') or {})
    if desired_role:
        variant['behavior']['combatRole'] = desired_role
    variant['behavior']['tactics'] = [
        *list(variant['behavior'].get('tactics') or []),
        f'Use the local {prefix.lower()} traits to make the encounter feel specific to this region.',
    ]
    damage_type = _damage_type_for_tags(theme_tags)
    if damage_type:
        variant['resistances'] = sorted(set([*(variant.get('resistances') or []), damage_type]))
        first_attack = next((ability for ability in variant.get('abilities') or [] if isinstance(ability, dict) and ability.get('damage')), None)
        if first_attack:
            new_ability = deepcopy(first_attack)
            new_ability['id'] = stable_slug(f'{variant["id"]}_{damage_type}_surge')
            new_ability['name'] = f'{prefix} Surge'
            new_ability['description'] = f'{variant["name"]} channels {damage_type} through a familiar attack pattern.'
            new_ability['cooldown'] = 'once_per_combat'
            new_ability['damage'] = dict(new_ability.get('damage') or {})
            new_ability['damage']['type'] = damage_type
            variant['abilities'].append(new_ability)
    tier_boost = {'trivial': 0, 'easy': 1, 'standard': 2, 'hard': 4, 'deadly': 7, 'boss': 12}.get(difficulty, 2)
    if difficulty != base_creature.get('challengeTier'):
        variant['stats']['maxHp'] = max(1, variant['stats']['maxHp'] + tier_boost * max(1, party_level))
        variant['stats']['armorClass'] = min(24, variant['stats']['armorClass'] + (1 if difficulty in {'hard', 'deadly', 'boss'} else 0))
    analysis = analyze_creature_balance(variant, party_level=party_level, party_size=party_size, target_difficulty=variant['challengeTier'])
    variant['balance'] = analysis
    if analysis.get('estimatedTier') == 'overpowered':
        variant = auto_scale_creature(
            variant,
            analysis,
            target_difficulty=variant['challengeTier'],
            party_level=party_level,
            party_size=party_size,
        )
    variant['variantOf'] = base_creature.get('id')
    variant['variantReason'] = request.get('descriptionHint') or f'Generated as a {prefix.lower()} variant.'
    return variant
