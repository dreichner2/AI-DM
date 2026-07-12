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
- `locationSceneStates`, the backward-compatible per-location cache for scene
  items, environment details, active scene references, and spatial state that
  is saved and restored during validated travel (the cache is server-only;
  players receive the filtered `currentScene` projection instead);
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

Narration persistence does not authorize mechanics. If the primary post-DM
pipeline fails, the compatibility immediate-state path may apply the validated
changes. If that path also fails, the saved narration remains in the transcript
but the turn is marked `failed` with `post_dm_state.status=failed` and
`recovery_required=true`. Recovery metadata distinguishes `mechanics_status=none`
from `mechanics_status=partial`: partial means one or more authoritative pre-DM
changes in `state_pipeline.immediateAppliedChanges` or `combatAppliedChanges`
already committed and must not be replayed. The safe projection includes the
pre-DM applied-change count and always records
`post_dm_mechanics_applied=false`; the turn's privileged `state_pipeline`
retains the exact applied-change evidence. Structured turn advancement,
clarification completion, and canon enqueue stop; operators must inspect and
correct only the unapplied remainder rather than replaying the turn. A
safe `turnRecoveryGate` in `Session.state_snapshot` pauses subsequent player
turns with `session_recovery_required` while leaving join and read paths
available. The failed `DmTurn.post_dm_state` row is the fail-closed source of
truth: if writing the redundant snapshot gate fails, later submissions still
discover the unresolved turn, remain blocked, and repair the gate when storage
recovers. The recovery endpoint can resolve directly from that failed row, so
an operator never needs to provoke another player action first. The
`dm_runtime_control` recovery endpoint clears only the matching
turn after an operator records either `state_corrected` or
`no_mechanical_change_required` plus a bounded note. The original turn remains
failed, the resolution metadata retains the none/partial mechanics summary,
and the operator decision has durable turn and privileged audit records. Replay
idempotency includes a one-way fingerprint of the normalized note; a changed
resolution or note conflicts instead of silently discarding new operator text.

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

When a pending turn needs a roll, `roll_required` carries the pending turn ID,
remaining player IDs, and a public roll specification limited to die, mode,
rule type, reason, result visibility, and public ability key/label. The client
uses that guidance to configure the request; it still cannot submit faces,
modifiers, totals, DCs, or authoritative provenance. A rejected action draft is
kept separately from the roll request and restored after resolution.

The player combat HUD is also a server projection, not persisted client truth.
`combat.legalActions` is derived for each owned player from the live snapshot
and persisted inventory. Stable action and target IDs are re-resolved under the
session turn lock before a turn is created; forged, stale, out-of-turn,
out-of-range, hidden, down, or fully covered targets are rejected. Current turn
order is enforceable. Separate action, movement, bonus-action, and reaction
counters are not persisted, so the projection marks those sub-turn counters as
untracked instead of claiming full tabletop action-economy enforcement.
Unavailable targets remain in the viewer projection with a reason. The frontend
disables and explains them; omission is reserved for targets the viewer must not
know exist.

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
- memory snippets in the compatibility `SessionState` projection are presented
  as "Recent Memory," not as authoritative canon facts;
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

Player progress payloads and player session snapshots share one checkpoint
projection. A checkpoint marked `hiddenToPlayers` (or equivalent DM-only
metadata) does not become visible merely because it is active or terminal, and
its ID is removed from active/completed/skipped/failed projections. Authors may
expose a safe alias with `playerTitle` or `playerSummary`; private authored
title, summary, route, and director fields remain absent.

## Lifecycle Boundary

Archived or deleted sessions and campaigns remain outside the live-play
mutation surface: they cannot accept room joins, player turns, clarification
resolutions, or turn-control changes. Session lifecycle commits share the
per-session turn coordinator. Campaign lifecycle commits fence every affected
session in ascending order before row locking, so archive, restore, and delete
cannot silently race an active turn.

## Long-Term Canon Memory

`aidm_server/emergent_memory.py` contains canon extraction and normalization
logic; it is not a separate state store. Durable long-term canon is stored in
`story_entities`, `story_facts`, `story_threads`, `turn_canon_updates`, and
`canon_jobs`.

Canon extraction runs outside the session coordinator. Before applying the
validated patch and refreshing the live projection, the worker acquires and
revalidates the complete shared campaign-pack lock set in a fresh scoped
database session. This prevents a canon waiter from projecting a snapshot it
loaded before a foreground turn committed.

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
