# AIDM Senior Code Review Addendum - 2026-06-12

Note: no active `improvements_suggestions.md` existed in the repository root when this review ran. Existing files were `docs/backend_improvement_suggestions.md` and archived legacy suggestion docs, so this addendum creates the requested target file rather than overwriting older documentation.

Scope: reliability, immersion, state correctness, combat quality, maintainability, security, production readiness, and test coverage. This review is based on current code paths in the working tree, with no code changes made outside this report.

## Review Passes

1. Repo structure and ownership
   - Backend entrypoints and guards: `aidm_server/main.py`, `aidm_server/auth.py`, `aidm_server/workspace_access.py`.
   - State pipeline: `aidm_server/game_state/orchestration/turn_pipeline.py`, `aidm_server/game_state/validation/validator.py`, `aidm_server/game_state/application/applier.py`, `aidm_server/game_state/extraction/*`.
   - Turn/runtime orchestration: `aidm_server/turn_engine.py`, `aidm_server/turn_coordinator.py`, `aidm_server/socket_contracts.py`, `aidm_server/blueprints/socketio_events.py`.
   - Combat and creatures: `aidm_server/combat/*`, `aidm_server/creatures/*`, `aidm_server/blueprints/creatures.py`.
   - Frontend session and inspector surfaces: `aidm_frontend/src/App.tsx`, `aidm_frontend/src/SessionBoard.tsx`, `aidm_frontend/src/InspectorPanel.tsx`, `aidm_frontend/src/BestiaryDebugPanel.tsx`, `aidm_frontend/src/useSessionSocket.ts`.
   - Tests: `tests/test_game_state_pipeline.py`, `tests/test_creatures_combat.py`, `tests/test_players_endpoints.py`, `tests/test_socketio_flow.py`, `aidm_frontend/src/*.test.tsx`.

2. Backend APIs, services, models, state mutation, and persistence
   - Normal socket turns are serialized through `session_turn_coordinator`.
   - Several REST endpoints mutate `Session.state_snapshot` directly outside that coordinator.
   - Validation is strong in many player-owned and inventory/currency flows, but several external entrypoints omit the actor context that makes those validations meaningful.

3. DM prompts, helper prompts, extraction, validation, and LLM trust boundaries
   - The pre-DM prompt explicitly forbids deciding success or mutating state.
   - The post-DM prompt asks the helper to extract concrete changes from narration and contains good grounding instructions.
   - The post-DM pipeline assigns stable IDs for helper output and filters some misrouted changes, but the generic validator/application layer still accepts id-less changes from direct callers.

4. Combat and creature behavior systems
   - The intent planner has useful candidate contracts, target scoring, morale, retreat/surrender, boss tactics, and sentient enemy hooks.
   - The current candidate contract primarily selects and summarizes intent. It does not yet execute attack/save/damage/condition mechanics as authoritative server-owned actions.

5. Frontend state display, player UX, debug tools, and API usage
   - The Inspector exposes a Bestiary tab and debug panel as normal player UI.
   - The panel fetches combat debug events and offers campaign pack seeding from the same general inspector area.

6. Security, production readiness, docs, and deployment assumptions
   - API guards enforce workspace token presence when auth is required and set workspace role/admin flags.
   - High-risk combat and bestiary routes mostly rely on workspace visibility rather than DM/admin capability checks.
   - Production safety should treat "can see the workspace/session" as different from "can mutate world/combat source of truth."

7. Tests and verification
   - There is meaningful coverage for happy-path combat planning, state validation, and endpoint behavior.
   - Missing coverage is concentrated around adversarial authorization, concurrent state mutations, direct REST mutation idempotency, and executable combat legality.

## Finding: Combat mutation and debug routes are available to ordinary workspace participants
Severity: Critical
Area: Security / State / Combat / Backend
Files:
- `aidm_server/main.py`
- `aidm_server/workspace_access.py`
- `aidm_server/blueprints/creatures.py`
- `aidm_server/game_state/validation/validator.py`
- `aidm_frontend/src/BestiaryDebugPanel.tsx`
- `tests/test_creatures_combat.py`

Problem:
Several combat routes behave like DM/admin/debug controls but only require workspace/session visibility. A participant who can reach the session can start combat, apply arbitrary combat state changes, force combat end checks, apply morale events, and read combat debug payloads. The most dangerous route is `POST /api/sessions/<session_id>/combat/apply-state-changes`, which accepts a caller-supplied `changes` list and validates it without an `expected_actor_id`.

Evidence:
- `aidm_server/main.py:184-261` applies API guards and sets workspace/admin flags, but does not enforce route-specific capabilities.
- `aidm_server/workspace_access.py:68-72` returns a session when its campaign is visible in the workspace.
- `aidm_server/blueprints/creatures.py:454-545` starts combat from a REST request and persists the new combat snapshot.
- `aidm_server/blueprints/creatures.py:560-582`, `585-611`, and `614-635` apply morale events, combat end changes, and arbitrary state changes directly to the session snapshot.
- `aidm_server/blueprints/creatures.py:638-663` returns full combat debug event payloads for any visible session.
- `aidm_server/game_state/validation/validator.py:1940-1970` only enforces current-player actor ownership when `expected_actor_id` is supplied.
- `aidm_server/game_state/validation/validator.py:2414-2450` skips that actor protection when the caller provides no expected actor.
- `tests/test_creatures_combat.py:2393-2417` uses `apply-state-changes` to set an enemy participant to 0 HP and then ends combat through the API.
- `aidm_frontend/src/BestiaryDebugPanel.tsx:147-154` fetches `/combat/debug` from a normal inspector panel load.

Impact on AIDM:
In a public or semi-public table, a player could defeat enemies, force campaign pack progress, alter combat participants, or inspect hidden combat planning/debug data without DM authority. Even if the current local mode is trusted, these routes become a production footgun because "workspace member" and "DM/admin" are different powers. This undermines player trust in combat outcomes and makes the backend less clearly authoritative.

Recommended fix:
Introduce a route-level capability policy for session/world mutation:
- Require `current_account_is_workspace_admin()` or an explicit DM/session-owner role for debug, manual combat start, bestiary seeding, and arbitrary combat state mutation.
- Remove or dev-gate `combat/apply-state-changes`; replace it with narrow, server-owned commands such as "mark enemy defeated by validated resolver result" or "apply morale event from engine."
- When a player-safe route truly needs to mutate state, pass `expected_actor_id=display_actor_id(player_id)` and reject cross-actor changes unless they are generated by a server-owned resolver with a specific source.
- Sanitize or admin-gate combat debug payloads. Player-facing combat summaries should expose only visible telegraphs, not resolver/debug internals.

Suggested tests:
- Non-admin account in the same workspace receives 403 for `/combat/debug`, `/combat/start`, `/combat/apply-state-changes`, `/combat/check-end?apply`, and bestiary seed/generation routes.
- A normal player cannot post `combat.participant.update` for an enemy or another player.
- Admin/DM can perform the same actions and receives audit metadata on the applied change.
- Player-safe combat summary still returns visible telegraphs without debug internals.

## Finding: Out-of-band session snapshot writes can clobber active turns
Severity: High
Area: State / Backend / Database / Architecture
Files:
- `aidm_server/turn_engine.py`
- `aidm_server/game_state/orchestration/turn_pipeline.py`
- `aidm_server/blueprints/creatures.py`
- `aidm_server/blueprints/players.py`
- `aidm_server/services/campaign_pack_progress.py`
- `aidm_server/services/campaign_pack.py`
- `aidm_server/canon_projection.py`

Problem:
The normal socket turn path serializes work per session, but multiple REST/service paths still read a snapshot, apply changes, and write `Session.state_snapshot` without the same session turn coordinator or a snapshot version check. During a streamed DM turn, these direct writes can race with the turn pipeline's `stateBeforeDm` and post-DM application.

Evidence:
- `aidm_server/turn_engine.py:871-879` wraps normal socket turn processing in `session_turn_coordinator.serialized(command.session_id)`.
- `aidm_server/game_state/orchestration/turn_pipeline.py:971-1116` builds `state_before_dm`, applies pre-DM and combat prep changes, and stores that state in turn metadata.
- `aidm_server/game_state/orchestration/turn_pipeline.py:1118-1290` later applies post-DM changes against `state_before_dm`, not necessarily the latest snapshot if another route wrote during the turn.
- `aidm_server/blueprints/creatures.py:536-545`, `579-582`, `599-603`, and `624-635` apply and persist combat changes directly.
- `aidm_server/blueprints/players.py:421-462` handles equipment updates by reading equipment state, applying state changes, assigning `session_obj.state_snapshot`, and committing without the session coordinator.
- `rg` shows additional direct snapshot writers in campaign pack progress, campaign pack import, canon projection, and session lifecycle services.

Impact on AIDM:
A player can equip gear, a debug route can alter combat, or campaign pack progress can update while a DM response is streaming. The post-DM pipeline can then persist an older `stateBeforeDm` plus extracted narration effects, silently dropping the out-of-band update. This is exactly the kind of drift that makes inventory, AC, HP, combat flags, quests, or campaign pack progress feel unreliable.

Recommended fix:
Create one authoritative session state mutation service and route all `Session.state_snapshot` writes through it:
- Acquire `session_turn_coordinator.serialized(session_id)` for every endpoint/service that mutates a live session snapshot.
- Reload the session state inside the lock before validation/application.
- Store and check a snapshot version or `lastUpdatedAt` token for optimistic concurrency.
- For long-running turns, either reject out-of-band mutations with a clear "turn in progress" response or queue them as pending state commands to apply after the turn.
- Keep canon projection and campaign pack progress writes explicitly marked as server-owned and serialized when touching active sessions.

Suggested tests:
- Simulate a turn that captures `stateBeforeDm`, then apply an equipment change before post-DM persistence; assert the equipment change remains in final state.
- Simulate `/combat/apply-state-changes` racing with a normal turn and assert the second writer waits, rejects, or merges deterministically.
- Add a regression test that `lastUpdatedAt` or snapshot version changes cause stale writes to be rejected.
- Verify campaign pack progress updates do not disappear after post-DM state persistence.

## Finding: State change idempotency is optional outside the post-DM extractor
Severity: High
Area: State / Backend / Testing
Files:
- `aidm_server/game_state/validation/validator.py`
- `aidm_server/game_state/application/applier.py`
- `aidm_server/game_state/extraction/post_dm_outcome_extractor.py`
- `aidm_server/blueprints/creatures.py`

Problem:
The state change ledger only protects changes with an `id`. The post-DM extractor assigns stable turn-scoped IDs, but direct callers can submit id-less changes. Validation and application allow those id-less changes to pass and apply repeatedly, because duplicate detection and ledger append are conditional on a non-empty change ID.

Evidence:
- `aidm_server/game_state/extraction/post_dm_outcome_extractor.py:400-417` assigns stable IDs to helper-proposed post-DM changes.
- `aidm_server/game_state/validation/validator.py:2441-2479` reads `change_id`, rejects duplicates only when `change_id` is present, and otherwise continues.
- `aidm_server/game_state/application/applier.py:1075-1084` skips duplicate detection for id-less changes.
- `aidm_server/game_state/application/applier.py:1412-1415` appends to `stateChangeLedger` only when `change_id` is present.
- `aidm_server/blueprints/creatures.py:614-635` passes caller-supplied changes directly from JSON to `validate_state_changes`.

Impact on AIDM:
Retries, double-clicks, client bugs, or manual/debug callers can duplicate damage, healing, gold, XP, loot, scene item removal, quest updates, or combat participant changes. The state ledger then cannot explain what happened because no durable ID was recorded. This can make the table feel arbitrary: the same hit lands twice, gold vanishes twice, or a quest completes through a repeated request.

Recommended fix:
Make change identity mandatory at the validation boundary:
- Reject mutable state changes without an `id`, `source`, and trusted origin, or synthesize a deterministic ID before validation based on session, turn, actor, type, target, amount, and source.
- Treat client-provided IDs from untrusted routes as idempotency keys scoped to session and actor, not global authority.
- Always append ledger entries for applied changes, including synthesized IDs, source, actor, route/turn, and timestamp.
- Add a helper such as `normalize_state_change_identity(state, change, context)` and call it from all mutation services before validation.

Suggested tests:
- Applying the same id-less `health.damage` twice should not double damage after normalization.
- Applying the same id-less `currency.add` or `xp.add` twice should not duplicate rewards.
- `combat/apply-state-changes` rejects external id-less changes or returns the synthesized ID and dedupes on retry.
- Ledger entries include source and route/turn context for applied external state changes.

## Finding: Enemy intent candidates are not yet executable combat actions
Severity: High
Area: Combat / DM Prompting / State
Files:
- `aidm_server/combat/intent_planner.py`
- `aidm_server/combat/pipeline.py`
- `aidm_server/combat/state.py`
- `aidm_server/game_state/orchestration/turn_pipeline.py`
- `aidm_server/game_state/extraction/prompts.py`
- `aidm_server/game_state/validation/validator.py`

Problem:
The combat system has a good candidate/intent layer, but selected enemy actions are still largely advisory. The engine selects an enemy intent and gives the DM required actions/telegraphs; it does not yet resolve attack rolls, saves, damage dice, ability effects, movement costs, or condition application as authoritative server-owned state changes. Post-DM extraction then infers combat outcomes from narration.

Evidence:
- `aidm_server/combat/intent_planner.py:382-425` builds an `engine_intent_bundle_v1` resolver, but its `actionBundle` contains descriptive steps like `movement_intent`, `use_ability`, and `combat_intent`, not executable state changes or rolls.
- `aidm_server/combat/intent_planner.py:428-462` marks candidates as resolvable when broad booleans pass; `action_economy_valid` and `resources_available` are hard-coded true.
- `aidm_server/combat/pipeline.py:449-469` plans enemy intents and returns `combat.intent.set` changes before narration.
- `aidm_server/combat/state.py:482-547` exposes `enemyRequiredActions` and telegraphs to the DM context.
- `aidm_server/game_state/extraction/prompts.py:93-97` then asks the post-DM helper to extract combat participant updates, movement, conditions, ability use, morale, and combat end from the DM response.
- `aidm_server/game_state/orchestration/turn_pipeline.py:1182-1268` merges post-DM extraction and applies those state changes after narration.
- `aidm_server/game_state/validation/validator.py:2069-2081` accepts `combat.participant.update` payloads with HP changes as structurally valid if the participant exists.

Impact on AIDM:
Enemies can appear tactical in text while the actual mechanical result depends on whether the DM narration and helper extraction happen to agree. A helper can infer HP changes that were not produced by a roll resolver, or miss damage that the intent implied. This weakens combat fairness, makes enemies feel inconsistent, and keeps the backend from being the source of truth for D&D-like mechanics.

Recommended fix:
Promote the candidate resolver from advisory to executable:
- Add an engine-owned combat action resolver that consumes the selected candidate and current combat state.
- Resolve attack/save/check mechanics server-side: roll, compare AC/DC, compute damage dice, apply resistance/vulnerability if modeled, apply conditions, consume ability resources, and emit validated state changes with roll records.
- Let the LLM choose among legal candidates only when allowed; the LLM should not author final damage or legality.
- Give the DM a compact "resolved combat result" packet to narrate, rather than asking the DM/helper to invent the mechanical result.
- Keep helper extraction as a backstop for purely narrative combat outcomes, not the main authority for enemy turns.

Suggested tests:
- A selected enemy attack against a player rolls against AC and applies exactly the computed damage on hit.
- A miss produces narration context but no HP change.
- A save-based ability applies full/half/no effect according to the ability schema.
- Enemy movement and conditions are emitted as state changes from the resolver, not inferred from DM text.
- The DM response cannot produce extra enemy damage unless it matches a server-owned resolved action ID.

## Finding: Combat ability and resource legality is too shallow
Severity: Medium
Area: Combat / State / Testing
Files:
- `aidm_server/combat/intent_planner.py`
- `aidm_server/game_state/validation/validator.py`
- `aidm_server/game_state/application/applier.py`
- `aidm_server/creatures/schemas.py`
- `tests/test_creatures_combat.py`

Problem:
Combat abilities track cooldowns and `usesRemaining`, but selection and validation mostly check that an ability exists. The planner can select a once-per-combat or depleted ability again, and validation accepts `combat.ability.mark_used` without confirming the ability is present, available, or unused.

Evidence:
- `aidm_server/creatures/schemas.py:314-316` preserves `usesRemaining` in normalized abilities.
- `aidm_server/combat/intent_planner.py:208-217` selects the first matching special/spell/legendary/lair ability by type/cooldown and does not check `used`, `usesRemaining`, `lastUsedRound`, or recharge state.
- `aidm_server/combat/intent_planner.py:428-462` reports `ability_available` as "ability object exists" and sets `resources_available` true.
- `aidm_server/game_state/validation/validator.py:2102-2107` validates `combat.ability.mark_used` by requiring only `abilityId`.
- `aidm_server/game_state/application/applier.py:1060-1068` decrements/marks an ability if found, but returns the participant even if no ability matched.
- `aidm_server/game_state/application/applier.py:1246-1253` treats a returned participant as applied and records the requested `abilityId`.
- `tests/test_creatures_combat.py:2135-2185` covers a successful ability mark-used path but not missing/depleted/used ability rejection.

Impact on AIDM:
Bosses and enemies may reuse limited powers, mark impossible abilities as consumed, or present "legal" candidates that should be blocked. This is especially noticeable for boss tactics, recharge powers, lair actions, and once-per-combat fear/hex abilities. It also makes balance tuning harder because ability frequency is not reliably enforced.

Recommended fix:
Centralize ability availability:
- Add `combat_ability_available(participant, ability_id, round, action_context)` and use it in candidate generation, candidate revalidation, validator, and applier.
- Treat unknown `abilityId` as rejected, not applied.
- Reject or downgrade candidates for `used`, `usesRemaining <= 0`, cooldown not ready, wrong action economy, unavailable reaction/legendary/lair timing, or invalid target type.
- Include resource consumption in the candidate dry run and final state change ledger.

Suggested tests:
- A once-per-combat ability marked used is not selected on a later turn.
- `combat.ability.mark_used` for an unknown ability is rejected or skipped without ledger success.
- `usesRemaining: 0` blocks selection and validation.
- Recharge abilities become available only after a deterministic recharge event.
- Boss/legendary/lair candidates respect action economy and timing.

## Finding: Bestiary and creature generation routes can mutate authored world content without DM/admin gating
Severity: Medium
Area: Security / UX / Architecture / Production Readiness
Files:
- `aidm_server/blueprints/creatures.py`
- `aidm_server/creatures/resolver.py`
- `aidm_frontend/src/InspectorPanel.tsx`
- `aidm_frontend/src/BestiaryDebugPanel.tsx`
- `tests/test_creatures_combat.py`

Problem:
The campaign bestiary is world-authoring data, but creation/generation/evolution routes are available through workspace-scoped APIs and the normal inspector UI. These routes can save generated creatures into a campaign, alter future encounter resolution, and potentially trigger provider work. In production, this should be a DM/admin capability, not a normal player affordance.

Evidence:
- `aidm_server/blueprints/creatures.py:288-315` saves a campaign bestiary entry from request payload.
- `aidm_server/blueprints/creatures.py:318-347` generates a campaign pack and saves entries by default unless `save` is explicitly false.
- `aidm_server/blueprints/creatures.py:350-364` resolves creatures and commits, using the resolver path that can save generated creatures depending on payload/context.
- `aidm_server/blueprints/creatures.py:394-429` evolves a creature and saves by default when `saveGenerated` is not false.
- `aidm_frontend/src/InspectorPanel.tsx:251-259` always exposes the Bestiary tab.
- `aidm_frontend/src/BestiaryDebugPanel.tsx:203-220` calls `/campaigns/<id>/bestiary/generate-pack` from the "Seed" button.
- `tests/test_creatures_combat.py:2424-2454` exercises pack generation, creature evolution, combat start, morale, and debug in one happy-path endpoint test.

Impact on AIDM:
Players can spoil or alter the monster roster, seed off-theme enemies into a campaign, and create persistent content that changes future encounter resolution. In a hosted product, the same routes can also become cost-abuse surfaces if generation calls use helper/provider models. From a UX perspective, visible bestiary/debug controls also break immersion by exposing behind-the-curtain encounter machinery.

Recommended fix:
- Split player-facing bestiary browsing from DM/admin authoring.
- Gate create/generate/evolve/save operations by DM/admin capability.
- Default non-admin resolver calls to `saveGenerated: false` and enforce server-side allowlists for persistence.
- Add per-workspace quotas/rate limits for generation routes beyond generic IP+route limits.
- Move "Seed" and debug controls behind a DM tools mode and hide them for players.

Suggested tests:
- Non-admin cannot create, seed, evolve, or save generated bestiary entries.
- Non-admin resolver calls cannot persist generated creatures even if payload requests saving.
- Admin can seed bestiary and receives a saved entry count.
- Player-facing Bestiary tab, if retained, omits debug events, seed controls, and unrevealed encounter-only entries.

## Finding: Debug payloads store and expose more than player-safe combat summaries
Severity: Medium
Area: Security / UX / Frontend / Backend
Files:
- `aidm_server/creatures/repository.py`
- `aidm_server/combat/pipeline.py`
- `aidm_server/blueprints/creatures.py`
- `aidm_frontend/src/BestiaryDebugPanel.tsx`

Problem:
Combat debug events persist full debug payloads, and the API returns them directly. The UI currently summarizes only parts of those events, but the raw payload returned by the backend includes resolver and intent planning details that are not player-safe. This is separate from route mutation risk: even read-only debug access can leak enemy plans, encounter resolution methods, hidden objectives, and validation outcomes.

Evidence:
- `aidm_server/creatures/repository.py:199-219` stores a deep copy of the debug payload in `CombatDebugEvent.payload_json`.
- `aidm_server/combat/pipeline.py:621-638` records pre-DM combat debug payloads containing resolver and intent plan data.
- `aidm_server/combat/pipeline.py:673-712` records post-DM combat outcome debug payloads including validation counts, applied/rejected combat changes, and state log.
- `aidm_server/blueprints/creatures.py:638-663` returns `payload: safe_json_loads(row.payload_json, {})` for each debug event.
- `aidm_frontend/src/BestiaryDebugPanel.tsx:81-117` reads resolver and intent plan fields from those payloads for display.

Impact on AIDM:
A player can inspect enemy intent summaries, resolver methods, hidden mechanics, and validation details. That spoils tactical play and makes the AI DM feel less like an immersive world and more like a visible backend. In production, debug payloads can also accidentally retain prompt-derived or helper-derived text that should not be broadly exposed.

Recommended fix:
- Treat full combat debug payloads as admin-only.
- Add a separate player-safe combat telemetry endpoint that returns only visible telegraphs, current combat status, and public participant summaries.
- Redact helper raw text, resolver internals, candidate backups, and validation internals from any non-admin response.
- Consider time-boxed debug retention and structured redaction before storage if payloads may contain model/provider details.

Suggested tests:
- Non-admin `/combat/debug` returns 403 or a redacted payload without resolver/intent internals.
- Admin `/combat/debug` returns full debug payload.
- Player-safe combat summary includes visible telegraphs but not hidden enemy backup candidates or validation logs.
- Stored debug payload redaction covers helper raw text and provider/model internals if those fields appear in future events.

## Finding: High-risk tests cover success paths but not adversarial invariants
Severity: Medium
Area: Testing / Security / State / Combat
Files:
- `tests/test_creatures_combat.py`
- `tests/test_game_state_pipeline.py`
- `tests/test_players_endpoints.py`
- `aidm_frontend/src/BestiaryDebugPanel.test.tsx`

Problem:
The test suite has strong happy-path coverage for combat and state pipeline behavior, but the riskiest invariants are not protected: route authorization, actor ownership on direct mutation routes, id-less retry dedupe, concurrent snapshot writes, and depleted combat abilities. Several current tests prove that powerful endpoints work, but not that the same endpoints are limited to the correct actor/capability or that retries are safe.

Evidence:
- `tests/test_creatures_combat.py:2135-2185` validates normal combat move/condition/ability/morale/round changes, but not invalid ability IDs or depleted resources.
- `tests/test_creatures_combat.py:2393-2417` validates that arbitrary combat participant HP updates can be posted and used to end an encounter.
- `tests/test_creatures_combat.py:2424-2454` validates generation/evolution/morale/debug endpoints together, but not non-admin rejection or redaction.
- `aidm_frontend/src/BestiaryDebugPanel.test.tsx:59-150` validates bestiary filtering, debug summary display, and seeding behavior, but not hiding those controls for non-admin users.
- `tests/test_game_state_pipeline.py` has duplicate ID tests, but current risk remains for id-less direct changes and external route retries.

Impact on AIDM:
Future changes can preserve the current happy path while leaving production-breaking holes intact. The app needs tests that encode table safety rules: players cannot mutate the world outside their character, retries cannot duplicate state, combat mechanics are server-owned, and debug/authoring tools are not player features.

Recommended fix:
Add an adversarial test layer around the existing happy-path tests:
- Route-level auth/capability tests for every session/world mutation endpoint.
- Actor ownership tests for every route that accepts an actor, participant, player, quest, inventory, or currency identifier.
- Idempotency tests for retries and id-less input.
- Concurrency tests for session snapshot writes.
- Combat legality tests for ability availability, action economy, and engine-owned damage.

Suggested tests:
- Non-admin player tries to damage another participant through `/combat/apply-state-changes` and receives 403 or validation rejection.
- Duplicate id-less `health.damage`, `currency.add`, and `xp.add` requests are deduped or rejected.
- Equipment update during a simulated active turn does not disappear after post-DM persistence.
- A used once-per-combat ability cannot be selected, marked used again, or represented as legal.
- Bestiary seed/debug controls are absent for non-admin frontend renders.

## Prioritized Roadmap

### Fix immediately

- Gate combat debug and mutation endpoints by DM/admin capability.
- Disable or dev-only-gate `/combat/apply-state-changes` until it can only apply server-owned commands.
- Require or synthesize state change IDs before application, then ledger every applied mutation.
- Add non-admin rejection tests for combat mutation, combat debug, and bestiary generation.

### Fix before public beta

- Route every live session snapshot mutation through a shared serialized state mutation service.
- Add optimistic snapshot version checks for REST and background writers.
- Redact debug payloads and split admin debug from player-safe combat summaries.
- Gate bestiary create/generate/evolve/save operations and add per-workspace generation quotas.
- Add adversarial retry/concurrency tests around HP, XP, currency, inventory, equipment, and combat state.

### Improve for game quality

- Turn enemy intent candidates into executable server-owned combat actions.
- Resolve attack/save/damage/condition mechanics before narration and give the DM a result packet to narrate.
- Implement ability availability/resource checks across planner, validator, and applier.
- Expand combat tests around morale, fleeing, surrender, objectives, boss abilities, recharge, and target legality.
- Keep helper extraction as a narrative backstop, not the primary source of enemy mechanical results.

### Nice-to-have cleanup

- Rename `BestiaryDebugPanel` or split it into `BestiaryPanel` and `DmCombatDebugPanel` so debug/admin intent is explicit.
- Consolidate scattered snapshot writers behind a narrow service API and document allowed mutation origins.
- Add a short state mutation contract doc explaining trusted sources, actor binding, change IDs, ledger semantics, and concurrency behavior.
- Add frontend role-based fixtures for player, DM/admin, and local no-auth modes.
