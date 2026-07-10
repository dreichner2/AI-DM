> **Historical audit ledger:** Each dated section below records the repository
> state and verification results at that time. Older findings, commands, file
> sizes, and release status may have been superseded. Use `docs/index.md`,
> `docs/roadmap.md`, and the generated release-checklist status for current
> guidance; preserve these entries as evidence rather than silently rewriting
> them.

# Daily AIDM Codebase Improvement Audit - 2026-06-28 06:04 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe recurring audit across backend correctness, creature endpoint numeric boundaries, tests, request parsing guardrails, secret scanning, state mutation inventory, observability evidence, release checklist status, and a light static scan of frontend/security/accessibility hotspots. Existing uncommitted audit-bundle work was preserved. This run made one small route-boundary robustness fix, refreshed release checklist status, prepended this report, and updated the fallback automation memory file.

## What Was Inspected

- Physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Automation memory:
  - `$CODEX_HOME/automations/daily-aidm-codebase-improvement-audit/memory.md` was not available because `CODEX_HOME` was empty in this shell.
  - Fallback memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md` was read before updating it.
- Prior recurring-audit context from local Codex memory and the local `aidm-daily-audit` workflow note: preserve dirty work, choose one narrow safe fix, verify it with focused checks, and keep release blockers prominent.
- Current dirty-tree boundary with `git status --short`; existing uncommitted files were treated as boundary state and not reverted:
  - `aidm_frontend/scripts/visual-smoke.cjs`
  - `aidm_frontend/src/App.test.tsx`
  - `aidm_server/capabilities.py`
  - `aidm_server/creatures/campaign_pack.py`
  - `aidm_server/creatures/resolver.py`
  - `improvements_suggestions.md`
  - `scripts/check_request_json_parsing.py`
  - `scripts/cleanup_artifacts.sh`
  - `scripts/render_packaging_cleanup_evidence.py`
  - `scripts/render_rc_issue_evidence.py`
  - `scripts/scan_secrets.py`
  - `tests/test_auth.py`
  - `tests/test_creatures_combat.py`
  - `tests/test_render_packaging_cleanup_evidence.py`
  - `tests/test_render_rc_issue_evidence.py`
  - `tests/test_request_json_parsing_guard.py`
  - `tests/test_secret_scan.py`
- Existing top report in `improvements_suggestions.md`, especially the 2026-06-27 16:05 MDT campaign-pack numeric hint hardening entry and its carry-forward warning about adjacent creature endpoint numeric parsing.
- Creature endpoint numeric coercion hotspots in `aidm_server/blueprints/creatures.py`, with focus on:
  - `/api/creatures/variant`
  - `/api/creatures/evolve`
  - `/api/creatures/analyze-balance`
  - manual `/api/sessions/<session_id>/combat/start`
- Existing helper patterns in the same blueprint:
  - `_positive_int`
  - `coerce_int`
  - `parse_json_body`
- Existing creature/combat endpoint coverage in `tests/test_creatures_combat.py`.
- Standard recurring audit guardrails:
  - `scripts/scan_secrets.py`
  - `scripts/check_request_json_parsing.py`
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/check_observability_bundle.py`
  - `scripts/generate_api_types.py --check`
  - `git diff --check`
- Release checklist status:
  - `make release-checklist-status`
  - `tmp/release/release-checklist-status.md`
  - `tmp/release/release-checklist-status.json`
- Broad static hotspot scan with `rg` for direct numeric parsing, request parsing, JSON loading, subprocess/shell usage, dangerous frontend DOM patterns, storage usage, and accessibility markers. This was used to choose a narrow fix; it was not treated as a complete security or UX audit.

## Small Safe Fix Made

### Harden `/api/creatures/variant` numeric hints

Affected files:
- `aidm_server/blueprints/creatures.py`
- `tests/test_creatures_combat.py`
- `improvements_suggestions.md`

Problem:
`create_creature_variant_endpoint` directly called `int(...)` on optional `partyLevel` and `partySize` request hints. A malformed authoring request such as `{"partyLevel": "bogus"}` could raise `ValueError` and turn a nonessential hint into a server error. This was the same boundary class as the prior resolver and campaign-pack numeric hardening, but this run limited the change to one endpoint.

Change:
- Reused the existing blueprint-local `_positive_int` helper for variant endpoint `partyLevel` and `partySize`.
- Preserved the existing fallback behavior:
  - `partyLevel` defaults to `1`.
  - `partySize` defaults to `4`.
  - both are clamped to at least `1`.
- Added endpoint regression coverage in `test_creature_api_endpoints` proving malformed variant numeric hints still return a generated variant.
- Did not change evolution, analyze-balance, manual combat start, campaign-pack saving, auth/capability gates, frontend UI, database state, or prompt/runtime behavior.

Rationale:
This is a low-blast-radius correctness and maintainability fix at a route boundary. It removes one avoidable 500-class path, follows an existing local helper pattern, and leaves adjacent route contracts for separate focused passes.

## Verification

- `./.venv/bin/python -m py_compile aidm_server/blueprints/creatures.py`
  - Passed.
- `./.venv/bin/python -m pytest tests/test_creatures_combat.py -k "test_creature_api_endpoints" -q`
  - Passed: 1 test, 71 deselected.
- `./.venv/bin/python -m pytest tests/test_creatures_combat.py -q`
  - Passed: 72 tests.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/check_observability_bundle.py`
  - Passed.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `git diff --check`
  - Passed.
- `make release-checklist-status`
  - Passed and wrote `tmp/release/release-checklist-status.md` and `.json`.
  - Summary: 104 passed, 2 external-required, 0 manual-review, 0 failed, 0 unmapped.

## High-Priority Findings

### High: Release checklist still has two external-required evidence items

Current evidence:
- `make release-checklist-status` generated `tmp/release/release-checklist-status.md` at `2026-06-28T12:03:57+00:00`.
- Summary remains 104 passed, 2 external-required, 0 manual-review, 0 failed, and 0 unmapped.
- Evidence packet status remains `ready-for-issue-closure`.
- Remaining external-required rows:
  - Data Integrity line 69: database backup taken before deployment.
  - Observability line 107: external telemetry endpoint receives events when enabled.

Risk:
The source-local checks and evidence renderers continue to pass, but final release closure still depends on hosted/staging proof. Local tests cannot prove provider-specific backup/restore completion or real external telemetry receipt.

Recommended next step:

```bash
make release-checklist-status
sed -n '1,80p' tmp/release/release-checklist-status.md
```

Then attach hosted database backup/restore evidence and hosted telemetry receipt evidence in the release venue.

### Medium-High: Creature endpoint numeric coercion remains inconsistent outside this route

Current evidence:
- Prior runs hardened `normalize_creature_request` and campaign-pack bestiary generation.
- This run hardened `/api/creatures/variant`.
- Static inspection still shows direct `int(...)` conversions in adjacent creature routes:
  - `/api/creatures/evolve`
  - `/api/creatures/analyze-balance`
  - manual `/api/sessions/<session_id>/combat/start` enemy count parsing
  - selected generated/derived creature internals where values may already be shape-controlled.

Risk:
Some conversions are protected by callers, route converters, or generated data shape, but the behavior is still uneven. Malformed optional numeric hints can become exceptions in route paths that should probably fall back or return validation errors.

Recommended next step:
- In a separate pass, centralize creature-route numeric hint parsing with focused endpoint tests for evolve, analyze-balance, and manual combat start. Keep it separate from this variant endpoint fix so route contracts stay reviewable.

### Medium-High: The recurring audit bundle remains broad

Current evidence:
- `git status --short` now includes 18 modified files across frontend tests, auth/capabilities, creature resolver/campaign-pack/blueprint helpers, request parsing guardrails, cleanup/evidence tooling, source-archive checks, secret scanning, and report updates.
- This run added the new `aidm_server/blueprints/creatures.py` route fix and one focused assertion block in the already-dirty `tests/test_creatures_combat.py`.

Risk:
The individual fixes are small and verified, but the accumulated diff spans many concerns. That makes review, rollback, and release attribution harder than each patch deserves.

Recommended next step:
- Split or commit the accumulated recurring-audit bundle by concern before another source-level audit change: security scanner, request parsing guard, cleanup/evidence tooling, source archive evidence, auth/capability hardening, creature numeric coercion, frontend regression/visual-smoke, and reports.

### Medium: Rendered UX and accessibility evidence was not recaptured

Current evidence:
- This run did not launch the app or capture screenshots.
- The source change was backend route numeric coercion, so visual-smoke would not directly prove the fix.
- The frontend/accessibility pass was limited to static hotspot scanning and existing guardrails.

Risk:
Static frontend scans and backend route tests are not a substitute for rendered UX evidence when frontend behavior or accessibility semantics are being reviewed.

Recommended next step:

```bash
PLAYWRIGHT_CHROMIUM_CHANNEL=chrome make visual-smoke
make visual-smoke-review
```

Run this after frontend runtime/UI changes or before release evidence refresh, not because of this backend route-only patch.

## Larger Suggested Improvements

- Create a shared creature route numeric hint helper for party level, party size, enemy count, generated pack count, and danger level so resolver, generator, variant, evolution, balance, and combat-start paths stop defining subtly different integer behavior.
- Add focused endpoint regressions for malformed numeric hints on `/api/creatures/evolve`, `/api/creatures/analyze-balance`, and manual `/api/sessions/<id>/combat/start`.
- Add a small `make audit-guardrails` target that wraps the cheap recurring checks used here: secret scan, request JSON guard, state snapshot writer guard, observability bundle check, API type drift check, creature/combat focused tests, and release checklist status.
- Regenerate packaging/source-archive evidence after the current dirty bundle is split or committed; source archive evidence is less useful while it predates many uncommitted source changes.
- Keep the two external-required release checklist rows visible until hosted database backup/restore proof and real external telemetry receipt evidence are attached.

## Recommended Next Run Focus

1. Prefer review/split/commit of the accumulated audit bundle before adding another source fix.
2. If another code fix is still needed, continue the endpoint-level creature numeric coercion follow-up with tight tests.
3. If release is near, refresh visual-smoke and source-archive packaging evidence after the dirty bundle is resolved.
4. Close the two external-required release checklist rows with hosted/staging evidence when those environments are available.

# Daily AIDM Codebase Improvement Audit - 2026-06-27 16:05 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe recurring audit across backend correctness, campaign-pack creature generation, creature/combat tests, request parsing guardrails, secret scanning, state mutation inventory, observability evidence, release checklist status, and the already-dirty recurring-audit bundle. Existing uncommitted auth/capability, resolver, request-JSON, cleanup/evidence, visual-smoke, scanner, frontend regression, test, and report edits were preserved. This run made one small campaign-pack robustness fix, refreshed release checklist status, prepended this report, and updated the fallback automation memory file.

## What Was Inspected

- Physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Automation memory lookup:
  - `$CODEX_HOME/automations/daily-aidm-codebase-improvement-audit/memory.md` was not available in this shell.
  - Fallback memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md` was read.
- Prior recurring-audit context from local Codex memory and the local `aidm-daily-audit` workflow notes: preserve dirty work, choose a narrow safe fix, run focused checks, and keep release blockers prominent.
- Current dirty-tree boundary with `git status --short`; existing uncommitted files were treated as boundary state and not reverted:
  - `aidm_frontend/scripts/visual-smoke.cjs`
  - `aidm_frontend/src/App.test.tsx`
  - `aidm_server/capabilities.py`
  - `aidm_server/creatures/resolver.py`
  - `improvements_suggestions.md`
  - `scripts/check_request_json_parsing.py`
  - `scripts/cleanup_artifacts.sh`
  - `scripts/render_packaging_cleanup_evidence.py`
  - `scripts/render_rc_issue_evidence.py`
  - `scripts/scan_secrets.py`
  - `tests/test_auth.py`
  - `tests/test_creatures_combat.py`
  - `tests/test_render_packaging_cleanup_evidence.py`
  - `tests/test_render_rc_issue_evidence.py`
  - `tests/test_request_json_parsing_guard.py`
  - `tests/test_secret_scan.py`
- Existing top report in `improvements_suggestions.md`, especially the 2026-06-27 06:02 MDT resolver numeric-coercion entry and its suggested follow-up around adjacent creature numeric parsing.
- Campaign-pack bestiary generation:
  - `aidm_server/creatures/campaign_pack.py`
  - `generate_campaign_pack_bestiary`
  - `DEFAULT_PACK_ROLES`
  - existing campaign-pack creature/evolution tests in `tests/test_creatures_combat.py`
- Adjacent creature endpoint numeric coercion hotspots, without widening this run into endpoint behavior changes:
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/creatures/generator.py`
  - `aidm_server/creatures/resolver.py`
- Standard recurring audit guardrails:
  - `scripts/scan_secrets.py`
  - `scripts/check_request_json_parsing.py`
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/check_observability_bundle.py`
  - `scripts/generate_api_types.py --check`
  - `git diff --check`
- Release checklist status:
  - `make release-checklist-status`
  - `tmp/release/release-checklist-status.md`
  - `tmp/release/release-checklist-status.json`

## Small Safe Fix Made

### Harden campaign-pack bestiary numeric hints

Affected files:
- `aidm_server/creatures/campaign_pack.py`
- `tests/test_creatures_combat.py`
- `improvements_suggestions.md`

Problem:
`generate_campaign_pack_bestiary` used direct `int(...)` conversions for optional `count`, `partyLevel`, and `partySize` hints. A malformed request body such as `{"count": "bogus"}` could raise `ValueError` before the generator reached its intended defaults. This was the same class of numeric-boundary issue found in the previous resolver-focused audit, but in a smaller campaign-pack helper surface.

Change:
- Added local `_bounded_int` and `_minimum_int` helpers in `aidm_server/creatures/campaign_pack.py`.
- Preserved the existing count behavior: default `8`, minimum `3`, maximum `18`.
- Preserved the existing party behavior: `partyLevel` defaults to `2`, `partySize` defaults to `4`, and both keep their existing minimum of `1` without adding a new upper cap.
- Reused the coerced party values inside the generation loop instead of reparsing them for every creature.
- Added `test_campaign_pack_generation_defaults_malformed_numeric_hints` to prove malformed numeric hints fall back and still generate the default eight campaign-pack creatures.
- Did not change saved bestiary behavior, campaign import behavior, resolver routing, auth/capability gates, prompt templates, frontend UI, database state, or release policy.

Rationale:
This is a low-blast-radius correctness and maintainability fix at a deterministic helper boundary. It prevents malformed optional authoring hints from crashing campaign-pack bestiary generation, keeps the existing bounds intact, and avoids widening this audit into multiple route contracts.

## Verification

- `./.venv/bin/python -m py_compile aidm_server/creatures/campaign_pack.py`
  - Passed.
- `./.venv/bin/python -m pytest tests/test_creatures_combat.py -k "campaign_pack_generation" -q`
  - Passed: 2 tests, 70 deselected.
- `./.venv/bin/python -m pytest tests/test_creatures_combat.py -q`
  - Passed: 72 tests.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/check_observability_bundle.py`
  - Passed.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `git diff --check`
  - Passed.
- `make release-checklist-status`
  - Passed and wrote `tmp/release/release-checklist-status.md` and `.json`.
  - Summary: 104 passed, 2 external-required, 0 manual-review, 0 failed, 0 unmapped.

## High-Priority Findings

### High: Release checklist still has two external-required evidence items

Current evidence:
- `make release-checklist-status` generated `tmp/release/release-checklist-status.md` at `2026-06-27T22:05:31+00:00`.
- Summary remains 104 passed, 2 external-required, 0 manual-review, 0 failed, and 0 unmapped.
- Evidence packet status remains `ready-for-issue-closure`.
- Remaining external-required rows:
  - Data Integrity line 69: database backup taken before deployment.
  - Observability line 107: external telemetry endpoint receives events when enabled.

Risk:
The source-local checks and evidence renderers continue to pass, but final release closure still depends on hosted/staging proof. Local tests cannot prove provider-specific backup/restore completion or real external telemetry receipt.

Recommended next step:

```bash
make release-checklist-status
sed -n '1,80p' tmp/release/release-checklist-status.md
```

Then attach hosted database backup/restore evidence and hosted telemetry receipt evidence in the release venue.

### Medium-High: Creature numeric coercion remains inconsistent outside this helper

Current evidence:
- The previous run hardened `normalize_creature_request` for malformed `partyLevel` and `partySize`.
- This run hardened campaign-pack bestiary generation for malformed `count`, `partyLevel`, and `partySize`.
- Static inspection still shows direct `int(...)` conversions in adjacent creature generation/endpoint paths, especially:
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/creatures/generator.py`
  - a few resolver internals where IDs and scene danger are converted after normalization.

Risk:
Some of those conversions are protected by route converters, caller assumptions, or generated data shape, but the behavior is not uniform. Malformed optional numeric hints can still become exceptions in paths that should probably return validation errors or fall back to safe defaults.

Recommended next step:
- In a separate pass, centralize creature numeric coercion across variant/evolve/analyze/generator routes with endpoint-level regression tests. Keep it separate from this campaign-pack helper fix to avoid mixing multiple contracts in one audit.

### Medium-High: The recurring audit bundle is broad enough to slow review

Current evidence:
- `git status --short` now includes 17 modified files across frontend tests, auth/capabilities, creature resolver/generator helpers, request parsing guardrails, cleanup/evidence tooling, source-archive checks, secret scanning, and report updates.
- This run added only `aidm_server/creatures/campaign_pack.py` plus one focused test in `tests/test_creatures_combat.py`, while preserving the existing dirty bundle.

Risk:
The individual fixes are still small and verified, but the accumulated diff is now a multi-topic review. That raises the chance of delaying or confusing review even when each patch is safe on its own.

Recommended next step:
- Split or commit the accumulated recurring-audit bundle by concern before another source-level audit change: security scanner, request parsing guard, cleanup/evidence tooling, source archive evidence, auth/capability hardening, creature numeric coercion, frontend regression/visual-smoke, and reports.

### Medium: Rendered UX evidence was not recaptured

Current evidence:
- This run did not launch the app or capture screenshots.
- The source change was backend campaign-pack helper logic, so visual-smoke would not directly prove the fix.

Risk:
Static frontend scans and backend tests are not a substitute for rendered UX evidence when frontend code changes are being reviewed.

Recommended next step:

```bash
PLAYWRIGHT_CHROMIUM_CHANNEL=chrome make visual-smoke
make visual-smoke-review
```

Run this after frontend runtime/UI changes or before release evidence refresh, not because of this backend helper-only patch.

## Larger Suggested Improvements

- Create a shared creature numeric normalization helper for party level, party size, enemy count, generated pack count, and danger level so resolver, generator, variant, evolution, balance, and campaign-pack paths stop each defining subtly different integer behavior.
- Add focused endpoint regressions for malformed numeric hints on `/api/creatures/variant`, `/api/creatures/evolve`, `/api/creatures/analyze-balance`, and manual `/api/sessions/<id>/combat/start`.
- Add a small `make audit-guardrails` target that wraps the cheap recurring checks used here: secret scan, request JSON guard, state snapshot writer guard, observability bundle check, API type drift check, creature/combat focused tests, and release checklist status.
- Regenerate packaging/source-archive evidence after the current dirty bundle is split or committed; source archive evidence is less useful while it predates many uncommitted source changes.
- Keep the two external-required release checklist rows visible until hosted database backup/restore proof and real external telemetry receipt evidence are attached.

## Recommended Next Run Focus

1. Prefer review/split/commit of the accumulated audit bundle before adding another source fix.
2. If another code fix is still needed, make it the endpoint-level creature numeric coercion follow-up with tight tests.
3. If release is near, refresh source archive and packaging evidence after the dirty bundle is resolved.
4. Close the two external-required release checklist rows with hosted/staging evidence when those environments are available.

# Daily AIDM Codebase Improvement Audit - 2026-06-27 06:02 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe recurring audit across backend correctness, creature/combat helpers, frontend accessibility/static hygiene, security guardrails, release evidence, developer workflow checks, and the already-dirty recurring-audit bundle. Existing uncommitted auth/capability, request-JSON, cleanup/evidence, visual-smoke, scanner, source-archive, frontend regression, test, and report edits were preserved. This run made one small backend robustness fix, refreshed the release checklist status, prepended this report, and updated the fallback automation memory file.

## What Was Inspected

- Physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Automation memory at `$CODEX_HOME/automations/daily-aidm-codebase-improvement-audit/memory.md`; `CODEX_HOME` is unset in this shell, so the standard fallback `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md` was read.
- Prior recurring-audit context from local Codex memory: preserve dirty work, choose a narrow safe fix, verify it with focused checks, and keep release blockers prominent.
- Current dirty-tree boundary with `git status --short`; existing uncommitted files were treated as boundary state and not reverted:
  - `aidm_frontend/scripts/visual-smoke.cjs`
  - `aidm_frontend/src/App.test.tsx`
  - `aidm_server/capabilities.py`
  - `aidm_server/creatures/resolver.py`
  - `improvements_suggestions.md`
  - `scripts/check_request_json_parsing.py`
  - `scripts/cleanup_artifacts.sh`
  - `scripts/render_packaging_cleanup_evidence.py`
  - `scripts/render_rc_issue_evidence.py`
  - `scripts/scan_secrets.py`
  - `tests/test_auth.py`
  - `tests/test_creatures_combat.py`
  - `tests/test_render_packaging_cleanup_evidence.py`
  - `tests/test_render_rc_issue_evidence.py`
  - `tests/test_request_json_parsing_guard.py`
  - `tests/test_secret_scan.py`
- Existing top report in `improvements_suggestions.md`, especially the 2026-06-26 16:04 MDT source-archive link-target hardening entry and its carry-forward release findings.
- Backend request parsing and integer coercion hotspots:
  - `aidm_server/validation.py`
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/creatures/resolver.py`
  - `aidm_server/creatures/campaign_pack.py`
  - `aidm_server/blueprints/sessions.py`
  - `aidm_server/blueprints/maps.py`
  - `aidm_server/blueprints/segments.py`
- Creature/combat tests around resolver behavior and request normalization:
  - `tests/test_creatures_combat.py`
- Static frontend UX/accessibility hygiene by scanning `aidm_frontend/src` and `aidm_frontend/scripts` for obvious `href="#"`, `target="_blank"`, `debugger`, and console logging issues. No app-source issue was found that justified widening this run.
- Security and developer workflow guardrails:
  - `scripts/scan_secrets.py`
  - `scripts/check_request_json_parsing.py`
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/check_observability_bundle.py`
  - `scripts/generate_api_types.py --check`
  - `git diff --check`
- Release checklist status:
  - `make release-checklist-status`
  - `tmp/release/release-checklist-status.md`
  - `tmp/release/release-checklist-status.json`

## Small Safe Fix Made

### Harden creature request party-level and party-size normalization

Affected files:
- `aidm_server/creatures/resolver.py`
- `tests/test_creatures_combat.py`
- `improvements_suggestions.md`

Problem:
`normalize_creature_request` already normalized booleans and bounded `enemyCount`, but it still used raw `int(...)` conversions for `partyLevel` and `partySize`. A malformed caller value such as `"bogus"` could raise `ValueError` before the resolver reached its intended defaults. This was inconsistent with the nearby safe integer handling and could turn a bad optional creature-resolution hint into a 500-style failure in resolver-backed flows.

Change:
- Added a tiny `_minimum_int` helper in `aidm_server/creatures/resolver.py`.
- Switched `partyLevel` and `partySize` normalization to that helper, preserving the existing lower-bound behavior while defaulting malformed values to `1` and `4`.
- Extended the existing `test_creature_request_normalizes_string_booleans_and_enemy_count_bounds` regression to cover malformed `partyLevel` and low `partySize`.
- Did not change generation prompts, bestiary matching thresholds, save policy, database state, auth behavior, frontend UI, Socket.IO behavior, or release policy.

Rationale:
This is a low-blast-radius correctness fix at the request-normalization boundary. It makes resolver behavior consistent with the existing `enemyCount` guard, avoids one class of avoidable runtime exception, and is easy to verify with the existing creature/combat test module.

## Verification

- `./.venv/bin/python -m py_compile aidm_server/creatures/resolver.py`
  - Passed.
- `./.venv/bin/python -m pytest tests/test_creatures_combat.py -k "creature_request_normalizes" -q`
  - Passed: 1 test, 70 deselected.
- `./.venv/bin/python -m pytest tests/test_creatures_combat.py -q`
  - Passed: 71 tests.
- `./.venv/bin/python -m pytest tests/test_render_rc_issue_evidence.py tests/test_render_packaging_cleanup_evidence.py tests/test_request_json_parsing_guard.py tests/test_secret_scan.py -q`
  - Passed: 32 tests.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/check_observability_bundle.py`
  - Passed.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `git diff --check`
  - Passed.
- `make release-checklist-status`
  - Passed and wrote `tmp/release/release-checklist-status.md` and `.json`.
  - Summary: 104 passed, 2 external-required, 0 manual-review, 0 failed, 0 unmapped.

## High-Priority Findings

### High: Release checklist still has two external-required evidence items

Current evidence:
- `make release-checklist-status` reports 104 passed, 2 external-required, 0 manual-review, 0 failed, and 0 unmapped.
- Evidence packet status remains `ready-for-issue-closure`.
- Remaining external-required rows:
  - Data Integrity line 69: database backup taken before deployment.
  - Observability line 107: external telemetry endpoint receives events when enabled.

Risk:
The local code and evidence guardrails are in good shape, but final release closure still depends on hosted/staging proof. Local tests cannot prove provider-specific hosted backup/restore or real external telemetry receipt.

Recommended next step:

```bash
make release-checklist-status
sed -n '1,80p' tmp/release/release-checklist-status.md
```

Then attach hosted database backup/restore evidence and hosted telemetry receipt evidence in the release venue.

### Medium-High: Creature numeric coercion still has follow-up cleanup outside this resolver path

Current evidence:
- This run fixed `partyLevel` and `partySize` inside `normalize_creature_request`.
- Static inspection still found raw `int(...)` conversions in adjacent creature endpoints and helpers, including:
  - `aidm_server/blueprints/creatures.py` for variant, evolve, analyze-balance, and manual combat-start values.
  - `aidm_server/creatures/campaign_pack.py` for campaign-pack count, party level, and party size.
  - `aidm_server/creatures/resolver.py` for direct `campaignId` and `sessionId` conversion after normalization.

Risk:
Not all of those are equally user-facing, and some may be protected by route or caller assumptions. They are still worth normalizing through shared validation helpers so malformed optional numeric hints degrade to validation errors or defaults instead of exceptions.

Recommended next step:
- In a separate small pass, add or reuse one creature numeric coercion helper and cover the affected endpoint contracts with focused tests. Keep that separate from today's resolver-only fix to avoid widening review scope.

### Medium-High: The recurring audit bundle is still accumulating review scope

Current evidence:
- `git status --short` now includes a broad multi-area recurring-audit bundle across auth/capabilities, request JSON parsing, cleanup/evidence tooling, source-archive inspection, secret scanning, creature resolver normalization, frontend tests, visual-smoke tooling, and reports.
- This run added only `aidm_server/creatures/resolver.py` and `tests/test_creatures_combat.py` as source/test changes, plus this report entry.

Risk:
Each individual change remains bounded and verified, but the combined diff is increasingly hard to review as one unit. Accumulated small fixes can become operationally risky if they are not split or committed by concern.

Recommended next step:
- Review and split or commit the accumulated hardening bundle before adding another source change in the next recurring audit.

### Medium: Visual-smoke evidence was not recaptured

Current evidence:
- This run performed static frontend/UX hygiene scans but did not launch the app or capture fresh screenshots.
- The code change was backend resolver-only, so recapturing visual smoke would have added runtime churn without directly proving the fix.

Risk:
The frontend scan did not find an obvious safe edit, but it is not a rendered UX proof for future UI changes.

Recommended next step:

```bash
PLAYWRIGHT_CHROMIUM_CHANNEL=chrome make visual-smoke
make visual-smoke-review
```

Run that after frontend runtime/UI changes, not for this resolver-only backend fix.

## Larger Suggested Improvements

- Centralize creature numeric coercion for party level, party size, enemy count, and generated-pack counts so resolver, variant, evolution, balance, and combat-start paths share the same malformed-input behavior.
- Add a small `make audit-guardrails` target that wraps the recurring cheap checks used here: scanner, request JSON guard, state writer guard, observability check, API type check, focused release-tooling tests, and release checklist status.
- Keep the release checklist's two external-required rows explicit until hosted database backup/restore and telemetry receipt evidence are attached.
- Add a freshness warning to packaging cleanup evidence when the newest source archive predates the current working-tree changes. This remains useful because the latest source archive evidence can otherwise look stronger than it is for a dirty tree.
- Split the current dirty bundle by concern before review: auth/capabilities, scanner/security, request parsing guard, cleanup/evidence tooling, source-archive evidence, creature resolver robustness, frontend regression coverage, visual-smoke workflow, and report updates.

## Recommended Next Run Focus

1. If publishing is near, regenerate the source archive and rerun packaging cleanup evidence so the archive matches the current source tree.
2. If staying local-only, continue with small deterministic backend guardrails, especially the remaining creature numeric coercion follow-up.
3. Close the two external-required release checklist rows with hosted/staging database backup proof and telemetry receipt proof when those environments are available.
4. Split or commit the accumulated recurring-audit bundle before adding another source change.

# Daily AIDM Codebase Improvement Audit - 2026-06-26 16:04 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe recurring audit across release packaging evidence, source-archive safety checks, security guardrails, frontend evidence review, static backend guardrails, and the already-dirty recurring-audit bundle. Existing uncommitted auth/capability, request-JSON, cleanup/evidence, visual-smoke, scanner, frontend regression, test, and report edits were preserved. This run made one small release-tooling hardening fix, refreshed non-destructive release evidence outputs, prepended this report, and recreated the missing fallback automation memory file.

## What Was Inspected

- Physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Automation memory at `$CODEX_HOME/automations/daily-aidm-codebase-improvement-audit/memory.md`; the shell did not expose an existing file, so the standard fallback `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md` was recreated before closeout.
- Prior recurring-audit context from Codex memory and the local `aidm-daily-audit` workflow notes: preserve unrelated dirty work, choose one narrow safe fix, run focused verification, and keep release blockers prominent.
- Existing top report in `improvements_suggestions.md`, especially the 2026-06-26 06:04 MDT scanner hardening entry and its carry-forward release checklist findings.
- Dirty-tree boundary with `git status --short`; existing uncommitted files were treated as boundary state and not reverted:
  - `aidm_frontend/scripts/visual-smoke.cjs`
  - `aidm_frontend/src/App.test.tsx`
  - `aidm_server/capabilities.py`
  - `improvements_suggestions.md`
  - `scripts/check_request_json_parsing.py`
  - `scripts/cleanup_artifacts.sh`
  - `scripts/render_packaging_cleanup_evidence.py`
  - `scripts/scan_secrets.py`
  - `tests/test_auth.py`
  - `tests/test_render_packaging_cleanup_evidence.py`
  - `tests/test_request_json_parsing_guard.py`
  - `tests/test_secret_scan.py`
- Release and packaging evidence helpers:
  - `scripts/render_rc_issue_evidence.py`
  - `scripts/render_packaging_cleanup_evidence.py`
  - `scripts/create_source_archive.sh`
  - `tests/test_render_rc_issue_evidence.py`
  - `tests/test_render_packaging_cleanup_evidence.py`
  - `tests/test_render_release_evidence_packet.py`
  - `tests/test_render_release_checklist_status.py`
- Standard guardrails:
  - `scripts/scan_secrets.py`
  - `scripts/check_request_json_parsing.py`
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/check_observability_bundle.py`
  - `scripts/generate_api_types.py --check`
  - `git diff --check`
- Generated local evidence:
  - `make packaging-cleanup-evidence`
  - `make release-checklist-status`
  - `make visual-smoke-review`

## Small Safe Fix Made

### Flag forbidden symlink and hard-link targets in source archives

Affected files:
- `scripts/render_rc_issue_evidence.py`
- `tests/test_render_rc_issue_evidence.py`
- `improvements_suggestions.md`

Problem:
The source-archive inspector checked tar member paths for forbidden generated/runtime artifacts such as `tmp`, `.env.local`, SQLite databases, logs, build outputs, and dependency folders. It did not inspect tar symlink or hard-link targets. A source archive could therefore contain a harmless-looking member name that pointed at an excluded path or file type, and the release evidence would still report the archive as clean.

Change:
- Added `_link_target_is_forbidden` to reject empty link targets, absolute link targets, parent-directory traversal, forbidden path parts/pairs, and forbidden suffixes.
- Taught `inspect_source_archive` to append `member -> target` findings for unsafe symlink or hard-link targets.
- Added a focused regression test using a synthetic tarball with `AIDM-main/docs/current-database -> ../tmp/dnd_ai_dm.db`.
- Did not change app runtime behavior, database state, auth behavior, frontend UI, gameplay logic, provider calls, Socket.IO behavior, or release checklist policy.

Rationale:
This is a narrow packaging/evidence hardening change with low blast radius. It strengthens an existing release proof path and catches a class of archive hygiene issue that path-name checks alone miss.

## Verification

- `./.venv/bin/python -m py_compile scripts/render_rc_issue_evidence.py`
  - Passed.
- `./.venv/bin/python -m pytest tests/test_render_rc_issue_evidence.py -q`
  - Passed: 13 tests.
- `./.venv/bin/python -m pytest tests/test_render_rc_issue_evidence.py tests/test_render_packaging_cleanup_evidence.py tests/test_render_release_evidence_packet.py tests/test_render_release_checklist_status.py -q`
  - Passed: 57 tests.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/check_observability_bundle.py`
  - Passed.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `git diff --check`
  - Passed.
- `make packaging-cleanup-evidence`
  - Passed and wrote `tmp/release/packaging-cleanup-evidence.md` and `.json`.
  - The latest existing source archive inspected was `tmp/release/aidm-source-20260619-220029.tar.gz`: status passed, forbidden paths 0, large files 1, large files not LFS-tracked 0.
- `make release-checklist-status`
  - Passed and wrote `tmp/release/release-checklist-status.md` and `.json`.
  - Summary: 104 passed, 2 external-required, 0 manual-review, 0 failed, 0 unmapped.
- `make visual-smoke-review`
  - Passed against the latest existing screenshot set at `tmp/verification_artifacts/visual-smoke/2026-06-25T22-04-03-222Z`.
  - `tmp/release/visual-smoke-review.md` reports screenshots 3/3, failures none:
    - `desktop-shell.png`: 1440x900, 392853 bytes, 512+ unique colors, different-pixel ratio 0.9596.
    - `short-height-composer.png`: 1280x620, 283368 bytes, 512+ unique colors, different-pixel ratio 0.9724.
    - `mobile-full.png`: 390x844, 133162 bytes, 512+ unique colors, different-pixel ratio 0.9242.

## High-Priority Findings

### High: Release checklist still has two external-required evidence items

Current evidence:
- `make release-checklist-status` reports 104 passed, 2 external-required, 0 failed, and evidence packet status `ready-for-issue-closure`.
- Remaining external-required rows:
  - Data Integrity line 69: database backup taken before deployment.
  - Observability line 107: external telemetry endpoint receives events when enabled.

Risk:
The local source tree and generated local evidence are strong, but final release closure still depends on hosted/staging proof. Local checks cannot prove provider-specific hosted backup/restore or real telemetry receipt by the configured external endpoint.

Recommended next step:

```bash
make release-checklist-status
sed -n '1,80p' tmp/release/release-checklist-status.md
```

Then attach hosted database backup/restore evidence and hosted telemetry receipt evidence in the release venue.

### Medium-High: Source archive evidence is clean but not freshly regenerated for this dirty tree

Current evidence:
- `make packaging-cleanup-evidence` passed, but it inspected the latest existing archive: `tmp/release/aidm-source-20260619-220029.tar.gz`.
- This audit intentionally did not create another large source archive; it only hardened the inspector and rechecked the latest available archive.

Risk:
The archive hygiene policy now catches unsafe link targets, and the latest archive passes that policy. However, that archive predates the current dirty audit bundle, so it should not be treated as the publishable artifact for today's source tree.

Recommended next step:

```bash
make source-archive
make packaging-cleanup-evidence
```

Run those only when you are ready to publish or refresh release handoff artifacts, because they create another large archive under `tmp/release/`.

### Medium-High: The recurring audit bundle is still accumulating review scope

Current evidence:
- `git status --short` now includes a multi-area uncommitted audit bundle across auth/capabilities, request JSON parsing, cleanup/evidence tooling, secret scanning, source-archive evidence, frontend tests, visual-smoke tooling, and reports.
- This run added only `scripts/render_rc_issue_evidence.py` and `tests/test_render_rc_issue_evidence.py` as source/test changes, but it landed on top of an already broad dirty bundle.

Risk:
Each individual change remains bounded and verified, but the combined diff is increasingly hard to review as one unit. Accumulated small fixes can still become operationally risky if they are not split or committed by concern.

Recommended next step:
- Review and split or commit the accumulated hardening bundle before adding more source changes in the next recurring audit.

### Medium: Visual-smoke screenshots were reviewed, not recaptured

Current evidence:
- `make visual-smoke-review` passed against the latest existing screenshot set from 2026-06-25.
- This run did not launch the app or capture fresh screenshots because the code change was release-tooling only.

Risk:
The review artifact confirms the latest screenshot set is structurally valid, colorful, and nonblank. It does not prove today's unrelated future frontend edits render correctly in a live browser.

Recommended next step:

```bash
PLAYWRIGHT_CHROMIUM_CHANNEL=chrome make visual-smoke
make visual-smoke-review
```

Run that after frontend runtime/UI changes, not for packaging-only helper changes.

## Larger Suggested Improvements

- Add source-archive link-target checks to any future standalone packaging linter if the archive policy is split out of `render_rc_issue_evidence.py`.
- Add a small `make audit-guardrails` target that wraps the recurring local checks used here: scanner, request JSON guard, state writer guard, observability check, API type check, relevant evidence-helper tests, release checklist status, and visual-smoke review.
- Keep the release checklist's two external-required rows explicit until hosted database backup/restore and telemetry receipt evidence are attached.
- Add a freshness note to packaging cleanup evidence when the latest source archive predates the current working-tree changes. That would make stale archive evidence harder to overread during release handoff.
- Split the current dirty bundle by concern before review: auth/capabilities, scanner/security, request parsing guard, cleanup/evidence tooling, source-archive evidence, frontend regression coverage, visual-smoke workflow, and report updates.

## Recommended Next Run Focus

1. If publishing is near, regenerate the source archive and rerun packaging cleanup evidence so the archive matches the current source tree.
2. If staying local-only, keep focusing on small deterministic guardrails and avoid broad runtime behavior changes.
3. Close the two external-required release checklist rows with hosted/staging database backup proof and telemetry receipt proof when those environments are available.
4. Split or commit the accumulated recurring-audit bundle before the next source change.

# Daily AIDM Codebase Improvement Audit - 2026-06-26 06:04 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe recurring audit across security guardrails, developer workflow, release evidence, frontend static health, observability tooling, and the already-dirty recurring-audit bundle. Existing uncommitted auth/capability, request-JSON, cleanup/evidence, visual-smoke, frontend regression, test, and report edits were preserved. This run made one small security-tooling fix, refreshed the release checklist and visual-smoke review evidence, prepended this report, and updated the fallback automation memory file.

## What Was Inspected

- Physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Automation memory: the shell did not expose `CODEX_HOME`, so the standard fallback `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md` was used and updated before closeout.
- Prior recurring-audit context from Codex memory, especially the 2026-06-25 visual-smoke Chrome fallback and the standing instruction to keep fixes tiny and release blockers prominent.
- Dirty-tree boundary with `git status --short`; existing uncommitted files were treated as boundary state and not reverted:
  - `aidm_frontend/scripts/visual-smoke.cjs`
  - `aidm_frontend/src/App.test.tsx`
  - `aidm_server/capabilities.py`
  - `improvements_suggestions.md`
  - `scripts/check_request_json_parsing.py`
  - `scripts/cleanup_artifacts.sh`
  - `scripts/render_packaging_cleanup_evidence.py`
  - `scripts/scan_secrets.py`
  - `tests/test_auth.py`
  - `tests/test_render_packaging_cleanup_evidence.py`
  - `tests/test_request_json_parsing_guard.py`
  - `tests/test_secret_scan.py`
- Existing top report in `improvements_suggestions.md`, including the carry-forward external-required release checklist rows and prior visual-smoke artifact path.
- Security and guardrail tooling:
  - `scripts/scan_secrets.py`
  - `tests/test_secret_scan.py`
  - `scripts/check_request_json_parsing.py`
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/check_observability_bundle.py`
  - `scripts/generate_api_types.py`
- Release evidence:
  - `make release-checklist-status`
  - `make visual-smoke-review`
  - `tmp/release/release-checklist-status.md`
  - `tmp/release/visual-smoke-review.md`
- Frontend static health:
  - `npm --prefix aidm_frontend run lint`
  - `npm --prefix aidm_frontend run typecheck`
  - `npm --prefix aidm_frontend audit --omit=dev`
- Static source scans for direct request JSON parsing, secret/token patterns, generated-artifact cleanup, subprocess usage, accessibility markers, local/session storage use, and TODO/FIXME-style comments.

## Small Safe Fix Made

### Detect GitHub fine-grained personal access tokens in the repo secret scanner

Affected files:
- `scripts/scan_secrets.py`
- `tests/test_secret_scan.py`
- `improvements_suggestions.md`

Problem:
The repo secret scanner already covered classic GitHub token prefixes such as `ghp_`, `gho_`, `ghu_`, `ghs_`, and `ghr_`, but it did not cover modern fine-grained GitHub personal access tokens using the `github_pat_` prefix. That left a practical blind spot in a security guardrail that is run locally and in CI.

Change:
- Added a `GitHub fine-grained token` pattern for `github_pat_...` values.
- Added a focused regression test proving such tokens are detected and redacted from scanner snippets.
- Did not change application runtime behavior, auth semantics, database state, frontend UI, gameplay logic, provider calls, Socket.IO behavior, or release checklist policy.

Rationale:
This is a narrow security-tooling improvement with low blast radius. It strengthens a repeatedly used guardrail without changing production code paths. Redaction coverage matters because scanner output may be copied into reports, CI logs, or review threads.

## Verification

- `./.venv/bin/python -m pytest tests/test_secret_scan.py -q`
  - Passed: 9 tests.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python -m pytest tests/test_auth.py tests/test_secret_scan.py tests/test_request_json_parsing_guard.py tests/test_render_packaging_cleanup_evidence.py tests/test_visual_smoke_review.py tests/test_check_observability_bundle.py -q`
  - Passed: 51 tests.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `./.venv/bin/python scripts/check_observability_bundle.py`
  - Passed.
- `make release-checklist-status`
  - Passed and wrote `tmp/release/release-checklist-status.md` and `.json`.
  - Summary: 104 passed, 2 external-required, 0 manual-review, 0 failed, 0 unmapped.
- `git diff --check`
  - Passed.
- `npm --prefix aidm_frontend run lint`
  - Passed.
- `npm --prefix aidm_frontend run typecheck`
  - Passed.
- `npm --prefix aidm_frontend audit --omit=dev`
  - Passed: 0 vulnerabilities.
- `make visual-smoke-review`
  - Passed against the latest existing screenshot set at `tmp/verification_artifacts/visual-smoke/2026-06-25T22-04-03-222Z`.
  - `tmp/release/visual-smoke-review.md` reports screenshots 3/3, failures none:
    - `desktop-shell.png`: 1440x900, 392853 bytes, 512+ unique colors, different-pixel ratio 0.9596.
    - `short-height-composer.png`: 1280x620, 283368 bytes, 512+ unique colors, different-pixel ratio 0.9724.
    - `mobile-full.png`: 390x844, 133162 bytes, 512+ unique colors, different-pixel ratio 0.9242.

## High-Priority Findings

### High: Release checklist still has two external-required evidence items

Current evidence:
- `make release-checklist-status` reports 104 passed, 2 external-required, 0 failed, and evidence packet status `ready-for-issue-closure`.
- Remaining external-required rows:
  - Data Integrity line 69: database backup taken before deployment.
  - Observability line 107: external telemetry endpoint receives events when enabled.

Risk:
The local source tree and generated evidence are passing, but final release closure still depends on hosted/staging proof. Local tests cannot prove a provider-specific database backup/restore path or real telemetry receipt by the configured external endpoint.

Recommended next step:

```bash
make release-checklist-status
sed -n '1,80p' tmp/release/release-checklist-status.md
```

Then attach hosted database backup/restore evidence and hosted telemetry receipt evidence in the release venue.

### Medium-High: The recurring audit bundle is still accumulating review scope

Current evidence:
- `git status --short` still includes a multi-area uncommitted audit bundle across auth/capabilities, request JSON parsing, cleanup/evidence tooling, secret scanning, frontend tests, visual-smoke tooling, and reports.
- This run added only the fine-grained GitHub token scanner coverage, but it necessarily landed in an already-dirty scanner/test pair.

Risk:
Each individual change remains bounded and verified, but the combined diff is becoming harder to review as one unit. Accumulated safe changes can still create operational drag if they wait too long before being split, reviewed, or committed.

Recommended next step:
- Review and commit or split the accumulated hardening bundle before adding additional source changes in the next recurring audit.

### Medium: Visual-smoke screenshots were reviewed, not recaptured

Current evidence:
- `make visual-smoke-review` passed against the latest existing screenshot set from 2026-06-25.
- This run did not launch the app or capture fresh screenshots because the only source change was scanner-only.

Risk:
The review artifact confirms the latest screenshot set is structurally valid, colorful, and nonblank. It does not prove that today's source tree renders identically in a live browser after unrelated future frontend edits.

Recommended next step:
- Recapture screenshots when frontend runtime/UI code changes, using the known local fallback:

```bash
PLAYWRIGHT_CHROMIUM_CHANNEL=chrome make visual-smoke
make visual-smoke-review
```

## Larger Suggested Improvements

- Add fine-grained scanner patterns for other token families only when the project actually uses those ecosystems, to avoid noisy overmatching.
- Add a small `make audit-guardrails` target that wraps the checks repeatedly used by this automation: scanner, request JSON guard, state writer guard, API type check, observability bundle check, frontend lint/typecheck, release checklist status, and visual-smoke review.
- Keep the release checklist's two external-required rows as explicit release-administration blockers until hosted database and telemetry evidence is attached.
- Consider a CI step for `scripts/check_request_json_parsing.py` and `scripts/check_state_snapshot_writers.py` if CI time remains acceptable; these are cheap, deterministic guardrails that catch source drift.
- Split the current dirty bundle by concern before review: auth/capabilities, scanner/security, request parsing guard, cleanup/evidence tooling, frontend regression coverage, visual-smoke workflow, and report updates.

## Recommended Next Run Focus

1. If credentials and hosted environment access are available, close the two external-required release checklist rows with real backup/restore and telemetry receipt proof.
2. If staying local-only, avoid expanding scanner/request-cleanup scope unless a fresh, concrete defect is found.
3. Split or commit the accumulated recurring-audit bundle before the next source change.
4. Recapture visual-smoke screenshots only if frontend UI/runtime files changed since the last screenshot set.

# Daily AIDM Codebase Improvement Audit - 2026-06-25 16:07 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe follow-up audit across developer workflow, frontend rendered UX evidence, release evidence status, security guardrails, and the already-dirty recurring-audit bundle. Existing uncommitted auth, request-JSON, cleanup/evidence, scanner, frontend modal regression, tests, and report edits were preserved. This run made one small developer-workflow hardening change to the visual smoke script, refreshed visual-smoke browser evidence with installed Chrome, refreshed release checklist status, prepended this dated report, and updated automation memory.

## What Was Inspected

- Current physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`; the shell still did not expose `CODEX_HOME`, so the standard `/Users/danny/.codex` fallback path was used.
- Dirty-tree boundary with `git status --short`; existing uncommitted files were treated as boundary state and not reverted:
  - `aidm_frontend/src/App.test.tsx`
  - `aidm_server/capabilities.py`
  - `improvements_suggestions.md`
  - `scripts/check_request_json_parsing.py`
  - `scripts/cleanup_artifacts.sh`
  - `scripts/render_packaging_cleanup_evidence.py`
  - `scripts/scan_secrets.py`
  - `tests/test_auth.py`
  - `tests/test_render_packaging_cleanup_evidence.py`
  - `tests/test_request_json_parsing_guard.py`
  - `tests/test_secret_scan.py`
- Latest top report in `improvements_suggestions.md`, especially the 2026-06-25 06:05 MDT modal pending-state regression and carry-forward recommendation to refresh browser/visual UX evidence.
- Frontend smoke and rendered evidence tooling:
  - `aidm_frontend/scripts/visual-smoke.cjs`
  - `aidm_frontend/scripts/browser-smoke.cjs`
  - `aidm_frontend/package.json`
  - `aidm_frontend/vite.config.ts`
  - `scripts/review_visual_smoke_artifacts.py`
  - `Makefile`
- Release evidence and guardrails:
  - `make visual-smoke`
  - `make visual-smoke-review`
  - `make release-checklist-status`
  - `scripts/scan_secrets.py`
  - `git diff --check`
- Static source scans for smoke targets, Playwright/browser usage, accessibility markers, request JSON parsing, token storage, subprocess usage, dynamic HTML markers, and TODO/FIXME-style comments.

## Small Safe Fix Made

### Harden visual-smoke managed process shutdown

Affected files:
- `aidm_frontend/scripts/visual-smoke.cjs`
- `improvements_suggestions.md`

Problem:
`visual-smoke.cjs` started an isolated backend and Vite frontend as managed child processes, but its shutdown path only sent termination signals and immediately continued. The neighboring `browser-smoke.cjs` script already waits for managed children to exit and escalates with `SIGKILL` after a grace period. The visual-smoke behavior was not a runtime app bug, but it left a developer-workflow gap: failed or interrupted visual smoke runs had weaker protection against leftover backend/frontend processes.

Change:
- Added `AIDM_VISUAL_SMOKE_SHUTDOWN_GRACE_MS`, defaulting to 2000 ms.
- Let `stopManaged` accept a configurable signal.
- Added `waitForChildExit` and `stopAllManaged`, matching the established browser-smoke pattern.
- Updated normal and error shutdown paths to await graceful exit, then escalate remaining managed children to `SIGKILL`.
- Did not change backend runtime behavior, database state, frontend production UI, gameplay logic, auth behavior, Socket.IO behavior, or release checklist rules.

Rationale:
This is a small developer-workflow and verification reliability fix. It keeps visual-smoke runs better contained when Playwright launch, browser interaction, backend startup, or screenshot capture fails. That matters because the recurring audit now uses visual smoke as release evidence and should not leave local processes behind.

## Verification

- `node --check aidm_frontend/scripts/visual-smoke.cjs`
  - Passed.
- `make visual-smoke`
  - First attempt failed at browser launch because Playwright's bundled Chromium executable was not present in `/Users/danny/Library/Caches/ms-playwright/...`.
  - Verified the failed run cleaned up managed backend/frontend processes and its temporary visual-smoke directory.
- `PLAYWRIGHT_CHROMIUM_CHANNEL=chrome make visual-smoke`
  - Passed using installed Google Chrome.
  - Wrote 3 screenshots under `tmp/verification_artifacts/visual-smoke/2026-06-25T22-04-03-222Z`.
- `make visual-smoke-review`
  - Passed.
  - `tmp/release/visual-smoke-review.md` reports screenshots 3/3, failures none:
    - `desktop-shell.png`: 1440x900, 392853 bytes, 512+ unique colors, different-pixel ratio 0.9596.
    - `short-height-composer.png`: 1280x620, 283368 bytes, 512+ unique colors, different-pixel ratio 0.9724.
    - `mobile-full.png`: 390x844, 133162 bytes, 512+ unique colors, different-pixel ratio 0.9242.
- `npm --prefix aidm_frontend run lint`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `make release-checklist-status`
  - Passed and wrote `tmp/release/release-checklist-status.md` and `.json`.
  - Summary: 104 passed, 2 external-required, 0 manual-review, 0 failed, 0 unmapped.
- `git diff --check`
  - Passed before this report prepend.

## High-Priority Findings

### High: Release checklist still has two external-required evidence items

Current evidence:
- `make release-checklist-status` reports 104 passed, 2 external-required, 0 failed, and evidence packet status `ready-for-issue-closure`.
- Remaining external-required rows:
  - Data Integrity line 69: database backup taken before deployment.
  - Observability line 107: external telemetry endpoint receives events when enabled.

Risk:
The local source tree and generated release evidence are passing, and this run refreshed rendered visual evidence. Final release closure still depends on target-specific hosted/staging proof. Local tests cannot substitute for provider-specific database backup/restore evidence or proof that the configured external telemetry endpoint receives events.

Recommended next step:

```bash
make release-checklist-status
sed -n '1,80p' tmp/release/release-checklist-status.md
```

Then attach hosted database backup/restore evidence and hosted telemetry receipt evidence in the release venue.

### Medium: Playwright bundled Chromium is missing locally

Current evidence:
- Plain `make visual-smoke` failed because Playwright expected a missing cached Chromium executable.
- `PLAYWRIGHT_CHROMIUM_CHANNEL=chrome make visual-smoke` passed with the installed system Chrome.

Risk:
Developers or automation runners that do not set `PLAYWRIGHT_CHROMIUM_CHANNEL=chrome` and have not installed Playwright browsers will fail visual smoke before exercising the app. This is not a product runtime issue, but it can make local evidence refreshes feel broken.

Recommended next step:
- Either install Playwright browsers for this checkout with the normal Playwright install flow, or document/export `PLAYWRIGHT_CHROMIUM_CHANNEL=chrome` for this Mac's local smoke workflow.

### Medium: The recurring audit hardening bundle is still growing

Current evidence:
- `git status --short` now includes the prior recurring audit bundle plus this run's `aidm_frontend/scripts/visual-smoke.cjs` developer-workflow fix.

Risk:
The individual changes are bounded and verified, but the review surface is accumulating across unrelated areas: auth/capability behavior, request JSON parsing guardrails, cleanup/evidence tooling, secret scanning, frontend modal regression coverage, visual-smoke process cleanup, tests, and report-only updates.

Recommended next step:
- Review and commit or split the accumulated hardening bundle before the next recurring audit run adds more scope.

## Larger Suggested Improvements

- Add a documented local smoke prerequisite path: either `npx playwright install chromium` or `PLAYWRIGHT_CHROMIUM_CHANNEL=chrome`, so `make visual-smoke` failures are less surprising on this Mac.
- Add a small regression test or CI probe for managed-process cleanup in smoke scripts if these scripts become shared release tooling across machines.
- Add a `make audit-guardrails` target that collects the recurring lightweight checks now used repeatedly: frontend lint, request JSON guard, state writer guard, API type drift check, secret scan, visual-smoke-review, release-checklist-status, and focused pytest bundles.
- Keep rendered browser evidence separate from source-only correctness checks in reports; it is useful evidence, but still not a replacement for hosted backup/telemetry proof.
- Split the current dirty bundle by concern before review: auth/capabilities, scanner/security, request parsing guard, cleanup/evidence tooling, frontend tests, smoke workflow, and report updates.

## Recommended Next Run Focus

1. Close the two external-required release checklist rows with hosted database backup/restore and telemetry receipt evidence, if credentials/environment are available.
2. If staying local-only, add the documented Playwright browser/channel prerequisite so visual smoke is one-command reliable on this Mac.
3. Avoid adding more scanner/request-cleanup changes unless a fresh defect is found; those areas already have several uncommitted audit changes.
4. Consider splitting or committing the accumulated recurring-audit bundle before adding another source fix.

# Daily AIDM Codebase Improvement Audit - 2026-06-25 06:05 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe source, test, UX/accessibility, release-tooling, and guardrail pass across the already-dirty recurring-audit checkout. Existing uncommitted auth, scanner, request-JSON, cleanup/evidence, test, and report edits were preserved; this run added only a focused frontend regression test for pending destructive-dialog closure behavior, refreshed current release-checklist evidence, prepended this dated report, and updated automation memory.

## What Was Inspected

- Current physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Dirty-tree boundary with `git status --short`; existing uncommitted edits were treated as boundary state and not reverted:
  - `aidm_server/capabilities.py`
  - `improvements_suggestions.md`
  - `scripts/check_request_json_parsing.py`
  - `scripts/cleanup_artifacts.sh`
  - `scripts/render_packaging_cleanup_evidence.py`
  - `scripts/scan_secrets.py`
  - `tests/test_auth.py`
  - `tests/test_render_packaging_cleanup_evidence.py`
  - `tests/test_request_json_parsing_guard.py`
  - `tests/test_secret_scan.py`
- Prior recurring-audit memory/context from `/Users/danny/.codex/memories/MEMORY.md`, `/Users/danny/.codex/memories/skills/aidm-daily-audit/SKILL.md`, and the automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`.
- Latest top report in `improvements_suggestions.md`, especially the 2026-06-24 cleanup/evidence hardening and the two carry-forward external-required release rows.
- Frontend modal UX/accessibility surfaces:
  - `aidm_frontend/src/ModalShell.tsx`
  - `aidm_frontend/src/useModalFocusTrap.ts`
  - `aidm_frontend/src/App.tsx`
  - `aidm_frontend/src/App.test.tsx`
  - `aidm_frontend/src/PlayerDeleteDialog.tsx`
  - `aidm_frontend/src/PlayerEditDialog.tsx`
- Frontend TTS/audio lifecycle code in `aidm_frontend/src/useTtsNarration.ts`, especially object URL cleanup and queued-audio failure paths.
- Existing guardrails and proof paths:
  - `scripts/check_request_json_parsing.py`
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/generate_api_types.py --check`
  - `scripts/scan_secrets.py`
- Release status tooling through `make release-checklist-status`.
- Frontend production dependency audit through `npm --prefix aidm_frontend audit --omit=dev`.
- Static source scans for direct JSON parsing, secret/token handling, subprocess/error-handling hotspots, dynamic HTML markers, modal roles, focus-trap behavior, and object URL lifecycle markers.

## Small Safe Fix Made

### Add a pending destructive-dialog close regression

Affected files:
- `aidm_frontend/src/App.test.tsx`
- `improvements_suggestions.md`

Problem:
The shared modal shell already disables close buttons/backdrop dismissal for pending player edit/delete operations, and `closeCurrentDialog` already avoids closing pending player dialogs when Escape is pressed. However, the regression suite only proved the normal delete confirmation can close with Escape before a delete request starts. It did not prove the more important pending state: once a destructive delete is in flight, Escape must not dismiss the confirmation and make the user lose sight of the ongoing operation.

Change:
- Added a focused `App.test.tsx` regression that opens the character delete confirmation, stalls the `/api/players/30` delete request, verifies the dialog enters its pending `Deleting...` state, presses Escape, and asserts the dialog remains open while exactly one delete request is in flight.
- Resolved the stalled delete request at the end of the test so React state settles cleanly and the dialog closes after the operation completes.
- Did not change runtime modal, auth, backend, database, Socket.IO, provider, or gameplay behavior.

Rationale:
This is a narrow UX/accessibility and safety guardrail. It locks down an existing intended behavior around destructive actions without changing production code. The most likely future regression would be an innocent refactor of `useModalFocusTrap`, `closeCurrentDialog`, or pending-dialog state that lets Escape dismiss an in-flight destructive action. The new test catches that without expanding the runtime blast radius.

## Verification

- `npm --prefix aidm_frontend run test:unit -- App.test.tsx`
  - Passed: 85 tests.
- `npm --prefix aidm_frontend run typecheck`
  - Passed.
- `npm --prefix aidm_frontend run lint`
  - Passed.
- `npm --prefix aidm_frontend audit --omit=dev`
  - Passed: 0 vulnerabilities.
- `./.venv/bin/python -m pytest tests/test_auth.py tests/test_secret_scan.py tests/test_request_json_parsing_guard.py tests/test_render_packaging_cleanup_evidence.py -q`
  - Passed: 44 tests.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `make release-checklist-status`
  - Passed and wrote `tmp/release/release-checklist-status.md` and `.json`.
  - Summary: 104 passed, 2 external-required, 0 manual-review, 0 failed, 0 unmapped.
- `git diff --check`
  - Passed after this report prepend.

## High-Priority Findings

### High: Release checklist still has two external-required evidence items

Current evidence:
- `make release-checklist-status` reports 104 passed, 2 external-required, 0 failed, and evidence packet status `ready-for-issue-closure`.
- Remaining external-required rows:
  - Data Integrity line 69: database backup taken before deployment.
  - Observability line 107: external telemetry endpoint receives events when enabled.

Risk:
The local source tree, generated evidence, focused tests, production dependency audit, and guardrail checks are in good shape, but final release closure still depends on hosted/staging proof. This should remain a release-administration blocker until provider-specific backup/restore evidence and real telemetry receipt evidence are attached.

Recommended next step:

```bash
make release-checklist-status
sed -n '1,80p' tmp/release/release-checklist-status.md
```

Then attach hosted database backup/restore proof and external telemetry receipt proof in the release venue.

### Medium-High: Rendered browser UX/accessibility proof was not refreshed

Current evidence:
- This run added and verified a focused modal pending-state regression, plus frontend typecheck and lint.
- This run did not start the app, capture screenshots, inspect console output, test keyboard-only flows in a real browser, or verify mobile/desktop layout.

Risk:
Unit tests and static checks are useful guardrails, but they cannot prove real focus behavior in a browser, visual overlap, mobile target sizing, scroll behavior, or rendered accessibility semantics.

Recommended next step:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
make visual-smoke-review
```

### Medium: The recurring audit hardening bundle is still accumulating uncommitted changes

Current evidence:
- `git status --short` still shows the prior auth-capability, scanner, request-JSON, cleanup, packaging-evidence, test, and report files modified together, plus this run's `aidm_frontend/src/App.test.tsx` addition.

Risk:
The changes are bounded and verified, but the review surface is growing. Additional recurring audit runs before review/commit will make it harder to separate auth behavior, security scanner hardening, request parsing guardrails, cleanup tooling, frontend UX regression coverage, and report-only changes.

Recommended next step:
- Review and commit the accumulated hardening bundle soon, or split it into reviewable auth, scanner, request-guard, cleanup/evidence, frontend-test, and report commits before the next recurring audit.

## Larger Suggested Improvements

- Add a `make audit-guardrails` target that runs request JSON parsing, state snapshot writer inventory, API type drift check, secret scan, frontend `App.test.tsx`, frontend typecheck/lint, packaging cleanup evidence tests, and the focused recurring-audit pytest bundle.
- Add a small `useModalFocusTrap` unit test harness or component-level test to isolate Escape, Tab wrap, disabled-close, and focus-return behavior outside the large `App.test.tsx` workflow.
- Refresh rendered browser/visual UX evidence separately so accessibility and layout conclusions are backed by actual screenshots and interaction checks.
- Keep release-readiness reporting explicit about the difference between local generated evidence completion and hosted/staging external proof.
- Split runtime auth/capability changes from scanner, request-guard, cleanup/evidence, frontend-test, and report-only changes when committing this dirty tree.

## Recommended Next Run Focus

1. Close or verify the two external-required release checklist rows with hosted database backup/restore and telemetry receipt evidence.
2. If release evidence is still waiting on external proof, run browser/visual smoke and inspect rendered UX/accessibility behavior.
3. If staying source-only, prefer a new guardrail outside the repeatedly touched scanner/request-cleanup areas.
4. Decide whether to commit or split the accumulated audit hardening bundle before another dated report is prepended.

# Daily AIDM Codebase Improvement Audit - 2026-06-24 16:04 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe follow-up source/tooling pass across the already-dirty recurring-audit checkout, missing automation memory, latest 2026-06-24 06:03 MDT report, cleanup and packaging evidence workflow, request JSON parsing guard, state snapshot writer inventory, generated API type drift, committed-secret scanner behavior, frontend production dependency audit state, release checklist status, and static security/UX/accessibility risk markers. Existing uncommitted auth, scanner, request-JSON, cleanup, and report edits were preserved; this run only tightened cleanup-script Git metadata preservation, updated packaging cleanup evidence expectations, expanded focused evidence tests, prepended this dated report, and wrote automation memory.

## What Was Inspected

- Current physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Dirty-tree boundary with `git status --short`; existing uncommitted edits were treated as boundary state and not reverted:
  - `aidm_server/capabilities.py`
  - `improvements_suggestions.md`
  - `scripts/check_request_json_parsing.py`
  - `scripts/cleanup_artifacts.sh`
  - `scripts/render_packaging_cleanup_evidence.py`
  - `scripts/scan_secrets.py`
  - `tests/test_auth.py`
  - `tests/test_render_packaging_cleanup_evidence.py`
  - `tests/test_request_json_parsing_guard.py`
  - `tests/test_secret_scan.py`
- Prior recurring-audit memory/context from `/Users/danny/.codex/memories/MEMORY.md` and `/Users/danny/.codex/memories/skills/aidm-daily-audit/SKILL.md`.
- Automation memory path `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`; no file existed at the start of this run.
- Latest top report in `improvements_suggestions.md`, especially the 06:03 MDT `.DS_Store` cleanup/evidence change and the two release checklist external-required rows.
- Cleanup and packaging evidence workflow:
  - `scripts/cleanup_artifacts.sh`
  - `scripts/render_packaging_cleanup_evidence.py`
  - `tests/test_render_packaging_cleanup_evidence.py`
  - `scripts/render_rc_issue_evidence.py`
  - `scripts/create_source_archive.sh`
- Existing guardrails and proof paths:
  - `scripts/check_request_json_parsing.py`
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/generate_api_types.py --check`
  - `scripts/scan_secrets.py`
- Frontend production dependency audit through `npm --prefix aidm_frontend audit --omit=dev`.
- Release checklist status through `make release-checklist-status`.
- Static source scans for direct JSON parsing, dynamic HTML/code markers, session/local storage token handling, subprocess usage, TODO/FIXME markers, cleanup/archive coverage, and accessibility/focus-trap hotspots.

## Small Safe Fix Made

### Preserve `.git` during bytecode cache cleanup and require evidence coverage

Affected files:
- `scripts/cleanup_artifacts.sh`
- `scripts/render_packaging_cleanup_evidence.py`
- `tests/test_render_packaging_cleanup_evidence.py`
- `improvements_suggestions.md`

Problem:
The earlier cleanup script already skipped `.venv` and `aidm_frontend/node_modules` while deleting `__pycache__` directories, and the newer `.DS_Store` cleanup pass also skipped `.git`. The bytecode-cache cleanup pass did not skip `.git`, leaving a small destructive-cleanup safety inconsistency. It was unlikely to matter in normal Git layouts, but cleanup tooling should not traverse Git metadata at all.

Change:
- Added `$ROOT_DIR/.git` pruning to the `__pycache__` cleanup `find` command.
- Added `git metadata preserved` to the packaging cleanup evidence checks.
- Updated packaging cleanup evidence tests so fixture cleanup scripts must include `.git` preservation coverage.
- Did not run `make clean` or delete any current local artifacts during this audit run.

Rationale:
This is a narrow developer-workflow and packaging-safety fix. It does not alter backend runtime behavior, auth, gameplay state, provider selection, database state, frontend UI behavior, generated API contracts, or release checklist rules. It only prevents cleanup traversal into Git internals and makes release evidence catch regressions in that preservation policy.

## Verification

- `bash -n scripts/cleanup_artifacts.sh`
  - Passed.
- `./.venv/bin/python -m py_compile scripts/render_packaging_cleanup_evidence.py`
  - Passed.
- `./.venv/bin/python -m pytest tests/test_render_packaging_cleanup_evidence.py -q`
  - Passed: 4 tests.
- `./.venv/bin/python -m pytest tests/test_auth.py tests/test_secret_scan.py tests/test_request_json_parsing_guard.py tests/test_render_packaging_cleanup_evidence.py -q`
  - Passed: 44 tests.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `npm --prefix aidm_frontend audit --omit=dev`
  - Passed: found 0 vulnerabilities.
- `make release-checklist-status`
  - Passed and wrote `tmp/release/release-checklist-status.md` and `.json`.
  - Summary: 104 passed, 2 external-required, 0 manual-review, 0 failed, 0 unmapped.
- `git diff --check`
  - Passed.

## High-Priority Findings

### High: Release checklist still has two external-required evidence items

Current evidence:
- `make release-checklist-status` reports 104 passed, 2 external-required, 0 failed, and evidence packet status `ready-for-issue-closure`.
- Remaining external-required rows:
  - Data Integrity line 69: database backup taken before deployment.
  - Observability line 107: external telemetry endpoint receives events when enabled.

Risk:
The local source tree, generated evidence, dependency audit, and guardrail checks are in good shape, but final release closure still depends on hosted/staging proof. This should remain a release-administration blocker until provider-specific backup/restore evidence and real telemetry receipt evidence are attached.

Recommended next step:

```bash
make release-checklist-status
sed -n '1,80p' tmp/release/release-checklist-status.md
```

Then attach hosted backup/restore proof and external telemetry receipt proof in the release venue.

### Medium-High: Rendered frontend UX/accessibility proof was not refreshed

Current evidence:
- This run checked static focus/accessibility markers and frontend production dependency audit state.
- This run did not start the app, capture screenshots, test keyboard-only modal flows, inspect console output, or verify mobile/desktop layout.

Risk:
Static checks cannot prove modal focus order, real screen-reader labels, mobile layout, visual overlap, button target sizing, or interaction quality.

Recommended next step:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
make visual-smoke-review
```

### Medium: The recurring audit hardening bundle is still accumulating uncommitted changes

Current evidence:
- `git status --short` still shows the auth-capability, scanner, request-JSON, cleanup, packaging-evidence, test, and report files modified together.

Risk:
The current changes are bounded and tested, but the review surface is growing. Another audit run before review/commit will make it harder to separate auth behavior, security scanner hardening, cleanup tooling, and report-only changes.

Recommended next step:
- Review and commit the accumulated hardening bundle soon, or deliberately split it into reviewable auth, scanner, request-guard, cleanup, and report commits before the next recurring audit.

## Larger Suggested Improvements

- Add a `make audit-guardrails` target that runs the request JSON guard, state snapshot writer guard, API type check, secret scanner, packaging cleanup evidence tests, and focused recurring-audit test bundle.
- Add a non-destructive cleanup dry run that reports `.DS_Store`, `__pycache__`, `tmp`, `.pytest_cache`, `.ruff_cache`, `.vite`, and `dist` candidates without deleting them.
- Refresh rendered browser/visual UX evidence separately so accessibility and layout conclusions are backed by actual screenshots and interaction checks.
- Keep release-readiness reporting explicit about the difference between local generated evidence completion and hosted/staging external proof.
- Split runtime auth/capability changes from scanner, request-guard, cleanup, and report-only changes when committing this dirty tree.

## Recommended Next Run Focus

1. Close or verify the two external-required release checklist rows with hosted backup/restore and telemetry receipt evidence.
2. If release evidence is still waiting on external proof, run browser/visual smoke and inspect UX/accessibility behavior.
3. If staying source-only, look for one new guardrail outside the repeatedly touched scanner/request-cleanup areas.
4. Decide whether to commit the accumulated audit hardening bundle before another dated report is prepended.

# Daily AIDM Codebase Improvement Audit - 2026-06-24 06:03 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe source/tooling pass across the already-dirty recurring-audit checkout, automation memory, latest 2026-06-23 reports, cleanup and packaging evidence workflow, request JSON parsing guard, state snapshot writer inventory, generated API type drift, committed-secret scanner behavior, auth-capability changes already present in the worktree, frontend production dependency audit state, release checklist status, and static security/UX/accessibility carry-forward risks. Existing uncommitted edits were preserved; this run only added macOS `.DS_Store` cleanup coverage, updated packaging cleanup evidence expectations, added focused test coverage, prepended this dated report, and updated automation memory.

## What Was Inspected

- Current physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Dirty-tree boundary with `git status --short`; existing uncommitted edits were treated as boundary state and not reverted:
  - `aidm_server/capabilities.py`
  - `improvements_suggestions.md`
  - `scripts/check_request_json_parsing.py`
  - `scripts/scan_secrets.py`
  - `tests/test_auth.py`
  - `tests/test_request_json_parsing_guard.py`
  - `tests/test_secret_scan.py`
- Automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`.
- Latest top report in `improvements_suggestions.md`, especially the 2026-06-23 16:03 MDT `.env.production` secret-scanner hardening and carry-forward release evidence gaps.
- Cleanup and packaging evidence workflow:
  - `scripts/cleanup_artifacts.sh`
  - `scripts/render_packaging_cleanup_evidence.py`
  - `tests/test_render_packaging_cleanup_evidence.py`
  - `scripts/create_source_archive.sh`
  - `.gitignore`
- Ignored Finder metadata currently present in the checkout through `find ... -name .DS_Store`.
- Existing guardrails and proof paths:
  - `scripts/check_request_json_parsing.py`
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/generate_api_types.py --check`
  - `scripts/scan_secrets.py`
- Frontend production dependency audit through `npm --prefix aidm_frontend audit --omit=dev`.
- Release checklist status through `make release-checklist-status`.
- Static source scans for JSON parsing bypasses, local/session storage token handling, dynamic HTML markers, subprocess usage, TODO/FIXME markers, cleanup/archive coverage, and secret/key references.

## Small Safe Fix Made

### Include `.DS_Store` in cleanup evidence and cleanup behavior

Affected files:
- `scripts/cleanup_artifacts.sh`
- `scripts/render_packaging_cleanup_evidence.py`
- `tests/test_render_packaging_cleanup_evidence.py`
- `improvements_suggestions.md`

Problem:
The repository already ignores `.DS_Store`, the source archive excludes `.DS_Store`, and many ignored `.DS_Store` files are present across the local checkout. However, `scripts/cleanup_artifacts.sh` did not remove these macOS Finder metadata files, and `scripts/render_packaging_cleanup_evidence.py` did not require cleanup coverage for them. That left a small repo-hygiene and packaging-evidence gap: ignored metadata could keep accumulating locally even though packaging policy already treats it as unwanted source handoff material.

Change:
- Added a pruned `find` pass to `scripts/cleanup_artifacts.sh` that deletes `.DS_Store` files while skipping `.git`, `.venv`, and `aidm_frontend/node_modules`.
- Added `macOS Finder metadata` to the packaging cleanup evidence needles.
- Updated focused packaging cleanup evidence tests so passing cleanup evidence now proves `.DS_Store` cleanup coverage.
- Did not run `make clean` or delete current local artifacts during this audit run.

Rationale:
This is a narrow developer-workflow and packaging-readiness fix. It does not alter backend runtime behavior, auth, capability checks, gameplay state mutation, provider selection, database state, frontend UI behavior, generated API contracts, or release checklist logic. It only aligns cleanup behavior with existing ignore/archive policy and makes the evidence renderer catch regressions.

## Verification

- `bash -n scripts/cleanup_artifacts.sh`
  - Passed.
- `./.venv/bin/python -m pytest tests/test_render_packaging_cleanup_evidence.py -q`
  - Passed: 4 tests.
- `./.venv/bin/python -m py_compile scripts/render_packaging_cleanup_evidence.py`
  - Passed.
- `./.venv/bin/python -m pytest tests/test_auth.py tests/test_secret_scan.py tests/test_request_json_parsing_guard.py tests/test_render_packaging_cleanup_evidence.py -q`
  - Passed: 44 tests.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `npm --prefix aidm_frontend audit --omit=dev`
  - Passed: found 0 vulnerabilities.
- `make release-checklist-status`
  - Passed and wrote `tmp/release/release-checklist-status.md` and `.json`.
  - Summary: 104 passed, 2 external-required, 0 manual-review, 0 failed, 0 unmapped.
- `git diff --check`
  - Passed.

## High-Priority Findings

### High: Release checklist still has two external-required evidence items

Current evidence:
- `make release-checklist-status` reports 104 passed, 2 external-required, 0 failed, and evidence packet status `ready-for-issue-closure`.
- Remaining external-required rows:
  - Data Integrity line 69: database backup taken before deployment.
  - Observability line 107: external telemetry endpoint receives events when enabled.

Risk:
The source tree and generated local evidence remain strong, but final release closure still depends on target-specific hosted/staging proof. The release should not be described as administratively closed until hosted database backup/restore evidence and external telemetry receipt proof are attached or reviewed.

Recommended next step:

```bash
make release-checklist-status
sed -n '1,80p' tmp/release/release-checklist-status.md
```

Then attach hosted backup/restore proof and external telemetry receipt proof in the release venue.

### Medium-High: Rendered frontend UX/accessibility proof was still not refreshed

Current evidence:
- This run checked frontend production dependency audit state and static source risk markers.
- This run did not start the frontend, capture screenshots, run browser flows, inspect keyboard focus, or verify rendered mobile/desktop layout.

Risk:
Static checks do not prove modal focus traps, screen-reader labels, mobile layout, visual overlap, button sizing, console cleanliness, or real interaction quality.

Recommended next step:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
make visual-smoke-review
```

### Medium: Accumulated audit/auth hardening edits remain uncommitted

Current evidence:
- `git status --short` now shows modified files:
  - `aidm_server/capabilities.py`
  - `improvements_suggestions.md`
  - `scripts/check_request_json_parsing.py`
  - `scripts/cleanup_artifacts.sh`
  - `scripts/render_packaging_cleanup_evidence.py`
  - `scripts/scan_secrets.py`
  - `tests/test_auth.py`
  - `tests/test_render_packaging_cleanup_evidence.py`
  - `tests/test_request_json_parsing_guard.py`
  - `tests/test_secret_scan.py`

Risk:
The edits are bounded and tested, but recurring audit changes continue stacking in the same worktree. That makes review harder and increases the chance of mixing unrelated auth, scanner, request-guard, cleanup, and report changes in one release/publish step.

Recommended next step:
- Review and commit the accumulated hardening bundle soon, or deliberately split it into reviewable auth, guardrail, cleanup, and report commits before the next recurring audit.

## Larger Suggested Improvements

- Add a `make audit-guardrails` target that runs the request JSON guard, state snapshot writer guard, API type check, secret scanner, packaging cleanup evidence tests, and focused recurring-audit test bundle.
- Refresh rendered browser/visual UX evidence in a separate run so accessibility and layout conclusions are backed by actual screenshots and interaction checks.
- Extend packaging cleanup evidence to record whether ignored metadata files existed before cleanup in a non-destructive dry-run mode.
- Add a short release-readiness note that distinguishes local generated evidence completion from the two external-required hosted/staging proof attachments.
- Keep runtime auth/capability changes split from scanner, cleanup, and report-only changes when committing the current dirty tree.

## Recommended Next Run Focus

1. Close or verify the two external-required release checklist rows with hosted backup/restore and telemetry receipt evidence.
2. If release evidence is still waiting on external proof, run frontend browser/visual smoke and inspect UX/accessibility behavior.
3. If staying source-only, look for one new guardrail blind spot outside the recently repeated secret-scanner/request-JSON areas.
4. Decide whether to commit the accumulated audit hardening bundle before another dated report is prepended.

# Daily AIDM Codebase Improvement Audit - 2026-06-23 16:03 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe follow-up pass across the already-dirty recurring-audit checkout, prior same-day audit report, missing automation memory file, auth-capability boundary changes already present in the worktree, committed-secret scanner behavior, request JSON parsing guard behavior, state snapshot writer inventory, generated API type drift, frontend production dependency audit state, release checklist status, and static security/UX/accessibility carry-forward risks. The worktree already contained uncommitted changes in `aidm_server/capabilities.py`, `tests/test_auth.py`, `improvements_suggestions.md`, `scripts/check_request_json_parsing.py`, `scripts/scan_secrets.py`, `tests/test_request_json_parsing_guard.py`, and `tests/test_secret_scan.py` at the start of this run. Existing edits were preserved; this run only extended the committed-secret scanner's file-candidate logic for real environment files with environment suffixes, added focused coverage, prepended this dated report, and recreated automation memory.

## What Was Inspected

- Current physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Dirty-tree boundary with `git status --short`; existing uncommitted auth-capability and audit-guardrail edits were treated as boundary state and not reverted.
- Automation memory path `$CODEX_HOME/automations/daily-aidm-codebase-improvement-audit/memory.md`; no prior file was present at the start of this run.
- Latest same-day top report in `improvements_suggestions.md`, especially the 2026-06-23 06:04 MDT committed-secret scanner expansion for `.pem`/`.key` and private-key blocks.
- Current auth-capability diff in:
  - `aidm_server/capabilities.py`
  - `tests/test_auth.py`
- Committed-secret scanner implementation and tests:
  - `scripts/scan_secrets.py`
  - `tests/test_secret_scan.py`
- Request JSON parsing guard implementation and tests:
  - `scripts/check_request_json_parsing.py`
  - `tests/test_request_json_parsing_guard.py`
  - `aidm_server/validation.py`
- State snapshot writer inventory guard:
  - `scripts/check_state_snapshot_writers.py`
  - `docs/state_snapshot_writer_inventory.md`
- API contract generation check:
  - `scripts/generate_api_types.py --check`
- Frontend production dependency audit state through `npm --prefix aidm_frontend audit --omit=dev`.
- Release checklist status through `make release-checklist-status`.
- Static source scans for JSON parsing bypasses, committed secret and token references, `.pem`/`.key`/`.env*` files, frontend browser storage/cookie usage, dynamic code/HTML injection markers, TODO/workaround markers, and capability-gated routes.

## Small Safe Fix Made

### Scan real `.env.*` files with deployment suffixes

Affected files:
- `scripts/scan_secrets.py`
- `tests/test_secret_scan.py`
- `improvements_suggestions.md`

Problem:
The committed-secret scanner intentionally skips `.env.local`, but its text-candidate decision still depended mostly on `Path.suffix`. A real environment file such as `.env.production` has suffix `.production`, so it could be skipped before pattern inspection even though production/staging env files are exactly where accidentally committed provider tokens, telemetry keys, or operator tokens are high-risk.

Change:
- Updated `_is_text_candidate` so dot-env files are scanned when their filename starts with `.env`, unless an earlier skip rule excludes the file.
- Preserved the existing explicit `.env.local` skip.
- Added `test_secret_scan_scans_real_env_files_with_environment_suffixes` to prove a secret in `.env.production` is detected and redacted.

Rationale:
This is a narrow security and developer-workflow hardening change. It does not alter runtime auth, provider selection, database state, gameplay state mutation, frontend behavior, generated API contracts, or deployment configuration. It only closes a scanner file-selection blind spot for env files that should not be committed with real secrets.

## Verification

- `./.venv/bin/python -m pytest tests/test_secret_scan.py -q`
  - Passed: 8 tests.
- `./.venv/bin/python -m py_compile scripts/scan_secrets.py tests/test_secret_scan.py`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python -m pytest tests/test_auth.py tests/test_secret_scan.py tests/test_request_json_parsing_guard.py -q`
  - Passed: 40 tests.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `npm --prefix aidm_frontend audit --omit=dev`
  - Passed: found 0 vulnerabilities.
- `make release-checklist-status`
  - Wrote `tmp/release/release-checklist-status.md` and `.json`.
  - Summary: 104 passed, 2 external-required, 0 manual-review, 0 failed, 0 unmapped.
- `git diff --check`
  - Passed as part of the combined guardrail command.

## High-Priority Findings

### High: Release checklist still has two external-required evidence items

Current evidence:
- `make release-checklist-status` reports 104 passed, 2 external-required, 0 failed, and evidence packet status `ready-for-issue-closure`.
- Remaining external-required rows:
  - Data Integrity line 69: database backup taken before deployment.
  - Observability line 107: external telemetry endpoint receives events when enabled.

Risk:
The generated release packet is locally clean, but the release checklist still needs target-specific hosted/staging proof for backup/restore and telemetry receipt. Treat the source tree and generated packet as strong, but do not call the release administratively closed until those two external evidence items are attached or reviewed in the release venue.

Recommended next step:

```bash
make release-checklist-status
sed -n '1,80p' tmp/release/release-checklist-status.md
```

Then attach hosted backup/restore evidence and external telemetry receipt proof where the RC/release process expects them.

### Medium-High: Rendered frontend UX/accessibility proof was not refreshed

Current evidence:
- This run checked frontend production dependency audit state only.
- This run did not start the frontend, capture screenshots, run browser flows, inspect keyboard focus, or verify rendered mobile/desktop layout.

Risk:
Static checks do not prove modal focus traps, button sizing, mobile layout, visual overlap, screen-reader labels, console cleanliness, or real interaction quality.

Recommended next step:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
make visual-smoke-review
```

### Medium: Accumulated audit/auth hardening edits remain uncommitted

Current evidence:
- `git status --short` still shows modified files:
  - `aidm_server/capabilities.py`
  - `tests/test_auth.py`
  - `improvements_suggestions.md`
  - `scripts/check_request_json_parsing.py`
  - `scripts/scan_secrets.py`
  - `tests/test_request_json_parsing_guard.py`
  - `tests/test_secret_scan.py`

Risk:
The changes are bounded and tested, but recurring audit edits are stacking in the same files. That makes it harder to review one run's change independently and can increase merge/release friction.

Recommended next step:
- Review the accumulated hardening bundle and either commit it as one audit bundle or explicitly keep it as local-only work before more recurring-audit changes accumulate.

## Larger Suggested Improvements

- Add a short secret-scanner policy note explaining which env files are intentionally skipped (`.env.local`) and which deployment env files are scanned.
- Consider a single `make audit-guardrails` target that runs the request JSON guard, state snapshot writer guard, API type contract check, secret scanner, and the focused guardrail tests used by recurring audits.
- Refresh browser/visual smoke in a separate run so UX/accessibility claims are backed by rendered evidence instead of static source inspection.
- Keep runtime auth/capability changes isolated from scanner/report changes when committing, if the current dirty tree is split into reviewable bundles.
- Add a release-status explainer that distinguishes generated local evidence completion from the two external-required hosted/staging proof attachments.

## Recommended Next Run Focus

1. Confirm the two external-required release checklist rows have hosted backup/restore and telemetry receipt evidence attached.
2. If release evidence is closed, run frontend browser/visual smoke and inspect accessibility/focus behavior.
3. If staying source-only, inspect another single guardrail blind spot with direct tests rather than broad runtime behavior.
4. Decide whether the accumulated daily-audit hardening bundle should be committed before more audit reports are prepended.

# Daily AIDM Codebase Improvement Audit - 2026-06-23 06:04 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement pass across the already-dirty recurring-audit checkout, the absent automation memory file, the latest 2026-06-22 reports, committed-secret scanner coverage, request JSON parsing guard behavior, state snapshot writer inventory, generated API type drift, frontend production dependency audit state, release proof value status, operator signoff status, release checklist status, and static security/UX/accessibility carry-forward risks. The worktree already contained uncommitted audit changes in `improvements_suggestions.md`, `scripts/check_request_json_parsing.py`, `scripts/scan_secrets.py`, `tests/test_request_json_parsing_guard.py`, and `tests/test_secret_scan.py` at the start of this run. Those edits were preserved; this run only extended the committed-secret scanner to cover private-key files, added focused coverage, prepended this dated report, and updated automation memory.

## What Was Inspected

- Current physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Dirty-tree boundary with `git status --short`; existing uncommitted audit files were treated as boundary state and not reverted.
- Automation memory path `$CODEX_HOME/automations/daily-aidm-codebase-improvement-audit/memory.md`; no prior file was present at the start of this run.
- Latest top reports in `improvements_suggestions.md`, especially:
  - 2026-06-22 16:02 MDT positional `request.get_json(False, True)` guard coverage.
  - 2026-06-22 06:03 MDT secret-scanner allowlist tightening.
  - 2026-06-21 request JSON parsing and scanner redaction guardrails.
- Committed-secret scanner implementation and tests:
  - `scripts/scan_secrets.py`
  - `tests/test_secret_scan.py`
- Request JSON parsing guard implementation and focused tests:
  - `scripts/check_request_json_parsing.py`
  - `tests/test_request_json_parsing_guard.py`
  - `aidm_server/validation.py`
- Existing lightweight release/developer guardrails:
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/generate_api_types.py --check`
  - `scripts/scan_secrets.py`
  - `git diff --check`
- Frontend dependency/security state through `npm --prefix aidm_frontend audit --omit=dev`.
- External proof and signoff renderers:
  - `make external-proof-values-check`
  - `make operator-signoff-status`
  - `make release-checklist-status`
  - `make rc-finalize-signoff`
- Static source scans for direct JSON parsing, private-key markers, committed `.pem`/`.key` files, frontend storage/cookie usage, dynamic code/HTML injection surfaces, TODO/workaround markers, and provider/key-related configuration references.

## Small Safe Fix Made

### Scan committed private-key files

Affected files:
- `scripts/scan_secrets.py`
- `tests/test_secret_scan.py`
- `improvements_suggestions.md`

Problem:
The committed-secret scanner covered common source/config extensions and several provider-token patterns, but it did not treat `.pem` or `.key` files as text candidates. That meant an accidentally committed private key file could be skipped before pattern inspection, even though this repository has release and deployment workflows where key material should never land in source.

Change:
- Added `.pem` and `.key` to the scanner's text-candidate extension list.
- Added a `Private key block` detector for `-----BEGIN ... PRIVATE KEY-----` headers.
- Added `test_secret_scan_detects_private_key_files` to prove a `.pem` file is scanned, detected, and redacted in the emitted snippet.
- Kept the private-key fixture assembled at runtime so the repository scanner does not flag its own test source.

Rationale:
This is a narrow security/developer-workflow guardrail fix. It changes no runtime auth behavior, provider behavior, database state, gameplay state mutation, frontend behavior, API contract, or deployment configuration. It only expands an existing local/CI scanner so a high-risk committed file type is inspected.

## Verification

- `./.venv/bin/python -m pytest tests/test_secret_scan.py -q`
  - Passed: 7 tests.
- `./.venv/bin/python -m py_compile scripts/scan_secrets.py tests/test_secret_scan.py`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python -m pytest tests/test_request_json_parsing_guard.py -q`
  - Passed: 6 tests.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `npm --prefix aidm_frontend audit --omit=dev`
  - Passed: found 0 production vulnerabilities.
- `make external-proof-values-check`
  - Passed: required complete 22/22; no missing required fields, metadata errors, or invalid errors.
- `make operator-signoff-status`
  - Passed: required complete 19/19; missing or invalid required items 0.
- `make release-checklist-status`
  - Rendered status: 104 passed, 2 external-required, 0 failed, evidence packet `ready-for-issue-closure`.
- `make rc-finalize-signoff`
  - Passed: regenerated operator signoff, external proof check, release evidence packet, artifact consistency, and release checklist status.
- `git diff --check`
  - Passed.

## High-Priority Findings

### High: Release checklist still has two external-required attachment/review items

Current evidence:
- `make external-proof-values-check` passed with required complete 22/22.
- `make operator-signoff-status` passed with required complete 19/19 and no errors.
- `make rc-finalize-signoff` passed end to end.
- `make release-checklist-status` still reports 2 `external-required` items:
  - Data Integrity line 69: database backup taken before deployment.
  - Observability line 107: external telemetry endpoint receives events when enabled.

Risk:
The signoff manifest is complete, but the checklist still wants target-specific/manual evidence attachment or review for backup/restore and telemetry receipt. Treat the current state as signoff-ready from generated local artifacts, but not as fully closed administratively until those two evidence attachments are verified in the release venue.

Recommended next step:

```bash
make release-checklist-status
sed -n '1,80p' tmp/release/release-checklist-status.md
```

Then confirm the hosted backup/restore proof and external telemetry receipt are attached where the RC issue/release process expects them.

### Medium-High: Rendered frontend UX/accessibility proof was not refreshed in this run

Current evidence:
- This run checked frontend production dependency audit state only.
- This run did not start the frontend, capture screenshots, run a browser flow, inspect focus behavior, or review rendered mobile/desktop states.

Risk:
Static source checks and dependency audits do not prove modal focus traps, keyboard reachability, mobile layout, visual overlap, button sizing, screen-reader labels, or console cleanliness.

Recommended next step:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
make visual-smoke-review
```

### Medium: Existing audit guardrail edits remain uncommitted

Current evidence:
- `git status --short` still shows modified audit files:
  - `improvements_suggestions.md`
  - `scripts/check_request_json_parsing.py`
  - `scripts/scan_secrets.py`
  - `tests/test_request_json_parsing_guard.py`
  - `tests/test_secret_scan.py`

Risk:
The changes are bounded and verified, but repeated daily audit edits are accumulating in the same files. That can make it harder to isolate one run's fix or produce a clean release-candidate worktree.

Recommended next step:
- Review the accumulated guardrail diff as one audit bundle and either commit it or explicitly decide to keep it as local-only work.

## Larger Suggested Improvements

- Keep the secret scanner focused on high-confidence findings, but add provider/file-type coverage when it maps directly to AIDM's documented deployment surfaces.
- Consider adding private-key scanning to any future pre-commit hook or local `dev-check` documentation if the team starts handling deployment certificates in adjacent folders.
- Add a short scanner policy note describing why `.env.local` is intentionally skipped while `.pem`/`.key` files are not.
- Keep using `make rc-finalize-signoff` as the source of truth for generated signoff readiness after external proof values are filled.
- Add a small report helper that explains why `release-checklist-status` can show `external-required` while `operator-signoff-status` is passed, so future audit reports do not confuse generated signoff completeness with administrative evidence attachment.
- Refresh browser/visual UX and accessibility proof in a separate run; do not infer rendered quality from source-only checks.

## Recommended Next Run Focus

1. Confirm the two `external-required` release checklist rows have their backup/restore and telemetry evidence attached in the RC issue/release venue.
2. If release evidence is administratively closed, run frontend browser/visual smoke and inspect accessibility/focus behavior.
3. If staying source-only, look for another single guardrail blind spot with focused tests rather than broadening runtime behavior.
4. Decide whether to commit the accumulated recurring-audit guardrail changes before more daily audit edits stack up.

# Daily AIDM Codebase Improvement Audit - 2026-06-22 16:02 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement follow-up across the already-dirty daily-audit checkout, fallback automation memory, the same-day 2026-06-22 06:03 MDT report, request JSON parsing guard behavior, committed-secret scanner behavior, state snapshot writer inventory guard, generated API type drift, frontend production dependency audit state, and carry-forward hosted/staging proof gaps. The worktree already contained uncommitted audit changes in `improvements_suggestions.md`, `scripts/check_request_json_parsing.py`, `scripts/scan_secrets.py`, `tests/test_request_json_parsing_guard.py`, and `tests/test_secret_scan.py` at the start of this run. Those edits were preserved; this run only extended the request JSON parsing guard, added focused coverage, prepended this dated report, and updated automation memory.

## What Was Inspected

- Current physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Dirty-tree boundary with `git status --short`; existing uncommitted guardrail/report files were treated as boundary state and not reverted.
- Automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md` because `$CODEX_HOME` was unset in this shell.
- Latest top reports in `improvements_suggestions.md`, especially:
  - 2026-06-22 06:03 MDT secret-scanner allowlist tightening.
  - 2026-06-21 16:04 MDT request JSON parsing guard syntax-error handling.
  - Carry-forward hosted/staging proof blockers.
- Request JSON parsing guard implementation and focused tests:
  - `scripts/check_request_json_parsing.py`
  - `tests/test_request_json_parsing_guard.py`
  - `aidm_server/validation.py`
- Existing lightweight release/developer guardrails:
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/generate_api_types.py --check`
  - `scripts/scan_secrets.py`
- Committed-secret scanner tests already dirty from prior same-day audit work:
  - `scripts/scan_secrets.py`
  - `tests/test_secret_scan.py`
- Frontend production dependency audit state through `npm --prefix aidm_frontend audit --omit=dev`.
- Static source references for request JSON parsing forms in `aidm_server`, `scripts`, and `tests`.

## Small Safe Fix Made

### Catch positional silent JSON parsing in the request guard

Affected files:
- `scripts/check_request_json_parsing.py`
- `tests/test_request_json_parsing_guard.py`
- `improvements_suggestions.md`

Problem:
The request JSON parsing guard now uses AST inspection and catches direct backend calls such as `request.get_json(silent=True)`, including multiline keyword calls. Flask also accepts positional arguments as `get_json(force=False, silent=False, cache=True)`, so a route-level call like `request.get_json(False, True)` could still bypass the guard while enabling silent JSON parsing outside the shared validation helpers.

Change:
- Extended `_is_request_get_json_silent_call` to flag a literal second positional `True` argument as silent mode.
- Added `test_find_violations_reports_positional_silent_json_parsing` to prove the bypass form is reported.
- Preserved the existing allowed-helper behavior, multiline keyword detection, syntax-error reporting, and CLI wording.

Rationale:
This is a narrow developer-workflow and correctness guardrail fix. It changes no runtime request parsing, auth behavior, database state, gameplay state mutation, provider configuration, frontend behavior, generated API contracts, or secret-scan policy. It only closes a positional-call blind spot in an existing source check.

## Verification

- `./.venv/bin/python -m pytest tests/test_request_json_parsing_guard.py -q`
  - Passed: 6 tests.
- `./.venv/bin/python -m py_compile scripts/check_request_json_parsing.py tests/test_request_json_parsing_guard.py`
  - Passed.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python -m pytest tests/test_secret_scan.py -q`
  - Passed: 6 tests.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `npm --prefix aidm_frontend audit --omit=dev`
  - Passed: found 0 production vulnerabilities.
- `git diff --check`
  - Passed.

## High-Priority Findings

### High: Hosted beta proof remains the main release blocker

Current evidence:
- Local source guardrails for committed-secret scanning, request JSON parsing, state snapshot writer inventory, generated API type drift, release evidence helpers, frontend production dependency audit, and external proof validation are present and passing locally.
- This run did not have a hosted/staging target URL, target env file, operator token, non-admin token, workspace ID, campaign/session/player IDs, or deployment output to prove the real hosted environment.

Risk:
Local source checks cannot prove hosted CORS, cookie flags, CSRF behavior, account-token suppression, non-admin denial behavior, metrics exposure, Socket.IO deployment behavior, provider configuration, observability receipt, beta SLOs, or final operator signoff.

Recommended next step:

```bash
make hosted-rc-evidence HOSTED_RC_EVIDENCE_ARGS="--target-url <target-url> --auth-token <operator-token> --workspace-id <workspace-id> --non-admin-token <token> --campaign-id <campaign-id> --session-id <session-id> --player-id <player-id> --env-file <target-env>"
```

### Medium-High: Browser-token and cookie behavior still needs hosted confirmation

Current evidence:
- Prior static inspection found the expected account/workspace token migration and cookie handling paths in `aidm_frontend/src/useRuntimeSettings.ts` and request header construction in `aidm_frontend/src/api.ts`.
- This run did not start a hosted browser session or inspect final deployed response headers.

Risk:
A local-only token transport path can look correct while hosted SameSite, Secure, CSRF, CORS, or reverse-proxy behavior differs. This is especially important for account-token suppression and non-admin denial proof.

Recommended next step:

```bash
make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_ARGS="--target-url <target-url> --auth-token <operator-token> --non-admin-token <token>"
```

### Medium: Rendered frontend UX/accessibility proof was not refreshed

Current evidence:
- This run verified frontend production dependency audit state only.
- This run did not start the frontend, capture screenshots, run a browser flow, or inspect rendered interactive states.

Risk:
Static source inspection and dependency checks do not prove modal focus behavior, keyboard reachability, mobile layout, visual overlap, button sizing, screen-reader labels, or console cleanliness.

Recommended next step:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
make visual-smoke-review
```

## Larger Suggested Improvements

- Keep expanding source guardrails around concrete bypasses rather than broadening runtime behavior during daily audits.
- Consider documenting the Flask `get_json(force, silent, cache)` positional order near the checker predicate so future edits do not accidentally drop positional coverage.
- Keep secret-scan reporting redacted and value-scoped; failed scans should identify file, line, and finding kind without echoing credential material into logs.
- Keep source-only audit changes small until hosted/staging proof is available. The strongest current release-readiness improvement is still live hosted evidence, not broad local churn.
- Refresh rendered frontend smoke separately; static checks cannot replace visual, keyboard, and focus validation.

## Recommended Next Run Focus

1. If hosted/staging credentials and IDs are available, run `make hosted-rc-evidence` and inspect the generated evidence.
2. If hosted proof remains unavailable, run frontend browser/visual smoke and `make visual-smoke-review`.
3. Re-run `make external-proof-values-check`, `make rc-finalize-signoff`, and `make release-checklist-status` only after real hosted proof values are present.
4. Preserve the existing uncommitted guardrail changes unless the user explicitly asks for a broader cleanup or commit.

# Daily AIDM Codebase Improvement Audit - 2026-06-22 06:03 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement pass across the existing daily-audit guardrail checkout, fallback automation memory, the latest 2026-06-21 reports, committed-secret scanner behavior, request JSON parsing guard behavior, state snapshot writer inventory guard, generated API type drift, dependency audit state, source references around JSON parsing, browser token/storage handling, cookie/CSRF surfaces, subprocess and broad exception boundaries, and frontend UX/accessibility proof gaps. The worktree already contained uncommitted audit changes in `improvements_suggestions.md`, `scripts/check_request_json_parsing.py`, `scripts/scan_secrets.py`, `tests/test_request_json_parsing_guard.py`, and `tests/test_secret_scan.py` at the start of this run. Those edits were preserved; this run only tightened secret-scanner allowlisting, added focused scanner coverage, prepended this dated report, and updated automation memory.

## What Was Inspected

- Current physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Dirty-tree boundary with `git status --short`; existing uncommitted files matched prior audit guardrail work and were not reverted.
- Automation memory fallback at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md` because `$CODEX_HOME` was unset in this shell.
- Latest top reports in `improvements_suggestions.md`, especially:
  - 2026-06-21 16:04 MDT request JSON parsing guard syntax-error handling.
  - 2026-06-21 06:02 MDT secret-scan output redaction.
  - Carry-forward hosted/staging proof blockers.
- Committed-secret scanner implementation and focused tests:
  - `scripts/scan_secrets.py`
  - `tests/test_secret_scan.py`
- Request JSON parsing guard implementation and tests:
  - `scripts/check_request_json_parsing.py`
  - `tests/test_request_json_parsing_guard.py`
  - `aidm_server/validation.py`
- Existing lightweight release/developer guardrails:
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/generate_api_types.py --check`
  - `scripts/scan_secrets.py`
- Frontend dependency and static security surfaces:
  - `npm --prefix aidm_frontend audit --omit=dev`
  - Static searches for direct DOM HTML injection, dynamic code execution, browser storage, and cookie access in `aidm_frontend/src`.
- Backend/script static surfaces for direct JSON parsing, subprocess use, broad exception boundaries, and temporary-workaround markers.

## Small Safe Fix Made

### Tighten secret-scanner allowlisting to the matched secret value

Affected files:
- `scripts/scan_secrets.py`
- `tests/test_secret_scan.py`
- `improvements_suggestions.md`

Problem:
The committed-secret scanner allowed a finding when either the detected secret value or the full source line contained allowlist markers such as `example`, `fake`, or `placeholder`. That meant a real pasted credential could be skipped if the variable name or nearby comment included a benign marker, for example `OPENAI_EXAMPLE_API_KEY = "sk-..."`.

Change:
- Removed full-line allowlisting from `scan_paths`; allowlist markers now apply only to the matched secret value.
- Preserved existing placeholder behavior for detected values like `your-token-placeholder`.
- Added `test_secret_scan_does_not_allowlist_real_secret_from_line_context` to prove a real-looking key is still reported when `example` appears elsewhere on the line.
- Kept the prior scanner redaction behavior intact so reported snippets still replace the matched value with `<redacted>`.

Rationale:
This is a narrow security/developer-workflow hardening change. It changes no runtime behavior, auth behavior, database state, gameplay state mutation, frontend behavior, provider configuration, generated API contracts, or secret pattern policy. It only closes an allowlist bypass in an existing repo guardrail.

## Verification

- `./.venv/bin/python -m pytest tests/test_secret_scan.py -q`
  - Passed: 6 tests.
- `./.venv/bin/python -m py_compile scripts/scan_secrets.py tests/test_secret_scan.py`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python -m pytest tests/test_request_json_parsing_guard.py -q`
  - Passed: 5 tests.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `npm --prefix aidm_frontend audit --omit=dev`
  - Passed: found 0 production vulnerabilities.
- `git diff --check`
  - Passed.

## High-Priority Findings

### High: Hosted beta proof remains the main release blocker

Current evidence:
- Local source guardrails for committed-secret scanning, request JSON parsing, state snapshot writer inventory, generated API type drift, release evidence helpers, frontend production dependency audit, and external proof validation are present and passing locally.
- This run did not have a hosted/staging target URL, target env file, operator token, non-admin token, workspace ID, campaign/session/player IDs, or deployment output to prove the real hosted environment.

Risk:
Local source checks cannot prove hosted CORS, cookie flags, CSRF behavior, account-token suppression, non-admin denial behavior, metrics exposure, Socket.IO deployment behavior, provider configuration, observability receipt, beta SLOs, or final operator signoff.

Recommended next step:

```bash
make hosted-rc-evidence HOSTED_RC_EVIDENCE_ARGS="--target-url <target-url> --auth-token <operator-token> --workspace-id <workspace-id> --non-admin-token <token> --campaign-id <campaign-id> --session-id <session-id> --player-id <player-id> --env-file <target-env>"
```

### Medium-High: Browser-token and cookie behavior still needs hosted confirmation

Current evidence:
- Static frontend inspection found the expected account/workspace token migration and cookie handling paths in `aidm_frontend/src/useRuntimeSettings.ts` and request header construction in `aidm_frontend/src/api.ts`.
- The local tests and source checks do not prove the browser receives final hosted cookie attributes or CSRF behavior from the deployed backend.

Risk:
A local-only token transport path can look correct while hosted SameSite, Secure, CSRF, CORS, or reverse-proxy behavior differs. This is especially important for account-token suppression and non-admin denial proof.

Recommended next step:

```bash
make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_ARGS="--target-url <target-url> --auth-token <operator-token> --non-admin-token <token>"
```

Use the repository's exact hosted evidence command if the hosted RC evidence wrapper has the required IDs available.

### Medium: Rendered frontend UX/accessibility proof was not refreshed

Current evidence:
- This run inspected frontend storage, cookie, DOM-injection, and dynamic-code surfaces statically, and `npm audit --omit=dev` found 0 production vulnerabilities.
- This run did not start the frontend, capture screenshots, run a browser flow, or inspect rendered interactive states.

Risk:
Static source inspection and backend/developer guardrails do not prove modal focus behavior, keyboard reachability, mobile layout, visual overlap, button sizing, screen-reader labels, or console cleanliness.

Recommended next step:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
make visual-smoke-review
```

## Larger Suggested Improvements

- Keep scanner allowlisting value-scoped. If future examples need to be ignored, prefer fake values that contain explicit placeholder markers instead of allowing entire source lines.
- Consider adding a compact regression test for each newly supported provider key format as provider integrations expand.
- Keep the committed-secret scanner redaction behavior from the prior audit in place. Failed scans should identify file, line, and finding kind without echoing credential material into logs.
- Keep source-only audit changes small until hosted/staging proof is available. The strongest current release-readiness improvement is still live hosted evidence, not broad local churn.
- Refresh rendered frontend smoke separately; static scans cannot replace visual and keyboard/focus validation.

## Recommended Next Run Focus

1. If hosted/staging credentials and IDs are available, run `make hosted-rc-evidence` and inspect the generated evidence.
2. If hosted proof remains unavailable, run frontend browser/visual smoke and `make visual-smoke-review`.
3. Re-run `make external-proof-values-check`, `make rc-finalize-signoff`, and `make release-checklist-status` only after real hosted proof values are present.
4. Preserve the existing uncommitted guardrail changes unless the user explicitly asks for a broader cleanup or commit.

# Daily AIDM Codebase Improvement Audit - 2026-06-21 16:04 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement follow-up across the already-dirty daily-audit guardrail checkout, the fallback automation memory, the latest 2026-06-21 06:02 MDT report, committed-secret scanner behavior, request JSON parsing guard behavior, state snapshot writer inventory guard, generated API type drift, source references around JSON parsing, browser token/storage handling, cookie/CSRF/CORS surfaces, subprocess usage, broad exception boundaries, ignored/generated artifacts, and frontend UX/accessibility proof gaps. The worktree already contained uncommitted audit changes in `improvements_suggestions.md`, `scripts/check_request_json_parsing.py`, `scripts/scan_secrets.py`, `tests/test_request_json_parsing_guard.py`, and `tests/test_secret_scan.py` at the start of this run. Those edits were preserved; this run only added syntax-error handling to the request JSON parsing guard, focused guard coverage, this dated report, and automation-memory updates.

## What Was Inspected

- Current physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Dirty-tree boundary with `git status --short`; existing uncommitted files matched prior audit guardrail work and were not reverted.
- Automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md` because `$CODEX_HOME` was unset in this shell.
- Latest top report in `improvements_suggestions.md`, especially:
  - 2026-06-21 06:02 MDT secret-scan output redaction.
  - 2026-06-20 16:01 MDT request JSON parsing guard AST hardening.
  - Carry-forward hosted/staging proof blockers.
- Request JSON parsing guard implementation and tests:
  - `scripts/check_request_json_parsing.py`
  - `tests/test_request_json_parsing_guard.py`
  - `aidm_server/validation.py`
- Secret scan implementation and tests already dirty from prior audit work:
  - `scripts/scan_secrets.py`
  - `tests/test_secret_scan.py`
- Existing lightweight release/developer guardrails:
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/generate_api_types.py --check`
  - `scripts/scan_secrets.py`
- Static source searches across backend, frontend, scripts, and tests for direct JSON parsing, dangerous DOM escape hatches, browser storage/cookie handling, subprocess usage, broad exception boundaries, ignored/generated artifacts, and UX/accessibility proof surfaces.

## Small Safe Fix Made

### Make the request JSON parsing guard fail cleanly on syntax errors

Affected files:
- `scripts/check_request_json_parsing.py`
- `tests/test_request_json_parsing_guard.py`
- `improvements_suggestions.md`

Problem:
The request JSON parsing guard now uses AST inspection, which correctly catches multiline `request.get_json(silent=True)` calls. However, if a scanned backend Python file was temporarily unparseable, `ast.parse(...)` would raise a raw `SyntaxError` traceback. That made the guard noisier and less actionable in local or CI output, especially during active route editing.

Change:
- Catch `SyntaxError` while scanning non-allowlisted backend Python files.
- Report the file, line number, syntax-error message, and source line as a normal guard violation.
- Preserve the existing direct-call detection and the existing CLI wording substring `Direct get_json(silent=True) usage found` so older test expectations and operator muscle memory still work.
- Added `test_find_violations_reports_syntax_errors_without_traceback` to prove the guard reports unparseable files without throwing.

Rationale:
This is a narrow developer-workflow and correctness guardrail hardening change. It changes no runtime request handling, auth behavior, database state, gameplay state mutation, provider configuration, frontend behavior, generated API contracts, or scanner matching policy. It only makes an existing safety check fail with a clear, bounded message instead of a Python traceback.

## Verification

- `./.venv/bin/python -m pytest tests/test_request_json_parsing_guard.py -q`
  - Passed: 5 tests.
- `./.venv/bin/python -m py_compile scripts/check_request_json_parsing.py tests/test_request_json_parsing_guard.py`
  - Passed.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python -m pytest tests/test_secret_scan.py -q`
  - Passed: 5 tests.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `git diff --check`
  - Passed.

## High-Priority Findings

### High: Hosted beta proof remains the main release blocker

Current evidence:
- Local source guardrails for committed-secret scanning, request JSON parsing, state snapshot writer inventory, generated API type drift, release evidence helpers, and external proof validation are present and passing locally.
- This run did not have a hosted/staging target URL, target env file, operator token, non-admin token, workspace ID, campaign/session/player IDs, or deployment output to prove the real hosted environment.

Risk:
Local source checks cannot prove hosted CORS, cookie flags, CSRF behavior, account-token suppression, non-admin denial behavior, metrics exposure, Socket.IO deployment behavior, provider configuration, observability receipt, beta SLOs, or final operator signoff.

Recommended next step:

```bash
make hosted-rc-evidence HOSTED_RC_EVIDENCE_ARGS="--target-url <target-url> --auth-token <operator-token> --workspace-id <workspace-id> --non-admin-token <token> --campaign-id <campaign-id> --session-id <session-id> --player-id <player-id> --env-file <target-env>"
```

### Medium-High: Final release confidence still depends on filled external proof values

Current evidence:
- External proof and signoff tooling exists, and the related local source checks remained compatible with this run's changes.
- This run did not execute final proof-value validation against real hosted artifacts or an operator signoff manifest.

Risk:
Generated release artifacts are not sufficient by themselves. Missing, placeholder, local-only, or unmerged values must remain separate from final hosted readiness.

Recommended next step:

```bash
make external-proof-values-check
make rc-finalize-signoff
make release-checklist-status
```

Run those only after hosted evidence values are filled or merged.

### Medium: Rendered frontend UX/accessibility proof was not refreshed

Current evidence:
- This run inspected frontend storage and UX proof surfaces statically, but did not start the frontend, capture screenshots, run a browser flow, or inspect rendered interactive states.

Risk:
Static source inspection and backend/developer guardrails do not prove modal focus behavior, keyboard reachability, mobile layout, visual overlap, button sizing, screen-reader labels, or console cleanliness.

Recommended next step:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
make visual-smoke-review
```

## Larger Suggested Improvements

- Keep `scripts/check_request_json_parsing.py` as a small release guardrail, but treat any reported syntax-error violation as a signal to fix the edited route before relying on JSON parser policy proof.
- Consider adding a compact CI artifact summary for request-JSON guard failures if the guard starts catching multiple files; the current single-script output is adequate for local runs.
- Keep the committed-secret scanner redaction from the prior audit in place. Failed secret scans should identify file, line, and finding kind without echoing credential material into logs.
- Keep source-only audit changes small until hosted/staging proof is available. The strongest current release-readiness improvement is still live hosted evidence, not broad local churn.
- Refresh rendered frontend smoke separately; static scans cannot replace visual and keyboard/focus validation.

## Recommended Next Run Focus

1. If hosted/staging credentials and IDs are available, run `make hosted-rc-evidence` and inspect the generated evidence.
2. If hosted proof remains unavailable, run frontend browser/visual smoke and `make visual-smoke-review`.
3. Re-run `make external-proof-values-check`, `make rc-finalize-signoff`, and `make release-checklist-status` only after real hosted proof values are present.
4. Preserve the existing uncommitted guardrail changes unless the user explicitly asks for a broader cleanup or commit.

# Daily AIDM Codebase Improvement Audit - 2026-06-21 06:02 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement pass across the already-dirty audit guardrail checkout, the automation memory fallback path, the latest 2026-06-20 reports, committed-secret scanner behavior, request JSON parsing guard behavior, state snapshot writer inventory guard, generated API type drift, source references around JSON parsing, browser-token storage, cookies/CSRF/CORS, subprocess usage, broad exception boundaries, and frontend UX/accessibility proof gaps. The worktree already contained uncommitted audit changes in `improvements_suggestions.md`, `scripts/check_request_json_parsing.py`, `scripts/scan_secrets.py`, `tests/test_request_json_parsing_guard.py`, and `tests/test_secret_scan.py` at the start of this run. Those edits were preserved; this run only added scanner output redaction, focused scanner coverage, this dated report, and automation-memory updates.

## What Was Inspected

- Current physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Dirty-tree boundary with `git status --short`; the existing uncommitted edits matched prior audit guardrail work and were not reverted.
- Automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md` because `$CODEX_HOME` was unset in this shell.
- Latest top report in `improvements_suggestions.md`, especially:
  - 2026-06-20 16:01 MDT request JSON parsing guard hardening.
  - 2026-06-20 06:03 MDT Google/Gemini API-key scanner coverage.
  - Carry-forward hosted/staging proof blockers.
- Secret scan implementation and tests:
  - `scripts/scan_secrets.py`
  - `tests/test_secret_scan.py`
  - Makefile/CI references that run `scripts/scan_secrets.py`.
- Request JSON parsing guard implementation and tests:
  - `scripts/check_request_json_parsing.py`
  - `tests/test_request_json_parsing_guard.py`
  - `aidm_server/validation.py`
- Existing lightweight release/developer guardrails:
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/generate_api_types.py --check`
  - `scripts/scan_secrets.py`
- Broad source search across backend, frontend, scripts, and tests for `request.get_json`, shared JSON parsing helpers, local/session storage, cookies, CSRF/CORS, dangerous DOM APIs, subprocess usage, broad exception boundaries, and UX/accessibility proof surfaces.

## Small Safe Fix Made

### Redact detected secret values from committed-secret scan findings

Affected files:
- `scripts/scan_secrets.py`
- `tests/test_secret_scan.py`
- `improvements_suggestions.md`

Problem:
The committed-secret scanner was already useful as a local, CI, and release guardrail, but when it found a likely secret it stored and printed `line.strip()[:160]` as the finding snippet. That meant a failed scan could echo the detected credential value back into terminal logs or CI logs while trying to prevent that same credential from being committed.

Change:
- Added `_redacted_snippet(line, secret_value)` to replace only the detected secret value with `<redacted>` before storing the finding snippet.
- Kept detection, allowlist checks, path skipping, line numbers, finding kinds, and file paths unchanged.
- Added `test_secret_scan_redacts_secret_values_from_findings` to prove the scanner still gives useful context while omitting the actual key value.

Rationale:
This is a narrow security/developer-workflow hardening change. It reduces accidental secret exposure in failed guardrail output without changing runtime behavior, auth, provider configuration, database state, gameplay state mutation, frontend behavior, generated API contracts, or the scanner's matching policy.

## Verification

- `./.venv/bin/python -m pytest tests/test_secret_scan.py -q`
  - Passed: 5 tests.
- `./.venv/bin/python -m py_compile scripts/scan_secrets.py tests/test_secret_scan.py`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python -m pytest tests/test_request_json_parsing_guard.py -q`
  - Passed: 4 tests.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `git diff --check`
  - Passed.

## High-Priority Findings

### High: Hosted beta proof remains the main release blocker

Current evidence:
- Local source guardrails for committed-secret scanning, request JSON parsing, state snapshot writer inventory, generated API type drift, release evidence helpers, and external proof validation are present and passing locally.
- This run did not have a hosted/staging target URL, target env file, operator token, non-admin token, workspace ID, campaign/session/player IDs, or deployment output to prove the real hosted environment.

Risk:
Local source checks cannot prove hosted CORS, cookie flags, CSRF behavior, account-token suppression, non-admin denial behavior, metrics exposure, Socket.IO deployment behavior, provider configuration, observability receipt, beta SLOs, or final operator signoff.

Recommended next step:

```bash
make hosted-rc-evidence HOSTED_RC_EVIDENCE_ARGS="--target-url <target-url> --auth-token <operator-token> --workspace-id <workspace-id> --non-admin-token <token> --campaign-id <campaign-id> --session-id <session-id> --player-id <player-id> --env-file <target-env>"
```

### Medium-High: Final release confidence still depends on filled external proof values

Current evidence:
- External proof and signoff tooling exists, and the related local source checks remained compatible with this run's changes.
- This run did not execute final proof-value validation against real hosted artifacts or an operator signoff manifest.

Risk:
Generated release artifacts are not sufficient by themselves. Missing, placeholder, local-only, or unmerged values must remain separate from final hosted readiness.

Recommended next step:

```bash
make external-proof-values-check
make rc-finalize-signoff
make release-checklist-status
```

Run those only after hosted evidence values are filled or merged.

### Medium: Rendered frontend UX/accessibility proof was not refreshed

Current evidence:
- This run inspected frontend token/storage and UX proof surfaces statically, but did not start the frontend, capture screenshots, run a browser flow, or inspect rendered interactive states.

Risk:
Static source inspection and backend/developer guardrails do not prove modal focus behavior, keyboard reachability, mobile layout, visual overlap, button sizing, screen-reader labels, or console cleanliness.

Recommended next step:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
make visual-smoke-review
```

## Larger Suggested Improvements

- Keep `scripts/scan_secrets.py` in CI and publish guardrails, but keep scanner output redacted by default. Failed checks should identify file, line, and kind without replaying secret material into logs.
- If provider support expands, add provider-specific token signatures with targeted tests rather than broad token regexes that create noisy false positives.
- Consider a future `--show-secret-context` escape hatch only if there is a real operator need; default output should remain redacted.
- Keep source-only audit changes small until hosted/staging proof is available. The strongest current release-readiness improvement is still live hosted evidence, not broad local churn.
- Refresh rendered frontend smoke separately; static scans cannot replace visual and keyboard/focus validation.

## Recommended Next Run Focus

1. If hosted/staging credentials and IDs are available, run `make hosted-rc-evidence` and inspect the generated evidence.
2. If hosted proof remains unavailable, run frontend browser/visual smoke and `make visual-smoke-review`.
3. Re-run `make external-proof-values-check`, `make rc-finalize-signoff`, and `make release-checklist-status` only after real hosted proof values are present.
4. Preserve the existing uncommitted guardrail changes unless the user explicitly asks for a broader cleanup or commit.

# Daily AIDM Codebase Improvement Audit - 2026-06-20 16:01 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement follow-up across the already-dirty checkout, automation memory fallback behavior, the prior 2026-06-20 secret-scan audit, request JSON parsing guard coverage, state snapshot writer inventory guard, generated API contract check, committed-secret scanning, and source references to direct JSON parsing. The worktree already contained the 2026-06-20 06:03 MDT report plus `scripts/scan_secrets.py` and `tests/test_secret_scan.py` changes at the start of this run. Those edits were preserved. This run only tightened the request JSON parsing guard, added focused coverage for that guard, prepended this report, and updated automation memory.

## What Was Inspected

- Current physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Dirty-tree boundary with `git status --short`; existing edits were limited to:
  - `improvements_suggestions.md`
  - `scripts/scan_secrets.py`
  - `tests/test_secret_scan.py`
- Automation memory location from `$CODEX_HOME/automations/daily-aidm-codebase-improvement-audit/memory.md`; the shell expansion did not find an existing file, so this run used the documented fallback path `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`.
- The 2026-06-20 06:03 MDT top report, especially the prior Google/Gemini API-key scanner hardening and the carry-forward release blockers.
- Request JSON parsing guard implementation and tests:
  - `scripts/check_request_json_parsing.py`
  - `tests/test_request_json_parsing_guard.py`
  - `aidm_server/validation.py`
- Direct JSON parsing references across backend, scripts, and tests with a source search for `get_json`, `parse_json_body`, and `parse_optional_json_body`.
- Existing lightweight release guardrails:
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/generate_api_types.py --check`
  - `scripts/scan_secrets.py`
- Existing secret-scan regression tests from the prior run, to verify this run did not disturb that uncommitted work.

## Small Safe Fix Made

### Detect multiline route-level `request.get_json(silent=True)` calls

Affected files:
- `scripts/check_request_json_parsing.py`
- `tests/test_request_json_parsing_guard.py`
- `improvements_suggestions.md`

Problem:
The request JSON parsing guard enforced the right policy, but it used a single-line regex. That caught compact route code like:

```python
payload = request.get_json(silent=True)
```

It did not catch an equivalent call split across lines:

```python
payload = request.get_json(
    silent=True,
)
```

That left a small bypass in a release guardrail whose purpose is to keep route handlers on the shared `parse_json_body` and `parse_optional_json_body` helpers.

Change:
- Replaced the line-oriented regex check with a small AST check for direct `request.get_json(silent=True)` calls.
- Kept the existing allowlist behavior for `aidm_server/validation.py` and explicit `--allow-path` values.
- Added `test_find_violations_reports_multiline_silent_json_parsing` to prove the multiline bypass is now detected with the route path and call-start line.

Rationale:
This is a narrow developer-workflow and correctness guardrail fix. It changes no runtime request handling, no auth behavior, no database state, no gameplay state mutation, no provider configuration, no generated API contracts, and no frontend behavior. It only makes an existing local/CI-style safety check harder to bypass accidentally.

## Verification

- `./.venv/bin/python -m pytest tests/test_request_json_parsing_guard.py -q`
  - Passed: 4 tests.
- `./.venv/bin/python -m py_compile scripts/check_request_json_parsing.py tests/test_request_json_parsing_guard.py`
  - Passed.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `./.venv/bin/python -m pytest tests/test_secret_scan.py -q`
  - Passed: 4 tests.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `git diff --check`
  - Passed.

## High-Priority Findings

### High: Hosted beta proof remains the main release blocker

Current evidence:
- Source-level guardrails for request JSON parsing, state snapshot writer inventory, generated API contract drift, committed-secret scanning, release evidence helpers, and external proof validation are present and passing locally.
- This run did not have a hosted/staging target URL, target env file, operator token, non-admin token, workspace ID, campaign/session/player IDs, or deployment output to prove the real hosted environment.

Risk:
Local source checks cannot prove hosted CORS, cookie flags, CSRF behavior, account-token suppression, non-admin denial behavior, metrics exposure, Socket.IO deployment behavior, provider configuration, observability receipt, beta SLOs, or final operator signoff.

Recommended next step:

```bash
make hosted-rc-evidence HOSTED_RC_EVIDENCE_ARGS="--target-url <target-url> --auth-token <operator-token> --workspace-id <workspace-id> --non-admin-token <token> --campaign-id <campaign-id> --session-id <session-id> --player-id <player-id> --env-file <target-env>"
```

### Medium-High: Final release confidence still depends on filled external proof values

Current evidence:
- The external proof/signoff tooling exists, and related source checks remain compatible with this run's changes.
- This run did not execute final proof-value validation against real hosted artifacts or an operator signoff manifest.

Risk:
Generated release artifacts are not sufficient by themselves. Missing, placeholder, local-only, or unmerged values must remain separate from final hosted readiness.

Recommended next step:

```bash
make external-proof-values-check
make rc-finalize-signoff
make release-checklist-status
```

Run those only after hosted evidence values are filled or merged.

### Medium: Rendered frontend UX/accessibility proof was not refreshed

Current evidence:
- This run focused on backend/developer guardrails and did not start a browser, capture screenshots, or inspect interactive frontend states.

Risk:
Static source inspection and backend guardrails do not prove modal focus behavior, keyboard reachability, mobile layout, visual overlap, button sizing, or console cleanliness.

Recommended next step:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
make visual-smoke-review
```

## Larger Suggested Improvements

- Keep the request JSON parsing guard in `dev-check` and RC checks; with the AST detector it now covers both compact and formatted call styles.
- Consider extending the guard later to flag route-level aliases only if such aliases appear in real code. Avoid speculative broad matching that could create noisy false positives.
- Keep the source-only audit cadence small. The codebase has useful local guardrails now, while the largest release-readiness gap remains hosted evidence.
- Once hosted credentials and IDs are available, prioritize end-to-end hosted proof over additional local-only guardrails.

## Recommended Next Run Focus

1. If hosted/staging credentials and IDs are available, run `make hosted-rc-evidence` and inspect the generated evidence.
2. If hosted proof is still unavailable, run frontend browser/visual smoke and `make visual-smoke-review`.
3. Re-run `make external-proof-values-check`, `make rc-finalize-signoff`, and `make release-checklist-status` after real hosted proof values are present.
4. Preserve the existing uncommitted secret-scan changes unless the user explicitly asks for a broader cleanup or commit.

# Daily AIDM Codebase Improvement Audit - 2026-06-20 06:03 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement pass across the clean checkout, automation memory, prior dated audit reports, request JSON parsing guard, state snapshot writer inventory guard, generated API contracts, release-evidence helper scripts, external proof/signoff tooling, local SLO baseline tooling, frontend token/storage references, and committed-secret scan coverage. The worktree was clean at the start of this run. This run only extended the local secret scanner, added focused coverage for that scanner, prepended this report, and updated automation memory.

## What Was Inspected

- Automation memory path `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`; it did not exist at the start of this run, so the run treated this as a fresh automation-memory seed.
- Current physical checkout path `/Users/danny/Developer/AIDM-main` with `pwd -P`.
- Dirty-tree boundary with `git status --short`; the checkout started clean.
- The 2026-06-19 top reports in `improvements_suggestions.md`, especially:
  - optional JSON parsing centralization;
  - state snapshot writer inventory hardening;
  - hosted/staging proof as the remaining high-priority release blocker.
- Existing guardrails:
  - `scripts/check_request_json_parsing.py`
  - `scripts/check_state_snapshot_writers.py`
  - `scripts/generate_api_types.py --check`
  - `scripts/scan_secrets.py`
- Release and external-proof tooling:
  - `scripts/check_external_proof_values.py`
  - `scripts/merge_external_proof_values.py`
  - `scripts/render_local_beta_slo_baseline.py`
  - `scripts/render_release_checklist_status.py`
  - related focused tests under `tests/`.
- Frontend token/storage scan surfaces:
  - `aidm_frontend/src/useRuntimeSettings.ts`
  - `aidm_frontend/src/api.ts`
  - `aidm_frontend/src/useTtsNarration.ts`
  - `aidm_frontend/src/SceneMusicPlayer.tsx`
  - `aidm_frontend/src/DiceRollDialog.tsx`
- Broad source searches for dangerous DOM APIs, local/session storage, cookies, direct JSON parsing, shell execution, broad exception handling, and release evidence JSON parsing.

## Small Safe Fix Made

### Add Google/Gemini API-key detection to the committed-secret scan

Affected files:
- `scripts/scan_secrets.py`
- `tests/test_secret_scan.py`
- `improvements_suggestions.md`

Problem:
The secret scanner already detected several high-risk committed secret shapes, including OpenAI-style keys, GitHub tokens, AWS access keys, Deepgram keys, and generic sensitive assignments. AIDM also uses Gemini through `GOOGLE_GENAI_API_KEY`, but the scanner did not have a dedicated Google API-key signature. That left a realistic provider key shape outside the fastest pre-publish guard.

Change:
- Added a `Google API key` pattern matching `AIza...` Google API-key shaped values.
- Added `test_secret_scan_detects_google_api_key` to prove the scanner flags a Gemini/Google-style key fixture.
- Verified the added fixture does not cause the repo-level scanner to flag its own test file.

Rationale:
This is a narrow security/developer-workflow hardening change. It improves the existing local guard used before publish and does not touch runtime provider configuration, app behavior, auth, database state, gameplay state mutation, frontend UX, generated API contracts, or deployment behavior.

## Verification

- `./.venv/bin/python -m pytest tests/test_secret_scan.py -q`
  - Passed: 4 tests.
- `./.venv/bin/python -m py_compile scripts/scan_secrets.py tests/test_secret_scan.py`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.

## High-Priority Findings

### High: Hosted beta proof remains the main release blocker

Current evidence:
- Source-level guardrails are increasingly strong: request JSON parsing, state snapshot writer inventory, generated API contract checks, committed-secret scanning, external proof value checks, hosted RC evidence scripts, local beta SLO baseline tooling, and release checklist/status tooling are all present.
- This run did not have a hosted/staging target URL, target env file, operator token, non-admin token, workspace ID, campaign/session/player IDs, or deployment output to prove the real hosted environment.

Risk:
The repo can prove a lot locally, but it still cannot prove hosted CORS, cookie flags, CSRF behavior, account-token suppression, non-admin denial behavior, metrics exposure, Socket.IO deployment behavior, provider configuration, observability receipt, beta SLOs, or final operator signoff until the hosted evidence suite runs against the real target.

Recommended next step:

```bash
make hosted-rc-evidence HOSTED_RC_EVIDENCE_ARGS="--target-url <target-url> --auth-token <operator-token> --workspace-id <workspace-id> --non-admin-token <token> --campaign-id <campaign-id> --session-id <session-id> --player-id <player-id> --env-file <target-env>"
```

### Medium-High: Final release confidence still depends on external proof values and operator signoff

Current evidence:
- The external proof tooling has strong local validation for missing values, persisted secrets, wrong GitHub Action repositories, missing local evidence files, local-only beta SLO evidence, and source archive attachment checks.
- This run inspected the tooling but did not run it against filled final external proof values.

Risk:
The release checklist can show that artifacts were generated, but final readiness still depends on real, filled values and an operator signoff manifest generated from those values. Empty, incomplete, or local-only evidence must not be treated as hosted release proof.

Recommended next step:

```bash
make external-proof-values-check
make rc-finalize-signoff
make release-checklist-status
```

Run those after hosted proof values have been filled or merged.

### Medium: Rendered frontend UX/accessibility proof was not refreshed in this run

Current evidence:
- Static scans did not reveal dangerous DOM API usage.
- This run did not start the frontend, run browser smoke, or inspect screenshots.

Risk:
Static checks do not prove modal focus behavior, keyboard reachability, mobile layout, visual overlap, button sizing, or console cleanliness.

Recommended next step:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
make visual-smoke-review
```

## Larger Suggested Improvements

- Keep `scripts/scan_secrets.py` in the publish guardrail path; the scanner now better matches the provider keys AIDM actually supports.
- Consider adding similarly specific signatures for any future provider keys if new providers are added, but avoid overly broad token regexes that create noisy false positives.
- Keep external-proof validation focused on evidence quality, not just artifact existence. Incomplete values should remain clearly separate from final hosted readiness.
- Continue to favor one small guardrail/test improvement per audit until hosted proof and signoff artifacts are available.

## Recommended Next Run Focus

1. If hosted/staging credentials and IDs are available, run `make hosted-rc-evidence` and inspect the generated evidence.
2. If hosted proof is still unavailable, run frontend browser/visual smoke and `make visual-smoke-review`.
3. Re-run `make external-proof-values-check` once real hosted proof values are filled or merged.
4. Keep source-only audits small; the major remaining gap is live hosted evidence, not broad code churn.

# Daily AIDM Codebase Improvement Audit - 2026-06-19 16:03 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: afternoon safe-improvement follow-up across the already-dirty checkout, automation memory, same-day report history, request JSON parsing guard, state snapshot writer inventory guard, RC/dev-check wiring, hosted beta evidence tooling, generated API contracts, and lightweight security/frontend scan surfaces. The worktree was already broadly dirty at the start of this run, including the 2026-06-19 06:04 MDT optional JSON parsing work plus untracked release-readiness scripts/docs/tests. Those changes were preserved. This run only tightened the state snapshot writer checker, added focused coverage for that checker, prepended this report, and updated automation memory.

## What Was Inspected

- Automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`, including the 2026-06-18 and 2026-06-19 morning audit entries.
- Current physical checkout path `/Users/danny/Developer/AIDM-main` and `git status --short`; the worktree remains a large mixed dirty tree with many modified and untracked backend, frontend, docs, scripts, workflow, requirements, and tests files.
- The existing 2026-06-19 06:04 MDT top report, especially the optional JSON parsing helper work and the updated priority assessment that hosted/staging evidence is now the main release blocker.
- Request JSON parsing guard work:
  - `scripts/check_request_json_parsing.py`
  - `tests/test_request_json_parsing_guard.py`
  - `Makefile` `request-json-parsing` and `dev-check` wiring
  - `scripts/closed_beta_rc_check.py` RC command plan wiring
- State snapshot writer inventory work:
  - `scripts/check_state_snapshot_writers.py`
  - `docs/state_snapshot_writer_inventory.md`
  - `tests/test_state_snapshot_writers.py`
  - `Makefile` `state-writers` and `dev-check` wiring
  - `scripts/closed_beta_rc_check.py` RC command plan wiring
- Hosted/release evidence surfaces:
  - `scripts/deployment_readiness_check.py`
  - `scripts/hosted_cookie_auth_smoke.py`
  - `scripts/security_forbidden_smoke.py`
  - `scripts/hosted_rc_evidence_check.py`
  - `scripts/render_release_evidence_packet.py`
  - `docs/production-readiness.md`
  - `docs/release_checklist.md`
  - `.github/workflows/closed-beta-rc.yml`
- Frontend/security scan targets using source searches for dangerous DOM APIs, token storage, cookies, CSRF markers, ARIA roles, keyboard handlers, and button usage.
- Generated API contract drift with `scripts/generate_api_types.py --check`.

## Small Safe Fix Made

### Detect destructured `Session.state_snapshot` assignments in the inventory guard

Affected files:
- `scripts/check_state_snapshot_writers.py`
- `tests/test_state_snapshot_writers.py`
- `improvements_suggestions.md`

Problem:
The state snapshot writer inventory checker now provides useful release safety, but its assignment target detector only recognized direct assignment targets such as:

```python
session.state_snapshot = safe_json_dumps(snapshot, {})
```

It did not recursively inspect tuple, list, or starred assignment targets. That meant a future write shaped like either of these could bypass the inventory even though it still writes `Session.state_snapshot`:

```python
session.state_snapshot, marker = value, True
[prefix, session.state_snapshot] = [None, value]
```

Change:
- Updated `_is_state_snapshot_target` to recursively inspect `ast.Tuple`, `ast.List`, and `ast.Starred` assignment targets.
- Added `test_state_snapshot_writer_scan_detects_destructured_assignment_targets`, which uses a temporary repo root and proves both tuple and list destructuring writes are detected with the expected path, scope, and line.

Rationale:
This is a guard-script hardening change, not a runtime behavior change. It reduces the chance that future direct snapshot writes escape the documented inventory, while avoiding any changes to gameplay state mutation, persistence, campaign-pack progress, frontend code, live runtime configuration, or deployment behavior.

## Verification

- `./.venv/bin/python -m pytest tests/test_state_snapshot_writers.py -q`
  - Passed: 4 tests.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 22 direct writes across 18 documented scopes.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py --print-current`
  - Passed and printed the current 22 detected writer locations for inspection.
- `./.venv/bin/python -m py_compile scripts/check_state_snapshot_writers.py tests/test_state_snapshot_writers.py`
  - Passed.
- `./.venv/bin/python scripts/check_request_json_parsing.py`
  - Passed: no direct `request.get_json(silent=True)` usage outside shared helpers.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.

## High-Priority Findings

### High: Hosted beta proof remains the main release blocker

Current evidence:
- Local/source gates are now much stronger: request JSON parsing guard, state writer inventory guard, hosted cookie-auth smoke, security forbidden smoke, deployment-readiness evidence generation, hosted RC evidence orchestration, visual-smoke review, Socket.IO worker-model decision checks, and release packet rendering are all present in the dirty tree.
- This run still did not have a hosted/staging target URL, target env file, account credentials/token, workspace ID, or deployment output to prove the actual hosted environment.

Risk:
The source tree is approaching a better RC gate story, but it still cannot prove hosted CORS, cookie flags, CSRF behavior, account-token suppression, metrics exposure, Socket.IO worker behavior, provider configuration, observability receipt, or beta SLOs until those checks run against the real target.

Recommended next step:
Run the hosted target evidence suite against the real beta/staging deployment:

```bash
make hosted-rc-evidence HOSTED_RC_EVIDENCE_ARGS="--target-url <target-url> --auth-token <operator-token> --workspace-id <workspace-id> --non-admin-token <token> --campaign-id <campaign-id> --session-id <session-id> --player-id <player-id> --env-file <target-env>"
```

### Medium-High: The dirty worktree is now the practical review risk

Current evidence:
- The checkout remains broadly dirty across backend, frontend, docs, scripts, tests, workflow configuration, requirements, and new release tooling.
- This run preserved all pre-existing dirty changes and only touched the state writer checker, its test, this report, and automation memory.

Risk:
A large mixed diff can hide small regressions and make it hard to decide what is actually ready to publish. The current tree appears to contain several coherent topics, but they should not be reviewed as one undifferentiated change set without a careful final pass.

Recommended next step:
Review or split the dirty tree by topic before commit/PR:

- request parsing and validation guards;
- state snapshot writer inventory and RC gates;
- hosted auth/deployment readiness evidence tooling;
- campaign-pack progress/import/linter changes;
- frontend beta runtime notes, support-bundle UI, and responsive CSS;
- dependency/workflow updates.

### Medium: Rendered UX/accessibility proof is still missing for dirty frontend changes

Current evidence:
- The morning run reported `cd aidm_frontend && npm run typecheck` passed.
- Static searches did not show dangerous DOM APIs in frontend source.
- The dirty tree includes substantial frontend and responsive CSS changes.
- This run did not start the dev server or run browser/visual smoke.

Risk:
TypeScript and source scans do not prove modal focus, keyboard reachability, mobile layout, visual overlap, button sizing, or console cleanliness.

Recommended next step:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
make visual-smoke-review
```

## Larger Suggested Improvements

- Keep `make request-json-parsing` and `make state-writers` in `dev-check` and the RC gate; both are now useful low-cost drift guards.
- Consider adding the request JSON parsing guard and state writer guard to any lightweight pre-PR checklist if the full RC gate is too expensive for daily work.
- Avoid a bulk migration of direct `Session.state_snapshot` writers. The inventory shows the current 22 writes are categorized; migrate only one ownership category at a time when the helper boundary is obvious.
- Generate a release evidence packet only after hosted/staging deployment-readiness, hosted cookie auth, security-forbidden smoke, export/import smoke, beta SLO evidence, and visual-smoke review are available.

## Recommended Next Run Focus

1. If hosted/staging credentials are available, run `make hosted-rc-evidence` against the real target and inspect the evidence report.
2. If no hosted target is available, run frontend browser/visual smoke and `make visual-smoke-review` for the dirty UI changes.
3. Review the broad dirty worktree by topic and decide what should be split before publish.
4. Keep audits focused on one small guard or regression at a time until the release-readiness worktree is stabilized.

# Daily AIDM Codebase Improvement Audit - 2026-06-19 06:04 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement pass across the already-dirty checkout, automation memory, recent report history, shared request-body validation helpers, optional campaign-pack import request parsing, campaign-pack import endpoint coverage, direct `Session.state_snapshot` writer ownership checks, hosted cookie-auth and deployment-readiness workflow surfaces, frontend storage/security/accessibility scan targets, generated API contracts, and lightweight backend/frontend developer gates. The worktree was already broadly dirty at the start of this run with prior validation-helper work, state-writer inventory work, hosted readiness scripts/docs, frontend runtime panels, and many tests in progress. Those changes were preserved. This run only made a small validation/helper refactor, wired two campaign-pack import routes through it, added focused tests, prepended this report, and updated automation memory.

## What Was Inspected

- Automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`, including the 2026-06-18 06:05 MDT and 16:05 MDT runs.
- Current physical checkout path `/Users/danny/Developer/AIDM-main` and `git status --short`; the working tree already contained many modified and untracked files before this run.
- Prior top-of-file report history in `improvements_suggestions.md`, especially the carried-forward concerns around direct session snapshot writers, hosted auth proof, deployment readiness, and generated resolver contracts.
- `aidm_server/validation.py` and direct validation helper tests in `tests/test_validation_helpers.py`.
- The only backend direct `request.get_json(silent=True)` callers outside the validation helper:
  - `aidm_server/blueprints/campaigns.py::import_example_campaign_pack`
  - `aidm_server/blueprints/campaigns.py::import_installed_campaign_pack`
- Campaign-pack import coverage in `tests/test_campaigns_endpoints.py`, including example-pack imports, installed-pack reimports, malformed request handling, dry-run behavior, and linter authoring-report assertions already present in the dirty tree.
- Newly present direct state snapshot writer guard:
  - `scripts/check_state_snapshot_writers.py`
  - `docs/state_snapshot_writer_inventory.md`
  - `tests/test_state_snapshot_writers.py`
  - `Makefile` and `scripts/closed_beta_rc_check.py` integration.
- Hosted auth and release-readiness workflow surfaces:
  - `scripts/hosted_cookie_auth_smoke.py`
  - `scripts/deployment_readiness_check.py`
  - `scripts/security_forbidden_smoke.py`
  - `scripts/render_release_evidence_packet.py`
  - `docs/auth_modes.md`
  - `docs/production-readiness.md`
  - `docs/release_checklist.md`
  - `.github/workflows/closed-beta-rc.yml`
- Frontend token, storage, CSRF, and accessibility-related surfaces with searches for `dangerouslySetInnerHTML`, `innerHTML`, `eval`, `new Function`, `localStorage`, `sessionStorage`, `document.cookie`, `authToken`, `csrf`, ARIA attributes, roles, keyboard handlers, and button usage.
- Generated API contract consistency via `scripts/generate_api_types.py --check`.
- Developer workflow gates in `Makefile`, `aidm_frontend/package.json`, and the closed-beta/deployment readiness tests.

## Small Safe Fix Made

### Centralize optional JSON-object request parsing

Affected files:
- `aidm_server/validation.py`
- `aidm_server/blueprints/campaigns.py`
- `tests/test_validation_helpers.py`
- `tests/test_campaigns_endpoints.py`
- `improvements_suggestions.md`

Problem:
Required JSON endpoints now use `parse_json_body`, but the two optional-body campaign-pack import routes still duplicated the lower-level Flask parsing expression `request.get_json(silent=True) if request.is_json else {}`. The duplicated code was small, but it carried a subtle contract that matters for imports:

- no JSON body is allowed and should behave like `{}`;
- malformed JSON with `Content-Type: application/json` must still return a validation error;
- JSON arrays, scalars, and `null` must not flow into route code as object payloads.

Change:
- Added `parse_optional_json_body(request)` to `aidm_server/validation.py`.
- Kept `parse_json_body(request)` as the required-body parser.
- Replaced the duplicated optional parser in:
  - `import_example_campaign_pack`
  - `import_installed_campaign_pack`
- Added direct helper tests proving the difference between required and optional parsing:
  - required parser returns `None` for absent, malformed, or non-object bodies;
  - optional parser returns `{}` for omitted/non-JSON optional bodies;
  - optional parser returns `None` for malformed or non-object JSON bodies.
- Added an endpoint regression proving malformed optional JSON is rejected for example campaign-pack import.

Rationale:
This is a small maintainability and correctness guard. It removes the last direct route-level `request.get_json(silent=True)` callers from backend blueprints, centralizes the optional-body contract, and preserves campaign-pack import behavior. It does not change campaign-pack import semantics, persistence, auth, state mutation, generated API contracts, frontend behavior, provider behavior, live runtime configuration, or deployment workflow behavior.

## Verification

- `./.venv/bin/python -m pytest tests/test_validation_helpers.py tests/test_campaigns_endpoints.py::test_import_example_campaign_pack_rejects_malformed_optional_json tests/test_campaigns_endpoints.py::test_import_example_campaign_pack_creates_playable_campaign tests/test_campaigns_endpoints.py::test_import_road_of_unremembered_kings_example_pack_dry_run tests/test_campaigns_endpoints.py::test_installed_campaign_pack_library_lists_details_and_imports -q`
  - Passed: 8 tests.
- `./.venv/bin/python -m py_compile aidm_server/validation.py aidm_server/blueprints/campaigns.py tests/test_validation_helpers.py tests/test_campaigns_endpoints.py`
  - Passed.
- `rg -n "request\\.get_json\\(silent=True\\)|get_json\\(silent=True\\) if request\\.is_json" aidm_server`
  - Passed inspection: only the shared validation helper now calls `request.get_json(silent=True)`.
- `./.venv/bin/python scripts/check_state_snapshot_writers.py`
  - Passed: inventory matches 19 direct writes across 17 documented scopes.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `git diff --check -- aidm_server/validation.py aidm_server/blueprints/campaigns.py tests/test_validation_helpers.py tests/test_campaigns_endpoints.py improvements_suggestions.md`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.
- `cd aidm_frontend && npm run typecheck`
  - Passed.
- `./.venv/bin/python -m pytest tests/test_state_snapshot_writers.py tests/test_closed_beta_rc_check.py tests/test_deployment_readiness_check.py -q`
  - Passed: 21 tests.

## High-Priority Findings

### High: Hosted beta proof is now the main release blocker

Current evidence:
- Source-level hosted readiness work is now much stronger than the previous reports indicated:
  - `make deployment-readiness` exists.
  - `make hosted-cookie-auth-smoke` exists.
  - `make local-beta-slo-baseline` and `make beta-slo-baseline` exist.
  - `scripts/render_release_evidence_packet.py` and `.github/workflows/closed-beta-rc.yml` can collect evidence artifacts.
  - docs now describe cookie-only hosted auth, CSRF, Socket.IO worker-model proof, migration-chain drills, state-writer inventory, support bundles, SLO baselines, and RC evidence packets.
- This automation run did not have a hosted target URL, hosted env file, account credentials/token, workspace ID, or deployment output to prove the live target.

Risk:
The local source gates are meaningful, but they still cannot prove the actual hosted/staging deployment's CORS, cookie flags, CSRF behavior, account-token response suppression, metrics exposure, Socket.IO worker behavior, provider configuration, or beta SLO state.

Recommended next step:
Run the hosted target evidence commands against the real beta/staging URL and attach the generated reports:

```bash
make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> --target-url <target-url> --auth-token <token> --evidence-report tmp/release/deployment-readiness-evidence.md"
make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_SMOKE_ARGS="--target-url <target-url> --account-intent signup --evidence-report tmp/release/hosted-cookie-auth-evidence.md"
make beta-slo-baseline BETA_SLO_BASELINE_ARGS="--target-url <target-url> --auth-token <token> --workspace-id <workspace-id> --release RC1 --environment staging"
```

### Medium-High: State snapshot writers are inventoried, but direct-write categories still need disciplined migration

Current evidence:
- The previous high-priority gap is no longer "no inventory": `scripts/check_state_snapshot_writers.py` passes and `docs/state_snapshot_writer_inventory.md` classifies every detected direct write.
- The inventory currently documents 19 direct writes across 17 scopes.
- Categories include central runtime persistence, serialized turn pipeline writes, campaign-pack serialized writes, initialization/import, lifecycle metadata, projection refresh, runtime control, and regression fixtures.

Risk:
The inventory is a major safety improvement, but direct write categories still need continued discipline. Some categories are intentionally direct today, especially turn-pipeline no-change persistence, campaign-pack progress mirroring, and turn-control persistence. If future edits add writes without the checker or expand a category beyond its intended ownership boundary, state revision/audit semantics could drift again.

Recommended next step:
Keep the checker in RC/dev gates and migrate one live-runtime category at a time only when there is a clear helper boundary. Start with the narrowest candidate: a named helper for turn-control or no-change turn-pipeline persistence, not a broad rewrite.

### Medium-High: The dirty worktree is now a release-management risk

Current evidence:
- The checkout started this run with roughly 50 modified tracked files and many untracked files across backend, frontend, docs, scripts, tests, requirements, and GitHub workflow configuration.
- The dirty tree includes important release machinery such as hosted auth smoke, deployment readiness, migration drills, SLO rendering, RC evidence packet rendering, state-writer inventory, Socket.IO worker-model checks, frontend runtime notes, and campaign-pack import UX/tests.
- This run preserved all pre-existing dirty changes and only added a narrow validation helper change.

Risk:
The changes may be internally coherent, but the current worktree is too broad to reason about as one release unit without a final pass. A small unrelated regression could hide inside the large mixed diff, and review will be harder if release workflow, docs, backend behavior, frontend UI, dependency pins, and tests are all shipped together without staging.

Recommended next step:
Before publishing, split or at least review the dirty tree by topic:

- state-writer inventory and RC workflow gates;
- hosted auth/deployment readiness evidence tooling;
- campaign-pack progress/import/linter changes;
- frontend beta runtime and support-bundle UI;
- dependency/workflow updates.

### Medium: Frontend source checks passed, but rendered UX/accessibility proof is still missing for the dirty UI changes

Current evidence:
- `cd aidm_frontend && npm run typecheck` passed.
- Static searches found no `dangerouslySetInnerHTML`, no direct `innerHTML`, and no `eval`/`new Function` usage in frontend source.
- The dirty tree includes substantial frontend changes in `App.tsx`, `SessionBoard.tsx`, `InspectorPanel.tsx`, `BetaIncidentPanel.tsx`, `CampaignPackImportDialog.tsx`, `BetaRuntimeNotesPanel.tsx`, and responsive/style files.
- This run did not start the dev server or run browser/visual smoke.

Risk:
TypeScript can prove structure, but not mobile layout, keyboard reachability, focus order, visual overlap, modal behavior, or live browser console cleanliness.

Recommended next step:
Run the frontend browser and visual smoke gates after the dirty UI set is ready:

```bash
cd aidm_frontend && npm run smoke:browser
cd aidm_frontend && npm run smoke:visual
```

## Larger Suggested Improvements

- Treat hosted/staging evidence as the next release-critical milestone; the local checks are now strong enough that live target proof is the bigger unknown.
- Keep `parse_json_body` and `parse_optional_json_body` as the only low-level request JSON parsing entry points for backend blueprints.
- Continue using the state snapshot writer inventory as a guardrail, but do not migrate direct writers in bulk. Move one ownership category only when the helper name and audit semantics are obvious.
- Add a small CI or dev-check assertion for direct route-level `request.get_json(silent=True)` if the project wants to prevent blueprints from bypassing validation helpers again.
- Run browser/visual smoke before treating the frontend runtime notes, support bundle, campaign-pack import UI, and responsive CSS changes as release-ready.
- Use the release evidence packet as the handoff artifact once hosted deployment-readiness, hosted cookie auth, security-forbidden smoke, export/import smoke, and beta SLO evidence are available.

## Recommended Next Run Focus

1. Run or inspect hosted/staging deployment-readiness evidence if a target URL and credentials are available.
2. Run frontend browser/visual smoke for the current dirty UI changes.
3. Review the dirty tree by topic and identify whether it should be split before commit/PR.
4. Consider adding a tiny guard against direct route-level `request.get_json(silent=True)` usage outside `aidm_server/validation.py`.

# Daily AIDM Codebase Improvement Audit - 2026-06-18 16:02 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: follow-up safe-improvement pass across the current dirty checkout, automation memory, recent report history, player/admin creature resolver response boundaries, response DTO tests, shared validation helper work already present from the prior run, direct session snapshot writers, hosted auth/session storage surfaces, deployment-readiness workflow, frontend security/accessibility scan targets, generated API contracts, and lightweight developer workflow checks. The worktree was already dirty at the start of this run with the 2026-06-18 06:05 MDT validation-helper/report changes in progress; those changes were preserved. This run only changed the creature resolver response helper, response DTO tests, this report, and automation memory.

## What Was Inspected

- Automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`; the shell had `CODEX_HOME` unset, so the concrete Codex home path was used directly.
- Current git status and diffs to avoid overwriting unrelated or prior-run work:
  - `aidm_server/validation.py`
  - `tests/test_validation_helpers.py`
  - `improvements_suggestions.md`
- The 2026-06-18 06:05 MDT report section, including its direct `parse_json_body` regression coverage and carried-forward findings.
- `aidm_server/blueprints/creatures.py`, especially `_creature_resolution_response`, `/api/creatures/resolve`, `debug_read` capability checks, and bestiary authoring permission gates.
- `aidm_server/capabilities.py` to confirm non-admin account requests only receive player capabilities while workspace admins receive `debug_read`.
- Existing player/admin bestiary route coverage in `tests/test_auth.py::test_bestiary_authoring_endpoints_require_workspace_admin_account`.
- Existing response DTO tests in `tests/test_response_dtos.py`.
- Generated resolver result contract definitions in `aidm_server/api_type_contract.py` and `aidm_frontend/src/apiContract.generated.ts`, which still model the full admin-capable `CreatureResolutionResult` shape with optional `debug`.
- Direct session snapshot persistence via `rg -n "\.state_snapshot\s*=|mutate_session_state\(" aidm_server tests`, confirming the writer-inventory work remains unresolved.
- Frontend/backend security and accessibility scan targets with searches for `dangerouslySetInnerHTML`, `innerHTML`, `eval`, `new Function`, `localStorage`, `sessionStorage`, CSRF, account token transport, listbox roles, and button types.
- Hosted auth/session-storage documentation and checks in `docs/beta_runbook.md`, `docs/production-readiness.md`, `docs/release_checklist.md`, `aidm_frontend/src/useRuntimeSettings.ts`, and `scripts/deployment_readiness_check.py`.
- Developer workflow gates: focused pytest, Python compilation, generated API type check, diff whitespace check, and committed-secret scan.

## Small Safe Fix Made

### Default-deny player creature resolver response fields

Affected files:
- `aidm_server/blueprints/creatures.py`
- `tests/test_response_dtos.py`
- `improvements_suggestions.md`

Problem:
The player-facing creature resolver preview previously returned a shallow copy of the resolver result with only `debug` removed. That fixed the known debug leakage, but it was not a durable boundary: any future operator-only field added to `resolve_creature_for_encounter` would become player-visible by default unless each new field remembered to update the response scrubber.

Change:
- Added `_PUBLIC_CREATURE_RESOLUTION_FIELDS` in `aidm_server/blueprints/creatures.py` for the explicit player-visible resolver response keys:
  - `creature`
  - `source`
  - `resolutionMethod`
  - `matchScore`
  - `generated`
  - `savedToBestiary`
  - `notes`
- Updated `_creature_resolution_response` so actors without `debug_read` receive only those fields.
- Preserved admin/debug behavior: actors with `debug_read` still receive the full resolver result, including `debug` and any operator-only diagnostics.
- Added `tests/test_response_dtos.py::test_player_creature_resolution_response_is_public_allowlist`, proving an unexpected `operatorOnly` field is removed from player responses and retained for debug-capable actors.

Rationale:
This is a narrow response-boundary hardening fix. It does not change resolver selection, generation, persistence, bestiary writes, auth decisions, combat state, frontend code, or generated contracts. It turns the existing "remove known sensitive field" behavior into a safer allowlist for non-debug actors.

## Verification

- `./.venv/bin/python -m pytest tests/test_response_dtos.py::test_player_creature_resolution_response_is_public_allowlist tests/test_auth.py::test_bestiary_authoring_endpoints_require_workspace_admin_account -q`
  - Passed: 2 tests.
- `./.venv/bin/python -m pytest tests/test_response_dtos.py tests/test_validation_helpers.py -q`
  - Passed: 5 tests.
- `./.venv/bin/python -m py_compile aidm_server/blueprints/creatures.py aidm_server/validation.py tests/test_response_dtos.py tests/test_validation_helpers.py`
  - Passed.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `git diff --check -- aidm_server/blueprints/creatures.py aidm_server/validation.py tests/test_response_dtos.py tests/test_validation_helpers.py improvements_suggestions.md`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py aidm_server/blueprints/creatures.py aidm_server/validation.py tests/test_response_dtos.py tests/test_validation_helpers.py improvements_suggestions.md`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.

## High-Priority Findings

### High: Direct session snapshot writers still need explicit ownership categories

Current evidence:
- The central mutation path remains `aidm_server/services/session_state_mutation.py::mutate_session_state`.
- Fresh scan still found direct runtime/service assignments in:
  - `aidm_server/blueprints/sessions.py`
  - `aidm_server/canon_projection.py`
  - `aidm_server/game_state/application/applier.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`
  - `aidm_server/services/campaign_pack.py`
  - `aidm_server/services/campaign_pack_progress.py`
  - `aidm_server/services/campaign_pack_storage.py`
  - `aidm_server/services/session_import.py`
  - `aidm_server/services/session_lifecycle.py`
  - `aidm_server/turn_control.py`

Risk:
Initialization, import, cleanup, projection refresh, campaign-pack progress, and live gameplay mutation still appear as similar direct JSON-column writes at the persistence boundary. That keeps revision semantics, audit coverage, lock ordering, and conflict behavior hard to audit.

Recommended next step:
Create a checked writer inventory and classify each direct assignment as initialization, import, cleanup, projection, campaign-pack progression, live gameplay mutation, repair, or derived persistence. Migrate one clearly live mutation/repair category at a time through `mutate_session_state` or an intentionally named internal variant.

### Medium-High: Hosted account/session storage still needs browser-flow proof

Current evidence:
- Docs and readiness checks describe hosted same-origin cookie auth, `AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=true`, `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false`, and CSRF via `X-AIDM-CSRF-Token`.
- The frontend still has local/session storage paths for local/private operator workflows and account/workspace remembered state.
- This run did not have a hosted target, env file, or auth token to prove the browser flow end to end.

Risk:
The source-level controls look intentional, but hosted beta still needs proof that browser-readable account tokens are disabled, unsafe cookie-authenticated writes include CSRF, logout clears account/capability state, workspace switching refreshes roles, and stale admin UI cannot survive a downgrade.

Recommended next step:
Add or run a hosted-mode Playwright/browser regression around login, `/api/accounts/session`, `/api/accounts/me`, logout, workspace switch/select, CSRF on unsafe writes, and stale `isWorkspaceAdmin` display.

### Medium-High: Deployment-readiness checks still need live beta/staging evidence

Current evidence:
- `scripts/deployment_readiness_check.py`, `Makefile`, `docs/production-readiness.md`, and `docs/release_checklist.md` define the live-target readiness workflow.
- This automation run only performed local source/test checks and did not have beta/staging deployment inputs.

Risk:
Local checks cannot prove hosted CORS, cookie flags, security headers, metrics exposure, Socket.IO worker behavior, live auth exceptions, or actual provider configuration.

Recommended next step:
Run `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> --target-url <target-url> --auth-token <token>"` against the real beta target and record the output in the release checklist or beta runbook.

### Medium: Player resolver contract is safer, but generated types still describe the admin-capable shape

Current evidence:
- This run added a non-debug allowlist at the route response boundary.
- `CreatureResolutionResult` in the generated API contract still includes optional `debug` because admins can still receive the full resolver result.

Risk:
The main player leakage risk is now reduced at the route boundary. The remaining maintainability risk is documentation/type clarity: frontend or future route code may not immediately distinguish admin-capable resolver payloads from player-visible resolver previews.

Recommended next step:
Consider adding a named `PublicCreatureResolutionResult` contract or endpoint-specific response type if the generated contract machinery can express role-specific payloads without a large refactor.

## Larger Suggested Improvements

- Build the direct `state_snapshot` writer inventory as a small checked artifact, then migrate one ownership category per pass.
- Add hosted-mode browser coverage for cookie-only account auth, CSRF, logout cleanup, workspace switching, and role downgrade behavior.
- Attach the next real deployment-readiness output to `docs/release_checklist.md` or `docs/beta_runbook.md`.
- If generated contracts can support it cleanly, split admin-capable resolver results from player resolver previews.
- Revisit the custom listbox-style campaign-pack picker for full keyboard semantics or simplify it to a native/select-like pattern.
- Keep optional cleanup of ignored `.DS_Store`, `.pytest_cache`, and `__pycache__` artifacts as local maintenance, not as source churn.

## Recommended Next Run Focus

1. Create the direct `state_snapshot` writer inventory and classify each writer by ownership category.
2. Add hosted auth browser-flow proof for cookie auth, CSRF, logout, workspace switching, and stale role clearing.
3. Run deployment readiness against the real beta/staging target when target URL, env file, and token are available.
4. Evaluate whether a role-specific generated resolver preview type is worth the contract churn.

# Daily AIDM Codebase Improvement Audit - 2026-06-18 06:05 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement pass across the clean checkout, prior audit memory, current report history, request validation helpers, endpoint JSON parsing, session snapshot mutation ownership, hosted account/session storage, deployment-readiness workflow, frontend forms/accessibility surfaces, generated contracts, ignored local artifacts, and lightweight developer workflow checks. The worktree was clean at the start of this run. This run only changed shared validation helper documentation, added direct helper regression coverage, and prepended this report.

## What Was Inspected

- Automation memory path `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`; it did not exist for this automation ID, so this run created the first memory note after completion.
- Current git status and branch state to avoid overwriting unrelated work: `main...origin/main` with no dirty tracked files at start.
- Top-of-file report history in `improvements_suggestions.md`, especially the 2026-06-17 16:02 MDT shared request-body hardening and the 2026-06-17 06:02 MDT LLM stream fallback telemetry fix.
- Shared validation helpers in `aidm_server/validation.py`, including `parse_json_body`, `missing_fields`, `coerce_bool`, `positive_int`, and `json_object`.
- Route request-body parsing across backend blueprints:
  - Most mutating routes now use `parse_json_body(request)`.
  - The two remaining direct `request.get_json(silent=True)` callers are campaign-pack import endpoints that intentionally allow an omitted body while rejecting malformed or non-object JSON.
- Existing route-level validation tests in `tests/test_worlds_endpoints.py`, `tests/test_maps_endpoints.py`, `tests/test_rules_and_segments.py`, and `tests/test_tts_endpoints.py`.
- Direct session snapshot persistence via `rg "\.state_snapshot\s*=" aidm_server`, which still finds runtime/service writers in session routes, canon projection, the state applier, the turn pipeline, campaign-pack services, session import/lifecycle, and turn control.
- The centralized mutation path in `aidm_server/services/session_state_mutation.py`, including audit stamping, revision handling, and lock coordination.
- Hosted auth/session storage in `aidm_frontend/src/useRuntimeSettings.ts` and its tests, including `sessionStorage`, `localStorage`, `accountTokenTransport`, HTTP-only-cookie mode, workspace switching, logout cleanup, and remembered-account refresh.
- Frontend form and accessibility surfaces in `App.tsx`, `WorldDialogs.tsx`, `CreateCampaignDialog.tsx`, `PlayerEditDialog.tsx`, `CampaignPackImportDialog.tsx`, `InspectorPanel.tsx`, `SessionBoard.tsx`, and `ActionComposer.tsx`, with special attention to modal semantics, button types inside forms, tab roles, labels, and custom picker controls.
- Security-sensitive frontend/backend sinks and token surfaces with searches for `dangerouslySetInnerHTML`, `innerHTML`, `eval`, `new Function`, cookies, bearer tokens, API keys, and secret-like strings.
- Generated API contract sources in `aidm_server/api_type_contract.py` and `aidm_frontend/src/apiContract.generated.ts`.
- Developer workflow gates in `Makefile`, `scripts/closed_beta_rc_check.py`, `scripts/deployment_readiness_check.py`, `scripts/generate_api_types.py`, `scripts/scan_secrets.py`, frontend `package.json`, `docs/production-readiness.md`, and `docs/release_checklist.md`.
- Ignored local artifacts such as `.DS_Store`, `.pytest_cache`, and `__pycache__`; `.gitignore` already covers them, so this run did not delete local machine artifacts.

## Small Safe Fix Made

### Add direct regression coverage for object-only JSON body parsing

Affected files:
- `aidm_server/validation.py`
- `tests/test_validation_helpers.py`
- `improvements_suggestions.md`

Problem:
The shared `parse_json_body` helper now has an important object-only contract after the previous validation hardening, but that behavior was only protected indirectly through endpoint tests. A later edit could accidentally allow arrays, scalars, malformed JSON, or `null` through the helper and reintroduce route-level `.get(...)` crashes.

Change:
- Added a concise docstring to `parse_json_body` documenting that it returns only JSON object bodies.
- Added direct helper tests covering:
  - valid JSON object payloads,
  - non-JSON request bodies,
  - malformed JSON,
  - JSON `null`,
  - JSON arrays,
  - JSON string scalars.

Rationale:
This is a low-risk maintainability and correctness guard. It does not change runtime behavior, route behavior, provider behavior, persistence, auth, state mutation, frontend behavior, or generated contracts. It makes the helper contract explicit and easy to verify independently of any single endpoint.

## Verification

- `./.venv/bin/python -m pytest tests/test_validation_helpers.py -q`
  - Passed: 2 tests.
- `./.venv/bin/python -m pytest tests/test_validation_helpers.py tests/test_worlds_endpoints.py::test_create_world_validates_request_body_and_fields tests/test_maps_endpoints.py::test_create_map_validates_request_body_and_fields tests/test_tts_endpoints.py::test_llm_config_rejects_ambiguous_persist_boolean -q`
  - Passed: 5 tests.
- `./.venv/bin/python -m py_compile aidm_server/validation.py tests/test_validation_helpers.py`
  - Passed.
- `./.venv/bin/python -m py_compile scripts/deployment_readiness_check.py`
  - Passed.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `git diff --check -- aidm_server/validation.py tests/test_validation_helpers.py improvements_suggestions.md`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py aidm_server/validation.py tests/test_validation_helpers.py improvements_suggestions.md`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python scripts/scan_secrets.py`
  - Passed: no likely committed secrets found.

## High-Priority Findings

### High: Direct session snapshot writers still need explicit ownership categories

Current evidence:
- `aidm_server/services/session_state_mutation.py` provides the central locked, revision-aware, audit-stamped mutation path.
- Fresh scan still found direct runtime/service assignments in:
  - `aidm_server/blueprints/sessions.py`
  - `aidm_server/canon_projection.py`
  - `aidm_server/game_state/application/applier.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`
  - `aidm_server/services/campaign_pack.py`
  - `aidm_server/services/campaign_pack_progress.py`
  - `aidm_server/services/campaign_pack_storage.py`
  - `aidm_server/services/session_import.py`
  - `aidm_server/services/session_lifecycle.py`
  - `aidm_server/turn_control.py`

Risk:
Some direct writes are legitimate initialization, import, cleanup, projection, or server-owned application paths. The unresolved risk is that live gameplay mutation, repair, campaign-pack progression, projection refresh, and lifecycle cleanup still look identical at the persistence boundary. That makes lock ordering, state revision semantics, audit visibility, and conflict handling harder to reason about.

Recommended next step:
Create a checked writer inventory with one row per direct assignment and classify each writer as initialization, import, cleanup, projection, live gameplay mutation, repair, or derived persistence. Then migrate one clearly live mutation or repair path at a time through `mutate_session_state` or a named server-internal equivalent.

### Medium-High: Hosted account/session storage still needs browser-flow proof

Current evidence:
- `aidm_frontend/src/useRuntimeSettings.ts` still stores account/workspace state in browser storage while also supporting HTTP-only-cookie account transport.
- `aidm_frontend/src/useRuntimeSettings.test.tsx` coverage is strong for remembered accounts, HTTP-only-cookie mode, saved workspace selection, workspace deletion/removal, logout cleanup, and role refresh.
- `scripts/deployment_readiness_check.py` expects hosted browser auth to disable account-token responses unless an explicit storage exception is documented.

Risk:
The local/private operator mode remains useful, but hosted beta still needs browser-flow evidence that browser-readable account tokens are disabled, CSRF is used for cookie-authenticated writes, logout clears account/capability state, workspace switching refreshes role state, and stale admin UI cannot persist after a downgrade.

Recommended next step:
Add or run a hosted-mode Playwright/browser regression around login, `/api/accounts/session`, `/api/accounts/me`, logout, workspace switch/select, and stale `isWorkspaceAdmin` display. Keep local unauthenticated operator mode separate from hosted beta expectations.

### Medium-High: Deployment-readiness checks still need live beta/staging evidence

Current evidence:
- `scripts/deployment_readiness_check.py` validates production env choices, auth requirements, CORS, secure cookie settings, account-token response policy, Socket.IO staging proof, `/api/health`, metrics, Prometheus output, security headers, and fallback-provider status.
- `Makefile` exposes `deployment-readiness`.
- `docs/production-readiness.md` and `docs/release_checklist.md` point to the live target command, but this automation run did not have a real beta/staging URL, env file, or auth token.

Risk:
Local source checks cannot prove hosted cookie flags, CORS, security headers, metrics exposure, Socket.IO worker behavior, auth exceptions, or provider configuration in the actual deployment target.

Recommended next step:
Run `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> --target-url <target-url> --auth-token <token>"` against the real beta target and save the result in `docs/release_checklist.md` or `docs/beta_runbook.md`.

### Medium: Player resolver preview should still move to an explicit player DTO

Current evidence:
- Prior work removed resolver `debug` from non-`debug_read` responses.
- The shared `CreatureResolutionResult` contract still includes optional `debug`, and there is not yet a dedicated player-facing resolver response type.

Risk:
The known debug leakage is closed, but a later operator-only resolver field could be added to the shared result and become player-visible unless the player preview response is allowlisted by design.

Recommended next step:
Define a dedicated player preview DTO or reusable allowlist sanitizer. Add a regression that compares player/admin resolver payload keys and fails if debug rankings, model names, normalized request metadata, hidden candidate identifiers, or other operator-only fields reappear in player responses.

## Larger Suggested Improvements

- Build the direct `state_snapshot` writer inventory as a small markdown or checked JSON artifact, then migrate one ownership category at a time.
- Add hosted-mode browser coverage for cookie-only account auth, CSRF, logout cleanup, workspace switching, and role downgrade behavior.
- Attach the next real deployment-readiness output to the release checklist or beta runbook so hosted readiness is evidence-backed.
- Add a player-specific resolver DTO/allowlist and generated-contract coverage so operator-only resolver fields cannot drift into player previews.
- Consider a small shared helper for optional JSON object bodies if campaign-pack import keeps accepting omitted request bodies. The current direct callers are guarded, but a named helper would make the distinction between "required JSON object" and "optional JSON object" harder to miss.
- Improve custom picker keyboard behavior where controls use rich `role="listbox"`/`role="option"` markup. Either implement arrow-key roving focus and active-descendant semantics, or use a simpler native/select-like pattern for fully predictable accessibility.
- Keep ignored local `.DS_Store`, `.pytest_cache`, and `__pycache__` cleanup as an optional local-maintenance task rather than a source change. The files are already ignored and were not tracked.

## Recommended Next Run Focus

1. Create the direct `state_snapshot` writer inventory and classify each writer by ownership category.
2. Add the player resolver preview DTO/allowlist on top of the existing debug stripping.
3. Add or run hosted auth browser proof for logout, workspace switch, CSRF, and stale admin capability clearing.
4. Run deployment readiness against the actual beta/staging target when target URL, env file, and token are available.

# Daily AIDM Codebase Improvement Audit - 2026-06-17 16:02 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement pass across the current dirty checkout, automation memory, recent report history, shared request validation, endpoint input handling, frontend auth/session storage surfaces, direct session snapshot writers, deployment-readiness workflow, generated artifacts, local ignored artifacts, and lightweight developer workflow checks. The worktree was already dirty at the start of this run with pre-existing changes in creature contract/route files, state-pipeline files, LLM provider tests, auth/game-state tests, and this report. Those existing changes were preserved; this run only changed shared JSON body validation, added a focused worlds endpoint regression, and prepended this report.

## What Was Inspected

- Automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`, which showed the same-day LLM stream fallback telemetry fix and carried-forward focus on direct `state_snapshot` writer ownership, hosted auth proof, deployment-readiness evidence, and player resolver DTO hardening.
- Current git status and diff shape to avoid overwriting unrelated or prior-run work:
  - `aidm_frontend/src/apiContract.generated.ts`
  - `aidm_server/api_type_contract.py`
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/game_state/extraction/post_dm_outcome_extractor.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`
  - `aidm_server/game_state/validation/validator.py`
  - `aidm_server/llm.py`
  - `tests/test_auth.py`
  - `tests/test_game_state_pipeline.py`
  - `tests/test_llm_provider.py`
  - `improvements_suggestions.md`
- Top-of-file report history in `improvements_suggestions.md` to avoid repeating the 2026-06-17 06:02 MDT LLM telemetry fix and the 2026-06-16 resolver debug hardening.
- Shared request validation in `aidm_server/validation.py`, especially `parse_json_body`, `coerce_bool`, text validation helpers, and positive integer validation.
- Every current `parse_json_body(request)` caller across backend blueprints. The callers consistently expect JSON object bodies and then call `.get(...)`, which made the helper contract important.
- Route-level validation coverage in `tests/test_worlds_endpoints.py`, `tests/test_maps_endpoints.py`, `tests/test_rules_and_segments.py`, and `tests/test_tts_endpoints.py`.
- Frontend auth/session storage and account refresh behavior in `aidm_frontend/src/useRuntimeSettings.ts`, including local/session storage, HTTP-only cookie transport handling, logout cleanup, workspace role refresh, and stale admin capability clearing.
- Frontend button/form accessibility scan targets in `aidm_frontend/src/**/*.tsx`, with special attention to forms and untyped buttons. No small isolated button-type patch was selected because the shared backend validation crash risk was clearer and easier to verify.
- Direct session snapshot persistence via `rg "\.state_snapshot\s*=" aidm_server tests`, confirming the same ownership-classification work remains open in runtime/service paths.
- Local ignored artifacts such as `.DS_Store` and `__pycache__`. They are ignored and not tracked, so this run did not delete local runtime/test artifacts.
- Developer workflow targets in `Makefile`, `scripts/scan_secrets.py`, `scripts/deployment_readiness_check.py`, and frontend `package.json`.

## Small Safe Fix Made

### Reject non-object JSON bodies in the shared request parser

Affected files:
- `aidm_server/validation.py`
- `tests/test_worlds_endpoints.py`
- `improvements_suggestions.md`

Problem:
`parse_json_body` was typed and used as `dict | None`, but it returned any valid JSON value from Flask. Several route handlers immediately call `.get(...)` after checking only `payload is None`. A request such as `POST /api/worlds` with a JSON array could therefore reach route code as a list and raise an attribute error instead of returning a normal 400 validation response.

Change:
- Updated `parse_json_body` to return the parsed payload only when it is a JSON object.
- Non-JSON, malformed JSON, arrays, scalars, and `null` now all return `None` through this helper.
- Added a route-level regression in `tests/test_worlds_endpoints.py` proving a JSON array body receives a 400 `validation_error` instead of crashing.

Rationale:
This is a narrow contract-alignment fix. Existing object-body API requests keep the same behavior, and existing endpoint code already treats `None` as invalid/missing body. Optional-body endpoints that use `parse_json_body(request) or {}` now also avoid non-object crashes by falling back to their existing empty-payload behavior.

## Verification

- `./.venv/bin/python -m pytest tests/test_worlds_endpoints.py::test_create_world_validates_request_body_and_fields`
  - Passed: 1 test.
- `./.venv/bin/python -m pytest tests/test_worlds_endpoints.py tests/test_maps_endpoints.py tests/test_rules_and_segments.py::test_create_segment_validates_request_body_and_fields tests/test_tts_endpoints.py::test_llm_config_rejects_ambiguous_persist_boolean`
  - Passed: 13 tests.
- `./.venv/bin/python -m py_compile aidm_server/validation.py tests/test_worlds_endpoints.py`
  - Passed.
- `git diff --check -- aidm_server/validation.py tests/test_worlds_endpoints.py improvements_suggestions.md`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py aidm_server/validation.py tests/test_worlds_endpoints.py improvements_suggestions.md`
  - Passed: no likely committed secrets found.

## High-Priority Findings

### High: Direct session snapshot writers still need explicit ownership categories

Current evidence:
- `aidm_server/services/session_state_mutation.py` provides the central locked, revision-aware mutation path.
- Direct runtime/service assignments still appear in paths including:
  - `aidm_server/services/campaign_pack_progress.py`
  - `aidm_server/services/campaign_pack_storage.py`
  - `aidm_server/services/campaign_pack.py`
  - `aidm_server/services/session_import.py`
  - `aidm_server/services/session_lifecycle.py`
  - `aidm_server/blueprints/sessions.py`
  - `aidm_server/turn_control.py`
  - `aidm_server/canon_projection.py`
  - `aidm_server/game_state/application/applier.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`

Risk:
Some direct writes are legitimate initialization, import, cleanup, projection, or server-owned application paths. The remaining risk is that live gameplay mutation, repair, campaign-pack progression, and projection writes still look identical at the persistence boundary, making revision behavior, locking, audit visibility, and conflict handling harder to prove.

Recommended next step:
Build a checked writer inventory with one row per direct assignment and classify each writer as initialization, import, cleanup, projection, live gameplay mutation, or repair. Then migrate one clearly live mutation or repair path at a time through `mutate_session_state` or a named server-internal equivalent.

### Medium-High: Hosted account/session storage still needs browser-flow proof

Current evidence:
- `aidm_frontend/src/useRuntimeSettings.ts` still has local/session storage code for account, workspace, and token-adjacent state while also supporting `http_only_cookie` transport.
- Tests cover remembered account refresh, role downgrade refresh, logout cleanup, and some token migration behavior.
- `scripts/deployment_readiness_check.py` expects `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false` for cookie-only hosted browser auth.

Risk:
Local/private operator mode is useful, but hosted beta still needs real browser evidence that browser-readable account tokens are disabled, logout clears account/capability state, workspace switching refreshes role state, and stale admin capability UI cannot persist after a downgrade.

Recommended next step:
Add or run a hosted-mode browser regression around login, `/api/accounts/session`, `/api/accounts/me`, logout, workspace switch, and stale `isWorkspaceAdmin` display. Keep local unauthenticated operator mode separate from hosted beta expectations.

### Medium-High: Deployment-readiness checks still need live beta/staging evidence

Current evidence:
- `scripts/deployment_readiness_check.py` validates production env choices, CORS, secure cookie settings, bearer-token exceptions, metrics, `/api/health`, required security headers, and optional Socket.IO staging proof.
- `Makefile` exposes `deployment-readiness`.
- This automation run did not have a real beta/staging URL, env file, or auth token.

Risk:
Local code checks cannot prove hosted cookie flags, CORS, security headers, metrics exposure, Socket.IO worker behavior, or provider configuration in the actual target.

Recommended next step:
Run `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> --target-url <target-url> --auth-token <token>"` against the actual beta target and save the result in `docs/release_checklist.md` or `docs/beta_runbook.md`.

### Medium: Player resolver preview should still move to an explicit player DTO

Current evidence:
- Prior work removed resolver `debug` from non-`debug_read` responses.
- The shared `CreatureResolutionResult` contract still has optional operator/debug fields, and there is not yet a dedicated player-facing resolver response type.

Risk:
The obvious debug leakage is closed, but a later operator-only resolver field could be added to the shared result and become player-visible unless the player preview response is allowlisted by design.

Recommended next step:
Define a dedicated player preview DTO or reusable allowlist sanitizer. Add a regression that compares player/admin resolver payload keys and fails if debug rankings, model names, normalized request metadata, or hidden candidate identifiers reappear in player responses.

## Larger Suggested Improvements

- Add a direct `parse_json_body` unit test file covering object, non-JSON, array, scalar, malformed JSON, and `null` bodies so the shared helper contract is checked independently of any one route.
- Consider using a shared `validation_error` message for non-object JSON bodies, such as "Expected JSON object request body.", if the API should distinguish malformed JSON from wrong JSON shape.
- Turn the direct `state_snapshot` writer scan into a checked inventory artifact and use it as the migration queue for centralized mutation ownership.
- Add hosted-mode Playwright coverage for cookie-only account auth, logout cleanup, workspace switching, and stale role/capability clearing.
- Attach the next real deployment-readiness output to `docs/release_checklist.md` or `docs/beta_runbook.md`.
- Keep future safe-fix runs away from the broad pre-existing state-pipeline/auth diff until that patch set is committed, reviewed, or explicitly handed to the automation for continuation.

## Recommended Next Run Focus

1. Build the direct `state_snapshot` writer inventory and classify each writer by ownership category.
2. Add the strict player resolver preview DTO/allowlist on top of the existing debug omission.
3. Add hosted auth browser proof for logout, workspace switch, and stale admin capability clearing.
4. Add direct unit coverage for `parse_json_body` helper shapes if no higher-risk safe fix is available.

# Daily AIDM Codebase Improvement Audit - 2026-06-17 06:02 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement pass across the current dirty checkout, recent audit memory, report history, LLM fallback observability, telemetry surfaces, direct session snapshot writers, hosted auth/session storage, deployment-readiness workflow, frontend accessibility/storage scan targets, and lightweight developer workflow checks. The worktree was already dirty at the start of this run with pre-existing changes in creature route/contract files, state-pipeline files, auth/game-state tests, and this report. Those existing changes were preserved; this run only added a narrow LLM stream-fallback telemetry fix, a focused regression test, and this dated report.

## What Was Inspected

- Automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`, which showed the prior run's resolver debug hardening and carried-forward focus on state-snapshot writer ownership, hosted auth proof, and deployment-readiness evidence.
- Current git status and diff shape to avoid overwriting unrelated or prior-run work:
  - `aidm_frontend/src/apiContract.generated.ts`
  - `aidm_server/api_type_contract.py`
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/game_state/extraction/post_dm_outcome_extractor.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`
  - `aidm_server/game_state/validation/validator.py`
  - `tests/test_auth.py`
  - `tests/test_game_state_pipeline.py`
  - `improvements_suggestions.md`
- Top-of-file report history in `improvements_suggestions.md` to avoid repeating the 2026-06-16 creature resolver and bestiary preview fixes.
- LLM fallback paths in `aidm_server/llm.py`, especially the difference between `query_dm_function`, `query_dm_function_stream`, `query_gpt`, and `query_gpt_stream` failure handling.
- Telemetry event recording in `aidm_server/telemetry.py`, confirming that local metrics can count events even when external delivery is disabled.
- Provider regression coverage in `tests/test_llm_provider.py`.
- Defensive exception handling and outbound request patterns in `aidm_server/*`, `scripts/*`, and `tests/*`.
- Direct session snapshot persistence via `rg "\.state_snapshot\s*=" aidm_server`, which still found runtime/system writers in campaign-pack progress/storage, session lifecycle/import, sessions routes, turn control, canon projection, applier, and turn pipeline code.
- Hosted account/session storage in `aidm_frontend/src/useRuntimeSettings.ts` and its tests, including `sessionStorage`, `localStorage`, `accountTokenTransport`, and `http_only_cookie` handling.
- Deployment-readiness workflow in `scripts/deployment_readiness_check.py`, `Makefile`, `docs/production-readiness.md`, `docs/beta_runbook.md`, and `docs/release_checklist.md`.
- Frontend accessibility/security scan targets for common risks such as button types, ARIA usage, browser storage, and HTML injection sinks. No small isolated accessibility fix was more compelling than the LLM observability gap in this pass.

## Small Safe Fix Made

### Record telemetry when summary streaming falls back after provider failure

Affected files:
- `aidm_server/llm.py`
- `tests/test_llm_provider.py`
- `improvements_suggestions.md`

Problem:
`query_gpt_stream` already returned the same safe fallback message when a provider stream failed, but it swallowed the exception silently. Neighboring LLM paths already log warnings and emit telemetry events on provider failures. The silent path made summary-stream outages harder to diagnose from local metrics, beta incident panels, or logs.

Change:
- Added a warning log in `query_gpt_stream` when `provider.stream(...)` raises.
- Added `telemetry_event('llm.query_gpt_stream.failed', payload={'error': ...}, severity='warning')`.
- Preserved the exact fallback response text: `Session summary is temporarily unavailable due to AI provider unavailability.`
- Added `tests/test_llm_provider.py::test_query_gpt_stream_records_telemetry_on_provider_failure`, which proves the fallback still returns and the warning telemetry is emitted.

Rationale:
This is a narrow observability fix on an existing exception path. It does not alter provider selection, prompt construction, streaming success behavior, user-visible fallback copy, persistence, auth, state mutation, or frontend behavior.

## Verification

- `./.venv/bin/python -m pytest tests/test_llm_provider.py::test_query_gpt_stream_records_telemetry_on_provider_failure`
  - Passed: 1 test.

## High-Priority Findings

### High: Direct session snapshot writers still need explicit ownership categories

Current evidence:
- `aidm_server/services/session_state_mutation.py` provides a central mutation helper.
- Fresh scan still found direct instance writes such as:
  - `aidm_server/services/campaign_pack_progress.py`
  - `aidm_server/services/campaign_pack_storage.py`
  - `aidm_server/services/campaign_pack.py`
  - `aidm_server/services/session_import.py`
  - `aidm_server/services/session_lifecycle.py`
  - `aidm_server/blueprints/sessions.py`
  - `aidm_server/turn_control.py`
  - `aidm_server/canon_projection.py`
  - `aidm_server/game_state/application/applier.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`

Risk:
Some direct writes are legitimate initialization, import, cleanup, projection, or server-owned application paths. The remaining risk is that live gameplay mutation, repair, campaign-pack progression, and projection writes still look identical at the persistence boundary, making lock ordering, revision behavior, audit visibility, and conflict handling harder to prove.

Recommended next step:
Create a small writer inventory with one row per direct assignment and classify each writer as initialization, import, cleanup, projection, live gameplay mutation, or repair. Then migrate one clearly live mutation or repair path at a time through `mutate_session_state` or a named server-internal equivalent.

### Medium-High: Hosted account/session storage still needs browser-flow proof

Current evidence:
- `aidm_frontend/src/useRuntimeSettings.ts` still reads and writes account/workspace data through `sessionStorage` and `localStorage`.
- It also supports `http_only_cookie` transport, and tests cover some token migration/session behavior.
- `scripts/deployment_readiness_check.py` expects `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false` for cookie-only hosted browser auth.

Risk:
The local/private operator flow is useful, but hosted beta still needs real browser evidence that browser-readable account tokens are disabled, logout clears account/capability state, workspace switching refreshes role state, and stale admin capability UI cannot persist after a role downgrade.

Recommended next step:
Add or run a hosted-mode browser regression around login, `/api/accounts/session`, logout, workspace switch, and stale `isWorkspaceAdmin` display. Keep local unauthenticated operator mode separate from hosted beta expectations.

### Medium-High: Deployment-readiness checks need live beta/staging evidence

Current evidence:
- `scripts/deployment_readiness_check.py` validates production env choices, CORS, secure cookie settings, bearer-token exceptions, metrics, `/api/health`, required security headers, and optional Socket.IO staging proof.
- `Makefile` exposes `deployment-readiness`.
- Docs and release checklist point to `--target-url` and `--auth-token`, but this automation run did not have a real beta/staging URL, env file, or token.

Risk:
Local code checks cannot prove hosted cookie flags, CORS, security headers, metrics exposure, Socket.IO worker behavior, or provider configuration in the actual target.

Recommended next step:
Run `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> --target-url <target-url> --auth-token <token>"` against the actual beta target and save the result in the release checklist or beta runbook.

### Medium: Player resolver preview is improved, but an explicit player DTO is still safer

Current evidence:
- The prior 2026-06-16 run removed resolver `debug` from non-`debug_read` responses.
- The shared `CreatureResolutionResult` contract now has optional `debug`, but there is still not a dedicated player-facing resolver result type.

Risk:
The obvious debug leakage is closed, but a later operator-only resolver field could be added to the shared result and become player-visible unless the player preview response is allowlisted by design.

Recommended next step:
Define a dedicated player preview DTO or reusable allowlist sanitizer. Add a regression that directly compares player/admin resolver payload keys and fails if debug rankings, model names, normalized request metadata, or hidden candidate identifiers reappear in player responses.

## Larger Suggested Improvements

- Add a `make llm-check` or include a focused provider-fallback subset in `make dev-check` so streaming fallback telemetry regressions are easier to catch.
- Turn the direct `state_snapshot` writer scan into a checked inventory artifact, then use it as the migration queue for centralized mutation ownership.
- Add hosted-mode Playwright coverage for cookie-only account auth, logout cleanup, workspace switching, and stale role/capability clearing.
- Attach the next real deployment-readiness output to `docs/release_checklist.md` or `docs/beta_runbook.md` so beta readiness is based on target evidence rather than local intent.
- Keep small audit fixes away from the broad pre-existing state-pipeline/auth diff until that patch set is either committed, reviewed, or explicitly handed to the automation for continuation.

## Recommended Next Run Focus

1. Build the direct `state_snapshot` writer inventory and classify each writer by ownership category.
2. Add the strict player resolver preview DTO/allowlist on top of the existing debug omission.
3. Add hosted auth browser proof for logout, workspace switch, and stale admin capability clearing.
4. Run deployment-readiness against the actual beta/staging target when URL, env file, and auth token are available.

# Daily AIDM Codebase Improvement Audit - 2026-06-16 16:03 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement pass across the current dirty checkout, creature resolver preview security, API type contract consistency, auth/capability regression coverage, session-state mutation boundaries, hosted auth/storage readiness, deployment-readiness workflow, frontend TypeScript health, and developer workflow checks. The worktree was already dirty at the start of this run with existing changes in `aidm_server/blueprints/creatures.py`, state-pipeline files, auth/game-state tests, and this report. Those existing changes were preserved; this run only added a narrow resolver response hardening, matching contract/test updates, this dated report, and automation memory.

## What Was Inspected

- Automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`, which showed the prior run's bestiary save parsing fix and recommended focus on resolver preview sanitization, direct state writers, and hosted auth readiness.
- Current git status and diff shape to avoid overwriting unrelated or prior-run work:
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/game_state/extraction/post_dm_outcome_extractor.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`
  - `aidm_server/game_state/validation/validator.py`
  - `tests/test_auth.py`
  - `tests/test_game_state_pipeline.py`
  - `improvements_suggestions.md`
- Creature resolver API behavior in `aidm_server/blueprints/creatures.py` and resolver internals in `aidm_server/creatures/resolver.py`, especially the `debug.request`, `debug.rankings`, and `debug.generatedModel` fields returned by `/api/creatures/resolve`.
- Capability policy in `aidm_server/capabilities.py` and workspace/account request helpers in `aidm_server/workspace_access.py` to reuse the existing `debug_read` capability boundary.
- Auth/capability regression coverage in `tests/test_auth.py`, including player preview requests and admin save-capable resolver requests.
- Backend-owned API contract generation:
  - `aidm_server/api_type_contract.py`
  - `scripts/generate_api_types.py`
  - `aidm_frontend/src/apiContract.generated.ts`
- Direct `Session.state_snapshot` writers and central mutation-service usage across `aidm_server/services/*`, `aidm_server/blueprints/*`, `aidm_server/canon_projection.py`, `aidm_server/turn_control.py`, and `aidm_server/game_state/*`.
- Runtime account/session persistence in `aidm_frontend/src/useRuntimeSettings.ts`, including `sessionStorage`, `localStorage`, legacy cookie fallback, and hosted HTTP-only cookie transport expectations.
- Hosted readiness checks in `scripts/deployment_readiness_check.py` and the `Makefile` `deployment-readiness` target.
- Lightweight developer workflow health through generated-type, Python compile, whitespace, secret scan, and frontend TypeScript checks.

## Small Safe Fix Made

### Strip resolver debug internals from non-debug users

Affected files:
- `aidm_server/blueprints/creatures.py`
- `aidm_server/api_type_contract.py`
- `aidm_frontend/src/apiContract.generated.ts`
- `tests/test_auth.py`

Problem:
`/api/creatures/resolve` allowed non-saving player previews, but the resolver returned the same debug-rich payload to every caller. The `debug` object includes normalized request details, ranking buckets, candidate creature IDs, scores, and generated model metadata. That information is useful for operators but too revealing for normal player previews.

Change:
- Added `_creature_resolution_response(...)` at the route boundary.
- Kept full resolver results for callers with `debug_read`, which includes workspace admins and local unauthenticated operator mode.
- Removed the `debug` field from non-`debug_read` responses without changing resolver selection, generated creature shape, save behavior, persistence, or normal admin debugging.
- Made `CreatureResolutionResult.debug` optional in the backend API type contract and regenerated `aidm_frontend/src/apiContract.generated.ts`.
- Extended `tests/test_auth.py::test_bestiary_authoring_endpoints_require_workspace_admin_account` to prove a player preview with `saveGenerated: "off"` succeeds but omits `debug`, while the corresponding admin resolver call still includes debug rankings.

Rationale:
This is a small route-boundary hardening fix. It reduces player-visible implementation leakage while preserving the operator/debug workflow and the local unauthenticated operator convention already used in the backend capability model.

## Verification

- `./.venv/bin/python -m pytest tests/test_auth.py::test_bestiary_authoring_endpoints_require_workspace_admin_account`
  - Passed: 1 test.
- `./.venv/bin/python -m pytest tests/test_auth.py`
  - Passed: 25 tests.
- `./.venv/bin/python scripts/generate_api_types.py --check`
  - Passed.
- `./.venv/bin/python -m py_compile aidm_server/blueprints/creatures.py aidm_server/api_type_contract.py tests/test_auth.py scripts/generate_api_types.py`
  - Passed.
- `git diff --check -- aidm_server/blueprints/creatures.py aidm_server/api_type_contract.py aidm_frontend/src/apiContract.generated.ts tests/test_auth.py improvements_suggestions.md`
  - Passed after this report was prepended.
- `./.venv/bin/python scripts/scan_secrets.py aidm_server/blueprints/creatures.py aidm_server/api_type_contract.py aidm_frontend/src/apiContract.generated.ts tests/test_auth.py improvements_suggestions.md`
  - Passed: no likely committed secrets found.
- `npm --prefix aidm_frontend run typecheck`
  - Passed.

## High-Priority Findings

### High: Direct session snapshot writers still need explicit ownership categories

Current evidence:
- `aidm_server/services/session_state_mutation.py` provides locked, revision-aware mutation with audit stamping.
- Direct `Session.state_snapshot` assignments still exist in runtime or system paths such as:
  - `aidm_server/services/campaign_pack_progress.py`
  - `aidm_server/services/campaign_pack.py`
  - `aidm_server/services/campaign_pack_storage.py`
  - `aidm_server/services/session_import.py`
  - `aidm_server/services/session_lifecycle.py`
  - `aidm_server/blueprints/sessions.py`
  - `aidm_server/canon_projection.py`
  - `aidm_server/turn_control.py`
  - `aidm_server/game_state/application/applier.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`

Risk:
Some direct writes are legitimate initialization, import, cleanup, projection, or system-owned paths. The risk is that live mutation, repair, and progression writes still share the same raw assignment pattern, making it difficult to prove lock ordering, state revision behavior, audit visibility, and conflict handling.

Recommended next step:
Create a small inventory document or code comment table classifying each writer as initialization, import, cleanup, projection, live gameplay mutation, or repair. Migrate one live gameplay or repair path at a time through `mutate_session_state` or a named server-internal equivalent.

### Medium-High: Player preview DTO is improved but still not fully explicit

Current evidence:
- This run removes `debug` from non-`debug_read` resolver responses.
- Player preview responses still return the selected `creature`, `source`, `resolutionMethod`, `matchScore`, `generated`, `savedToBestiary`, and `notes`.
- `aidm_server/api_type_contract.py` now models `debug` as optional, but there is not yet a dedicated `PlayerCreatureResolutionResult` type.

Risk:
Removing `debug` closes the obvious internal leakage, but a strict player-facing DTO would make future resolver fields safer by default. Without that split, a later operator-only field could be added to the general result and become player-visible.

Recommended next step:
Define a dedicated player preview DTO or a reusable response sanitizer with an allowlist of fields. Add a regression that fails if `debug`, rankings, generated model names, request normalization, or hidden candidate metadata reappear in player preview responses.

### Medium-High: Hosted beta readiness still needs live target proof

Current evidence:
- `scripts/deployment_readiness_check.py` validates production environment settings, cookie auth, CORS, security headers, provider mode, metrics, and optional live target endpoints.
- This run inspected the readiness script but did not have a beta/staging URL, production env file, or auth token to run live readiness.

Risk:
Local tests cannot prove hosted cookie flags, CSRF/CORS behavior, security headers, metrics exposure, Socket.IO worker behavior, or provider configuration in the actual beta target.

Recommended next step:
Run `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--target-url <beta-url> --auth-token <token> ..."` with real beta/staging inputs and attach the output to `docs/beta_runbook.md` or the release checklist.

### Medium: Hosted-mode account storage needs browser-flow proof

Current evidence:
- `aidm_frontend/src/useRuntimeSettings.ts` migrates legacy account tokens out of `localStorage`, supports session storage, records account token transport, and handles HTTP-only cookie transport.
- The frontend still stores account snapshots in both `sessionStorage` and `localStorage`, and local/browser-readable cookie fallback remains available for non-hosted flows.
- `scripts/deployment_readiness_check.py` expects `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false` for cookie-only hosted browser auth.

Risk:
The local operator flow is useful, but hosted beta still needs end-to-end evidence that browser-readable bearer tokens are disabled, logout clears account/capability state, workspace switching refreshes role state, and stale admin capability display cannot survive a role downgrade.

Recommended next step:
Add or run a hosted-mode browser regression around login, `/api/accounts/session`, logout, workspace switch, and stale `isWorkspaceAdmin` display. Keep local unauthenticated operator mode separate from hosted beta expectations.

## Larger Suggested Improvements

- Convert the direct `state_snapshot` writer inventory into a small migration queue with one owner, one expected mutation category, and one test target per writer.
- Add a resolver response contract test that compares player and admin payload keys directly and documents which fields are intentionally shared.
- Add a CI or `make dev-check` gate that runs `scripts/generate_api_types.py --check` whenever `aidm_server/api_type_contract.py` changes.
- Add a short cleanup/documented command for ignored `.DS_Store` and `__pycache__` artifacts visible in local scans; this run did not remove them because they are unrelated local artifacts.
- Keep security-sensitive route changes at capability boundaries first, with frontend visibility as a mirror of backend permissions rather than the source of truth.

## Recommended Next Run Focus

1. Inventory direct `Session.state_snapshot` writers and migrate one clearly live mutation path through a central mutation helper.
2. Add a strict allowlist-based player creature resolver DTO, building on the `debug` omission from this run.
3. Add hosted-mode auth/session storage proof around logout, workspace switch, and stale capability state.
4. Run deployment-readiness against the actual beta target once target URL and token are available.

# Daily AIDM Codebase Improvement Audit - 2026-06-16 06:05 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: safe-improvement pass across the current dirty checkout, creature/bestiary route request parsing, auth/capability tests, session-state mutation boundaries, creature resolver response shape, frontend runtime account/session persistence, deployment-readiness checks, local ignored artifacts, and lightweight developer workflow gates. The worktree was already dirty at the start of this run with existing changes in `aidm_server/blueprints/creatures.py`, state-pipeline files, auth/game-state tests, and this report. Those existing changes were preserved; this run only added a narrow compatible bestiary route parsing fix, one regression test, this dated report, and automation memory.

## What Was Inspected

- Automation memory at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`, which showed the prior run's creature route preflight work and recommended next focus.
- Current git status and diff shape to avoid overwriting unrelated or prior-run work:
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/game_state/extraction/post_dm_outcome_extractor.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`
  - `aidm_server/game_state/validation/validator.py`
  - `tests/test_auth.py`
  - `tests/test_game_state_pipeline.py`
  - `improvements_suggestions.md`
- Creature and bestiary route behavior in `aidm_server/blueprints/creatures.py`, especially `saveGenerated` and `save` request flags.
- Auth/capability regression coverage in `tests/test_auth.py`.
- Creature resolver DTO/debug behavior in `aidm_server/creatures/resolver.py` and the API type contract reference in `aidm_server/api_type_contract.py`.
- Session-state mutation and direct snapshot persistence boundaries:
  - `aidm_server/services/session_state_mutation.py`
  - `aidm_server/blueprints/players.py`
  - `aidm_server/services/campaign_pack_progress.py`
  - `aidm_server/services/campaign_pack.py`
  - `aidm_server/turn_control.py`
  - `aidm_server/canon_projection.py`
  - `aidm_server/blueprints/sessions.py`
- Frontend runtime account/session storage in `aidm_frontend/src/useRuntimeSettings.ts` and related tests.
- Hosted deployment-readiness checks in `scripts/deployment_readiness_check.py`.
- Local ignored artifacts via `.gitignore` and `find . -name .DS_Store`.

## Small Safe Fix Made

### Normalize `save: "off"` for campaign bestiary pack previews

Affected files:
- `aidm_server/blueprints/creatures.py`
- `tests/test_auth.py`

Problem:
`/api/campaigns/<campaign_id>/bestiary/generate-pack` already had a non-persisting preview mode through `save: false`, but the route only recognized the literal boolean `False`. Sibling creature routes now use `coerce_bool`, so request values such as `"off"`, `"false"`, or `"0"` could behave inconsistently across bestiary tooling.

Change:
- Parsed the `save` flag with the existing `coerce_bool(..., True)` helper before deciding whether to persist generated pack creatures.
- Preserved default save-enabled behavior when the field is omitted or malformed.
- Added `tests/test_auth.py::test_generate_pack_string_save_off_generates_without_persisting`, proving an admin request with `save: "off"` returns generated creatures, writes no `BestiaryEntry` rows, and records an operator audit with `savedCount: 0`.

Rationale:
This is a route-boundary consistency fix. It does not alter creature generation, persistence schema, authorization policy, or normal save-enabled behavior.

## Verification

- `./.venv/bin/python -m pytest tests/test_auth.py::test_generate_pack_string_save_off_generates_without_persisting`
  - Passed: 1 test.
- `./.venv/bin/python -m pytest tests/test_auth.py`
  - Passed: 25 tests.
- `./.venv/bin/python -m py_compile aidm_server/blueprints/creatures.py tests/test_auth.py`
  - Passed.
- `git diff --check -- aidm_server/blueprints/creatures.py tests/test_auth.py improvements_suggestions.md`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py aidm_server/blueprints/creatures.py tests/test_auth.py improvements_suggestions.md`
  - Passed: no likely committed secrets found.

## High-Priority Findings

### High: Player-safe creature preview responses still need a sanitized DTO

Current evidence:
- `aidm_server/blueprints/creatures.py` allows non-saving player previews for `/api/creatures/resolve`.
- `aidm_server/creatures/resolver.py` returns `debug` data with normalized request details, ranking IDs, group internals, sources, and match scores.
- `aidm_server/api_type_contract.py` still models `CreatureResolutionResult.debug` as part of the response shape.

Risk:
Preview access can expose encounter-selection internals or hidden authored creature identifiers. The current capability gates protect save-capable routes, but they do not define a player-safe response contract for resolver previews.

Recommended next step:
Split resolver responses into a player-safe preview DTO and an operator/debug DTO. Add non-admin tests proving preview responses omit `debug.rankings`, hidden catalog identifiers, model/generation details, and other operator-only diagnostics.

### High: Direct session snapshot writers remain outside the centralized mutation service

Current evidence:
- `aidm_server/services/session_state_mutation.py` now provides locked, revision-aware mutation with audit stamping.
- Several legitimate flows still assign `Session.state_snapshot` directly, including campaign-pack progress/import, turn control, canon projection, session metadata cleanup, and some player/session helpers.

Risk:
Some direct writes are system-owned and may be valid, but the mixed write model makes it harder to prove revision consistency, conflict behavior, audit coverage, and ordering when live gameplay, campaign-pack progression, and repair jobs touch the same session.

Recommended next step:
Create an explicit inventory of direct `state_snapshot` writers, classify them as initialization/system repair/live mutation, and migrate live mutation paths through `mutate_session_state` or a server-internal equivalent that preserves lock, revision, and audit semantics.

### Medium-High: Hosted beta readiness still needs live target evidence

Current evidence:
- `scripts/deployment_readiness_check.py` validates production env requirements, auth, CORS, security headers, metrics, provider mode, and optional live target endpoints.
- This run did not have a beta/staging target URL or production env file to verify.

Risk:
Local tests cannot prove hosted cookie behavior, CSRF, CORS, security headers, metrics exposure, Socket.IO worker behavior, or provider configuration in the actual beta environment.

Recommended next step:
Run `make deployment-readiness` or `scripts/deployment_readiness_check.py --target-url <beta-url> --auth-token <token>` with real beta/staging inputs and attach the output to `docs/beta_runbook.md` or the release checklist.

### Medium: Runtime account storage still needs hosted-mode hardening proof

Current evidence:
- `aidm_frontend/src/useRuntimeSettings.ts` migrates legacy account tokens out of `localStorage`, supports session storage, and records an account-token transport mode.
- The file still keeps account snapshots in both `sessionStorage` and `localStorage`, and local/browser-readable cookie fallback remains available for non-hosted flows.
- The deployment-readiness script expects hosted HTTP-only cookie auth unless an explicit exception is supplied.

Risk:
The local/private operator flow is useful, but hosted beta needs evidence that browser-readable bearer tokens are disabled, stale admin capability display is cleared on logout/workspace switch, and account snapshots cannot keep privileged UI visible after role changes.

Recommended next step:
Add a hosted-mode regression or smoke path that proves `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false`, HTTP-only cookie transport is active, logout clears local account/capability state, and workspace switching refreshes `isWorkspaceAdmin`.

## Larger Suggested Improvements

- Define a route capability matrix for bestiary, custom races, campaign pack tools, imports, direct combat controls, session repairs, beta/debug endpoints, and player-visible previews.
- Add resolver response contract tests for player previews and operator debug calls.
- Convert direct session-state writes into named mutation categories with tests for revision increments, conflict responses, and audit records.
- Add a small artifact-cleanup task or documented command for ignored `.DS_Store` files; they are ignored and untracked, so this run did not remove them.
- Keep future state-pipeline work server-authoritative for mechanical cross-player damage and avoid allowing narration-only packets to bypass actor ownership.

## Recommended Next Run Focus

1. Sanitize the creature resolver preview DTO for non-operator users.
2. Inventory direct `Session.state_snapshot` writers and migrate one small live-mutation path through the mutation service.
3. Add hosted-mode auth/session storage proof around logout, workspace switch, and stale capability state.
4. Run deployment-readiness against the actual beta target once a target URL and token are available.

# Daily AIDM Codebase Improvement Audit - 2026-06-15 16:03 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: follow-up safe-improvement pass across creature/bestiary route authorization, save-preview request parsing, current auth regression coverage, prior 2026-06-15 audit findings, state-pipeline diff boundaries, frontend session/capability persistence, deployment-readiness hooks, and lightweight developer workflow checks. The worktree was already dirty at the start of this run with existing changes in `aidm_server/blueprints/creatures.py`, state-pipeline files, tests, and this report; those changes were preserved and only narrow compatible edits were added.

## What Was Inspected

- Recurring automation context and memory expectations. The automation memory file was missing at `/Users/danny/.codex/automations/daily-aidm-codebase-improvement-audit/memory.md`, so this run used the current report history and repository evidence rather than relying on a prior automation note.
- Current uncommitted diffs to avoid overwriting unrelated work:
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/game_state/extraction/post_dm_outcome_extractor.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`
  - `aidm_server/game_state/validation/validator.py`
  - `tests/test_auth.py`
  - `tests/test_game_state_pipeline.py`
  - `improvements_suggestions.md`
- Creature route capability and save behavior:
  - `/api/creatures/resolve`
  - `/api/creatures/evolve`
  - `/api/sessions/<session_id>/combat/plan-enemy-intents`
  - bestiary authoring helpers in `aidm_server/blueprints/creatures.py`
- Auth and capability regression coverage in `tests/test_auth.py`, especially the combat-operator and bestiary-authoring tests added by the prior in-progress audit work.
- Remaining state-pipeline ownership changes at a diff level, to ensure this run did not collide with the larger cross-player damage work already present.
- Frontend session/capability persistence and accessibility surfaces:
  - `aidm_frontend/src/useRuntimeSettings.ts`
  - `aidm_frontend/src/BestiaryDebugPanel.tsx`
- Developer workflow checks and release gates:
  - `Makefile`
  - `scripts/scan_secrets.py`
  - `scripts/deployment_readiness_check.py`
  - `pytest.ini`

## Small Safe Fixes Made

### Preflight save-capable creature evolution before evolution work

Affected files:
- `aidm_server/blueprints/creatures.py`
- `tests/test_auth.py`

Problem:
`/api/creatures/evolve` validated `baseCreature`, then performed `evolve_creature(...)`, and only afterward checked whether a campaign save required `dm_authoring`. Non-admin save attempts were rejected before persistence, but they still consumed evolution work and could expose avoidable future cost if evolution becomes model-backed or heavier.

Change:
- Moved the existing `dm_authoring` preflight and campaign lookup ahead of `evolve_creature(...)` for save-enabled campaign requests.
- Preserved non-saving preview behavior by still allowing `saveGenerated: false` style requests to evolve without saving.
- Added `tests/test_auth.py::test_evolve_save_forbidden_preflights_before_evolution_work`, which monkeypatches `evolve_creature` to fail if it runs for a forbidden save request.

Rationale:
This keeps the same route policy while reducing resource work for forbidden requests. It is a route-boundary change only and does not alter the evolution algorithm or persistence format.

### Align `saveGenerated` preview parsing at the route boundary

Affected files:
- `aidm_server/blueprints/creatures.py`
- `tests/test_auth.py`

Problem:
The route preflights only treated the literal boolean `false` as a non-saving preview, while the creature resolver already accepts string-style booleans such as `"off"` and `"false"`. A player preview request using those existing API conventions could be rejected as if it intended to save.

Change:
- Added a small `_save_generated_enabled(...)` helper using the existing `coerce_bool` parser.
- Reused it in both `/api/creatures/resolve` and `/api/creatures/evolve`.
- Updated the bestiary auth regression to prove player preview requests with `saveGenerated: "off"` still succeed without saving.

Rationale:
This makes route authorization match the existing resolver request semantics and improves UX without broadening save-capable access.

## Verification

- `./.venv/bin/python -m pytest tests/test_auth.py::test_evolve_save_forbidden_preflights_before_evolution_work`
  - Passed: 1 test.
- `./.venv/bin/python -m pytest tests/test_auth.py`
  - Passed: 24 tests.
- `./.venv/bin/python -m py_compile aidm_server/blueprints/creatures.py tests/test_auth.py`
  - Passed.
- `git diff --check -- aidm_server/blueprints/creatures.py tests/test_auth.py improvements_suggestions.md`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py aidm_server/blueprints/creatures.py tests/test_auth.py improvements_suggestions.md`
  - Passed: no likely committed secrets found.

## High-Priority Findings Refreshed This Run

### High: Player-safe creature previews still need a sanitized response shape

Current evidence:
- `/api/creatures/resolve` now gates save-enabled campaign requests, but non-saving player previews remain allowed.
- `aidm_server/creatures/resolver.py` still returns a `debug` object with ranking and request-normalization details.

Risk:
Preview access may leak encounter-selection internals or authored campaign/region creature identifiers. That may be acceptable for DM tools but should not be assumed player-safe without a response contract.

Recommended next step:
Split resolver output into a player-safe preview DTO and an operator/debug DTO. Add non-admin tests asserting previews omit `debug.rankings`, hidden catalog identifiers, and generated model details.

### Medium-High: Mutable creative-content policy is still incomplete

Current evidence:
- Bestiary save-capable routes now use `dm_authoring`, but custom race generation/create/update/delete policy still appears separate from bestiary authoring.

Risk:
Without a route capability matrix, it remains unclear which content is player-owned homebrew, shared DM-authored catalog content, or workspace-admin-only content.

Recommended next step:
Define the route capability matrix before changing behavior. Then add ownership or admin-gate tests for custom races based on the intended product policy.

### Medium-High: Hosted beta readiness still needs target-environment proof

Current evidence:
- Local auth, CSRF, security-header, Socket.IO, and deployment-readiness checks exist in code and scripts.
- This run did not exercise the real hosted/staging target.

Risk:
Local tests cannot prove cookie auth, CSRF, logout cleanup, workspace switching, stale capability display, security headers, metrics, and Socket.IO affinity in the real deployment environment.

Recommended next step:
Run `make deployment-readiness` or `scripts/deployment_readiness_check.py` against the actual beta target with required staging proof, then attach the output to the beta checklist.

## Larger Suggested Improvements

- Add a route capability matrix for mutable routes, including bestiary, custom races, pack tools, session imports, combat controls, and beta/debug endpoints.
- Normalize boolean request parsing for remaining save/preview flags such as campaign bestiary pack generation, where the endpoint still uses literal `is not False` semantics.
- Add player-safe response contract tests for creature resolver previews, bestiary browsing, and custom race surfaces.
- Add frontend session regression coverage for logout, workspace switching, and stale admin capability display.
- Keep future state-pipeline changes server-authoritative for cross-player mechanical effects; avoid reintroducing model/narration ownership bypasses.

## Recommended Next Run Focus

1. Sanitize or split creature resolver debug fields for non-operator previews.
2. Decide custom race ownership and capability policy, then encode it in tests.
3. Normalize the remaining `save`/`saveGenerated` request parsing flags.
4. Run hosted deployment-readiness checks and record the exact target evidence.

# Daily AIDM Codebase Improvement Audit - 2026-06-15 06:04 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: focused safe-improvement pass across the current AIDM backend capability surface, creature/combat helper APIs, state-pipeline actor ownership for cross-player HP changes, prior dated audit recommendations, frontend capability/session storage, direct session-state write patterns, test coverage, and lightweight developer workflow checks. The worktree was clean at the start of this run. The top-of-file 2026-06-15 beta-hardening update showed that several stale findings from the 2026-06-14 automation memory had already been implemented, so this run verified the current code instead of reapplying older recommendations. A 07:33 MDT same-thread follow-up also fixed the enemy-damage case where Alice can act while a backend-resolved enemy attack damages Bob.

## What Was Inspected

- Automation memory for this recurring audit, especially the last run's combat/bestiary authorization findings and recommended next-run focus.
- Current report history in this file, including the 2026-06-15 beta-hardening update and the 2026-06-14 route-capability audit.
- Backend route capability and role checks:
  - `aidm_server/capabilities.py`
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/blueprints/system.py`
  - `aidm_server/blueprints/sessions.py`
  - `aidm_server/blueprints/campaigns.py`
  - `aidm_server/blueprints/races.py`
- Creature resolver/debug behavior:
  - `aidm_server/creatures/resolver.py`
  - `aidm_server/creatures/evolution.py`
  - `aidm_server/creatures/repository.py`
- Session-state mutation and remaining direct snapshot write evidence:
  - `aidm_server/services/session_state_mutation.py`
  - `aidm_server/services/campaign_pack_storage.py`
  - `aidm_server/services/campaign_pack_progress.py`
  - `aidm_server/blueprints/players.py`
- Frontend capability/session persistence and operator UI exposure:
  - `aidm_frontend/src/useRuntimeSettings.ts`
  - `aidm_frontend/src/BestiaryDebugPanel.tsx`
  - `aidm_frontend/src/capabilities.ts`
  - `aidm_frontend/src/App.test.tsx`
- Existing regression coverage:
  - `tests/test_auth.py`
  - `tests/test_game_state_pipeline.py`
  - `tests/test_creatures_combat.py`
  - `tests/test_deployment_readiness_check.py`
  - `tests/test_races_endpoints.py`
- State-pipeline actor ownership and enemy-resolved combat damage:
  - `aidm_server/game_state/extraction/post_dm_outcome_extractor.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`
  - `aidm_server/game_state/validation/validator.py`
  - `tests/test_game_state_pipeline.py`
- Developer workflow checks:
  - `Makefile`
  - `pytest.ini`
  - `scripts/scan_secrets.py`
  - `scripts/deployment_readiness_check.py`

## Small Safe Fixes Made

### Require operator capability for enemy intent planning

Affected files:
- `aidm_server/blueprints/creatures.py`
- `tests/test_auth.py`

Problem:
`/api/sessions/<session_id>/combat/plan-enemy-intents` was still workspace-visible after neighboring combat mutation/debug routes were moved behind operator capability checks. It returns hidden enemy intent planning data and should follow the same DM/operator boundary as combat debug and direct combat-state controls.

Change:
- Added `_combat_operator_forbidden_response()` to the enemy-intent planning route.
- Extended `tests/test_auth.py::test_combat_operator_endpoints_require_workspace_admin_account` so player-role accounts get `403` and workspace-admin accounts still get an intent plan.

Rationale:
This is a narrow route-boundary hardening that reuses the existing helper and preserves local unauthenticated operator mode. It does not change intent planning itself or normal turn-driven combat behavior.

### Require authoring capability for save-capable creature resolution

Affected files:
- `aidm_server/blueprints/creatures.py`
- `tests/test_auth.py`

Problem:
`/api/creatures/resolve` can call the resolver with `campaignId`, and the resolver defaults `saveGenerated` to true. When resolution creates a generated or variant creature, it can save campaign/session bestiary content. That made the route inconsistent with the newly gated bestiary create/generate/evolve-save endpoints.

Change:
- Added a route preflight: when `campaignId` is present and `saveGenerated` is not explicitly `false`, require the existing `dm_authoring` capability.
- Preserved non-saving player previews by allowing `saveGenerated: false`.
- Extended `tests/test_auth.py::test_bestiary_authoring_endpoints_require_workspace_admin_account` to cover player `403`, non-saving player preview success, and admin success.

Rationale:
This keeps preview-only behavior available while preventing normal player accounts from indirectly persisting DM-authored creature content through the resolver. The change is contained to one route preflight and one auth regression.

### Preserve trusted mechanical damage while blocking narration-authorized cross-player HP changes

Affected files:
- `aidm_server/game_state/extraction/post_dm_outcome_extractor.py`
- `aidm_server/game_state/orchestration/turn_pipeline.py`
- `aidm_server/game_state/validation/validator.py`
- `tests/test_game_state_pipeline.py`

Problem:
The post-DM extractor had a narration-confirmation path that could authorize `health.damage` and `combat.participant.update` against another player when the DM text said that target took damage. That closed a live HP drift issue, but it made untrusted helper/model JSON too powerful: Alice's turn could authorize Bob's HP loss solely because narration included "Bob takes 4 damage." Tightening that path also needed to preserve legitimate cross-player damage when the backend has already resolved the mechanics, such as an enemy attack against Bob during Alice's turn, Alice's own trusted player-attack damage against Bob, or a known battlefield hazard damaging Bob.

Change:
- Removed the narration-confirmed cross-player damage/participant-HP authorization path from the extractor.
- Limited `authorizedCrossActorChangeIds` ownership bypasses to the narrow safe allowlist already needed for named cross-player heals and combat XP rewards.
- Added a post-DM bridge that converts only backend-owned `dmContextPacket.combatState.enemyResolvedActions` packets with `hit: true`, positive `damageTotal`, a real enemy participant, and a real player target into canonical `health.damage` changes.
- Added a generic `trustedDamageEvents` bridge for server-resolved player attacks and environmental hazards. Player-attack events must come from the acting player; hazard events must reference a known battlefield hazard.
- Validated trusted damage changes separately from helper/narration output, then validated all untrusted changes with the current `expected_actor_id` lock.
- Added regressions proving narration-confirmed cross-player damage is filtered, while Alice's turn can still apply trusted enemy, player-attack, and hazard damage to Bob exactly once.

Rationale:
This keeps the ownership boundary intact: the LLM can narrate Bob taking damage, but the state system only mutates Bob when a server-owned mechanical packet says Bob took damage. The canonical `health.damage` path also syncs Bob's player row and combat participant HP without allowing redundant participant rewrites from narration.

## Verification

- `./.venv/bin/python -m pytest tests/test_auth.py::test_combat_operator_endpoints_require_workspace_admin_account tests/test_auth.py::test_bestiary_authoring_endpoints_require_workspace_admin_account`
  - Passed: 2 tests.
- `./.venv/bin/python -m py_compile aidm_server/blueprints/creatures.py tests/test_auth.py`
  - Passed.
- `git diff --check -- aidm_server/blueprints/creatures.py tests/test_auth.py`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py aidm_server/blueprints/creatures.py tests/test_auth.py`
  - Passed: no likely committed secrets found.
- `./.venv/bin/python -m pytest tests/test_auth.py`
  - Passed: 23 tests.
- `./.venv/bin/python -m pytest tests/test_creatures_combat.py::test_creature_deep_api_endpoints_for_pack_evolution_morale_and_debug tests/test_creatures_combat.py::test_combat_apply_state_changes_uses_request_scoped_ids_unless_keyed tests/test_creatures_combat.py::test_combat_apply_state_changes_rejects_stale_session_state_revision`
  - Passed: 3 tests.
- `./.venv/bin/python -m pytest tests/test_game_state_pipeline.py::test_post_dm_pipeline_applies_trusted_enemy_damage_to_non_acting_player tests/test_game_state_pipeline.py::test_post_dm_pipeline_applies_trusted_player_attack_damage_to_other_player tests/test_game_state_pipeline.py::test_post_dm_pipeline_applies_trusted_environment_hazard_damage_to_other_player tests/test_game_state_pipeline.py::test_post_dm_helper_filters_narration_confirmed_damage_to_other_player tests/test_game_state_pipeline.py::test_post_dm_helper_filters_narration_confirmed_cross_player_participant_hp_drop`
  - Passed: 5 tests.
- `./.venv/bin/python -m pytest tests/test_game_state_pipeline.py`
  - Passed: 165 tests.
- `./.venv/bin/python -m pytest tests/test_auth.py`
  - Passed: 23 tests.
- `./.venv/bin/python -m py_compile aidm_server/game_state/orchestration/turn_pipeline.py aidm_server/game_state/extraction/post_dm_outcome_extractor.py aidm_server/game_state/validation/validator.py aidm_server/blueprints/creatures.py tests/test_game_state_pipeline.py tests/test_auth.py`
  - Passed.
- `git diff --check -- aidm_server/game_state/orchestration/turn_pipeline.py aidm_server/game_state/extraction/post_dm_outcome_extractor.py aidm_server/game_state/validation/validator.py aidm_server/blueprints/creatures.py tests/test_game_state_pipeline.py tests/test_auth.py improvements_suggestions.md`
  - Passed.
- `./.venv/bin/python scripts/scan_secrets.py aidm_server/blueprints/creatures.py aidm_server/game_state/extraction/post_dm_outcome_extractor.py aidm_server/game_state/orchestration/turn_pipeline.py aidm_server/game_state/validation/validator.py tests/test_auth.py tests/test_game_state_pipeline.py improvements_suggestions.md`
  - Passed: no likely committed secrets found.

Note: one adjacent pytest selector from older automation memory was stale and no longer matched the current test name. I resolved it to the current `test_combat_apply_state_changes_uses_request_scoped_ids_unless_keyed` selector before recording the successful verification above.

## High-Priority Finding Resolved In The 07:33 MDT Follow-Up

### Resolved: LLM narration alone could authorize cross-player HP damage

Current evidence:
- `aidm_server/game_state/extraction/post_dm_outcome_extractor.py` no longer emits authorized cross-actor damage IDs based on narration confirmation.
- `aidm_server/game_state/validation/validator.py` no longer lets `authorizedCrossActorChangeIds` bypass ownership for `health.damage` or `combat.participant.update`.
- `aidm_server/game_state/orchestration/turn_pipeline.py` now creates cross-player damage only from trusted backend packets: `enemyResolvedActions` or explicit `trustedDamageEvents` for player attacks and environmental hazards.

Residual risk:
Future combat mechanics that need cross-player effects should use explicit server-generated state changes, not helper/model authorization IDs. The new tests should be extended if saves, resistance, area effects, or multi-target damage move into this path.

## High-Priority Findings Refreshed This Run

### High: Creature resolver still returns debug rankings to non-saving player previews

Current evidence:
- `aidm_server/blueprints/creatures.py` still allows `/api/creatures/resolve` with `saveGenerated: false` for workspace-visible campaign requests.
- `aidm_server/creatures/resolver.py` includes `debug` in every result and populates it with normalized request data plus campaign, region, and core ranking IDs/scores.

Risk:
The save-capable path is now gated, but the preview path can still reveal encounter-selection internals and potentially authored campaign/region creature IDs. That may be acceptable for a DM tool, but it is not clearly a player-safe response contract.

Recommended next step:
Split resolver responses into a player-safe preview shape and an operator debug shape. Add tests that assert player previews do not include `debug.rankings`, request echoes, generated model names, or hidden authored-catalog identifiers.

### Medium-High: Creature evolve-save authorization happens after evolution work

Current evidence:
- `aidm_server/blueprints/creatures.py` computes the evolved creature before checking `_bestiary_authoring_forbidden_response()` for campaign saves.

Risk:
Non-admin save attempts are correctly rejected before persistence, but the server still performs avoidable evolution/balance work before returning `403`. If evolution later becomes model-backed or more expensive, this becomes a resource-control and information-flow issue.

Recommended next step:
Preflight the authoring capability before `evolve_creature(...)` when a request includes `campaignId` and save is enabled, while preserving `saveGenerated: false` preview behavior.

### Medium-High: Public-beta session posture still needs hosted proof

Current evidence:
- `aidm_frontend/src/useRuntimeSettings.ts` has improved by moving auth/workspace tokens out of local storage, but it still stores account/workspace metadata in both session and local storage and keeps compatibility migration code for older browser state.
- The 2026-06-15 beta-hardening update still lists target-environment deployment readiness, hosted metrics, Socket.IO worker model proof, and provider backup/restore rehearsal as remaining infrastructure work.

Risk:
Local browser persistence may be fine for current table workflows, but hosted beta needs proof that cookie-auth, CSRF, logout cleanup, workspace switching, stale capability display, security headers, metrics, and Socket.IO affinity all behave in the real target environment.

Recommended next step:
Run the deployment readiness gate against the actual hosted/staging target and add a small frontend session-regression pass around logout, workspace switching, and stale admin capability display.

### Medium: Custom race authoring capability policy is still implicit

Current evidence:
- `aidm_server/blueprints/races.py` lets workspace-visible users generate, create, update, and delete custom races in the current workspace.

Risk:
This may be intentional player-authored homebrew, but it is a different policy from campaign bestiary authoring. Without a route capability matrix, it is hard to tell which creative content is player-owned, DM-authored, or workspace-admin-only.

Recommended next step:
Define the custom-race policy explicitly before changing behavior. If custom races are shared DM-authored catalog content, add role gates and non-admin 403 tests. If they are player-authored content, add ownership tests so one player cannot update/delete another player's draft.

## Larger Suggested Improvements

- Add a route capability matrix that classifies each mutable route as player action, player-owned authoring, DM authoring, runtime control, debug read, workspace admin, or server internal.
- Introduce a sanitized resolver DTO so debug fields are opt-in and tied to `debug_read`.
- Move authoring preflights before expensive generation/evolution work where save intent is known up front.
- Add contract tests for "player-safe preview" responses across creature resolver, custom races, and bestiary browsing.
- Run `make deployment-readiness` with the real staging/hosted env values and attach the output to the beta checklist.

## Recommended Next Run Focus

1. Preflight `/api/creatures/evolve` authoring checks before evolution work for save-enabled campaign requests.
2. Add player-safe resolver response tests and strip/split debug fields for non-operator previews.
3. Decide and test the custom-race ownership/capability policy.
4. Refresh hosted deployment readiness evidence instead of relying only on local gates.

# AIDM Beta-Hardening Implementation Update - 2026-06-15

Scope: implementation pass for the prior closed-beta hardening recommendations across route authorization, serialized session-state mutation, operator/player UI separation, frontend decomposition, accessibility coverage, observability, deployment readiness, release workflow, and beta feedback loops. Older dated audit entries below are preserved as historical evidence; items listed there as open may now be resolved by this pass.

## Implemented Since The 2026-06-14 Audit

- Added a central route capability layer and applied admin/local-operator gates to operator-grade combat and bestiary routes, including combat start, morale apply, combat-end apply, raw combat state changes, combat debug reads, campaign bestiary authoring/generation, and creature evolve/save flows.
- Added non-admin 403 and admin-success tests for the previously player-reachable combat and bestiary mutation surfaces.
- Added a shared session-state mutation service that acquires the per-session coordinator, reloads state inside the lock, validates/applies changes, persists with revision/audit metadata, and returns structured conflict responses.
- Routed player equipment and combat REST mutations through the shared mutation path, and wrapped campaign-pack progress service entrypoints in the same reentrant turn coordinator boundary.
- Added durable snapshot-diff audit rows for equipment, combat, and campaign-pack progress writes, plus a generic operator-action audit for bestiary authoring/generation/evolve-save, campaign/session archive/restore/delete, session import, and campaign-pack import.
- Split bestiary/debug behavior into player-safe browsing and operator-only authoring/debug surfaces, with backend capability responses driving frontend visibility while preserving graceful 403 handling.
- Extracted remaining App dialog orchestration into focused modules for worlds, campaigns, sessions, players, saved workspaces, shared modal shell, focus trap, and danger confirmation flows.
- Added modal accessibility regressions for focus placement, Escape behavior, focus trapping, focus return, accessible descriptions, and danger confirmation cancellation.
- Added bad-turn reporting tied to turn/provider/model metadata, plus operator-only beta incident and audit APIs for failed turns, failed canon jobs, telemetry incidents, tester reports, state mutations, and operator actions.
- Added deterministic scenario quality regressions for opening narration, impossible actions, combat roll prompts, item use, campaign-pack checkpoint triggers, NPC continuity, and canon recall.
- Added a safe-mode banner when the deterministic fallback provider is active.
- Added production cookie-auth posture support with HttpOnly account cookies, optional suppression of raw account tokens in account JSON, CSRF enforcement for unsafe cookie-authenticated REST requests, and required security headers.
- Added a hosted deployment readiness gate (`scripts/deployment_readiness_check.py`, `make deployment-readiness`) for production env, auth, secrets, CORS, DB-backed rate limiting/turn coordination, Socket.IO worker model proof, observability ownership, telemetry, security headers, cookie posture, fallback-provider posture, and optional live health/metrics/header checks.
- Added a static observability bundle validator (`scripts/check_observability_bundle.py`, `make observability-check`) covering Prometheus/Grafana files, dashboard metrics, provisioning paths, and optional `docker compose config`.
- Added an executable SQLite backup/restore drill (`scripts/backup_restore_drill.py`, `make backup-restore-drill`) and wired it into the closed-beta RC gate against an isolated migrated database.
- Added a closed-beta RC command/workflow, scenario gate, release checklist updates, production env example, issue templates, PR template, license, and changelog.

## Current Verification

- `make closed-beta-rc` passes with the observability check and backup/restore drill included.
- The latest full run included backend tests, smoke flow, scenario regressions, API type drift check, secret scan, Python dependency audit, frontend typecheck/lint/unit tests, build, bundle budget, frontend dependency audit, and browser smoke.
- Focused deployment-readiness, observability-check, audit-log, and coordinator serialization tests pass.
- `git diff --check` passes.

## Remaining Work That Requires Target Infrastructure

- Run `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> --target-url <target-url> --auth-token <token>"` against the actual hosted/staging target.
- Prove hosted metrics and alert ingestion at the selected observability provider with the named alert owner.
- Prove the chosen Socket.IO deployment model in staging, especially sticky-session affinity or shared message-queue delivery for multi-worker deployments.
- Run the Docker-backed observability validation on a Docker-capable release machine when that environment is available: `make observability-check OBSERVABILITY_CHECK_ARGS="--check-docker-compose --require-docker"`.
- For a hosted database, run and record the provider-specific backup/restore rehearsal; the bundled backup/restore drill covers local/private SQLite beta data only.
- Record the release checklist as checked evidence for the actual RC tag/build.

# Daily AIDM Codebase Improvement Audit - 2026-06-14 16:05 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: focused safe-improvement pass across backend combat authorization, combat debug robustness, adjacent auth tests, previously flagged bestiary/debug exposure, direct session-state mutation paths, frontend accessibility/security exposure points, developer workflow checks, and prior dated audit recommendations. The worktree was clean at the start of this run; this run only edited `aidm_server/blueprints/creatures.py`, `tests/test_auth.py`, and this report.

## What Was Inspected

- Automation memory for this recurring audit, especially the prior run's recommendation to add non-admin 403 coverage for `/combat/debug` and `/combat/apply-state-changes`.
- Prior audit sections in this file, especially the route-capability, bestiary/debug UI separation, and direct session snapshot writer findings.
- Backend app auth context and workspace role wiring:
  - `aidm_server/main.py`
  - `aidm_server/auth.py`
  - `aidm_server/workspace_access.py`
  - `aidm_server/blueprints/runtime_config.py`
- Backend combat and bestiary route surface:
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/blueprints/sessions.py`
  - `aidm_server/blueprints/campaigns.py`
- Existing role/capability tests:
  - `tests/test_auth.py`
  - `tests/test_campaign_pack_progress.py`
  - `tests/test_creatures_combat.py`
- Frontend exposure and accessibility scan around debug/admin controls:
  - `aidm_frontend/src/BestiaryDebugPanel.tsx`
  - `aidm_frontend/src/InspectorPanel.tsx`
  - `aidm_frontend/src/ActionComposer.tsx`
  - `aidm_frontend/src/useRuntimeSettings.ts`
- Developer workflow/security surfaces:
  - `Makefile`
  - `pytest.ini`
  - `scripts/scan_secrets.py`

## Small Safe Fixes Made

### Require workspace-admin accounts for raw combat state changes and combat debug logs

Affected files:
- `aidm_server/blueprints/creatures.py`
- `tests/test_auth.py`

Problem:
`/api/sessions/<session_id>/combat/apply-state-changes` and `/api/sessions/<session_id>/combat/debug` were protected by workspace visibility but not by account role. In an authenticated shared table, a normal player account in the workspace could reach a raw combat state mutation endpoint and inspect internal combat debug events.

Change:
- Added `_combat_operator_forbidden_response()` in `aidm_server/blueprints/creatures.py`.
- Preserved local unauthenticated operator mode by allowing requests with no account context.
- Allowed workspace-admin accounts to continue using the endpoints.
- Returned a normal `403 forbidden` response for non-admin accounts.
- Added `tests/test_auth.py::test_combat_state_and_debug_endpoints_require_workspace_admin_account` to verify player 403 responses and admin success responses.

Rationale:
This is a narrow security hardening change that follows the existing campaign-pack progress pattern: local operator mode still works, but once a real account is present, DM/operator-grade state mutation and debug inspection require workspace-admin role.

### Make combat debug limit parsing tolerant of malformed input

Affected files:
- `aidm_server/blueprints/creatures.py`
- `tests/test_auth.py`

Problem:
`/api/sessions/<session_id>/combat/debug?limit=invalid` used direct `int(...)` parsing and could raise a server error after the request passed authorization.

Change:
- Switched the debug endpoint to the repo's existing `coerce_int(..., 50)` helper.
- Kept the existing clamp behavior of `1..100`.
- Covered the malformed limit case in the new auth regression.

Rationale:
This is a one-line correctness fix in the same endpoint touched for security. It prevents a trivial malformed query from becoming a 500.

## Verification

- `./.venv/bin/python -m pytest tests/test_auth.py::test_combat_state_and_debug_endpoints_require_workspace_admin_account`
  - Passed.
- `./.venv/bin/python -m pytest tests/test_auth.py::test_combat_state_and_debug_endpoints_require_workspace_admin_account tests/test_auth.py::test_llm_config_update_requires_owner_account_admin_role`
  - Passed: 2 tests.
- `./.venv/bin/python -m py_compile aidm_server/blueprints/creatures.py tests/test_auth.py`
  - Passed.
- `git diff --check -- aidm_server/blueprints/creatures.py tests/test_auth.py`
  - Passed.
- `./.venv/bin/python -m pytest tests/test_auth.py`
  - Passed: 17 tests.
- `./.venv/bin/python -m pytest tests/test_creatures_combat.py::test_creature_deep_api_endpoints_for_pack_evolution_morale_and_debug tests/test_creatures_combat.py::test_combat_apply_state_changes_synthesizes_stable_ids_for_retries`
  - Passed: 2 tests.
- `./.venv/bin/python scripts/scan_secrets.py aidm_server/blueprints/creatures.py tests/test_auth.py`
  - Passed: no likely committed secrets found.

## High-Priority Findings Refreshed This Run

### High: Other combat mutation routes still need the same DM/admin capability boundary

Current evidence:
- `aidm_server/blueprints/creatures.py` now protects arbitrary combat state changes and raw debug reads.
- `aidm_server/blueprints/creatures.py` still leaves these state-changing combat routes at workspace-visibility level only:
  - `/api/sessions/<session_id>/combat/start`
  - `/api/sessions/<session_id>/combat/apply-morale-event`
  - `/api/sessions/<session_id>/combat/check-end` when `apply` is set
- The existing unauthenticated local-operator behavior is still required by local development and was preserved by this run.

Risk:
Authenticated player accounts could still start combat, apply morale changes, or apply combat-end state through REST routes if they know the session ID inside their workspace.

Recommended next step:
Extend the same helper to these combat state-changing routes, but do it with route-level tests first so player 403 behavior and local operator compatibility are explicit.

### High: Bestiary authoring and creature evolve/save routes remain player-reachable inside a workspace

Current evidence:
- `aidm_server/blueprints/creatures.py` still exposes campaign bestiary create, campaign bestiary generate/save, and creature evolve/save routes without a workspace-admin role check.
- The frontend bestiary/debug panel still blends browsing, campaign-pack bestiary seeding, and debug display in one inspector tab.

Risk:
A normal participant in a shared/public deployment could author or persist DM-facing creature content, and the UI does not yet clearly separate player-safe browsing from operator-only tools.

Recommended next step:
Add non-admin 403 tests for bestiary create/generate/save and creature evolve/save. Then gate those routes and drive frontend visibility from backend capability data instead of client-side assumptions.

### High: Direct session snapshot writers still bypass one serialized mutation boundary

Current evidence:
- Combat REST routes still persist session state directly after validation.
- Player equipment and campaign-pack progress services still have direct session snapshot write paths.
- These writes sit outside the normal streamed-turn coordinator boundary.

Risk:
REST/service writes can race active streamed turns and be overwritten by later turn persistence. The likely user-visible symptoms remain disappearing equipment changes, combat HP/status drift, and campaign-pack progress that appears to apply and later reverts.

Recommended next step:
Create a shared session-state mutation service that acquires the per-session coordinator, reloads inside the lock, applies validated changes, persists through one helper, and records revision/audit metadata. Start with a regression around manual equipment or combat HP updates surviving a simulated active turn.

### Medium-High: Frontend account/session persistence needs a security pass before public beta

Current evidence:
- `aidm_frontend/src/useRuntimeSettings.ts` intentionally uses browser storage for workspace/account runtime selection.
- The app has grown more account/role-aware, and operator-only controls now depend on server-side authorization instead of just hidden client UI.

Risk:
Local storage behavior may be acceptable for the current local table workflow, but before public beta the app should explicitly review token/session persistence, logout cleanup, cross-workspace switching, and stale admin capability display.

Recommended next step:
Document the expected browser storage threat model, add tests around logout/workspace switching cleanup, and prefer server capability checks for every operator-only action.

## Larger Suggested Improvements

- Promote the new combat helper into a broader route capability helper or decorator after the remaining combat and bestiary 403 tests exist.
- Add a route capability matrix covering `local_operator`, `workspace_admin`, `player`, `server_owned`, and `debug_read`.
- Split the frontend bestiary tab into player-safe browsing and operator-only authoring/debug sections.
- Add frontend tests that verify operator controls stay hidden or degrade quietly when the backend returns 403.
- Build a small `make audit-touched` or `make dev-check` target that runs py_compile, touched pytest, diff-check, and touched-file secret scan.
- Add a concurrency regression suite for direct session snapshot writers before attempting a shared mutation service refactor.

## Notes

- No live backend restart, Tailscale tunnel check, frontend build, or browser smoke was needed for this backend-only route hardening.
- This run intentionally did not change broader combat start/morale/check-end behavior because those routes need their own tests and have more gameplay-facing blast radius.
- The previous frontend debug fetch fallback means the UI should already tolerate the new 403 behavior for combat debug events.

## Recommended Next Run Focus

1. Add non-admin 403 tests for combat start, morale apply, and combat check-end with `apply: true`, then extend the same role helper if the tests prove the desired behavior.
2. Add non-admin 403 tests for bestiary create/generate/save and creature evolve/save before changing those routes.
3. Start the direct session-state writer concurrency regression with one equipment or combat HP overwrite case.
4. Audit frontend runtime account storage and operator-control rendering for stale capability displays.

# Daily AIDM Codebase Improvement Audit - 2026-06-14 06:06 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: focused safe-improvement pass across frontend bestiary/debug UX, accessibility semantics, backend route capability boundaries, direct session-state writers, campaign-pack encounter-group work already in the worktree, tests, security scanning, and developer workflow checks. The worktree already contained unrelated pending changes in `aidm_server/blueprints/creatures.py`, `aidm_server/game_state/campaign_pack_encounters.py`, `docs/campaign_packs.md`, `docs/examples/the_road_of_unremembered_kings_campaign.json`, `scripts/launch_desktop_app.sh`, `tests/test_campaign_pack_linter.py`, and untracked `tests/test_campaign_pack_encounter_groups.py`; this run preserved those changes and only edited the bestiary frontend/test/style files plus this report.

## What Was Inspected

- Prior dated audit sections in `improvements_suggestions.md`, especially the recurring recommendations around combat/bestiary route authorization, debug payload exposure, direct session snapshot writes, and bestiary UI semantics.
- Frontend bestiary/debug panel:
  - `aidm_frontend/src/BestiaryDebugPanel.tsx`
  - `aidm_frontend/src/BestiaryDebugPanel.test.tsx`
  - `aidm_frontend/src/styles/inspector.css`
  - `aidm_frontend/src/InspectorPanel.tsx`
- Backend combat and bestiary route surface:
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/workspace_access.py`
  - `aidm_server/blueprints/sessions.py`
- Direct session snapshot mutation paths:
  - `aidm_server/blueprints/players.py`
  - `aidm_server/services/campaign_pack_progress.py`
- Existing campaign-pack encounter-group changes already present in the worktree:
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/game_state/campaign_pack_encounters.py`
  - `tests/test_campaign_pack_encounter_groups.py`
  - `tests/test_campaign_pack_linter.py`
- Developer workflow and safety checks:
  - `Makefile`
  - `aidm_frontend/package.json`
  - `scripts/scan_secrets.py`

## Small Safe Fixes Made

### Keep bestiary browsing available when optional combat debug is unavailable

Affected files:
- `aidm_frontend/src/BestiaryDebugPanel.tsx`
- `aidm_frontend/src/BestiaryDebugPanel.test.tsx`

Problem:
`BestiaryDebugPanel` loaded core bestiary, campaign bestiary, and combat debug events in one `Promise.all`. Combat debug data is optional, but any debug endpoint error would fail the whole panel and hide normal bestiary browsing. That is especially fragile because combat debug payloads should become admin/DM-only on the backend.

Change:
- Wrapped only the combat debug request in a local fallback that returns `{ events: [] }` when the optional debug fetch fails.
- Preserved normal error handling for core and campaign bestiary requests.
- Added a regression test proving a debug fetch failure does not show an error, does not render debug details, and still leaves bestiary browsing usable.

Rationale:
This is a narrow UX/security-prep fix. It makes future backend restriction of raw combat debug data less disruptive without weakening any current backend behavior.

### Replace invalid bestiary listbox semantics with a normal list of buttons

Affected files:
- `aidm_frontend/src/BestiaryDebugPanel.tsx`
- `aidm_frontend/src/styles/inspector.css`

Problem:
The bestiary creature picker declared `role="listbox"` but rendered plain buttons instead of ARIA `option` children and did not implement listbox keyboard behavior. That creates invalid semantics and unpredictable assistive-technology behavior.

Change:
- Replaced the fake listbox container with a semantic `<ul aria-label="Bestiary creatures">`.
- Wrapped each creature button in an `<li>`.
- Added `aria-pressed` to expose the selected creature button state.
- Adjusted the list CSS to remove browser default bullets/spacing and keep the compact layout.

Rationale:
This is a low-risk accessibility correction that matches the component's actual interaction model: a short list of independent selectable buttons.

## Verification

- `npm run test:unit -- BestiaryDebugPanel.test.tsx`
  - Passed: 1 test file, 2 tests.
- `npm run typecheck`
  - Passed.
- `npm run lint -- src/BestiaryDebugPanel.tsx`
  - Passed.
- `git diff --check -- aidm_frontend/src/BestiaryDebugPanel.tsx aidm_frontend/src/BestiaryDebugPanel.test.tsx aidm_frontend/src/styles/inspector.css`
  - Passed.
- `./.venv/bin/python -m pytest tests/test_campaign_pack_encounter_groups.py tests/test_campaign_pack_linter.py`
  - Passed: 7 tests.
- `./.venv/bin/python scripts/scan_secrets.py aidm_frontend/src/BestiaryDebugPanel.tsx aidm_frontend/src/BestiaryDebugPanel.test.tsx aidm_frontend/src/styles/inspector.css`
  - Passed: no likely committed secrets found.

## High-Priority Findings Refreshed This Run

### Critical: Combat mutation, bestiary authoring, and combat debug routes still need DM/admin capability checks

Current evidence:
- `aidm_server/blueprints/creatures.py:352-379` creates campaign/region bestiary entries.
- `aidm_server/blueprints/creatures.py:382-411` generates and optionally saves campaign bestiary packs.
- `aidm_server/blueprints/creatures.py:458-493` evolves a creature and can save it to campaign/session bestiary scope.
- `aidm_server/blueprints/creatures.py:518-614` starts combat, persists combat state, syncs the combat encounter record, and records debug payloads.
- `aidm_server/blueprints/creatures.py:629-656` applies morale events directly to persisted combat state.
- `aidm_server/blueprints/creatures.py:659-690` can apply combat-end state and campaign-pack progress when `apply` is set.
- `aidm_server/blueprints/creatures.py:693-720` accepts arbitrary combat state changes and persists them.
- `aidm_server/blueprints/creatures.py:723-748` returns raw combat debug events.
- `aidm_server/blueprints/sessions.py:570-579` already demonstrates the desired pattern for campaign-pack progress control by rejecting non-admin operators.

Risk:
These routes perform source-of-truth DM/admin actions or expose raw internal debug payloads. A normal table participant in a shared/public deployment could potentially mutate combat state, author hidden bestiary content, start/end combat, or inspect internals.

Recommended next step:
Add route-level tests first: normal player should receive 403 for combat debug, combat start, arbitrary combat state changes, combat-end apply, morale apply, bestiary create/generate/save, and creature evolve/save. Then implement the smallest shared `workspace admin or local unauthenticated owner mode` helper that makes those tests pass.

### High: Bestiary browsing, authoring, and debug UI still share one player-facing panel

Current evidence:
- `aidm_frontend/src/InspectorPanel.tsx` exposes the Bestiary tab through the general inspector.
- `aidm_frontend/src/BestiaryDebugPanel.tsx:147-150` still attempts to load combat debug events when a session is selected, though this run made failures non-blocking.
- `aidm_frontend/src/BestiaryDebugPanel.tsx:263-274` renders campaign-pack seeding controls in the same panel as normal browsing.
- `aidm_frontend/src/BestiaryDebugPanel.tsx:329-338` renders recent combat debug summaries when the backend returns them.

Risk:
Even with the debug fallback fixed, the UI still blends player-safe catalog browsing with DM/admin authoring and debug tools. That makes authorization harder to reason about and increases accidental exposure risk.

Recommended next step:
Split the component into player-safe browsing and operator-only authoring/debug sections. Drive visibility from backend capability fields after backend route gates exist, not from client-side assumptions alone.

### High: Direct session snapshot writers still bypass one serialized mutation boundary

Current evidence:
- `aidm_server/blueprints/players.py:425-462` manually reads session state, applies inventory equipment changes, and writes `session_obj.state_snapshot`.
- `aidm_server/services/campaign_pack_progress.py:180-208` writes migrated/progressed campaign-pack state directly.
- `aidm_server/services/campaign_pack_progress.py:396-407` writes manually controlled campaign-pack progress directly.
- `aidm_server/blueprints/creatures.py:601`, `:649`, `:674`, and `:705` persist combat/session changes from REST routes outside the normal socket turn coordinator.

Risk:
REST/service writes can still race with active streamed turns and be overwritten by later post-DM persistence. The highest-risk visible symptoms remain equipment, HP/combat state, campaign-pack progress, and projection/canon refreshes that appear applied but later disappear.

Recommended next step:
Create one shared session-state mutation service that acquires the per-session coordinator, reloads inside the lock, applies validated changes, persists through one helper, and records revision/audit metadata. Start with a regression around manual equipment changes surviving a simulated active turn.

### Medium-High: Campaign-pack encounter group support now has duplicated parsing helpers

Current evidence:
- The existing dirty worktree adds `_encounter_enemy_specs` in both `aidm_server/blueprints/creatures.py` and `aidm_server/game_state/campaign_pack_encounters.py`.
- Targeted tests in `tests/test_campaign_pack_encounter_groups.py` and `tests/test_campaign_pack_linter.py` passed in this run.

Risk:
The behavior currently looks tested, but duplicated parsing logic can drift between the API preview path and the materialized combat-start path. A future change to `enemyGroups`, `enemyIds`, or count precedence could update one path and leave the other inconsistent.

Recommended next step:
After the current campaign-pack work settles, move encounter enemy-spec parsing into one shared campaign-pack utility and keep both API and materializer tests pointed at that shared behavior.

## Larger Suggested Improvements

- Define a route capability matrix for `workspace_admin`, `dm`, `player`, `local_debug`, and `server_owned` operations; enforce it with a small shared helper/decorator.
- Split player-safe bestiary browsing from operator-only campaign bestiary authoring and combat debug inspection.
- Route every `Session.state_snapshot` write through a coordinated mutation service with idempotency, optimistic revision checks, and audit metadata.
- Add a frontend test that simulates backend 403 for operator-only bestiary/debug controls once route capabilities exist.
- Add accessibility coverage for inspector panels, especially tab semantics, selected button states, and compact scrollable lists.
- Consider a compact `make dev-check` target for touched-file secret scan, focused pytest, frontend typecheck/lint, and API contract checks once the current dirty campaign-pack worktree is merged or shelved.

## Notes

- No live backend restart, Tailscale tunnel check, or browser smoke was needed for these frontend-only fixes.
- The campaign-pack encounter-group changes already in the worktree were not edited by this run, but their targeted tests passed.
- The frontend bestiary panel now tolerates a future backend 403 for combat debug events, but the backend route is still not protected.

## Recommended Next Run Focus

1. Add non-admin 403 tests for `/api/sessions/<id>/combat/debug` and `/api/sessions/<id>/combat/apply-state-changes`, then implement the smallest shared capability helper needed to make them pass.
2. Extend the same capability pattern to bestiary create/generate/save and creature evolve/save routes.
3. Add the first concurrency-focused regression for manual equipment updates during an active streamed turn before designing the shared session-state mutation service.
4. Consolidate campaign-pack encounter enemy-spec parsing after the current campaign-pack branch/worktree changes are ready to modify.

# Daily AIDM Codebase Improvement Audit - 2026-06-13 16:04 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: focused safe-improvement pass across combat API idempotency, route/security boundaries, direct session snapshot writers, frontend bestiary/debug UX, accessibility semantics, and developer workflow checks. The worktree already contained unrelated pending changes in `aidm_frontend/src/ActionComposer.tsx`, `aidm_frontend/src/App.tsx`, `aidm_frontend/src/DiceRollDialog.tsx`, plus an untracked campaign example JSON; this run preserved those changes and only edited the combat API route and its regression test.

## What Was Inspected

- Prior dated audit sections in `improvements_suggestions.md`, especially the recommended focus around `/combat/apply-state-changes`, id-less submitted changes, route authorization, and serialized session-state writes.
- Combat API mutation surface:
  - `aidm_server/blueprints/creatures.py`
  - `aidm_server/game_state/validation/validator.py`
  - `aidm_server/game_state/application/applier.py`
  - `aidm_server/game_state/models.py`
- Combat endpoint regression coverage in `tests/test_creatures_combat.py`, especially campaign-pack combat start/end, morale, debug-event, and arbitrary state-change API tests.
- Session snapshot mutation paths:
  - `aidm_server/blueprints/players.py`
  - `aidm_server/services/campaign_pack_progress.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`
  - `aidm_server/turn_engine.py`
- Frontend bestiary/debug and accessibility exposure:
  - `aidm_frontend/src/BestiaryDebugPanel.tsx`
  - `aidm_frontend/src/InspectorPanel.tsx`
  - `aidm_frontend/src/ActionComposer.tsx`
  - `aidm_frontend/src/App.tsx`
- Developer workflow and security scan surfaces:
  - `Makefile`
  - `pytest.ini`
  - `aidm_frontend/package.json`
  - `scripts/scan_secrets.py`

## Small Safe Fix Made

### Synthesize deterministic IDs for id-less combat API state changes

Affected files:
- `aidm_server/blueprints/creatures.py`
- `tests/test_creatures_combat.py`

Problem:
`/api/sessions/<session_id>/combat/apply-state-changes` accepted caller-supplied state changes and forwarded them directly into validation. When a submitted change omitted `id`, the existing ledger protection could not reject retries, so an API retry or client double-submit could record another applied change even though it represented the same mutation.

Change:
- Added `_combat_api_changes_with_ids`, which preserves caller-provided `id`/`changeId` values and synthesizes a stable `chg_...` ID only when the route receives an id-less change.
- The generated key is scoped to the session, this API boundary, the batch index, and a canonical JSON fingerprint of the submitted change excluding ID aliases.
- Updated `apply_session_combat_changes` to normalize submitted changes before validation.
- Added a regression test that posts the same id-less combat participant update twice, verifies the first request applies with a generated ID, verifies the second request is rejected as already applied, and confirms the ledger contains only one entry for that generated ID.

Rationale:
This is a narrow correctness/security hardening fix at an untrusted mutation boundary. It does not alter clients that already send IDs, and it uses the existing validator/applier idempotency path rather than inventing new dedupe behavior. Including the batch index avoids unexpectedly collapsing two identical id-less changes inside a single submitted batch.

Verification:
- `.venv/bin/python -m py_compile aidm_server/blueprints/creatures.py tests/test_creatures_combat.py`
- `.venv/bin/python -m pytest tests/test_creatures_combat.py -k "stable_ids_for_retries or campaign_pack_combat_end_advances_encounter_checkpoint"`
- `.venv/bin/python -m pytest tests/test_creatures_combat.py`
- `git diff --check -- aidm_server/blueprints/creatures.py tests/test_creatures_combat.py`
- `.venv/bin/python scripts/scan_secrets.py aidm_server/blueprints/creatures.py tests/test_creatures_combat.py`

Result:
All targeted checks passed: 2 focused endpoint tests, 70 creature/combat tests, diff whitespace check, py-compile, and touched-file secret scan.

## High-Priority Findings Refreshed This Run

### Critical: Combat mutation, bestiary authoring, and debug routes still need DM/admin capability checks

Current evidence:
- `aidm_server/blueprints/creatures.py:348-377` generates and optionally saves campaign bestiary packs.
- `aidm_server/blueprints/creatures.py:424-459` evolves a creature and can save it to campaign/session bestiary scope.
- `aidm_server/blueprints/creatures.py:484-580` starts combat, persists combat state, syncs the combat encounter record, and records debug payloads.
- `aidm_server/blueprints/creatures.py:595-622` applies morale events directly to persisted combat state.
- `aidm_server/blueprints/creatures.py:625-656` can apply combat-end state and campaign-pack progress when `apply` is set.
- `aidm_server/blueprints/creatures.py:659-686` accepts arbitrary combat state changes and persists them.
- `aidm_server/blueprints/creatures.py:689-712` returns combat debug event payloads.

Risk:
These are source-of-truth DM/admin actions, but the route layer still exposes them like general session APIs. In a shared table or public deployment, a normal participant could potentially start/end combat, mutate enemy state, seed/evolve hidden bestiary content, or inspect debug internals.

Recommended next step:
Add route-level capability checks and tests before changing all behavior broadly. Start with non-admin 403 coverage for combat debug, combat start, arbitrary combat state mutation, combat-end apply, morale apply, bestiary generation/save, and creature evolve/save routes. Then add an explicit local trusted-development bypass if needed.

### High: Direct session snapshot writers still bypass one serialized mutation boundary

Current evidence:
- `aidm_server/turn_engine.py:876` serializes normal socket turns with `session_turn_coordinator.serialized`.
- `aidm_server/blueprints/players.py:421-462` handles manual equip/unequip by reading state, applying changes, and writing `session_obj.state_snapshot` outside that coordinator.
- `aidm_server/services/campaign_pack_progress.py:181` and `:208` write migrated/progressed campaign-pack state directly.
- `aidm_server/blueprints/creatures.py:567`, `:615`, `:640`, and `:671` persist combat/session changes directly through the route helper.
- `aidm_server/game_state/orchestration/turn_pipeline.py:1047-1050`, `:1181`, and `:1288-1291` persist normal turn snapshots from captured state.

Risk:
Out-of-band REST/service writes can still race with an active streamed turn and be overwritten by a later post-DM persistence step. The risk is highest for equipment, HP, combat state, campaign-pack progress, and projection/canon refreshes.

Recommended next step:
Create a shared session-state mutation service that acquires the same per-session coordinator, reloads inside the lock, applies validated changes, persists through one helper, and records version/audit metadata. Add a regression that a manual equipment or campaign-pack progress change made during a simulated turn survives final post-DM persistence.

### High: Id-less state changes are now hardened for combat API submissions, but other public mutation boundaries still need review

Current evidence:
- This run normalized id-less submitted changes for `aidm_server/blueprints/creatures.py:659-686`.
- The lower-level validator still only rejects already-applied and duplicate-in-batch IDs when a non-empty ID is present.
- The applier still only skips already-applied changes when `change_id` is non-empty.

Risk:
The most obvious combat API retry path is now covered, but any other public or service boundary that forwards id-less state changes can still bypass ledger dedupe. Centralizing ID requirements would make retries safer across inventory, currency, XP, scene, NPC, and combat updates.

Recommended next step:
Audit every external route/service that calls `validate_state_changes` or `apply_state_changes`. Either require an ID at untrusted boundaries or synthesize a deterministic scoped ID before validation, with route-specific tests.

### Medium-High: Player-facing bestiary UI still mixes catalog browsing with debug/authoring powers

Current evidence:
- `aidm_frontend/src/InspectorPanel.tsx:251-259` exposes the Bestiary tab in the general inspector.
- `aidm_frontend/src/BestiaryDebugPanel.tsx:147-154` fetches combat debug events alongside normal bestiary data.
- `aidm_frontend/src/BestiaryDebugPanel.tsx:203-219` seeds campaign-pack bestiary entries from the same panel.
- `aidm_frontend/src/BestiaryDebugPanel.tsx:325-335` renders recent combat debug summaries.

Risk:
Even after backend authorization is added, the current UI blends player-safe catalog browsing with DM/debug tools. That increases accidental exposure risk and makes it harder to reason about what normal players should see.

Recommended next step:
Split bestiary browsing from DM/admin authoring/debug tools. Drive visibility from explicit backend capability fields rather than client-side convention.

## Larger Suggested Improvements

- Define a route capability matrix for `workspace_admin`, `dm`, `player`, `local_debug`, and `server_owned` actions; enforce it in a shared decorator/helper.
- Route every `Session.state_snapshot` write through a coordinated mutation service with fresh reload, idempotency policy, optimistic revision/audit metadata, and one persistence path.
- Make state change identity mandatory or deterministic at all public mutation boundaries.
- Split combat debug payloads into player-safe summaries and admin-only raw/debug records.
- Add adversarial tests for non-admin combat mutation, debug redaction, bestiary authoring, id-less duplicate submissions outside combat, and snapshot races during streamed turns.
- Add a compact `make dev-check` target that runs script syntax checks, touched-file secret scan, focused backend tests, generated API type check, and frontend typecheck/lint when dependencies are present.
- Clean up bestiary list semantics: `BestiaryDebugPanel` currently uses a `listbox` for catalog browsing, but the content behaves more like a selectable list of cards/details. Prefer plain list/listitem semantics or a fully keyboard-managed listbox.

## Notes

- No live backend restart, Tailscale tunnel check, or browser smoke was needed for this backend API idempotency fix.
- Existing unrelated frontend changes and the untracked campaign JSON were left untouched.
- Broader route authorization and serialized mutation changes were intentionally left as report items because they need role semantics and larger regression coverage.

## Recommended Next Run Focus

1. Add non-admin rejection tests for `/api/sessions/<id>/combat/debug` and `/combat/apply-state-changes`, then implement the smallest shared capability helper needed to make them pass.
2. Audit other direct callers of `validate_state_changes`/`apply_state_changes` for id-less external input and add deterministic IDs where the boundary is untrusted.
3. Add a race-focused regression around manual equipment changes during an active streamed turn before designing the shared mutation service.

# Daily AIDM Codebase Improvement Audit - 2026-06-13 06:04 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: focused safe-improvement pass across combat correctness, state mutation integrity, route/security boundaries, frontend debug/UX exposure, tests, and developer workflow. The worktree already contained unrelated pending changes in prompt/provider/runtime/frontend contract areas; this run preserved those changes and only edited the combat validation/application path plus its regression test.

## What Was Inspected

- Prior audit section in `improvements_suggestions.md`, especially the recommended next-run focus around unknown `combat.ability.mark_used` behavior.
- Combat state validation and application:
  - `aidm_server/game_state/validation/validator.py`
  - `aidm_server/game_state/application/applier.py`
  - `tests/test_creatures_combat.py`
  - `tests/test_game_state_pipeline.py`
- Combat and bestiary route surface:
  - `aidm_server/blueprints/creatures.py`
  - Route groups for bestiary generation/save, combat start, morale, combat-end apply, arbitrary combat state changes, and combat debug events.
- Live session snapshot mutation paths:
  - `aidm_server/blueprints/players.py`
  - `aidm_server/services/campaign_pack_progress.py`
  - `aidm_server/services/campaign_pack.py`
  - `aidm_server/services/campaign_pack_storage.py`
  - `aidm_server/turn_control.py`
  - `aidm_server/canon_projection.py`
  - `aidm_server/game_state/orchestration/turn_pipeline.py`
- Frontend bestiary/debug exposure and basic accessibility semantics:
  - `aidm_frontend/src/BestiaryDebugPanel.tsx`
  - `aidm_frontend/src/InspectorPanel.tsx`
  - `aidm_frontend/src/SessionBoard.tsx`
  - `aidm_frontend/src/App.tsx`
- Existing developer workflow and verification commands available in `Makefile`, `pytest.ini`, and frontend package scripts.

## Small Safe Fix Made

### Reject unknown combat ability usage before ledger/application

Affected files:
- `aidm_server/game_state/validation/validator.py`
- `aidm_server/game_state/application/applier.py`
- `tests/test_creatures_combat.py`

Problem:
`combat.ability.mark_used` required an `abilityId`, but validation did not confirm that the ability existed on the resolved combat participant. The applier also returned the participant even when no ability matched, so a caller could produce an applied ledger entry that looked like a successful ability use while no cooldown, use count, or `lastUsedRound` changed.

Change:
- Validation now resolves the participant and rejects `combat.ability.mark_used` when the requested `abilityId` is absent from that participant's `abilities`.
- The applier fallback now returns `None` when the participant exists but the ability is missing, so direct internal callers that bypass validation skip the change instead of recording it.
- Added a regression test proving an unknown wolf ability is rejected and does not append a ledger entry.

Rationale:
This is a narrow correctness fix with low product risk. Valid ability usage still follows the existing path; invalid ability usage stops earlier and more honestly. It also supports future cooldown and combat legality work by making ability-use records trustworthy.

Verification:
- `.venv/bin/python -m py_compile aidm_server/game_state/validation/validator.py aidm_server/game_state/application/applier.py`
- `.venv/bin/python -m pytest tests/test_creatures_combat.py -k "fine_combat_changes or combat_ability_mark_used"`
- `.venv/bin/python -m pytest tests/test_creatures_combat.py`
- `.venv/bin/python -m pytest tests/test_game_state_pipeline.py`
- `git diff --check -- aidm_server/game_state/validation/validator.py aidm_server/game_state/application/applier.py tests/test_creatures_combat.py`

Result:
All targeted checks passed: 2 focused combat tests, 67 creature/combat tests, and 151 state-pipeline tests.

## High-Priority Findings Refreshed This Run

### Critical: Combat mutation, bestiary authoring, and debug routes still need DM/admin capability checks

Current evidence:
- `aidm_server/blueprints/creatures.py:318-347` can generate and save campaign bestiary packs.
- `aidm_server/blueprints/creatures.py:394-429` can evolve a creature and save it to campaign/session bestiary scope.
- `aidm_server/blueprints/creatures.py:454-550` starts combat and persists the new combat state.
- `aidm_server/blueprints/creatures.py:565-592` applies morale events directly to session combat state.
- `aidm_server/blueprints/creatures.py:595-626` can apply combat-end state and campaign-pack progress when `apply` is set.
- `aidm_server/blueprints/creatures.py:629-655` accepts caller-supplied combat state changes and persists them.
- `aidm_server/blueprints/creatures.py:658-680` returns combat debug event payloads.

Risk:
These are DM/admin/source-of-truth actions, but the route surface still reads like a general workspace/session API. In shared tables or public deployments, a normal participant could potentially start or end combat, mutate enemy state, seed/evolve hidden bestiary content, or inspect debug internals.

Recommended next step:
Add route-level capability checks and tests before changing behavior broadly. Start with non-admin 403 coverage for combat debug, combat start, arbitrary combat state mutation, combat-end apply, morale apply, and bestiary generation/save/evolve routes. Then add an explicit DM/admin escape hatch for local trusted development.

### High: Direct session snapshot writers still bypass one serialized mutation boundary

Current evidence:
- `aidm_server/turn_engine.py:876` serializes normal socket turns with `session_turn_coordinator.serialized`.
- `aidm_server/blueprints/players.py:421-462` reads a session/player equipment state, applies inventory equip/unequip, and commits `session_obj.state_snapshot` outside that coordinator.
- `aidm_server/services/campaign_pack_progress.py:181` and `:208` write migrated/progressed campaign-pack state directly.
- `aidm_server/blueprints/creatures.py:537`, `:585`, `:610`, and `:640` persist combat/session changes directly through the route helper.
- `aidm_server/game_state/orchestration/turn_pipeline.py:1048-1050`, `:1181`, and `:1288-1291` persist the normal turn snapshots from captured `state_before_dm`/`final_state`.

Risk:
Out-of-band writes can still race with an active streamed turn and be overwritten by a later post-DM persistence step. This is most dangerous for inventory/equipment, HP, combat, campaign-pack progress, and canon/projection updates.

Recommended next step:
Create a shared session-state mutation service that acquires the same per-session coordinator, reloads inside the lock, applies validated changes, persists through one helper, and records version/audit metadata. Add a regression that equipment or campaign-pack progress changed during a simulated turn survives final post-DM persistence.

### High: Id-less state changes remain repeatable outside ledger protection

Current evidence:
- `aidm_server/game_state/validation/validator.py:2670-2674` only checks already-applied and duplicate-in-batch IDs when `change_id` is non-empty.
- `aidm_server/game_state/application/applier.py:1238-1240` only skips already-applied changes when `change_id` is non-empty.
- Some routes synthesize stable IDs, but the generic mutation boundary still accepts id-less changes from callers that reach it.

Risk:
Retries, double-clicks, or direct API submissions can duplicate effects if they omit IDs. The state ledger then cannot explain, dedupe, or audit the repeated mutation.

Recommended next step:
Require or synthesize deterministic IDs at untrusted mutation boundaries before validation. For direct API routes, derive a scoped idempotency key from session, route/source, actor/participant, change type, target, amount, and relevant state revision.

### Medium-High: Player-facing bestiary UI still mixes catalog browsing with debug/authoring powers

Current evidence:
- `aidm_frontend/src/InspectorPanel.tsx:251-259` exposes the Bestiary tab in the general inspector.
- `aidm_frontend/src/BestiaryDebugPanel.tsx:147-154` fetches combat debug events alongside normal bestiary data.
- `aidm_frontend/src/BestiaryDebugPanel.tsx:203-219` seeds campaign-pack bestiary entries from the same panel.
- `aidm_frontend/src/BestiaryDebugPanel.tsx:325-335` renders recent combat debug summaries in that UI.
- `aidm_frontend/src/InspectorPanel.tsx:597-605` can render combat resolver/debug metadata when debug mode is enabled.

Risk:
Even with backend authorization fixed later, the UX currently blends player-visible catalog browsing with DM/debug tools. That makes accidental exposure and mistaken product expectations more likely.

Recommended next step:
Split the bestiary into player-safe read-only browsing and DM/admin tools. Hide debug event fetches and campaign-pack seeding behind explicit role/capability props from the runtime config/API, not just client-side convention.

## Larger Suggested Improvements

- Define a capability matrix for `workspace_admin`, `dm`, `player`, `local_debug`, and `server_owned` actions, then enforce it in route decorators or a shared policy helper.
- Route all `Session.state_snapshot` writes through one mutation service with the session coordinator, fresh reload, idempotency requirements, and structured audit metadata.
- Make state change identity mandatory at all public mutation boundaries; keep helper/extractor-generated stable IDs as the normal path.
- Split combat debug payloads into player-safe summaries and admin-only raw debug events.
- Add adversarial tests for non-admin combat mutation, debug redaction, bestiary authoring, id-less duplicate submissions, and out-of-band session writes during a turn.
- Add a compact `make dev-check` target that runs script syntax checks, secret scan, focused backend workflow tests, API type generation check, and frontend typecheck/lint when dependencies are present.
- Consider accessibility cleanup for composite widgets in inspector panels: use true list/listitem semantics or plain button groups instead of partially implemented listbox patterns.

## Notes

- No live backend restart, Tailscale tunnel check, or browser smoke was needed for this backend validation/application fix.
- Existing unrelated changes in frontend/API contract/runtime/prompt files were left untouched.
- The broader route authorization and session mutation findings were intentionally not patched in this run because they need role semantics, migration-safe tests, and product decisions around local trusted play versus multiplayer deployments.

## Recommended Next Run Focus

1. Add non-admin rejection tests for `/api/sessions/<id>/combat/debug` and `/combat/apply-state-changes`; implement the smallest shared capability helper needed to make them pass.
2. Add deterministic ID synthesis or rejection at `combat/apply-state-changes` for id-less submitted changes.
3. Add a regression around equipment changes during an active turn to pin down the session snapshot race before designing the shared mutation service.

# Daily AIDM Codebase Improvement Audit - 2026-06-12 21:34 MDT

Automation ID: `daily-aidm-codebase-improvement-audit`

Scope: small safe improvement pass across developer workflow, correctness guardrails, security findings, state mutation architecture, tests, and frontend/admin UX boundaries. This pass intentionally avoided broad combat/security rewrites because the current risks need explicit product/role decisions and more comprehensive regression coverage.

## What Was Inspected

- Existing root audit report in `improvements_suggestions.md`, especially the combat mutation, state race, and idempotency findings.
- Developer workflow entrypoints: `Makefile`, `.pre-commit-config.yaml`, `scripts/check_local_health.sh`, `scripts/run_local_backend.sh`, `scripts/run_unified_local.sh`, `scripts/launch_backend_service.sh`, and `scripts/launch_frontend_service.sh`.
- Secret-scan workflow: `scripts/scan_secrets.py` and `tests/test_secret_scan.py`.
- Local generated artifact hygiene via `git status --short --untracked-files=all`, `git ls-files`, and ignored cache checks.
- Current high-risk combat and state mutation evidence in `aidm_server/blueprints/creatures.py`, `aidm_server/turn_engine.py`, `aidm_server/game_state/validation/validator.py`, and `aidm_server/game_state/application/applier.py`.
- README quick-start and frontend package scripts to understand the intended local verification path.

## Small Safe Fix Made

### Make `scripts/check_local_health.sh` repo-root safe

Affected files:
- `scripts/check_local_health.sh`
- `tests/test_check_local_health.py`

Problem:
`scripts/check_local_health.sh` invoked `.venv/bin/python` relative to the caller's current working directory. It worked through `make health` from the repository root, but it was fragile when run directly from another directory, from automation, or from service wrappers. The Python import block also depended on the current directory being the repo root.

Change:
- Added `REPO_ROOT` detection using the script location.
- Added `AIDM_PYTHON` override support, defaulting to `${REPO_ROOT}/.venv/bin/python`.
- Added a clear missing-Python error with the expected install action.
- Changed the script to `cd "${REPO_ROOT}"` before the Python config check.
- Added a focused pytest that runs the health script from a temporary non-repo working directory, stubs `curl`, sets `AIDM_PYTHON` to the active test interpreter, and verifies the expected health URLs.

Rationale:
This is a developer-workflow correctness fix with low product risk. It does not change which endpoints are checked, does not touch runtime state, and is easy to verify without starting backend/frontend services.

Verification:
- `bash -n scripts/check_local_health.sh`
- `for file in scripts/*.sh; do bash -n "$file" || exit 1; done`
- `.venv/bin/python -m pytest tests/test_check_local_health.py`
- `.venv/bin/python -m pytest tests/test_secret_scan.py tests/test_check_local_health.py`
- `.venv/bin/python scripts/scan_secrets.py scripts/check_local_health.sh tests/test_check_local_health.py`

Result:
All targeted checks passed.

## High-Priority Findings Refreshed This Run

### Critical: Combat mutation and debug endpoints still need DM/admin gating

Current evidence:
- `aidm_server/blueprints/creatures.py:454-550` starts combat and persists a new combat snapshot from the API.
- `aidm_server/blueprints/creatures.py:565-592` applies morale events directly.
- `aidm_server/blueprints/creatures.py:595-626` can apply combat-end state when requested.
- `aidm_server/blueprints/creatures.py:629-655` accepts caller-supplied combat state changes and persists them.
- `aidm_server/blueprints/creatures.py:658-670` exposes combat debug events.

Risk:
Workspace/session visibility is still too broad for DM/admin/debug powers. In public or semi-public play, these endpoints can let ordinary participants alter combat state or inspect internals that should remain hidden.

Recommended next step:
Add route-level capability checks and tests before changing endpoint behavior. Start with non-admin 403 coverage for combat debug, combat start, combat state mutation, morale, combat-end apply, and bestiary generation/save routes.

### High: Direct session snapshot writers still bypass the normal turn coordinator

Current evidence:
- `aidm_server/turn_engine.py:875-883` serializes normal socket turns through `session_turn_coordinator`.
- `aidm_server/blueprints/creatures.py:536-549`, `585-591`, `609-617`, and `639-647` read, apply, persist, sync, and commit combat/session state outside that coordinator.

Risk:
REST/debug/service mutations can race with a streamed DM turn and clobber state captured earlier in `stateBeforeDm`. This can drop equipment, HP, combat, quest, or campaign-pack progress changes.

Recommended next step:
Create a shared session state mutation service that acquires the same per-session lock, reloads the session inside the lock, applies validated changes, and records a version/ledger entry. Add a regression test for an equipment or combat mutation during a simulated active turn.

### High: Id-less state changes remain repeatable outside ledger protection

Current evidence:
- `aidm_server/game_state/validation/validator.py:2630-2666` only checks duplicate IDs when a non-empty `id` exists.
- `aidm_server/game_state/application/applier.py:1232-1241` only skips already-applied changes when `change_id` is present.

Risk:
External callers, retries, or client double-submits can duplicate mutable effects when they send id-less state changes. The ledger cannot explain or dedupe those effects.

Recommended next step:
Normalize or require state change identity at the mutation boundary. For untrusted routes, synthesize a deterministic idempotency key scoped to session, actor, route/source, change type, target, and amount, then ledger every applied change.

### Medium-High: Combat ability usage still accepts unknown abilities as applied participant changes

Current evidence:
- `aidm_server/game_state/application/applier.py:1210-1225` returns the participant even when no ability with the requested `abilityId` was found.
- The caller path records `abilityId` on the applied change after receiving a participant.

Risk:
Ability-use records can look successful even when the requested ability was absent. This weakens later work on cooldowns, once-per-combat powers, and combat legality.

Recommended next step:
Add a focused failing test for unknown `combat.ability.mark_used`, then change the applier/validator to reject or skip unknown ability IDs. This is smaller than the broader combat resolver work and is a good candidate for a future safe fix.

## Larger Suggested Improvements

- Define explicit DM/admin/player capabilities and use them consistently for combat, bestiary authoring, debug payloads, generation, and session/world mutation routes.
- Split player-safe combat summaries from admin-only debug payloads; avoid returning resolver internals, hidden plans, helper raw text, or validation logs to normal players.
- Route all live `Session.state_snapshot` writes through one serialized mutation service with optimistic version checks and structured audit metadata.
- Make state change identity mandatory or deterministic before validation/application so retries are safe by default.
- Promote enemy combat intent from advisory text to server-owned executable mechanics over time: roll, resolve, emit state changes, then narrate.
- Add adversarial tests for route authorization, actor ownership, idempotency, snapshot races, unknown/depleted combat abilities, and debug redaction.
- Split `BestiaryDebugPanel` into player-facing bestiary UI and DM/admin tools so normal play does not expose debug affordances.
- Consider adding a lightweight CI job that runs `make secrets`, `bash -n scripts/*.sh`, and the developer-workflow pytest subset.

## Notes

- Generated Python caches and `.DS_Store` files were present in local source directories but are ignored and untracked. No cleanup was performed to avoid destructive filesystem churn during the audit.
- No live backend restart, tunnel verification, or browser UI smoke was needed for this script-only fix.
- No unrelated user changes were reverted.

## Recommended Next Run Focus

1. Add a focused regression test for unknown `combat.ability.mark_used`, then make the applier skip/reject unknown abilities.
2. Add the first non-admin rejection tests around `/api/sessions/<id>/combat/debug` and `/combat/apply-state-changes`.
3. Add shell syntax coverage for all scripts or a `make dev-check` target that bundles secrets, script syntax, and focused workflow tests.

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
