# Campaign Packs

Campaign packs are structured adventure modules that seed an AIDM campaign with authored locations, NPCs, quests, enemies, encounters, segments, checkpoints, and director rules.

The current contract is version `1`. The JSON Schema lives at [campaign_pack.schema.json](campaign_pack.schema.json), and a runnable example lives at [examples/bleakmoor_intro_campaign_pack.json](examples/bleakmoor_intro_campaign_pack.json).

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
- `Session.state_snapshot.campaignPack`, including the full hidden pack catalog and director rules

## Compatibility

- `schemaVersion` defaults to `1`.
- Accepted values are `1`, `1.0`, and `1.0.0`; they are normalized to `1`.
- Unsupported schema versions return `unsupported_schema_version`.
- Unknown fields are preserved where practical, so pack authors can add future metadata without breaking imports.
- AIDM treats `Campaign.location` and `Campaign.current_quest` as import/backcompat fields. Live play state comes from `Session.state_snapshot`.

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
- `directorRules`

## Content Sources

Imported pack-authored records are tagged with:

```json
{
  "source": "campaign_pack",
  "packId": "bleakmoor_intro"
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
| `invalid_pack_reference` | A pack record references a location, NPC, quest, segment, enemy, encounter, or checkpoint ID that is not defined in the pack. |
| `invalid_checkpoint_graph` | Checkpoint `nextCheckpointIds` form a cycle. |
| `unsupported_schema_version` | `schemaVersion` is not accepted by this AIDM build. |
| `world_not_found` | The pack references an existing world that does not exist in the current workspace. |
| `campaign_pack_import_failed` | Import failed after validation due to an unexpected persistence error. |

## Checkpoint Controls

Pack progress is stored in `Session.state_snapshot.campaignPack` and mirrored in `Session.state_snapshot.flags`.

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

## Branching Semantics

Checkpoint graph fields:

- `nextCheckpointIds`: normal downstream beats.
- `alternateCheckpointIds`: downstream beats that can complete the current beat when reached by another route.
- `prerequisiteCheckpointIds`: beats that must be resolved before this checkpoint can become active.
- `prerequisitePolicy`: `completed`, `completed_or_skipped`, `completed_or_skipped_or_failed`, or `terminal`.
- `optional`: marks a beat as non-blocking when the tracker is choosing the next linear checkpoint.
- `failureCheckpointIds`: fallback beats used when a checkpoint fails.
- `completeWhen`: state, quest, objective, segment, location, or encounter conditions that complete a checkpoint.
- `failWhen`: quest, objective, or encounter conditions that fail a checkpoint.
- `directorRules`: checkpoint-specific policy overrides merged over the pack-level director rules while active.

When `completeWhen` is present, only those explicit predicates complete the checkpoint. Without `completeWhen`, `locationIds` complete a checkpoint only when the checkpoint does not also declare objective, segment, or encounter completion cues.

Pack encounter completion is tied to checkpoints through `checkpoint.encounterIds` and encounter `completion.anyOf`. Supported completion outcome labels include `defeat`, `bargain`, `negotiate`, `surrender`, `flee`, `objective`, `resolve`, and `success`. This lets a checkpoint complete through combat, negotiation, surrender, flight, or objective resolution without forcing one tactical answer.

## Current Limits

- Locations, NPCs, quests, segments, checkpoints, and encounters: 250 records each.
- Enemies: 150 records.
- Record IDs: 120 characters.
- Titles: 120 characters.
- Names: 160 characters.
- Long text fields: 4000 characters.

Pack-authored map/clue/faction runtime controls are still expanding areas because the state pipeline does not yet expose first-class `map`, `clue`, or `faction` mutation types. Today those usually flow through flags, scene items, NPC/location metadata, and map endpoints.
