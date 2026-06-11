from __future__ import annotations

from copy import deepcopy
from typing import Any

from aidm_server.creatures.balance import analyze_creature_balance, auto_scale_creature
from aidm_server.creatures.schemas import normalize_creature_definition
from aidm_server.game_state.models import stable_slug


def _list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or '').strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def evolve_creature(
    base_creature: dict[str, Any],
    event_context: dict[str, Any] | None = None,
    *,
    party_level: int = 1,
    party_size: int = 4,
) -> dict[str, Any]:
    context = event_context if isinstance(event_context, dict) else {}
    base = normalize_creature_definition(base_creature, source=base_creature.get('source') if isinstance(base_creature, dict) else None)
    event_tags = _list(context.get('eventTags') or context.get('event_tags') or context.get('tags'))
    history = _list(context.get('personalHistory') or context.get('personal_history') or context.get('history'))
    grudge_target = str(context.get('grudgeTargetId') or context.get('grudge_target_id') or '').strip()
    scar_name = str(context.get('newName') or context.get('new_name') or '').strip()
    if not scar_name:
        suffix = 'the Scarred' if any(tag.lower() in {'fire', 'burned', 'scarred'} for tag in event_tags) else 'the Changed'
        scar_name = f"{base['name']} {suffix}"

    evolved = deepcopy(base)
    evolved['id'] = stable_slug(scar_name)
    evolved['name'] = scar_name[:100]
    evolved['source'] = 'evolved'
    evolved['version'] = int(evolved.get('version') or 1) + 1
    evolved['descriptionShort'] = f"{base['name']} changed because of prior player actions."
    evolved['descriptionLong'] = (
        f"{base['name']} survived an important campaign event and now carries persistent scars, motives, "
        'and behavior changes tied to the party.'
    )
    evolved['visualTags'] = sorted(set([*(evolved.get('visualTags') or []), *event_tags, 'evolved']))
    evolved['personalHistory'] = history or [context.get('reason') or 'Changed by campaign events.']
    evolved['baseCreatureId'] = base.get('id')
    evolved['behavior'] = dict(evolved.get('behavior') or {})
    evolved['behavior']['primaryGoal'] = context.get('primaryGoal') or 'survive'
    evolved['behavior']['secondaryGoals'] = sorted(set([*(evolved['behavior'].get('secondaryGoals') or []), 'revenge'] if grudge_target else evolved['behavior'].get('secondaryGoals') or []))
    evolved['behavior']['targetPriority'] = [
        *(['personal_grudge_target'] if grudge_target else []),
        *[item for item in evolved['behavior'].get('targetPriority') or [] if item != 'nearest'],
        'isolated',
        'nearest',
    ][:8]
    evolved['behavior']['personalityTags'] = sorted(set([*(evolved['behavior'].get('personalityTags') or []), 'vengeful' if grudge_target else 'changed']))
    evolved['behavior']['selfPreservation'] = max(0, min(100, int(evolved['behavior'].get('selfPreservation') or 50) + 10))
    evolved['behavior']['morale'] = max(0, min(100, int(evolved['behavior'].get('morale') or 50) + 5))
    evolved['behavior']['specialBehaviorNotes'] = [
        *(evolved['behavior'].get('specialBehaviorNotes') or []),
        'Remembers prior player actions and should behave as a recurring campaign presence.',
    ]
    if grudge_target:
        evolved['combatMemorySeed'] = {'personalGrudgeTargetId': grudge_target}

    if any(tag.lower() in {'fire', 'burned', 'ash', 'ember'} for tag in event_tags):
        evolved['resistances'] = sorted(set([*(evolved.get('resistances') or []), 'fire']))
        evolved['vulnerabilities'] = [item for item in evolved.get('vulnerabilities') or [] if item != 'fire']

    analysis = analyze_creature_balance(evolved, party_level=party_level, party_size=party_size, target_difficulty=evolved.get('challengeTier'))
    evolved['balance'] = analysis
    if analysis.get('estimatedTier') == 'overpowered':
        evolved = auto_scale_creature(
            evolved,
            analysis,
            target_difficulty=evolved.get('challengeTier'),
            party_level=party_level,
            party_size=party_size,
        )
    evolved['evolutionReason'] = context.get('reason') or 'Persistent creature evolution.'
    return evolved
