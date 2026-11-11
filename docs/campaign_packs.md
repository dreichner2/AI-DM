# Campaign Packs

Campaign packs are structured adventure modules that seed an AIDM campaign with authored locations, NPCs, quests, enemies, encounters, segments, checkpoints, clues, factions, maps, handouts, lore, and director rules.

The current contract is version `1`. The JSON Schema lives at [campaign_pack.schema.json](campaign_pack.schema.json), and bundled examples live in [examples/](examples/). The backend loads both the schema and every `docs/examples/*.json` file at runtime, so changes to these files can change validation, the campaign picker, or Play Now behavior; they are executable product assets rather than illustrative snippets.

Current example packs:

- [examples/bleakmoor_intro_campaign_pack.json](examples/bleakmoor_intro_campaign_pack.json): internal test fixture for import and visibility checks; hidden from the player-facing campaign picker.
- [examples/shadow_over_the_greenway_campaign_pack.json](examples/shadow_over_the_greenway_campaign_pack.json): larger checkpoint-spine campaign with branches and encounter pressure.
- [examples/shadow_under_eryn_luin_campaign_pack.json](examples/shadow_under_eryn_luin_campaign_pack.json): larger multi-location campaign with hidden lore and finale state.
- [examples/the_road_of_unremembered_kings_campaign.json](examples/the_road_of_unremembered_kings_campaign.json): original full campaign with soft checkpoint pathing, redundant clues, factions, maps, handouts, lore, and multiple ending states.

`The Road of Unremembered Kings` is the default pack used by
`POST /api/onboarding/play-now`. The Bleakmoor fixture is intentionally omitted
from the player-facing bundled-pack list because its metadata marks it internal
and test-only.

## Import Flow

Use `POST /api/campaigns/import-pack` with the pack JSON body.

Use `POST /api/campaigns/import-pack?dry_run=true` to validate and preview without creating records. A dry run returns `imported: false`, pack metadata, counts, the resolved world behavior, starting quest/location, visible starting records, and normalized director rules.

Successful import creates:

- a `World`, unless an existing `world_id` or `worldId` is supplied
- a `Campaign`
- an opening `Session`
- a `SessionState`
- `CampaignSegment` rows for imported segments
- campaign-scope `BestiaryEntry` rows for imported enemies
- an `installed_campaign_packs` library record with pack version, schema version, source filename, hash, importer, manifest, and validation time
- durable `campaign_packs`, `campaign_pack_records`, `campaign_pack_sessions`, `campaign_pack_checkpoint_progress`, and `campaign_pack_progress_events` rows
- `Session.state_snapshot.campaignPack`, a compact runtime mirror containing the hidden pack catalog, director rules, progress, version metadata, shared-group key, GM-only notes, and the GM-only `startingBrief` (`startingState.partyStatus` and `partyNotes`)

The catalog (`campaignPack.catalog`) holds every authored record under
`locations`, `npcs`, `quests`, `enemies`, `encounters`, `segments`, `clues`,
`factions`, `maps`, `handouts`, `lore`, and `merchants`. Records are copied whole: nested
predicates, reward lists, and interactable requirements are imported exactly as
authored, or the import is rejected (see Current Limits); nothing is silently
truncated. Older snapshots that stored lore under `catalog.lores` are migrated
to `catalog.lore`.

The opening scene (`currentScene`) is built from `startingState.currentScene`:
name (or `title`), `description`, `summary`, `visibleDetails`,
`hiddenSceneNotes`, scene type, danger, mood, positions, `items`,
`interactables`, and `hazards`. When the starting scene lists no `items`,
`interactables`, or `hazards`, the starting location's own collections are
used, in the same form as when a location is restored on arrival (a
`sceneState` collection wins over the top-level one). `startingState.flags`
seeds the authoritative flags; `campaignFlags` is accepted as an alias, and
keys in `flags` win on conflict. The starting location's `restPolicy` (or one on
`startingState.currentScene`) becomes `currentScene.restPolicy`, and the start
counts as the party's first arrival there: its `onEnter` consequences are
applied at import.

The snapshot also gets `merchants` (see Merchants), and `campaignPack` keeps
the world `map` settings, `routes`, and `storyFlags`.

## Bundled Example Library

`GET /api/campaigns/example-packs` returns player-visible summaries for bundled
packs. It requires `player_read`; it does not return the full hidden manifest.

Workspace admins can import a bundled pack with:

```text
POST /api/campaigns/example-packs/<pack_id>/import
```

The request body is optional. It can supply `world_id`/`worldId` and
`session_name`/`sessionName`; `dry_run`/`dryRun` can be supplied in the body or
query string. Import requires `dm_authoring`. The server resolves the pack by
its manifest `packId`, not by its filename.

## Compatibility

- `schemaVersion` defaults to `1`.
- Accepted values are `1`, `1.0`, and `1.0.0`; they are normalized to `1`.
- Unsupported schema versions return `unsupported_schema_version`.
- Unknown fields are preserved where practical, so pack authors can add future metadata without breaking imports.
- AIDM treats `Campaign.location` and `Campaign.current_quest` as import/backcompat fields. Live play state comes from `Session.state_snapshot`.
- Legacy campaign-pack snapshots are migrated in-process to the current snapshot/progress shape before progress reads and writes.

## Required Fields

Top-level required fields:

- `packId`
- `title`

Recommended fields:

- `schemaVersion`
- `version`
- `description`
- `world`
- `startingState.locationId`
- `startingState.questId`
- `startingState.checkpointId` (optional; selects the authored initial
  checkpoint instead of defaulting to the first checkpoint)
- `locations`
- `npcs`
- `quests`
- `enemies`
- `encounters`
- `segments`
- `checkpoints`
- `clues`
- `factions`
- `maps`
- `handouts`
- `lore`
- `merchants`
- `storyFlags`
- `map` and `routes`
- `directorRules`
- `dependencies`
- `mods`
- `multiSessionGroupKey`
- `gmNotes`
- `hiddenSceneNotes`

Validated travel to a catalog location materializes its player-safe authored
scene fields, items, and NPC/quest references into live runtime state. Author
notes and Dungeon Master-only catalog metadata remain confined to the pack.

## Content Sources

Imported pack-authored records are tagged with:

```json
{
  "source": "campaign_pack",
  "packId": "bleakmoor_intro"
}
```

Imported `CampaignSegment` rows also keep explicit identity fields:

```json
{
  "external_id": "seg_question_veyra",
  "source": "campaign_pack",
  "source_pack_id": "bleakmoor_intro",
  "metadata_json": {
    "packId": "bleakmoor_intro",
    "packSegmentId": "seg_question_veyra"
  }
}
```

Runtime additions should use one of:

- `campaign_pack`: authored module content
- `emergent`: improvised runtime content
- `player_created`: player-caused additions
- `dm_override`: deliberate DM/admin override
- `admin_override`: explicit admin override

## Director Rules

Supported director rule keys:

```json
{
  "mainQuestGeneration": "pack_only",
  "sideQuestGeneration": "allowed_tagged",
  "newNpcs": "allowed_as_minor_or_temporary",
  "newLocations": "allowed_as_local_detail",
  "offTrackPolicy": "improvise_and_reconnect",
  "checkpointStyle": "soft"
}
```

`pack_only` means the DM should not invent replacement main quests. Local improvised content can still exist when allowed, but it must be tagged as emergent and should carry a rejoin target.

These six keys (or their snake_case forms) are the only ones read. The four
generation keys drive drift control in the validator and reach the DM prompt;
`offTrackPolicy` and `checkpointStyle` reach the prompt only. Any other key
(for example `tone` or `pathingGuidance`) is ignored and reported by the
linter.

## Field Reference

Each field is marked by what the engine does with it:

- **mechanical**: runtime code acts on it deterministically.
- **prompt**: it reaches the DM (model) prompt.
- **player**: it is shown in the player-filtered snapshot or progress payload.
- **stored**: it is kept in the operator catalog only; it never reaches players
  or the prompt.

Fields not listed are preserved in the manifest and catalog but have no runtime
consumer.

| Record | Field | Use |
| --- | --- | --- |
| pack | `packId`, `schemaVersion`, `world`, `multiSessionGroupKey` | mechanical |
| pack | `title`, `description`, `version` | mechanical, player |
| pack | `directorRules` (six keys above) | mechanical, prompt |
| pack | `gmNotes`, `hiddenSceneNotes` | prompt |
| pack | `merchants` | mechanical (runtime `snapshot.merchants`) |
| pack | `storyFlags` | mechanical, prompt (never player) |
| pack | `map`, `routes` | mechanical, player (`worldMap`) |
| startingState | `locationId`, `questId`, `checkpointId`, `known*Ids`, `active*Ids`, `partyNpcs`, `combat` | mechanical |
| startingState | `flags` (alias `campaignFlags`) | mechanical |
| startingState | `clock` (`{"day", "phase"}`; see Clock Start) | mechanical, player |
| startingState | `partyStatus`, `partyNotes` | stored (`campaignPack.startingBrief`) |
| currentScene | `name`/`title`, `sceneType`, `dangerLevel`, `mood`, `combatState`, `musicTag`, positions, `items`, `interactables`, `hazards` | mechanical, player |
| currentScene | `description` | mechanical, prompt, player |
| currentScene | `summary`, `visibleDetails` | player |
| currentScene | `hiddenSceneNotes` | stored (on the opening scene) |
| location | `id`, `name`, `status`, `connectedLocationIds`, `npcIds`, `questIds`, scene fields, `items`, `interactables`, `hazards`, `sceneState`, `visibleAtStart` | mechanical |
| location | `description`, `type` | prompt, player |
| location | `onEnter`, `restPolicy` | mechanical |
| location | `mapPosition`, `mapIcon` | player (`worldMap`) |
| location | `region`, `playerSummary`, `playerDescription`, `tags` | player |
| location | `gmNotes` | stored |
| scene object | `kind`, state fields, `requirements`, `contents`, `discoversClueIds`, `setsFlags`, `discoversLocationIds`, `reveals`, `mechanicalEffects`, `triggerable`, `repeatable` | mechanical |
| scene object | `allowedActions`, `usableInCombat`, `resolvesEncounter`, `choiceActions` (see Scene Object Fields) | mechanical, player |
| scene object | `actionLabels`, `proper` | player |
| scene object | `actionText` | mechanical, prompt (told when the action succeeds; never in the player snapshot) |
| npc | `id`, `name`, `locationId`, `status`, `disposition`, visibility flags, `hidden`, `hiddenUntil` (see Meeting People) | mechanical |
| npc | `role`, `race`, `questIds` | prompt, player |
| npc | `playerSummary`, `description`/`playerDescription`, `aliases`, `tags` | player |
| npc | `personality`, `motivation`, `knows`, `secret`, `useInPlay`, `ifSkipped`, `gmNotes`, `trueName` | stored |
| quest | `id`, `title`, `status`, `objectives`, `completionPolicy`, `failOnObjectiveFailure`, `rewards`, `xpReward`, `rewardActorId`, `onComplete`, `onFail`, `visibleAtStart` | mechanical |
| quest | `summary`, `stage` | prompt, player |
| quest | `playerSummary`, `description` | player |
| objective | `id`, `status`, `optional`, `prerequisiteObjectiveIds`, `completeWhen`, `failWhen` | mechanical |
| objective | `description` | prompt, player |
| encounter | `id`, `enemyIds`, `enemyGroups`, `completion`, `rewards`, `partyAllocation`, `outcomes`, `outcomeRewards`, `outcomeConsequences`, `questEvents`, `on*` hooks, `questIds`, `trigger`, `once`, `when`, `unless`, `scaling`, `adjustments`, `allowParley`, `lethalDefeat` | mechanical |
| encounter | `title`, `summary`/`description`, `locationIds`, `checkpointIds` | prompt |
| encounter | `setup` (read aloud when its fight starts, by either narrator) | mechanical, prompt |
| encounter | `stakes`, `gmNotes`, `difficulty`, `tags` | stored |
| enemy | creature fields (`stats`, `abilities`, `behavior`, ...), `xpReward` | mechanical |
| enemy | `challengeTier: "boss"` (never yields or retreats above a quarter of its HP) | mechanical |
| enemy | `singularName`, `proper`, `pronoun` (how one of them is named and referred to in narration) | mechanical |
| enemy | `captiveKnows` (a line told each time a captive one is questioned) | mechanical, prompt |
| enemy ability | `durationRounds` (1–10 of the target's turns its `conditionsApplied` last; default 1, the end of its next turn; a save is repeated at the end of each; the linter errors outside 1–10) | mechanical |
| enemy | `descriptionShort`, `challengeTier`, `creatureType`, `tags` | prompt |
| checkpoint | `id`, graph fields, `completeWhen`, `failWhen`, `branchWhen`, `prerequisite*`, `optional`, `terminal`, `canCompleteOutOfOrder`, `rejoinTargetCheckpointId`, `directorRules` | mechanical |
| checkpoint | `title`, `summary`, `chapter`, `act`, `priority`, `gate`, related ids, `clueIds` | prompt |
| checkpoint | `playerTitle`, `playerSummary` | player |
| checkpoint | `endingVariants` (terminal checkpoints; see Checkpoint Controls) | mechanical, player |
| checkpoint | `gmNotes` | stored |
| segment | `id`, `title`, `trigger`, `locationId` (location trigger), `tags` | mechanical |
| segment | `description` (or `summary`) | prompt |
| segment | `summary`, `playerSummary`, `tone`, `gmNotes`, `questIds` | stored (`catalog.segments`) |
| clue, faction, map, handout, lore | `id` and the record (materialized by `clue.discover`, `faction.discover`, `map.reveal`, `handout.reveal`, `lore.unlock`) | mechanical |
| clue, faction, map, handout, lore | `title`/`name`, `summary`, `description`, `status`, related ids, `tags` | player |

## Segments

Each segment becomes a `CampaignSegment` row and a `catalog.segments` entry
with `id`, `title`, `summary`, `description`, `playerSummary`, `tone`,
`gmNotes`, `locationId`, `questIds`, and `tags`.

Triggers:

- a plain string such as `"veyra, captain"`: keywords; any keyword in the
  player's message triggers the segment before the turn;
- `{"type": "keyword", "keywords": ["veyra", "captain"], "match": "any"}`:
  the same, as an object (`match` may be `all`);
- `{"type": "state", "location_contains": "...", "quest_contains": "..."}`:
  substring checks on the live location and active quests after each turn;
- `{"type": "location", "locationId": "loc_gate"}`: after each turn, triggers
  when the party's current location id equals `locationId`;
- `{"type": "manual"}` or no trigger: only an operator triggers it.

A segment with a `locationId` and no `trigger` gets the location trigger for
that location. Triggered segments complete checkpoints that list them.

## Quest Predicate Language

Objective `completeWhen` and `failWhen` are predicates evaluated by the quest
engine after every applied change. Narration cannot complete objectives, and it
can set only declared story flags (see Story Flags); objectives progress only
through predicates or operator tools.

Event keys match the single applied change: `eventType` (or `event`,
`changeType`) is the change type, and `actorId`, `targetActorId`, `targetId`,
`objectId`, `itemId`, `itemName`, `npcId`, `locationId`, `encounterId`,
`participantId`, `endReason`, `flagKey`, `checkpointId`, `objectiveId`, and
`questId` must equal the change's field (strings compare case-insensitively).
`quantityAtLeast` (or `amountAtLeast`) compares the change's amount.

State keys are checked against the state after the change: `atLocationId`;
`locationId` and `flagKey` with `flagValue`/`equals` (default `true`) when no
event type is given; `possessesItemId` or `possessesItemName` with optional
`actorId` and `quantityAtLeast`; `objectiveIds` with `objectivePolicy`
(`all` or `any`). Event and state keys in one predicate must all hold.

`all`/`allOf` and `any`/`anyOf` hold lists of predicates. Entries that are not
objects are ignored, a combinator with no predicate is never satisfied, and
other keys next to a combinator are ignored. Any other key is ignored by the
engine; the linter reports it as an error.

Event types that reach the engine include every applied state change
(`inventory.*`, `currency.*`, `scene.move_location`, `location.*`, `npc.*`,
`clue.*`, `flag.set`, `combat.*`, ...), encounter events (`combat.outcome` and
authored `questEvents`), and interactable events
(`interactable.opened|closed|locked|unlocked|searched|broken|used|inspected`,
`hazard.triggered|disarmed|...`).

Encounter reward items get a new instance id, so use `possessesItemName` (not
`possessesItemId`) for items granted by encounters. Quest reward items keep
their authored id.

## Authoring Examples

These shapes stay well inside the nesting limit.

Objectives:

```json
{
  "id": "quest_open_road",
  "title": "Open the Road",
  "status": "active",
  "rewards": {"xp": 100, "items": [{"id": "road_seal", "name": "Road Seal", "quantity": 1}]},
  "onComplete": [{"type": "flag.set", "flagKey": "road_open", "flagValue": true}],
  "objectives": [
    {"id": "obj_search_box", "description": "Search the waystation lockbox.",
     "completeWhen": {"eventType": "interactable.searched", "targetId": "ws_lockbox"}},
    {"id": "obj_win_bridge", "description": "Clear the bridge.",
     "completeWhen": {"anyOf": [{"eventType": "bridge_won"}, {"eventType": "bridge_terms_agreed"}]},
     "failWhen": {"eventType": "combat.outcome", "encounterId": "enc_bridge_guard", "endReason": "players_fled"}},
    {"id": "obj_reach_gate", "description": "Carry the seal to the gate.",
     "prerequisiteObjectiveIds": ["obj_search_box", "obj_win_bridge"],
     "completeWhen": {"atLocationId": "loc_gate", "possessesItemName": "Bridge Seal"}}
  ]
}
```

An interactable (on `location.interactables` or `location.sceneState.interactables`):

```json
{"id": "ws_lockbox", "name": "Iron Lockbox", "kind": "container",
 "playerDescription": "A locked iron box.", "locked": true, "breakable": true,
 "requirements": {"unlock": {"anyOf": [{"anyItemIds": ["rusted_key"]},
                                       {"check": {"id": "pick_lockbox", "dc": 14, "skill": "sleight_of_hand"}}]}},
 "contents": [{"id": "ledger_page", "name": "Ledger Page", "type": "gear"}],
 "discoversClueIds": {"search": ["clue_scratched_names"]},
 "setsFlags": {"unlock": {"lockbox_open": true}},
 "discoversLocationIds": {"search": ["loc_hidden_valley"]},
 "reveals": ["ws_hidden_niche"], "eventVisibility": "players"}
```

A trapped hazard:

```json
{"id": "ws_needle_trap", "name": "Needle Trap", "kind": "hazard", "active": true, "disarmable": true,
 "requirements": {"disarm": {"check": {"id": "disarm_needle", "dc": 13, "ability": "dexterity"}}},
 "mechanicalEffects": {
   "failure:disarm": {"damage": {"dice": "2d6", "type": "piercing",
                                 "save": {"ability": "dexterity", "dc": 12, "onSuccess": "half"}}},
   "trigger": {"damage": {"dice": "1d6", "type": "poison"},
               "conditions": [{"condition": "poisoned", "rounds": 3}], "targets": "party"}}}
```

An encounter that starts itself, scaled to the party:

```json
{"id": "enc_bridge_guard", "title": "The Bridge Guard",
 "enemyGroups": [{"enemyId": "enemy_bridge_guard", "count": 4}],
 "trigger": {"type": "on_enter", "locationId": "loc_bridge"},
 "unless": {"flagKey": "has_road_seal", "equals": true}}
```

A checkpoint with a flag-gated branch:

```json
[
  {"id": "cp_02_road", "title": "The Road That Moves",
   "completeWhen": {"encounterIds": ["enc_bridge_guard"], "clueIds": ["clue_lantern_bell"]},
   "nextCheckpointIds": ["cp_03_ally_route", "cp_03_waystation"]},
  {"id": "cp_03_ally_route", "title": "Radan's Shortcut", "playerTitle": "A Ranger's Path",
   "branchWhen": {"flagKey": "radan_allied", "equals": true},
   "completeWhen": {"flags": {"shortcut_found": true}},
   "nextCheckpointIds": ["cp_05_valley"]},
  {"id": "cp_03_waystation", "title": "The Waystation",
   "completeWhen": {"objectiveIds": ["obj_search_box"], "questIds": ["quest_open_road"]},
   "nextCheckpointIds": ["cp_05_valley"]}
]
```

### Worked example

[The Road of Unremembered Kings](examples/the_road_of_unremembered_kings_campaign.json),
the Play Now adventure, uses every mechanism on this page and passes
`lint --strict`. Read it alongside this reference:

- `map`, `routes`, and each location's `mapPosition`, `mapIcon`, `restPolicy`,
  `sceneType`, and `connectedLocationIds`. A place starts undiscovered and is
  revealed by its neighbour's `onEnter` `location.discover`; the valley starts
  `"status": "hidden"` and is revealed only by an object or an encounter.
- Objects on every location: checks (`inn_drivers_bench`, `ws_memorial_wall`,
  `ridge_culvert_grate`), keys and a check to break (`inn_wagon_strongbox`),
  a hidden object revealed by a search (`inn_lead_wagon`), flag-gated actions
  (`cairn_banner_stone`, `valley_last_road_stone`, whose claim is a
  `triggerable` action), and contents (coin included), clues, flags, and
  places found by actions.
- Encounters started by `on_interactable` and `on_enter` triggers with
  `when`/`unless` gates, `enemyGroups` authored for four heroes, and
  per-outcome quest events and consequences, clue discoveries among them
  (`enc_roadwarden_judgment`, `enc_final_binding`).
- A quest whose objectives complete from flags, places, and an encounter quest
  event, with optional objectives, rewards, and `onComplete`; checkpoints
  completed by objectives, places, flags, and an encounter, ending on a
  terminal checkpoint; `storyFlags` so talk can achieve what objects can; and
  two merchants, one selling a hammer whose `itemId` a requirement names.
- The Story view fields: `actionLabels` and `allowedActions` on every object,
  `actionText` for the bell, the oath, the grate and the road-stone's fates, the banner
  stone's `usableInCombat` and `resolvesEncounter` oath, the road-stone's
  `choiceActions` and the terminal checkpoint's `endingVariants` (four fates,
  four endings), `adjustments` and `setup` on the finale, `singularName` and
  `proper` enemies, an ability with `durationRounds`, and
  `startingState.clock`.

`tests/test_flagship_adventure.py` plays it through in Safe Mode by two routes,
and `tests/test_play_now_release_smoke.py` (`make play-now-smoke`) replays the
careful route and a lost-then-won finale while checking what the player reads.

## Stable Import Errors

The importer returns these stable `error_code` values:

| Code | Meaning |
| --- | --- |
| `validation_error` | The body is not valid JSON, required fields are missing, fields are the wrong shape, IDs are duplicated, or starting references do not point at imported records. |
| `invalid_campaign_pack_schema` | The body failed the documented JSON schema contract, with a path such as `quests[2].objectives[1].status`. |
| `invalid_pack_reference` | A pack record references a location, NPC, quest, segment, enemy, encounter, or checkpoint ID that is not defined in the pack. |
| `invalid_checkpoint_graph` | Checkpoint `nextCheckpointIds` form a cycle. |
| `pack_content_out_of_bounds` | A value nests too deeply or has a string, object, or list larger than the limits below; the message names its JSON path, such as `quests[0].objectives[1].completeWhen.allOf[0]`. |
| `unsupported_schema_version` | `schemaVersion` is not accepted by this AIDM build. |
| `world_not_found` | The pack references an existing world that does not exist in the current workspace. |
| `campaign_pack_import_failed` | Import failed after validation due to an unexpected persistence error. |

## Pack Author Tools

Run the local pack tool before importing or publishing a pack:

```bash
PACK=docs/examples/the_road_of_unremembered_kings_campaign.json
.venv/bin/python scripts/aidm_pack.py lint "$PACK"
.venv/bin/python scripts/aidm_pack.py report "$PACK"
.venv/bin/python scripts/aidm_pack.py preview "$PACK"
.venv/bin/python scripts/aidm_pack.py graph "$PACK"
.venv/bin/python scripts/aidm_pack.py test-checkpoints "$PACK"
```

The linter uses the same import dry-run validator as the API, then adds authoring checks for unreachable checkpoints, missing completion cues, hidden records visible at start, large prompt-budget records, dependency declarations, and `pack_only` checkpoints without rejoin targets. It also reports everything the engine would reject or ignore:

| Severity | Code | Reported when |
| --- | --- | --- |
| error | `pack_content_out_of_bounds` | a value exceeds a nesting, string, key, or list limit (one issue per JSON path) |
| error | `invalid_id` | a record or objective `id` is not `[a-z0-9_]+` (the importer rewrites IDs but not references) |
| error | `unknown_reference` | `connectedLocationIds`, `npcIds`, `questIds`, `encounterIds`, `enemyIds` (and `enemyGroups[].enemyId`), `locationIds`, `checkpointIds`, segment `locationId`, checkpoint id lists (also inside `completeWhen`/`failWhen`), `startingState` ids, objective prerequisites, a predicate `encounterId`, `objectiveId(s)`, `questId`, `locationId`, `atLocationId`, `npcId`, `checkpointId`, an encounter trigger's `locationId`/`checkpointId`/`targetId`, an `onEnter` consequence's `clueId`/`questId`/`locationId`, an object's `discoversClueIds`/`discoversLocationIds`, a merchant's `npcId`/`locationId`, or a route's `from`/`to` names no record |
| error | `invalid_encounter_trigger` | an encounter trigger has an unknown type, or lacks the id or `flagKey` its type needs, or names an unknown object action |
| error | `unsupported_consequence_type` | an `onEnter` consequence type is not applied |
| error | `merchant_without_place` | a merchant has neither `npcId` nor `locationId` |
| error | `invalid_merchant_price` | a stock entry without `catalog` has no positive whole `gp`/`sp`/`cp` price |
| error | `invalid_story_flag_key` | a story flag key is not `[a-z0-9_]+` |
| error | `invalid_interactable_catalog` | `interactables.validate_interactable_catalog` rejects a location's or the opening scene's objects |
| error | `unknown_predicate_key` | an objective `completeWhen`/`failWhen` uses a key outside the predicate language, or a key next to a combinator |
| error | `invalid_predicate_entry` | a predicate is not an object or is empty, or a combinator is not a list or holds no predicate |
| error | `invalid_segment_trigger` | a segment trigger has an unknown type, or a keyword trigger has no keywords, or a location trigger has no `locationId` |
| warning | `objective_without_mechanics` | an objective has no `completeWhen`, `failWhen`, or prerequisites (only an operator can complete it) |
| warning | `location_without_connections` | a location has no `connectedLocationIds` (structured travel cannot reach or leave it) |
| warning | `branch_flag_never_set` | a `branchWhen.flagKey` is set by nothing in the pack (starting flags, rewards, `flag.set` consequences, `onEnter`, object `setsFlags`, or a declared story flag) |
| warning | `possesses_encounter_reward_item_id` | a predicate `possessesItemId` names an encounter-reward item (use `possessesItemName`) |
| warning | `unread_director_rule` | a director-rule key the engine does not read |
| warning | `prose_in_complete_when` | checkpoint `completeWhen.anyOf` holds prose instead of encounter outcome labels |
| warning | `unread_checkpoint_condition_key` | checkpoint `completeWhen`/`failWhen` has a key progress does not read (for example `summary`) |
| warning | `starting_campaign_flags_alias` | `startingState.campaignFlags` is used (prefer `flags`) |
| warning (error under `--strict`) | `story_flag_set_by_pack` | a `storyFlags` key that an object's `setsFlags`, an encounter (outcome, reward or hook), a quest (objective or `onComplete`/`onFail` consequence) or a location's `onEnter` also sets; the message names the first one. Narration may set any declared flag, so talk can set it before that does |
| warning | `npc_listed_nowhere` | a person with `knows` lines is neither `visibleAtStart` (nor a starting `knownNpcIds`/`activeNpcIds` entry) nor in any location's `npcIds`, so the party can never talk to them |
| warning | `unsupported_scene_type` | a location's (or its `sceneState`'s) `sceneType` is not a supported scene type; the place is entered as `exploration` |
| warning | `location_description_addresses_players` | a location `description` or `playerDescription` contains "the players" (any case); it is printed on arrival |

`lint` exits non-zero on errors only; `lint --strict` also fails on warnings
and reports `story_flag_set_by_pack` as an error.

The `report` command prints an author-facing validation summary for starting references, populated authored collections, visible-at-start records, hidden player records, checkpoint reachability, optional/terminal checkpoints, encounter checkpoint linkage, and loose encounters. Add `--json` when release evidence or external authoring tools need the structured report.

The frontend import dialog exposes the same lint, graph, and authoring report preview so authors can edit JSON and inspect warnings before creating a campaign.

The API endpoint `POST /api/campaigns/pack-tools/lint` returns `ok`, `issues`, `summary`, `preview`, `graph`, and `authoring_report`. Warnings do not block import; errors do.

`POST /api/campaigns/pack-tools/forge` creates a deterministic version-1 draft
from a required `title` plus optional `prompt`, `tone`, and `packId`. It returns
the draft pack, wrapped import payload, serialized manifest text, and lint
result; it does not import or persist the draft. Both pack-tool endpoints
require `dm_authoring`.

All bundled example packs, including the internal Bleakmoor fixture, lint
without errors. The Road of Unremembered Kings also passes `lint --strict`; the
others still carry warnings (non-mechanical objectives, missing location
connections, prose completion text, unread director rules).

## Installed Pack Library

Every successful import stores the validated manifest in `installed_campaign_packs`. Workspace admins can inspect and reuse that library:

- `GET /api/campaigns/installed-packs`
- `GET /api/campaigns/installed-packs/{installed_pack_id}`
- `POST /api/campaigns/installed-packs/{installed_pack_id}/import`

The import-from-installed endpoint creates another campaign from the stored manifest and accepts optional `world_id`, `worldId`, `session_name`, `sessionName`, `dry_run`, and `dryRun` fields. This supports repeat starts, version comparison by `pack_hash`/`pack_version`, and future marketplace/library flows without requiring authors to re-upload a file.

## Checkpoint Controls

Pack progress is written to durable campaign-pack progress tables and mirrored into `Session.state_snapshot.campaignPack` plus `Session.state_snapshot.flags` for prompt/runtime speed.

Use `GET /api/sessions/{session_id}/campaign-pack/progress` to inspect the active, completed, skipped, and available checkpoints.

Use `POST /api/sessions/{session_id}/campaign-pack/progress` with:

```json
{
  "action": "advance",
  "checkpointId": "cp_old_road",
  "reason": "Manual table correction"
}
```

Supported actions:

- `advance`: complete the active checkpoint and move to the next checkpoint or the supplied checkpoint.
- `skip`: mark the active checkpoint skipped/completed and move downstream.
- `fail`: mark the active checkpoint failed and move to `failureCheckpointIds` or the next available downstream checkpoint.
- `rewind`: move back to the last completed checkpoint or supplied checkpoint.
- `override`: set the active checkpoint to `checkpointId` without treating it as completed.

Manual checkpoint controls record append-only `campaign_pack.progress.changed` events in both `TurnEvent` and `campaign_pack_progress_events`. Pass `expectedRevision` or `expected_revision` on POST requests to reject stale manual controls.

Progress mutations serialize through the per-session turn coordinator. Shared
groups acquire every active member lease in deterministic order, discard clean
pre-wait discovery identities, and revalidate membership from fresh rows after
the leases are held. A membership change retries before mutation, and the full
lock set remains held through commit or rollback so a waiting state mutation or
canon projection cannot replace a foreground snapshot commit. The frontend
exposes advance, skip, fail, rewind, and override controls only to actors with
operator capability; the server remains authoritative for authorization and
revision checks.

If a pack declares `multiSessionGroupKey`, progress changes propagate to other active sessions that imported the same pack ID with the same group key. This is intended for parallel-party or shared-world campaigns; omit the key for independent runs.

## Branching Semantics

Checkpoint graph fields:

- `nextCheckpointIds`: normal downstream beats.
- `branchWhen`: optional `{ "flagKey": "...", "equals": ... }` predicate on a
  candidate checkpoint. The candidate is eligible only when the persisted
  runtime flag matches with the same scalar type and value.
- `alternateCheckpointIds`: downstream beats that can complete the current beat when reached by another route.
- `prerequisiteCheckpointIds`: beats that must be resolved before this checkpoint can become active.
- `prerequisitePolicy`: `completed`, `completed_or_skipped`, `completed_or_skipped_or_failed`, or `terminal`.
- `optional`: marks a beat as non-blocking when the tracker is choosing the next linear checkpoint.
- `failureCheckpointIds`: fallback beats used when a checkpoint fails. A
  failure never advances onto a terminal checkpoint: when the only way on is the
  ending, the checkpoint stays active so the party can try again. A party defeat
  (every hero down) fails a checkpoint through its `failWhen.encounterIds` only
  when the encounter or its combat sets `lethalDefeat: true`; either way the
  party wakes at the end of the fight. Fleeing (`players_fled`) fails it either
  way.
- `terminal`: marks an end/finale checkpoint. Once reached it stays the active
  checkpoint with status `reached` (it never completes itself away), and the
  player projection carries `ending: {checkpointId, title, summary, text}` from
  its player-safe title and summary. Player checkpoint payloads include
  `terminal: true|false`.
- `endingVariants`: on a terminal checkpoint, `[{"when": {"flagKey", "equals"},
  "title", "text"}]`. The first variant whose flag holds names the ending
  (`ending.title`, the journal's `stageTitle`, the `The story ends — {title}`
  beat) and tells it (`ending.text`, the journal's `stageSummary`); with no
  match the checkpoint's own player title and summary do. The linter errors on
  a variant without a flag predicate, title and text, and warns about a flag
  the pack never sets or variants on a checkpoint that is not terminal. From the
  Play Now pack's last checkpoint:

  ```json
  {"id": "cp_07_road_remembers", "terminal": true,
   "endingVariants": [
     {"when": {"flagKey": "ward_renewed", "equals": true}, "title": "The Road Remembers",
      "text": "The lanterns burn steady along the western roads, and the names on the last stone no longer fade. ..."},
     {"when": {"flagKey": "ward_broken", "equals": true}, "title": "Only a Road",
      "text": "No ward keeps the western roads now. ..."}]}
  ```
- `chapter` and `act`: group checkpoints for authored graph views.
- `priority`: authoring metadata for director and graph views. Runtime branch
  selection currently uses the authored `nextCheckpointIds` order.
- `gate`: declares soft, hard, optional, or no gate behavior.
- `canCompleteOutOfOrder`: allows a checkpoint to complete without becoming the active spine.
- `playerTitle` and `playerSummary`: player-safe labels used by filtered progress payloads.
- `completeWhen`: conditions that complete a checkpoint; any one suffices. Read keys: `locationIds` (party is there), `segmentIds` (segment triggered), `objectiveIds` (completed, scoped to `questIds` when given), `questIds` (completed), `encounterIds` (ended with an allowed outcome; `anyOf` lists the allowed encounter outcome labels), `clueIds` (all discovered), and `flags` (`{key: value}`, all must match `snapshot.flags` with the `branchWhen` comparison rules). Prose in `anyOf` and other keys such as `summary` are ignored.
- `failWhen`: `questIds`, `objectiveIds`, or `encounterIds` conditions that fail a checkpoint.
- `gmNotes`: kept with the checkpoint for operators; never projected to players.
- `directorRules`: checkpoint-specific policy overrides merged over the pack-level director rules while active.

When `completeWhen` is present, only those explicit predicates complete the checkpoint. Without `completeWhen`, `locationIds` complete a checkpoint only when the checkpoint does not also declare objective, segment, or encounter completion cues.

When several downstream checkpoints are authored, candidates with `branchWhen`
are selected from authoritative `snapshot.flags`; narration and recent-memory
text are not branch inputs. Authors should include an unconditional downstream
candidate as the fallback route. Existing packs without `branchWhen` retain
their ordered first-available behavior.

Pack encounter completion is tied to checkpoints through `checkpoint.encounterIds` and encounter `completion.anyOf`. Supported completion outcome labels include `defeat`, `bargain`, `negotiate`, `surrender`, `flee`, `objective`, `resolve`, and `success`. This lets a checkpoint complete through combat, negotiation, surrender, flight, or objective resolution without forcing one tactical answer.

Pack encounter activation is also supported. When a `combat.start` change references `campaignPackEncounterId`/`encounterId`, or the active checkpoint has `encounterIds` and no explicit non-pack enemy participants, AIDM materializes the authored encounter, instantiates enemies from the campaign-pack bestiary, preserves player participants, and stamps combat flags with the pack encounter, checkpoint, enemy, and allowed-outcome IDs. A hostile player action starts the active checkpoint's encounter this way.

### Encounters that start themselves

An encounter may carry a `trigger`:

- `{"type": "on_enter", "locationId": "..."}`: the party arrives at the location;
- `{"type": "on_checkpoint", "checkpointId": "..."}`: the checkpoint is active;
- `{"type": "on_flag", "flagKey": "...", "equals": <value>}`: the flag is set to
  that value (`equals` defaults to `true`);
- `{"type": "on_interactable", "targetId": "...", "action": "open"}`: that
  object action succeeds (any action when `action` is omitted).

`once` (default `true`) fires the trigger only once; `when`
(`{"flagKey", "equals"}`) must hold and `unless` must not, with the
`branchWhen` comparison rules. Triggers are evaluated against the changes
applied before narration and again against the changes narration produces.
When one is due and no combat is active, combat starts in that turn through
the same pack-encounter materialization a hostile action uses; before
narration the DM receives the started combat (participants and initiative) as
an authoritative result. The encounter id is recorded in
`flags.campaignPackTriggeredEncounterIds`. `on_checkpoint` encounters start
during the first turn's combat preparation after the checkpoint becomes
active.

### Encounters fit the party

Enemy counts and numbers are authored for four player characters. With fewer
heroes present the encounter is scaled when its combat is created:

| Heroes present | Enemy HP (rounded up) | Enemy damage rolls (rounded down, at least 1) | Enemy attack bonus and save DCs | At most |
| --- | --- | --- | --- | --- |
| 1 | × 0.30 | × 0.40 | −3 | 1 enemy |
| 2 | × 0.60 | × 0.70 | −1 | 3 enemies |
| 3 | × 0.80 | × 0.85 | ±0 | 4 enemies |

Each group keeps `max(1, round(count × heroes / 4))` enemies (halves round up);
while the total exceeds the most, whole groups drop from the end (never the
first), then the last group loses enemies. Four or more heroes, or
`scaling: false` on the encounter, keep the authored numbers. The result is
recorded in `combat.flags.partyScaling`. A set-piece duel whose enemy keeps its
authored hit points and damage whatever the party size:

```json
{"id": "enc_champion_duel", "enemyGroups": [{"enemyId": "enemy_champion", "count": 1}], "scaling": false}
```

### What the party knows adjusts the fight

`adjustments` apply when the encounter's combat is created, before party
scaling. Each one whose `when` flag holds either multiplies one enemy group's
HP (`enemyId` with `hpMultiplier`, 0.25–1.5, multiplied with the scaling
multiplier) or keeps groups away (`removeEnemyIds`, removed before the most
enemies is applied). Its `text` must be told before the first blows: the
scripted narrator prints it after the encounter's `setup`, and a narrating model
is given it as a line it must convey. From the Play Now finale:

```json
"adjustments": [
  {"when": {"flagKey": "true_name_known", "equals": true}, "enemyId": "enemy_cinder_scribe",
   "hpMultiplier": 0.75,
   "text": "You speak his name — Maedran Voss — and the ash-ink on his hands runs like water."},
  {"when": {"flagKey": "roadwardens_appeased", "equals": true},
   "removeEnemyIds": ["enemy_name_eaten_roadwardens"],
   "text": "The nameless dead stand aside. They will not fight for him."}
]
```

### Read-aloud setup, parley and lethal fights

- `setup` is the encounter's read-aloud line, told when its fight starts and
  before any adjustment line (`"setup": "\"A bell sounds from somewhere ahead.
  One by one, pale lights appear between the rain-dark stones.\""` on
  `enc_pale_lantern_detour`). `stakes` and `gmNotes` stay with the operator.
- Enemies do not yield, surrender, negotiate or retreat until they are at or
  below half HP, their side has lost a member, or it is round 4 or later and
  they have been hurt. `"allowParley": true` lifts that from the first round
  (for an encounter meant to be talked down; the Play Now pack sets none).
- A hero is never killed: where they would die they are out of the fight, and
  when the fight ends with any hero standing every downed hero comes round with
  1 HP at once. When every hero is down the party is beaten, never killed: at the end of the
  fight it wakes later with 1 HP (the clock moves on one phase), the encounter
  re-arms, and no checkpoint fails. `"lethalDefeat": true` only makes the defeat
  decisive: it fails the checkpoint that names the encounter in `failWhen`, so
  the story takes that checkpoint's failure route, and the party still wakes
  (see [gameplay systems](gameplay_systems.md)). Consequences authored under the
  `defeat` outcome still apply when the party is beaten, so never set a
  story-ending flag there; the Play Now finale has no `defeat` outcome, and a
  lost or fled fight never ends the story.

### Enemy names and condition durations

`singularName` names one member of a group enemy and `proper: true` marks a
proper noun that is never pluralised or given an article. An ability's
`durationRounds` (1–10, default 1: until the end of the target's next turn) says
how many of the target's turns its `conditionsApplied` last; an ability with a
save repeats it at the end of each of those turns. From the Play Now pack:

```json
{"id": "enemy_pale_lanterns", "name": "Pale Lanterns", "singularName": "Pale Lantern",
 "abilities": [{"id": "pale_lantern_lure", "name": "Lantern-Lure", "save": {"ability": "wis", "dc": 13},
                "conditionsApplied": ["charmed", "drawn_aside"], "durationRounds": 2}]}
{"id": "enemy_cinder_scribe", "name": "The Cinder Scribe", "proper": true, "challengeTier": "boss"}
```

Narration refers to one of an enemy as "it" unless the record gives a
`pronoun` (`he`, `she`, `they` or `it`; a `proper` name without one is
"they"): `You get an arm around the ashmarked cutthroat and bear it down.`

### Captives

An enemy the heroes bind (captured) or that yields stays in the scene as a
captive when the fight ends, until the party travels on. `captiveKnows` (optional,
up to 20 lines) is what one tells under questioning, a line per question in
order; once they run out, or without the field, it `glares and says nothing more
you can use.` An AI narrator is given the enemy record and these lines as a
person present. From the Play Now pack:

```json
{"id": "enemy_ashmarked_cutthroats", "name": "Ashmarked Cutthroats", "singularName": "Ashmarked Cutthroat",
 "captiveKnows": [
   "The road. Lately it don't take you where you're going. It takes you where it wants.",
   "We were paid in ash-marked coin to bring names, ledgers and stone. I never saw who paid.",
   "There's a man they call the Scribe. He don't want the road dead. He wants it to answer to him."]}
```

## Meeting People

Arriving at a place (any applied `scene.move_location`) meets every person its
`npcIds` lists who is not yet known: they join `knownNpcs` as a
`visibleAtStart` person is seeded at the start (status `met` unless the record
has one) and the scene's present cast, so the Story view offers `Talk to …` and
typed talk reaches them. The arrival's applied change records `metNpcIds` in the
place's order, and the turn gets one beat per person, `Met — Radan Greycloak`.
A `visibleAtStart` person is unchanged, and a person in no place's `npcIds` is
never met by arrival.

Two NPC fields hold a listed person back:

- `"hidden": true`: never met by arrival; only an operator or an explicit
  change reveals them.
- `"hiddenUntil": {"flagKey": "...", "equals": true}` (`equals` defaults to
  `true`): met by arrival only once the flag holds. When a flag change makes it
  hold while the party is at their place, they are met then (that change
  records `metNpcIds`) and join the present cast.

A person hidden from players (`hiddenToPlayers`) is not met by arrival either.
Until a held-back person is met they stay out of the present cast, and nothing
about them (name, `knows`, `secret`) reaches a player. A session that is at a
place whose listed people are not yet known (the starting place, or a save from
before arrival met people) meets them with the next change applied there.

```json
{"locations": [{"id": "loc_old_mill", "npcIds": ["npc_miller", "npc_drowned_girl", "npc_smuggler"]}],
 "npcs": [{"id": "npc_miller", "name": "Hesk the Miller", "knows": ["The wheel stopped the night the river ran red."]},
          {"id": "npc_drowned_girl", "name": "The Drowned Girl", "hiddenUntil": {"flagKey": "mill_wheel_turned", "equals": true}},
          {"id": "npc_smuggler", "name": "Corran Vey", "hidden": true}]}
```

## Arrival, Objects, and Discoveries

A place is entered with its `sceneType` when that is one of `social`,
`exploration`, `travel`, `dungeon`, `rest`, `mystery`, `shopping` or `dialogue`
(`combat` is entered as `exploration`); without one, or with any other value,
it is entered as `exploration`, and the linter warns about the other value.

A location's `onEnter` list holds trusted consequences applied once, on the
party's first arrival (recorded in `flags.locationEnteredIds`; the starting
location counts at import). It accepts the quest consequence types below.

Quest `onComplete`/`onFail` and location `onEnter` consequences may be
`flag.set`, `flag.unset`, `npc.update`, `npc.relationship.update`,
`location.update`, `scene.update`, `clue.discover` (`{"clueId"}`), `quest.add`
(`{"questId"}`), and `location.discover` (`{"locationId"}`). Records named by id
are materialized from the catalog, and a `location.update` naming a catalog
location that is not yet known discovers it first.

Objects resolve themselves on the server:

- A requirement `check` (`{"id", "dc", "ability", "skill"}`) is rolled when the
  action is submitted: one d20 seeded from the action id plus the character's
  modifier (proficiency and wounds from the character sheet, advantage and
  bonus dice from the modifier engine). The roll, total, DC, and outcome are
  stored on the event (`event.check`) and returned with the action result. The
  DC defaults to 10; `"showDc": true` on the check lets players see it in the
  offered action and the check beat. A failed check is an applied attempt
  (`interactable.check_failed` or `hazard.check_failed`); the character is
  remembered in the object's `failedCheckActorIds`. Trying again as before is
  refused ("You have already tried that; something has to change first."), but
  outside a fight the hero may take their time: the action is offered again with
  `retry: true`, and taking it with `take_time` advances the clock one phase and
  resolves the check as if the die showed 20.
- `mechanicalEffects` keyed by action, or by `failure:<action>` for a failed
  check, apply `damage` (`{"dice", "type", "save": {"ability", "dc",
  "onSuccess": "half" | "none"}}`) and `conditions` (`[{"condition",
  "rounds"}]`) to the `actor` (default) or the whole `party`. Damage follows
  the vitality rules; conditions apply to combatants only.
- On a successful action, `discoversClueIds`, `setsFlags`, and
  `discoversLocationIds` (each keyed by action) discover catalog clues, set
  flags, and add catalog locations to the known places with status
  `discovered`.
- Contents a container reveals (on open, or all of them on search) become
  scene items with `containerId`, so each can be taken with the exact-id scene
  pickup; the container still lists them until they are taken. A contents
  entry may be coin, `{"currency": {"gp": 15}}`: it becomes a pile named for
  its amount (id `<container id>_coins`), and taking it adds to the taker's
  coin. Narration of a reveal never takes what was revealed.
- `trigger` is offered on hazards and on any object marked `"triggerable": true`;
  once triggered, an object refuses the trigger again unless it is
  `"repeatable": true` or has been reset (hazards support `reset`).
- A requirement's `flags` may expect `false`: it holds while the flag is unset
  or false (`{"quest_road_complete": false}` offers a fate only until the quest
  is done); afterwards the action is refused with `That can no longer be done.`
- During a fight an object's actions are refused (`Not while the fight is
  on.`) unless listed in its `usableInCombat`; those are offered with the
  combat actions and cost the actor's action.

### Scene Object Fields

These fields shape what the Story view offers and what the narrator tells.
Every example is from the Play Now pack.

- `allowedActions` limits the actions an object supports; without it the
  object's kind and flags decide. Offered actions follow the fixed order search,
  use, trigger, open, unlock, inspect, close, lock, break, disarm, reset.

  ```json
  {"id": "inn_drivers_bench", "allowedActions": ["inspect", "search"]}
  ```

- `actionLabels` are the words the Story view offers for an action (at most 48
  characters); without one an action reads `Examine {name}`, `Search {name}`,
  `Open {name}` and so on. Typed words matching a label take that action.

  ```json
  {"id": "inn_drivers_bench",
   "actionLabels": {"inspect": "Look over the driver's bench", "search": "Search under the driver's bench"}}
  ```

- `actionText` is what an action achieved, told by the narrator when the action
  succeeds (at most 600 characters). It never reaches the player snapshot and is
  never told after a failed attempt.

  ```json
  {"id": "inn_wagon_bell",
   "actionText": {"use": "You strike the bell. It gives one low note that hangs over the yard longer than it should. ..."}}
  ```

- `usableInCombat` lists the actions a hero may take on the object during a
  fight, on their own turn while their action is unspent; taking one spends the
  action.
- `resolvesEncounter` settles an encounter peacefully when the named action
  succeeds: the encounter is completed with the outcome (`victory`, `surrender`,
  `negotiation` or `objective_completion`) through the same path a combat ending
  uses, its combat ends at once, and its trigger never fires again. With
  `usableInCombat` the oath can end the fight it averts:

  ```json
  {"id": "cairn_banner_stone",
   "usableInCombat": ["use"],
   "resolvesEncounter": {"use": {"encounterId": "enc_roadwarden_judgment", "outcome": "negotiation"}}}
  ```

- `choiceActions` marks actions that are alternatives to one another; each
  offered entry for a listed action carries `choice: true` and the Story view
  shows them together. The linter errors on an action the object does not
  support.

  ```json
  {"id": "valley_last_road_stone", "choiceActions": ["use", "break", "lock", "trigger"]}
  ```

- `proper: true` marks an object name that is a proper noun, so refusals read
  "Excalibur is locked." rather than "The Excalibur is locked."; a name starting
  with "The" is never given another article.

The linter warns about a label or text for an action the object does not
support.

### Encounter rewards and consequences

An encounter may declare common successful-outcome rewards in `rewards` (or the
compatibility alias `reward`). The compact object form accepts `xp` or
`experience`, currency keys `pp`/`gp`/`ep`/`sp`/`cp`, `items`, `flags`, and
`questEvents`; an array of explicit typed reward records is also accepted.
Outcome-specific rewards may be placed in
`outcomes.<outcome>.rewards` or `outcomeRewards.<outcome>`.

Use the canonical outcome keys `victory`, `defeat`, `retreat`, `surrender`,
`negotiation`, and `objective_completion`. Common rewards apply only to
victory, surrender, negotiation, and objective completion. A consolation reward
for defeat or a cost/recovery item after retreat must therefore be explicitly
authored under that outcome.

Actor-bound rewards support `split`, `each`, `first`, and `actor` allocation.
XP and currency default to `split`, preserving the authored party total; items
default to `first`. Set `allocation` on one reward, or use
`partyAllocation` at encounter/outcome scope. An explicit reward `actorId` must
be an exact eligible participant and never falls back to another character.
Only present player combat participants receive actor-bound rewards; defeated
or unconscious participants remain eligible, while absent participants do not.
Each awarded item instance receives its own deterministic ID.

Outcome consequences may be authored under
`outcomes.<outcome>.consequences`, `outcomeConsequences.<outcome>`, or the named
hooks `onVictory`, `onDefeat`, `onRetreat`, `onSurrender`, `onNegotiation`, and
`onObjectiveCompletion`. The bounded consequence types are `flag.set`,
`flag.unset`, `npc.update`, `npc.relationship.update`, `npc.move`,
`location.update`, `location.discover`, `scene.update`, `scene.item.add`,
`scene.item.remove`, `clue.discover`, and `quest.add`; records named by id are
materialized from the catalog. A `combat.end` for a combat that has already
ended grants nothing again.
Use quest events rather than direct `quest.complete` or `quest.fail`
consequences so the quest engine remains authoritative.

Every ID in encounter `questIds` receives a default `combat.outcome` event.
Additional events may declare exact `questId`, optional `objectiveId`, and
`eventType` in the encounter or outcome `questEvents` list. These events can
satisfy authored mechanical quest predicates, but they do not bypass objective
prerequisites or terminal validation.

Reward derivation runs only after persisted `combat.end` state agrees with the
stable encounter and end reason. Outputs, quest events, and the final reward
marker use ledger-stable IDs. A retry after a partial application queues only
the missing outputs, and the transaction is not finalized until every required
ID exists. Encounters without a stable ID, interrupted/mismatched outcomes, and
unsupported records award nothing. Rewards are authored; the engine does not
infer arbitrary loot or XP from narration.

Example:

```json
{
  "id": "enc_bridge_guard",
  "title": "The Bridge Guard",
  "questIds": ["q_open_the_road"],
  "rewards": {
    "xp": 120,
    "gp": 18,
    "items": [{"id": "bridge_seal", "name": "Bridge Seal", "quantity": 1}],
    "flags": [{"flagKey": "bridge_secured", "flagValue": true}]
  },
  "partyAllocation": {"xp": "split", "currency": "split", "item": "first"},
  "outcomes": {
    "negotiation": {
      "questEvents": [
        {"eventType": "bridge_terms_agreed", "questId": "q_open_the_road"}
      ],
      "consequences": [
        {"type": "flag.set", "flagKey": "guards_spared", "flagValue": true}
      ]
    },
    "defeat": {
      "consequences": [
        {"type": "flag.set", "flagKey": "bridge_held_by_enemy", "flagValue": true}
      ]
    }
  }
}
```

## First-Class Pack Content Actions

Campaign packs can now drive mystery, faction, map, handout, and lore state through first-class changes:

- `clue.discover`
- `clue.update`
- `faction.discover`
- `faction.relationship.update`
- `map.reveal`
- `map.region.update`
- `handout.reveal`
- `lore.unlock`

Pack-authored records are materialized from `campaignPack.catalog`; non-pack additions are tagged as emergent or blocked according to the active director rules.

## Merchants

The top-level `merchants` list:

```json
{"id": "mara_counter", "name": "Mara's Counter", "npcId": "npc_mara", "locationId": "loc_inn",
 "buyRate": 0.5, "restock": "long_rest",
 "stock": [{"name": "Potion of Healing", "type": "consumable", "effectId": "potion_healing",
            "price": {"gp": 50}, "quantity": 3},
           {"catalog": "torch", "quantity": 10}]}
```

`buyRate` defaults to 0.5 and `restock` to `long_rest` (or `never`); a stock
`quantity` defaults to unlimited. The importer writes `snapshot.merchants[<id>]`
in the runtime trade shape: every stock entry gets a stable `stockId`
(`<merchant id>_<index>`), a priced entry keeps `name`, `type` (default
`gear`), `effectId`, `metadata`, `price`, and `quantity` (`null` = unlimited),
and a `{"catalog": key}` entry is stored as `{"stockId", "catalog", "quantity"}`
for the trade rules to price. Either kind may carry `itemId`: the item a
purchase puts in the inventory keeps that id, so a requirement such as
`anyToolIds` can name it. `initialStock` is a copy of the stock. A merchant
needs an `npcId` or a `locationId`, and prices must be positive whole `gp`,
`sp`, or `cp` amounts.

## Rest Policy

A location may carry `restPolicy` (`safe`, `risky`, or `none`); it is copied to
`currentScene.restPolicy` at import and on every arrival. Without one, a place of
danger 3 or more is `risky` (short rests only) and 6 or more `none`.

```json
{"id": "loc_lantern_post_inn", "restPolicy": "safe"}
```

## Clock Start

`startingState.clock` sets when the adventure begins (`phase` is `morning`,
`afternoon`, `evening` or `night`); without it the party starts on day 1 in the
morning. The Play Now adventure opens at dusk:

```json
"startingState": {"clock": {"day": 1, "phase": "evening"}}
```

## World Map

The top-level `map` (`{"title", "terrain", "seed"}`, terrain one of `moor`,
`forest`, `mountain`, `coast`, `plains`, `marsh`), per-location `mapPosition`
(`{"x", "y"}` on a 0-100 grid) and `mapIcon` (`inn`, `town`, `road`, `bridge`,
`ruin`, `tower`, `forest`, `cave`, `camp`, `shrine`, `cairn`, `valley`, `gate`,
`marsh`, `peak`, `unknown`), and top-level `routes`
(`[{"from", "to", "style": "road" | "trail" | "hidden"}]`, default `road` for
every adjacency) describe the map. Locations without a position are placed at
import by a seeded force-directed layout over the adjacency graph (normalized
to 8-92 on both axes, at least 14 apart while the map has room) and the
positions are stored. From the Play Now pack:

```json
{"map": {"title": "The Western Roadlands", "terrain": "moor", "seed": 1107},
 "routes": [{"from": "loc_old_north_road", "to": "loc_ridge_and_culvert_paths", "style": "trail"},
            {"from": "loc_ridge_and_culvert_paths", "to": "loc_valley_last_road_stone", "style": "hidden"}],
 "locations": [{"id": "loc_lantern_post_inn", "mapPosition": {"x": 22, "y": 82}, "mapIcon": "inn"}]}
```

The player snapshot carries `worldMap`:
`{"title", "terrain", "seed", "nodes": [{"id", "name", "icon", "x", "y",
"state"}], "routes": [{"from", "to", "style", "travelable"}]}`. A node is
`current`, `visited` (the party has been there), `known` (discovered, not
visited), or `rumoured` (an undiscovered neighbour of a visited place, shown as
`?`); nothing else appears. `hidden` routes appear only when both ends are
discovered. Campaigns without a pack map their known locations with a computed
layout.

## Story Flags

`storyFlags` declares the flags narration may set:

```json
[{"key": "mara_trusts_party", "when": "Mara decides she can trust the party with what she saw.",
  "values": [true]}]
```

`values` defaults to `[true]`; keys must be `[a-z0-9_]+`. A narrated `flag.set`
is accepted only for a declared key and one of its values; every other narrated
flag change is rejected. The DM context lists the flags with their `when` text
and current value under `campaign_pack_director.storyFlags`, and the
post-narration extraction may propose them. Players never see `storyFlags`. A
`branchWhen` on a story flag does not raise `branch_flag_never_set`. Declare
only flags that talk alone decides: a declared key that an object, encounter,
quest or arrival also sets raises `story_flag_set_by_pack`, because narration
can set it from a line of talk before the object or fight does.

## Hidden Information

Operator/DM views can see the full catalog, `gmNotes`, `hiddenSceneNotes`, future checkpoints, and progress-event audit data. Player-facing session state and progress payloads filter hidden catalog records, future checkpoint details, alternate routes, director rules, hidden notes, the starting brief, and the raw `stateChangeLedger`. The player projection is an allowlist: it shows the opening scene's `summary` and `visibleDetails` but never `hiddenSceneNotes`, and never shows NPC `personality`, `motivation`, `knows`, `secret`, `useInPlay`, `ifSkipped`, `trueName`, encounter `setup` (it is only read aloud when the fight starts) or `stakes`, object `actionText`, or any `gmNotes`.

The player view also carries a `journal` block built from what the party knows:
the quest's `playerSummary`, the active checkpoint's `playerTitle` and
`playerSummary` as the current stage, completed objectives and the active
checkpoint's open ones (never future objectives), discovered clues, people met
(with their `playerSummary` and a readable `role`), and places known. Author
`playerTitle`/`playerSummary` on every checkpoint and `playerSummary` on quests
and NPCs so the journal reads well.

Use `playerTitle`, `playerSummary`, `visibleToPlayers`, `knownToPlayers`, and `hiddenToPlayers` to control what filtered progress payloads may reveal.

`hiddenToPlayers` and equivalent DM-only metadata are fail-closed. A hidden
checkpoint does not reveal its authored title, summary, or ID merely because it
becomes active, completed, skipped, or failed. Player active/completed/skipped/
failed ID lists and flags are filtered through the same projection. To expose a
safe player-facing milestone while keeping the authored checkpoint hidden,
provide `playerTitle` or `playerSummary`; only that alias and current status are
projected.

## Off-Track Support

The campaign-pack director computes an off-track packet with:

- `locationOffTrack`
- `questOffTrack`
- `npcDependencyBroken`
- `checkpointBypassed`
- `requiredClueDestroyed`
- `combatOutcomeDiverged`
- `rejoinTargetConfidence`
- `offTrackScore`
- `offTrackReasons`

The DM prompt receives those details with the active checkpoint, next checkpoint candidates, known relevant records, GM-only notes, and rejoin target.

## Current Limits

- Locations, NPCs, quests, segments, checkpoints, encounters, clues, factions, maps, handouts, lore, and merchants: 250 records each.
- Enemies: 150 records.
- Record IDs: 120 characters; use lowercase `[a-z0-9_]` IDs everywhere.
- Titles: 120 characters.
- Names: 160 characters.
- Any string in a record or pack-level value: 4000 characters.
- Nesting: a container may sit at most 12 levels below its record (the record
  is level 0, so `quests[0].objectives[0].completeWhen` is level 3).
- Objects: 80 keys (keys up to 120 characters). Lists inside records: 150 items.

Content beyond a limit fails the import with `pack_content_out_of_bounds` and
its JSON path; the linter reports every such path as an error.
