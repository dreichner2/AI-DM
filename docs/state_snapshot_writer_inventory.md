# Session State Snapshot Writer Inventory

`Session.state_snapshot` is the live runtime truth once a session has a
snapshot. Direct writes are allowed only when they have a documented ownership
category and the write count for that scope matches this inventory.

Run:

```bash
.venv/bin/python scripts/check_state_snapshot_writers.py
```

The checker scans production Python under `aidm_server/` and release/support
scripts under `scripts/`, then compares every `*.state_snapshot = ...`
assignment and every `Session(..., state_snapshot=...)` constructor seed to the
table below. Test fixtures are intentionally excluded.

## Categories

- `central_runtime_persistence`: the shared persistence boundary used after
  validated state changes are applied.
- `turn_pipeline_serialized`: turn-pipeline persistence while the active turn is
  already under the per-session turn coordinator.
- `campaign_pack_serialized`: campaign-pack progress writes protected by the
  per-session turn coordinator.
- `serialized_metadata`: non-gameplay snapshot metadata writes that reload,
  revise, audit, and commit under the per-session turn coordinator.
- `initialization_import`: session creation/import paths that establish the
  starting runtime snapshot.
- `onboarding_initialization`: onboarding-only finalization before a new
  session is published, with any replay/repair of a visible session serialized.
- `lifecycle_metadata`: archive, restore, rename, or end-session metadata writes
  that do not alter gameplay state.
- `projection_refresh`: derived refreshes from canon/projection sources back
  into the runtime snapshot.
- `runtime_control`: direct control-plane state such as active-turn control.
- `regression_fixture`: isolated support script fixture setup.

## Inventory

| Path | Scope | Expected writes | Category | Coordinator boundary | Audit/evidence | Action |
| --- | --- | ---: | --- | --- | --- | --- |
| `aidm_server/game_state/application/applier.py` | `persist_state_to_database` | 1 | `central_runtime_persistence` | Shared persistence helper called after state-change validation/application. | Player rows and session snapshot are persisted together. | Keep as the preferred runtime persistence boundary. |
| `aidm_server/game_state/orchestration/turn_pipeline.py` | `pre_dm_pipeline` | 1 | `turn_pipeline_serialized` | Active turn pipeline path; gameplay turns are serialized before this stage. | Used only when no immediate/combat changes require `persist_state_to_database`. | Keep documented; consider routing no-change writes through a named helper later. |
| `aidm_server/game_state/orchestration/turn_pipeline.py` | `post_dm_pipeline` | 2 | `turn_pipeline_serialized` | Active turn pipeline path; gameplay turns are serialized before this stage. | Used when post-DM extraction is skipped or no post-DM changes apply. | Keep documented; consider routing no-change writes through a named helper later. |
| `aidm_server/services/campaign_pack_progress.py` | `_update_campaign_pack_progress_locked` | 2 | `campaign_pack_serialized` | Called by `update_campaign_pack_progress`, which uses `session_turn_coordinator.serialized(session_id)`. | Records `session_state_mutation_audits` for migration/progress writes and progress events when changed. | Keep serialized; future refactor can wrap this in `mutate_session_state` or a campaign-pack-specific mutation helper. |
| `aidm_server/services/campaign_pack_progress.py` | `_control_campaign_pack_progress_locked` | 1 | `campaign_pack_serialized` | Called by `control_campaign_pack_progress`, which uses `session_turn_coordinator.serialized(session_id)`. | Records `session_state_mutation_audits` and progress events for operator control changes. | Keep serialized; future refactor can wrap this in `mutate_session_state` or a campaign-pack-specific mutation helper. |
| `aidm_server/services/session_state_mutation.py` | `mutate_session_snapshot_metadata` | 1 | `serialized_metadata` | Reloads the current row after acquiring `session_turn_coordinator.serialized(session_id)` and commits before releasing the coordinator. | Increments `stateRevision`, stamps the bounded in-snapshot mutation audit, and records a durable `session_state_mutation_audits` row with a snapshot diff. | Keep as the shared boundary for metadata that is not represented by the gameplay change schema. |
| `aidm_server/services/campaign_pack.py` | `import_campaign_pack` | 2 | `initialization_import` | Creates/imports a session starting snapshot before active play, first as a constructor seed and then with the DB-assigned session id. | Campaign-pack import creates `SessionState` and installed-pack records in the same transaction. | Keep direct as initialization; both writes must stay import-only. |
| `aidm_server/services/session_import.py` | `import_session_export` | 2 | `initialization_import` | Creates a new imported session snapshot before active play, first as an empty constructor seed and then with imported provenance. | Import path validates payload and recreates projections/log entries for the new session. | Keep direct as initialization; both writes must stay import-only. |
| `aidm_server/services/play_now.py` | `_sync_play_now_snapshot` | 1 | `onboarding_initialization` | For a newly imported session, completes the uncommitted starting snapshot before the onboarding transaction is published. For an idempotent replay, `ensure_play_now_adventure` reloads under `session_turn_coordinator.serialized(session_id)` and commits before releasing it. | Uses `state_snapshot_for_session` to preserve existing runtime fields while synchronizing the selected starter hero and Play Now provenance; regression coverage verifies that only the replay path acquires the session coordinator. | Keep limited to Play Now initialization/repair; route unrelated live metadata changes through `mutate_session_snapshot_metadata`. |
| `aidm_server/blueprints/sessions.py` | `start_new_session` | 1 | `initialization_import` | Creates a new session snapshot only for idempotency metadata before active play. | Start-session path stores `client_session_id` in `Session.client_session_id` and mirrors it into the initial snapshot for legacy/idempotency compatibility. | Keep direct as initialization; do not expand this path into gameplay mutation. |
| `aidm_server/services/campaign_pack_storage.py` | `propagate_shared_campaign_pack_progress` | 1 | `campaign_pack_serialized` | Campaign-pack progress acquires source and shared sibling session locks in sorted order; the propagation loop also guards each sibling with the per-session coordinator. | Syncs durable `CampaignPackSession` progress for sibling sessions, skips stale incoming revisions, and records `session_state_mutation_audits` with source `system.campaign_pack.shared_progress`. | Covered by shared-progress propagation, lock-id, and stale-revision regression tests. |
| `aidm_server/blueprints/sessions.py` | `end_game_session` | 1 | `lifecycle_metadata` | Endpoint-level session lifecycle operation. | Writes recap/ended timestamp and records a `session_ended` turn event. | Keep direct as lifecycle metadata. |
| `aidm_server/blueprints/sessions.py` | `update_session` | 1 | `lifecycle_metadata` | Endpoint-level metadata operation. | Cleans stale display metadata from snapshot when session name changes. | Keep direct as lifecycle metadata. |
| `aidm_server/services/session_lifecycle.py` | `archive_session_record` | 1 | `lifecycle_metadata` | Endpoint/service lifecycle operation. | Records `operator_action` for archive. | Keep direct as lifecycle metadata. |
| `aidm_server/services/session_lifecycle.py` | `restore_session_record` | 1 | `lifecycle_metadata` | Endpoint/service lifecycle operation. | Records `operator_action` for restore. | Keep direct as lifecycle metadata. |
| `aidm_server/canon_projection.py` | `_sync_session_snapshot` | 1 | `projection_refresh` | Projection refresh from canon facts/threads into runtime summary fields. | Only writes when location/quest projection changed. | Keep documented as projection refresh; avoid expanding into gameplay mutation. |
| `aidm_server/turn_control.py` | `save_turn_control` | 1 | `runtime_control` | Called by turn-control paths that own turn sequencing state. | Updates only `turnControl` plus `updated_at`. | Candidate for a named mutation helper if turn-control writes need revision/audit semantics. |
| `scripts/scenario_regression.py` | `_setup_npc` | 1 | `regression_fixture` | Isolated regression fixture setup, not live runtime code. | Creates deterministic scenario state for local quality regression. | Keep as fixture. |
| `scripts/security_forbidden_smoke.py` | `_seed_isolated_runtime` | 1 | `regression_fixture` | Isolated security smoke setup, not live runtime code. | Creates deterministic combat state so non-admin forbidden-response checks can hit existing session/bestiary routes. | Keep as fixture. |
| `scripts/render_local_beta_slo_baseline.py` | `seed_baseline_data` | 1 | `regression_fixture` | Isolated beta SLO fixture setup, not live runtime code. | Creates deterministic session state so `/api/beta/slo` and `/api/beta/incidents` can be rendered in local RC evidence. | Keep as fixture. |
