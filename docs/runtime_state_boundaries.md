# Runtime State Boundaries

AIDM has multiple state representations because authored content, live game
state, legacy projections, campaign-pack progress, and long-term canon serve
different purposes. Use these boundaries when adding or repairing runtime
features.

## Live Runtime Truth

`Session.state_snapshot` is the live runtime game state once a session has a
snapshot. Systems that need current play state should read it for:

- `currentScene`;
- `playerCharacters`;
- inventory, health, XP, and currency;
- quests and locations;
- `knownNpcs` and `partyNpcs`;
- combat and campaign-pack runtime mirrors;
- flags; and
- `stateChangeLedger`.

Future live systems should use `Session.state_snapshot` unless they
intentionally need authored source data, durable campaign-pack progress,
long-term canon memory, or a compatibility projection.

## Validated Mutation Boundary

Normal gameplay state changes pass through the state extraction, validation,
and application pipeline and are persisted through
`persist_state_to_database`. Narrow session metadata changes use
`aidm_server/services/session_state_mutation.py`, including
`mutate_session_snapshot_metadata`, so they run under serialized coordination,
stamp the state revision, record source/change classifications, and create a
durable audit row.

Some import, repair, lifecycle, and test paths write snapshots directly. The
validated inventory of those exceptions is
[state snapshot writer inventory](state_snapshot_writer_inventory.md); keep it
current when adding a writer.

Turn metadata under `state_pipeline` is audit, debug, and per-turn pipeline
metadata. It explains extraction, validation, application, and summary results
for an individual turn; it is not the canonical session state.

## Projection And Summary State

`SessionState` is a projection and summary record for older context paths,
rolling summaries, current-location/current-quest summaries, active-segment
summaries, and memory snippets. It is not the live source of truth for mutable
runtime state after `Session.state_snapshot` exists.

`Campaign.location` and `Campaign.current_quest` are seed and compatibility
fields. They can initialize or support older views, but do not supersede a live
session snapshot.

## Campaign-Pack State

`campaign_packs`, `campaign_pack_records`, `campaign_pack_sessions`,
`campaign_pack_checkpoint_progress`, and `campaign_pack_progress_events` hold
durable pack definitions, bindings, progress, and progress history.

The snapshot `campaignPack` object is a runtime mirror assembled from those
records. Gameplay reads and updates the mirror through campaign-pack services;
durable progress writes remain the recoverable record outside the snapshot.
Do not treat either representation as a substitute for the other.

## Long-Term Canon Memory

`aidm_server/emergent_memory.py` contains canon extraction and normalization
logic; it is not a separate state store. Durable long-term canon is stored in
`story_entities`, `story_facts`, `story_threads`, `turn_canon_updates`, and
`canon_jobs`.

These records capture story knowledge that arose through play. They are not the
immediate mutable source for the current scene, active quest list, character
resources, combat state, or per-turn application.

## Authored Story And Map Data

`CampaignSegment` stores authored or planned story content. Segment activation
can project authored details into the live snapshot, but segment records are not
the live quest system.

`Map` records store authored map assets and metadata. They are not the runtime
location graph for navigation unless a feature explicitly projects map data
into `Session.state_snapshot`.
