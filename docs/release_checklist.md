# Playable Beta Release Checklist

This file defines reusable release criteria; unchecked boxes are not a claim
about the current candidate. Generate candidate-specific status from the latest,
same-commit evidence with `make release-checklist-status`, and treat ignored
`tmp/release/` artifacts as stale whenever `HEAD` has advanced since their RC
report.

## Preflight
- [ ] `make closed-beta-rc` passes, or each equivalent gate below is recorded separately. The full gate verifies exact Python 3.14.6, Node 24.18.0, and npm 12.0.0 toolchains. For shareable local evidence, run `.venv/bin/python scripts/closed_beta_rc_check.py --evidence-report tmp/release/rc-evidence.md`.
- [ ] RC evidence is generated from a clean signed-off commit/worktree before final issue closure.
- [ ] `.venv/bin/python scripts/deploy_bootstrap.py --check-only` passes.
- [ ] `make request-json-parsing` confirms backend routes use shared JSON request parsing helpers instead of direct `request.get_json(silent=True)`.
- [ ] `.venv/bin/python -m pytest` passes.
- [ ] `.venv/bin/python scripts/smoke_beta_flow.py` passes in isolated fallback mode.
- [ ] `.venv/bin/python scripts/scenario_regression.py` passes and records provider/model for each scenario.
- [ ] If live/local validation is needed, `.venv/bin/python scripts/smoke_beta_flow.py --use-local-env` is run intentionally against the target database/provider.
- [ ] `GET /api/health` confirms the expected environment/auth flags and reports the selected provider/model as configured; deterministic fallback is accepted only for an explicitly documented safe-mode drill.
- [ ] `make db-upgrade` applies cleanly.
- [ ] GitHub Actions `AIDM CI` passes backend tests, frontend checks, bundle budget, and browser smoke.
- [ ] GitHub Actions `Closed Beta RC` passes before tagging an RC build.
- [ ] GitHub Actions `Closed Beta RC` uploads the `closed-beta-rc-evidence` artifact containing `tmp/release/rc-evidence.md`, issue snippets, the release evidence packet, source archive plus `.sha256`, security/export-import evidence, visual-smoke screenshots/review evidence, and GitHub Actions run URL evidence when produced.
- [ ] `make github-actions-rc-plan` records local GitHub Actions readiness for the signed-off commit and, when intentionally run with `GITHUB_ACTIONS_RC_PLAN_ARGS="--dispatch-closed-beta-rc"`, dispatches the manual `Closed Beta RC` workflow only after the candidate is clean unless `--allow-dirty` is explicitly provided.
- [ ] `make github-actions-evidence GITHUB_ACTIONS_EVIDENCE_ARGS="--auto-gh --include-gh-details --verify-closed-beta-rc-artifact-contents"` or manual URL input records the successful `AIDM CI` run URL, `Closed Beta RC` run URL, and downloaded `closed-beta-rc-evidence` artifact content proof for the signed-off commit in `tmp/release/github-actions-evidence.md`.
- [ ] `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> --target-url <target-url> --auth-token <token> --evidence-report tmp/release/deployment-readiness-evidence.md"` passes for the hosted/staging target, with documented flags for same-origin CORS, bearer-token auth exceptions, or Socket.IO staging proof when applicable. When `AIDM_SERVE_FRONTEND=true`, separately verify `GET /` and one built `/assets/*` file through the deployed edge because deployment readiness currently checks the API/WebSocket surface, not the SPA build.
- [ ] `make hosted-rc-evidence HOSTED_RC_EVIDENCE_ARGS="--target-url <target-url> --auth-token <operator-token> --workspace-id <workspace-id> --non-admin-token <token> --campaign-id <campaign-id> --session-id <session-id> --player-id <player-id> --env-file <target-env>"` runs the hosted deployment-readiness, cookie-auth, non-admin forbidden, export/import, and beta SLO evidence plan. It must not report `manual-evidence-required`; provide `--hosted-backup-restore-evidence`, `--hosted-worker-process-evidence`, `--source-archive-attachment-evidence`, and `--external-telemetry-receipt` when those manual proofs are ready.
- [ ] `make rc-issue-evidence` renders issue-ready Markdown under `tmp/release/issue-evidence/` from the latest RC evidence report and source archive scan.
- [ ] `make rc-issue-closure-evidence` writes read-only closure/comment evidence for RC gate issues `#3`-`#9` before final issue closure.
- [ ] `make rc-recommendation-matrix` renders `tmp/release/rc-recommendation-matrix.md` and `.json`, mapping the original RC recommendations to current implementation, hosted proof, and manual signoff status.
- [ ] `make external-proof-inputs` renders `tmp/release/external-proof-inputs.md` and `.json` with the hosted/GitHub/operator fields and command templates still needed for final RC proof.
- [ ] `make external-proof-execution-plan` renders `tmp/release/external-proof-execution-plan.md` and `.json`, grouping remaining hosted/GitHub/operator proof into ordered execution phases.
- [ ] `make operator-signoff-values-template` renders `tmp/release/external-proof-values.example.json` from the latest external proof inputs, pre-seeding only non-secret evidence already proven by the current packet. GitHub Actions URLs are pre-seeded as final proof only after the packet shows a clean signed-off worktree. Copy the template locally to `tmp/release/external-proof-values.json` only when filling proof values; sensitive token fields are intentionally omitted and must be passed through commands or a secret manager instead.
- [ ] `make external-proof-values-check` writes `tmp/release/external-proof-values-status.md` and `.json`, checking filled proof values for missing required fields, placeholder metadata, conditional Socket.IO staging proof, and accidentally persisted command-only tokens before final signoff.
- [ ] `make external-proof-values-merge` runs only after `tmp/release/external-proof-values.json` exists from the operator-filled template and hosted RC evidence has produced a passed, usable `external-proof-values.hosted-rc.json` fragment.
- [ ] `make rc-finalize-signoff` runs after external hosted proof values are filled and merged. It writes the final `tmp/release/operator-signoff.json`, stamps the generated signoff-status evidence back into `tmp/release/external-proof-values.json`, requires `make external-proof-values-check` to pass, and refreshes the release packet/checklist.
- [ ] `make rc-handoff-artifacts` generates the local handoff bundle after the latest RC evidence run, including frontend `npm ci` evidence, source archive, a planned hosted RC command artifact when no real hosted evidence exists, issue snippets, recommendation matrix, external proof inputs, external proof execution plan, signoff values template, external proof values status, signoff-from-inputs preview, signoff status/draft/action plan, release evidence packet, artifact consistency report, and checklist status.
- [ ] `make release-evidence-packet` renders `tmp/release/release-evidence-packet.md` and `.json` so the RC handoff has one manifest of local evidence, artifact paths, source archive checksum, dirty-worktree status, and remaining external exceptions.
- [ ] `make release-artifact-consistency` renders `tmp/release/release-artifact-consistency.md` and `.json`, proving the release packet, source archive, `.sha256` sidecar, operator signoff status, and generated proof docs all reference the same source archive checksum.
- [ ] `make release-checklist-status` renders `tmp/release/release-checklist-status.md` and `.json` from the latest evidence packet so remaining local, external, and manual checklist items are visible in one place.
- [ ] Candidate checklist output reports RC evidence as `current`; a `stale`, `dirty`, `not-signed-off`, or `unavailable` banner blocks treating local RC rows as candidate proof.
- [ ] `make operator-signoff-draft` seeds `tmp/release/operator-signoff.draft.json` from the latest release evidence packet without marking local isolated smokes, dry-run hosted plans, or placeholder targets as completed hosted proof.
- [ ] `make operator-signoff-action-plan` renders `tmp/release/operator-signoff-action-plan.md` and `.json` with the remaining signoff commands, required inputs, and evidence fields.
- [ ] `make operator-signoff-status OPERATOR_SIGNOFF_STATUS_ARGS="--require-complete"` passes before RC issue closure. Prefer `make rc-finalize-signoff` after external proof values are filled; it generates the final signoff manifest, self-records the signoff-status evidence, runs the proof-values check, and refreshes packet artifacts.
- [ ] `make post-rc-issue-evidence` previews the GitHub issue comments before any remote mutation; use `POST_RC_ISSUE_EVIDENCE_ARGS="--post"` only after review.
- [ ] RC gate issues are closed with `docs/rc_issue_evidence_template.md` evidence entries or generated `tmp/release/issue-evidence/issue-*.md` snippets, not only code-change summaries.

## Frontend
- [ ] `make frontend-npm-ci-evidence` records that `cd aidm_frontend && npm ci` installs from lockfile in `tmp/release/frontend-npm-ci-evidence.md`.
- [ ] `cd aidm_frontend && npm test` passes typecheck, lint, and unit tests.
- [ ] `cd aidm_frontend && npm run lint` passes.
- [ ] `cd aidm_frontend && npm run typecheck` passes.
- [ ] With Node 24.18.0 and npm 12.0.0, `cd aidm_frontend && npm run build` passes and produces `aidm_frontend/dist/index.html` for deployments using `AIDM_SERVE_FRONTEND=true`.
- [ ] `cd aidm_frontend && npm run bundle:budget` passes after build.
- [ ] RC browser smoke runs the built single-origin frontend and verifies required security headers and CSP on the UI response.
- [ ] RC visual smoke captures desktop, short-height, and mobile screenshots without console errors, horizontal overflow, or clipped core panels.
- [ ] `make visual-smoke-review` writes `tmp/release/visual-smoke-review.md` and `.json` confirming expected screenshot dimensions, nonblank pixel variation, and no missing screenshots.
- [ ] `make rc-issue-evidence` records the latest visual-smoke screenshot directory and review report in `tmp/release/issue-evidence/issue-04-frontend.md`.
- [ ] `cd aidm_frontend && npm audit --omit=dev` has no unresolved production issues.
- [ ] `.github/dependabot.yml` covers Python and frontend dependency update PRs.
- [ ] Modal accessibility regressions cover focus placement, Escape close, focus trapping, focus return, dialog descriptions, and danger confirmation cancellation.

## Security
- [ ] `AIDM_AUTH_REQUIRED=true` in deployed environment.
- [ ] Strong token configured in `AIDM_API_AUTH_TOKENS`.
- [ ] CORS allowlists are explicit (no wildcard in production).
- [ ] `docs/auth_modes.md` and `docs/production-readiness.md` match the intended exposure/provider mode, and any bearer-token browser exception or provider-specific runtime requirement is documented.
- [ ] Hosted same-origin deployments either enable HTTP-only account cookies or document why bearer/session storage remains acceptable.
- [ ] If cookie auth is enabled, `AIDM_ACCOUNT_COOKIE_SECURE=true`; if cookie-only browser auth is required, `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false`.
- [ ] `make hosted-cookie-auth-smoke` writes `tmp/release/hosted-cookie-auth-evidence.md` during the local RC gate and proves cookie-only account login, no raw account-token JSON response, CSRF enforcement on unsafe REST, logout cleanup, workspace role downgrade refresh, and Socket.IO cookie auth.
- [ ] `make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_SMOKE_ARGS="--target-url <target-url> --account-intent signup --evidence-report tmp/release/hosted-cookie-auth-evidence.md"` passes against the hosted/staging URL, or `--account-intent login --username <user> --password <pass>` is used for a pre-provisioned test account.
  Preferred combined invocation: `make hosted-cookie-release-proof HOSTED_COOKIE_RELEASE_PROOF_ARGS="--target-url <target-url> --account-intent signup"`. One opt-in two-account cookie flow writes `hosted-cookie-auth-evidence.md`, `security-forbidden-evidence.md`, `export-import-evidence.md`, and `beta-slo-baseline.md` without bearer-token arguments; generated passwords, cookies, CSRF values, and the one-time workspace token are never written to evidence, and proof sessions plus the temporary workspace are cleaned even when a later check fails where the target remains reachable.
- [ ] `make security-forbidden-smoke` proves non-admin accounts are rejected by combat operator, bestiary authoring/save, and beta operator endpoints.
- [ ] `make security-forbidden-smoke SECURITY_FORBIDDEN_SMOKE_ARGS="--target-url <target-url> --account-token <non-admin-token> --workspace-id <workspace-id> --campaign-id <campaign-id> --session-id <session-id> --evidence-report tmp/release/security-forbidden-evidence.md"` passes against hosted/staging before closing the security gate.
- [ ] A non-admin player receives their own full character in session list/state/export responses, only public identity and bounded combat status for party peers, no peer sheets/stats/inventory/spells/resources/abilities/armor metadata, and `404 player_not_found` when selecting a peer with `export?player_id=`. Accountless workspace/table tokens retain only the public party projection and cannot fetch or mutate a player directly by ID, bind Socket.IO to a guessed player, or receive sender-private roll provenance; an admin export remains complete.
- [ ] Raw campaign canon and campaign/region bestiary catalogs require `debug_read`; player Chronicle exports retain public narration and revealed chapter titles but omit progress actions/reasons/revisions/event IDs, provider/model traces, state-pipeline notes, and Director's Commentary; administrator and local-operator exports remain complete.
- [ ] Clarification original actions, inventory-derived options, and persisted state-pipeline detail are sent or returned only to the acting player or an administrator; party peers receive only a neutral waiting status.
- [ ] Security headers are enabled, including `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, and `Permissions-Policy`.
- [ ] Passwordless legacy recovery requires a valid saved or operator-issued high-entropy account token; matching username/first/last-name fields alone cannot set a password, and an operator-issued code is rotated after successful recovery.
- [ ] Workspace-password target limiting is scoped per authenticated account and canonical workspace while IP+workspace and IP-wide limits remain active; focused regression coverage proves one account cannot consume another account's cross-IP target bucket, while same-source IP saturation can still reject both.
- [ ] Hosted telemetry routes `auth.preauth_rate_limited` target events for `account-legacy-claim` and `workspace-password` to `AIDM_ALERT_OWNER` as abuse signals; for workspace passwords, `dimension=target` now represents an account-scoped canonical-workspace bucket.

## Data Integrity
- [ ] Database backup taken before deployment.
- [ ] `make backup-restore-drill BACKUP_RESTORE_DRILL_ARGS="--database-uri sqlite:////absolute/path/to/dnd_ai_dm.db"` creates a backup and verifies a restored copy for local/private SQLite beta databases. Hosted database restore drills are documented with the provider-specific runbook.
- [ ] New tables exist: `dm_turns`, `session_states`, `story_entities`, `story_facts`, `story_threads`, `turn_canon_updates`, `turn_events`, `session_state_mutation_audits`, and `operator_action_audits`.
- [ ] Session log and state endpoints return consistent turn IDs.
- [ ] Session export/import smoke restores a JSON export into a new active session without duplicating projected log entries.
- [ ] `make session-export-import-smoke` writes `tmp/release/export-import-evidence.md` during the local RC gate, and `make session-export-import-smoke SESSION_EXPORT_IMPORT_SMOKE_ARGS="--target-url <target-url> --auth-token <token> --workspace-id <workspace-id> --session-id <session-id> --player-id <player-id> --evidence-report tmp/release/export-import-evidence.md"` passes against hosted/staging before hosted data-integrity sign-off.
- [ ] Bad-turn reports attach to the exact `turn_id`, provider, and model, and `/api/beta/incidents` plus the operator inspector Ops tab show failed turns, failed canon jobs, and tester reports for workspace admins.
- [ ] `/api/beta/session-quality?session_id=<id>` and the operator inspector Ops tab show the selected session provider/model mix, latency, failed turns, canon failures, bad-turn reports, unresolved clarifications, state/operator audit counts, and a compact `operator_summary` headline/details block.
- [ ] `/api/beta/audits` returns recent session-state mutation diffs and operator actions for workspace admins, including combat/equipment/campaign-pack progress, bestiary authoring, import, archive, restore, and delete activity.
- [ ] `make migration-chain-drill` verifies the Alembic `upgrade head -> downgrade base -> upgrade head` path against an isolated database.
- [ ] `.venv/bin/python scripts/check_state_snapshot_writers.py` passes and `docs/state_snapshot_writer_inventory.md` classifies every direct `Session.state_snapshot` writer.

## Runtime Quality
- [ ] Socket message stream includes `turn_id`, `requires_roll`, `rules_hint`, `context_version`.
- [ ] Typed `action_intent` metadata persists for roll/ability/item actions.
- [ ] Player roll requests cannot supply authoritative faces, kept values, modifiers, or totals; one committed roll creates one durable roll event, one sender-private receipt, and a provenance-redacted room result before narration.
- [ ] Retrying an uncertain turn reuses the original payload and `client_message_id`; a completed duplicate returns the persisted turn, an incomplete `processing` turn replays its persisted sender-private roll receipt and resumes without a second roll/incoming event/pre-DM application or peer rebroadcast, and automatic or manual reconnect reloads the current session snapshot.
- [ ] `turn_status` events progress through narration, save, canon, and failure states.
- [ ] `AIDM_SOCKETIO_WORKER_MODEL` is explicitly set to `single`; hosted production rejects deferred multi-worker models.
- [ ] `make socketio-worker-model-decision` passes and `docs/socketio_worker_model.md` records the RC1 hosted worker-model decision.
- [ ] Hosted single-worker beta start command is `scripts/run_production_server.sh` with `AIDM_ENV=production`, `AIDM_SOCKETIO_ASYNC_MODE=threading`, `AIDM_SOCKETIO_WORKER_MODEL=single`, `AIDM_GUNICORN_THREADS=100`, and `WEB_CONCURRENCY=1`; `scripts/run_production_server.sh --print` shows the exact Gunicorn gthread command.
- [ ] Deployment evidence proves exactly one backend process/replica; process-local presence/music makes multi-worker production unsafe even with turn fencing.
- [ ] Campaign-pack progress service calls are serialized through the reentrant session turn coordinator, including nested calls from active turn processing.
- [ ] `make socket-concurrency-smoke` proves same-session queue locking and different-session socket turn persistence.
- [ ] Beta runtime notices are visible for deterministic fallback, missing live provider configuration, local/private auth-disabled mode, unavailable TTS, and process-local provider changes.
- [ ] Segment trigger events retain reason/spec metadata in durable operator records, while the player room event exposes only the revealed segment ID, title, and description.
- [ ] Improvised canon is persisted into emergent memory tables after a narrated turn.
- [ ] Scenario quality regressions cover opening narration, impossible actions, combat roll prompts, item use, checkpoint triggers, NPC continuity, and canon recall.
- [ ] Session end recap is stored and retrievable.

## Observability
- [ ] `AIDM_OBSERVABILITY_PROVIDER` and `AIDM_ALERT_OWNER` are set in production.
- [ ] `/api/metrics` reflects request/turn counters.
- [ ] `/api/metrics/prometheus` returns Prometheus text output with API counters and beta gauges.
- [ ] Deployment readiness live checks pass for `/api/health`, `/api/metrics`, `/api/metrics/prometheus`, and required security headers.
- [ ] `/api/beta/support-bundle` and `make export-support-bundle` export session quality, incidents, audits, recent turns, canon jobs, session logs, turn events, and relevant telemetry counters for workspace admins.
- [ ] The Beta feedback prompt records per-turn coherence, fun, and rules scores, and coherence submissions feed `/api/beta/slo` plus session-quality summaries.
- [ ] `make observability-check` validates the bundled Prometheus/Grafana files; on Docker-capable release machines, `make observability-check OBSERVABILITY_CHECK_ARGS="--check-docker-compose --require-docker"` also validates `docker compose config`. `docs/observability.md` confirms the bundled ports, anonymous access, and default credentials remain trusted-local only.
- [ ] `make local-beta-slo-baseline` writes local-only SLO evidence and raw `tmp/release/beta-slo*.json` artifacts as part of the RC gate.
- [ ] External telemetry endpoint receives events when enabled.
- [ ] `make beta-slo-baseline BETA_SLO_BASELINE_ARGS="--target-url <target-url> --auth-token <token> --workspace-id <workspace-id> --release RC1 --environment staging --output tmp/release/beta-slo-baseline.md"` writes `tmp/release/beta-slo-baseline.md` with target-environment metrics before tester expansion.
- [ ] Rate-limit and auth errors are monitored.
- [ ] DM generation failures are monitored and below threshold.
- [ ] TTS `/api/tts/stream` returns chunk headers and records mid-stream chunk failures in telemetry.

## Packaging
- [ ] `make packaging-cleanup-evidence` verifies `make clean` removes cache/runtime/build artifacts before packaging without deleting the current `tmp/release` evidence bundle.
- [ ] `make packaging-cleanup-evidence` verifies `make clean-deps` covers local dependency folders when preparing a source-only handoff or commit.
- [ ] `make source-archive` creates a shareable source archive under `tmp/release/`.
- [ ] The source archive has a matching `.sha256` sidecar and that checksum is listed in `tmp/release/release-evidence-packet.md` plus `tmp/release/release-artifact-consistency.md`.
- [ ] `make rc-issue-evidence` records the source archive path and clean archive scan in `tmp/release/issue-evidence/issue-09-packaging.md`.
- [ ] The manual `Closed Beta RC` workflow artifact includes the generated source archive for reviewer download before tagging a hosted RC.
- [ ] Release archive does not include `.venv`, `aidm_frontend/node_modules`, `aidm_frontend/dist`, local SQLite data, logs, or `.env.local`.
- [ ] `docs/beta_tester_onboarding.md`, `docs/beta_runbook.md`, `docs/production-readiness.md`, and `docs/observability.md` are reviewed for the signed-off target and linked where relevant for invited testers/operators.
