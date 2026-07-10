from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aidm_server.character_state import serialize_stats_payload
from aidm_server.canon_inventory import inventory_payload
from aidm_server.models import Player, safe_json_dumps, safe_json_loads
from aidm_server.profile_icons import profile_icon_src_for_character
from aidm_server.spellbook import ensure_character_sheet_spellbook
from aidm_server.starting_inventory import starting_inventory_for_class


PREGEN_VERSION = 1


@dataclass(frozen=True)
class PregeneratedCharacterPreset:
    character_id: str
    character_name: str
    player_name: str
    race: str
    sex: str
    class_name: str
    level: int
    tagline: str
    stats: dict[str, Any]
    character_sheet: dict[str, Any]


PREGENERATED_CHARACTER_PRESETS: tuple[PregeneratedCharacterPreset, ...] = (
    PregeneratedCharacterPreset(
        character_id='arden-vale',
        character_name='Arden Vale',
        player_name='Local Player',
        race='Human',
        sex='male',
        class_name='Fighter',
        level=1,
        tagline='A steady sword arm who protects the road when words fail.',
        stats={
            'ability_scores': {
                'strength': 15,
                'dexterity': 12,
                'constitution': 14,
                'intelligence': 10,
                'wisdom': 11,
                'charisma': 10,
            },
            'skill_proficiencies': ['athletics', 'perception'],
            'gold': 10,
            'default_weapon_id': 'starter_fighter_longsword',
        },
        character_sheet={
            'background': 'Road warden',
            'personality': 'Patient, direct, and careful with frightened strangers.',
            'ideal': 'Protection matters most when nobody powerful is watching.',
            'bond': 'Keeps an old mile-marker charm from the first road they guarded.',
            'flaw': 'Steps into danger before asking for help.',
        },
    ),
    PregeneratedCharacterPreset(
        character_id='liora-quill',
        character_name='Liora Quill',
        player_name='Local Player',
        race='Elf',
        sex='female',
        class_name='Wizard',
        level=1,
        tagline='A sharp-eyed scholar who reads omens in ink, ash, and old stone.',
        stats={
            'ability_scores': {
                'strength': 8,
                'dexterity': 14,
                'constitution': 13,
                'intelligence': 15,
                'wisdom': 12,
                'charisma': 10,
            },
            'skill_proficiencies': ['arcana', 'investigation'],
            'gold': 10,
            'default_weapon_id': 'starter_wizard_quarterstaff',
        },
        character_sheet={
            'background': 'Archive apprentice',
            'personality': 'Curious, precise, and only a little too pleased by a good puzzle.',
            'ideal': 'Forgotten names deserve to be found before power claims them.',
            'bond': 'Carries marginalia from a missing mentor.',
            'flaw': 'Will open the strange book first and regret it later.',
        },
    ),
    PregeneratedCharacterPreset(
        character_id='mara-fen',
        character_name='Mara Fen',
        player_name='Local Player',
        race='Halfling',
        sex='female',
        class_name='Rogue',
        level=1,
        tagline='A quick hand and quicker grin, useful when roads hide locked answers.',
        stats={
            'ability_scores': {
                'strength': 8,
                'dexterity': 15,
                'constitution': 13,
                'intelligence': 12,
                'wisdom': 12,
                'charisma': 13,
            },
            'skill_proficiencies': ['stealth', 'sleight_of_hand', 'thieves_tools'],
            'gold': 12,
            'default_weapon_id': 'starter_rogue_rapier',
        },
        character_sheet={
            'background': 'Courier',
            'personality': 'Warm, observant, and allergic to locked doors.',
            'ideal': 'The small folk survive by sharing warnings quickly.',
            'bond': 'Knows half the roadside inns by their kitchen doors.',
            'flaw': 'Cannot resist proving a suspicious person is lying.',
        },
    ),
    PregeneratedCharacterPreset(
        character_id='tovan-ember',
        character_name='Tovan Ember',
        player_name='Local Player',
        race='Dwarf',
        sex='male',
        class_name='Cleric',
        level=1,
        tagline='A keeper of roadside shrines with a lantern, a shield, and stubborn mercy.',
        stats={
            'ability_scores': {
                'strength': 13,
                'dexterity': 10,
                'constitution': 14,
                'intelligence': 10,
                'wisdom': 15,
                'charisma': 10,
            },
            'skill_proficiencies': ['insight', 'medicine'],
            'gold': 10,
            'default_weapon_id': 'starter_cleric_mace',
        },
        character_sheet={
            'background': 'Shrine keeper',
            'personality': 'Gentle until someone threatens the helpless.',
            'ideal': 'Mercy is an oath, not a mood.',
            'bond': 'Restores neglected roadside shrines one stone at a time.',
            'flaw': 'Trusts old vows longer than old vowbreakers deserve.',
        },
    ),
)


def default_pregenerated_character_id() -> str:
    return PREGENERATED_CHARACTER_PRESETS[0].character_id


def pregenerated_character_preset(character_id: str | None = None) -> PregeneratedCharacterPreset | None:
    selected_id = str(character_id or default_pregenerated_character_id()).strip()
    for preset in PREGENERATED_CHARACTER_PRESETS:
        if preset.character_id == selected_id:
            return preset
    return None


def pregenerated_character_payload(preset: PregeneratedCharacterPreset) -> dict[str, Any]:
    inventory = starting_inventory_for_class(preset.class_name)
    stats_payload, stats_error = serialize_stats_payload(_stats_with_metadata(preset), level=preset.level)
    stats = safe_json_loads(stats_payload, {}) if stats_payload and not stats_error else _stats_with_metadata(preset)
    sheet, _changed = ensure_character_sheet_spellbook(
        preset.character_sheet,
        class_name=preset.class_name,
        race_name=preset.race,
        level=preset.level,
    )
    return {
        'character_id': preset.character_id,
        'character_name': preset.character_name,
        'name': preset.player_name,
        'race': preset.race,
        'sex': preset.sex,
        'class_': preset.class_name,
        'char_class': preset.class_name,
        'level': preset.level,
        'tagline': preset.tagline,
        'profile_image': profile_icon_src_for_character(preset.race, preset.sex),
        'stats': stats,
        'inventory': inventory_payload(inventory),
        'character_sheet': sheet,
    }


def list_pregenerated_character_payloads() -> list[dict[str, Any]]:
    return [pregenerated_character_payload(preset) for preset in PREGENERATED_CHARACTER_PRESETS]


def build_player_from_preset(
    preset: PregeneratedCharacterPreset,
    *,
    workspace_id: str,
    campaign_id: int,
    account_id: int | None = None,
) -> Player:
    stats_payload, stats_error = serialize_stats_payload(_stats_with_metadata(preset), level=preset.level)
    if stats_error:
        raise ValueError(stats_error)
    inventory = starting_inventory_for_class(preset.class_name)
    sheet, _changed = ensure_character_sheet_spellbook(
        preset.character_sheet,
        class_name=preset.class_name,
        race_name=preset.race,
        level=preset.level,
    )
    return Player(
        workspace_id=workspace_id,
        account_id=account_id,
        campaign_id=campaign_id,
        name=preset.player_name,
        character_name=preset.character_name,
        race=preset.race,
        sex=preset.sex,
        class_=preset.class_name,
        level=preset.level,
        stats=stats_payload,
        inventory=safe_json_dumps(inventory, []),
        character_sheet=safe_json_dumps(sheet, {}),
    )


def player_matches_preset(player: Player, preset: PregeneratedCharacterPreset) -> bool:
    stats = safe_json_loads(player.stats, {})
    metadata = stats.get('metadata') if isinstance(stats, dict) and isinstance(stats.get('metadata'), dict) else {}
    return (
        metadata.get('source') == 'play_now'
        and metadata.get('pregenId') == preset.character_id
        and player.campaign_id is not None
    )


def _stats_with_metadata(preset: PregeneratedCharacterPreset) -> dict[str, Any]:
    stats = dict(preset.stats)
    metadata = dict(stats.get('metadata')) if isinstance(stats.get('metadata'), dict) else {}
    metadata.update(
        {
            'source': 'play_now',
            'pregenId': preset.character_id,
            'pregenVersion': PREGEN_VERSION,
        }
    )
    stats['metadata'] = metadata
    return stats
