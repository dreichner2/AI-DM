# Backend Improvement Status and Candidates

Review renewed: 2026-07-10

This document replaces the 2026-06-03 snapshot with a verified status check.
It is deliberately narrow: completed recommendations are recorded so they are
not proposed again, and the active list contains only structural work still
visible in the current implementation. Product behavior should not change as a
side effect of these refactors.

## Original Recommendations Now Implemented

| Original recommendation | Current implementation |
| --- | --- |
| Move canon extraction off the visible gameplay response path. | `aidm_server/canon_jobs.py` provides durable queued/running/terminal jobs, retry and stale-job recovery, and background processing. A saved visible response is not erased by a later canon failure. |
| Centralize provider IDs, labels, models, defaults, and capabilities. | `aidm_server/provider_registry.py` is shared by configuration, runtime provider creation, and the runtime-config API. |
| Reduce `aidm_server/llm.py` provider-specific fallback duplication. | `aidm_server/llm.py` is now a 333-line narration facade; provider implementations and model fallback live in `aidm_server/llm_providers.py`, while shared HTTP timeout/session behavior lives in `aidm_server/http_client.py`. |
| Put hard limits around canon context retrieval. | `aidm_server/canon_retrieval.py` applies bounded entity, fact, and thread candidate queries before hybrid lexical/local-embedding ranking and reports the active limits in retrieval metadata. |
| Centralize object-only JSON request parsing. | Write endpoints use `aidm_server.validation.parse_json_body`; `scripts/check_request_json_parsing.py` guards against direct silent parsing outside shared helpers. |
| Restrict persistent provider changes outside local/test use. | Runtime provider mutation has its own blueprint/service, is limited to the owner/default workspace admin or an unscoped bootstrap operator credential, and rejects API persistence outside development, local, or test environments. |
| Keep generated/runtime content out of release archives. | Cleanup, source-archive, packaging-evidence, secret-scan, checksum, and release-artifact-consistency tooling enforce the handoff boundary. |

## Verified Current Candidates

### 1. Continue decomposing the turn engine by owned phase

`aidm_server/turn_engine.py` remains about 2,160 lines after action, roll,
narration, segment, Socket.IO, and combat responsibilities were extracted. It
still coordinates submission/idempotency, interaction targeting, character and
PvP validation, roll gates, persistence, narration, post-turn work, and canon
dispatch.

Keep `TurnEngine` as the transaction/order coordinator, but move cohesive
helpers behind phase interfaces only when focused tests can preserve emission,
commit, and failure ordering. The next safe seams are interaction-target
preparation and roll-gate lifecycle; avoid a broad rewrite.

### 2. Finish separating canon extraction, validation, and persistence

`aidm_server/emergent_memory.py` is about 1,143 lines. Inventory parsing,
location inference, projection, text normalization, and retrieval have already
moved to focused modules, but this file still combines provider extraction,
heuristic patches, entity/fact resolution, patch validation, and database
application.

Extract one boundary at a time, beginning with entity/fact persistence helpers
or patch normalization/validation. Keep `extract_canon_patch`,
`validate_canon_patch`, and `apply_canon_patch` as stable entry points until
canon-job and Socket.IO failure regressions prove an equivalent replacement.

### 3. Split provider implementations from helper-profile configuration

`aidm_server/llm_providers.py` is about 1,680 lines and owns Gemini,
OpenAI-compatible, Codex CLI, deterministic fallback, provider factories, and
task-specific helper profile resolution. The provider registry has removed
catalog drift, but implementation and helper-policy concerns still share one
module.

A low-risk split would keep the public provider/factory imports stable while
moving helper profile selection and task overrides to a focused module. Any
change must preserve provider fallback telemetry, timeout behavior, Codex
tool-event fail-closed handling, and the fixed-input helper comparison tools.

## Guardrails for This Work

- Prefer bounded extractions with focused regression tests over line-count-only
  rewrites.
- Preserve the turn event spine, transaction boundaries, status-event order,
  and saved-response behavior.
- Keep provider/model metadata owned by `provider_registry.py`.
- Run backend tests relevant to the extracted boundary plus
  `scripts/check_request_json_parsing.py` and
  `scripts/check_state_snapshot_writers.py` when those surfaces are touched.
- Record newly discovered product requirements separately; a refactor document
  must not silently redefine gameplay or release policy.
