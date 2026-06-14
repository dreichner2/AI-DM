# Campaign Packs

Campaign packs are structured adventure modules that seed an AIDM campaign with authored locations, NPCs, quests, enemies, encounters, segments, checkpoints, clues, factions, maps, handouts, lore, and director rules.

The current contract is version `1`. The JSON Schema lives at [campaign_pack.schema.json](campaign_pack.schema.json), and runnable examples live in [examples/](examples/).

Current example packs:

- [examples/bleakmoor_intro_campaign_pack.json](examples/bleakmoor_intro_campaign_pack.json): internal test fixture for import and visibility checks; hidden from the player-facing campaign picker.
- [examples/shadow_over_the_greenway_campaign_pack.json](examples/shadow_over_the_greenway_campaign_pack.json): larger checkpoint-spine campaign with branches and encounter pressure.
- [examples/shadow_under_eryn_luin_campaign_pack.json](examples/shadow_under_eryn_luin_campaign_pack.json): larger multi-location campaign with hidden lore and finale state.
- [examples/the_road_of_unremembered_kings_campaign.json](examples/the_road_of_unremembered_kings_campaign.json): original full campaign with soft checkpoint pathing, redundant clues, factions, maps, handouts, lore, and multiple ending states.

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
- `Session.state_snapshot.campaignPack`, a compact runtime mirror containing the hidden pack catalog, director rules, progress, version metadata, shared-group key, and GM-only notes

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
- `directorRules`
- `dependencies`
- `mods`
- `multiSessionGroupKey`
- `gmNotes`
- `hiddenSceneNotes`

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

## Stable Import Errors

The importer returns these stable `error_code` values:

| Code | Meaning |
| --- | --- |
| `validation_error` | The body is not valid JSON, required fields are missing, fields are the wrong shape, IDs are duplicated, or starting references do not point at imported records. |
| `invalid_campaign_pack_schema` | The body failed the documented JSON schema contract, with a path such as `quests[2].objectives[1].status`. |
| `invalid_pack_reference` | A pack record references a location, NPC, quest, segment, enemy, encounter, or checkpoint ID that is not defined in the pack. |
| `invalid_checkpoint_graph` | Checkpoint `nextCheckpointIds` form a cycle. |
| `unsupported_schema_version` | `schemaVersion` is not accepted by this AIDM build. |
| `world_not_found` | The pack references an existing world that does not exist in the current workspace. |
| `campaign_pack_import_failed` | Import failed after validation due to an unexpected persistence error. |

## Pack Author Tools

Run the local pack tool before importing or publishing a pack:

```bash
PACK=docs/examples/the_road_of_unremembered_kings_campaign.json
python scripts/aidm_pack.py lint "$PACK"
python scripts/aidm_pack.py preview "$PACK"
python scripts/aidm_pack.py graph "$PACK"
python scripts/aidm_pack.py test-checkpoints "$PACK"
```

The linter uses the same import dry-run validator as the API, then adds authoring checks for unreachable checkpoints, missing completion cues, hidden records visible at start, large prompt-budget records, dependency declarations, and `pack_only` checkpoints without rejoin targets.

The frontend import dialog exposes the same lint and graph preview so authors can edit JSON and inspect warnings before creating a campaign.

The API endpoint `POST /api/campaigns/pack-tools/lint` returns `ok`, `issues`, `summary`, `preview`, and `graph`. Warnings do not block import; errors do.

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

If a pack declares `multiSessionGroupKey`, progress changes propagate to other active sessions that imported the same pack ID with the same group key. This is intended for parallel-party or shared-world campaigns; omit the key for independent runs.

## Branching Semantics

Checkpoint graph fields:

- `nextCheckpointIds`: normal downstream beats.
- `alternateCheckpointIds`: downstream beats that can complete the current beat when reached by another route.
- `prerequisiteCheckpointIds`: beats that must be resolved before this checkpoint can become active.
- `prerequisitePolicy`: `completed`, `completed_or_skipped`, `completed_or_skipped_or_failed`, or `terminal`.
- `optional`: marks a beat as non-blocking when the tracker is choosing the next linear checkpoint.
- `failureCheckpointIds`: fallback beats used when a checkpoint fails.
- `terminal`: marks an end/finale checkpoint.
- `chapter` and `act`: group checkpoints for authored graph views.
- `priority`: chooses among multiple reachable next checkpoints.
- `gate`: declares soft, hard, optional, or no gate behavior.
- `canCompleteOutOfOrder`: allows a checkpoint to complete without becoming the active spine.
- `playerTitle` and `playerSummary`: player-safe labels used by filtered progress payloads.
- `completeWhen`: state, quest, objective, segment, location, clue, or encounter conditions that complete a checkpoint.
- `failWhen`: quest, objective, or encounter conditions that fail a checkpoint.
- `directorRules`: checkpoint-specific policy overrides merged over the pack-level director rules while active.

When `completeWhen` is present, only those explicit predicates complete the checkpoint. Without `completeWhen`, `locationIds` complete a checkpoint only when the checkpoint does not also declare objective, segment, or encounter completion cues.

Pack encounter completion is tied to checkpoints through `checkpoint.encounterIds` and encounter `completion.anyOf`. Supported completion outcome labels include `defeat`, `bargain`, `negotiate`, `surrender`, `flee`, `objective`, `resolve`, and `success`. This lets a checkpoint complete through combat, negotiation, surrender, flight, or objective resolution without forcing one tactical answer.

Pack encounter activation is also supported. When a `combat.start` change references `campaignPackEncounterId`/`encounterId`, or the active checkpoint has `encounterIds` and no explicit non-pack enemy participants, AIDM materializes the authored encounter, instantiates enemies from the campaign-pack bestiary, preserves player participants, and stamps combat flags with the pack encounter, checkpoint, enemy, and allowed-outcome IDs.

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

## Hidden Information

Operator/DM views can see the full catalog, `gmNotes`, `hiddenSceneNotes`, future checkpoints, and progress-event audit data. Player-facing session state and progress payloads filter hidden catalog records, future checkpoint details, alternate routes, director rules, hidden notes, and the raw `stateChangeLedger`.

Use `playerTitle`, `playerSummary`, `visibleToPlayers`, `knownToPlayers`, and `hiddenToPlayers` to control what filtered progress payloads may reveal.

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

- Locations, NPCs, quests, segments, checkpoints, encounters, clues, factions, maps, handouts, and lore: 250 records each.
- Enemies: 150 records.
- Record IDs: 120 characters.
- Titles: 120 characters.
- Names: 160 characters.
- Long text fields: 4000 characters.
