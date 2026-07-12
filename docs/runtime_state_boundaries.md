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

## Dice And Turn Authority

Player roll requests may select a supported die or mode and identify the
intended check, but client-provided faces, kept values, modifiers, totals, and
natural-language claims are never authoritative. `aidm_server/player_rolls.py`
uses a cryptographically secure server roll and derives modifiers from the
persisted player record and any persisted pending-roll specification.

The canonical roll is saved with the incoming `DmTurn` and one durable
`ROLL_RESOLVED_EVENT` in the same transaction. Only after that commit does the
server broadcast `roll_resolved`. Retrying an uncertain turn must reuse the
original `client_message_id`. A completed duplicate returns the existing turn;
an incomplete `processing` turn replays the persisted private roll receipt to
the requester and resumes narration from persisted pre-DM pipeline metadata.
Neither path rolls again, creates a second incoming or roll event, rebroadcasts
the result to peers, or reapplies already-recorded pre-DM state. The frontend
animation represents the committed result and never generates gameplay truth.

Detailed ability score, proficiency, wound, DC, and attack provenance is private
to the acting player and operator records. Room-wide roll events retain only the
shared die, mode, faces, kept value, aggregate modifier, total, and reason.

The player combat HUD is also a server projection, not persisted client truth.
`combat.legalActions` is derived for each owned player from the live snapshot
and persisted inventory. Stable action and target IDs are re-resolved under the
session turn lock before a turn is created; forged, stale, out-of-turn,
out-of-range, hidden, down, or fully covered targets are rejected. Current turn
order is enforceable. Separate action, movement, bonus-action, and reaction
counters are not persisted, so the projection marks those sub-turn counters as
untracked instead of claiming full tabletop action-economy enforcement.

## Player-Visible Projections

The stored session snapshot remains complete server-side. Non-admin session
list, workspace, state, and export responses are account-scoped projections:

- characters owned by the requesting account retain their full runtime detail;
- other party members expose public identity plus bounded shared combat HP,
  conditions, and alive/conscious state;
- peer statistics, character sheets, inventory, spells, resources, abilities,
  armor details, metadata, state-change ledgers, and hidden campaign-pack data
  are removed;
- accountless workspace/table credentials own no private player record and
  cannot bind a socket to a guessed player;
- clarification actions, inventory options, and state-pipeline detail remain
  visible only to the acting player and operators;
- raw canon and campaign/region bestiary catalogs are operator reads, while
  player Chronicle HTML retains only public prose and revealed chapter titles;
- explicit export selection of a player the requester does not own returns
  `404`; workspace administrators keep the complete operator view.

Do not persist a redacted response back over `Session.state_snapshot`. Redaction
is a response boundary, not a mutation or alternate source of truth.

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
the live quest system. Raw segment routes and untriggered rows are DM-authoring
data. Players receive only triggered public story fields; durable events may
retain trigger reason/spec data for operators, but player room events do not.

`Map` records store authored map assets and metadata. They are not the runtime
location graph for navigation unless a feature explicitly projects map data
into `Session.state_snapshot`. Their `visibility` is either `player` (revealed)
or `dm` (DM-only). Existing records default to `player`; player REST/workspace
queries and the map inspector exclude `dm` rows, while authoring views retain
both. Socket.IO and backend session exports do not serialize authored `Map` rows. If a
future backend realtime or export contract adds them, it must reuse
`aidm_server.map_visibility.visible_maps_query` rather than copying stored rows.
The browser's richer JSON export includes its already viewer-filtered map list;
non-operators defensively retain only `visibility=player` entries.

Visibility is currently map-wide, not a per-layer permission system. Keep
secret layers in a separate DM-only map until a versioned layer schema defines
its own player projection.
