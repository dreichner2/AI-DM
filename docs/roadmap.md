# AIDM Roadmap

AIDM is now in beta-hardening territory: the core gameplay, campaign, player,
session, world, map, Socket.IO, state pipeline, canon memory, and metrics
surfaces exist. The highest-return work is reducing security, runtime, and
maintenance risk before expanding gameplay scope.

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
  now require a valid saved account token, a stored password match, or an
  explicit legacy claim that sets a password immediately.
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

## Beta Hardening

- Keep account recovery explicit. If a legacy account has no password and the
  saved account token is gone, use the legacy claim path only when identity
  details match and a password is being set.
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
  backup/restore path; the bundled drill is intentionally limited to
  file-backed SQLite used by local/private beta runs.

## Not Now

- Do not replace the backend-owned TypeScript contract with OpenAPI yet. The
  current contract generator is low-friction; the immediate win is drift
  checking and response tests.
- Do not add more gameplay surface before the security/runtime/docs hardening
  items above are boring.
