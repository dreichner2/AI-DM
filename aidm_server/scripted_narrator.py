"""Deterministic Safe Mode narrator.

Narrates engine-resolved results and authored scene content from the same
CONTEXT, RULES_HINT and player input the live DM model receives, without any
network call. Phrasing varies by turn without randomness: each template has a
few variants chosen by ``sha1(f"{turn_number}:{template_key}")``.

Only one case ever asks for a roll: a turn whose rules hint requires a roll that
has not been made yet. Every other sentence is kept free of roll-request
phrasing so the server never builds a roll gate from it.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Any

from aidm_server.character_state import ROLL_TYPE_BASE_DC, SKILL_ABILITY
from aidm_server.turn_roll_policy import TurnRollPolicy
from aidm_server.turn_rules import response_mentions_roll_request


MIN_WORDS = 40
MAX_WORDS = 110
MAX_OPENING_WORDS = 220
MAX_QUOTED_INPUT_CHARS = 160
MAX_LEADS = 3
MAX_OPENING_DETAILS = 3
OPENING_CLOSING_LINE = 'What do you do?'
OPENING_MESSAGE_KIND = 'opening'

ABILITY_NAMES = ('strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma')
RULE_TYPE_LABELS = {
    'stealth': 'Dexterity (Stealth)',
    'social': 'Charisma (Persuasion)',
    'lore': 'Intelligence (Investigation)',
    'athletics': 'Strength (Athletics)',
    'mobility': 'Dexterity (Acrobatics)',
    'thieves_tools': "Dexterity (Thieves' Tools)",
    'spell': 'spellcasting',
    'attack': 'weapon attack',
    'initiative': 'initiative',
    'check': 'ability',
}
INVESTIGATION_ROLL_TYPES = frozenset(
    {'perception', 'investigation', 'insight', 'lore', 'arcana', 'history', 'nature', 'religion', 'survival'}
)
STEALTH_ROLL_TYPES = frozenset({'stealth'})
PHYSICAL_ROLL_TYPES = frozenset({'athletics', 'acrobatics', 'mobility'})
SOCIAL_ROLL_TYPES = frozenset({'persuasion', 'deception', 'intimidation', 'social'})
CURRENCY_NAMES = {'gp': 'gold', 'sp': 'silver', 'cp': 'copper', 'ep': 'electrum', 'pp': 'platinum'}
FIRST_NAME_STOPWORDS = frozenset(
    {'the', 'old', 'young', 'sir', 'lady', 'lord', 'captain', 'brother', 'sister', 'master', 'mother', 'father'}
)
DISPOSITION_MANNERS = {
    'friendly': 'with an easy warmth',
    'helpful': 'readily',
    'cautious_helpful': 'carefully, weighing each word before giving it',
    'cautious': 'carefully, weighing each word',
    'neutral': 'evenly',
    'curious': 'with open curiosity',
    'suspicious': 'with a narrow, measuring look',
    'wary': 'warily, keeping some distance',
    'afraid': 'in a low, uneasy voice',
    'frightened': 'in a low, uneasy voice',
    'hostile': 'coldly, making no effort to hide it',
}

TEMPLATES: dict[str, tuple[str, ...]] = {
    'opening_npcs': (
        '{npcs} {verb} here with you.',
        'Close at hand {verb} {npcs}.',
        'Within earshot {verb} {npcs}.',
    ),
    'invalid': ('That will not work: {reason}',),
    'closing_invalid': ('What do you do instead?', 'What do you try instead?', 'What is your next move?'),
    'closing': ('What do you do?', 'What do you do next?', 'What now?', 'Where do you go from here?'),
    'player_attack_hit': (
        '{actor} {weapon} finds {target}: {total} against AC {ac}, a hit for {damage} {damage_type} damage.',
        '{actor} {weapon} lands on {target} with {total} against AC {ac}, a hit for {damage} {damage_type} damage.',
        '{actor} {weapon} cuts in at {target}: {total} against AC {ac} hits for {damage} {damage_type} damage.',
    ),
    'player_attack_miss': (
        '{actor} {weapon} goes wide of {target}: {total} against AC {ac}, a miss.',
        '{actor} {weapon} swings at {target} with {total} against AC {ac} and misses.',
        '{target} slips {actor_object} {weapon}: {total} against AC {ac}, a miss.',
    ),
    'enemy_attack_hit': (
        '{enemy} strikes at {target} with {ability}: {total} against AC {ac}, a hit for {damage} {damage_type} damage.',
        '{enemy} lunges at {target} with {ability} and lands it, {total} against AC {ac}, for {damage} {damage_type} damage.',
        '{enemy} answers with {ability}: {total} against AC {ac} hits {target} for {damage} {damage_type} damage.',
    ),
    'enemy_attack_miss': (
        '{enemy} strikes at {target} with {ability}: {total} against AC {ac}, a miss.',
        '{enemy} lunges at {target} with {ability} but falls short, {total} against AC {ac}.',
        '{enemy} answers with {ability}, but {total} against AC {ac} misses {target}.',
    ),
    'enemy_save': (
        '{enemy} uses {ability}; {target} {resist} with a {save_ability} save of {total} against DC {dc}, {outcome}{damage_clause}.',
        '{enemy} turns {ability} on {target}, who {resist} with a {save_ability} save of {total} against DC {dc}, {outcome}{damage_clause}.',
        '{ability} from {enemy} washes over {target}: a {save_ability} save of {total} against DC {dc}, {outcome}{damage_clause}.',
    ),
    'enemy_unrolled': (
        '{enemy} moves to {intent}.',
        '{enemy} chooses to {intent}.',
        '{enemy} shifts tactics and moves to {intent}.',
    ),
    'conditions': (
        '{target} {be} now {conditions}.',
        'When it is over, {target_lower} {be} {conditions}.',
        'It leaves {target_object} {conditions}.',
    ),
    'defeated': ('{target} goes down and does not rise.', '{target} crumples and stays down.', '{target} falls.'),
    'player_down': (
        '{target} {be} down at 0 hit points.',
        '{target} {drop} to 0 hit points.',
        'At 0 hit points, {target_lower} {be} down.',
    ),
    'combat_start': ('The fight is on.', 'Steel is out; the fight begins.', 'There is no talking now; the fight begins.'),
    'combat_end': ('The fight is over{outcome}.', 'The last blow lands and the fight is over{outcome}.', 'Quiet returns; the fight is over{outcome}.'),
    'death_save': (
        '{target} {fight} to stay alive{detail}.',
        '{target} {cling} to life{detail}.',
        'Breath by breath, {target_lower} {fight} death{detail}.',
    ),
    'spell_cast': ('You cast {spell}.', 'You loose {spell}.', 'You shape {spell} and let it go.'),
    'spell_attack': (
        '{spell} streaks at {target_object}: {total} against AC {ac}, {outcome}.',
        '{spell} flies toward {target_object} with {total} against AC {ac}, {outcome}.',
        '{spell} lances out at {target_object}: {total} against AC {ac}, {outcome}.',
    ),
    'spell_save': (
        '{target} {resist} {spell} with a {ability} save of {total} against DC {dc}, {outcome}.',
        '{spell} presses on {target_object}: a {ability} save of {total} against DC {dc}, {outcome}.',
        'Against {spell}, {target_lower} {manage} a {ability} save of {total} against DC {dc}, {outcome}.',
    ),
    'spell_automatic': (
        '{spell} reaches {target_object}.',
        '{spell} settles over {target_object}.',
        '{spell} finds {target_object} without fail.',
    ),
    'spell_damage': (
        '{target} {take} {amount} {damage_type} damage.',
        'It costs {target_object} {amount} {damage_type} damage.',
        '{amount} {damage_type} damage tears into {target_object}.',
    ),
    'spell_healing': (
        '{target} {recover} {amount} hit points.',
        'Warmth floods back: {target_lower} {recover} {amount} hit points.',
        '{amount} hit points return to {target_object}.',
    ),
    'spell_temp_hp': (
        '{target} {gain} {amount} temporary hit points.',
        'A ward settles on {target_object}: {amount} temporary hit points.',
        '{amount} temporary hit points gather around {target_object}.',
    ),
    'spell_condition_add': (
        '{target} {be} now {condition}.',
        'The magic leaves {target_object} {condition}.',
        'When it fades, {target_lower} {be} {condition}.',
    ),
    'spell_condition_remove': (
        '{target} {be} no longer {condition}.',
        'The spell lifts it: {target_lower} {be} no longer {condition}.',
        '{target} {shake} off being {condition}.',
    ),
    'concentration': ('You hold your concentration on {spell}.', 'You keep {spell} steady in your mind.', 'You hold {spell} with steady focus.'),
    'capability_heal': (
        'You use {name}, restoring {amount} hit points{to_target}.',
        'You call on {name} and restore {amount} hit points{to_target}.',
        '{name} answers you, restoring {amount} hit points{to_target}.',
    ),
    'capability_effect': ('You use {name}: {effect}.', 'You call on {name}: {effect}.', '{name} answers you: {effect}.'),
    'capability_plain': ('You use {name}.', 'You call on {name}.', '{name} answers when you reach for it.'),
    'travel': ('You arrive at {place}.', 'The way opens onto {place}.', 'You reach {place}.'),
    'travel_present': ('{npcs} {verb} here.', 'Waiting here {verb} {npcs}.', 'You find {npcs} here.'),
    'item_gain_one': ('You now carry {item}.', '{item} goes into your pack.', 'You take {item}.'),
    'item_gain_many': ('You now carry {amount} {item}.', '{amount} {item} go into your pack.', 'You take {amount} {item}.'),
    'item_lose': ('You no longer carry {item}.', '{item} leaves your hands.', 'You part with {item}.'),
    'item_use': ('You use {item}.', 'You put {item} to use.', 'You make use of {item}.'),
    'item_equip': ('You ready {item}.', 'You take up {item}.', 'You fit {item} into place.'),
    'item_unequip': ('You stow {item}.', 'You put {item} away.', 'You set {item} aside.'),
    'scene_item': ('{item} lies within reach.', '{item} is there for the taking.', '{item} sits in plain view.'),
    'rest_hp': ('You rest and recover {amount} hit points.', 'Rest does its work: you recover {amount} hit points.', 'You come back from your rest {amount} hit points stronger.'),
    'rest_plain': ('You finish your rest.', 'Your rest ends, and your strength is your own again.', 'You rise from your rest.'),
    'objective_update': ('Your work on {quest} moves forward.', '{quest} takes a step forward.', 'Another piece of {quest} falls into place.'),
    'objective_add': ('{quest} gains a new task.', 'A new task joins {quest}.', '{quest} asks something more of you.'),
    'quest_complete': ('{quest} is complete.', 'You have seen {quest} through.', '{quest} is done.'),
    'quest_fail': ('{quest} has failed.', '{quest} slips beyond saving.', '{quest} is lost.'),
    'xp': ('You gain {amount} XP.', 'The experience is worth {amount} XP.', 'You earn {amount} XP.'),
    'currency_gain': ('You gain {amount} {currency}.', '{amount} {currency} goes into your purse.', 'You pocket {amount} {currency}.'),
    'currency_spend': ('You spend {amount} {currency}.', '{amount} {currency} leaves your purse.', 'You pay {amount} {currency}.'),
    'interactable_contents': ('Inside you find {items}.', 'It holds {items}.', 'Within lies {items}.'),
    'roll_success_investigation': (
        'You find what the others missed.',
        'Something gives itself away.',
        'Your attention catches on one thing.',
    ),
    'roll_success_detail': (
        'Your eye settles on one thing.',
        'One detail stands out.',
        'You notice it now.',
    ),
    'roll_failure_investigation': (
        'You look again, but nothing new reveals itself.',
        'Whatever is here stays hidden from you for now.',
        'You come away with nothing you did not already know.',
    ),
    'roll_success_stealth_npc': (
        'You slip out of sight, and {npc} never looks your way.',
        'You move like a shadow; {npc} does not notice a thing.',
        '{npc} glances right past you, none the wiser.',
    ),
    'roll_success_stealth': (
        'You slip out of sight and stay there, unseen.',
        'You move like a shadow and nobody marks you.',
        'You fold yourself into the dark, and no alarm follows.',
    ),
    'roll_failure_stealth_npc': (
        '{npc} turns sharply toward you; you have been noticed.',
        'A scuff gives you away, and {npc} looks straight at you.',
        '{npc} stiffens and stares right at where you are hiding.',
    ),
    'roll_failure_stealth': (
        'A loose stone skitters underfoot, and you know you have been noticed.',
        'A scuff gives you away; heads turn toward you.',
        'You misjudge the cover, and you are seen.',
    ),
    'roll_success_physical_npc': (
        'You make it cleanly, and {npc} sees exactly how easily you managed it.',
        'You pull it off in one smooth motion while {npc} watches.',
        'You land it, and {npc} has to look twice.',
    ),
    'roll_success_physical': (
        'You make it cleanly and land on your feet.',
        'You pull it off in one smooth motion.',
        'Muscle and timing hold; you make it.',
    ),
    'roll_failure_physical_npc': (
        'Your footing goes and the attempt falls short, in full view of {npc}.',
        'You come up short and stumble, and {npc} sees all of it.',
        'It gets away from you, with {npc} watching.',
    ),
    'roll_failure_physical': (
        'Your footing goes and the attempt falls short.',
        'You come up short and stumble.',
        'It gets away from you halfway through.',
    ),
    'roll_success_social_npc': (
        '{npc} hesitates, then gives way.',
        'Your words land; {npc} comes around.',
        '{npc} weighs you for a long moment and yields.',
    ),
    'roll_success_social': (
        'Your words land, and the resistance in front of you softens.',
        'You find the right words at the right moment.',
        'They come around to your way of seeing it.',
    ),
    'roll_failure_social_npc': (
        '{npc} is unmoved and makes that plain.',
        'Your words slide off {npc} without leaving a mark.',
        '{npc} shakes their head; you have not convinced anyone.',
    ),
    'roll_failure_social': (
        'Your words fall flat.',
        'Nobody is moved by what you say.',
        'It comes out wrong, and you can see it land wrong.',
    ),
    'roll_success_generic': (
        'It works, and the moment turns your way.',
        'You carry it off.',
        'It goes the way you meant it to.',
    ),
    'roll_failure_generic': (
        'It does not go your way, and the moment slips past.',
        'It falls apart in your hands.',
        'Despite your effort, it does not come off.',
    ),
    'roll_ack': (
        'You commit to it, "{action}", and everything around you narrows to this one attempt, the next breath deciding how cleanly you carry it off.',
        'You set yourself and go for it, "{action}", with no way to take it back once you start and the outcome hanging on nerve and timing.',
        'You draw a slow breath and make your move, "{action}", knowing the next few heartbeats will show whether you judged this right.',
    ),
    'roll_ack_plain': (
        'You commit to the attempt, and everything around you narrows to this one moment, the next breath deciding how cleanly you carry it off.',
        'You set yourself and go for it, with no way to take it back once you start and the outcome hanging on nerve and timing.',
        'You draw a slow breath and make your move, knowing the next few heartbeats will show whether you judged this right.',
    ),
    'npc_manner': (
        '{name}{role} answers {manner}.',
        '{name}{role} meets your eyes and answers {manner}.',
        '{name}{role} sets everything else aside and answers {manner}.',
    ),
    'npc_knows': (
        '"{fact}" {first} tells you.',
        '"{fact}" {first} says, voice low.',
        '"{fact}" {first} says after a moment.',
    ),
    'npc_nothing': (
        '{first} listens, but has nothing new to give you yet.',
        '{first} hears you out and offers nothing more for now.',
        '{first} thinks it over, then shakes their head.',
    ),
    'action': ('You go ahead: "{action}"', 'You do it: "{action}"', 'You follow through: "{action}"'),
    'action_plain': ('You take a moment and act.', 'You follow through.', 'You go ahead with it.'),
    'leads': ('Within reach are {leads}.', 'Close at hand: {leads}.', 'Your options sit nearby: {leads}.'),
    'fill': (
        'The air is still, and every small sound seems to carry.',
        'For a moment nothing moves, as if the place itself is waiting on you.',
        'Somewhere close, a board creaks and settles again.',
        'The light shifts while the moment stretches, waiting on your choice.',
    ),
}


@dataclass(frozen=True)
class NarrationInput:
    context: dict[str, Any]
    rules_hint: dict[str, Any]
    player_input: str
    speaker: str | None
    opening_brief: dict[str, Any] | None

    @property
    def turn_number(self) -> int:
        value = self.rules_hint.get('turn_number')
        return value if isinstance(value, int) else 0

    @property
    def live_state(self) -> dict:
        return _dict(self.context.get('live_world_state'))

    @property
    def scene(self) -> dict:
        return _dict(self.live_state.get('currentScene'))

    @property
    def pipeline(self) -> dict:
        return _dict(self.rules_hint.get('state_pipeline'))

    @property
    def applied_changes(self) -> list[dict]:
        return _dicts(self.pipeline.get('stateChangesAlreadyApplied'))

    @property
    def relevant_records(self) -> dict:
        return _dict(_dict(self.context.get('campaign_pack_director')).get('relevantRecords'))

    @property
    def present_npcs(self) -> list[dict]:
        return [npc for npc in _dicts(self.live_state.get('activeNpcs')) if _text(npc.get('name'))]

    @property
    def previous_narration(self) -> str:
        turns = _dicts(self.context.get('recent_turns'))
        return _text(turns[-1].get('dm_output')) if turns else ''

    def pick(self, key: str, **values: Any) -> str:
        options = TEMPLATES[key]
        digest = hashlib.sha1(f'{self.turn_number}:{key}'.encode('utf-8')).hexdigest()
        return options[int(digest, 16) % len(options)].format(**values)

    def is_speaker(self, name: Any) -> bool:
        return bool(self.speaker) and _text(name).casefold() == _text(self.speaker).casefold()


def narrate(
    context: dict[str, Any],
    rules_hint: dict[str, Any],
    player_input: str,
    speaker: str | None = None,
    opening_brief: dict[str, Any] | None = None,
) -> str:
    turn = NarrationInput(
        context=_dict(context),
        rules_hint=_dict(rules_hint),
        player_input=str(player_input or ''),
        speaker=_text(speaker) or None,
        opening_brief=opening_brief if isinstance(opening_brief, dict) else None,
    )
    if turn.rules_hint.get('message_kind') == OPENING_MESSAGE_KIND:
        return _narrate_opening(turn)
    invalid_actions = _dicts(turn.pipeline.get('invalidActions'))
    if invalid_actions:
        return _narrate_invalid(turn, invalid_actions)
    result_lines = [
        *_combat_lines(turn),
        *_spell_lines(turn),
        *_capability_lines(turn),
        *_change_lines(turn),
    ]
    roll_paragraphs = _resolved_roll_paragraphs(turn)
    if result_lines or roll_paragraphs:
        if roll_paragraphs:
            result_lines.append(roll_paragraphs[0])
        blocks = [result_lines, roll_paragraphs[1:]]
        return _compose(turn, blocks, fill=_scene_fill(turn), closing=turn.pick('closing'))
    if turn.rules_hint.get('requires_roll') is True and turn.rules_hint.get('roll_value') is None:
        return _narrate_roll_request(turn)
    npc = _addressed_npc(turn)
    if npc:
        return _narrate_npc_talk(turn, npc)
    return _narrate_action(turn)


# Cases ------------------------------------------------------------------------------------------------------------


def _narrate_opening(turn: NarrationInput) -> str:
    brief = turn.opening_brief or {}
    brief_scene = _dict(brief.get('scene'))
    scene = turn.scene
    lead = _text(brief_scene.get('summary') or scene.get('summary')) or _text(
        brief_scene.get('description') or scene.get('description')
    )
    details = _string_items(brief_scene.get('visibleDetails')) or _string_items(scene.get('visibleDetails'))
    npcs = [
        (_text(npc.get('name')), _humanize(npc.get('role')))
        for npc in _dicts(brief.get('presentNpcs')) or turn.present_npcs
        if _text(npc.get('name'))
    ]
    blocks = [
        _safe_sentences(_split_sentences(lead)),
        _safe_sentences([_sentence(detail) for detail in details[:MAX_OPENING_DETAILS]]),
    ]
    if npcs:
        listed = _join_words([f'{name} ({role})' if role else name for name, role in npcs])
        blocks.append(_safe_sentences([turn.pick('opening_npcs', npcs=listed, verb='is' if len(npcs) == 1 else 'are')]))
    return _compose(
        turn,
        [block for block in blocks if block],
        fill=[],
        closing=OPENING_CLOSING_LINE,
        max_words=MAX_OPENING_WORDS,
    )


def _narrate_invalid(turn: NarrationInput, invalid_actions: list[dict]) -> str:
    lines = []
    for action in invalid_actions[:2]:
        reason = _text(action.get('reason') or action.get('summary')) or 'it is not possible right now'
        lines.append(_sentence(turn.pick('invalid', reason=_clip(reason, 200))))
    return _compose(turn, [_safe_sentences(lines)], fill=_scene_fill(turn), closing=turn.pick('closing_invalid'))


def _narrate_roll_request(turn: NarrationInput) -> str:
    action = (_safe_quote(turn.player_input) or '').rstrip('.')
    acknowledgement = turn.pick('roll_ack', action=action) if action else turn.pick('roll_ack_plain')
    return _compose(turn, [[acknowledgement]], fill=_scene_fill(turn), closing=_roll_request_line(turn))


def _narrate_npc_talk(turn: NarrationInput, npc: dict) -> str:
    name = _text(npc.get('name'))
    first = _first_name(name) or name
    role = _humanize(npc.get('role'))
    manner = DISPOSITION_MANNERS.get(_key(npc.get('disposition')), 'evenly')
    lines = [turn.pick('npc_manner', name=name, role=f', the {role},' if role else '', manner=manner)]
    knows = _npc_knows(turn, npc)
    if knows:
        mentions = sum(1 for recent in _dicts(turn.context.get('recent_turns')) if _mentions_npc(recent, npc))
        fact = knows[min(mentions, len(knows) - 1)]
        lines.append(turn.pick('npc_knows', fact=_speech(fact), first=first))
    else:
        lines.append(turn.pick('npc_nothing', first=first))
    return _compose(turn, [_safe_sentences(lines)], fill=_scene_fill(turn), closing=turn.pick('closing'))


def _narrate_action(turn: NarrationInput) -> str:
    action = _safe_quote(turn.player_input)
    if action and action[-1] not in '.!?…':
        action = f'{action}.'
    opening_line = turn.pick('action', action=action) if action else turn.pick('action_plain')
    details = _unused_scene_details(turn)
    core = [opening_line, *details[:1]]
    leads = _leads_sentence(turn)
    fill = [*([leads] if leads else []), *details[1:]]
    return _compose(turn, [_safe_sentences(core)], fill=fill, closing=turn.pick('closing'))


# Mechanical results ---------------------------------------------------------------------------------------------


def _combat_lines(turn: NarrationInput) -> list[str]:
    combat_state = _dict(turn.pipeline.get('combatState'))
    lines: list[str] = []
    targets: list[str] = []
    player_action = _dict(combat_state.get('playerResolvedAction'))
    attacks = [player_action] if player_action else []
    for event in _dicts(combat_state.get('trustedDamageEvents')):
        if event not in attacks:
            attacks.append(event)
    for attack in attacks:
        lines.append(_player_attack_line(turn, attack))
        targets.append(_text(attack.get('targetName')))
    for action in _dicts(combat_state.get('enemyResolvedActions')):
        line = _enemy_action_line(turn, action)
        if line:
            lines.append(line)
        conditions = _string_items(action.get('conditionsApplied'))
        if conditions:
            lines.append(_conditions_line(turn, action.get('targetName'), conditions))
        targets.append(_text(action.get('targetName')))
    lines.extend(_defeat_lines(turn, targets))
    for change in turn.applied_changes:
        change_type = _text(change.get('type'))
        if change_type == 'combat.start':
            lines.append(turn.pick('combat_start'))
        elif change_type == 'combat.end':
            status = _key(change.get('combatStatus'))
            outcome = f': {_humanize(status)}' if status and status not in {'ended', 'none', 'complete', 'completed'} else ''
            lines.append(turn.pick('combat_end', outcome=outcome))
        elif change_type == 'combat.death_save':
            lines.append(_death_save_line(turn, change))
    return lines


def _player_attack_line(turn: NarrationInput, attack: dict) -> str:
    actor_name = _text(attack.get('sourceActorName'))
    is_you = not actor_name or turn.is_speaker(actor_name)
    weapon = _text(attack.get('weaponName')) or 'weapon'
    values = {
        'actor': 'Your' if is_you else f'{actor_name}\'s',
        'actor_object': 'your' if is_you else f"{actor_name}'s",
        'weapon': weapon,
        'target': _target_ref(turn, attack.get('targetName')),
        'total': _number(attack.get('attackTotal')),
        'ac': _number(attack.get('targetArmorClass')),
        'damage': _number(attack.get('damageTotal')),
        'damage_type': _text(attack.get('damageType')) or 'weapon',
    }
    key = 'player_attack_hit' if attack.get('hit') is True else 'player_attack_miss'
    return _sentence(turn.pick(key, **values))


def _enemy_action_line(turn: NarrationInput, action: dict) -> str | None:
    enemy = _text(action.get('enemyName')) or 'The enemy'
    target = _target_ref(turn, action.get('targetName'))
    ability = _text(action.get('abilityName')) or 'an attack'
    if action.get('resolvedWithoutRoll') is True:
        intent = _humanize(action.get('intentType')) or 'act'
        return _sentence(turn.pick('enemy_unrolled', enemy=enemy, intent=intent))
    damage = _number(action.get('damageTotal'))
    damage_type = _text(action.get('damageType')) or 'weapon'
    if action.get('saveDC') is not None and action.get('attackTotal') is None:
        damage_clause = f', taking {damage} {damage_type} damage' if damage not in ('?', '0') else ''
        return _sentence(
            turn.pick(
                'enemy_save',
                enemy=enemy,
                ability=ability,
                target=target,
                resist=_person(turn, action.get('targetName'))['resist'],
                save_ability=_humanize(action.get('saveAbility')).title() or 'ability',
                total=_number(action.get('saveTotal')),
                dc=_number(action.get('saveDC')),
                outcome='a success' if action.get('saveSucceeded') is True else 'a failure',
                damage_clause=damage_clause,
            )
        )
    if action.get('attackTotal') is None:
        return None
    key = 'enemy_attack_hit' if action.get('hit') is True else 'enemy_attack_miss'
    return _sentence(
        turn.pick(
            key,
            enemy=enemy,
            target=target,
            ability=ability,
            total=_number(action.get('attackTotal')),
            ac=_number(action.get('targetArmorClass')),
            damage=damage,
            damage_type=damage_type,
        )
    )


def _conditions_line(turn: NarrationInput, target_name: Any, conditions: list[str]) -> str:
    return _sentence(
        turn.pick(
            'conditions',
            **_person(turn, target_name),
            conditions=_join_words([_humanize(condition) for condition in conditions]),
        )
    )


def _defeat_lines(turn: NarrationInput, target_names: list[str]) -> list[str]:
    participants = _dicts(_dict(turn.live_state.get('combat')).get('participants'))
    wanted = {name.casefold() for name in target_names if name}
    lines = []
    for participant in participants:
        name = _text(participant.get('name'))
        if not name or name.casefold() not in wanted:
            continue
        hp = _dict(participant.get('hp'))
        current = hp.get('current')
        down = participant.get('isAlive') is False or (isinstance(current, (int, float)) and current <= 0)
        if not down:
            continue
        if _key(participant.get('team')) == 'enemy':
            lines.append(turn.pick('defeated', target=name))
        else:
            lines.append(_sentence(turn.pick('player_down', **_person(turn, name))))
    return lines


def _death_save_line(turn: NarrationInput, change: dict) -> str:
    name = _text(change.get('participantName') or change.get('targetName')) or turn.speaker
    roll = change.get('roll')
    result = _humanize(change.get('result'))
    detail = ''
    if isinstance(roll, int) and result:
        detail = f': {roll}, {result}'
    elif result:
        detail = f': {result}'
    return _sentence(turn.pick('death_save', **_person(turn, name), detail=detail))


def _spell_lines(turn: NarrationInput) -> list[str]:
    lines: list[str] = []
    for change in turn.applied_changes:
        if _text(change.get('type')) != 'spell.cast':
            continue
        resolution = _dict(change.get('spellResolution'))
        if not resolution:
            continue
        spell = _text(resolution.get('spellName') or change.get('spellName')) or 'the spell'
        lines.append(turn.pick('spell_cast', spell=spell))
        for target in _dicts(resolution.get('targets')):
            lines.extend(_spell_target_lines(turn, spell, target))
        if _dict(resolution.get('concentration')).get('started') is True:
            lines.append(turn.pick('concentration', spell=spell))
    return lines


def _spell_target_lines(turn: NarrationInput, spell: str, target: dict) -> list[str]:
    person = _person(turn, target.get('targetName'))
    delivery = _dict(target.get('delivery'))
    delivery_type = _key(delivery.get('type'))
    lines = []
    if delivery_type == 'attack':
        lines.append(
            turn.pick(
                'spell_attack',
                **person,
                spell=spell,
                total=_number(delivery.get('total')),
                ac=_number(delivery.get('targetArmorClass')),
                outcome='a hit' if delivery.get('delivered') is True else 'a miss',
            )
        )
    elif delivery_type == 'save':
        lines.append(
            turn.pick(
                'spell_save',
                **person,
                spell=spell,
                ability=_humanize(delivery.get('ability')).title() or 'ability',
                total=_number(delivery.get('total')),
                dc=_number(delivery.get('dc')),
                outcome='a success' if delivery.get('saveSucceeded') is True else 'a failure',
            )
        )
    else:
        lines.append(turn.pick('spell_automatic', **person, spell=spell))
    for effect in _dicts(target.get('effects')):
        line = _spell_effect_line(turn, effect, person)
        if line:
            lines.append(_sentence(line))
    return lines


def _spell_effect_line(turn: NarrationInput, effect: dict, person: dict[str, str]) -> str | None:
    kind = _key(effect.get('kind'))
    if effect.get('applied') is False:
        return None
    amount = _number(effect.get('amountApplied'))
    if kind == 'damage':
        return turn.pick('spell_damage', **person, amount=amount, damage_type=_text(effect.get('damageType')) or 'magical')
    if kind == 'healing':
        return turn.pick('spell_healing', **person, amount=amount)
    if kind == 'temporary_hp':
        return turn.pick('spell_temp_hp', **person, amount=amount)
    if kind == 'condition':
        key = 'spell_condition_remove' if _key(effect.get('operation')) == 'remove' else 'spell_condition_add'
        return turn.pick(key, **person, condition=_humanize(effect.get('condition')))
    return None


def _capability_lines(turn: NarrationInput) -> list[str]:
    lines = []
    for change in turn.applied_changes:
        if _text(change.get('type')) != 'class_feature.use':
            continue
        name = _text(change.get('capabilityName')) or 'your training'
        effect = _humanize(change.get('effectType'))
        amount = change.get('amount')
        target = _text(change.get('targetName'))
        to_target = f' to {target}' if target and not turn.is_speaker(target) else ''
        if isinstance(amount, int) and amount > 0 and 'heal' in effect:
            lines.append(turn.pick('capability_heal', name=name, amount=amount, to_target=to_target))
        elif effect:
            lines.append(turn.pick('capability_effect', name=name, effect=effect))
        else:
            lines.append(turn.pick('capability_plain', name=name))
    return lines


def _change_lines(turn: NarrationInput) -> list[str]:
    lines: list[str] = []
    for change in turn.applied_changes:
        change_type = _text(change.get('type'))
        item = _text(change.get('itemName'))
        amount = change.get('amount')
        quest = _text(change.get('questTitle'))
        if change_type == 'scene.move_location':
            lines.extend(_travel_lines(turn, change))
        elif change_type == 'scene.interactable.action':
            lines.extend(_interactable_lines(turn, change))
        elif change_type == 'inventory.add' and item:
            if isinstance(amount, int) and amount > 1:
                lines.append(turn.pick('item_gain_many', amount=amount, item=item))
            else:
                lines.append(_sentence(turn.pick('item_gain_one', item=item)))
        elif change_type == 'inventory.remove' and item:
            lines.append(_sentence(turn.pick('item_lose', item=item)))
        elif change_type == 'inventory.mark_used' and item:
            lines.append(turn.pick('item_use', item=item))
        elif change_type == 'inventory.equip' and item:
            lines.append(turn.pick('item_equip', item=item))
        elif change_type == 'inventory.unequip' and item:
            lines.append(turn.pick('item_unequip', item=item))
        elif change_type == 'scene.item.add' and item:
            lines.append(_sentence(turn.pick('scene_item', item=item)))
        elif change_type == 'rest.complete':
            if isinstance(amount, int) and amount > 0:
                lines.append(turn.pick('rest_hp', amount=amount))
            else:
                lines.append(turn.pick('rest_plain'))
        elif change_type in {'quest.objective.update', 'quest.objective.add'} and quest:
            key = 'objective_add' if change_type == 'quest.objective.add' else 'objective_update'
            lines.append(_sentence(turn.pick(key, quest=quest)))
        elif change_type == 'quest.complete' and quest:
            lines.append(_sentence(turn.pick('quest_complete', quest=quest)))
        elif change_type == 'quest.fail' and quest:
            lines.append(_sentence(turn.pick('quest_fail', quest=quest)))
        elif change_type == 'xp.add' and isinstance(change.get('xp'), int) and change['xp'] > 0:
            lines.append(turn.pick('xp', amount=change['xp']))
        elif change_type in {'currency.add', 'currency.remove'} and isinstance(amount, int) and amount > 0:
            currency = CURRENCY_NAMES.get(_key(change.get('currency')), _text(change.get('currency')) or 'coin')
            key = 'currency_gain' if change_type == 'currency.add' else 'currency_spend'
            lines.append(turn.pick(key, amount=amount, currency=currency))
        elif change_type == 'spell.cast' and not change.get('spellResolution') and change.get('spellName'):
            lines.append(turn.pick('spell_cast', spell=_text(change.get('spellName'))))
    return _safe_sentences(lines)


def _travel_lines(turn: NarrationInput, change: dict) -> list[str]:
    scene = turn.scene
    place = _text(change.get('locationName')) or _text(scene.get('name')) or 'a new place'
    lines = [turn.pick('travel', place=place)]
    description = _text(scene.get('summary')) or _text(scene.get('description'))
    lines.extend(_split_sentences(description)[:2])
    npcs = [_text(npc.get('name')) for npc in turn.present_npcs]
    if npcs:
        lines.append(turn.pick('travel_present', npcs=_join_words(npcs), verb='is' if len(npcs) == 1 else 'are'))
    return lines


def _interactable_lines(turn: NarrationInput, change: dict) -> list[str]:
    interactable = next(
        (
            item
            for item in _dicts(turn.scene.get('interactables'))
            if _text(item.get('id')) and _text(item.get('id')) == _text(change.get('targetId'))
        ),
        {},
    )
    name = _text(change.get('targetName')) or _text(interactable.get('name')) or _humanize(change.get('targetId')) or 'it'
    action = _key(change.get('objectAction'))
    sentences = {
        'open': f'You open the {name}.',
        'close': f'You close the {name}.',
        'unlock': f'The {name} unlocks with a click.',
        'lock': f'You lock the {name}.',
        'search': f'You search the {name}.',
        'inspect': f'You look the {name} over closely.',
        'use': f'You use the {name}.',
        'break': f'The {name} breaks under your hands.',
        'disarm': f'You disarm the {name}.',
        'trigger': f'The {name} goes off.',
    }
    lines = [sentences.get(action) or _sentence(f'You {_humanize(action) or "work at"} the {name}')]
    if action in {'open', 'search', 'inspect'}:
        contents = [_text(item.get('name')) for item in _dicts(interactable.get('contents')) if _text(item.get('name'))]
        if contents:
            lines.append(turn.pick('interactable_contents', items=_join_words(contents)))
    return lines


def _resolved_roll_paragraphs(turn: NarrationInput) -> list[str]:
    roll = _dict(turn.rules_hint.get('authoritative_roll'))
    if not turn.rules_hint.get('resolved_turn_id') or not roll:
        return []
    rule_type = _key(roll.get('rule_type') or turn.rules_hint.get('roll_type')) or 'check'
    total = roll.get('total') if isinstance(roll.get('total'), int) else turn.rules_hint.get('roll_value')
    if not isinstance(total, int):
        return []
    label = _roll_label(rule_type)
    label = label[:1].upper() + label[1:]
    if rule_type == 'check':
        label = 'Ability check'
    stated_dc = _int_or_none(roll.get('dc')) or _int_or_none(roll.get('task_dc'))
    outcome = _key(roll.get('outcome'))
    if outcome not in {'success', 'failure'}:
        reference_dc = stated_dc or _base_dc(rule_type)
        outcome = '' if reference_dc is None else ('success' if total >= reference_dc else 'failure')
    dc_clause = f' against DC {stated_dc}' if stated_dc is not None else ''
    if not outcome:
        return [f'{label}: {total}{dc_clause}.']
    result_line = f'{label}: {total}{dc_clause} — {outcome}.'
    return [result_line, *_roll_outcome_lines(turn, rule_type, success=outcome == 'success')]


def _roll_outcome_lines(turn: NarrationInput, rule_type: str, *, success: bool) -> list[str]:
    npc = _npc_named_in(turn, _original_action(turn))
    npc_name = _text(npc.get('name')) if npc else ''
    if rule_type in INVESTIGATION_ROLL_TYPES:
        if not success:
            return [turn.pick('roll_failure_investigation')]
        clue = _unused_clue(turn)
        if clue:
            return _safe_sentences([turn.pick('roll_success_investigation'), _sentence(clue)])
        details = _unused_scene_details(turn)
        if details:
            return [turn.pick('roll_success_detail'), details[0]]
        return [turn.pick('roll_success_generic')]
    family = (
        'stealth'
        if rule_type in STEALTH_ROLL_TYPES
        else 'physical'
        if rule_type in PHYSICAL_ROLL_TYPES
        else 'social'
        if rule_type in SOCIAL_ROLL_TYPES
        else 'generic'
    )
    result = 'success' if success else 'failure'
    if family == 'generic':
        return [turn.pick(f'roll_{result}_generic')]
    if npc_name:
        return [turn.pick(f'roll_{result}_{family}_npc', npc=npc_name)]
    return [turn.pick(f'roll_{result}_{family}')]


def _roll_request_line(turn: NarrationInput) -> str:
    rule_type = _key(turn.rules_hint.get('roll_type')) or 'check'
    dc = _dc_from_hint(turn.rules_hint.get('dc_hint')) or _base_dc(rule_type)
    dc_clause = f', DC {dc}' if dc is not None else ''
    if rule_type.endswith('_saving_throw'):
        ability = rule_type[: -len('_saving_throw')].title()
        return f'Make {_article(ability)} {ability} saving throw{dc_clause}.'
    label = _roll_label(rule_type)
    return f'Make {_article(label)} {label} check{dc_clause}.'


# Scene material ---------------------------------------------------------------------------------------------------


def _scene_details(turn: NarrationInput) -> list[str]:
    scene = turn.scene
    details = [_sentence(detail) for detail in _string_items(scene.get('visibleDetails'))]
    details.extend(_split_sentences(_text(scene.get('description'))))
    details.extend(_split_sentences(_text(scene.get('summary'))))
    return _safe_sentences(list(dict.fromkeys(detail for detail in details if detail)))


def _unused_scene_details(turn: NarrationInput) -> list[str]:
    details = _scene_details(turn)
    previous = _normalized(turn.previous_narration)
    unused = [detail for detail in details if _normalized(detail) not in previous]
    if unused:
        return unused
    if not details:
        return []
    offset = turn.turn_number % len(details)
    return details[offset:] + details[:offset]


def _scene_fill(turn: NarrationInput) -> list[str]:
    leads = _leads_sentence(turn)
    return [*_unused_scene_details(turn)[:2], *([leads] if leads else [])]


def _leads_sentence(turn: NarrationInput) -> str | None:
    npcs = [_text(npc.get('name')) for npc in turn.present_npcs]
    interactables = [
        f'the {re.sub(r"^the ", "", _text(item.get("name")), flags=re.IGNORECASE)}'
        for item in _dicts(turn.scene.get('interactables'))
        if _text(item.get('name'))
    ]
    exits = [f'the way to {name}' for name in _exit_names(turn)]
    leads: list[str] = []
    for index in range(MAX_LEADS):
        for group in (npcs, interactables, exits):
            if index < len(group) and len(leads) < MAX_LEADS:
                leads.append(group[index])
    if not leads:
        return None
    sentence = turn.pick('leads', leads=_join_words(leads))
    return None if requests_roll(sentence) else sentence


def _exit_names(turn: NarrationInput) -> list[str]:
    location_id = _text(turn.scene.get('locationId'))
    known_locations = [
        location
        for location in _dicts(turn.live_state.get('recentLocations'))
        if _text(location.get('id')) and _text(location.get('name'))
    ]
    known_locations.extend(
        location
        for location in _dicts(turn.relevant_records.get('locations'))
        if location.get('knownToPlayers') is True and _text(location.get('id')) and _text(location.get('name'))
    )
    names_by_id = {_text(location.get('id')): _text(location.get('name')) for location in reversed(known_locations)}
    current = next((location for location in known_locations if _text(location.get('id')) == location_id), {})
    connected = _string_items(current.get('connectedLocationIds'))
    return [names_by_id[location] for location in connected if location in names_by_id]


def _unused_clue(turn: NarrationInput) -> str | None:
    previous = _normalized(turn.previous_narration)
    for clue in _dicts(turn.relevant_records.get('clues')):
        summary = _text(clue.get('summary'))
        if clue.get('discovered') is True or not summary:
            continue
        if _normalized(summary) in previous:
            continue
        return summary
    return None


def _npc_knows(turn: NarrationInput, npc: dict) -> list[str]:
    npc_id = _text(npc.get('id'))
    name = _text(npc.get('name')).casefold()
    for record in _dicts(turn.relevant_records.get('npcs')):
        if (npc_id and _text(record.get('id')) == npc_id) or _text(record.get('name')).casefold() == name:
            return _safe_sentences([_text(item) for item in _string_items(record.get('knows'))])
    return []


def _addressed_npc(turn: NarrationInput) -> dict | None:
    return _npc_named_in(turn, turn.player_input)


def _npc_named_in(turn: NarrationInput, text: str) -> dict | None:
    for npc in turn.present_npcs:
        if _text_mentions_npc(text, npc):
            return npc
    return None


def _text_mentions_npc(text: str, npc: dict) -> bool:
    name = _text(npc.get('name'))
    if not text or not name:
        return False
    names = [name]
    first = _first_name(name)
    if first:
        names.append(first)
    return any(re.search(rf'\b{re.escape(candidate)}\b', text, re.IGNORECASE) for candidate in names)


def _mentions_npc(recent_turn: dict, npc: dict) -> bool:
    return _text_mentions_npc(_text(recent_turn.get('player_input')), npc) or _text_mentions_npc(
        _text(recent_turn.get('dm_output')),
        npc,
    )


def _original_action(turn: NarrationInput) -> str:
    resolved_turn_id = turn.rules_hint.get('resolved_turn_id')
    session_memory = _dict(turn.context.get('session_memory'))
    session_state = _dict(turn.context.get('session_state'))
    for source in (
        turn.context.get('recent_turns'),
        session_memory.get('recent_beats'),
        session_state.get('memory_snippets'),
        turn.context.get('pending_checks'),
    ):
        for entry in _dicts(source):
            if entry.get('turn_id') == resolved_turn_id and _text(entry.get('player_input')):
                return _text(entry.get('player_input'))
    return turn.player_input


def _target_ref(turn: NarrationInput, name: Any) -> str:
    if turn.is_speaker(name):
        return 'you'
    return _text(name) or 'the target'


def _person(turn: NarrationInput, name: Any) -> dict[str, str]:
    """Subject, object and verb forms for a named participant, in second person for the speaker."""
    if turn.is_speaker(name):
        forms = dict(target='You', target_lower='you', target_object='you', be='are')
        verbs = ('take', 'recover', 'gain', 'resist', 'fight', 'cling', 'manage', 'shake', 'drop')
        return {**forms, **{verb: verb for verb in verbs}}
    display = _text(name) or 'the target'
    return {
        'target': display[:1].upper() + display[1:],
        'target_lower': display,
        'target_object': display,
        'be': 'is',
        'take': 'takes',
        'recover': 'recovers',
        'gain': 'gains',
        'resist': 'resists',
        'fight': 'fights',
        'cling': 'clings',
        'manage': 'manages',
        'shake': 'shakes',
        'drop': 'drops',
    }


# Composition --------------------------------------------------------------------------------------------------------


def _compose(
    turn: NarrationInput,
    blocks: list[list[str]],
    *,
    fill: list[str],
    closing: str,
    max_words: int = MAX_WORDS,
    min_words: int = MIN_WORDS,
) -> str:
    """Join sentences into short paragraphs within the word bounds, ending on ``closing``."""
    budget = max_words - _word_count(closing)
    used = 0
    paragraphs: list[list[str]] = []
    stop = False
    for block in blocks:
        kept: list[str] = []
        for sentence in block:
            words = _word_count(sentence)
            if used + words > budget:
                if not paragraphs and not kept:
                    kept.append(_clip_words(sentence, budget))
                    used = budget
                stop = True
                break
            kept.append(sentence)
            used += words
        if kept:
            paragraphs.append(kept)
        if stop:
            break
    tail: list[str] = []
    first_fill = turn.pick('fill')
    generic_fill = [first_fill, *(line for line in TEMPLATES['fill'] if line != first_fill)]
    for sentence in [*fill, *generic_fill]:
        if used + _word_count(closing) >= min_words:
            break
        words = _word_count(sentence)
        if used + words > budget or any(sentence in paragraph for paragraph in paragraphs) or sentence in tail:
            continue
        tail.append(sentence)
        used += words
    tail.append(closing)
    paragraphs.append(tail)
    return '\n\n'.join(' '.join(paragraph) for paragraph in paragraphs)


def requests_roll(text: str) -> bool:
    """True when the server would read ``text`` as asking the player to roll."""
    return (
        response_mentions_roll_request(text)
        or TurnRollPolicy.response_requests_stated_check(text)
        or TurnRollPolicy.response_requests_group_roll(text)
    )


def _safe_sentences(sentences: list[str]) -> list[str]:
    return [sentence for sentence in sentences if sentence and not requests_roll(sentence)]


def _safe_quote(text: str) -> str | None:
    quoted = _clip(re.sub(r'\s+', ' ', str(text or '')).strip().replace('"', "'"), MAX_QUOTED_INPUT_CHARS)
    if not quoted or requests_roll(quoted):
        return None
    return quoted


# Small helpers ------------------------------------------------------------------------------------------------------


def _dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _dicts(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value).strip() if value not in (None, '') else ''


def _key(value: Any) -> str:
    return _text(value).casefold().replace(' ', '_').replace('-', '_')


def _string_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items = []
    for item in value:
        if isinstance(item, dict):
            item = item.get('text') or item.get('description') or item.get('summary')
        text = _text(item)
        if text:
            items.append(text)
    return items


def _humanize(value: Any) -> str:
    return re.sub(r'\s+', ' ', _text(value).replace('_', ' ')).strip()


def _number(value: Any) -> str:
    return str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else '?'


def _int_or_none(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _sentence(text: str) -> str:
    text = _text(text)
    if not text:
        return ''
    text = text[0].upper() + text[1:]
    if text[-1] not in '.!?…"\'”':
        text += '.'
    return text


def _split_sentences(text: str) -> list[str]:
    return [_sentence(part) for part in re.split(r'(?<=[.!?])\s+', _text(text)) if part.strip()]


def _speech(fact: str) -> str:
    fact = _text(fact).replace('"', "'")
    if fact.endswith('.'):
        return f'{fact[:-1]},'
    return fact


def _clip(text: str, limit: int) -> str:
    text = _text(text)
    if len(text) <= limit:
        return text
    cut = text[: limit - 1].rsplit(' ', 1)[0].rstrip(' ,;:')
    return f'{cut}…'


def _clip_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    return ' '.join(words[: max(1, limit)]).rstrip(' ,;:') + '…'


def _word_count(text: str) -> int:
    return len(re.findall(r'\S+', text or ''))


def _normalized(text: str) -> str:
    return re.sub(r'\s+', ' ', _text(text)).casefold()


def _join_words(items: list[str]) -> str:
    items = [item for item in items if item]
    if len(items) <= 1:
        return ''.join(items)
    if len(items) == 2:
        return f'{items[0]} and {items[1]}'
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _first_name(name: str) -> str | None:
    first = _text(name).split(' ')[0] if _text(name) else ''
    if len(first) < 3 or first.casefold() in FIRST_NAME_STOPWORDS or first == _text(name):
        return None
    return first


def _article(label: str) -> str:
    return 'an' if label[:1].lower() in 'aeiou' else 'a'


def _roll_label(rule_type: str) -> str:
    if rule_type in RULE_TYPE_LABELS:
        return RULE_TYPE_LABELS[rule_type]
    if rule_type in SKILL_ABILITY:
        skill = ' '.join(word if word == 'of' else word.title() for word in _humanize(rule_type).split())
        return f'{SKILL_ABILITY[rule_type].title()} ({skill})'
    if rule_type.endswith('_saving_throw'):
        return f"{rule_type[: -len('_saving_throw')].title()} saving throw"
    if rule_type in ABILITY_NAMES:
        return rule_type.title()
    return RULE_TYPE_LABELS['check']


def _base_dc(rule_type: str) -> int | None:
    if rule_type == 'initiative':
        return None
    if rule_type in ROLL_TYPE_BASE_DC:
        return ROLL_TYPE_BASE_DC[rule_type]
    return ROLL_TYPE_BASE_DC['check']


def _dc_from_hint(dc_hint: Any) -> int | None:
    text = _text(dc_hint)
    match = re.search(r'\bDC\s*(\d{1,2})\b', text, re.IGNORECASE) or re.match(r'^(\d{1,2})(?!\s*-)', text)
    if not match:
        return None
    value = int(match.group(1))
    return value if 5 <= value <= 30 else None
