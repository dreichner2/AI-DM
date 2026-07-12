# AIDM Roadmap

AIDM is now in beta-hardening territory: the core gameplay, campaign, player,
session, world, map, Socket.IO, state pipeline, canon memory, and metrics
surfaces exist. The highest-return work is reducing security, runtime, and
maintenance risk before expanding gameplay scope.

## Current Status - 2026-07-12

- The current implementation includes the intended closed-beta gameplay
  surface, executable local release/readiness gates, hosted cookie
  authentication, the single-worker production topology, and
  PostgreSQL-backed production guards. This is not a readiness declaration;
  hosted evidence, backup proof, and operator signoff remain separate gates.
- The dated technology upgrade report records a completed hosted-staging
  PostgreSQL 17-to-18 upgrade with rollback and forward-restore evidence. The
  repository cannot re-verify that external state, and a production database
  cutover remains a separate maintenance and operator-signoff decision.
- The provider registry currently supports DeepSeek, Codex CLI, Gemini,
  NVIDIA/Kimi, and the deterministic fallback. Codex CLI routes its current
  default selection through GPT-5.6 Sol Medium and runs without host tool
  access; tool events fail closed.
- Release state is determined by `docs/release_checklist.md` and generated RC
  evidence, not by the Done list in this roadmap.

Archived review material lives in:

- `docs/archive/improvements_suggestions_legacy.md`
- `docs/archive/organized_improvement_suggestions_legacy.md`

## Done

- Backend app factory, central config, request IDs, CORS scoping, auth context,
  rate limiting, migration/bootstrap checks, and production schema guardrails.
- Backend-owned DTO contracts with generated frontend TypeScript types.
- Session event spine, turn persistence, turn status events, state application,
  canon job queue, and projection repair tooling.
- Frontend TypeScript strictness, split CSS files, extracted hooks/components,
  browser smoke tests, bundle budget checks, and generated API contract usage.
- Account login hardening for passwordless legacy accounts: existing accounts
  now require a valid saved account token or a high-entropy operator-issued
  replacement to set a password; names alone are not account-recovery proof.
- Workspace-password target limiting now isolates the cross-IP ceiling by
  authenticated account and canonical workspace while retaining IP+workspace
  and IP-wide abuse controls.
- Read-only player detail fetches: starting inventory/spell repair now lives
  behind an explicit repair endpoint instead of writing during `GET`.
- Session start idempotency validation rejects overlong client keys instead of
  truncating them.
- Deploy bootstrap refuses production server startup through Werkzeug and
  requires database-backed rate limiting and turn coordination in production.
- Remaining App dialog surfaces are componentized: archive/restore managers,
  campaign chooser, player edit/delete, world manager/delete, and
  create-campaign dialogs now live outside `aidm_frontend/src/App.tsx`.
- Extracted dialogs use a shared modal shell plus the common focus-trap hook, so
  focus placement, Escape handling, Tab loops, and focus return are maintained in
  one frontend path.
- Campaign, session, and player archive/delete lifecycle orchestration lives in
  service modules with focused data-integrity tests.
- Modal accessibility regressions cover focus placement, Escape close, focus
  trapping, danger confirmation cancellation, and dialog descriptions.
- Production bootstrap now requires declared observability ownership
  (`AIDM_OBSERVABILITY_PROVIDER`, `AIDM_ALERT_OWNER`) and the supported
  `AIDM_SOCKETIO_WORKER_MODEL=single` topology.
- Multi-worker Socket.IO is deferred until presence/music state is shared and
  both affinity and shared-queue delivery are proven under the real topology.
- Hosted same-origin account auth can use server-issued `HttpOnly` account
  cookies, suppress raw account tokens in JSON responses, and enforce a
  companion CSRF token on unsafe cookie-authenticated REST requests.
- Player dice are generated on the server, derived from persisted character and
  pending-roll state, committed as durable turn evidence, and then broadcast for
  client presentation. Client roll totals and natural-language claims are not
  mechanical authority.
- Server-issued `roll_required` events now carry viewer-safe roll guidance and
  remaining-player state. The composer opens the authoritative check in one
  action, removes the rejected optimistic entry, and preserves the player's
  draft until the roll resolves.
- The combat HUD keeps unavailable known targets visible as disabled options
  with server-issued legality reasons instead of presenting only successful
  choices.
- Uncertain frontend turn retries reuse the original idempotency key, duplicate
  acknowledgments reconcile cleanly, and both automatic and manual reconnects
  reload the persisted session snapshot.
- A turn interrupted after its incoming commit can resume from persisted
  pre-DM pipeline state without rerolling or replaying the incoming mutation.
- Non-admin session projections preserve the requester's complete character but
  redact peer character sheets, inventory, spells, resources, statistics, and
  other private mechanics while retaining bounded party combat status.
- Raw segment reads are DM-only; player campaign workspaces and socket events
  expose revealed story content without trigger conditions or recipes.
- Authored maps persist explicit player/DM visibility. Player REST, workspace,
  browser export, and inspector projections fail closed on DM-only or stale
  cached rows, while admins can reveal or hide maps without rewriting content.
- Frontend campaign/session lifecycle, campaign-pack progress, director,
  authoring, and admin-composer mutations are shown only to actors with operator
  capability. Compatibility memory snippets are labeled "Recent Memory" rather
  than durable canon.
- Archived/deleted sessions and campaigns reject live joins and mutations;
  lifecycle commits are fenced against active turns. Hidden campaign-pack
  checkpoint IDs stay out of all player progress projections unless the author
  supplies an explicit player-safe alias.
- A double failure in post-DM state application now retains narration for audit
  but marks mechanics unapplied, fails the turn, and blocks structured turn
  advancement and canon enqueue.
- Candidate release-checklist output now identifies current, stale, dirty,
  unsigned, or unavailable RC evidence instead of allowing old local proof to
  read as current.
- Release archive inspection rejects unresolved Git LFS pointers, and hosted
  beta-SLO evidence must contain positive DM/provider samples plus an explicit
  tester-expansion decision before the checklist can pass it.

## Beta Hardening

- Keep account recovery explicit. If a legacy account has no password and the
  saved account token is gone, verify the requester out of band and issue a
  replacement with `scripts/issue_legacy_recovery_code.py`. Never treat names
  alone as recovery authority or send the raw code through issue trackers/logs.
- Keep mutating repair behavior behind explicit commands or POST endpoints.
  Avoid hidden writes in diagnostics, browser refreshes, and smoke tests.
- Keep CI drift checks active: generated API types, backend tests, frontend
  tests/build, browser smoke, bundle budget, secret scan, Python audit, and
  focused Ruff correctness lint.
- Tester bad-turn reports now persist provider/model snapshots and feed the
  operator-only beta incident endpoint and inspector Ops tab alongside failed
  turns and canon jobs.
- Operator audit APIs now expose recent session-state mutation diffs and
  operator authoring actions for workspace admins. Equipment, combat, and
  campaign-pack progress writes produce durable mutation audit rows; bestiary
  create/generate/evolve-save, campaign/session archive/restore/delete,
  session import, and campaign-pack import writes produce operator-action audit
  rows.
- Campaign-pack progress service entrypoints now serialize through the same
  reentrant per-session turn coordinator used by active turn processing.
- The frontend shows a safe-mode banner when the deterministic fallback provider
  is active so playtesters know a live LLM is not serving turns.
- Deterministic scenario regressions now cover opening narration, impossible
  action boundaries, combat roll prompts, item use, checkpoint triggers, active
  NPC continuity, and durable canon recall with provider/model recorded per
  scenario.
- Run production bootstrap in `--check-only` mode before deployment, then start
  the app with a real Socket.IO-capable production server.
- Hosted closed-beta deployment readiness has an executable gate:
  `scripts/deployment_readiness_check.py` validates production env choices,
  required security/auth/observability settings, optional live health/metrics
  endpoints, required security headers, and a forced WebSocket upgrade through
  the deployed edge. Unsupported multi-worker models fail this gate.
- The local Prometheus/Grafana observability bundle has an executable validator
  (`scripts/check_observability_bundle.py`) that checks required files,
  dashboard metrics, provisioning paths, and optionally `docker compose config`
  where Docker is available.
- Local/private SQLite beta data now has an executable backup/restore drill
  (`scripts/backup_restore_drill.py`, `make backup-restore-drill`) that creates
  a backup and verifies a restored copy without mutating the source database.
- Hosted PostgreSQL data has a separate guarded custom-archive drill
  (`scripts/postgres_backup_restore_drill.py`,
  `make postgres-backup-restore-drill`) that requires a distinct, explicitly
  supplied empty target and compares restored schema/data evidence.
- The Codex CLI provider uses isolated, tool-free execution, dedicated saved
  authentication or an access token, bounded timeouts, validated event order,
  and deployment-readiness checks.
- Fixed-input model evaluation is available through
  `scripts/compare_helper_profiles.py`,
  `scripts/compare_tactics_compilers.py`, and
  `scripts/evaluate_combat_helpers.py`; these tools compare helper/compiler
  profiles without changing runtime defaults.

## Deployment Actions

- Set the hosted `AIDM_OBSERVABILITY_PROVIDER` and `AIDM_ALERT_OWNER` values in
  the target environment, then run the deployment-readiness gate and prove
  metrics/alert ingestion in staging.
- For hosted RC1, use the `single` worker-model decision in
  `docs/socketio_worker_model.md`, then attach hosted process evidence showing
  exactly one backend process/replica. Do not override this decision until the
  shared-state and multi-worker work recorded in that decision document lands.
- Enable `AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=true` and
  `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false` for hosted same-origin cookie-only
  auth when the deployment threat model calls for it.
- For hosted databases, document and rehearse the provider-specific
  snapshot/PITR path in addition to the guarded PostgreSQL custom-archive
  drill. A local or staging restore comparison does not replace provider-level
  production recovery proof.
- Before changing helper or compiler model defaults, run the fixed-input
  comparison tools, preserve their JSON evidence, and review quality, latency,
  parse failures, and cost outside this roadmap.

## Not Now

- The combat HUD now uses viewer-scoped, server-issued action and target IDs with
  current-turn and range-band validation. Persisted sub-turn action, movement,
  bonus-action, and reaction counters; spell/class-feature enumeration; and
  grid/pathfinding/line-of-sight rules remain separate gameplay projects.
- A richer campaign resume dashboard remains a high-value UX follow-up.
- Class-derived and migrated legacy weapon proficiencies are now persisted in a
  private server-owned player profile. Operator UI and rules support for custom
  proficiencies acquired later from ancestry, feats, training, or magic remain
  future work.
- Authored map visibility is currently whole-map `player` or `dm` visibility;
  per-layer and per-token reveal controls remain future work.
- Do not replace the backend-owned TypeScript contract with OpenAPI yet. The
  current contract generator is low-friction; the immediate win is drift
  checking and response tests.
- Do not add more gameplay surface before the security/runtime/docs hardening
  items above are boring.
