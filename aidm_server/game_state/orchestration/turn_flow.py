"""Less waiting at the table: who tells a turn, the turn a hero's action ends for them, and how long each turn kept
the players waiting.

Who tells a turn (:func:`telling`). The narrator (a model, when one is set up) tells every turn outside a fight and
every fight turn that holds an attack, a spell, a manoeuvre, an improvised action, words the hero said or a look, a
finishing blow, an enemy phase, a fight starting or ending, or a roll. Any other fight action resolves with nothing to
tell beyond its own result (End turn, Dodge, Disengage, Advance, Fall back, Dash, Help, Hide, Stabilize, a class
feature, a potion, Take heart): the scripted narrator's sentences tell it and no model is called (``scripted``), as
they tell Take heart out of a fight. When those sentences are only the engine's turn line (an End turn with a hero
next and nobody acting between), the turn has no narration at all (``turn_line``): no reply is streamed and the log
gains the turn line alone. A step into another room of a place (``zone_move``) is told by the scripted narrator too.

The turn ends itself (:func:`turn_ends_itself`). A hero whose ``End my turn for me`` is on (``auto_end_turn`` sent
with the action) has their turn ended in the same turn when the action they took leaves them only moving (Advance,
Fall back), ending the turn or taking heart: no attack, bonus action, other free action or bonus-action spell is still
legal. Healing that nobody needs is no choice to wait on (a potion, or Healing Word, with every friend unhurt), as
Second Wind at full health is not offered at all. A roll the player has yet to press keeps the turn open until it resolves.

Timings (:func:`turn_timings`). Each finished turn records ``timings`` in its metadata: ``engine_ms`` (submit to the
engine's results sent), ``narration_ms`` (first to last narration chunk; 0 when no model was called),
``settle_ms`` (narration end to the turn finished), ``model_called``, the turn ``kind`` (:data:`TURN_KINDS`) and
``time_to_act_ms`` (submit to the turn finished: how long the players waited to act again).
"""

from __future__ import annotations

from math import ceil
from statistics import median
from typing import Any, Iterable

from aidm_server import scripted_narrator
from aidm_server.character_resources import spell_cast_legality
from aidm_server.combat.legal_actions import legal_combat_actions_for_player
from aidm_server.combat.maneuvers import IMPROVISE_TYPE, MANEUVER_TYPES
from aidm_server.combat.positioning import participant_by_id
from aidm_server.combat.state import combat_turn_economy, default_turn_economy
from aidm_server.game_state.models import find_actor
from aidm_server.game_state.orchestration.zone_turns import zone_move_only
from aidm_server.item_effects import ITEM_EFFECTS
from aidm_server.models import Player, safe_json_loads
from aidm_server.prompt_templates import prompt_rules_hint
from aidm_server.spell_effects import active_repeat_attack, cannot_cast_reason
from aidm_server.spellbook import authoritative_spell_effect, normalize_spellbook

TOLD_BY_NARRATOR = 'narrator'
TOLD_SCRIPTED = 'scripted'
TOLD_TURN_LINE = 'turn_line'
# The model a turn told by the scripted narrator records (as Safe Mode does).
SCRIPTED_NARRATOR_MODEL = 'deterministic-v1'
# Turn metadata: who told the turn (``{"told": ...}``) and its timings.
TURN_FLOW_KEY = 'turnFlow'
TIMINGS_KEY = 'timings'
# Turn metadata: the hero's ``End my turn for me`` choice sent with the action.
AUTO_END_TURN_REQUEST_KEY = 'auto_end_turn'
# The state pipeline's mark on a turn the engine ended for the hero.
AUTO_END_TURN_KEY = 'autoEndTurn'
# The fight actions a model narrates; every other fight action is told by its own result.
NARRATED_FIGHT_ACTIONS = frozenset({'attack', *MANEUVER_TYPES, IMPROVISE_TYPE})
# What a hero may still do and have their turn end anyway: move, end it, tell a finishing blow (free, any time) or
# take heart (free: holding Inspiration never keeps a turn open).
LEAVES_ONLY = frozenset({'advance', 'reposition', 'end_turn', 'finisher', 'take_heart'})
# The intents that spend a hero's turn in a fight (and so may leave nothing but moving).
TURN_SPENDING_KINDS = frozenset({'combat', 'spell', 'capability', 'object', 'roll'})
ENGINE_ACTION_KINDS = frozenset({'object', 'travel', 'item', 'rest', 'shop', 'spell', 'capability', 'roll'})
TURN_KINDS: tuple[tuple[str, str], ...] = (
    ('explore', 'Explore'),
    ('talk', 'Talk'),
    ('fight_action', 'Fight action'),
    ('end_turn', 'End turn'),
    ('enemy_phase', 'Enemy phase'),
)


def _text(value: Any) -> str:
    return str(value or '').strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _combat_action(action_intent: dict[str, Any] | None) -> dict[str, Any] | None:
    """The server-resolved fight action of a ``combat`` intent; None for any other intent."""

    intent = _dict(action_intent)
    return _dict(intent.get('combat')) if intent.get('kind') == 'combat' else None


def _told_quietly(rules_hint: dict[str, Any], action_intent: dict[str, Any] | None) -> bool:
    """A fight action whose turn holds nothing a model tells, or Take heart out of a fight (see the module docstring)."""

    if _text(_dict(action_intent).get('kind')) == 'inspiration':
        return True
    if zone_move_only(rules_hint.get('state_pipeline')):
        # Stepping into another room: its description and arrival line are the engine's own words.
        return True
    combat_action = _combat_action(action_intent)
    if combat_action is None or _text(combat_action.get('action_type')) in NARRATED_FIGHT_ACTIONS:
        return False
    if rules_hint.get('requires_roll') is True or isinstance(rules_hint.get('authoritative_roll'), dict):
        return False
    pipeline = _dict(rules_hint.get('state_pipeline'))
    combat_state = _dict(pipeline.get('combatState'))
    if not combat_state:
        return False
    if (
        isinstance(combat_state.get('playerResolvedAction'), dict)
        or _dicts(combat_state.get('trustedDamageEvents'))
        or _dicts(combat_state.get('enemyResolvedActions'))
        or _text(_dict(combat_state.get('playerResolvedCombatAction')).get('actionType')) in NARRATED_FIGHT_ACTIONS
    ):
        return False
    facts = _dict(pipeline.get('narration'))
    return not any(facts.get(key) for key in ('fightStart', 'fightEnds', 'partyDefeat', 'finishingBlow', 'finisher'))


def scripted_narration(context: dict[str, Any], rules_hint: dict[str, Any], player_input: str, speaker: str | None) -> str:
    """The turn as the scripted narrator tells it, exactly as Safe Mode would."""

    return scripted_narrator.narrate(context, prompt_rules_hint(rules_hint), player_input, speaker)


def telling(
    rules_hint: dict[str, Any],
    action_intent: dict[str, Any] | None,
    *,
    player_input: str,
    speaker: str | None,
) -> tuple[str, str]:
    """Who tells the turn (``narrator``, ``scripted`` or ``turn_line``) and, for ``turn_line``, the line."""

    if not _told_quietly(rules_hint, action_intent):
        return TOLD_BY_NARRATOR, ''
    line = scripted_narrator.engine_turn_line(rules_hint)
    if line and scripted_narration({}, rules_hint, player_input, speaker).strip() == line:
        return TOLD_TURN_LINE, line
    return TOLD_SCRIPTED, ''


def scripted_stream(user_input: str, context: str, speaking_player: dict | None = None, rules_hint: dict | None = None):
    """A narration stream that tells the turn with the scripted narrator (the ``stream`` a turn ``scripted`` uses)."""

    yield scripted_narration(
        _dict(safe_json_loads(context, {})),
        _dict(rules_hint),
        str(user_input or ''),
        _text(_dict(speaking_player).get('character_name')) or None,
    )


def told(metadata: dict[str, Any]) -> str:
    """Who told a saved turn: the narrator unless the turn records the engine telling it."""

    return _text(_dict(metadata.get(TURN_FLOW_KEY)).get('told')) or TOLD_BY_NARRATOR


def _spends_turn(action_intent: dict[str, Any] | None) -> bool:
    intent = _dict(action_intent)
    if _text(intent.get('kind')) not in TURN_SPENDING_KINDS:
        return False
    combat_action = _combat_action(intent)
    return combat_action is None or _text(combat_action.get('action_type')) != 'end_turn'


def _hurt(participant: dict[str, Any] | None) -> bool:
    hp = _dict(_dict(participant).get('hp'))
    return int(hp.get('current') or 0) < int(hp.get('max') or 0)


def _heals_nobody(action: dict[str, Any], combat: dict[str, Any]) -> bool:
    """A healing item (a Potion of Healing) none of whose targets is hurt: like Second Wind at full health, it is
    no choice to keep a turn open for."""

    effect = ITEM_EFFECTS.get(_text(_dict(action.get('item')).get('effectId'))) or {}
    if action.get('type') != 'item' or effect.get('kind') != 'healing':
        return False
    return not any(
        _hurt(participant_by_id(combat, target.get('id')))
        for target in _dicts(action.get('targets'))
        if target.get('available') is True
    )


def _friend_hurt(combat: dict[str, Any]) -> bool:
    return any(
        _text(participant.get('team')).lower() in {'player', 'ally'} and participant.get('isAlive') is not False and _hurt(participant)
        for participant in _dicts(combat.get('participants'))
    )


def _bonus_spell_ready(state: dict[str, Any], actor_id: str) -> bool:
    """A bonus-action spell the hero could still cast this turn (Healing Word while a friend is hurt, Spiritual
    Weapon's next attack)."""

    combat = _dict(state.get('combat'))
    economy = combat_turn_economy(combat, actor_id=actor_id) or default_turn_economy(actor_id, combat.get('round'))
    participant = participant_by_id(combat, actor_id)
    actor = find_actor(state, actor_id) or {}
    if int(economy.get('bonusActionRemaining') or 0) <= 0 or not actor or cannot_cast_reason(participant):
        return False
    spellbook = normalize_spellbook(actor.get('spellbook'), class_name=actor.get('class'))
    for spell in _dicts(spellbook.get('knownSpells')):
        effect = authoritative_spell_effect(spell.get('name')) or {}
        if effect.get('castingTime') != 'bonus_action':
            continue
        if active_repeat_attack(combat, actor_id, _text(spell.get('id'))) is not None:
            return True
        if int(spell.get('level') or 0) > 0 and economy.get('leveledSpellCast') is True:
            continue
        if any(_text(part.get('kind')) == 'healing' for part in _dicts(effect.get('effects'))) and not _friend_hurt(combat):
            continue
        legality = spell_cast_legality(
            spellbook,
            actor.get('spellResources'),
            spell_name_or_id=_text(spell.get('id')) or spell.get('name'),
            class_name=_text(actor.get('class')) or None,
            level=max(1, int(actor.get('level') or 1)),
        )
        if legality.get('legal') is True:
            return True
    return False


def turn_ends_itself(
    *,
    state: dict[str, Any],
    player: Player,
    actor_id: str,
    action_intent: dict[str, Any] | None,
) -> bool:
    """The hero's action, now resolved in ``state``, leaves them only moving or ending their turn."""

    if not _spends_turn(action_intent):
        return False
    bundle = legal_combat_actions_for_player(state, player)
    if bundle is None or not bundle.get('isCurrentActor'):
        return False
    actions = _dicts(bundle.get('actions'))
    end_turn = next((action for action in actions if action.get('type') == 'end_turn'), {})
    if end_turn.get('available') is not True:
        return False
    combat = _dict(state.get('combat'))
    if any(
        action.get('available') is True and _text(action.get('type')) not in LEAVES_ONLY and not _heals_nobody(action, combat)
        for action in actions
    ):
        return False
    return not _bonus_spell_ready(state, actor_id)


def turn_kind(metadata: dict[str, Any]) -> str:
    """``explore``, ``talk``, ``fight_action``, ``end_turn`` or ``enemy_phase``: what a saved turn was."""

    pipeline = _dict(metadata.get('state_pipeline'))
    combat_state = _dict(_dict(pipeline.get('dmContextPacket')).get('combatState'))
    action_intent = _dict(metadata.get('action_intent'))
    if _dicts(combat_state.get('enemyResolvedActions')):
        return 'enemy_phase'
    if _text(_dict(_combat_action(action_intent)).get('action_type')) == 'end_turn':
        return 'end_turn'
    if combat_state:
        return 'fight_action'
    applied = [*_dicts(pipeline.get('immediateAppliedChanges')), *_dicts(pipeline.get('combatAppliedChanges'))]
    if applied or metadata.get('message_kind') == 'opening' or _text(action_intent.get('kind')) in ENGINE_ACTION_KINDS:
        return 'explore'
    return 'talk'


def turn_timings(
    *,
    submitted_at: float,
    engine_done_at: float,
    narration_ms: int,
    model_called: bool,
    narration_ended_at: float,
    finished_at: float,
    kind: str,
) -> dict[str, Any]:
    """A finished turn's ``timings`` (perf-counter instants in seconds; see the module docstring)."""

    def span(start: float, end: float) -> int:
        return max(0, int(round((end - start) * 1000)))

    return {
        'engine_ms': span(submitted_at, engine_done_at),
        'narration_ms': max(0, int(narration_ms)) if model_called else 0,
        'settle_ms': span(narration_ended_at, finished_at),
        'model_called': model_called,
        'kind': kind,
        'time_to_act_ms': span(submitted_at, finished_at),
    }


def _nearest_rank(values: list[int], percentile: int) -> int:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, ceil(percentile / 100 * len(ordered)) - 1))]


def time_to_act_rows(timings: Iterable[Any]) -> list[dict[str, Any]]:
    """The median and 90th-percentile time to act by turn kind, one row per kind that has turns."""

    by_kind: dict[str, list[int]] = {}
    for record in timings:
        record = _dict(record)
        value = record.get('time_to_act_ms')
        if isinstance(value, int) and not isinstance(value, bool) and _text(record.get('kind')):
            by_kind.setdefault(_text(record['kind']), []).append(value)
    return [
        {
            'kind': kind,
            'label': label,
            'turn_count': len(by_kind[kind]),
            'median_ms': int(round(median(by_kind[kind]))),
            'p90_ms': _nearest_rank(by_kind[kind], 90),
        }
        for kind, label in TURN_KINDS
        if by_kind.get(kind)
    ]
