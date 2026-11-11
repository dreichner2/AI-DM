"""Deterministic Safe Mode narrator.

Narrates engine-resolved results and authored scene content from the same
CONTEXT, RULES_HINT and player input the live DM model receives, without any
network call. It says only what happened: a reply is the result paragraphs,
with the scene paragraph only in the opening, on arrival and when the hero
looks around, and the closing question only in the opening (and on a turn that
leaves a finishing blow to the hero). The pipeline's
``narration`` packet (``RULES_HINT.state_pipeline.narration``) names the
objects acted on, the person talked to, how typed words mapped, the authored
setup and adjustments of a fight that starts, the enemy abilities whose
conditions the hero shakes off as their turn ends, a rule that newly moved the
hero's attack roll (``attackModifiers``), who acts next in a fight, how a
fight ends and a party defeat. An enemy turn is told as what it does in the
world, never by the planner's name for it. Phrasing varies by turn without
randomness: each template has a few variants chosen by
``sha1(f"{turn_number}:{template_key}")``.

Alone, the hero is "you". In party play (the packet's ``party``: more than one
hero in the session) one story is read by every player, so no sentence says
"you": the acting or affected hero is named, then "they" (``PARTY_TEMPLATES``).
When a hero's turn ends with nothing else to tell, the narration is only the
line saying who acts next (:func:`engine_turn_line`, which the AI narrator's
replies end with as well).

Fight maneuvers, the improvised action, an enemy breaking free of a hold or
yielding in it, and opportunity attacks on an enemy that breaks away are told in
their own words (an enemy is "it" unless its record gives a pronoun); a
captive answers questions with its record's ``captiveKnows``, and is left
behind when the party travels on. A failed attempt says the hero tries ("You
try to break the strongbox open."), never that it was done.

On a turn whose rolls are shown to the players as cards (``storyRolls``), each
rolled result is told without its numbers (``ROLLED_TEMPLATES``, three ways per
outcome). Enemies speak or show their records' ``barks`` where the packet's
``barks`` place them (a taunt replaces a turn that only filled time). A hero who
looks over a fight is told where everyone stands. The finishing blow: the foe a
hero fells that way is not told dying, and the reply ends on the one closing
question (``finishingBlow``); the hero's words that end it are followed by its
fall, or by its sparing (``finisher``); a moment let pass opens the turn with
``{Enemy} falls.`` (``finisherLapsed``). A tie line told for a hero (``threads``)
is its own paragraph after the hero's results and a fight's setup; taking heart
(``takeHeart``) is one line.

A conversation (``conversation``) is told in the people's own authored words: opening it tells the person's
greeting the first time (a greeting that is all speech is given to them: ``"…," Mara says.``), after that
``Mara Reedwake turns to you.``; a topic's line or its failure line verbatim, the generated topic's ``knows`` line as
before; a reaction's authored line, else one plain sentence for what the act did; words that reach nothing read
``Mara Reedwake has nothing to say to that.``; leaving reads ``You leave it there with Mara Reedwake.``.

The turn's director note (``director``, :mod:`aidm_server.game_state.orchestration.director`): a callback opens the
reply as its own paragraph, word for word; a clock step or a nudge is its last paragraph (before whose turn it is),
word for word, as is the clarify note's sentence naming two things on offer; the breathe note adds nothing.

In a place with rooms the people present are those in the hero's own zone
(``present``). A step into another zone opens the reply (``zoneMove``): ``You
step into the common room.``, the zone's description the first time, then who
is there; a person next door called to answers in one line (``callOut``). A
fight that starts says where its enemies stand in reach words and who came
running from another zone, and an enemy coming in is told in distance words
(``The wardbound beast crosses about thirty feet to you.``).

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
from aidm_server.combat.maneuvers import IMPROVISE_TYPE, MANEUVER_TYPES, pronoun
from aidm_server.game_state.orchestration import director
from aidm_server.game_state.orchestration.narration_facts import TAKE_TIME_LINE, filler_enemy_turn
from aidm_server.prompt_templates import rolls_shown
from aidm_server.turn_roll_policy import TurnRollPolicy
from aidm_server.turn_rules import response_mentions_roll_request


MAX_OPENING_WORDS = 220
MAX_OPENING_DETAILS = 3
OPENING_CLOSING_LINE = 'What do you do?'
OPENING_MESSAGE_KIND = 'opening'
ONE_THING_AT_A_TIME = 'One thing at a time — {target} is still there to {verb}.'
DEFEAT_PARAGRAPH = (
    'Darkness takes you. When you come to, the light has changed and {enemies} {be} gone. You are alive — barely.'
)
PARTY_DEFEAT_PARAGRAPH = (
    'Darkness takes the party. When they come to, the light has changed and {enemies} {be} gone. They are alive — barely.'
)
COIN_CODES = frozenset({'pp', 'gp', 'ep', 'sp', 'cp'})
# After a failed check for an object action, what the hero does, said after the action's sentence.
FAILED_CHECK_LINES = {'search': 'come up empty', 'inspect': 'come up empty'}
# Verbs whose third person is not the plain ``-s``/``-es``/``-ies`` form.
IRREGULAR_THIRD_PERSON = {'be': 'is', 'have': 'has', 'do': 'does', 'go': 'goes'}
# Verb slots of ``_person``: the base form for "you" and "they", the third person for a name.
PERSON_VERBS = (
    'take', 'recover', 'gain', 'resist', 'fight', 'cling', 'manage', 'shake', 'drop', 'reel', 'twist', 'stand', 'go', 'stay',
)
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
CAPTIVE_SILENT = {False: '{captive} glares and says nothing more you can use.', True: '{captive} glares and says nothing more the party can use.'}
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
    'capability_heal_rolled': (
        'You use {name}, and strength returns{to_target}.',
        'You call on {name}, and strength floods back{to_target}.',
        '{name} answers you, and strength returns{to_target}.',
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
# Party play: the same lines naming the hero (``{actor}``, ``{actor_s}`` for the possessive) instead of "you".
PARTY_TEMPLATES: dict[str, tuple[str, ...]] = {
    'opening_npcs': (
        '{npcs} {verb} here with the party.',
        'Close at hand {verb} {npcs}.',
        'Within earshot {verb} {npcs}.',
    ),
    'spell_cast': ('{actor} casts {spell}.', '{actor} looses {spell}.', '{actor} shapes {spell} and lets it go.'),
    'concentration': (
        'They hold their concentration on {spell}.',
        'They keep {spell} steady in their mind.',
        'They hold {spell} with steady focus.',
    ),
    'capability_heal': (
        '{actor} uses {name}, restoring {amount} hit points{to_target}.',
        '{actor} calls on {name} and restores {amount} hit points{to_target}.',
        '{name} answers {actor}, restoring {amount} hit points{to_target}.',
    ),
    'capability_heal_rolled': (
        '{actor} uses {name}, and strength returns{to_target}.',
        '{actor} calls on {name}, and strength floods back{to_target}.',
        '{name} answers {actor}, and strength returns{to_target}.',
    ),
    'capability_effect': ('{actor} uses {name}: {effect}.', '{actor} calls on {name}: {effect}.', '{name} answers {actor}: {effect}.'),
    'capability_plain': ('{actor} uses {name}.', '{actor} calls on {name}.', '{name} answers when {actor} reaches for it.'),
    'travel': ('The party arrives at {place}.', 'The way opens onto {place}.', 'The party reaches {place}.'),
    'travel_present': ('{npcs} {verb} here.', 'Waiting here {verb} {npcs}.', 'The party finds {npcs} here.'),
    'item_gain_one': ('{actor} now carries {item}.', '{item} goes into {actor_s} pack.', '{actor} takes {item}.'),
    'item_gain_many': ('{actor} now carries {amount} {item}.', '{amount} {item} go into {actor_s} pack.', '{actor} takes {amount} {item}.'),
    'item_lose': ('{actor} no longer carries {item}.', '{item} leaves {actor_s} hands.', '{actor} parts with {item}.'),
    'item_use': ('{actor} uses {item}.', '{actor} puts {item} to use.', '{actor} makes use of {item}.'),
    'item_equip': ('{actor} readies {item}.', '{actor} takes up {item}.', '{actor} fits {item} into place.'),
    'item_unequip': ('{actor} stows {item}.', '{actor} puts {item} away.', '{actor} sets {item} aside.'),
    'rest_hp': (
        '{actor} rests and recovers {amount} hit points.',
        'Rest does its work: {actor} recovers {amount} hit points.',
        '{actor} comes back from their rest {amount} hit points stronger.',
    ),
    'rest_plain': ('{actor} finishes their rest.', '{actor_s} rest ends, and their strength is their own again.', '{actor} rises from their rest.'),
    'currency_gain': ('{actor} gains {amount} {currency}.', '{amount} {currency} goes into {actor_s} purse.', '{actor} pockets {amount} {currency}.'),
    'currency_spend': ('{actor} spends {amount} {currency}.', '{amount} {currency} leaves {actor_s} purse.', '{actor} pays {amount} {currency}.'),
    'roll_success_investigation': (
        '{actor} finds what the others missed.',
        'Something gives itself away.',
        '{actor_s} attention catches on one thing.',
    ),
    'roll_failure_investigation': (
        '{actor} looks again, but nothing new reveals itself.',
        'Whatever is here stays hidden from {actor} for now.',
        '{actor} comes away with nothing they did not already know.',
    ),
    'roll_success_stealth_npc': (
        '{actor} slips out of sight, and {npc} never looks their way.',
        '{actor} moves like a shadow; {npc} does not notice a thing.',
        '{npc} glances right past {actor}, none the wiser.',
    ),
    'roll_success_stealth': (
        '{actor} slips out of sight and stays there, unseen.',
        '{actor} moves like a shadow and nobody marks them.',
        '{actor} folds into the dark, and no alarm follows.',
    ),
    'roll_failure_stealth_npc': (
        '{npc} turns sharply toward {actor}; they have been noticed.',
        'A scuff gives {actor} away, and {npc} looks straight at them.',
        '{npc} stiffens and stares right at where {actor} is hiding.',
    ),
    'roll_failure_stealth': (
        'A loose stone skitters under {actor_s} feet, and they know they have been noticed.',
        'A scuff gives {actor} away; heads turn toward them.',
        '{actor} misjudges the cover and is seen.',
    ),
    'roll_success_physical_npc': (
        '{actor} makes it cleanly, and {npc} sees exactly how easily they managed it.',
        '{actor} pulls it off in one smooth motion while {npc} watches.',
        '{actor} lands it, and {npc} has to look twice.',
    ),
    'roll_success_physical': (
        '{actor} makes it cleanly and lands on their feet.',
        '{actor} pulls it off in one smooth motion.',
        'Muscle and timing hold; {actor} makes it.',
    ),
    'roll_failure_physical_npc': (
        '{actor_s} footing goes and the attempt falls short, in full view of {npc}.',
        '{actor} comes up short and stumbles, and {npc} sees all of it.',
        'It gets away from {actor}, with {npc} watching.',
    ),
    'roll_failure_physical': (
        '{actor_s} footing goes and the attempt falls short.',
        '{actor} comes up short and stumbles.',
        'It gets away from {actor} halfway through.',
    ),
    'roll_success_social_npc': (
        '{npc} hesitates, then gives way.',
        '{actor_s} words land; {npc} comes around.',
        '{npc} weighs {actor} for a long moment and yields.',
    ),
    'roll_success_social': (
        '{actor_s} words land, and the resistance in front of them softens.',
        '{actor} finds the right words at the right moment.',
        'They come around to {actor_s} way of seeing it.',
    ),
    'roll_failure_social_npc': (
        '{npc} is unmoved and makes that plain.',
        '{actor_s} words slide off {npc} without leaving a mark.',
        '{npc} shakes their head; {actor} has not convinced anyone.',
    ),
    'roll_failure_social': (
        '{actor_s} words fall flat.',
        'Nobody is moved by what {actor} says.',
        'It comes out wrong, and {actor} can see it land wrong.',
    ),
    'roll_success_generic': (
        'It works, and the moment turns {actor_s} way.',
        '{actor} carries it off.',
        'It goes the way {actor} meant it to.',
    ),
    'roll_failure_generic': (
        'It does not go {actor_s} way, and the moment slips past.',
        'It falls apart in {actor_s} hands.',
        'Despite {actor_s} effort, it does not come off.',
    ),
    'roll_ack': (
        '{actor} commits to the attempt, and everything around them narrows to this one moment, the next breath deciding how cleanly they carry it off.',
        '{actor} sets themself and goes for it, with no way to take it back once they start and the outcome hanging on nerve and timing.',
        '{actor} draws a slow breath and makes their move, knowing the next few heartbeats will show whether they judged this right.',
    ),
    'no_target': ('There is no one here to fight.', '{actor_s} blade finds only air; no enemy stands before them.'),
    'npc_knows': (
        '"{fact}" {first} tells {actor}.',
        '"{fact}" {first} says, voice low.',
        '"{fact}" {first} says after a moment.',
    ),
}
# Party play: the hero's attack right after the sentence that named them closing in.
PARTY_FOLLOW_UP_ATTACK = {
    True: 'Their {weapon} lands with {total} against AC {ac}, a hit for {damage} {damage_type} damage.',
    False: 'Their {weapon} swings with {total} against AC {ac} and misses.',
}
# On a turn whose rolls are shown as cards (``storyRolls``) a rolled result is told without its numbers, three
# ways per outcome. ``{owner}`` is "Your", "Seren Ashgrove's" or "Their"; ``{enemy_s}`` the enemy's possessive;
# ``{ability}`` the last word of the ability's name ("pounce" for "Dust-Marked Pounce"); the target's forms and
# verbs come from ``_person``.
ROLLED_TEMPLATES: dict[str, tuple[str, ...]] = {
    'hero_hit': (
        '{owner} {weapon} bites deep into {target_object}.',
        '{owner} {weapon} finds {target_object} and draws blood.',
        '{owner} {weapon} lands hard on {target_object}.',
    ),
    'hero_miss': (
        '{owner} {weapon} goes wide of {target_object}.',
        '{target} slips past {owner_lower} {weapon}.',
        '{owner} {weapon} cuts only air where {target_lower} stood.',
    ),
    'hero_critical': (
        '{owner} {weapon} finds the gap and tears through {target_object}.',
        'A perfect blow: {owner_lower} {weapon} drives deep into {target_object}.',
        '{owner} {weapon} strikes true, and {target_lower} {reel} from it.',
    ),
    'enemy_hit': (
        "{enemy_s} {ability} bears {target_object} down hard.",
        "{enemy_s} {ability} catches {target_object} and draws blood.",
        "{enemy_s} {ability} lands on {target_object}.",
    ),
    'enemy_miss': (
        "{enemy_s} {ability} goes wide of {target_object}.",
        "{target} {twist} clear of {enemy_s} {ability}.",
        "{enemy_s} {ability} finds only air where {target_lower} stood.",
    ),
    'enemy_critical': (
        "{enemy_s} {ability} tears into {target_object} with terrible force.",
        "{target} {take} the full weight of {enemy_s} {ability}.",
        "{enemy_s} {ability} finds the gap, and {target_lower} {reel}.",
    ),
    'enemy_save_made': (
        "{enemy_s} {ability} slides off {target_object}.",
        "{target} {shake} off {enemy_s} {ability}.",
        "{target} {stand} firm against {enemy_s} {ability}.",
    ),
    'enemy_save_failed': (
        "{enemy_s} {ability} takes hold of {target_object}.",
        "{target} cannot shake off {enemy_s} {ability}.",
        "{enemy_s} {ability} sinks into {target_object}.",
    ),
    'spell_hit': ('{spell} strikes {target_object}.', '{spell} finds {target_object}.', '{spell} slams into {target_object}.'),
    'spell_miss': (
        '{spell} flies wide of {target_object}.',
        '{target} {twist} clear of {spell}.',
        '{spell} spends itself on empty air beside {target_object}.',
    ),
    'spell_critical': (
        '{spell} hits {target_object} dead on.',
        '{spell} tears into {target_object} with full force.',
        '{spell} strikes {target_object} squarely, and {target_lower} {reel}.',
    ),
    'spell_save_made': ('{target} {resist} {spell}.', '{spell} slides off {target_object}.', '{target} {shake} off {spell}.'),
    'spell_save_failed': ('{spell} takes hold of {target_object}.', '{target} cannot resist {spell}.', '{spell} breaks through to {target_object}.'),
    'spell_damage': ('It hurts {target_object}.', '{target} {take} the brunt of it.', 'It leaves {target_object} bloodied.'),
    'healing': ('Strength returns to {target_object}.', 'The wounds close on {target_object}.', 'Some colour returns to {target_object}.'),
    'player_down': ('{target} {be} down.', '{target} {drop} and {be} still.', '{target} {go} down and {stay} down.'),
}
# A bark spoken by an enemy: the verbs rotate.
BARK_VERBS = ('says', 'spits', 'hisses', 'calls')
# The finishing blow, told in the hero's own words or spared; and the moment let pass.
FINISHER_FALLS = '{enemy} falls, and does not rise.'
FINISHER_SPARED = {False: 'You turn the blow. {enemy} lives, and is yours.', True: '{actor} turns the blow. {enemy} lives, and is theirs.'}
FINISHER_LAPSED = '{enemy} falls.'
# Take heart: the hero spends their Inspiration.
TAKE_HEART = {False: 'You take a breath and remember why you came.', True: '{hero} takes a breath and remembers why they came.'}
# A look over the fight: where someone stands from the hero.
TURNS_TO = {False: '{name} turns to you.', True: '{name} turns to {actor}.'}
LEAVES_IT = {False: 'You leave it there with {name}.', True: '{actor} leaves it there with {name}.'}
NOTHING_TO_SAY = '{name} has nothing to say to that.'
SAYS_NO_MORE = '{name} says nothing more about that.'
# What an act did when nobody wrote a line for it, by (act, outcome); a pair of forms is (alone, party play).
REACTION_LINES: dict[tuple[str, str], Any] = {
    ('promise', 'default'): ('{name} takes your word.', "{name} takes {actor_s} word."),
    ('show', 'cares'): '{name} looks at it for a long moment.',
    ('threaten', 'worked'): '{name} gives way.',
    ('threaten', 'failed'): '{name} does not give way.',
    ('offer', 'accepted'): '{name} takes it.',
    ('claim', 'believed'): ('{name} takes you at your word.', '{name} takes {actor} at their word.'),
    ('claim', 'disbelieved'): ('{name} does not believe you.', '{name} does not believe {actor}.'),
    ('apologise', 'forgiven'): '{name} lets it go.',
}
POSITION_WORDS = {'on': 'is on {hero}', 'close': 'is in close', 'near': 'is a few strides off', 'far': 'hangs back', 'distant': 'is far off'}


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
        """The people present; in a place with rooms only those in the hero's own zone (the packet's ``present``)."""

        people = [npc for npc in _dicts(self.live_state.get('activeNpcs')) if _text(npc.get('name'))]
        here = self.facts.get('present')
        return [npc for npc in people if _text(npc.get('name')) in here] if isinstance(here, list) else people

    @property
    def previous_narration(self) -> str:
        turns = _dicts(self.context.get('recent_turns'))
        return _text(turns[-1].get('dm_output')) if turns else ''

    @property
    def party(self) -> dict:
        """``{"actor", "heroes": [{"id", "name"}]}`` when more than one hero is in the session; empty alone."""

        return _dict(self.facts.get('party'))

    @property
    def party_play(self) -> bool:
        return bool(self.party)

    @property
    def actor(self) -> str:
        """The acting hero's name, as party play tells it."""

        return _text(self.party.get('actor')) or _text(self.speaker) or 'The hero'

    def hero_name(self, actor_id: Any) -> str:
        """The name of the hero with this actor id; the acting hero when it is not known."""

        hero = next((hero for hero in _dicts(self.party.get('heroes')) if _text(hero.get('id')) == _text(actor_id)), {})
        return _text(hero.get('name')) or self.actor

    def pick(self, key: str, **values: Any) -> str:
        party = self.party_play and key in PARTY_TEMPLATES
        options = PARTY_TEMPLATES[key] if party else TEMPLATES[key]
        if party:
            actor = values.pop('actor', self.actor)
            values = {'actor': actor, 'actor_s': _possessive(actor), **values}
        digest = hashlib.sha1(f'{self.turn_number}:{key}'.encode('utf-8')).hexdigest()
        return options[int(digest, 16) % len(options)].format(**values)

    @property
    def rolls_shown(self) -> bool:
        """The turn's rolls are shown to the players as cards (``storyRolls``): no sentence restates a number."""

        return rolls_shown(self.rules_hint)

    def pick_rolled(self, key: str, **values: Any) -> str:
        """A rolled result told without its numbers (``ROLLED_TEMPLATES``)."""

        options = ROLLED_TEMPLATES[key]
        digest = hashlib.sha1(f'{self.turn_number}:rolled:{key}'.encode('utf-8')).hexdigest()
        return _sentence(options[int(digest, 16) % len(options)].format(**values))

    def is_speaker(self, name: Any) -> bool:
        speaker = self.actor if self.party_play else _text(self.speaker)
        return bool(speaker) and _text(name).casefold() == speaker.casefold()

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

    def pronoun(self, name: Any) -> dict[str, str]:
        """The enemy's pronoun forms (``it``, ``its``) as the narration packet gives them."""

        text = _text(name).casefold()
        enemy = next((enemy for enemy in _dicts(self.facts.get('enemies')) if _text(enemy.get('name')).casefold() == text), {})
        return pronoun(enemy.get('pronoun'))

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
    unseen = _dict(turn.facts.get('unseen'))
    if _text(unseen.get('line')):
        # Words that name a thing still hidden: nothing is attempted (W9-SCENE amendment 5).
        return _compose(turn, [[_text(unseen['line'])]])
    invalid_actions = _dicts(turn.pipeline.get('invalidActions'))
    if invalid_actions:
        return _compose(turn, [_refusals(invalid_actions)])
    told = _dict(turn.facts.get('finisher'))
    if told:
        # The hero's own words, shown above as their action, are the blow; only its end is told.
        return _compose(turn, [[_finisher_line(turn, told)]])
    # The place first, then the hero's own result, then the fight, then what it brought.
    arrival = _text(turn.facts.get('arrival'))
    blocks: list[list[str]] = [*_director_opening(turn), _captives_left_lines(turn)]
    blocks.extend(_arrival_blocks(turn, {'locationName': _the(arrival)}) if arrival else [])
    moved = _zone_move_blocks(turn)
    blocks.extend(moved)
    if moved and _dict(turn.facts.get('talk')):
        # Stepped next door to talk: the talk follows the arrival in the same reply.
        blocks.append([_talk_line(turn, _dict(turn.facts['talk']))])
    blocks.extend(_conversation_blocks(turn))
    own_blocks, aftermath = _change_blocks(turn)
    blocks.append([*_take_heart_lines(turn), *_spell_lines(turn), *_capability_lines(turn)])
    blocks.extend(own_blocks)
    blocks.extend(_effect_blocks(turn))
    blocks.extend(_fight_start_blocks(turn))
    # A hero's own thread, each its own paragraph: after what was found, or with a fight's setup.
    blocks.extend([_text(thread.get('text'))] for thread in _dicts(turn.facts.get('threads')) if _text(thread.get('text')))
    if isinstance(turn.facts.get('positions'), list):
        # A look over the fight: where everyone stands.
        blocks.append([_positions_line(turn)])
    blocks.append(_combat_lines(turn))
    blocks.append(aftermath)
    roll_paragraphs = _resolved_roll_paragraphs(turn) or _free_roll_paragraphs(turn)
    if roll_paragraphs:
        blocks.extend([[roll_paragraphs[0]], roll_paragraphs[1:]])
    # An object's failing-forward text and a fight's results, told as written (W9-SCENE); after a defeat they follow
    # the fall (see _compose).
    if not _dict(turn.facts.get('partyDefeat')):
        blocks.extend([text] for text in _string_items(_dict(turn.facts.get('sceneTold')).get('closing')))
    if any(blocks) or turn.facts.get('partyDefeat'):
        return _compose(turn, blocks)
    if turn.rules_hint.get('no_target') is True:
        return _compose(turn, [[turn.pick('no_target')]])
    if turn.rules_hint.get('requires_roll') is True and turn.rules_hint.get('roll_value') is None:
        return _narrate_roll_request(turn)
    talk = _dict(turn.facts.get('talk'))
    if talk:
        return _compose(turn, [[_talk_line(turn, talk)]])
    called = _dict(turn.facts.get('callOut'))
    if called:
        return _compose(turn, [[_call_out_line(turn, called)]])
    npc = _addressed_npc(turn)
    if npc:
        return _compose(turn, [[_npc_talk_line(turn, npc)]])
    if _dict(turn.facts.get('freeText')).get('kind') == 'look':
        return _compose(turn, _scene_paragraphs(turn))
    if _next_actor_line(turn) or _lapsed_lines(turn):
        # A turn that ends with nothing else to tell hands straight on to whoever acts next.
        return _compose(turn, [])
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
    if talk.get('captive') is True:
        captive = turn.creature(name) if turn.creature(name) != name else f'the {name.lower()}'
        if not fact:
            return CAPTIVE_SILENT[turn.party_play].format(captive=_capitalized(captive))
        return turn.pick('npc_knows', fact=_speech(fact), first=captive)
    if not fact:
        return f'{name or "They"} has nothing more to add.'
    first = _first_name(name) or name
    return turn.pick('npc_knows', fact=_speech(fact), first=first)


def _director_opening(turn: NarrationInput) -> list[list[str]]:
    """The callback the director chose to open the reply with, word for word."""

    opening = director.told_by_engine(turn.facts, director.OPENING)
    return [[opening]] if opening else []


def _given_to(line: str, first: str) -> str:
    """A line that is all speech, given to its speaker: ``"Say it quickly," Osric says.``; any other line as it is."""

    text = _text(line)
    inner = text[1:-1].strip() if len(text) > 2 and text[0] == text[-1] == '"' and '"' not in text[1:-1] else ''
    if not inner or not first:
        return text
    if inner.endswith('.'):
        inner = f'{inner[:-1]},'
    return f'"{inner}" {first} says.'


def _reaction_line(turn: NarrationInput, name: str, reaction: dict) -> str:
    if _text(reaction.get('line')):
        return _text(reaction['line'])
    forms = REACTION_LINES.get((_text(reaction.get('act')), _text(reaction.get('outcome'))))
    if forms is None:
        return NOTHING_TO_SAY.format(name=name)
    form = forms if isinstance(forms, str) else forms[turn.party_play]
    return form.format(name=name, actor=turn.actor, actor_s=_possessive(turn.actor))


def _conversation_blocks(turn: NarrationInput) -> list[list[str]]:
    """The hero's conversation this turn, as the module docstring tells it."""

    talk = _dict(turn.facts.get('conversation'))
    name = _text(talk.get('name'))
    if not talk or not name:
        return []
    first = _text(talk.get('first')) or name
    lines: list[str] = []
    if talk.get('opened') is True:
        greeting = _text(talk.get('greeting'))
        lines.append(_given_to(greeting, first) if greeting else TURNS_TO[turn.party_play].format(name=name, actor=turn.actor))
    topic = _dict(talk.get('topic'))
    reaction = _dict(talk.get('reaction'))
    if topic.get('knows') is True:
        told = _text(topic.get('line'))
        lines.append(turn.pick('npc_knows', fact=_speech(told), first=first) if told else f'{name} has nothing more to add.')
    elif topic:
        lines.append(_text(topic.get('line')) or SAYS_NO_MORE.format(name=name))
    elif reaction:
        lines.append(_reaction_line(turn, name, reaction))
    elif talk.get('free') is True:
        lines.append(NOTHING_TO_SAY.format(name=name))
    if talk.get('left') is True:
        lines.append(LEAVES_IT[turn.party_play].format(name=name, actor=turn.actor))
    return [[line] for line in lines if line]


def _take_heart_lines(turn: NarrationInput) -> list[str]:
    taken = _dict(turn.facts.get('takeHeart'))
    if not taken:
        return []
    return [TAKE_HEART[turn.party_play].format(hero=_text(taken.get('hero')) or turn.actor)]


def _captives_left_lines(turn: NarrationInput) -> list[str]:
    line = _text(turn.facts.get('captivesLeft'))
    return [line] if line else []


def _finisher_line(turn: NarrationInput, told: dict) -> str:
    """``The hollow marshal falls, and does not rise.``; spared, ``You turn the blow. … lives, and is yours.``"""

    enemy = _capitalized(turn.creature(told.get('enemy')))
    if told.get('spared') is True:
        return FINISHER_SPARED[turn.party_play].format(actor=turn.actor, enemy=enemy)
    return FINISHER_FALLS.format(enemy=enemy)


def _lapsed_lines(turn: NarrationInput) -> list[str]:
    lapsed = _dict(turn.facts.get('finisherLapsed'))
    return [FINISHER_LAPSED.format(enemy=_capitalized(turn.creature(lapsed.get('enemy'))))] if lapsed else []


def _positions_line(turn: NarrationInput) -> str:
    """``The ashmarked cutthroat is on you, unhurt; Corin Ashby is a few strides off.``"""

    hero = turn.actor if turn.party_play else 'you'
    parts = [
        f"{_text(entry.get('name'))} {POSITION_WORDS.get(_key(entry.get('where')), POSITION_WORDS['near']).format(hero=hero)}"
        + (f", {_text(entry.get('health'))}" if _text(entry.get('health')) else '')
        for entry in _dicts(turn.facts.get('positions'))
        if _text(entry.get('name'))
    ]
    if not parts:
        return 'Nobody else is standing.'
    return _sentence('; '.join(parts))


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
    running = [
        _sentence(f"{_text(hero.get('name')) if turn.party_play else 'You'} {'comes' if turn.party_play else 'come'} running from {_text(hero.get('from'))}")
        for hero in _dicts(started.get('cameRunning'))
        if _text(hero.get('from'))
    ]
    blocks.append(_safe_sentences([*([_sentence(_text(started['positions']))] if _text(started.get('positions')) else []), *running]))
    blocks.append(_bark_lines(turn, 'start'))
    return [block for block in blocks if block]


def _bark_lines(turn: NarrationInput, after: str) -> list[str]:
    """The barks told at one place in the turn: ``start``, ``hero`` or after an enemy's turn (its id)."""

    return [_bark_line(turn, bark) for bark in _dicts(turn.facts.get('barks')) if _text(bark.get('after')) == after]


def _bark_line(turn: NarrationInput, bark: dict) -> str:
    """Speech with its speaker (``"Not paid enough for this," the ashmarked cutthroat spits.``); a tell as written."""

    line = _text(bark.get('line'))
    if bark.get('speech') is not True:
        return _sentence(line)
    words = line.strip('"“”').strip()
    if words.endswith('.') and not words.endswith('…'):
        words = f'{words[:-1]},'
    digest = hashlib.sha1(f'{turn.turn_number}:bark:{line}'.encode('utf-8')).hexdigest()
    return f'"{words}" {turn.creature(bark.get("name"))} {BARK_VERBS[int(digest, 16) % len(BARK_VERBS)]}.'


def _combat_lines(turn: NarrationInput) -> list[str]:
    """The fight in order: it starts, the hero's move or attack, the enemies' turns, who fell, death saves, its end."""

    combat_state = _dict(turn.pipeline.get('combatState'))
    lines = [turn.pick('combat_start') for change in turn.applied_changes if _text(change.get('type')) == 'combat.start'][:1]
    targets: list[str] = []
    tactical_action = _dict(combat_state.get('playerResolvedCombatAction'))
    if _key(tactical_action.get('actionType')) in {*MANEUVER_TYPES, IMPROVISE_TYPE}:
        lines.extend(_maneuver_lines(turn, tactical_action))
    elif tactical := _tactical_line(turn, tactical_action):
        lines.append(tactical)
    player_action = _dict(combat_state.get('playerResolvedAction'))
    attacks = [player_action] if player_action else []
    for event in _dicts(combat_state.get('trustedDamageEvents')):
        if event not in attacks:
            attacks.append(event)
    # A rule that moved the hero's roll is named once, on the attack it first changed.
    modifier_clause = _modifier_clause(turn, turn.facts.get('attackModifiers')) if player_action else ''
    closed_on = _closed_on(turn, tactical_action)
    for attack in attacks:
        follows_up = bool(closed_on) and attack is player_action and _text(attack.get('targetName')).casefold() == closed_on
        line = _player_attack_line(turn, attack, follows_up=follows_up)
        if modifier_clause and attack is player_action:
            line = f'{line[:-1]} — {modifier_clause}.'
        lines.append(line)
        targets.append(_text(attack.get('targetName')))
    lines.extend(_bark_lines(turn, 'hero'))
    for entry in _dicts(turn.facts.get('shakenOff')):
        person = _person(turn, entry.get('name'))
        lines.append(_sentence(f"{person['target']} {person['shake']} off {_text(entry.get('source'))}"))
    enemy_actions = _dicts(combat_state.get('enemyResolvedActions'))
    fleeing = [action for action in enemy_actions if _fled(action)]
    routed = _key(turn.facts.get('fightEnds')) == 'enemies_fled'
    untold = [bark for bark in _dicts(turn.facts.get('barks')) if _text(bark.get('after')) not in {'start', 'hero'}]
    for action in enemy_actions:
        lines.extend(_breaking_away_lines(turn, action))
        barks = [bark for bark in untold if _text(bark.get('after')) == _text(action.get('enemyId'))]
        untold = [bark for bark in untold if bark not in barks]
        # A taunt replaces the line of a turn that only filled time.
        taunt = next((bark for bark in barks if bark.get('kind') == 'taunt'), None) if filler_enemy_turn(action) else None
        if routed and len(fleeing) > 1 and _fled(action):
            # The last of several enemies breaking at once is told once.
            line = 'The last of them break and run.' if action is fleeing[0] else None
        elif taunt:
            line = _bark_line(turn, taunt)
        else:
            line = _enemy_action_line(turn, action, routed=routed)
            if line and action.get('moveOnly') is not True and (moving := _enemy_movement_line(turn, action)):
                lines.append(moving)
        if line:
            lines.append(line)
        if action.get('wrested') is True or (not _text(action.get('goal')) and _text(action.get('stakeLine'))):
            lines.append(_text(action['stakeLine']))
        lines.extend(_bark_line(turn, bark) for bark in barks if bark is not taunt)
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


def _closes_in(action: dict) -> bool:
    movement = _dict(action.get('movement'))
    return _key(action.get('actionType')) == 'advance' and _key(movement.get('toRangeBand')) == 'melee' and bool(movement.get('engagedWith'))


def _closed_on(turn: NarrationInput, action: dict) -> str:
    """In party play, the (casefolded) target the acting hero's sentence just named them closing in on."""

    actor = _text(action.get('actorName'))
    if not turn.party_play or not _closes_in(action) or (actor and not turn.is_speaker(actor)):
        return ''
    return _text(action.get('targetName')).casefold()


def _tactical_line(turn: NarrationInput, action: dict) -> str:
    """The hero's move or tactic; closing into melee, or moving out toward an enemy standing off, is told in the
    same voice as the attack after it."""

    toward = bool(_dict(action.get('movement')).get('towardId'))
    if _closes_in(action) or toward:
        actor = _text(action.get('actorName'))
        verb = 'move' if toward else 'close'
        if not actor or turn.is_speaker(actor):
            who = f'{turn.actor} {verb}s' if turn.party_play else f'You {verb}'
        else:
            who = f'{actor} {verb}s'
        return _sentence(f"{who} {'out toward' if toward else 'in on'} {_target_ref(turn, action.get('targetName'))}")
    if _key(action.get('actionType')) == 'plan':
        # A plan in the fight is told with the turn's other plans (``effects``).
        return ''
    return _sentence(_text(action.get('outcome')))


def _player_attack_line(turn: NarrationInput, attack: dict, *, follows_up: bool = False) -> str:
    """The hero's attack; in party play right after they were named closing in, ``Their mace lands …``."""

    actor_name = _text(attack.get('sourceActorName')) or (turn.actor if turn.party_play else '')
    is_you = not turn.party_play and (not actor_name or turn.is_speaker(actor_name))
    weapon = _text(attack.get('weaponName')) or 'weapon'
    if turn.rolls_shown:
        owner = 'Their' if follows_up else 'Your' if is_you else _possessive(actor_name)
        outcome = ('critical' if attack.get('critical') is True else 'hit') if attack.get('hit') is True else 'miss'
        return turn.pick_rolled(
            f'hero_{outcome}',
            owner=owner,
            owner_lower=owner.lower() if owner in {'Your', 'Their'} else owner,
            weapon=weapon.lower(),
            **_person(turn, attack.get('targetName')),
        )
    values = {
        'actor': 'Your' if is_you else f'{actor_name}\'s',
        'actor_object': 'your' if is_you else f"{actor_name}'s",
        'weapon': weapon.lower() if turn.party_play else weapon,
        'target': _target_ref(turn, attack.get('targetName')),
        'total': _number(attack.get('attackTotal')),
        'ac': _number(attack.get('targetArmorClass')),
        'damage': _number(attack.get('damageTotal')),
        'damage_type': _text(attack.get('damageType')) or 'weapon',
    }
    if follows_up:
        return _sentence(PARTY_FOLLOW_UP_ATTACK[attack.get('hit') is True].format(**values))
    key = 'player_attack_hit' if attack.get('hit') is True else 'player_attack_miss'
    return _sentence(turn.pick(key, **values))


def _modifier_clause(turn: NarrationInput, modifiers: Any) -> str:
    """``your wounds cost you 2`` (``their wounds cost them 2`` in party play) for the hero's roll modifiers
    (``[{"label", "value"}]``); with the roll's card showing the numbers, ``your wounds drag at you``."""

    parts = []
    for modifier in _dicts(modifiers):
        label, value = _text(modifier.get('label')), _int_or_none(modifier.get('value'))
        if not label or not value:
            continue
        if label == 'Wounded' and value < 0 and turn.rolls_shown:
            parts.append('their wounds drag at them' if turn.party_play else 'your wounds drag at you')
        elif label == 'Wounded' and value < 0:
            parts.append(f'their wounds cost them {-value}' if turn.party_play else f'your wounds cost you {-value}')
        elif turn.rolls_shown:
            parts.append(label.lower())
        else:
            parts.append(f'{label.lower()} {"+" if value > 0 else "−"}{abs(value)}')
    return _join_words(parts)


def _hero_says(turn: NarrationInput, *phrases: str, pronoun: bool = False) -> str:
    """The acting hero doing each phrase in turn: ``You grab for it, but …`` alone; in party play by name,
    or ``They …`` right after the sentence that named them."""

    if not turn.party_play:
        return f"You {' '.join(phrases)}"
    if pronoun:
        return f"They {' '.join(phrases)}"
    first, *rest = phrases
    return ' '.join([turn.actor, _third_person(first), *(_and_third_person(phrase) for phrase in rest)])


def _and_third_person(phrase: str) -> str:
    """``and bear it down`` -> ``and bears it down``."""

    lead, _space, rest = phrase.partition(' ')
    return f'{lead} {_third_person(rest)}' if lead in {'and', 'but'} and rest else phrase


def _skill_label(check: dict) -> str:
    return ' '.join(word if word == 'of' else word.capitalize() for word in _key(check.get('skill')).split('_')) or 'Check'


def _maneuver_lines(turn: NarrationInput, action: dict) -> list[str]:
    """A fight maneuver or improvised action in the hero's voice, after closing in when it did."""

    lines = []
    closed = _closes_in({**action, 'actionType': 'advance'})
    if closed:
        lines.append(_sentence(f"{turn.actor if turn.party_play else 'You'} {'closes' if turn.party_play else 'close'} in on {_target_ref(turn, action.get('targetName'))}"))
    check = _dict(action.get('check'))
    roll = f"{_skill_label(check)} {_number(check.get('total'))} against DC {_number(check.get('dc'))}" if check and not turn.rolls_shown else ''

    def rolled(text: str) -> str:
        return f'{text}: {roll}' if roll else text

    target = _target_ref(turn, action.get('targetName'))
    words = turn.pronoun(action.get('targetName'))
    it, its, them, s = words['subject'], words['possessive'], words['object'], words['s']
    success = action.get('success') is True
    yours = 'theirs' if turn.party_play else 'yours'
    hero_poss = 'their' if turn.party_play else 'your'
    kind = _key(action.get('actionType'))
    if kind == 'grapple' and success:
        lines.append(f"{rolled(_hero_says(turn, f'get an arm around {target}', f'and bear {them} down', pronoun=closed))}. {it.capitalize()} {words['is']} going nowhere.")
    elif kind == 'grapple':
        lines.append(f"{rolled(_hero_says(turn, f'grab for {target},', f'but {it} twist{s} free', pronoun=closed))}.")
    elif kind == 'shove' and success:
        goes = 'goes' if s else 'go'
        lines.append(f"{rolled(_hero_says(turn, f'put {hero_poss} shoulder into {target}', f'and {it} {goes} down hard', pronoun=closed))}.")
    elif kind == 'shove':
        lines.append(f"{rolled(_hero_says(turn, f'put {hero_poss} shoulder into {target},', f'but {it} keep{s} {its} feet', pronoun=closed))}.")
    elif kind == 'subdue' and success:
        tied = _hero_says(turn, f'wrench {its} arms back', 'and tie them', pronoun=closed)
        lines.append(f"{rolled(tied)}. {_capitalized(target)} is {yours}.")
    elif kind == 'subdue':
        lines.append(f"{rolled(_hero_says(turn, f'try to bind {target},', f'but {it} fight{s} free of the rope', pronoun=closed))}.")
    elif kind == 'demand_surrender':
        speaker = ('they' if closed else turn.actor) if turn.party_play else 'you'
        tells = 'tell' if speaker in {'you', 'they'} else 'tells'
        weapon = _text(action.get('targetWeapon'))
        ending = (f'The {weapon} drops.' if weapon else f'{it.capitalize()} give{s} up.') if success else f'{it.capitalize()} fight{s} on.'
        check_line = '' if turn.rolls_shown else f' Intimidation {_number(check.get("total"))} against DC {_number(check.get("dc"))}.'
        lines.append(f'"Enough," {speaker} {tells} {them}.{check_line} {ending}')
    else:
        attempt = _attempt_phrase(turn, _text(action.get('attempt')))
        if not success:
            # A failed attempt never says it was done: "You try to kick the lantern at it".
            attempt = f'try to {attempt}' if attempt else ''
        tried = _hero_says(turn, attempt, pronoun=closed) if attempt else _hero_says(turn, 'try something', pronoun=closed)
        if success and _text(action.get('targetName')):
            lines.append(f'{rolled(tried)}. {_capitalized(target)} is off balance.')
        elif success:
            lines.append(f'{rolled(tried)}, and it works.')
        else:
            lines.append(f"{tried}, but it does not come off{': ' + roll if roll else ''}.")
    return lines


def _attempt_phrase(turn: NarrationInput, attempt: str) -> str:
    """The typed words of an improvised action as the hero's verb phrase: "I kick the lantern at it" ->
    ``kick the lantern at it``, with "my" as "your" (``their`` in party play)."""

    words = attempt.strip().rstrip('.!').split()
    if words and words[0].lower() == 'i':
        words = words[1:]
    if not words:
        return ''
    owner = 'their' if turn.party_play else 'your'
    words = [owner if word.lower() == 'my' else 'you' if word.lower() == 'me' and not turn.party_play else 'them' if word.lower() == 'me' else word for word in words]
    return ' '.join([words[0].lower(), *words[1:]])


def _breaking_away_lines(turn: NarrationInput, action: dict) -> list[str]:
    """Opportunity attacks on an enemy that breaks away from melee; a hold that ended without a fight."""

    enemy = turn.creature(action.get('enemyName')) or 'the enemy'
    them = turn.pronoun(action.get('enemyName'))['object']
    lines = [_sentence(f'{enemy} is free of the hold')] if action.get('grappleEnded') is True else []
    for reaction in _dicts(action.get('reactions')):
        attack = _dict(reaction.get('attack'))
        if _key(reaction.get('kind')) != 'opportunity_attack' or attack.get('attackTotal') is None:
            continue
        attacker = _text(attack.get('attackerName') or reaction.get('reactorName'))
        weapon = (_text(attack.get('weaponName')) or 'weapon').lower()
        owner = 'your' if turn.is_speaker(attacker) and not turn.party_play else _possessive(attacker)
        roll = f"{_number(attack.get('attackTotal'))} against AC {_number(attack.get('targetArmorClass'))}"
        if turn.rolls_shown:
            outcome = f'catches {them}' if attack.get('hit') is True else f'swings after {them} and misses'
            lines.append(f'As {enemy} breaks away, {owner} {weapon} {outcome}.')
        elif attack.get('hit') is True:
            lines.append(f"As {enemy} breaks away, {owner} {weapon} catches {them}: {roll}, {_number(attack.get('damageTotal'))} damage.")
        else:
            lines.append(f'As {enemy} breaks away, {owner} {weapon} swings after {them} and misses: {roll}.')
    return lines


def _fled(action: dict) -> bool:
    return (
        action.get('resolvedWithoutRoll') is True
        and _key(action.get('intentType')) in FLEEING_INTENTS
        and action.get('moveOnly') is not True
        and action.get('droppedByReaction') is not True
    )


def _enemy_movement_line(turn: NarrationInput, action: dict) -> str:
    """An enemy coming in, in distance words (a band is about thirty feet): ``The wardbound beast crosses about
    thirty feet to you.``; ``The hollow marshal comes about thirty feet closer.``; '' when it did not come in."""

    steps = _dicts(action.get('movement'))
    if not steps or _key(action.get('intentType')) in FLEEING_INTENTS or _text(action.get('goal')):
        return ''
    enemy = _capitalized(turn.creature(action.get('enemyName')) or 'the enemy')
    feet = ('thirty', 'sixty', 'ninety')[min(len(steps), 3) - 1]
    last = steps[-1]
    if _key(last.get('toRangeBand')) == 'melee' and last.get('engageWith'):
        return f"{enemy} crosses about {feet} feet to {_person(turn, action.get('targetName'))['target_object']}."
    return f'{enemy} comes about {feet} feet closer.'


def _unrolled_enemy_line(turn: NarrationInput, action: dict, enemy: str, *, routed: bool) -> str:
    """What an enemy turn that rolled nothing did, in the world's words."""

    intent = _key(action.get('intentType'))
    if action.get('turnLost') is True:
        return _sentence(f'{enemy} cannot act')
    if action.get('held') is True and intent == 'surrender':
        words = turn.pronoun(action.get('enemyName'))
        return f'{_capitalized(enemy)} goes slack. "Enough," {words["subject"]} say{words["s"]}.'
    if action.get('droppedByReaction') is True:
        words = turn.pronoun(action.get('enemyName'))
        return _sentence(f'{enemy} falls where {words["subject"]} stand{words["s"]}')
    if action.get('moveOnly') is True:
        return _enemy_movement_line(turn, action) or _sentence(f'{enemy} closes in')
    if intent in FLEEING_INTENTS:
        return _sentence(f'{enemy} breaks and runs' if routed else f'{enemy} gives ground')
    template = ENEMY_TURN_LINES.get(intent, '{enemy} waits.')
    its = 'their' if turn.is_proper(action.get('enemyName')) else 'its'
    return _sentence(template.format(enemy=enemy, ally=turn.creature(action.get('targetName')) or 'an ally', its=its))


def _enemy_action_line(turn: NarrationInput, action: dict, *, routed: bool = False) -> str | None:
    if _text(action.get('goal')) and _text(action.get('stakeLine')):
        # What the fight is about (W9-SCENE): the runner's or the silencer's move, as the engine resolved it.
        return _text(action['stakeLine'])
    enemy = turn.creature(action.get('enemyName')) or 'The enemy'
    target = _target_ref(turn, action.get('targetName'))
    ability = _text(action.get('abilityName')) or 'an attack'
    escape = _dict(action.get('breakFree'))
    if escape:
        grappler = _text(action.get('grapplerName'))
        holder = 'you' if turn.is_speaker(grappler) and not turn.party_play else grappler
        roll = '' if turn.rolls_shown else f": {_number(escape.get('total'))} against DC {_number(escape.get('dc'))}"
        if escape.get('success') is True:
            return f"{_capitalized(enemy)} tears loose of {holder}{roll}."
        hold = 'your hold' if holder == 'you' else f"{_possessive(holder)} hold"
        return f"{_capitalized(enemy)} strains against {hold} and cannot break it{roll}."
    if action.get('resolvedWithoutRoll') is True:
        return _unrolled_enemy_line(turn, action, enemy, routed=routed)
    if turn.rolls_shown:
        return _rolled_enemy_line(turn, action, enemy)
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


def _ability_noun(name: Any) -> str:
    """``pounce`` for "Dust-Marked Pounce", ``lure`` for "Lantern-Lure"."""

    words = re.split(r'[\s-]+', _text(name).lower())
    return words[-1] if words and words[-1] else 'attack'


def _rolled_enemy_line(turn: NarrationInput, action: dict, enemy: str) -> str | None:
    """An enemy's rolled attack or forced save told without its numbers."""

    values = {'enemy_s': _possessive(enemy), 'ability': _ability_noun(action.get('abilityName')), **_person(turn, action.get('targetName'))}
    if action.get('saveDC') is not None and action.get('attackTotal') is None:
        return turn.pick_rolled('enemy_save_made' if action.get('saveSucceeded') is True else 'enemy_save_failed', **values)
    if action.get('attackTotal') is None:
        return None
    outcome = ('critical' if action.get('critical') is True else 'hit') if action.get('hit') is True else 'miss'
    return turn.pick_rolled(f'enemy_{outcome}', **values)


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
    # The enemy whose finishing blow the hero will tell does not die in this turn's words.
    finishing = _text(_dict(turn.facts.get('finishingBlow')).get('enemyId'))
    lines = []
    for participant in participants:
        name = _text(participant.get('name'))
        if not name or name.casefold() not in wanted or (finishing and _text(participant.get('id')) == finishing):
            continue
        hp = _dict(participant.get('hp'))
        current = hp.get('current')
        down = participant.get('isAlive') is False or (isinstance(current, (int, float)) and current <= 0)
        if not down:
            continue
        if _key(participant.get('team')) == 'enemy':
            lines.append(_sentence(turn.pick('defeated', target=turn.creature(name))))
        elif turn.rolls_shown:
            lines.append(turn.pick_rolled('player_down', **_person(turn, name)))
        else:
            lines.append(_sentence(turn.pick('player_down', **_person(turn, name))))
    return lines


def _death_save_line(turn: NarrationInput, change: dict) -> str:
    name = _text(change.get('participantName') or change.get('targetName')) or turn.speaker
    roll = change.get('roll')
    result = _humanize(change.get('result'))
    detail = ''
    if isinstance(roll, int) and result and not turn.rolls_shown:
        detail = f': {roll}, {result}'
    elif result:
        detail = f': {result}'
    return _sentence(turn.pick('death_save', **_person(turn, name), detail=detail))


def _spell_lines(turn: NarrationInput) -> list[str]:
    """Each spell cast: ``You speak Healing Word.`` for healing, ``You cast Shield on yourself.`` for a spell
    only on its caster, else a cast line; then what it did to each target."""

    lines: list[str] = []
    for change in turn.applied_changes:
        if _text(change.get('type')) != 'spell.cast':
            continue
        resolution = _dict(change.get('spellResolution'))
        if not resolution:
            continue
        spell = _text(resolution.get('spellName') or change.get('spellName')) or 'the spell'
        targets = _dicts(resolution.get('targets'))
        healing = any(_key(effect.get('kind')) == 'healing' for target in targets for effect in _dicts(target.get('effects')))
        on_self = bool(targets) and all(turn.is_speaker(target.get('targetName')) for target in targets)
        if healing:
            lines.append(_hero_does(turn, f'speak {spell}'))
        elif on_self:
            lines.append(_hero_does(turn, f"cast {spell} on {'themself' if turn.party_play else 'yourself'}"))
        else:
            lines.append(turn.pick('spell_cast', spell=spell))
        for target in targets:
            lines.extend(_spell_target_lines(turn, spell, target, announced=healing or on_self))
        if _dict(resolution.get('concentration')).get('started') is True:
            lines.append(turn.pick('concentration', spell=spell))
    return lines


def _spell_target_lines(turn: NarrationInput, spell: str, target: dict, *, announced: bool) -> list[str]:
    """What the spell did to one target; the caster, named in the cast line, is "you" or "they" here.
    An ``announced`` spell's cast line already says whom it reached."""

    person = _person(turn, target.get('targetName'), pronoun=True)
    delivery = _dict(target.get('delivery'))
    delivery_type = _key(delivery.get('type'))
    lines = []
    if turn.rolls_shown and delivery_type == 'attack':
        outcome = ('critical' if delivery.get('critical') is True else 'hit') if delivery.get('delivered') is True else 'miss'
        lines.append(turn.pick_rolled(f'spell_{outcome}', **person, spell=spell))
    elif turn.rolls_shown and delivery_type == 'save':
        lines.append(turn.pick_rolled('spell_save_made' if delivery.get('saveSucceeded') is True else 'spell_save_failed', **person, spell=spell))
    elif delivery_type == 'attack':
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
    elif not announced:
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
    if turn.rolls_shown and kind in {'damage', 'healing'}:
        return turn.pick_rolled('spell_damage' if kind == 'damage' else 'healing', **person)
    if kind == 'damage':
        return turn.pick('spell_damage', **person, amount=amount, damage_type=_text(effect.get('damageType')) or 'magical')
    if kind == 'healing':
        return f"{amount} hit points return to {person['target_object']}."
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
        name = _text(change.get('capabilityName')) or ('their training' if turn.party_play else 'your training')
        effect = _humanize(change.get('effectType'))
        amount = change.get('amount')
        target = _text(change.get('targetName'))
        to_target = f' to {target}' if target and not turn.is_speaker(target) else ''
        if isinstance(amount, int) and amount > 0 and 'heal' in effect and turn.rolls_shown:
            lines.append(turn.pick('capability_heal_rolled', name=name, to_target=to_target))
        elif isinstance(amount, int) and amount > 0 and 'heal' in effect:
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
        blocks.append([_hero_does(turn, f'take {_join_words(taken)}')])
    rest = _dict(facts.get('rest'))
    if rest:
        healed = isinstance(rest.get('hp'), int) and rest['hp'] > 0 and not turn.rolls_shown
        blocks.append([turn.pick('rest_hp', amount=rest['hp']) if healed else turn.pick('rest_plain')])
    object_facts = _dicts(facts.get('objects'))
    for change in turn.applied_changes:
        change_type = _text(change.get('type'))
        item = _text(change.get('itemName'))
        amount = change.get('amount')
        quest = _text(change.get('questTitle'))
        # In party play a change to a hero's own things names that hero.
        hero = {'actor': turn.hero_name(change.get('actorId'))} if turn.party_play else {}
        if change_type == 'scene.move_location':
            if not _text(facts.get('arrival')):
                blocks.extend(_arrival_blocks(turn, change))
        elif change_type == 'scene.interactable.action':
            fact = next(
                (fact for fact in object_facts if _text(fact.get('targetId')) == _text(change.get('targetId'))),
                None,
            )
            blocks.extend(_object_blocks(turn, fact) if fact else [[_object_sentence(turn, change)]])
        elif change_type == 'inventory.add' and item:
            if isinstance(amount, int) and amount > 1:
                lines.append(turn.pick('item_gain_many', amount=amount, item=item, **hero))
            else:
                lines.append(_sentence(turn.pick('item_gain_one', item=item, **hero)))
        elif change_type == 'inventory.remove' and item:
            lines.append(_sentence(turn.pick('item_lose', item=item, **hero)))
        elif change_type == 'inventory.mark_used' and item:
            lines.append(turn.pick('item_use', item=item, **hero))
        elif change_type == 'inventory.equip' and item:
            lines.append(turn.pick('item_equip', item=item, **hero))
        elif change_type == 'inventory.unequip' and item:
            lines.append(turn.pick('item_unequip', item=item, **hero))
        elif change_type == 'scene.item.add' and item:
            lines.append(_sentence(turn.pick('scene_item', item=item)))
        elif change_type == 'rest.complete' and not rest:
            if isinstance(amount, int) and amount > 0 and not turn.rolls_shown:
                lines.append(turn.pick('rest_hp', amount=amount))
            else:
                lines.append(turn.pick('rest_plain'))
        # Objectives, quest completion and XP are bookkeeping: the turn's story beats
        # report them ("Objective complete — …", "+150 XP"), so the prose does not.
        elif change_type == 'quest.fail' and quest:
            lines.append(_sentence(turn.pick('quest_fail', quest=quest)))
        elif change_type in {'currency.add', 'currency.remove'} and isinstance(amount, int) and amount > 0:
            currency = CURRENCY_NAMES.get(_key(change.get('currency')), _text(change.get('currency')) or 'coin')
            key = 'currency_gain' if change_type == 'currency.add' else 'currency_spend'
            lines.append(turn.pick(key, amount=amount, currency=currency, **hero))
        elif change_type == 'spell.cast' and not change.get('spellResolution') and change.get('spellName'):
            lines.append(turn.pick('spell_cast', spell=_text(change.get('spellName'))))
    return [_safe_sentences(block) for block in blocks], _safe_sentences(lines)


def _effect_blocks(turn: NarrationInput) -> list[list[str]]:
    """Each plan the hero resolved (W9-SCENE): its authored text as written, else what the hero did and what it
    changed (``You shut and bar the yard gate. The yard gate is barred.``), or that it did not work."""

    from aidm_server.game_state.orchestration.scene_plans import scripted_effect_lines

    def does(phrase: str) -> str:
        first, joined, rest = phrase.partition(' and ')
        if turn.party_play and joined and rest[:1].islower():
            # "shut and bar the gate": both verbs follow the hero's name.
            return _sentence(f'{turn.actor} {_third_person(first)} and {_third_person(rest)}')
        return _hero_does(turn, phrase)

    return [_safe_sentences(scripted_effect_lines(effect, hero_does=does)) for effect in _dicts(turn.facts.get('effects'))]


def _zone_move_blocks(turn: NarrationInput) -> list[list[str]]:
    """A step into another room or area of the place: ``You step into the common room.``, the zone's description the
    first time, then who is there (``Mara Reedwake is here.``)."""

    moved = _dict(turn.facts.get('zoneMove'))
    if not moved:
        return []
    present = _string_items(moved.get('present'))
    lines = [
        _hero_does(turn, f"step into {_text(moved.get('phrase')) or 'the next room'}"),
        *_split_sentences(_text(moved.get('description'))),
        *([f"{_join_words(present)} {'is' if len(present) == 1 else 'are'} here."] if present else []),
    ]
    return [_safe_sentences(lines)]


def _call_out_line(turn: NarrationInput, called: dict) -> str:
    """The one line a person next door calls back, no more: ``"Come through if there is more to say," Mara
    Reedwake calls from the common room.``"""

    return f'"Come through if there is more to say," {_text(called.get("name"))} calls from {_text(called.get("zone"))}.'


def _arrival_blocks(turn: NarrationInput, change: dict) -> list[list[str]]:
    scene = turn.scene
    place = _text(change.get('locationName')) or _text(scene.get('name')) or 'a new place'
    description = _text(scene.get('summary')) or _text(scene.get('description'))
    lines = [turn.pick('travel', place=place), *_split_sentences(description)[:2]]
    npcs = [_text(npc.get('name')) for npc in turn.present_npcs]
    if npcs:
        lines.append(turn.pick('travel_present', npcs=_join_words(npcs), verb='is' if len(npcs) == 1 else 'are'))
    return [lines]


def _object_blocks(turn: NarrationInput, fact: dict) -> list[list[str]]:
    """What one object action did: its steps, its sentence, the description told once, what a search found."""

    name = _text(fact.get('name'))
    action = _key(fact.get('action'))
    opening = [_text(fact.get('takeTimeLine')) or TAKE_TIME_LINE] if fact.get('takeTime') is True else []
    steps = _dicts(fact.get('steps'))
    if steps:
        opening.append(_steps_sentence(turn, steps, _text(fact.get('keyName'))))
    failed = fact.get('failed') is True
    label_sentence = _label_sentence(turn, _text(fact.get('label')), fact.get('labelAuthored') is True, action, name, failed=failed)
    # Authored action text tells what the action achieved, so a failed attempt never prints it.
    opening.append(label_sentence if failed else _text(fact.get('actionText')) or label_sentence)
    if failed:
        failed_line = _hero_does(turn, FAILED_CHECK_LINES[action]) if action in FAILED_CHECK_LINES else 'It will not give.'
        if _text(fact.get('onFailText')):
            # Failing forward: the attempt alone; the authored failure follows as written (``sceneTold``).
            return [_safe_sentences([_sentence(line) for line in opening])]
        return [_safe_sentences([*(_sentence(line) for line in opening), failed_line])]
    blocks = [_safe_sentences([_sentence(line) for line in opening])]
    description = _text(fact.get('description'))
    if description:
        blocks.append(_split_sentences(description))
    if action == 'search':
        blocks.append(
            _found_sentences(
                turn, fact.get('found'), fact.get('uncovered'), discovered=fact.get('discovered') is True, taken=fact.get('taken')
            )
        )
    elif fact.get('nothingNew') is True:
        blocks.append([_hero_does(turn, f'find nothing new on {_the(name)}')])
    elif fact.get('taken'):
        blocks.append(_found_sentences(turn, [], [], taken=fact.get('taken')))
    return blocks


def _label_sentence(turn: NarrationInput, label: str, authored: bool, action: str, name: str, *, failed: bool = False) -> str:
    """``Ring the wagon bell`` -> ``You ring the wagon bell.``; a default label names the object with an article.
    A failed attempt never says it was done: ``You try to break the strongbox open.``"""

    attempt = 'try to ' if failed else ''
    if authored and label:
        return _hero_does(turn, f'{attempt}{label[:1].lower()}{label[1:]}')
    verb = label.split(' ', 1)[0].lower() if label else action
    return _hero_does(turn, f'{attempt}{verb} {_the(name)}')


def _steps_sentence(turn: NarrationInput, steps: list[dict], key_name: str) -> str:
    """``You unlock the strongbox with the Caravan Strongbox Key and lift the lid.``"""

    first = steps[0]
    action = _key(first.get('action'))
    label = _text(first.get('label'))
    phrase = f'{label[:1].lower()}{label[1:]}' if label else f'{action} it'
    if action == 'unlock' and key_name:
        phrase += f' with {_the(key_name)}'
    if action == 'unlock' and any(_key(step.get('action')) == 'open' for step in steps[1:]):
        phrase += ' and lifts the lid' if turn.party_play else ' and lift the lid'
    return _hero_does(turn, phrase)


def _coins_first(items: list[str]) -> str:
    coins = [item for item in items if item.split()[-1].lower() in COIN_CODES]
    return _join_words([*coins, *(item for item in items if item not in coins)])


def _found_sentences(turn: NarrationInput, found: Any, uncovered: Any, *, discovered: bool = False, taken: Any = None) -> list[str]:
    """What a search turned up: the items found and taken there and then (``You find and take 6 gp and a
    Black-Feathered Arrow.``), those left lying, the hidden objects uncovered, or nothing more (unless it
    ``discovered`` a clue, a place or a story flag, which the search's own words tell)."""

    kept = [_text(item) for item in (taken if isinstance(taken, list) else []) if _text(item)]
    items = [_text(item) for item in (found if isinstance(found, list) else []) if _text(item)]
    objects = [_text(name) for name in (uncovered if isinstance(uncovered, list) else []) if _text(name)]
    sentences = []
    if kept:
        sentences.append(_sentence(_hero_says(turn, 'find', f'and take {_coins_first(kept)}')))
    if items:
        sentences.append(_hero_does(turn, f'find {_coins_first(items)}'))
    if objects:
        sentences.append(_hero_does(turn, f'uncover {_join_words([_the(name) for name in objects])}'))
    return sentences or ([] if discovered else [_hero_does(turn, 'find nothing more')])


def _object_sentence(turn: NarrationInput, change: dict) -> str:
    """Without the pipeline's object record: one plain sentence about the action."""

    name = _text(change.get('targetName')) or _humanize(change.get('targetId')) or 'it'
    action = _key(change.get('objectAction')) or 'work at'
    failed = _text(change.get('eventType') or _dict(change.get('event')).get('type')).endswith('.check_failed')
    return _hero_does(turn, f"{'try to ' if failed else ''}{_humanize(action)} {_the(name)}")


def _the(name: str) -> str:
    """``the Driver's Bench``; a name that already starts with "The" keeps it, lower-cased."""

    if not name:
        return 'it'
    if name.lower().startswith('the '):
        return f'the {name[4:]}'
    return f'the {name}'


def _free_roll_paragraphs(turn: NarrationInput) -> list[str]:
    """``You roll a 10.`` (``You roll a 13 for Strength.``) for a roll made with no check pending; with its card
    showing the number, ``You roll the die.`` (``You roll for Strength.``)."""

    roll = _dict(turn.rules_hint.get('authoritative_roll'))
    total = roll.get('total')
    if roll.get('free') is not True or not isinstance(total, int):
        return []
    ability = _humanize(_dict(roll.get('ability')).get('key')).title()
    if turn.rolls_shown:
        # The roll's card shows the number.
        return [_hero_does(turn, f"roll for {ability}" if ability else 'roll the die')]
    article = 'an' if str(total).startswith('8') or total in (11, 18) else 'a'
    return [_hero_does(turn, f"roll {article} {total}{f' for {ability}' if ability else ''}")]


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
    improvised = _dict(turn.facts.get('improvised'))
    if improvised:
        # An improvised attempt is told, never invented (W9-SCENE amendment 7).
        from aidm_server.game_state.orchestration.scene_plans import improvised_line

        told = [improvised_line(success=improvised.get('success') is True, hero=turn.actor if turn.party_play else '')]
        return told if turn.rolls_shown else [f'{label}: {total}{dc_clause} — {outcome}.', *told]
    if turn.rolls_shown:
        # The roll's card shows the numbers; the story tells what came of it.
        return _roll_outcome_lines(turn, rule_type, success=outcome == 'success') if outcome else []
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
    if turn.is_speaker(name) and not turn.party_play:
        return 'you'
    return turn.creature(name) or 'the target'


def _person(turn: NarrationInput, name: Any, *, pronoun: bool = False) -> dict[str, str]:
    """Subject, object and verb forms for a named participant: second person for the speaker alone; in
    party play the hero's name, or ``they`` with ``pronoun`` (the sentence before named them)."""

    if turn.is_speaker(name) and (pronoun or not turn.party_play):
        subject, object_form = ('They', 'them') if turn.party_play else ('You', 'you')
        forms = dict(target=subject, target_lower=subject.lower(), target_object=object_form, be='are')
        return {**forms, **{verb: verb for verb in PERSON_VERBS}}
    display = turn.creature(name) or 'the target'
    return {
        'target': display[:1].upper() + display[1:],
        'target_lower': display,
        'target_object': display,
        'be': 'is',
        **{verb: _third_person(verb) for verb in PERSON_VERBS},
    }


def _hero_does(turn: NarrationInput, phrase: str) -> str:
    """``You find a key.`` alone; ``Seren Ashgrove finds a key.`` in party play."""

    return _sentence(f'{turn.actor} {_third_person(phrase)}' if turn.party_play else f'You {phrase}')


def _third_person(phrase: str) -> str:
    """``search under the bench`` -> ``searches under the bench``: the first word in the third person."""

    verb, space, rest = phrase.partition(' ')
    lower = verb.lower()
    if lower in IRREGULAR_THIRD_PERSON:
        verb = IRREGULAR_THIRD_PERSON[lower]
    elif re.search(r'(?:s|sh|ch|x|z|o)$', lower):
        verb = f'{verb}es'
    elif re.search(r'[^aeiou]y$', lower):
        verb = f'{verb[:-1]}ies'
    else:
        verb = f'{verb}s'
    return f'{verb}{space}{rest}'


def _possessive(name: str) -> str:
    return f"{name}'s"


def _capitalized(text: str) -> str:
    return f'{text[:1].upper()}{text[1:]}'


def engine_turn_line(rules_hint: dict[str, Any]) -> str:
    """The lines the engine ends a turn with: in a fight still going, who acts next (``It is still
    Thalion's turn.`` / ``Corin Ashby is up next.``), and last the finishing blow's question when the turn
    set one (``The ashmarked cutthroat is finished. How do you end it?``); '' when there are none."""

    turn = NarrationInput(context={}, rules_hint=_dict(rules_hint), player_input='', speaker=None, opening_brief=None)
    return '\n\n'.join(line for line in (_next_actor_line(turn), _finisher_closing(turn)) if line)


# Composition --------------------------------------------------------------------------------------------------------


def _compose(turn: NarrationInput, blocks: list[list[str]]) -> str:
    """The reply's paragraphs (after a finishing blow let pass) and the director's closing note, then a defeat, the
    fight's next actor, the one-action reminder and the finishing blow's question."""

    # A finishing blow let pass is told before whatever this turn did.
    paragraphs = [block for block in [_lapsed_lines(turn), *blocks] if block]
    facts = turn.facts
    closing = director.scripted_closing(facts)
    if closing:
        paragraphs.append([closing])
    defeat = _dict(facts.get('partyDefeat'))
    if defeat:
        enemies = _text(defeat.get('enemies')) or 'the enemies'
        paragraph = PARTY_DEFEAT_PARAGRAPH if turn.party_play else DEFEAT_PARAGRAPH
        paragraphs.append([paragraph.format(enemies=enemies, be='are' if defeat.get('plural') is not False else 'is')])
        # An encounter's onDefeat, told as written after the fall (W9-SCENE).
        paragraphs.extend([text] for text in _string_items(_dict(facts.get('sceneTold')).get('closing')))
    elif _next_actor_line(turn):
        paragraphs.append([_next_actor_line(turn)])
    deferred = _dicts(_dict(facts.get('freeText')).get('deferred'))
    if deferred:
        paragraphs.append([ONE_THING_AT_A_TIME.format(target=_text(deferred[0].get('target')), verb=_text(deferred[0].get('verb')))])
    if _finisher_closing(turn):
        paragraphs.append([_finisher_closing(turn)])
    return '\n\n'.join(' '.join(paragraph) for paragraph in paragraphs)


def _finisher_closing(turn: NarrationInput) -> str:
    """The one closing question the narration asks: how the hero ends the foe they felled this turn."""

    if _dict(turn.facts.get('partyDefeat')):
        return ''
    return _text(_dict(turn.facts.get('finishingBlow')).get('closing'))


def _next_actor_line(turn: NarrationInput) -> str:
    """Who acts next in a fight still going after this turn; '' when none or after a party defeat."""

    if _dict(turn.facts.get('partyDefeat')) or _fight_ended(turn):
        return ''
    return _text(turn.facts.get('nextActorLine'))


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
