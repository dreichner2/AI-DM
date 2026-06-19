# Playable Beta Release Checklist

## Preflight
- [ ] `make closed-beta-rc` passes, or each equivalent gate below is recorded separately. For shareable local evidence, run `scripts/closed_beta_rc_check.py --evidence-report tmp/release/rc-evidence.md`.
- [ ] RC evidence is generated from a clean signed-off commit/worktree before final issue closure.
- [ ] `.venv/bin/python scripts/deploy_bootstrap.py --check-only` passes.
- [ ] `make request-json-parsing` confirms backend routes use shared JSON request parsing helpers instead of direct `request.get_json(silent=True)`.
- [ ] `.venv/bin/python -m pytest` passes.
- [ ] `.venv/bin/python scripts/smoke_beta_flow.py` passes in isolated fallback mode.
- [ ] `.venv/bin/python scripts/scenario_regression.py` passes and records provider/model for each scenario.
- [ ] If live/local validation is needed, `.venv/bin/python scripts/smoke_beta_flow.py --use-local-env` is run intentionally against the target database/provider.
- [ ] `GET /api/health` confirms expected flags.
- [ ] `flask db upgrade` applies cleanly.
- [ ] GitHub Actions `AIDM CI` passes backend tests, frontend checks, bundle budget, and browser smoke.
- [ ] GitHub Actions `Closed Beta RC` passes before tagging an RC build.
- [ ] GitHub Actions `Closed Beta RC` uploads the `closed-beta-rc-evidence` artifact containing `tmp/release/rc-evidence.md`, issue snippets, the release evidence packet, source archive plus `.sha256`, security/export-import evidence, visual-smoke screenshots/review evidence, and GitHub Actions run URL evidence when produced.
- [ ] `make github-actions-rc-plan` records local GitHub Actions readiness for the signed-off commit and, when intentionally run with `GITHUB_ACTIONS_RC_PLAN_ARGS="--dispatch-closed-beta-rc"`, dispatches the manual `Closed Beta RC` workflow only after the candidate is clean unless `--allow-dirty` is explicitly provided.
- [ ] `make github-actions-evidence GITHUB_ACTIONS_EVIDENCE_ARGS="--auto-gh --include-gh-details --verify-closed-beta-rc-artifact-contents"` or manual URL input records the successful `AIDM CI` run URL, `Closed Beta RC` run URL, and downloaded `closed-beta-rc-evidence` artifact content proof for the signed-off commit in `tmp/release/github-actions-evidence.md`.
- [ ] `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> --target-url <target-url> --auth-token <token> --evidence-report tmp/release/deployment-readiness-evidence.md"` passes for the hosted/staging target, with documented flags for same-origin CORS, bearer-token auth exceptions, or Socket.IO staging proof when applicable.
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
- [ ] `cd aidm_frontend && npm run build` passes.
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
- [ ] `docs/auth_modes.md` matches the intended exposure mode and any bearer-token browser exception is documented.
- [ ] Hosted same-origin deployments either enable HTTP-only account cookies or document why bearer/session storage remains acceptable.
- [ ] If cookie auth is enabled, `AIDM_ACCOUNT_COOKIE_SECURE=true`; if cookie-only browser auth is required, `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false`.
- [ ] `make hosted-cookie-auth-smoke` writes `tmp/release/hosted-cookie-auth-evidence.md` during the local RC gate and proves cookie-only account login, no raw account-token JSON response, CSRF enforcement on unsafe REST, logout cleanup, workspace role downgrade refresh, and Socket.IO cookie auth.
- [ ] `make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_SMOKE_ARGS="--target-url <target-url> --account-intent signup --evidence-report tmp/release/hosted-cookie-auth-evidence.md"` passes against the hosted/staging URL, or `--account-intent login --username <user> --password <pass>` is used for a pre-provisioned test account.
- [ ] `make security-forbidden-smoke` proves non-admin accounts are rejected by combat operator, bestiary authoring/save, and beta operator endpoints.
- [ ] `make security-forbidden-smoke SECURITY_FORBIDDEN_SMOKE_ARGS="--target-url <target-url> --account-token <non-admin-token> --workspace-id <workspace-id> --campaign-id <campaign-id> --session-id <session-id> --evidence-report tmp/release/security-forbidden-evidence.md"` passes against hosted/staging before closing the security gate.
- [ ] Security headers are enabled, including `Content-Security-Policy`, `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`, and `Permissions-Policy`.

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
- [ ] `turn_status` events progress through narration, save, canon, and failure states.
- [ ] `AIDM_SOCKETIO_WORKER_MODEL` is explicitly set to `single`, `sticky`, or `message_queue`.
- [ ] `make socketio-worker-model-decision` passes and `docs/socketio_worker_model.md` records the RC1 hosted worker-model decision.
- [ ] Hosted single-worker beta start command is `scripts/run_production_server.sh` with `AIDM_SOCKETIO_ASYNC_MODE=eventlet`, `AIDM_SOCKETIO_WORKER_MODEL=single`, and `WEB_CONCURRENCY=1`; `scripts/run_production_server.sh --print` shows the exact Gunicorn command.
- [ ] Multi-worker deployments set `AIDM_TURN_COORDINATOR_STORE=database`, have migration `0011_session_turn_locks` applied, and prove sticky-session affinity or Socket.IO message-queue delivery in staging.
- [ ] Sticky or message-queue Socket.IO deployments provide `--socketio-staging-proof` to the deployment-readiness gate.
- [ ] Campaign-pack progress service calls are serialized through the reentrant session turn coordinator, including nested calls from active turn processing.
- [ ] `make socket-concurrency-smoke` proves same-session queue locking and different-session socket turn persistence.
- [ ] Beta runtime notices are visible for deterministic fallback, missing live provider configuration, local/private auth-disabled mode, unavailable TTS, and process-local provider changes.
- [ ] Segment trigger events emit reason/spec metadata.
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
- [ ] `make observability-check` validates the bundled Prometheus/Grafana files; on Docker-capable release machines, `make observability-check OBSERVABILITY_CHECK_ARGS="--check-docker-compose --require-docker"` also validates `docker compose config`.
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
- [ ] `docs/beta_tester_onboarding.md` is reviewed and linked for invited testers.
