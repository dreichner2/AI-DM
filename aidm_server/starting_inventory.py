from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from aidm_server.canon_inventory import inventory_payload
from aidm_server.game_state.equipment import occupied_slots


STARTER_KITS: dict[str, list[dict[str, Any]]] = {
    'barbarian': [
        {'id': 'starter_barbarian_greataxe', 'name': 'Greataxe', 'quantity': 1, 'type': 'weapon', 'subtype': 'greataxe', 'equipped': True, 'slot': 'two_hands'},
        {'id': 'starter_barbarian_handaxe', 'name': 'Handaxe', 'quantity': 2, 'type': 'weapon', 'subtype': 'handaxe'},
        {'id': 'starter_barbarian_javelin', 'name': 'Javelin', 'quantity': 4, 'type': 'weapon', 'subtype': 'javelin'},
        {'id': 'starter_barbarian_pack', 'name': "Explorer's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
        {'id': 'starter_barbarian_rations', 'name': 'Ration', 'quantity': 5, 'type': 'consumable'},
    ],
    'bard': [
        {'id': 'starter_bard_rapier', 'name': 'Rapier', 'quantity': 1, 'type': 'weapon', 'subtype': 'rapier', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_bard_dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger'},
        {'id': 'starter_bard_leather_armor', 'name': 'Leather Armor', 'quantity': 1, 'type': 'armor', 'subtype': 'light armor', 'equipped': True, 'slot': 'body_armor'},
        {'id': 'starter_bard_lute', 'name': 'Lute', 'quantity': 1, 'type': 'tool', 'weight': 2},
        {'id': 'starter_bard_pack', 'name': "Diplomat's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'cleric': [
        {'id': 'starter_cleric_mace', 'name': 'Mace', 'quantity': 1, 'type': 'weapon', 'subtype': 'mace', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_cleric_shield', 'name': 'Shield', 'quantity': 1, 'type': 'armor', 'subtype': 'shield', 'equipped': True, 'slot': 'off_hand'},
        {'id': 'starter_cleric_scale_mail', 'name': 'Scale Mail', 'quantity': 1, 'type': 'armor', 'subtype': 'medium armor', 'equipped': True, 'slot': 'body_armor', 'weight': 45},
        {'id': 'starter_cleric_holy_symbol', 'name': 'Holy Symbol', 'quantity': 1, 'type': 'focus', 'weight': 1},
        {'id': 'starter_cleric_pack', 'name': "Priest's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'druid': [
        {'id': 'starter_druid_scimitar', 'name': 'Scimitar', 'quantity': 1, 'type': 'weapon', 'subtype': 'scimitar', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_druid_wooden_shield', 'name': 'Wooden Shield', 'quantity': 1, 'type': 'armor', 'subtype': 'shield', 'equipped': True, 'slot': 'off_hand', 'weight': 6},
        {'id': 'starter_druid_leather_armor', 'name': 'Leather Armor', 'quantity': 1, 'type': 'armor', 'subtype': 'light armor', 'equipped': True, 'slot': 'body_armor'},
        {'id': 'starter_druid_focus', 'name': 'Druidic Focus', 'quantity': 1, 'type': 'focus', 'weight': 1},
        {'id': 'starter_druid_pack', 'name': "Explorer's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'fighter': [
        {'id': 'starter_fighter_longsword', 'name': 'Longsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'longsword', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_fighter_shield', 'name': 'Shield', 'quantity': 1, 'type': 'armor', 'subtype': 'shield', 'equipped': True, 'slot': 'off_hand'},
        {'id': 'starter_fighter_chain_mail', 'name': 'Chain Mail', 'quantity': 1, 'type': 'armor', 'subtype': 'heavy armor', 'equipped': True, 'slot': 'body_armor'},
        {'id': 'starter_fighter_pack', 'name': "Explorer's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
        {'id': 'starter_fighter_rations', 'name': 'Ration', 'quantity': 5, 'type': 'consumable'},
        {'id': 'starter_fighter_torches', 'name': 'Torch', 'quantity': 5, 'type': 'gear'},
    ],
    'monk': [
        {'id': 'starter_monk_quarterstaff', 'name': 'Quarterstaff', 'quantity': 1, 'type': 'weapon', 'subtype': 'quarterstaff', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_monk_darts', 'name': 'Dart', 'quantity': 10, 'type': 'weapon', 'subtype': 'dart', 'weight': 0.25},
        {'id': 'starter_monk_pack', 'name': "Explorer's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
        {'id': 'starter_monk_rations', 'name': 'Ration', 'quantity': 5, 'type': 'consumable'},
    ],
    'paladin': [
        {'id': 'starter_paladin_longsword', 'name': 'Longsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'longsword', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_paladin_shield', 'name': 'Shield', 'quantity': 1, 'type': 'armor', 'subtype': 'shield', 'equipped': True, 'slot': 'off_hand'},
        {'id': 'starter_paladin_chain_mail', 'name': 'Chain Mail', 'quantity': 1, 'type': 'armor', 'subtype': 'heavy armor', 'equipped': True, 'slot': 'body_armor'},
        {'id': 'starter_paladin_holy_symbol', 'name': 'Holy Symbol', 'quantity': 1, 'type': 'focus', 'weight': 1},
        {'id': 'starter_paladin_pack', 'name': "Priest's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'ranger': [
        {'id': 'starter_ranger_longbow', 'name': 'Longbow', 'quantity': 1, 'type': 'weapon', 'subtype': 'longbow', 'equipped': True, 'slot': 'two_hands', 'weight': 2},
        {'id': 'starter_ranger_shortsword', 'name': 'Shortsword', 'quantity': 1, 'type': 'weapon', 'subtype': 'shortsword'},
        {'id': 'starter_ranger_leather_armor', 'name': 'Leather Armor', 'quantity': 1, 'type': 'armor', 'subtype': 'light armor', 'equipped': True, 'slot': 'body_armor'},
        {'id': 'starter_ranger_arrows', 'name': 'Arrow', 'quantity': 20, 'type': 'ammo', 'weight': 0.05},
        {'id': 'starter_ranger_pack', 'name': "Explorer's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'rogue': [
        {'id': 'starter_rogue_rapier', 'name': 'Rapier', 'quantity': 1, 'type': 'weapon', 'subtype': 'rapier', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_rogue_dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger', 'equipped': True, 'slot': 'off_hand'},
        {'id': 'starter_rogue_leather_armor', 'name': 'Leather Armor', 'quantity': 1, 'type': 'armor', 'subtype': 'light armor', 'equipped': True, 'slot': 'body_armor'},
        {'id': 'starter_rogue_thieves_tools', 'name': "Thieves' Tools", 'quantity': 1, 'type': 'tool', 'weight': 1},
        {'id': 'starter_rogue_pack', 'name': "Burglar's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'sorcerer': [
        {'id': 'starter_sorcerer_dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_sorcerer_crossbow', 'name': 'Light Crossbow', 'quantity': 1, 'type': 'weapon', 'subtype': 'crossbow', 'weight': 5},
        {'id': 'starter_sorcerer_focus', 'name': 'Arcane Focus', 'quantity': 1, 'type': 'focus', 'weight': 1},
        {'id': 'starter_sorcerer_pack', 'name': "Explorer's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'warlock': [
        {'id': 'starter_warlock_dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_warlock_crossbow', 'name': 'Light Crossbow', 'quantity': 1, 'type': 'weapon', 'subtype': 'crossbow', 'weight': 5},
        {'id': 'starter_warlock_leather_armor', 'name': 'Leather Armor', 'quantity': 1, 'type': 'armor', 'subtype': 'light armor', 'equipped': True, 'slot': 'body_armor'},
        {'id': 'starter_warlock_focus', 'name': 'Arcane Focus', 'quantity': 1, 'type': 'focus', 'weight': 1},
        {'id': 'starter_warlock_pack', 'name': "Scholar's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'wizard': [
        {'id': 'starter_wizard_quarterstaff', 'name': 'Quarterstaff', 'quantity': 1, 'type': 'weapon', 'subtype': 'quarterstaff', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_wizard_dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger'},
        {'id': 'starter_wizard_spellbook', 'name': 'Spellbook', 'quantity': 1, 'type': 'focus', 'weight': 3},
        {'id': 'starter_wizard_component_pouch', 'name': 'Component Pouch', 'quantity': 1, 'type': 'focus', 'weight': 2},
        {'id': 'starter_wizard_pack', 'name': "Scholar's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'gunslinger': [
        {'id': 'starter_gunslinger_pistol', 'name': 'Pistol', 'quantity': 1, 'type': 'weapon', 'subtype': 'firearm', 'equipped': True, 'slot': 'main_hand', 'weight': 3},
        {'id': 'starter_gunslinger_dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger'},
        {'id': 'starter_gunslinger_leather_armor', 'name': 'Leather Armor', 'quantity': 1, 'type': 'armor', 'subtype': 'light armor', 'equipped': True, 'slot': 'body_armor'},
        {'id': 'starter_gunslinger_ammunition', 'name': 'Ammunition', 'quantity': 20, 'type': 'ammo', 'weight': 0.05},
        {'id': 'starter_gunslinger_tools', 'name': "Gunsmith's Tools", 'quantity': 1, 'type': 'tool', 'weight': 5},
    ],
    'artificer': [
        {'id': 'starter_artificer_light_hammer', 'name': 'Light Hammer', 'quantity': 1, 'type': 'weapon', 'subtype': 'hammer', 'equipped': True, 'slot': 'main_hand', 'weight': 2},
        {'id': 'starter_artificer_dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger'},
        {'id': 'starter_artificer_leather_armor', 'name': 'Leather Armor', 'quantity': 1, 'type': 'armor', 'subtype': 'light armor', 'equipped': True, 'slot': 'body_armor'},
        {'id': 'starter_artificer_tools', 'name': "Tinker's Tools", 'quantity': 1, 'type': 'tool', 'weight': 10},
        {'id': 'starter_artificer_pack', 'name': "Artisan's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'scholar': [
        {'id': 'starter_scholar_dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_scholar_notebook', 'name': 'Notebook', 'quantity': 1, 'type': 'gear', 'weight': 1},
        {'id': 'starter_scholar_ink_pen', 'name': 'Ink and Pen', 'quantity': 1, 'type': 'gear', 'weight': 0.1},
        {'id': 'starter_scholar_pack', 'name': "Scholar's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'noble': [
        {'id': 'starter_noble_rapier', 'name': 'Rapier', 'quantity': 1, 'type': 'weapon', 'subtype': 'rapier', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_noble_fine_clothes', 'name': 'Fine Clothes', 'quantity': 1, 'type': 'clothing', 'equipped': True, 'slot': 'clothing', 'weight': 6},
        {'id': 'starter_noble_signet_ring', 'name': 'Signet Ring', 'quantity': 1, 'type': 'gear', 'weight': 0.1},
        {'id': 'starter_noble_pack', 'name': "Diplomat's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'medic': [
        {'id': 'starter_medic_dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_medic_kit', 'name': "Healer's Kit", 'quantity': 1, 'type': 'tool', 'weight': 3},
        {'id': 'starter_medic_bag', 'name': 'Medical Bag', 'quantity': 1, 'type': 'gear', 'weight': 5},
        {'id': 'starter_medic_clothes', 'name': "Traveler's Clothes", 'quantity': 1, 'type': 'clothing', 'equipped': True, 'slot': 'clothing', 'weight': 4},
    ],
    'operative': [
        {'id': 'starter_operative_sidearm', 'name': 'Sidearm', 'quantity': 1, 'type': 'weapon', 'subtype': 'firearm', 'equipped': True, 'slot': 'main_hand', 'weight': 3},
        {'id': 'starter_operative_knife', 'name': 'Knife', 'quantity': 1, 'type': 'weapon', 'subtype': 'knife'},
        {'id': 'starter_operative_vest', 'name': 'Tactical Vest', 'quantity': 1, 'type': 'armor', 'subtype': 'vest', 'equipped': True, 'slot': 'body_armor', 'weight': 8},
        {'id': 'starter_operative_ammunition', 'name': 'Ammunition', 'quantity': 20, 'type': 'ammo', 'weight': 0.05},
        {'id': 'starter_operative_field_pack', 'name': 'Field Pack', 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'entertainer': [
        {'id': 'starter_entertainer_dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_entertainer_instrument', 'name': 'Musical Instrument', 'quantity': 1, 'type': 'tool', 'weight': 2},
        {'id': 'starter_entertainer_costume', 'name': 'Costume', 'quantity': 1, 'type': 'clothing', 'equipped': True, 'slot': 'clothing', 'weight': 4},
        {'id': 'starter_entertainer_pack', 'name': "Performer's Pack", 'quantity': 1, 'type': 'gear', 'weight': 5},
    ],
    'professional': [
        {'id': 'starter_professional_dagger', 'name': 'Dagger', 'quantity': 1, 'type': 'weapon', 'subtype': 'dagger', 'equipped': True, 'slot': 'main_hand'},
        {'id': 'starter_professional_clothes', 'name': 'Professional Clothes', 'quantity': 1, 'type': 'clothing', 'equipped': True, 'slot': 'clothing', 'weight': 4},
        {'id': 'starter_professional_notebook', 'name': 'Notebook', 'quantity': 1, 'type': 'gear', 'weight': 1},
        {'id': 'starter_professional_tools', 'name': 'Work Tools', 'quantity': 1, 'type': 'tool', 'weight': 5},
    ],
}

def _starter_item(
    item_id: str,
    name: str,
    *,
    quantity: int = 1,
    item_type: str = 'gear',
    subtype: str | None = None,
    equipped: bool = False,
    slot: str | None = None,
    weight: int | float | None = None,
    tags: list[str] | None = None,
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        'id': f'starter_{item_id}',
        'name': name,
        'quantity': quantity,
        'type': item_type,
    }
    if subtype:
        item['subtype'] = subtype
    if equipped:
        item['equipped'] = True
    if slot:
        item['slot'] = slot
    if weight is not None:
        item['weight'] = weight
    if tags:
        item['tags'] = tags
    if aliases:
        item['aliases'] = aliases
    return item


STARTER_KITS.update(
    {
        'swashbuckler': [
            _starter_item('swashbuckler_rapier', 'Rapier', item_type='weapon', subtype='rapier', equipped=True, slot='main_hand'),
            _starter_item('swashbuckler_dagger', 'Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='off_hand'),
            _starter_item('swashbuckler_leather', 'Leather Armor', item_type='armor', subtype='light armor', equipped=True, slot='body_armor'),
            _starter_item('swashbuckler_rope', 'Silk Rope', item_type='gear', weight=5),
            _starter_item('swashbuckler_flair', "Duelist's Sash", item_type='clothing', equipped=True, slot='clothing', weight=1),
        ],
        'cavalier': [
            _starter_item('cavalier_lance', 'Lance', item_type='weapon', subtype='lance', equipped=True, slot='main_hand', weight=6),
            _starter_item('cavalier_shield', 'Shield', item_type='armor', subtype='shield', equipped=True, slot='off_hand'),
            _starter_item('cavalier_chain_mail', 'Chain Mail', item_type='armor', subtype='heavy armor', equipped=True, slot='body_armor'),
            _starter_item('cavalier_longsword', 'Longsword', item_type='weapon', subtype='longsword'),
            _starter_item('cavalier_saddle', 'Riding Saddle', item_type='gear', weight=25),
            _starter_item('cavalier_banner', 'House Banner', item_type='gear', weight=2),
        ],
        'guardian': [
            _starter_item('guardian_warhammer', 'Warhammer', item_type='weapon', subtype='warhammer', equipped=True, slot='main_hand', weight=2),
            _starter_item('guardian_shield', 'Shield', item_type='armor', subtype='shield', equipped=True, slot='off_hand'),
            _starter_item('guardian_chain_mail', 'Chain Mail', item_type='armor', subtype='heavy armor', equipped=True, slot='body_armor'),
            _starter_item('guardian_repair_straps', 'Armor Repair Straps', item_type='gear', weight=2),
            _starter_item('guardian_watch_whistle', 'Watch Whistle', item_type='gear', weight=0.1),
        ],
        'marshal': [
            _starter_item('marshal_longsword', 'Longsword', item_type='weapon', subtype='longsword', equipped=True, slot='main_hand'),
            _starter_item('marshal_shield', 'Shield', item_type='armor', subtype='shield', equipped=True, slot='off_hand'),
            _starter_item('marshal_chain_shirt', 'Chain Shirt', item_type='armor', subtype='medium armor', equipped=True, slot='body_armor', weight=20),
            _starter_item('marshal_banner', "Commander's Banner", item_type='gear', weight=3),
            _starter_item('marshal_map_case', 'Map Case', item_type='gear', weight=1),
            _starter_item('marshal_signal_whistle', 'Signal Whistle', item_type='gear', weight=0.1),
        ],
        'oracle': [
            _starter_item('oracle_quarterstaff', 'Quarterstaff', item_type='weapon', subtype='quarterstaff', equipped=True, slot='main_hand'),
            _starter_item('oracle_dagger', 'Dagger', item_type='weapon', subtype='dagger'),
            _starter_item('oracle_focus', 'Oracle Focus', item_type='focus', weight=1),
            _starter_item('oracle_incense', 'Divination Incense', quantity=3, item_type='consumable', weight=0.1),
            _starter_item('oracle_journal', 'Prophecy Journal', item_type='gear', weight=1),
        ],
        'inquisitor': [
            _starter_item('inquisitor_shortsword', 'Shortsword', item_type='weapon', subtype='shortsword', equipped=True, slot='main_hand'),
            _starter_item('inquisitor_hand_crossbow', 'Hand Crossbow', item_type='weapon', subtype='crossbow', weight=3),
            _starter_item('inquisitor_leather', 'Leather Armor', item_type='armor', subtype='light armor', equipped=True, slot='body_armor'),
            _starter_item('inquisitor_symbol', 'Holy Symbol', item_type='focus', weight=1),
            _starter_item('inquisitor_evidence', 'Evidence Pouch', item_type='gear', weight=1),
            _starter_item('inquisitor_manacles', 'Manacles', item_type='gear', weight=6),
        ],
        'warpriest': [
            _starter_item('warpriest_warhammer', 'Warhammer', item_type='weapon', subtype='warhammer', equipped=True, slot='main_hand', weight=2),
            _starter_item('warpriest_shield', 'Shield', item_type='armor', subtype='shield', equipped=True, slot='off_hand'),
            _starter_item('warpriest_chain_mail', 'Chain Mail', item_type='armor', subtype='heavy armor', equipped=True, slot='body_armor'),
            _starter_item('warpriest_symbol', 'Holy Symbol', item_type='focus', weight=1),
            _starter_item('warpriest_healers_kit', "Healer's Kit", item_type='tool', weight=3),
        ],
        'shaman': [
            _starter_item('shaman_spear', 'Spear', item_type='weapon', subtype='spear', equipped=True, slot='main_hand', weight=3),
            _starter_item('shaman_hide', 'Hide Armor', item_type='armor', subtype='medium armor', equipped=True, slot='body_armor', weight=12),
            _starter_item('shaman_spirit_fetish', 'Spirit Fetish', item_type='focus', weight=1),
            _starter_item('shaman_herb_pouch', 'Herb Pouch', item_type='tool', weight=1),
            _starter_item('shaman_ritual_mask', 'Ritual Mask', item_type='gear', weight=1),
        ],
        'witch': [
            _starter_item('witch_dagger', 'Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='main_hand'),
            _starter_item('witch_patron_focus', 'Patron Focus', item_type='focus', weight=1),
            _starter_item('witch_herb_pouch', 'Herb Pouch', item_type='tool', weight=1),
            _starter_item('witch_ritual_candles', 'Ritual Candles', quantity=5, item_type='consumable', weight=0.1),
            _starter_item('witch_familiar_token', 'Familiar Token', item_type='gear', weight=0.1),
        ],
        'magus': [
            _starter_item('magus_longsword', 'Longsword', item_type='weapon', subtype='longsword', equipped=True, slot='main_hand'),
            _starter_item('magus_leather', 'Leather Armor', item_type='armor', subtype='light armor', equipped=True, slot='body_armor'),
            _starter_item('magus_arcane_focus', 'Arcane Focus', item_type='focus', weight=1),
            _starter_item('magus_component_pouch', 'Component Pouch', item_type='focus', weight=2),
            _starter_item('magus_spell_notes', 'Spell Notes', item_type='gear', weight=1),
        ],
        'summoner': [
            _starter_item('summoner_dagger', 'Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='main_hand'),
            _starter_item('summoner_focus', 'Summoning Focus', item_type='focus', weight=1),
            _starter_item('summoner_eidolon_token', 'Eidolon Bond Token', item_type='gear', weight=0.1),
            _starter_item('summoner_chalk', 'Ritual Chalk', item_type='consumable', weight=0.2),
            _starter_item('summoner_pack', "Scholar's Pack", item_type='gear', weight=5),
        ],
        'elementalist': [
            _starter_item('elementalist_quarterstaff', 'Quarterstaff', item_type='weapon', subtype='quarterstaff', equipped=True, slot='main_hand'),
            _starter_item('elementalist_focus', 'Elemental Focus', item_type='focus', weight=1),
            _starter_item('elementalist_resistant_cloak', 'Weatherproof Cloak', item_type='clothing', equipped=True, slot='cloak', weight=2),
            _starter_item('elementalist_sample_vials', 'Element Sample Vials', quantity=4, item_type='tool', weight=0.5),
            _starter_item('elementalist_pack', "Explorer's Pack", item_type='gear', weight=5),
        ],
        'warden': [
            _starter_item('warden_spear', 'Spear', item_type='weapon', subtype='spear', equipped=True, slot='main_hand', weight=3),
            _starter_item('warden_shield', 'Shield', item_type='armor', subtype='shield', equipped=True, slot='off_hand'),
            _starter_item('warden_hide', 'Hide Armor', item_type='armor', subtype='medium armor', equipped=True, slot='body_armor', weight=12),
            _starter_item('warden_survival_kit', 'Survival Kit', item_type='tool', weight=4),
            _starter_item('warden_territory_token', 'Territory Token', item_type='gear', weight=0.1),
        ],
        'beastmaster': [
            _starter_item('beastmaster_shortbow', 'Shortbow', item_type='weapon', subtype='shortbow', equipped=True, slot='two_hands', weight=2),
            _starter_item('beastmaster_shortsword', 'Shortsword', item_type='weapon', subtype='shortsword'),
            _starter_item('beastmaster_leather', 'Leather Armor', item_type='armor', subtype='light armor', equipped=True, slot='body_armor'),
            _starter_item('beastmaster_whistle', 'Companion Whistle', item_type='gear', weight=0.1),
            _starter_item('beastmaster_feed', 'Animal Feed', quantity=3, item_type='consumable', weight=2),
        ],
        'shapeshifter': [
            _starter_item('shapeshifter_quarterstaff', 'Quarterstaff', item_type='weapon', subtype='quarterstaff', equipped=True, slot='main_hand'),
            _starter_item('shapeshifter_hide', 'Hide Armor', item_type='armor', subtype='medium armor', equipped=True, slot='body_armor', weight=12),
            _starter_item('shapeshifter_moon_charm', 'Moon Charm', item_type='focus', weight=0.1),
            _starter_item('shapeshifter_form_wraps', 'Adaptive Wraps', item_type='clothing', equipped=True, slot='clothing', weight=2),
            _starter_item('shapeshifter_pack', "Explorer's Pack", item_type='gear', weight=5),
        ],
        'psychic': [
            _starter_item('psychic_dagger', 'Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='main_hand'),
            _starter_item('psychic_crystal', 'Crystal Focus', item_type='focus', weight=1),
            _starter_item('psychic_journal', 'Dream Journal', item_type='gear', weight=1),
            _starter_item('psychic_headwrap', 'Meditation Headwrap', item_type='clothing', equipped=True, slot='helmet', weight=0.2),
            _starter_item('psychic_pack', "Scholar's Pack", item_type='gear', weight=5),
        ],
        'psion': [
            _starter_item('psion_crystal_blade', 'Crystal Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='main_hand', weight=1),
            _starter_item('psion_focus', 'Psionic Focus', item_type='focus', weight=1),
            _starter_item('psion_discipline_cards', 'Discipline Cards', item_type='gear', weight=0.5),
            _starter_item('psion_meditation_mat', 'Meditation Mat', item_type='gear', weight=2),
        ],
        'medium': [
            _starter_item('medium_dagger', 'Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='main_hand'),
            _starter_item('medium_spirit_board', 'Spirit Board', item_type='focus', weight=2),
            _starter_item('medium_candles', 'Ritual Candles', quantity=5, item_type='consumable', weight=0.1),
            _starter_item('medium_veil', 'Channeling Veil', item_type='clothing', equipped=True, slot='hood', weight=0.5),
        ],
        'occultist': [
            _starter_item('occultist_dagger', 'Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='main_hand'),
            _starter_item('occultist_relic_case', 'Relic Case', item_type='focus', weight=2),
            _starter_item('occultist_chalk', 'Ritual Chalk', item_type='consumable', weight=0.2),
            _starter_item('occultist_catalog', 'Artifact Catalog', item_type='gear', weight=2),
            _starter_item('occultist_gloves', 'Handling Gloves', item_type='clothing', equipped=True, slot='hands', weight=0.5),
        ],
        'mesmerist': [
            _starter_item('mesmerist_dagger', 'Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='main_hand'),
            _starter_item('mesmerist_watch', 'Hypnotic Focus', item_type='focus', weight=0.5),
            _starter_item('mesmerist_mask', 'Stage Mask', item_type='gear', weight=0.5),
            _starter_item('mesmerist_notebook', 'Suggestion Notebook', item_type='gear', weight=1),
        ],
        'necromancer': [
            _starter_item('necromancer_dagger', 'Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='main_hand'),
            _starter_item('necromancer_bone_focus', 'Bone Focus', item_type='focus', weight=1),
            _starter_item('necromancer_spellbook', 'Gravebound Spellbook', item_type='focus', weight=3),
            _starter_item('necromancer_grave_salt', 'Grave Salt', quantity=3, item_type='consumable', weight=0.2),
            _starter_item('necromancer_black_robe', 'Black Robe', item_type='clothing', equipped=True, slot='clothing', weight=4),
        ],
        'alchemist': [
            _starter_item('alchemist_dagger', 'Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='main_hand'),
            _starter_item('alchemist_supplies', "Alchemist's Supplies", item_type='tool', weight=8),
            _starter_item('alchemist_bomb_vials', 'Bomb Vials', quantity=3, item_type='consumable', weight=0.5),
            _starter_item('alchemist_antitoxin', 'Antitoxin', quantity=2, item_type='consumable', weight=0.5),
            _starter_item('alchemist_formula_book', 'Formula Book', item_type='gear', weight=3),
        ],
        'investigator': [
            _starter_item('investigator_rapier', 'Rapier', item_type='weapon', subtype='rapier', equipped=True, slot='main_hand'),
            _starter_item('investigator_leather', 'Leather Armor', item_type='armor', subtype='light armor', equipped=True, slot='body_armor'),
            _starter_item('investigator_tools', "Investigator's Kit", item_type='tool', weight=4),
            _starter_item('investigator_notebook', 'Case Notebook', item_type='gear', weight=1),
            _starter_item('investigator_lens', 'Magnifying Lens', item_type='gear', weight=0.1),
        ],
        'merchant': [
            _starter_item('merchant_cane', 'Weighted Cane', item_type='weapon', subtype='club', equipped=True, slot='main_hand', weight=2),
            _starter_item('merchant_fine_clothes', 'Fine Clothes', item_type='clothing', equipped=True, slot='clothing', weight=6),
            _starter_item('merchant_ledger', 'Merchant Ledger', item_type='gear', weight=2),
            _starter_item('merchant_scale', "Merchant's Scale", item_type='tool', weight=3),
            _starter_item('merchant_lockbox', 'Small Lockbox', item_type='gear', weight=5),
        ],
        'blood_hunter': [
            _starter_item('blood_hunter_longsword', 'Longsword', item_type='weapon', subtype='longsword', equipped=True, slot='main_hand'),
            _starter_item('blood_hunter_crossbow', 'Light Crossbow', item_type='weapon', subtype='crossbow', weight=5),
            _starter_item('blood_hunter_leather', 'Leather Armor', item_type='armor', subtype='light armor', equipped=True, slot='body_armor'),
            _starter_item('blood_hunter_monster_kit', "Monster Hunter's Kit", item_type='tool', weight=4),
            _starter_item('blood_hunter_silver_oil', 'Silvering Oil', quantity=2, item_type='consumable', weight=0.5),
        ],
        'mystic_theurge': [
            _starter_item('theurge_quarterstaff', 'Quarterstaff', item_type='weapon', subtype='quarterstaff', equipped=True, slot='main_hand'),
            _starter_item('theurge_holy_symbol', 'Holy Symbol', item_type='focus', weight=1),
            _starter_item('theurge_arcane_focus', 'Arcane Focus', item_type='focus', weight=1),
            _starter_item('theurge_prayer_book', 'Prayer Book', item_type='gear', weight=3),
            _starter_item('theurge_component_pouch', 'Component Pouch', item_type='focus', weight=2),
        ],
        'skald': [
            _starter_item('skald_battleaxe', 'Battleaxe', item_type='weapon', subtype='battleaxe', equipped=True, slot='main_hand', weight=4),
            _starter_item('skald_shield', 'Shield', item_type='armor', subtype='shield', equipped=True, slot='off_hand'),
            _starter_item('skald_hide', 'Hide Armor', item_type='armor', subtype='medium armor', equipped=True, slot='body_armor', weight=12),
            _starter_item('skald_war_drum', 'War Drum', item_type='tool', weight=3),
            _starter_item('skald_saga_book', 'Saga Book', item_type='gear', weight=2),
        ],
        'shadowblade': [
            _starter_item('shadowblade_shortsword', 'Shortsword', item_type='weapon', subtype='shortsword', equipped=True, slot='main_hand'),
            _starter_item('shadowblade_dagger', 'Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='off_hand'),
            _starter_item('shadowblade_leather', 'Leather Armor', item_type='armor', subtype='light armor', equipped=True, slot='body_armor'),
            _starter_item('shadowblade_tools', "Thieves' Tools", item_type='tool', weight=1),
            _starter_item('shadowblade_cloak', 'Shadow Cloak', item_type='clothing', equipped=True, slot='cloak', weight=2),
        ],
        'dragon_disciple': [
            _starter_item('dragon_disciple_scimitar', 'Scimitar', item_type='weapon', subtype='scimitar', equipped=True, slot='main_hand'),
            _starter_item('dragon_disciple_scale_mail', 'Scale Mail', item_type='armor', subtype='medium armor', equipped=True, slot='body_armor', weight=45),
            _starter_item('dragon_disciple_scale_focus', 'Drake-Scale Focus', item_type='focus', weight=1),
            _starter_item('dragon_disciple_claw_wraps', 'Claw Wraps', item_type='gear', weight=1),
        ],
        'rune_knight': [
            _starter_item('rune_knight_warhammer', 'Warhammer', item_type='weapon', subtype='warhammer', equipped=True, slot='main_hand', weight=2),
            _starter_item('rune_knight_shield', 'Shield', item_type='armor', subtype='shield', equipped=True, slot='off_hand'),
            _starter_item('rune_knight_chain_mail', 'Chain Mail', item_type='armor', subtype='heavy armor', equipped=True, slot='body_armor'),
            _starter_item('rune_knight_chisels', 'Rune-Carving Tools', item_type='tool', weight=2),
            _starter_item('rune_knight_rune_stones', 'Blank Rune Stones', quantity=5, item_type='gear', weight=0.2),
        ],
        'technomancer': [
            _starter_item('technomancer_sidearm', 'Sidearm', item_type='weapon', subtype='firearm', equipped=True, slot='main_hand', weight=3),
            _starter_item('technomancer_jacket', 'Armored Jacket', item_type='armor', subtype='light armor', equipped=True, slot='body_armor', weight=6),
            _starter_item('technomancer_deck', 'Spell Deck', item_type='focus', weight=2),
            _starter_item('technomancer_repair_kit', 'Electronics Repair Kit', item_type='tool', weight=4),
            _starter_item('technomancer_battery', 'Spare Battery', quantity=2, item_type='consumable', weight=0.5),
        ],
        'engineer': [
            _starter_item('engineer_wrench', 'Heavy Wrench', item_type='weapon', subtype='club', equipped=True, slot='main_hand', weight=2),
            _starter_item('engineer_work_clothes', 'Work Clothes', item_type='clothing', equipped=True, slot='clothing', weight=4),
            _starter_item('engineer_toolkit', "Engineer's Toolkit", item_type='tool', weight=10),
            _starter_item('engineer_repair_kit', 'Repair Kit', item_type='tool', weight=5),
            _starter_item('engineer_flashlight', 'Flashlight', item_type='gear', weight=1),
        ],
        'medic': [
            _starter_item('medic_shears', 'Trauma Shears', item_type='tool', weight=0.2),
            _starter_item('medic_kit', "Healer's Kit", item_type='tool', weight=3),
            _starter_item('medic_bag', 'Medical Bag', item_type='gear', weight=5),
            _starter_item('medic_clothes', "Traveler's Clothes", item_type='clothing', equipped=True, slot='clothing', weight=4),
            _starter_item('medic_antiseptic', 'Antiseptic', quantity=2, item_type='consumable', weight=0.5),
        ],
        'pilot': [
            _starter_item('pilot_jacket', 'Flight Jacket', item_type='armor', subtype='light armor', equipped=True, slot='body_armor', weight=4),
            _starter_item('pilot_multitool', 'Multitool', item_type='tool', weight=1),
            _starter_item('pilot_nav_kit', 'Navigation Kit', item_type='tool', weight=3),
            _starter_item('pilot_radio', 'Radio Headset', item_type='gear', weight=1),
            _starter_item('pilot_vehicle_key', 'Vehicle Key', item_type='gear', weight=0.1),
        ],
        'business_professional': [
            _starter_item('business_clothes', 'Professional Clothes', item_type='clothing', equipped=True, slot='clothing', weight=4),
            _starter_item('business_laptop', 'Laptop', item_type='tool', weight=3),
            _starter_item('business_phone', 'Smartphone', item_type='gear', weight=0.5),
            _starter_item('business_briefcase', 'Briefcase', item_type='gear', weight=3),
            _starter_item('business_cards', 'Business Card Case', item_type='gear', weight=0.2),
        ],
        'entertainer': [
            _starter_item('entertainer_stage_outfit', 'Stage Outfit', item_type='clothing', equipped=True, slot='clothing', weight=4),
            _starter_item('entertainer_performance_kit', 'Performance Kit', item_type='tool', weight=3),
            _starter_item('entertainer_makeup', 'Makeup Kit', item_type='tool', weight=1),
            _starter_item('entertainer_phone', 'Smartphone', item_type='gear', weight=0.5),
            _starter_item('entertainer_cash_pouch', 'Cash Pouch', item_type='gear', weight=0.2),
        ],
        'public_safety_officer': [
            _starter_item('public_safety_baton', 'Baton', item_type='weapon', subtype='club', equipped=True, slot='main_hand', weight=2),
            _starter_item('public_safety_vest', 'Protective Vest', item_type='armor', subtype='vest', equipped=True, slot='body_armor', weight=8),
            _starter_item('public_safety_radio', 'Radio', item_type='gear', weight=1),
            _starter_item('public_safety_flashlight', 'Flashlight', item_type='gear', weight=1),
            _starter_item('public_safety_duty_belt', 'Duty Belt', item_type='gear', weight=2),
        ],
        'medical_professional': [
            _starter_item('medical_scrubs', 'Scrubs', item_type='clothing', equipped=True, slot='clothing', weight=2),
            _starter_item('medical_bag', 'Medical Bag', item_type='gear', weight=5),
            _starter_item('medical_diagnostic_kit', 'Diagnostic Kit', item_type='tool', weight=3),
            _starter_item('medical_tablet', 'Medical Tablet', item_type='tool', weight=1),
            _starter_item('medical_gloves', 'Disposable Gloves', quantity=10, item_type='consumable', weight=0.1),
        ],
        'legal_professional': [
            _starter_item('legal_suit', 'Professional Suit', item_type='clothing', equipped=True, slot='clothing', weight=4),
            _starter_item('legal_briefcase', 'Legal Briefcase', item_type='gear', weight=3),
            _starter_item('legal_case_files', 'Case Files', item_type='gear', weight=2),
            _starter_item('legal_laptop', 'Laptop', item_type='tool', weight=3),
            _starter_item('legal_recorder', 'Voice Recorder', item_type='gear', weight=0.5),
        ],
        'media_professional': [
            _starter_item('media_jacket', 'Press Jacket', item_type='clothing', equipped=True, slot='clothing', weight=3),
            _starter_item('media_camera', 'Camera', item_type='tool', weight=2),
            _starter_item('media_recorder', 'Audio Recorder', item_type='gear', weight=0.5),
            _starter_item('media_laptop', 'Laptop', item_type='tool', weight=3),
            _starter_item('media_press_badge', 'Press Badge', item_type='gear', weight=0.1),
        ],
        'educator': [
            _starter_item('educator_clothes', 'Practical Clothes', item_type='clothing', equipped=True, slot='clothing', weight=4),
            _starter_item('educator_laptop', 'Laptop', item_type='tool', weight=3),
            _starter_item('educator_lesson_notes', 'Lesson Notes', item_type='gear', weight=1),
            _starter_item('educator_reference_books', 'Reference Books', item_type='gear', weight=5),
            _starter_item('educator_marker_set', 'Marker Set', item_type='tool', weight=0.5),
        ],
        'service_worker': [
            _starter_item('service_uniform', 'Work Uniform', item_type='clothing', equipped=True, slot='clothing', weight=3),
            _starter_item('service_multitool', 'Multitool', item_type='tool', weight=1),
            _starter_item('service_phone', 'Smartphone', item_type='gear', weight=0.5),
            _starter_item('service_keyring', 'Keyring', item_type='gear', weight=0.2),
            _starter_item('service_cash_apron', 'Cash Apron', item_type='gear', weight=1),
        ],
        'tradesperson': [
            _starter_item('trades_work_clothes', 'Work Clothes', item_type='clothing', equipped=True, slot='clothing', weight=4),
            _starter_item('trades_tool_belt', 'Tool Belt', item_type='tool', weight=8),
            _starter_item('trades_utility_knife', 'Utility Knife', item_type='weapon', subtype='knife', equipped=True, slot='main_hand', weight=1),
            _starter_item('trades_flashlight', 'Flashlight', item_type='gear', weight=1),
            _starter_item('trades_work_gloves', 'Work Gloves', item_type='clothing', equipped=True, slot='hands', weight=0.5),
        ],
        'street_operator': [
            _starter_item('street_knife', 'Knife', item_type='weapon', subtype='knife', equipped=True, slot='main_hand', weight=1),
            _starter_item('street_dark_clothes', 'Dark Hoodie', item_type='clothing', equipped=True, slot='clothing', weight=3),
            _starter_item('street_burner_phone', 'Burner Phone', item_type='gear', weight=0.5),
            _starter_item('street_lockpicks', 'Lockpicks', item_type='tool', weight=0.5),
            _starter_item('street_cash_stash', 'Cash Stash', item_type='gear', weight=0.2),
        ],
    }
)

KEYWORD_KITS: tuple[tuple[tuple[str, ...], str], ...] = (
    (('archer', 'sniper', 'bow'), 'ranger'),
    (('gunslinger', 'firearm', 'pistol', 'rifle'), 'gunslinger'),
    (('alchemist', 'artificer', 'engineer', 'inventor', 'smith', 'technomancer'), 'artificer'),
    (('healer', 'oracle', 'priest', 'shaman', 'warpriest', 'inquisitor'), 'cleric'),
    (('mage', 'witch', 'arcanist', 'psion', 'magus', 'summoner', 'elementalist', 'psychic', 'medium', 'occultist', 'mesmerist', 'necromancer', 'mystic', 'dragon'), 'wizard'),
    (('knight', 'warden', 'guardian', 'soldier', 'warrior', 'cavalier'), 'fighter'),
    (('beastmaster', 'shapeshifter'), 'druid'),
    (('assassin', 'scout', 'thief', 'ninja', 'swashbuckler', 'shadowblade', 'investigator', 'blood hunter'), 'rogue'),
    (('scholar', 'educator'), 'scholar'),
    (('noble', 'merchant', 'marshal'), 'noble'),
    (('medic',), 'medic'),
    (('medical', 'doctor', 'nurse', 'surgeon', 'paramedic'), 'medical_professional'),
    (('pilot',), 'pilot'),
    (('public safety', 'guard', 'security', 'police', 'firefighter', 'emt'), 'public_safety_officer'),
    (('operative',), 'operative'),
    (('business', 'executive', 'accountant', 'salesperson', 'investor'), 'business_professional'),
    (('legal', 'lawyer', 'attorney', 'judge', 'paralegal'), 'legal_professional'),
    (('media', 'journalist', 'reporter', 'photographer', 'streamer'), 'media_professional'),
    (('educator', 'teacher', 'professor', 'librarian'), 'educator'),
    (('service worker', 'barista', 'cashier', 'server'), 'service_worker'),
    (('tradesperson', 'electrician', 'plumber', 'welder', 'mechanic'), 'tradesperson'),
    (('street operator', 'hustler', 'criminal', 'fixer', 'informant'), 'street_operator'),
    (('professional',), 'professional'),
    (('entertainer', 'skald'), 'entertainer'),
)
HEALERS_KIT_CLASSES = frozenset({'cleric', 'druid'})


def _normalize_text(value: str | None) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


def _normalize_class_text(class_name: str | None) -> str:
    base = str(class_name or '').split('-', 1)[0]
    return _normalize_text(base)


def _normalize_subclass_text(class_name: str | None) -> str:
    parts = str(class_name or '').split('-', 1)
    if len(parts) < 2:
        return ''
    return _normalize_text(parts[1])


def _has_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _has_any_word_or_phrase(text: str, terms: tuple[str, ...]) -> bool:
    tokens = set(text.split())
    return any(term in text if ' ' in term else term in tokens for term in terms)


def _starter_kit_key(class_name: str | None) -> str | None:
    normalized = _normalize_class_text(class_name)
    if not normalized:
        return None
    compact = normalized.replace(' ', '_')
    if compact in STARTER_KITS:
        return compact
    tokens = set(normalized.split())
    for keywords, kit_key in KEYWORD_KITS:
        if any(keyword in tokens or keyword in normalized for keyword in keywords):
            return kit_key
    return None


def _merge_item(items: list[dict[str, Any]], item: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(item)
    normalized_name = _normalize_text(str(item.get('name') or ''))
    for existing in items:
        if _normalize_text(str(existing.get('name') or '')) != normalized_name:
            continue
        existing_quantity = int(existing.get('quantity') or 1)
        item_quantity = int(item.get('quantity') or 1)
        existing.update(item)
        existing['quantity'] = max(existing_quantity, item_quantity)
        return existing
    items.append(item)
    return item


def _resolve_equipped_conflicts(items: list[dict[str, Any]], equipped_item: dict[str, Any]) -> None:
    if not equipped_item.get('equipped'):
        return
    target_slots = occupied_slots(equipped_item)
    if not target_slots:
        return
    for item in items:
        if item is equipped_item or not item.get('equipped'):
            continue
        if occupied_slots(item) & target_slots:
            item['equipped'] = False


def _apply_subclass_items(items: list[dict[str, Any]], additions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for addition in additions:
        merged = _merge_item(items, addition)
        _resolve_equipped_conflicts(items, merged)
    return items


def _subclass_starting_items(kit_key: str, class_name: str | None) -> list[dict[str, Any]]:
    subclass = _normalize_subclass_text(class_name)
    if not subclass:
        return []
    base = _normalize_class_text(class_name)
    text = f'{base} {subclass}'.strip()
    additions: list[dict[str, Any]] = []

    def add(*items: dict[str, Any]) -> None:
        additions.extend(items)

    if _has_any(text, ('archer', 'sniper', 'marksman', 'musket', 'rifle', 'shooter', 'hawk bonded')):
        if kit_key in {'gunslinger', 'operative', 'technomancer', 'public_safety_officer'} or _has_any(text, ('musket', 'rifle', 'gun', 'firearm')):
            add(
                _starter_item('subclass_marksman_rifle', 'Long Rifle', item_type='weapon', subtype='firearm', equipped=True, slot='two_hands', weight=8),
                _starter_item('subclass_marksman_ammo', 'Ammunition', quantity=30, item_type='ammo', weight=0.05),
                _starter_item('subclass_marksman_scope', 'Scope', item_type='gear', weight=1),
            )
        else:
            add(
                _starter_item('subclass_archer_longbow', 'Longbow', item_type='weapon', subtype='longbow', equipped=True, slot='two_hands', weight=2),
                _starter_item('subclass_archer_arrows', 'Arrow', quantity=20, item_type='ammo', weight=0.05),
                _starter_item('subclass_archer_quiver', 'Quiver', item_type='gear', weight=1),
            )
            if kit_key in {'fighter', 'cavalier', 'guardian', 'marshal'}:
                add(_starter_item('subclass_archer_studded_leather', 'Studded Leather Armor', item_type='armor', subtype='light armor', equipped=True, slot='body_armor', weight=13))

    if _has_any(text, ('shield', 'bulwark', 'bodyguard', 'protection', 'protector', 'sentinel', 'iron wall')):
        add(
            _starter_item('subclass_defender_shield', 'Shield', item_type='armor', subtype='shield', equipped=True, slot='off_hand'),
            _starter_item('subclass_defender_reinforced_armor', 'Reinforced Armor', item_type='armor', subtype='heavy armor', equipped=True, slot='body_armor', weight=45),
            _starter_item('subclass_defender_repair_kit', 'Armor Repair Kit', item_type='tool', weight=3),
        )

    if _has_any(text, ('duelist', 'duel', 'fencer', 'swords', 'sword saint', 'swashbuckler', 'court duelist')):
        add(
            _starter_item('subclass_duelist_rapier', 'Rapier', item_type='weapon', subtype='rapier', equipped=True, slot='main_hand'),
            _starter_item('subclass_duelist_parrying_dagger', 'Parrying Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='off_hand', weight=1),
        )

    if _has_any(text, ('lancer', 'mounted', 'rider', 'cavalier', 'beast rider', 'mounted hunter')):
        add(
            _starter_item('subclass_mounted_lance', 'Lance', item_type='weapon', subtype='lance', equipped=True, slot='main_hand', weight=6),
            _starter_item('subclass_mounted_saddle', 'Riding Saddle', item_type='gear', weight=25),
            _starter_item('subclass_mounted_tack', 'Mount Tack', item_type='gear', weight=10),
        )

    if _has_any(text, ('beast', 'companion', 'eidolon', 'drake', 'dragon eidolon', 'wolf bonded', 'bear bonded', 'swarm friend', 'swarm caller', 'homunculist')):
        add(
            _starter_item('subclass_companion_whistle', 'Companion Whistle', item_type='gear', weight=0.1),
            _starter_item('subclass_companion_harness', 'Companion Harness', item_type='gear', weight=2),
            _starter_item('subclass_companion_feed', 'Companion Feed', quantity=3, item_type='consumable', weight=2),
        )

    if _has_any(text, ('dragon', 'drake', 'draconic', 'red dragon', 'blue dragon', 'green dragon', 'black dragon', 'white dragon', 'metallic dragon', 'gem dragon')):
        add(
            _starter_item('subclass_dragon_focus', 'Drake-Scale Focus', item_type='focus', weight=1),
            _starter_item('subclass_dragon_resistant_cloak', 'Element-Scorched Cloak', item_type='clothing', equipped=True, slot='cloak', weight=2),
        )

    if _has_any(text, ('alchemist', 'alchemy', 'bomber', 'bombardier', 'mutagen', 'toxic', 'poison', 'plague', 'grenadier')):
        add(
            _starter_item('subclass_alchemy_supplies', "Alchemist's Supplies", item_type='tool', weight=8),
            _starter_item('subclass_alchemy_vials', 'Prepared Vials', quantity=3, item_type='consumable', weight=0.5),
        )
        if _has_any(text, ('poison', 'toxic')):
            add(_starter_item('subclass_poisoners_kit', "Poisoner's Kit", item_type='tool', weight=2))

    if _has_any(text, ('healer', 'healing', 'life', 'hospitaler', 'medic', 'doctor', 'nurse', 'surgeon', 'paramedic', 'emt', 'chirurgeon', 'therapist', 'caregiver')):
        add(
            _starter_item('subclass_healer_kit', "Healer's Kit", item_type='tool', weight=3),
            _starter_item('subclass_healer_medical_bag', 'Medical Bag', item_type='gear', weight=5),
            _starter_item('subclass_healer_antiseptic', 'Antiseptic', quantity=2, item_type='consumable', weight=0.5),
        )

    if _has_any(text, ('scholar', 'knowledge', 'lore', 'scribe', 'archivist', 'professor', 'teacher', 'librarian', 'research', 'linguist', 'cartographer')):
        add(
            _starter_item('subclass_scholar_notebook', 'Research Notebook', item_type='gear', weight=1),
            _starter_item('subclass_scholar_ink', 'Ink and Pen', item_type='gear', weight=0.1),
            _starter_item('subclass_scholar_reference', 'Reference Book', item_type='gear', weight=5),
        )

    if _has_any(text, ('stealth', 'assassin', 'ninja', 'infiltrator', 'burglar', 'spy', 'smuggler', 'shadow', 'stalker', 'phantom thief', 'night assassin')):
        add(
            _starter_item('subclass_stealth_tools', "Thieves' Tools", item_type='tool', weight=1),
            _starter_item('subclass_stealth_cloak', 'Dark Cloak', item_type='clothing', equipped=True, slot='cloak', weight=2),
            _starter_item('subclass_stealth_dagger', 'Dagger', item_type='weapon', subtype='dagger', equipped=True, slot='off_hand'),
        )

    if _has_any(text, ('rune', 'forge', 'craft', 'clockwork', 'engineer', 'mechanic', 'gadget', 'trap', 'wand', 'smith', 'artillerist', 'construct')):
        add(
            _starter_item('subclass_crafter_tools', "Tinker's Tools", item_type='tool', weight=10),
            _starter_item('subclass_crafter_repair', 'Repair Kit', item_type='tool', weight=5),
        )

    if _has_any(text, ('demolition', 'bombardier', 'siege', 'explosive')):
        add(
            _starter_item('subclass_demolition_charges', 'Practice Charges', quantity=2, item_type='consumable', weight=1),
            _starter_item('subclass_demolition_fuse', 'Fuse Cord', item_type='gear', weight=1),
        )

    if _has_any(text, ('hacker', 'cyber', 'machine', 'drone', 'signal', 'spell hacker', 'technomancer')):
        add(
            _starter_item('subclass_tech_deck', 'Portable Computer', item_type='tool', weight=3),
            _starter_item('subclass_tech_drone', 'Utility Drone', item_type='gear', weight=2),
            _starter_item('subclass_tech_battery', 'Spare Battery', quantity=2, item_type='consumable', weight=0.5),
        )

    if _has_any_word_or_phrase(text, ('fire', 'wildfire', 'flame', 'phoenix', 'storm', 'tempest', 'lightning', 'thunder', 'water', 'waves', 'sea', 'ice', 'winter', 'earth', 'stone', 'wood', 'metal', 'elemental', 'lava', 'void', 'aether')):
        add(
            _starter_item('subclass_element_focus', 'Elemental Focus', item_type='focus', weight=1),
            _starter_item('subclass_element_resistant_cloak', 'Weatherproof Cloak', item_type='clothing', equipped=True, slot='cloak', weight=2),
        )

    if _has_any(text, ('death', 'grave', 'necromancer', 'undead', 'ghost', 'spirit', 'ancestor', 'haunted', 'bones', 'dirge')):
        add(
            _starter_item('subclass_spirit_focus', 'Spirit Focus', item_type='focus', weight=1),
            _starter_item('subclass_grave_salt', 'Grave Salt', quantity=3, item_type='consumable', weight=0.2),
        )

    if _has_any(text, ('occult', 'hex', 'curse', 'witch', 'fey', 'nightmare', 'hag', 'coven', 'pact', 'fiend', 'vestige', 'great old one')):
        add(
            _starter_item('subclass_occult_focus', 'Occult Focus', item_type='focus', weight=1),
            _starter_item('subclass_ritual_candles', 'Ritual Candles', quantity=5, item_type='consumable', weight=0.1),
        )

    if _has_any(text, ('psychic', 'psion', 'telepath', 'telekinetic', 'seer', 'empath', 'dream', 'mind', 'soulknife')):
        add(
            _starter_item('subclass_psionic_focus', 'Crystal Focus', item_type='focus', weight=1),
            _starter_item('subclass_psionic_journal', 'Meditation Journal', item_type='gear', weight=1),
        )

    if _has_any(text, ('travel', 'horizon', 'planar', 'courier', 'caravan', 'envoy', 'flight attendant', 'truck driver', 'park ranger')):
        add(
            _starter_item('subclass_travel_map', 'Route Map', item_type='gear', weight=1),
            _starter_item('subclass_travel_kit', "Traveler's Kit", item_type='gear', weight=5),
        )

    if _has_any(text, ('legal', 'lawyer', 'attorney', 'judge', 'paralegal', 'mediator', 'prosecutor', 'defense')):
        add(
            _starter_item('subclass_legal_case_files', 'Case Files', item_type='gear', weight=2),
            _starter_item('subclass_legal_recorder', 'Voice Recorder', item_type='gear', weight=0.5),
        )

    if _has_any(text, ('journalist', 'reporter', 'photo', 'camera', 'documentarian', 'podcast', 'streamer', 'publicist', 'editor')):
        add(
            _starter_item('subclass_media_camera', 'Camera', item_type='tool', weight=2),
            _starter_item('subclass_media_recorder', 'Audio Recorder', item_type='gear', weight=0.5),
            _starter_item('subclass_media_press_badge', 'Press Badge', item_type='gear', weight=0.1),
        )

    if _has_any(text, ('business', 'corporate', 'sales', 'accountant', 'investor', 'broker', 'consultant', 'entrepreneur', 'owner')):
        add(
            _starter_item('subclass_business_laptop', 'Laptop', item_type='tool', weight=3),
            _starter_item('subclass_business_ledger', 'Ledger', item_type='gear', weight=2),
        )

    if _has_any(text, ('performer', 'dancer', 'entertainer', 'musician', 'actor', 'comedian', 'drag', 'influencer', 'bartender', 'host', 'jester')):
        add(
            _starter_item('subclass_performer_kit', 'Performance Kit', item_type='tool', weight=3),
            _starter_item('subclass_performer_costume', 'Costume', item_type='clothing', equipped=True, slot='clothing', weight=4),
        )

    if _has_any(text, ('firefighter',)):
        add(
            _starter_item('subclass_firefighter_axe', 'Fire Axe', item_type='weapon', subtype='axe', equipped=True, slot='two_hands', weight=6),
            _starter_item('subclass_firefighter_turnout', 'Turnout Coat', item_type='armor', subtype='protective gear', equipped=True, slot='body_armor', weight=10),
            _starter_item('subclass_firefighter_rescue', 'Rescue Kit', item_type='tool', weight=6),
        )

    if _has_any(text, ('patrol officer', 'police', 'security guard', 'corrections', 'bodyguard', 'military police', 'lawkeeper')):
        add(
            _starter_item('subclass_public_safety_sidearm', 'Sidearm', item_type='weapon', subtype='firearm', equipped=True, slot='main_hand', weight=3),
            _starter_item('subclass_public_safety_radio', 'Radio', item_type='gear', weight=1),
            _starter_item('subclass_public_safety_restraints', 'Restraints', item_type='gear', weight=1),
        )

    if _has_any(text, ('cashier', 'server', 'barista', 'hotel clerk', 'cleaner', 'delivery', 'caregiver', 'call center', 'restaurant')):
        add(
            _starter_item('subclass_service_keyring', 'Keyring', item_type='gear', weight=0.2),
            _starter_item('subclass_service_notepad', 'Notepad', item_type='gear', weight=0.2),
        )

    if _has_any(text, ('electrician', 'plumber', 'construction', 'welder', 'carpenter', 'farmer', 'lineworker')):
        add(
            _starter_item('subclass_trades_tool_belt', 'Tool Belt', item_type='tool', weight=8),
            _starter_item('subclass_trades_work_gloves', 'Work Gloves', item_type='clothing', equipped=True, slot='hands', weight=0.5),
        )

    if _has_any(text, ('hustler', 'informant', 'getaway', 'fence', 'enforcer', 'con artist', 'bookie', 'graffiti')):
        add(
            _starter_item('subclass_street_burner', 'Burner Phone', item_type='gear', weight=0.5),
            _starter_item('subclass_street_cash', 'Cash Stash', item_type='gear', weight=0.2),
        )

    return additions


def _consumable_starter_items(kit_key: str) -> list[dict[str, Any]]:
    """Every kit carries a Potion of Healing; healers also carry a Healer's Kit."""

    items: list[dict[str, Any]] = [
        {
            'id': f'starter_{kit_key}_potion_healing',
            'name': 'Potion of Healing',
            'quantity': 1,
            'type': 'potion',
            'aliases': ['Healing Potion'],
            'metadata': {'effectId': 'potion_healing'},
        }
    ]
    if kit_key in HEALERS_KIT_CLASSES:
        items.append(
            {
                'id': f'starter_{kit_key}_healers_kit',
                'name': "Healer's Kit",
                'quantity': 1,
                'type': 'tool',
                'weight': 3,
                'metadata': {'effectId': 'healers_kit', 'usesRemaining': 10},
            }
        )
    return items


def starting_inventory_for_class(class_name: str | None) -> list[dict[str, Any]]:
    kit_key = _starter_kit_key(class_name)
    if not kit_key:
        return []
    items = [*deepcopy(STARTER_KITS[kit_key]), *_consumable_starter_items(kit_key)]
    items = _apply_subclass_items(items, _subclass_starting_items(kit_key, class_name))
    return inventory_payload(items)
