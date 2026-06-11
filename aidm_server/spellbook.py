from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from functools import lru_cache
from typing import Any, Iterable

from aidm_server.models import safe_json_loads
from aidm_server.race_system import find_curated_race, race_definition_from_selection, race_selection_from_json


def _normalize_text(value: Any) -> str:
    return re.sub(r'\s+', ' ', str(value or '').strip())


def _slug(value: Any) -> str:
    normalized = re.sub(r'[^a-z0-9]+', '_', str(value or '').strip().lower()).strip('_')
    return normalized or 'spell'


def _bounded_spell_level(value: Any, *, default: int = 0) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(9, parsed))


def _bounded_character_level(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return max(1, min(20, parsed))


def _spell_id(name: str) -> str:
    return f"spell_{_slug(name)}"


def _source_label(source_type: str | None, source_detail: str | None) -> str | None:
    source_type = _normalize_text(source_type).lower()
    source_detail = _normalize_text(source_detail)
    if source_type and source_detail:
        return f'{source_type}:{source_detail}'
    return source_type or source_detail or None


def spell_payload(
    name: str,
    *,
    level: int = 0,
    source_type: str | None = None,
    source_detail: str | None = None,
    description: str | None = None,
    learned_at_level: int | None = None,
    learned_from: str | None = None,
    tags: Iterable[str] | None = None,
    tradition: str | None = None,
    catalog: str | None = None,
) -> dict[str, Any]:
    spell_name = _normalize_text(name)
    payload: dict[str, Any] = {
        'id': _spell_id(spell_name),
        'name': spell_name,
        'level': _bounded_spell_level(level),
    }
    if source_type:
        payload['sourceType'] = _normalize_text(source_type).lower()
    if source_detail:
        payload['sourceDetail'] = _normalize_text(source_detail)
    source = _source_label(source_type, source_detail)
    if source:
        payload['source'] = source
        payload['sources'] = [source]
    if description:
        payload['description'] = _normalize_text(description)
    tag_list = [_normalize_text(tag).lower() for tag in (tags or []) if _normalize_text(tag)]
    if tag_list:
        payload['tags'] = list(dict.fromkeys(tag_list))
    if tradition:
        payload['tradition'] = _normalize_text(tradition).lower()
    if catalog:
        payload['catalog'] = _normalize_text(catalog).lower()
    learned_level = _bounded_character_level(learned_at_level)
    if learned_level:
        payload['learnedAtLevel'] = learned_level
    if learned_from:
        payload['learnedFrom'] = _normalize_text(learned_from)
    return payload


SpellSpec = tuple[str, int, str]


OriginalTheme = tuple[str, str, tuple[str, ...], str]
OriginalForm = tuple[str, int, tuple[str, ...], str]


ORIGINAL_MAGIC_THEMES: tuple[OriginalTheme, ...] = (
    ('amber', 'Amber', ('arcane', 'time', 'ward'), 'fossilized sunlight and preserved moments'),
    ('ash', 'Ash', ('fire', 'death', 'shadow'), 'cold ash, memory of flame, and soft ruin'),
    ('aurora', 'Aurora', ('light', 'cosmic', 'illusion'), 'curtains of many-colored skyfire'),
    ('basalt', 'Basalt', ('earth', 'ward', 'craft'), 'volcanic stone and patient pressure'),
    ('beacon', 'Beacon', ('light', 'holy', 'guidance'), 'guiding light that refuses to be hidden'),
    ('briar', 'Briar', ('nature', 'thorn', 'ranger'), 'living thorns, hooked vines, and wild borders'),
    ('brine', 'Brine', ('water', 'storm', 'travel'), 'saltwater, tide-pull, and old drowned roads'),
    ('candle', 'Candle', ('light', 'spirit', 'ward'), 'small flames that hold back worse dark'),
    ('carnival', 'Carnival', ('fey', 'illusion', 'emotion'), 'bright masks, impossible music, and fey bargains'),
    ('cedar', 'Cedar', ('nature', 'healing', 'spirit'), 'evergreen breath, resin, and sheltering woods'),
    ('clockwork', 'Clockwork', ('craft', 'time', 'metal'), 'gears, measured time, and obedient mechanisms'),
    ('cobalt', 'Cobalt', ('storm', 'metal', 'arcane'), 'blue sparks, charged metal, and clean impact'),
    ('comet', 'Comet', ('cosmic', 'fire', 'travel'), 'falling stars and long burning omens'),
    ('copper', 'Copper', ('metal', 'lightning', 'craft'), 'conductive metal, living current, and quick repairs'),
    ('coral', 'Coral', ('water', 'life', 'ward'), 'reef growth, tide pools, and armored life'),
    ('crow', 'Crow', ('shadow', 'memory', 'spirit'), 'black feathers, stolen words, and watchful omens'),
    ('crystal', 'Crystal', ('arcane', 'earth', 'light'), 'faceted stone, refracted force, and clear resonance'),
    ('dawn', 'Dawn', ('holy', 'light', 'healing'), 'first light, renewal, and broken curses'),
    ('deepbell', 'Deepbell', ('sound', 'water', 'spirit'), 'low bells heard through water and stone'),
    ('dreamglass', 'Dreamglass', ('dream', 'illusion', 'mind'), 'transparent dreams, sleeping mirrors, and soft lies'),
    ('dust', 'Dust', ('earth', 'decay', 'travel'), 'old roads, dry bones, and forgotten footprints'),
    ('echo', 'Echo', ('sound', 'memory', 'bard'), 'returning sound and remembered voices'),
    ('ember', 'Ember', ('fire', 'elemental', 'sorcerer'), 'banked heat, sparks, and stubborn flame'),
    ('feral', 'Feral', ('beast', 'nature', 'shapeshift'), 'claws, scent, hunger, and borrowed instinct'),
    ('frostlace', 'Frostlace', ('cold', 'ward', 'illusion'), 'lace-thin ice, numbing air, and white patterns'),
    ('gilded', 'Gilded', ('light', 'social', 'charm'), 'golden glamour, courtly shine, and tempting promises'),
    ('glass', 'Glass', ('illusion', 'ward', 'arcane'), 'clear barriers, reflections, and brittle edges'),
    ('gravebloom', 'Gravebloom', ('death', 'life', 'spirit'), 'flowers rooted in old graves and unfinished farewells'),
    ('harbor', 'Harbor', ('water', 'ward', 'travel'), 'safe docks, mooring lines, and foghorn calls'),
    ('honey', 'Honey', ('healing', 'nature', 'emotion'), 'sweet preservation, soothing warmth, and patient bees'),
    ('horizon', 'Horizon', ('travel', 'space', 'guidance'), 'far roads, distance, and the line where worlds meet'),
    ('ink', 'Ink', ('shadow', 'knowledge', 'bard'), 'living ink, hidden clauses, and black script'),
    ('iron', 'Iron', ('metal', 'ward', 'oath'), 'cold iron, sworn boundaries, and refusing force'),
    ('ivory', 'Ivory', ('spirit', 'memory', 'holy'), 'white relics, ancestral signs, and quiet vows'),
    ('jade', 'Jade', ('life', 'earth', 'healing'), 'green stone, steady breath, and restored balance'),
    ('lantern', 'Lantern', ('light', 'travel', 'spirit'), 'carried light, safe passage, and revealed faces'),
    ('lilac', 'Lilac', ('emotion', 'dream', 'healing'), 'gentle fragrance, nostalgia, and softened grief'),
    ('lodestone', 'Lodestone', ('metal', 'force', 'travel'), 'magnetic pull, heavy direction, and anchored paths'),
    ('loom', 'Loom', ('fate', 'craft', 'bard'), 'threads, knots, and choices woven into pattern'),
    ('marble', 'Marble', ('earth', 'holy', 'ward'), 'polished stone, temple silence, and enduring shape'),
    ('mire', 'Mire', ('poison', 'earth', 'nature'), 'mud, bog gas, rot, and hungry ground'),
    ('mirror', 'Mirror', ('illusion', 'mind', 'space'), 'reflections, doubles, and reversed angles'),
    ('moon', 'Moon', ('moon', 'dream', 'shapeshift'), 'silver light, tides, and changing forms'),
    ('mycelium', 'Mycelium', ('nature', 'death', 'mind'), 'fungal threads, buried messages, and shared decay'),
    ('obsidian', 'Obsidian', ('shadow', 'fire', 'ward'), 'black glass, volcanic edges, and sealed heat'),
    ('orchid', 'Orchid', ('nature', 'charm', 'poison'), 'lush petals, alluring scent, and hidden venom'),
    ('paper', 'Paper', ('knowledge', 'craft', 'arcane'), 'folded pages, written orders, and fragile maps'),
    ('pearl', 'Pearl', ('water', 'healing', 'light'), 'soft luster, patient oceans, and protected wounds'),
    ('phantom', 'Phantom', ('spirit', 'illusion', 'shadow'), 'half-seen bodies, chills, and unfinished presence'),
    ('quartz', 'Quartz', ('earth', 'sound', 'arcane'), 'ringing crystal, vibration, and stored pressure'),
    ('quill', 'Quill', ('knowledge', 'bard', 'fate'), 'flying script, edits, and decisive signatures'),
    ('rain', 'Rain', ('water', 'storm', 'healing'), 'falling water, washed tracks, and softened ground'),
    ('ravenous', 'Ravenous', ('beast', 'death', 'warlock'), 'hunger, teeth, and consuming dark'),
    ('redwood', 'Redwood', ('nature', 'ward', 'life'), 'ancient trees, height, and deep roots'),
    ('riddle', 'Riddle', ('mind', 'illusion', 'knowledge'), 'unsolved questions, hidden doors, and clever traps'),
    ('river', 'River', ('water', 'travel', 'fate'), 'currents, crossings, and inevitable movement'),
    ('rose', 'Rose', ('emotion', 'thorn', 'healing'), 'petals, devotion, thorns, and costly mercy'),
    ('rust', 'Rust', ('metal', 'decay', 'artificer'), 'oxidized iron, failing locks, and red dust'),
    ('saffron', 'Saffron', ('fire', 'healing', 'social'), 'warm spice, golden smoke, and restored appetite'),
    ('sapphire', 'Sapphire', ('arcane', 'water', 'mind'), 'blue clarity, still water, and precise thought'),
    ('scarab', 'Scarab', ('earth', 'life', 'death'), 'carapaces, burial charms, and returning life'),
    ('selenite', 'Selenite', ('moon', 'ward', 'spirit'), 'pale crystal, lunar calm, and quiet protection'),
    ('serpent', 'Serpent', ('poison', 'charm', 'shapeshift'), 'coils, venom, hypnotic motion, and shed skin'),
    ('shale', 'Shale', ('earth', 'sound', 'decay'), 'layered stone, brittle records, and cracking plates'),
    ('silk', 'Silk', ('illusion', 'fey', 'craft'), 'fine threads, soft bindings, and impossible cloth'),
    ('silver', 'Silver', ('moon', 'holy', 'ward'), 'moonlit metal, purity, and clean cuts through curses'),
    ('smoke', 'Smoke', ('fire', 'shadow', 'travel'), 'vanishing trails, choking haze, and hidden exits'),
    ('song', 'Song', ('sound', 'emotion', 'bard'), 'melody, courage, grief, and shared rhythm'),
    ('spindle', 'Spindle', ('fate', 'time', 'craft'), 'turning tools, measured thread, and delayed outcomes'),
    ('starling', 'Starling', ('beast', 'sound', 'travel'), 'flocking birds, mimicry, and sudden turns'),
    ('storm', 'Storm', ('storm', 'lightning', 'air'), 'thunderheads, pressure, and wild current'),
    ('sunspot', 'Sunspot', ('light', 'fire', 'holy'), 'burning halos, exposed truth, and fierce daylight'),
    ('thorn', 'Thorn', ('thorn', 'nature', 'pain'), 'barbs, brambles, and boundaries that bite'),
    ('thunderhead', 'Thunderhead', ('storm', 'sound', 'force'), 'black clouds, rolling sound, and concussive air'),
    ('tidepool', 'Tidepool', ('water', 'life', 'illusion'), 'small seas, reflected skies, and hidden creatures'),
    ('topaz', 'Topaz', ('lightning', 'light', 'arcane'), 'yellow crystal, bright charge, and sudden insight'),
    ('verdigris', 'Verdigris', ('metal', 'poison', 'time'), 'green patina, age, and beautiful corrosion'),
    ('violet', 'Violet', ('mind', 'dream', 'emotion'), 'purple haze, delicate thought, and shared feeling'),
    ('void', 'Void', ('shadow', 'space', 'warlock'), 'starless gaps, silence, and impossible absence'),
    ('wheat', 'Wheat', ('life', 'healing', 'nature'), 'grain, harvest, common meals, and survival'),
    ('whisper', 'Whisper', ('sound', 'shadow', 'mind'), 'low voices, secrets, and carried breath'),
    ('wildglass', 'Wildglass', ('chaos', 'illusion', 'sorcerer'), 'unstable reflections and beautiful accidents'),
    ('windmill', 'Windmill', ('air', 'craft', 'travel'), 'turning blades, grain dust, and useful wind'),
    ('winter', 'Winter', ('cold', 'death', 'ward'), 'still snow, preserved silence, and hard survival'),
    ('wyrm', 'Wyrm', ('dragon', 'fire', 'oath'), 'draconic breath, old pride, and scaled power'),
    ('yew', 'Yew', ('death', 'nature', 'spirit'), 'grave trees, bows, poison, and ancestral shade'),
    ('zenith', 'Zenith', ('cosmic', 'light', 'oath'), 'high noon, perfect angles, and exposed purpose'),
)


ORIGINAL_MAGIC_FORMS: tuple[OriginalForm, ...] = (
    ('Glimmer', 0, ('utility', 'sensory'), 'Create a harmless sign of {motif}.'),
    ('Whisper', 0, ('utility', 'communication'), 'Carry a brief message through {motif}.'),
    ('Trace', 0, ('utility', 'tracking'), 'Mark, reveal, or follow a faint trace through {motif}.'),
    ('Palm', 0, ('utility', 'minor'), 'Hold a tiny useful expression of {motif} in one hand.'),
    ('Knot', 0, ('utility', 'control'), 'Tie a minor magical knot shaped by {motif}.'),
    ('Spark', 0, ('damage', 'minor'), 'Release a small, precise bite of {motif}.'),
    ('Mote', 0, ('utility', 'light'), 'Set a floating mote of {motif} near you.'),
    ('Charm', 1, ('social', 'emotion'), 'Tint a social moment with {motif}.'),
    ('Veil', 1, ('illusion', 'stealth'), 'Drape a short-lived veil of {motif} over a creature or object.'),
    ('Ward', 1, ('defense', 'ward'), 'Raise a quick protective sign of {motif}.'),
    ('Lash', 1, ('damage', 'control'), 'Strike or pull a target with a lash of {motif}.'),
    ('Step', 1, ('movement', 'travel'), 'Move through a short opening created by {motif}.'),
    ('Bloom', 1, ('healing', 'creation'), 'Coax a small restorative bloom of {motif}.'),
    ('Brand', 1, ('mark', 'damage'), 'Mark a target with a lingering sign of {motif}.'),
    ('Lens', 1, ('divination', 'knowledge'), 'Study a scene through a lens of {motif}.'),
    ('Snare', 2, ('control', 'trap'), 'Catch a creature or object in a snare of {motif}.'),
    ('Mirror', 2, ('illusion', 'defense'), 'Create a misleading reflection made from {motif}.'),
    ('Mantle', 2, ('buff', 'defense'), 'Wrap a willing creature in a mantle of {motif}.'),
    ('Tether', 2, ('control', 'movement'), 'Bind two points, creatures, or ideas with {motif}.'),
    ('Door', 2, ('movement', 'space'), 'Open a brief small passage through {motif}.'),
    ('Pulse', 3, ('area', 'damage'), 'Send a wave of {motif} through the immediate area.'),
    ('Bastion', 3, ('defense', 'area'), 'Build a temporary defensive shape out of {motif}.'),
    ('Chorus', 3, ('support', 'sound'), 'Let several allies share a chorus of {motif}.'),
    ('Swarm', 3, ('summon', 'area'), 'Call a restless swarm shaped by {motif}.'),
    ('Script', 3, ('knowledge', 'ritual'), 'Write a short-lived law or instruction in {motif}.'),
    ('Rift', 4, ('space', 'damage'), 'Tear open a dangerous rift filled with {motif}.'),
    ('Crown', 4, ('buff', 'command'), 'Crown a creature with commanding {motif}.'),
    ('Engine', 4, ('craft', 'sustained'), 'Assemble a sustained magical engine powered by {motif}.'),
    ('Eidolon', 5, ('summon', 'spirit'), 'Shape an autonomous eidolon from {motif}.'),
    ('Labyrinth', 5, ('control', 'mind'), 'Trap a target or area in confusing paths of {motif}.'),
    ('Dominion', 6, ('area', 'command'), 'Claim a broad zone under the rules of {motif}.'),
    ('Vessel', 6, ('transformation', 'buff'), 'Turn a willing creature into a vessel for {motif}.'),
    ('Parliament', 7, ('summon', 'social'), 'Convene many voices, shades, or forces of {motif}.'),
    ('Mandate', 7, ('command', 'ritual'), 'Declare a powerful temporary law written in {motif}.'),
    ('Apotheosis', 8, ('transformation', 'mythic'), 'Briefly raise a creature into a mythic shape of {motif}.'),
    ('Horizon', 8, ('space', 'travel'), 'Move a group along a vast boundary of {motif}.'),
    ('Genesis', 9, ('creation', 'mythic'), 'Create a lasting miracle seeded with {motif}.'),
    ('Apocalypse', 9, ('damage', 'mythic'), 'Unleash a scene-changing catastrophe of {motif}.'),
)


ORIGINAL_ARCHETYPE_TAGS: dict[str, set[str]] = {
    'wizard': {'arcane', 'knowledge', 'time', 'space', 'force', 'illusion', 'mind', 'cosmic', 'ward', 'craft'},
    'sorcerer': {'fire', 'storm', 'lightning', 'chaos', 'dragon', 'cosmic', 'light', 'shadow', 'elemental', 'air'},
    'warlock': {'shadow', 'void', 'death', 'spirit', 'dream', 'curse', 'ravenous', 'mind', 'space'},
    'cleric': {'holy', 'healing', 'life', 'spirit', 'ward', 'light', 'death', 'truth', 'mercy'},
    'druid': {'nature', 'beast', 'shapeshift', 'earth', 'water', 'life', 'storm', 'thorn', 'moon', 'plant'},
    'bard': {'sound', 'emotion', 'illusion', 'dream', 'memory', 'fey', 'social', 'fate', 'knowledge'},
    'paladin': {'holy', 'oath', 'ward', 'light', 'sun', 'valor', 'mercy', 'iron', 'truth'},
    'ranger': {'nature', 'beast', 'travel', 'tracking', 'thorn', 'moon', 'earth', 'water', 'storm'},
    'artificer': {'craft', 'metal', 'clockwork', 'rune', 'alchemy', 'lightning', 'ward', 'force', 'knowledge'},
}


ORIGINAL_STARTER_COUNTS = {
    'wizard': 14,
    'sorcerer': 12,
    'warlock': 10,
    'cleric': 11,
    'druid': 12,
    'bard': 12,
    'paladin': 7,
    'ranger': 8,
    'artificer': 12,
}


ORIGINAL_UNLOCK_COUNTS = {
    2: 2,
    3: 4,
    5: 4,
    7: 3,
    9: 3,
    11: 2,
    13: 2,
    15: 2,
    17: 2,
}


ORIGINAL_RACE_TAGS = {
    'aasimar': {'holy', 'light', 'healing', 'spirit'},
    'changeling': {'shapeshift', 'illusion', 'social', 'mirror'},
    'dragonborn': {'dragon', 'fire', 'storm', 'oath'},
    'elf': {'arcane', 'moon', 'dream', 'nature'},
    'fairy': {'fey', 'illusion', 'nature', 'emotion'},
    'firbolg': {'nature', 'spirit', 'beast', 'ward'},
    'genasi': {'fire', 'water', 'storm', 'earth', 'air', 'elemental'},
    'gnome': {'illusion', 'craft', 'knowledge', 'arcane'},
    'satyr': {'fey', 'sound', 'emotion', 'social'},
    'saiyan': {'force', 'storm', 'light', 'cosmic', 'beast'},
    'tiefling': {'fire', 'shadow', 'social', 'oath'},
    'triton': {'water', 'storm', 'travel', 'ward'},
    'yuan-ti': {'poison', 'serpent', 'mind', 'shadow'},
}


SHAPESHIFT_TRAIT_KEYWORDS = (
    'shapechanger',
    'shapeshifter',
    'shapeshift',
    'wild shape',
    'polymorph',
    'alter your appearance',
    'alter appearance',
    'change your shape',
    'change shape',
    'reshape your body',
)

STALE_AUTO_RACE_SOURCE_DETAILS = {'shapeshifter'}


def _looks_like_shapeshifter_trait(trait_text: str) -> bool:
    return any(keyword in trait_text for keyword in SHAPESHIFT_TRAIT_KEYWORDS)


def _theme_record(theme: OriginalTheme) -> dict[str, Any]:
    key, title, tags, motif = theme
    return {'key': key, 'title': title, 'tags': set(tags), 'motif': motif}


def _form_record(form: OriginalForm) -> dict[str, Any]:
    suffix, level, tags, template = form
    return {'suffix': suffix, 'level': level, 'tags': set(tags), 'template': template}


@lru_cache(maxsize=1)
def original_spell_catalog() -> tuple[dict[str, Any], ...]:
    """Return the generated original AIDM spell catalog.

    These are intentionally non-canon names. Character sheets receive small
    deterministic subsets; the full catalog exists so future UI/DM helpers can
    search a very broad magic space without being limited to tabletop books.
    """

    spells: list[dict[str, Any]] = []
    for raw_theme in ORIGINAL_MAGIC_THEMES:
        theme = _theme_record(raw_theme)
        for raw_form in ORIGINAL_MAGIC_FORMS:
            form = _form_record(raw_form)
            name = f"{theme['title']} {form['suffix']}"
            tags = sorted({*theme['tags'], *form['tags'], 'original', 'aidm'})
            spells.append(
                spell_payload(
                    name,
                    level=int(form['level']),
                    source_type='original_catalog',
                    source_detail=str(theme['key']),
                    description=str(form['template']).format(motif=theme['motif']),
                    tags=tags,
                    tradition='aidm-original',
                    catalog='aidm-original',
                )
            )
    return tuple(spells)


def original_spell_catalog_size() -> int:
    return len(original_spell_catalog())


def _stable_rank(seed: str, spell: dict[str, Any]) -> str:
    source = f"{seed}|{spell.get('id')}|{spell.get('name')}"
    return hashlib.sha1(source.encode('utf-8')).hexdigest()


def _max_spell_level_for_character(character_level: int) -> int:
    level = max(1, min(20, int(character_level or 1)))
    return max(1, min(9, (level + 1) // 2))


def _retag_catalog_spell(spell: dict[str, Any], *, source_type: str, source_detail: str, learned_at_level: int) -> dict[str, Any]:
    payload = deepcopy(spell)
    source_type = _normalize_text(source_type).lower()
    source_detail = _normalize_text(source_detail)
    source = _source_label(source_type, source_detail)
    payload['sourceType'] = source_type
    payload['sourceDetail'] = source_detail
    if source:
        payload['source'] = source
        payload['sources'] = [source]
    payload['learnedAtLevel'] = max(1, min(20, int(learned_at_level)))
    payload['catalog'] = payload.get('catalog') or 'aidm-original'
    return payload


def _catalog_candidates(*, tags: set[str], max_spell_level: int) -> list[dict[str, Any]]:
    max_level = max(0, min(9, int(max_spell_level)))
    candidates = []
    for spell in original_spell_catalog():
        spell_tags = {str(tag or '').strip().lower() for tag in spell.get('tags') or []}
        if int(spell.get('level') or 0) <= max_level and (not tags or spell_tags & tags):
            candidates.append(spell)
    return candidates


def _take_ranked_spells(
    candidates: Iterable[dict[str, Any]],
    *,
    count: int,
    seed: str,
    seen: set[str],
) -> list[dict[str, Any]]:
    ranked = sorted(candidates, key=lambda spell: (_stable_rank(seed, spell), str(spell.get('name') or '')))
    selected: list[dict[str, Any]] = []
    for spell in ranked:
        key = _slug(spell.get('name'))
        if key in seen:
            continue
        selected.append(deepcopy(spell))
        seen.add(key)
        if len(selected) >= count:
            break
    return selected


def original_class_spells_for_level(archetype: str, character_level: int = 1) -> list[dict[str, Any]]:
    class_tags = ORIGINAL_ARCHETYPE_TAGS.get(archetype, set())
    if not class_tags:
        return []
    level = max(1, min(20, int(character_level or 1)))
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for unlock_level, count in [(1, ORIGINAL_STARTER_COUNTS.get(archetype, 8)), *ORIGINAL_UNLOCK_COUNTS.items()]:
        if unlock_level > level:
            continue
        max_spell_level = _max_spell_level_for_character(unlock_level)
        candidates = _catalog_candidates(tags=class_tags, max_spell_level=max_spell_level)
        picked = _take_ranked_spells(
            candidates,
            count=count,
            seed=f'{archetype}:{unlock_level}:aidm-original',
            seen=seen,
        )
        selected.extend(
            _retag_catalog_spell(
                spell,
                source_type='class_catalog',
                source_detail=f'{archetype}:original:{unlock_level}',
                learned_at_level=unlock_level,
            )
            for spell in picked
        )
    return selected


def _race_catalog_tags(
    *,
    race_key: str | None,
    tags: set[str],
    trait_text: str,
) -> set[str]:
    result = set(ORIGINAL_RACE_TAGS.get(str(race_key or ''), set()))
    result.update(tag for tag in tags if tag in {'magical', 'fey', 'elemental', 'celestial', 'fiendish', 'draconic', 'aquatic', 'nature'})
    if _looks_like_shapeshifter_trait(trait_text):
        result.update({'shapeshift', 'illusion', 'mirror'})
    if 'breath_weapon' in trait_text or 'elemental_cone_or_line' in trait_text:
        result.update({'dragon', 'fire', 'storm'})
    if 'elemental' in tags or 'elemental' in trait_text:
        result.update({'fire', 'water', 'storm', 'earth', 'air'})
    if 'celestial' in tags or 'minor_healing' in trait_text:
        result.update({'holy', 'light', 'healing'})
    if 'fiendish' in tags or 'infernal' in trait_text:
        result.update({'fire', 'shadow', 'social'})
    if 'aquatic' in tags or 'ocean' in trait_text:
        result.update({'water', 'storm', 'travel'})
    if 'magical' in tags:
        result.update({'arcane', 'illusion', 'ward'})
    return result


def original_race_spells(
    *,
    race_key: str | None,
    race_tags: set[str],
    trait_text: str,
    count: int = 2,
) -> list[dict[str, Any]]:
    catalog_tags = _race_catalog_tags(race_key=race_key, tags=race_tags, trait_text=trait_text)
    if not catalog_tags:
        return []
    candidates = _catalog_candidates(tags=catalog_tags, max_spell_level=1)
    seen: set[str] = set()
    picked = _take_ranked_spells(
        candidates,
        count=max(1, count),
        seed=f'race:{race_key or "custom"}:aidm-original',
        seen=seen,
    )
    return [
        _retag_catalog_spell(
            spell,
            source_type='race_catalog',
            source_detail=str(race_key or 'custom'),
            learned_at_level=1,
        )
        for spell in picked
    ]


CLASS_STARTING_SPELLS: dict[str, list[SpellSpec]] = {
    'wizard': [
        ('Fire Bolt', 0, 'A precise ranged spark of arcane fire.'),
        ('Mage Hand', 0, 'Move or manipulate a small object at a distance.'),
        ('Prestidigitation', 0, 'Create small harmless sensory magical effects.'),
        ('Magic Missile', 1, 'Reliable darts of arcane force.'),
        ('Shield', 1, 'A sudden defensive ward.'),
        ('Mage Armor', 1, 'Protective arcane armor.'),
        ('Detect Magic', 1, 'Sense nearby magic.'),
        ('Sleep', 1, 'Lull weak creatures into magical sleep.'),
        ('Burning Hands', 1, 'A short cone of flame.'),
    ],
    'sorcerer': [
        ('Fire Bolt', 0, 'A ranged blast of innate fire.'),
        ('Ray of Frost', 0, 'A chilling ranged spell that slows a target.'),
        ('Mage Hand', 0, 'Shape minor force at a distance.'),
        ('Minor Illusion', 0, 'Create a small image or sound.'),
        ('Magic Missile', 1, 'Reliable darts of force.'),
        ('Shield', 1, 'A reflexive defensive ward.'),
        ('Chaos Bolt', 1, 'Unstable elemental damage.'),
        ('Burning Hands', 1, 'A burst of close flame.'),
    ],
    'warlock': [
        ('Eldritch Blast', 0, 'A beam of pact-born force.'),
        ('Mage Hand', 0, 'Move a small object at range.'),
        ('Minor Illusion', 0, 'Create a small image or sound.'),
        ('Hex', 1, 'Curse a foe and make strikes bite harder.'),
        ('Armor of Agathys', 1, 'A cold ward that punishes attackers.'),
        ('Hellish Rebuke', 1, 'Retaliatory infernal fire.'),
        ('Charm Person', 1, 'Briefly influence a humanoid.'),
    ],
    'cleric': [
        ('Sacred Flame', 0, 'Radiant fire against a visible foe.'),
        ('Thaumaturgy', 0, 'Small divine signs and omens.'),
        ('Guidance', 0, 'A small divine boost to an ability check.'),
        ('Cure Wounds', 1, 'Touch-based healing.'),
        ('Healing Word', 1, 'Quick ranged healing.'),
        ('Bless', 1, 'Bolster allies in danger.'),
        ('Shield of Faith', 1, 'Protect an ally with divine force.'),
        ('Detect Magic', 1, 'Sense magical auras.'),
    ],
    'druid': [
        ('Druidcraft', 0, 'Small nature signs and harmless natural magic.'),
        ('Produce Flame', 0, 'Hold and throw a small flame.'),
        ('Guidance', 0, 'A small primal boost to an ability check.'),
        ('Cure Wounds', 1, 'Primal healing by touch.'),
        ('Entangle', 1, 'Grasping plants restrain an area.'),
        ('Faerie Fire', 1, 'Outline creatures in revealing light.'),
        ('Speak with Animals', 1, 'Communicate with beasts.'),
        ('Goodberry', 1, 'Create nourishing healing berries.'),
        ('Primal Shift', 1, 'Brief shapeshifting or bestial adaptation when the story supports it.'),
    ],
    'bard': [
        ('Vicious Mockery', 0, 'Cutting words backed by enchantment.'),
        ('Minor Illusion', 0, 'Create a small image or sound.'),
        ('Mage Hand', 0, 'Manipulate a small object at range.'),
        ('Healing Word', 1, 'Quick inspirational healing.'),
        ('Dissonant Whispers', 1, 'Psychic fear through sound.'),
        ('Faerie Fire', 1, 'Reveal targets in magical light.'),
        ('Charm Person', 1, 'Briefly influence a humanoid.'),
        ('Thunderwave', 1, 'A concussive burst of sound.'),
    ],
    'paladin': [
        ('Divine Sense', 0, 'Sense holy, fiendish, or undead presence nearby.'),
        ('Lay on Hands', 1, 'Restore health through divine power.'),
        ('Bless', 1, 'Bolster allies in danger.'),
        ('Cure Wounds', 1, 'Touch-based healing.'),
        ('Shield of Faith', 1, 'Protect an ally with divine force.'),
        ('Divine Smite', 1, 'Channel divine power through a weapon hit.'),
    ],
    'ranger': [
        ("Hunter's Mark", 1, 'Mark prey and track strikes against it.'),
        ('Cure Wounds', 1, 'Practical wilderness healing.'),
        ('Speak with Animals', 1, 'Communicate with beasts.'),
        ('Entangle', 1, 'Use terrain and growth to restrain foes.'),
        ('Goodberry', 1, 'Create nourishing healing berries.'),
    ],
    'artificer': [
        ('Mending', 0, 'Repair a small break or tear.'),
        ('Mage Hand', 0, 'Manipulate a small object at range.'),
        ('Cure Wounds', 1, 'Magical field repair for living allies.'),
        ('Faerie Fire', 1, 'Reveal targets with glittering light.'),
        ('Grease', 1, 'Make a slick hazardous surface.'),
        ('Arcane Tinkering', 0, 'Imbue a tiny object with a minor magical effect.'),
    ],
}


CLASS_LEVEL_UNLOCKS: dict[str, dict[int, list[SpellSpec]]] = {
    'wizard': {
        2: [('Identify', 1, 'Learn the nature of magic objects and effects.')],
        3: [('Misty Step', 2, 'Teleport a short distance you can see.'), ('Scorching Ray', 2, 'Fire several rays of flame.')],
        5: [('Counterspell', 3, 'Interrupt another spell.'), ('Fireball', 3, 'A large explosive blast of fire.')],
        7: [('Dimension Door', 4, 'Teleport yourself and one companion farther away.')],
        9: [('Wall of Force', 5, 'Create a nearly unbreakable barrier of force.')],
        11: [('Disintegrate', 6, 'A dangerous beam of destructive force.')],
        13: [('Teleport', 7, 'Travel instantly across great distances.')],
        15: [('Sunburst', 8, 'A brilliant burst of radiant power.')],
        17: [('Wish', 9, 'Reality-bending high magic.')],
    },
    'sorcerer': {
        3: [('Misty Step', 2, 'Teleport a short distance.'), ('Shatter', 2, 'A burst of destructive sound.')],
        5: [('Fireball', 3, 'A large explosive blast of fire.'), ('Haste', 3, 'Supercharge a willing creature.')],
        7: [('Greater Invisibility', 4, 'Turn a creature invisible through battle.')],
        9: [('Telekinesis', 5, 'Move creatures or objects with sustained force.')],
        11: [('Chain Lightning', 6, 'Lightning arcs between multiple targets.')],
        13: [('Teleport', 7, 'Travel instantly across great distances.')],
        15: [('Power Word Stun', 8, 'Stun a weakened creature with a word.')],
        17: [('Meteor Swarm', 9, 'Call down devastating meteors.')],
    },
    'warlock': {
        3: [('Misty Step', 2, 'Teleport a short distance.'), ('Mirror Image', 2, 'Create illusory duplicates.')],
        5: [('Counterspell', 3, 'Interrupt another spell.'), ('Hunger of Hadar', 3, 'Open a region of alien dark cold.')],
        7: [('Banishment', 4, 'Send a target out of the scene briefly.')],
        9: [('Hold Monster', 5, 'Paralyze a creature with pact magic.')],
        11: [('Circle of Death', 6, 'A wide burst of necrotic power.')],
        13: [('Finger of Death', 7, 'A lethal necromantic attack.')],
        15: [('Maddening Darkness', 8, 'Summon supernatural darkness and psychic pain.')],
        17: [('Foresight', 9, 'Grant uncanny luck and awareness.')],
    },
    'cleric': {
        3: [('Lesser Restoration', 2, 'End a lesser affliction.'), ('Spiritual Weapon', 2, 'Manifest a divine weapon.')],
        5: [('Revivify', 3, 'Restore a recently dead ally.'), ('Spirit Guardians', 3, 'Call protective spirits around you.')],
        7: [('Death Ward', 4, 'Protect an ally from dropping to death.')],
        9: [('Greater Restoration', 5, 'End a major affliction.'), ('Flame Strike', 5, 'Call down divine fire.')],
        11: [('Heal', 6, 'Massive restorative magic.')],
        13: [('Resurrection', 7, 'Return a dead creature to life.')],
        15: [('Holy Aura', 8, 'Radiant protection for nearby allies.')],
        17: [('Mass Heal', 9, 'Restore many allies at once.')],
    },
    'druid': {
        2: [('Wild Shape', 1, 'Take a beast form when the story supports it.')],
        3: [('Moonbeam', 2, 'Call down a radiant column.'), ('Pass without Trace', 2, 'Hide the group in nature magic.')],
        5: [('Call Lightning', 3, 'Command repeated lightning strikes.'), ('Plant Growth', 3, 'Shape or overgrow terrain.')],
        7: [('Polymorph', 4, 'Transform a creature into another form.')],
        9: [('Greater Restoration', 5, 'End a major affliction.'), ('Tree Stride', 5, 'Step through trees.')],
        11: [('Transport via Plants', 6, 'Travel through living plants.')],
        13: [('Regenerate', 7, 'Restore severe injuries over time.')],
        15: [('Animal Shapes', 8, 'Transform willing allies into beasts.')],
        17: [('Shapechange', 9, 'Assume powerful magical forms.')],
    },
    'bard': {
        3: [('Invisibility', 2, 'Hide a creature from sight.'), ('Suggestion', 2, 'Magically influence a course of action.')],
        5: [('Hypnotic Pattern', 3, 'Mesmerize creatures with light.'), ('Dispel Magic', 3, 'End a magical effect.')],
        7: [('Greater Invisibility', 4, 'Turn a creature invisible through battle.')],
        9: [('Hold Monster', 5, 'Paralyze a creature.'), ('Synaptic Static', 5, 'A psychic blast that muddles minds.')],
        11: [('Mass Suggestion', 6, 'Influence many creatures at once.')],
        13: [('Teleport', 7, 'Travel instantly across great distances.')],
        15: [('Power Word Stun', 8, 'Stun a weakened creature with a word.')],
        17: [('Power Word Heal', 9, 'Restore an ally with a word.')],
    },
    'paladin': {
        3: [('Lesser Restoration', 2, 'End a lesser affliction.'), ('Magic Weapon', 2, 'Empower a weapon.')],
        5: [('Aura of Vitality', 3, 'Sustain healing through divine focus.')],
        9: [('Death Ward', 4, 'Protect an ally from dropping to death.')],
        13: [('Banishing Smite', 5, 'Strike with banishing force.')],
    },
    'ranger': {
        3: [('Pass without Trace', 2, 'Hide the group in wilderness magic.'), ('Spike Growth', 2, 'Turn ground into painful terrain.')],
        5: [('Conjure Barrage', 3, 'Create a sweeping ranged attack.'), ('Water Breathing', 3, 'Let allies breathe underwater.')],
        9: [('Freedom of Movement', 4, 'Ignore many restraints and movement hindrances.')],
        13: [('Swift Quiver', 5, 'Attack rapidly with enchanted ammunition.')],
    },
    'artificer': {
        3: [('Heat Metal', 2, 'Overheat worked metal.'), ('Web', 2, 'Fill an area with sticky strands.')],
        5: [('Dispel Magic', 3, 'End a magical effect.'), ('Haste', 3, 'Supercharge a willing creature.')],
        7: [('Fabricate', 4, 'Convert raw materials into crafted objects.')],
        9: [('Animate Objects', 5, 'Bring objects to life briefly.')],
    },
}


CLASS_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (('wizard', 'mage', 'witch', 'arcanist', 'psion', 'magus', 'summoner', 'elementalist', 'psychic', 'medium', 'occultist', 'mesmerist', 'necromancer', 'mystic', 'spellblade', 'spell hacker'), 'wizard'),
    (('sorcerer', 'dragon blood', 'wild magic'), 'sorcerer'),
    (('warlock', 'pact', 'hexblade'), 'warlock'),
    (('cleric', 'healer', 'oracle', 'priest', 'shaman', 'warpriest', 'inquisitor'), 'cleric'),
    (('druid', 'beastmaster', 'shapeshifter', 'shapechanger', 'warden'), 'druid'),
    (('bard', 'skald', 'entertainer'), 'bard'),
    (('paladin', 'holy knight', 'divine knight'), 'paladin'),
    (('ranger',), 'ranger'),
    (('artificer', 'alchemist', 'engineer', 'inventor', 'technomancer'), 'artificer'),
)


RACE_SPELLS: dict[str, list[SpellSpec]] = {
    'aasimar': [('Light', 0, 'Create holy light.'), ('Healing Hands', 1, 'Restore a small amount of health.')],
    'changeling': [('Disguise Self', 1, 'Alter your appearance magically.'), ('Alter Self', 2, 'Briefly reshape your body.')],
    'dragonborn': [('Draconic Spark', 0, 'Create a tiny elemental sign.'), ('Breath Weapon', 1, 'Exhale elemental power tied to ancestry.')],
    'elf': [('Minor Illusion', 0, 'Create a small image or sound.'), ('Detect Magic', 1, 'Sense nearby magic.')],
    'fairy': [('Druidcraft', 0, 'Small fey nature signs.'), ('Faerie Fire', 1, 'Outline creatures in magical light.')],
    'firbolg': [('Hidden Step', 1, 'Briefly vanish from sight.'), ('Speak with Animals', 1, 'Communicate with beasts.')],
    'genasi': [('Elemental Attunement', 0, 'Shape a tiny expression of your element.'), ('Elemental Burst', 1, 'Release a small elemental burst.')],
    'gnome': [('Minor Illusion', 0, 'Create a small image or sound.'), ('Speak with Small Beasts', 1, 'Communicate simple ideas with small animals.')],
    'satyr': [('Friends', 0, 'Add a flash of fey charm to a social moment.'), ('Charm Person', 1, 'Briefly influence a humanoid.')],
    'saiyan': [
        ('Ki Blast', 0, 'Project a focused ranged burst of life energy.'),
        ('Ki Sense', 0, 'Feel strong nearby life force, battle pressure, or hidden power.'),
        ('Battle Aura', 1, 'Flare your ki to empower a charge, strike, leap, or intimidation moment.'),
        ('Zenkai Surge', 1, 'After surviving terrible harm, convert recovery into a short burst of fighting power.'),
        ('Great Ape Transformation', 2, 'A dangerous full-moon or Blutz Wave transformation into a gigantic berserk form.'),
    ],
    'tiefling': [('Thaumaturgy', 0, 'Small infernal signs and omens.'), ('Hellish Rebuke', 1, 'Retaliatory infernal fire.')],
    'triton': [('Shape Water', 0, 'Shape a small amount of water.'), ('Fog Cloud', 1, 'Create concealing mist.'), ('Thunderwave', 1, 'A concussive burst of sound.')],
    'yuan-ti': [('Poison Spray', 0, 'A short-range poison attack.'), ('Suggestion', 2, 'Magically influence a course of action.')],
}


def class_spell_archetype(class_name: str | None) -> str | None:
    normalized = re.sub(r'[^a-z0-9]+', ' ', str(class_name or '').split('-', 1)[0].lower()).strip()
    if not normalized:
        return None
    compact = normalized.replace(' ', '_')
    if compact in CLASS_STARTING_SPELLS:
        return compact
    tokens = set(normalized.split())
    for keywords, archetype in CLASS_KEYWORDS:
        if any(keyword in tokens or keyword in normalized for keyword in keywords):
            return archetype
    return None


def _spell_specs_to_payloads(
    specs: Iterable[SpellSpec],
    *,
    source_type: str,
    source_detail: str,
    learned_at_level: int | None = None,
) -> list[dict[str, Any]]:
    return [
        spell_payload(
            name,
            level=level,
            source_type=source_type,
            source_detail=source_detail,
            description=description,
            learned_at_level=learned_at_level,
        )
        for name, level, description in specs
        if _normalize_text(name)
    ]


def class_spells_for_level(class_name: str | None, level: int = 1) -> list[dict[str, Any]]:
    archetype = class_spell_archetype(class_name)
    if not archetype:
        return []
    character_level = max(1, min(20, int(level or 1)))
    spells = _spell_specs_to_payloads(
        CLASS_STARTING_SPELLS.get(archetype, []),
        source_type='class',
        source_detail=archetype,
        learned_at_level=1,
    )
    for unlock_level, specs in sorted(CLASS_LEVEL_UNLOCKS.get(archetype, {}).items()):
        if unlock_level <= character_level:
            spells.extend(
                _spell_specs_to_payloads(
                    specs,
                    source_type='level',
                    source_detail=f'{archetype}:{unlock_level}',
                    learned_at_level=unlock_level,
                )
            )
    spells.extend(original_class_spells_for_level(archetype, character_level))
    return spells


def _race_definition(race_name: str | None, race_selection: Any = None) -> dict[str, Any] | None:
    if isinstance(race_selection, str):
        selection = race_selection_from_json(race_selection, race_name)
    elif isinstance(race_selection, dict):
        selection = race_selection
    else:
        selection = None
    race = race_definition_from_selection(selection, race_name) if selection else None
    return race or find_curated_race(race_name)


def _race_spell_key(race: dict[str, Any] | None, race_name: str | None) -> str | None:
    if race:
        race_id = str(race.get('id') or '').strip().lower()
        race_text = ' '.join(
            str(value or '')
            for value in [race_id, race.get('name'), *(race.get('aliases') if isinstance(race.get('aliases'), list) else [])]
        ).lower()
        for key in RACE_SPELLS:
            if key in race_id or key in race_text:
                return key
    normalized = str(race_name or '').strip().lower()
    for key in RACE_SPELLS:
        if key in normalized:
            return key
    return None


def race_spells(race_name: str | None, race_selection: Any = None) -> list[dict[str, Any]]:
    race = _race_definition(race_name, race_selection)
    key = _race_spell_key(race, race_name)
    tags = {str(tag or '').strip().lower() for tag in (race or {}).get('tags', []) if str(tag or '').strip()}
    traits = race.get('traits') if isinstance(race, dict) else []
    trait_text = json.dumps(traits or [], sort_keys=True).lower()
    if key:
        return [
            *_spell_specs_to_payloads(RACE_SPELLS[key], source_type='race', source_detail=key, learned_at_level=1),
            *original_race_spells(race_key=key, race_tags=tags, trait_text=trait_text, count=2),
        ]

    if _looks_like_shapeshifter_trait(trait_text):
        return [
            *_spell_specs_to_payloads(RACE_SPELLS['changeling'], source_type='race', source_detail='shapeshifter', learned_at_level=1),
            *original_race_spells(race_key='shapeshifter', race_tags=tags, trait_text=trait_text, count=2),
        ]
    if 'breath_weapon' in trait_text or 'elemental_cone_or_line' in trait_text:
        return [
            *_spell_specs_to_payloads(RACE_SPELLS['dragonborn'], source_type='race', source_detail='draconic', learned_at_level=1),
            *original_race_spells(race_key='draconic', race_tags=tags, trait_text=trait_text, count=2),
        ]
    if 'elemental' in tags or 'elemental' in trait_text:
        return [
            *_spell_specs_to_payloads(RACE_SPELLS['genasi'], source_type='race', source_detail='elemental', learned_at_level=1),
            *original_race_spells(race_key='elemental', race_tags=tags, trait_text=trait_text, count=2),
        ]
    if 'celestial' in tags or 'minor_healing' in trait_text:
        return [
            *_spell_specs_to_payloads(RACE_SPELLS['aasimar'], source_type='race', source_detail='celestial', learned_at_level=1),
            *original_race_spells(race_key='celestial', race_tags=tags, trait_text=trait_text, count=2),
        ]
    race_id = str((race or {}).get('id') or '').strip().lower()
    if 'magical' in tags and race_id not in {'human', 'afro-diasporic-human'}:
        source_detail = str((race or {}).get('id') or race_name or 'magical')
        return [
            *_spell_specs_to_payloads(
                [('Prestidigitation', 0, 'A small inherited magical effect.'), ('Detect Magic', 1, 'Sense nearby magic.')],
                source_type='race',
                source_detail=source_detail,
                learned_at_level=1,
            ),
            *original_race_spells(race_key=source_detail, race_tags=tags, trait_text=trait_text, count=2),
        ]
    return []


def normalize_spell(raw_spell: Any) -> dict[str, Any] | None:
    if isinstance(raw_spell, str):
        name = _normalize_text(raw_spell)
        return spell_payload(name) if name else None
    if not isinstance(raw_spell, dict):
        return None
    name = _normalize_text(raw_spell.get('name') or raw_spell.get('spellName') or raw_spell.get('spell_name'))
    if not name:
        return None
    normalized = {
        **raw_spell,
        'id': _normalize_text(raw_spell.get('id') or raw_spell.get('spellId') or raw_spell.get('spell_id') or _spell_id(name)),
        'name': name,
        'level': _bounded_spell_level(raw_spell.get('level', raw_spell.get('spellLevel', raw_spell.get('spell_level', 0)))),
    }
    sources = raw_spell.get('sources')
    if isinstance(sources, list):
        normalized['sources'] = [_normalize_text(source) for source in sources if _normalize_text(source)]
    elif raw_spell.get('source'):
        normalized['sources'] = [_normalize_text(raw_spell.get('source'))]
    else:
        source = _source_label(raw_spell.get('sourceType'), raw_spell.get('sourceDetail'))
        if source:
            normalized['source'] = source
            normalized['sources'] = [source]
    return normalized


def normalize_spellbook(raw_spellbook: Any) -> dict[str, Any]:
    if isinstance(raw_spellbook, dict):
        raw_known = (
            raw_spellbook.get('knownSpells')
            or raw_spellbook.get('known_spells')
            or raw_spellbook.get('spells')
            or []
        )
        prepared = raw_spellbook.get('preparedSpells') or raw_spellbook.get('prepared_spells') or []
        sources = raw_spellbook.get('sources') or []
    elif isinstance(raw_spellbook, list):
        raw_known = raw_spellbook
        prepared = []
        sources = []
    else:
        raw_known = []
        prepared = []
        sources = []

    known: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_spell in raw_known:
        spell = normalize_spell(raw_spell)
        if not spell:
            continue
        key = _slug(spell.get('id') or spell.get('name'))
        name_key = _slug(spell.get('name'))
        if key in seen or name_key in seen:
            continue
        seen.add(key)
        seen.add(name_key)
        known.append(spell)
    prepared_names = [_normalize_text(item) for item in prepared if _normalize_text(item)] if isinstance(prepared, list) else []
    source_names = [_normalize_text(item) for item in sources if _normalize_text(item)] if isinstance(sources, list) else []
    for spell in known:
        for source in spell.get('sources') or []:
            if source and source not in source_names:
                source_names.append(source)
    return {
        'knownSpells': known,
        'preparedSpells': prepared_names,
        'sources': source_names,
    }


def _auto_race_source_details(spell: dict[str, Any]) -> set[str]:
    details: set[str] = set()
    source_type = _normalize_text(spell.get('sourceType') or spell.get('source_type')).lower()
    source_detail = _normalize_text(spell.get('sourceDetail') or spell.get('source_detail')).lower()
    if source_type in {'race', 'race_catalog'} and source_detail:
        details.add(source_detail)

    raw_sources = spell.get('sources') if isinstance(spell.get('sources'), list) else []
    if spell.get('source'):
        raw_sources = [*raw_sources, spell.get('source')]
    for raw_source in raw_sources:
        source = _normalize_text(raw_source).lower()
        if source.startswith('race:') or source.startswith('race_catalog:'):
            details.add(source.split(':', 1)[1])
    return details


def _remove_stale_auto_race_spells(raw_spellbook: Any, current_race_key: str | None) -> dict[str, Any]:
    spellbook = normalize_spellbook(raw_spellbook)
    current_key = _normalize_text(current_race_key).lower()
    if current_key in STALE_AUTO_RACE_SOURCE_DETAILS:
        return spellbook

    filtered_spells = [
        spell
        for spell in spellbook['knownSpells']
        if not (_auto_race_source_details(spell) & STALE_AUTO_RACE_SOURCE_DETAILS)
    ]
    if len(filtered_spells) == len(spellbook['knownSpells']):
        return spellbook

    remaining_names = {_slug(spell.get('name')) for spell in filtered_spells if spell.get('name')}
    prepared = [
        spell_name
        for spell_name in spellbook.get('preparedSpells', [])
        if _slug(spell_name) in remaining_names
    ]
    return normalize_spellbook({'knownSpells': filtered_spells, 'preparedSpells': prepared})


def merge_spellbooks(existing: Any, incoming: Any) -> dict[str, Any]:
    merged = normalize_spellbook(existing)
    incoming_book = normalize_spellbook(incoming)
    spells = merged['knownSpells']
    by_name = {_slug(spell.get('name')): spell for spell in spells if spell.get('name')}
    by_id = {_slug(spell.get('id')): spell for spell in spells if spell.get('id')}
    for incoming_spell in incoming_book['knownSpells']:
        name_key = _slug(incoming_spell.get('name'))
        id_key = _slug(incoming_spell.get('id'))
        existing_spell = by_name.get(name_key) or by_id.get(id_key)
        if existing_spell:
            existing_sources = existing_spell.setdefault('sources', [])
            if not isinstance(existing_sources, list):
                existing_sources = []
                existing_spell['sources'] = existing_sources
            for source in incoming_spell.get('sources') or []:
                if source not in existing_sources:
                    existing_sources.append(source)
            for key, value in incoming_spell.items():
                if key not in existing_spell and value not in (None, '', [], {}):
                    existing_spell[key] = value
            continue
        spells.append(deepcopy(incoming_spell))
        by_name[name_key] = spells[-1]
        by_id[id_key] = spells[-1]
    for source in incoming_book.get('sources') or []:
        if source not in merged['sources']:
            merged['sources'].append(source)
    return merged


def spellbook_for_character(
    *,
    class_name: str | None,
    race_name: str | None,
    race_selection: Any = None,
    level: int = 1,
) -> dict[str, Any]:
    spells = [
        *class_spells_for_level(class_name, level),
        *race_spells(race_name, race_selection),
    ]
    return normalize_spellbook(spells)


def character_sheet_record(raw_value: Any) -> dict[str, Any]:
    if isinstance(raw_value, dict):
        return deepcopy(raw_value)
    if raw_value is None:
        return {}
    loaded = safe_json_loads(raw_value, None)
    if isinstance(loaded, dict):
        return loaded
    if isinstance(raw_value, str) and raw_value.strip():
        return {'notes': raw_value.strip()}
    return {}


def spellbook_from_character_sheet(raw_sheet: Any) -> dict[str, Any]:
    sheet = character_sheet_record(raw_sheet)
    raw_spellbook = sheet.get('spellbook')
    if raw_spellbook is None:
        raw_spellbook = sheet.get('knownSpells') or sheet.get('known_spells') or sheet.get('spells')
    return normalize_spellbook(raw_spellbook)


def known_spell_names(raw_spellbook: Any) -> list[str]:
    return [spell['name'] for spell in normalize_spellbook(raw_spellbook).get('knownSpells', []) if spell.get('name')]


def ensure_character_sheet_spellbook(
    raw_sheet: Any,
    *,
    class_name: str | None,
    race_name: str | None,
    race_selection: Any = None,
    level: int = 1,
) -> tuple[dict[str, Any], bool]:
    sheet = character_sheet_record(raw_sheet)
    before = json.dumps(sheet, sort_keys=True, default=str)
    race = _race_definition(race_name, race_selection)
    current_race_key = _race_spell_key(race, race_name)
    baseline = spellbook_for_character(
        class_name=class_name,
        race_name=race_name,
        race_selection=race_selection,
        level=level,
    )
    if not baseline.get('knownSpells'):
        return sheet, False
    existing = sheet.get('spellbook')
    if existing is None:
        existing = sheet.get('knownSpells') or sheet.get('known_spells') or sheet.get('spells')
    existing = _remove_stale_auto_race_spells(existing, current_race_key)
    merged = merge_spellbooks(existing, baseline)
    sheet['spellbook'] = merged
    sheet['spells'] = known_spell_names(merged)
    after = json.dumps(sheet, sort_keys=True, default=str)
    return sheet, before != after


def spell_from_change(change: dict[str, Any]) -> dict[str, Any] | None:
    raw_spell = change.get('spell') if isinstance(change.get('spell'), dict) else {}
    spell_name = _normalize_text(
        change.get('spellName')
        or change.get('spell_name')
        or raw_spell.get('name')
        or raw_spell.get('spellName')
        or raw_spell.get('spell_name')
    )
    if not spell_name and isinstance(change.get('spell'), str):
        spell_name = _normalize_text(change.get('spell'))
    if not spell_name:
        return None
    return spell_payload(
        spell_name,
        level=_bounded_spell_level(change.get('spellLevel', change.get('spell_level', raw_spell.get('level', raw_spell.get('spellLevel', 0))))),
        source_type=change.get('sourceType') or change.get('source_type') or raw_spell.get('sourceType') or 'story',
        source_detail=change.get('sourceDetail') or change.get('source_detail') or raw_spell.get('sourceDetail') or 'learned',
        description=change.get('description') or raw_spell.get('description'),
        learned_at_level=change.get('learnedAtLevel') or change.get('learned_at_level') or raw_spell.get('learnedAtLevel'),
        learned_from=change.get('learnedFrom') or change.get('learned_from') or raw_spell.get('learnedFrom') or change.get('sourceText'),
    )
