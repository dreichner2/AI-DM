"""Deterministic Safe Mode narrator.

Narrates engine-resolved results and authored scene content from the same
CONTEXT, RULES_HINT and player input the live DM model receives, without any
network call. It says only what happened: a reply is the result paragraphs,
with the scene paragraph only in the opening, on arrival and when the hero
looks around, and the closing question only in the opening. The pipeline's
``narration`` packet (``RULES_HINT.state_pipeline.narration``) names the
objects acted on, the person talked to, how typed words mapped, the authored
setup and adjustments of a fight that starts, the enemy abilities whose
conditions the hero shakes off as their turn ends, a rule that newly moved the
hero's attack roll (``attackModifiers``), who acts next in a fight, how a
fight ends and a party defeat. An enemy turn is told as what it does in the
world, never by the planner's name for it. Phrasing varies by turn without
randomness: each template has a few variants chosen by
``sha1(f"{turn_number}:{template_key}")``.

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
from aidm_server.game_state.orchestration.narration_facts import TAKE_TIME_LINE
from aidm_server.turn_roll_policy import TurnRollPolicy
from aidm_server.turn_rules import response_mentions_roll_request


MAX_OPENING_WORDS = 220
MAX_OPENING_DETAILS = 3
OPENING_CLOSING_LINE = 'What do you do?'
OPENING_MESSAGE_KIND = 'opening'
ONE_THING_AT_A_TIME = 'One thing at a time — the rest is still yours to do.'
DEFEAT_PARAGRAPH = (
    'Darkness takes you. When you come to, the light has changed and {enemies} {be} gone. You are alive — barely.'
)
COIN_CODES = frozenset({'pp', 'gp', 'ep', 'sp', 'cp'})
# After a failed check for an object action, said after the action's sentence.
FAILED_CHECK_LINES = {'search': 'You come up empty.', 'inspect': 'You come up empty.'}
# An enemy turn that rolls nothing, by intent; ``{enemy}`` is its name in prose, ``{ally}`` the ally it shields,
# ``{its}`` its possessive (``their`` for a proper name: "The Cinder Scribe turns back to their work.").
ENEMY_TURN_LINES = {
    'delay': '{enemy} hangs back, watching.',
    'protect_ally': '{enemy} moves to shield {ally}.',
    'complete_objective': '{enemy} turns back to {its} work.',
    'use_environment': '{enemy} looks for an advantage in the ground.',
    'negotiate': '{enemy} throws down {its} weapon and yields.',
    'surrender': '{enemy} throws down {its} weapon and yields.',
    'yield': '{enemy} throws down {its} weapon and yields.',
    'reposition': '{enemy} moves for a better angle.',
    'defend': '{enemy} takes cover.',
    'hide': '{enemy} takes cover.',
    'call_reinforcements': '{enemy} shouts for help.',
}
FLEEING_INTENTS = frozenset({'retreat', 'flee'})
SAVE_ABILITY_NAMES = {
    'str': 'Strength',
    'dex': 'Dexterity',
    'con': 'Constitution',
    'int': 'Intelligence',
    'wis': 'Wisdom',
    'cha': 'Charisma',
}

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
TEMPLATES: dict[str, tuple[str, ...]] = {
    'opening_npcs': (
        '{npcs} {verb} here with you.',
        'Close at hand {verb} {npcs}.',
        'Within earshot {verb} {npcs}.',
    ),
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
    'enemy_save': ('{enemy} turns {ability} on {target}: a {save_ability} save of {total} against DC {dc}, {outcome}{damage_clause}.',),
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
    'roll_success_investigation': (
        'You find what the others missed.',
        'Something gives itself away.',
        'Your attention catches on one thing.',
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
        'You commit to the attempt, and everything around you narrows to this one moment, the next breath deciding how cleanly you carry it off.',
        'You set yourself and go for it, with no way to take it back once you start and the outcome hanging on nerve and timing.',
        'You draw a slow breath and make your move, knowing the next few heartbeats will show whether you judged this right.',
    ),
    'no_target': ('There is no one here to fight.', 'Your blade finds only air; no enemy stands before you.'),
    'npc_knows': (
        '"{fact}" {first} tells you.',
        '"{fact}" {first} says, voice low.',
        '"{fact}" {first} says after a moment.',
    ),
    'nothing_happens': ('Nothing comes of it yet.', 'The moment holds, and nothing changes.', 'No answer comes.'),
    'scene_present': ('{npcs} {verb} here.', 'Close at hand {verb} {npcs}.', 'Within earshot {verb} {npcs}.'),
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
    def facts(self) -> dict:
        return _dict(self.pipeline.get('narration'))

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

    def creature(self, name: Any) -> str:
        """An enemy's name in prose: ``the wardbound beast``; a proper name keeps its capitals."""

        text = _text(name)
        for enemy in _dicts(self.facts.get('enemies')):
            if _text(enemy.get('name')).casefold() != text.casefold():
                continue
            if enemy.get('proper') is True:
                return f'the {text[4:]}' if text.lower().startswith('the ') else text
            return f'the {text.lower()}'
        return text

    def is_proper(self, name: Any) -> bool:
        """The enemy has a proper name (``The Cinder Scribe``), so it is a someone: ``their``, not ``its``."""

        text = _text(name).casefold()
        return any(_text(enemy.get('name')).casefold() == text and enemy.get('proper') is True for enemy in _dicts(self.facts.get('enemies')))


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
        return _compose(turn, [_refusals(invalid_actions)])
    # The place first, then the hero's own result, then the fight, then what it brought.
    arrival = _text(turn.facts.get('arrival'))
    blocks: list[list[str]] = _arrival_blocks(turn, {'locationName': _the(arrival)}) if arrival else []
    own_blocks, aftermath = _change_blocks(turn)
    blocks.append([*_spell_lines(turn), *_capability_lines(turn)])
    blocks.extend(own_blocks)
    blocks.extend(_fight_start_blocks(turn))
    blocks.append(_combat_lines(turn))
    blocks.append(aftermath)
    roll_paragraphs = _resolved_roll_paragraphs(turn)
    if roll_paragraphs:
        blocks.extend([[roll_paragraphs[0]], roll_paragraphs[1:]])
    if any(blocks) or turn.facts.get('partyDefeat'):
        return _compose(turn, blocks)
    if turn.rules_hint.get('no_target') is True:
        return _compose(turn, [[turn.pick('no_target')]])
    if turn.rules_hint.get('requires_roll') is True and turn.rules_hint.get('roll_value') is None:
        return _narrate_roll_request(turn)
    talk = _dict(turn.facts.get('talk'))
    if talk:
        return _compose(turn, [[_talk_line(turn, talk)]])
    npc = _addressed_npc(turn)
    if npc:
        return _compose(turn, [[_npc_talk_line(turn, npc)]])
    if _dict(turn.facts.get('freeText')).get('kind') == 'look':
        return _compose(turn, _scene_paragraphs(turn))
    return _compose(turn, [[turn.pick('nothing_happens')]])


# Cases ------------------------------------------------------------------------------------------------------------


def _narrate_opening(turn: NarrationInput) -> str:
    brief = turn.opening_brief or {}
    brief_scene = _dict(brief.get('scene'))
    scene = turn.scene
    lead = _text(brief_scene.get('summary') or scene.get('summary')) or _text(
        brief_scene.get('description') or scene.get('description')
    )
    details = _string_items(brief_scene.get('visibleDetails')) or _string_items(scene.get('visibleDetails'))
    # Roles are authoring labels ("accidental witness"); the cast is introduced by name only.
    npcs = [
        _text(npc.get('name'))
        for npc in _dicts(brief.get('presentNpcs')) or turn.present_npcs
        if _text(npc.get('name'))
    ]
    blocks = [
        _safe_sentences(_split_sentences(lead)),
        _safe_sentences([_sentence(detail) for detail in details[:MAX_OPENING_DETAILS]]),
    ]
    if npcs:
        listed = _join_words(npcs)
        blocks.append(_safe_sentences([turn.pick('opening_npcs', npcs=listed, verb='is' if len(npcs) == 1 else 'are')]))
    blocks = _clip_blocks([block for block in blocks if block], MAX_OPENING_WORDS - _word_count(OPENING_CLOSING_LINE))
    return '\n\n'.join(' '.join(block) for block in [*blocks, [OPENING_CLOSING_LINE]])


def _refusals(invalid_actions: list[dict]) -> list[str]:
    # A refusal is the plain sentence the rules gave, with no prefix.
    lines = []
    for action in invalid_actions[:2]:
        reason = _text(action.get('reason') or action.get('summary')) or 'That can\'t be done right now.'
        lines.append(_sentence(_clip(reason, 200)))
    return _safe_sentences(lines)


def _narrate_roll_request(turn: NarrationInput) -> str:
    # The player's own words sit directly above this narration, so they are never quoted back.
    return _compose(turn, [[turn.pick('roll_ack')], [_roll_request_line(turn)]])


def _talk_line(turn: NarrationInput, talk: dict) -> str:
    name = _text(talk.get('name'))
    fact = _text(talk.get('fact'))
    if not fact:
        return f'{name or "They"} has nothing more to add.'
    first = _first_name(name) or name
    return turn.pick('npc_knows', fact=_speech(fact), first=first)


def _npc_talk_line(turn: NarrationInput, npc: dict) -> str:
    """Without the pipeline's talk record: the person's next fact by how often they were addressed."""

    name = _text(npc.get('name'))
    knows = _npc_knows(turn, npc)
    if not knows:
        return f'{name} has nothing more to add.'
    mentions = sum(1 for recent in _dicts(turn.context.get('recent_turns')) if _mentions_npc(recent, npc))
    if mentions >= len(knows):
        return f'{name} has nothing more to add.'
    return turn.pick('npc_knows', fact=_speech(knows[mentions]), first=_first_name(name) or name)


def _scene_paragraphs(turn: NarrationInput) -> list[list[str]]:
    scene = turn.scene
    lead = _split_sentences(_text(scene.get('summary')) or _text(scene.get('description')))
    details = [_sentence(detail) for detail in _string_items(scene.get('visibleDetails'))]
    npcs = [_text(npc.get('name')) for npc in turn.present_npcs]
    present = (
        [turn.pick('scene_present', npcs=_join_words(npcs), verb='is' if len(npcs) == 1 else 'are')] if npcs else []
    )
    return [_safe_sentences(lead), _safe_sentences(details), _safe_sentences(present)]


# Mechanical results ---------------------------------------------------------------------------------------------


def _fight_start_blocks(turn: NarrationInput) -> list[list[str]]:
    """A fight that starts: the encounter's read-aloud setup, then each adjustment's line, before the first blows."""

    started = _dict(turn.facts.get('fightStart'))
    blocks = [_safe_sentences(_split_sentences(_text(started.get('setup'))))]
    blocks.extend(_safe_sentences([_sentence(line)]) for line in _string_items(started.get('adjustments')))
    return [block for block in blocks if block]


def _combat_lines(turn: NarrationInput) -> list[str]:
    """The fight in order: it starts, the hero's move or attack, the enemies' turns, who fell, death saves, its end."""

    combat_state = _dict(turn.pipeline.get('combatState'))
    lines = [turn.pick('combat_start') for change in turn.applied_changes if _text(change.get('type')) == 'combat.start'][:1]
    targets: list[str] = []
    tactical = _tactical_line(turn, _dict(combat_state.get('playerResolvedCombatAction')))
    if tactical:
        lines.append(tactical)
    player_action = _dict(combat_state.get('playerResolvedAction'))
    attacks = [player_action] if player_action else []
    for event in _dicts(combat_state.get('trustedDamageEvents')):
        if event not in attacks:
            attacks.append(event)
    # A rule that moved the hero's roll is named once, on the attack it first changed.
    modifier_clause = _modifier_clause(turn.facts.get('attackModifiers')) if player_action else ''
    for attack in attacks:
        line = _player_attack_line(turn, attack)
        if modifier_clause and attack is player_action:
            line = f'{line[:-1]} — {modifier_clause}.'
        lines.append(line)
        targets.append(_text(attack.get('targetName')))
    for entry in _dicts(turn.facts.get('shakenOff')):
        person = _person(turn, entry.get('name'))
        lines.append(_sentence(f"{person['target']} {person['shake']} off {_text(entry.get('source'))}"))
    enemy_actions = _dicts(combat_state.get('enemyResolvedActions'))
    fleeing = [action for action in enemy_actions if _fled(action)]
    routed = _key(turn.facts.get('fightEnds')) == 'enemies_fled'
    for action in enemy_actions:
        if routed and len(fleeing) > 1 and _fled(action):
            # The last of several enemies breaking at once is told once.
            line = 'The last of them break and run.' if action is fleeing[0] else None
        else:
            line = _enemy_action_line(turn, action, routed=routed)
        if line:
            lines.append(line)
        conditions = _string_items(action.get('conditionsApplied'))
        if conditions:
            lines.append(_conditions_line(turn, action.get('targetName'), conditions))
        targets.append(_text(action.get('targetName')))
    lines.extend(_defeat_lines(turn, targets))
    for change in turn.applied_changes:
        change_type = _text(change.get('type'))
        if change_type == 'combat.end':
            status = _key(change.get('combatStatus'))
            outcome = f': {_humanize(status)}' if status and status not in {'ended', 'none', 'complete', 'completed'} else ''
            lines.append(turn.pick('combat_end', outcome=outcome))
        elif change_type == 'combat.death_save':
            lines.append(_death_save_line(turn, change))
    return lines


def _tactical_line(turn: NarrationInput, action: dict) -> str:
    """The hero's move or tactic; closing into melee is told in the same voice as the attack after it."""

    movement = _dict(action.get('movement'))
    if _key(action.get('actionType')) == 'advance' and _key(movement.get('toRangeBand')) == 'melee' and movement.get('engagedWith'):
        actor = _text(action.get('actorName'))
        who = 'You close' if not actor or turn.is_speaker(actor) else f'{actor} closes'
        return _sentence(f"{who} in on {_target_ref(turn, action.get('targetName'))}")
    return _sentence(_text(action.get('outcome')))


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


def _modifier_clause(modifiers: Any) -> str:
    """``your wounds cost you 2`` for the hero's roll modifiers (``[{"label", "value"}]``)."""

    parts = []
    for modifier in _dicts(modifiers):
        label, value = _text(modifier.get('label')), _int_or_none(modifier.get('value'))
        if not label or not value:
            continue
        if label == 'Wounded' and value < 0:
            parts.append(f'your wounds cost you {-value}')
        else:
            parts.append(f'{label.lower()} {"+" if value > 0 else "−"}{abs(value)}')
    return _join_words(parts)


def _fled(action: dict) -> bool:
    return (
        action.get('resolvedWithoutRoll') is True
        and _key(action.get('intentType')) in FLEEING_INTENTS
        and action.get('moveOnly') is not True
        and action.get('droppedByReaction') is not True
    )


def _unrolled_enemy_line(turn: NarrationInput, action: dict, enemy: str, *, routed: bool) -> str:
    """What an enemy turn that rolled nothing did, in the world's words."""

    intent = _key(action.get('intentType'))
    if action.get('turnLost') is True:
        return _sentence(f'{enemy} cannot act')
    if action.get('moveOnly') is True:
        return _sentence(f'{enemy} closes in')
    if intent in FLEEING_INTENTS:
        return _sentence(f'{enemy} breaks and runs' if routed else f'{enemy} gives ground')
    template = ENEMY_TURN_LINES.get(intent, '{enemy} waits.')
    its = 'their' if turn.is_proper(action.get('enemyName')) else 'its'
    return _sentence(template.format(enemy=enemy, ally=turn.creature(action.get('targetName')) or 'an ally', its=its))


def _enemy_action_line(turn: NarrationInput, action: dict, *, routed: bool = False) -> str | None:
    enemy = turn.creature(action.get('enemyName')) or 'The enemy'
    target = _target_ref(turn, action.get('targetName'))
    ability = _text(action.get('abilityName')) or 'an attack'
    if action.get('resolvedWithoutRoll') is True:
        return _unrolled_enemy_line(turn, action, enemy, routed=routed)
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
                save_ability=SAVE_ABILITY_NAMES.get(_key(action.get('saveAbility'))[:3], _humanize(action.get('saveAbility')).title() or 'ability'),
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
            lines.append(_sentence(turn.pick('defeated', target=turn.creature(name))))
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


def _change_blocks(turn: NarrationInput) -> tuple[list[list[str]], list[str]]:
    """The hero's own results (arrival, objects, what was taken, a rest), and the other results after them."""

    facts = turn.facts
    blocks: list[list[str]] = []
    lines: list[str] = []
    taken = [_text(phrase) for phrase in facts.get('taken') or [] if _text(phrase)]
    if taken:
        blocks.append([f'You take {_join_words(taken)}.'])
    rest = _dict(facts.get('rest'))
    if rest:
        blocks.append([turn.pick('rest_hp', amount=rest['hp']) if isinstance(rest.get('hp'), int) and rest['hp'] > 0 else turn.pick('rest_plain')])
    object_facts = _dicts(facts.get('objects'))
    for change in turn.applied_changes:
        change_type = _text(change.get('type'))
        item = _text(change.get('itemName'))
        amount = change.get('amount')
        quest = _text(change.get('questTitle'))
        if change_type == 'scene.move_location':
            if not _text(facts.get('arrival')):
                blocks.extend(_arrival_blocks(turn, change))
        elif change_type == 'scene.interactable.action':
            fact = next(
                (fact for fact in object_facts if _text(fact.get('targetId')) == _text(change.get('targetId'))),
                None,
            )
            blocks.extend(_object_blocks(fact) if fact else [[_object_sentence(change)]])
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
        elif change_type == 'rest.complete' and not rest:
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
    return [_safe_sentences(block) for block in blocks], _safe_sentences(lines)


def _arrival_blocks(turn: NarrationInput, change: dict) -> list[list[str]]:
    scene = turn.scene
    place = _text(change.get('locationName')) or _text(scene.get('name')) or 'a new place'
    description = _text(scene.get('summary')) or _text(scene.get('description'))
    lines = [turn.pick('travel', place=place), *_split_sentences(description)[:2]]
    npcs = [_text(npc.get('name')) for npc in turn.present_npcs]
    if npcs:
        lines.append(turn.pick('travel_present', npcs=_join_words(npcs), verb='is' if len(npcs) == 1 else 'are'))
    return [lines]


def _object_blocks(fact: dict) -> list[list[str]]:
    """What one object action did: its steps, its sentence, the description told once, what a search found."""

    name = _text(fact.get('name'))
    action = _key(fact.get('action'))
    opening = [TAKE_TIME_LINE] if fact.get('takeTime') is True else []
    steps = _dicts(fact.get('steps'))
    if steps:
        opening.append(_steps_sentence(steps, _text(fact.get('keyName'))))
    opening.append(_text(fact.get('actionText')) or _label_sentence(_text(fact.get('label')), fact.get('labelAuthored') is True, action, name))
    if fact.get('failed') is True:
        return [_safe_sentences([*(_sentence(line) for line in opening), FAILED_CHECK_LINES.get(action, 'It will not give.')])]
    blocks = [_safe_sentences([_sentence(line) for line in opening])]
    description = _text(fact.get('description'))
    if description:
        blocks.append(_split_sentences(description))
    if action == 'search':
        blocks.append(_found_sentences(fact.get('found'), fact.get('uncovered'), discovered=fact.get('discovered') is True))
    elif fact.get('nothingNew') is True:
        blocks.append([f'You find nothing new on {_the(name)}.'])
    return blocks


def _label_sentence(label: str, authored: bool, action: str, name: str) -> str:
    """``Ring the wagon bell`` -> ``You ring the wagon bell.``; a default label names the object with an article."""

    if authored and label:
        return _sentence(f'You {label[:1].lower()}{label[1:]}')
    verb = label.split(' ', 1)[0].lower() if label else action
    return _sentence(f'You {verb} {_the(name)}')


def _steps_sentence(steps: list[dict], key_name: str) -> str:
    """``You unlock the strongbox with the Caravan Strongbox Key and lift the lid.``"""

    first = steps[0]
    action = _key(first.get('action'))
    label = _text(first.get('label'))
    sentence = f'You {label[:1].lower()}{label[1:]}' if label else f'You {action} it'
    if action == 'unlock' and key_name:
        sentence += f' with {_the(key_name)}'
    if action == 'unlock' and any(_key(step.get('action')) == 'open' for step in steps[1:]):
        sentence += ' and lift the lid'
    return _sentence(sentence)


def _found_sentences(found: Any, uncovered: Any, *, discovered: bool = False) -> list[str]:
    """What a search turned up: the items found, the hidden objects uncovered, or nothing more
    (unless it ``discovered`` a clue, a place or a story flag, which the search's own words tell)."""

    items = [_text(item) for item in (found if isinstance(found, list) else []) if _text(item)]
    objects = [_text(name) for name in (uncovered if isinstance(uncovered, list) else []) if _text(name)]
    sentences = []
    if items:
        coins = [item for item in items if item.split()[-1].lower() in COIN_CODES]
        sentences.append(f'You find {_join_words([*coins, *(item for item in items if item not in coins)])}.')
    if objects:
        sentences.append(f'You uncover {_join_words([_the(name) for name in objects])}.')
    return sentences or ([] if discovered else ['You find nothing more.'])


def _object_sentence(change: dict) -> str:
    """Without the pipeline's object record: one plain sentence about the action."""

    name = _text(change.get('targetName')) or _humanize(change.get('targetId')) or 'it'
    action = _key(change.get('objectAction')) or 'work at'
    return _sentence(f'You {_humanize(action)} {_the(name)}')


def _the(name: str) -> str:
    """``the Driver's Bench``; a name that already starts with "The" keeps it, lower-cased."""

    if not name:
        return 'it'
    if name.lower().startswith('the '):
        return f'the {name[4:]}'
    return f'the {name}'


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
    return turn.creature(name) or 'the target'


def _person(turn: NarrationInput, name: Any) -> dict[str, str]:
    """Subject, object and verb forms for a named participant, in second person for the speaker."""
    if turn.is_speaker(name):
        forms = dict(target='You', target_lower='you', target_object='you', be='are')
        verbs = ('take', 'recover', 'gain', 'resist', 'fight', 'cling', 'manage', 'shake', 'drop')
        return {**forms, **{verb: verb for verb in verbs}}
    display = turn.creature(name) or 'the target'
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


def _compose(turn: NarrationInput, blocks: list[list[str]]) -> str:
    """The reply's paragraphs, then a defeat, the fight's next actor and the one-action reminder."""

    paragraphs = [block for block in blocks if block]
    facts = turn.facts
    defeat = _dict(facts.get('partyDefeat'))
    if defeat:
        enemies = _text(defeat.get('enemies')) or 'the enemies'
        paragraphs.append([DEFEAT_PARAGRAPH.format(enemies=enemies, be='are' if defeat.get('plural') is not False else 'is')])
    elif _text(facts.get('nextActorLine')) and not _fight_ended(turn):
        paragraphs.append([_text(facts.get('nextActorLine'))])
    if _dict(facts.get('freeText')).get('more') is True:
        paragraphs.append([ONE_THING_AT_A_TIME])
    return '\n\n'.join(' '.join(paragraph) for paragraph in paragraphs)


def _fight_ended(turn: NarrationInput) -> bool:
    """The fight ends this turn, so its ending sentence stands alone."""

    return bool(turn.facts.get('fightEnds')) or any(_text(change.get('type')) == 'combat.end' for change in turn.applied_changes)


def _clip_blocks(blocks: list[list[str]], budget: int) -> list[list[str]]:
    """Keep whole sentences while they fit ``budget`` words; a first sentence too long alone is clipped."""

    kept: list[list[str]] = []
    used = 0
    for block in blocks:
        paragraph = []
        for sentence in block:
            words = _word_count(sentence)
            if used + words > budget:
                if not kept and not paragraph:
                    paragraph.append(_clip_words(sentence, budget))
                return [*kept, paragraph] if paragraph else kept
            paragraph.append(sentence)
            used += words
        kept.append(paragraph)
    return kept


def requests_roll(text: str) -> bool:
    """True when the server would read ``text`` as asking the player to roll."""
    return (
        response_mentions_roll_request(text)
        or TurnRollPolicy.response_requests_group_roll(text)
    )


def _safe_sentences(sentences: list[str]) -> list[str]:
    return [sentence for sentence in sentences if sentence and not requests_roll(sentence)]


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
