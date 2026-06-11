from __future__ import annotations

from typing import Any

from aidm_server.creatures.core_bestiary import core_creature
from aidm_server.creatures.schemas import normalize_creature_definition
from aidm_server.creatures.variants import create_creature_variant
from aidm_server.game_state.models import stable_slug


DEFAULT_PACK_ROLES = [
    ('common', 'goblin_skirmisher', 'skirmisher', 'easy'),
    ('common', 'bandit_thug', 'brute', 'standard'),
    ('common', 'wolf', 'beast', 'easy'),
    ('elite', 'bandit_captain', 'leader', 'hard'),
    ('elite', 'wraith', 'assassin', 'hard'),
    ('boss', 'cult_leader', 'boss', 'boss'),
]


def _themes(payload: dict[str, Any]) -> list[str]:
    values = payload.get('themes') or payload.get('campaignThemes') or payload.get('campaign_themes') or []
    if isinstance(values, str):
        values = [item.strip() for item in values.replace(';', ',').split(',')]
    if not isinstance(values, list):
        values = []
    result = [str(item).strip().lower().replace(' ', '_') for item in values if str(item or '').strip()]
    return result[:8] or ['adventure']


def _pack_name(title: str, theme: str, base_name: str, rank: str) -> str:
    prefix = theme.replace('_', ' ').title()
    if rank == 'boss':
        return f"{prefix} Sovereign"
    if rank == 'elite':
        return f"{prefix} {base_name}"
    return f"{prefix} {base_name}"


def generate_campaign_pack_bestiary(payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    payload = payload if isinstance(payload, dict) else {}
    themes = _themes(payload)
    title = str(payload.get('title') or payload.get('campaignTitle') or payload.get('campaign_title') or 'Campaign').strip()
    count = max(3, min(18, int(payload.get('count') or 8)))
    creatures: list[dict[str, Any]] = []
    for index in range(count):
        rank, base_id, role, difficulty = DEFAULT_PACK_ROLES[index % len(DEFAULT_PACK_ROLES)]
        theme = themes[index % len(themes)]
        base = core_creature(base_id) or core_creature('bandit_thug')
        variant = create_creature_variant(
            base,
            {
                'themeTags': [theme, stable_slug(title), rank],
                'desiredRole': role,
                'difficulty': difficulty,
                'descriptionHint': f'{title} {rank} bestiary creature',
            },
            party_level=max(1, int(payload.get('partyLevel') or payload.get('party_level') or 2)),
            party_size=max(1, int(payload.get('partySize') or payload.get('party_size') or 4)),
        )
        variant['id'] = stable_slug(f"{title} {rank} {theme} {base['id']} {index + 1}")
        variant['name'] = _pack_name(title, theme, base['name'], rank)
        variant['source'] = 'campaign_pack'
        variant['descriptionShort'] = f'A {rank} creature for {title}, shaped by {theme.replace("_", " ")} themes.'
        variant['descriptionLong'] = (
            f'{variant["name"]} is part of the campaign pack bestiary for {title}. '
            'It is intended to be reused before live creature generation is considered.'
        )
        variant['visualTags'] = sorted(set([*(variant.get('visualTags') or []), rank, theme, stable_slug(title)]))
        variant['behavior'] = dict(variant.get('behavior') or {})
        if rank == 'boss':
            variant['behavior']['combatRole'] = 'boss'
            variant['behavior']['intelligenceProfile'] = 'tactical'
            variant['behavior']['primaryGoal'] = 'protect_location'
        creatures.append(normalize_creature_definition(variant, source='campaign_pack'))
    return creatures
