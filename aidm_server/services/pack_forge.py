from __future__ import annotations

import hashlib
import json
from typing import Any

from aidm_server.game_state.models import stable_slug
from aidm_server.services.campaign_pack_linter import lint_campaign_pack_manifest


MAX_FORGE_TITLE_LENGTH = 120
MAX_FORGE_PROMPT_LENGTH = 1200
MAX_FORGE_TONE_LENGTH = 160
STOP_WORDS = {
    'about',
    'after',
    'again',
    'against',
    'between',
    'campaign',
    'characters',
    'during',
    'fantasy',
    'from',
    'into',
    'that',
    'their',
    'there',
    'this',
    'with',
    'world',
}


class CampaignPackForgeError(ValueError):
    def __init__(self, message: str, *, error_code: str = 'validation_error', status_code: int = 400):
        super().__init__(message)
        self.public_message = message
        self.error_code = error_code
        self.status_code = status_code


def forge_campaign_pack(payload: dict[str, Any], *, workspace_id: str = 'forge') -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CampaignPackForgeError('Expected JSON request body.')

    title = _required_text(_first(payload, 'title', 'name'), field='title', max_length=MAX_FORGE_TITLE_LENGTH)
    prompt = _optional_text(
        _first(payload, 'prompt', 'theme', 'premise', 'description'),
        max_length=MAX_FORGE_PROMPT_LENGTH,
    )
    tone = _optional_text(_first(payload, 'tone', 'toneTag', 'tone_tag'), max_length=MAX_FORGE_TONE_LENGTH)
    seed = f'{title}|{prompt}|{tone}'
    pack_id = _pack_id(_optional_text(_first(payload, 'packId', 'pack_id'), max_length=80), seed=seed, title=title)
    traits = _forge_traits(title=title, prompt=prompt, tone=tone)
    pack = _manifest(title=title, prompt=prompt, tone=tone, pack_id=pack_id, traits=traits)
    source_filename = f'{pack_id}.json'
    wrapped_payload = {
        'sourceFilename': source_filename,
        'pack': pack,
    }
    lint = lint_campaign_pack_manifest(wrapped_payload, workspace_id=workspace_id)

    return {
        'ok': bool(lint.get('ok')),
        'sourceFilename': source_filename,
        'pack': pack,
        'payload': wrapped_payload,
        'manifestText': json.dumps(wrapped_payload, indent=2, ensure_ascii=True),
        'lint': lint,
    }


def _manifest(
    *,
    title: str,
    prompt: str,
    tone: str,
    pack_id: str,
    traits: dict[str, str],
) -> dict[str, Any]:
    root = stable_slug(pack_id)
    loc_start = f'{root}_threshold'
    loc_middle = f'{root}_crossroads'
    loc_final = f'{root}_heart'
    npc_guide = f'{root}_guide'
    npc_rival = f'{root}_rival'
    quest_main = f'{root}_main_quest'
    enemy_scout = f'{root}_scout'
    enemy_guardian = f'{root}_guardian'
    encounter_signal = f'{root}_signal_watch'
    encounter_final = f'{root}_final_guardian'
    clue_key = f'{root}_first_clue'
    faction_key = f'{root}_keepers'
    lore_key = f'{root}_hidden_lore'
    handout_key = f'{root}_sealed_handout'
    cp_arrival = f'{root}_cp_arrival'
    cp_trace = f'{root}_cp_trace'
    cp_choice = f'{root}_cp_choice'
    cp_final = f'{root}_cp_finale'

    premise = prompt or f'{title} begins with a missing oath, a contested route, and a place that should have stayed quiet.'
    tone_note = f' Tone: {tone}.' if tone else ''
    motif = traits['motif']
    threat = traits['threat']
    place = traits['place']
    secret = traits['secret']

    return {
        'schemaVersion': '1.0.0',
        'packId': pack_id,
        'title': title,
        'version': '1.0.0',
        'description': _trim(f'{premise} The pack opens at {place}, follows the trail of {motif}, and ends when the table decides what the truth costs.{tone_note}'),
        'world': {
            'name': f'{title} Setting',
            'description': _trim(f'A compact adventure region shaped by {motif}, public unease, and the hidden pressure of {secret}.'),
        },
        'startingState': {
            'locationId': loc_start,
            'questId': quest_main,
            'checkpointId': cp_arrival,
            'currentScene': {
                'locationId': loc_start,
                'name': f'{place} Arrival',
                'description': _trim(f'The party arrives as rumors of {threat} spread through {place}.'),
                'sceneType': 'exploration',
                'dangerLevel': 2,
                'mood': traits['mood'],
                'activeNpcIds': [npc_guide],
                'activeQuestIds': [quest_main],
            },
            'knownLocationIds': [loc_start],
            'knownNpcIds': [npc_guide],
            'activeNpcIds': [npc_guide],
            'knownQuestIds': [quest_main],
            'activeQuestIds': [quest_main],
            'flags': {
                'packForgeDraft': True,
                'motif': motif,
            },
        },
        'locations': [
            {
                'id': loc_start,
                'name': place,
                'type': 'settlement edge',
                'status': 'uneasy',
                'description': _trim(f'{place} is where witnesses, rumors, and practical needs collide around {motif}.'),
                'connectedLocationIds': [loc_middle],
                'visibleAtStart': True,
            },
            {
                'id': loc_middle,
                'name': traits['crossroads'],
                'type': 'crossroads',
                'status': 'contested',
                'description': _trim(f'A pressure point where allies can be won, tracks can be lost, and {threat} becomes harder to dismiss.'),
                'connectedLocationIds': [loc_start, loc_final],
            },
            {
                'id': loc_final,
                'name': traits['heart'],
                'type': 'lair',
                'status': 'sealed',
                'description': _trim(f'The hidden center of the adventure, holding the proof of {secret} and the final choice about {motif}.'),
                'connectedLocationIds': [loc_middle],
            },
        ],
        'npcs': [
            {
                'id': npc_guide,
                'name': traits['guide'],
                'role': 'local guide',
                'disposition': 'wary ally',
                'locationId': loc_start,
                'questIds': [quest_main],
                'visibleAtStart': True,
            },
            {
                'id': npc_rival,
                'name': traits['rival'],
                'role': 'rival claimant',
                'disposition': 'useful trouble',
                'locationId': loc_middle,
                'questIds': [quest_main],
            },
        ],
        'quests': [
            {
                'id': quest_main,
                'title': f'Uncover {motif.title()}',
                'status': 'active',
                'stage': 'opening',
                'summary': _trim(f'Follow the first lead from {place} to the source of {threat}, then decide who should control the truth.'),
                'objectives': [
                    {
                        'id': f'{root}_obj_first_lead',
                        'description': f'Question witnesses and secure the first clue about {motif}.',
                        'status': 'open',
                    },
                    {
                        'id': f'{root}_obj_crossroads',
                        'description': f'Navigate {traits["crossroads"]} without losing the trail.',
                        'status': 'open',
                    },
                    {
                        'id': f'{root}_obj_final_choice',
                        'description': f'Resolve the secret at {traits["heart"]}.',
                        'status': 'open',
                    },
                ],
                'visibleAtStart': True,
            }
        ],
        'enemies': [
            {
                'id': enemy_scout,
                'name': traits['scout'],
                'creatureType': 'humanoid',
                'challengeTier': 'minor',
                'locationIds': [loc_middle],
                'factionIds': [faction_key],
                'tags': ['campaign_pack', 'scout', stable_slug(motif)],
            },
            {
                'id': enemy_guardian,
                'name': traits['guardian'],
                'creatureType': 'guardian',
                'challengeTier': 'boss',
                'locationIds': [loc_final],
                'factionIds': [faction_key],
                'tags': ['campaign_pack', 'guardian', stable_slug(motif)],
            },
        ],
        'encounters': [
            {
                'id': encounter_signal,
                'title': 'Signal Watch',
                'summary': _trim(f'A tense interruption at {traits["crossroads"]} that reveals the opposition is organized.'),
                'locationIds': [loc_middle],
                'questIds': [quest_main],
                'checkpointIds': [cp_trace],
                'enemyIds': [enemy_scout],
                'completion': {
                    'anyOf': [
                        {'outcome': 'defeat', 'description': 'The scouts are defeated or driven off.'},
                        {'outcome': 'bargain', 'description': 'The scouts reveal who sent them.'},
                    ]
                },
            },
            {
                'id': encounter_final,
                'title': 'The Last Custodian',
                'summary': _trim(f'The final confrontation at {traits["heart"]}, where force, mercy, and disclosure are all valid endings.'),
                'locationIds': [loc_final],
                'questIds': [quest_main],
                'checkpointIds': [cp_final],
                'enemyIds': [enemy_guardian],
                'completion': {
                    'anyOf': [
                        {'outcome': 'defeat', 'description': 'The guardian is overcome.'},
                        {'outcome': 'truth', 'description': 'The secret is exposed and the guardian stands down.'},
                    ]
                },
            },
        ],
        'segments': [
            {
                'id': f'{root}_pressure_rises',
                'title': 'Pressure Rises',
                'description': _trim(f'Escalate signs of {threat} whenever the party delays or exposes themselves.'),
                'trigger': {'type': 'state', 'quest_contains': motif},
                'tags': ['pacing', stable_slug(traits['mood'])],
            }
        ],
        'checkpoints': [
            {
                'id': cp_arrival,
                'title': 'Arrival and First Lead',
                'playerTitle': 'Find the first lead',
                'summary': _trim(f'Establish {place}, introduce {traits["guide"]}, and point the party toward {traits["crossroads"]}.'),
                'chapter': 'Opening',
                'act': 'I',
                'priority': 90,
                'gate': 'soft',
                'visibleToPlayers': True,
                'locationIds': [loc_start],
                'questIds': [quest_main],
                'npcIds': [npc_guide],
                'clueIds': [clue_key],
                'nextCheckpointIds': [cp_trace],
                'completeWhen': {'clueId': clue_key, 'description': 'The party identifies the first actionable lead.'},
            },
            {
                'id': cp_trace,
                'title': 'Trace the Opposition',
                'playerTitle': 'Follow the opposition',
                'summary': _trim(f'The trail reaches {traits["crossroads"]}; reveal {traits["rival"]} and test the party with the first encounter.'),
                'chapter': 'Complication',
                'act': 'II',
                'priority': 80,
                'gate': 'soft',
                'locationIds': [loc_middle],
                'questIds': [quest_main],
                'npcIds': [npc_rival],
                'encounterIds': [encounter_signal],
                'nextCheckpointIds': [cp_choice],
                'completeWhen': {'encounterId': encounter_signal, 'description': 'The party gets a route to the hidden heart.'},
            },
            {
                'id': cp_choice,
                'title': 'Choose the Terms',
                'playerTitle': 'Decide how to proceed',
                'summary': _trim(f'Let the party choose stealth, negotiation, or direct pressure before reaching {traits["heart"]}.'),
                'chapter': 'Revelation',
                'act': 'II',
                'priority': 70,
                'gate': 'optional',
                'locationIds': [loc_middle, loc_final],
                'questIds': [quest_main],
                'alternateCheckpointIds': [cp_final],
                'nextCheckpointIds': [cp_final],
                'completeWhen': {'decision': 'The party chooses an approach to the final site.'},
            },
            {
                'id': cp_final,
                'title': 'Final Custodian',
                'playerTitle': 'Resolve the secret',
                'summary': _trim(f'Confront {traits["guardian"]}, reveal {secret}, and let the ending follow the party choices.'),
                'chapter': 'Finale',
                'act': 'III',
                'priority': 100,
                'gate': 'soft',
                'terminal': True,
                'locationIds': [loc_final],
                'questIds': [quest_main],
                'encounterIds': [encounter_final],
            },
        ],
        'clues': [
            {
                'id': clue_key,
                'title': 'The First Pattern',
                'summary': _trim(f'A sign that the visible problem is only the edge of {secret}.'),
            }
        ],
        'factions': [
            {
                'id': faction_key,
                'title': 'The Keepers of the Quiet Line',
                'summary': _trim(f'A small organized group trying to control what becomes known about {motif}.'),
            }
        ],
        'handouts': [
            {
                'id': handout_key,
                'title': 'Sealed Note',
                'summary': _trim(f'A private note naming {threat} and warning against entering {traits["heart"]}.'),
                'hiddenToPlayers': True,
            }
        ],
        'lore': [
            {
                'id': lore_key,
                'title': 'What Was Buried',
                'summary': _trim(f'The buried truth: {secret}. Reveal it only after the party earns leverage.'),
                'hiddenToPlayers': True,
            }
        ],
        'directorRules': {
            'mainQuestGeneration': 'allowed_tagged',
            'sideQuestGeneration': 'allowed_tagged',
            'newNpcs': 'allowed_as_minor_or_temporary',
            'newLocations': 'allowed_as_local_detail',
            'offTrackPolicy': 'improvise_and_reconnect',
            'checkpointStyle': 'guided',
        },
        'gmNotes': [
            'Keep the authored checkpoint spine visible, but allow player plans to determine how each checkpoint resolves.',
            'When improvising, reconnect new scenes to the active clue, the rival, or the final site.',
        ],
        'hiddenSceneNotes': {
            'secret': secret,
            'pressureClock': f'If the party stalls, {threat} claims a visible cost in {place}.',
        },
        'marketplace': {
            'source': 'pack_forge',
            'tone': tone,
            'tags': [stable_slug(motif), stable_slug(traits['mood']), 'starter_pack'],
        },
    }


def _forge_traits(*, title: str, prompt: str, tone: str) -> dict[str, str]:
    tokens = _theme_tokens(f'{title} {prompt} {tone}')
    anchor = tokens[0] if tokens else 'oath'
    second = tokens[1] if len(tokens) > 1 else 'shadow'
    lower = f'{title} {prompt} {tone}'.lower()
    if any(word in lower for word in ('sea', 'ship', 'tide', 'ocean', 'harbor')):
        place = 'The Breakwater Gate'
        crossroads = 'The Lantern Shoals'
        heart = 'The Drowned Archive'
        threat = 'a tide-marked conspiracy'
        mood = 'salt air, pressure, and old debt'
    elif any(word in lower for word in ('desert', 'sand', 'sun', 'dune', 'oasis')):
        place = 'The Glasswell Caravanserai'
        crossroads = 'The Singing Dunes'
        heart = 'The Buried Observatory'
        threat = 'a sun-scarred omen'
        mood = 'heat shimmer, patience, and dread'
    elif any(word in lower for word in ('city', 'noir', 'guild', 'court', 'throne')):
        place = 'The Sable Ward'
        crossroads = 'The Knife-Market Steps'
        heart = 'The Court Below'
        threat = 'a civic lie with teeth'
        mood = 'rain, whispers, and polished danger'
    elif any(word in lower for word in ('forest', 'fey', 'wild', 'grove', 'wood')):
        place = 'The Greenwake Road'
        crossroads = 'The Antlered Crossing'
        heart = 'The Rootbound Door'
        threat = 'a bargain waking under the trees'
        mood = 'green hush, wonder, and unease'
    elif any(word in lower for word in ('space', 'star', 'astral', 'moon', 'void')):
        place = 'The Starfall Causeway'
        crossroads = 'The Orrey Between'
        heart = 'The Moonless Engine'
        threat = 'an impossible signal'
        mood = 'cold light, awe, and isolation'
    else:
        place = 'The Old Milepost'
        crossroads = f'The {anchor.title()} Crossing'
        heart = f'The {second.title()} Vault'
        threat = f'a spreading rumor about {anchor}'
        mood = 'restless, mysterious, and hopeful'
    return {
        'motif': anchor.replace('_', ' '),
        'place': place,
        'crossroads': crossroads,
        'heart': heart,
        'threat': threat,
        'secret': f'{anchor.replace("_", " ")} and {second.replace("_", " ")} are bound by an older promise',
        'guide': f'Mara {anchor.title()}',
        'rival': f'Orin {second.title()}',
        'scout': f'{anchor.title()} Scout',
        'guardian': f'{second.title()} Custodian',
        'mood': mood,
    }


def _theme_tokens(value: str) -> list[str]:
    tokens: list[str] = []
    for raw in stable_slug(value).split('_'):
        token = raw.strip('_')
        if len(token) < 4 or token in STOP_WORDS or token in tokens:
            continue
        tokens.append(token)
        if len(tokens) >= 6:
            break
    return tokens


def _pack_id(explicit: str, *, seed: str, title: str) -> str:
    base = stable_slug(explicit or title)[:72]
    digest = hashlib.sha1(seed.encode('utf-8')).hexdigest()[:8]
    return f'forge_{base}_{digest}'[:120]


def _first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in record:
            return record[key]
    return None


def _required_text(value: Any, *, field: str, max_length: int) -> str:
    text = _optional_text(value, max_length=max_length)
    if not text:
        raise CampaignPackForgeError(f'{field} is required.')
    return text


def _optional_text(value: Any, *, max_length: int) -> str:
    if value is None:
        return ''
    if not isinstance(value, str):
        raise CampaignPackForgeError('Forge fields must be strings.')
    return _trim(value, max_length=max_length)


def _trim(value: str, *, max_length: int = 4000) -> str:
    text = ' '.join(str(value or '').split())
    return text[:max_length].rstrip()
