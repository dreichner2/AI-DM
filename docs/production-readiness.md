# Production Readiness

Use this as the closed-beta deployment checklist. Local launcher behavior is
useful for development, but it is not the production boundary.

## Required Configuration

Use `.env.production.example` as the placeholder template for deployment
secret/env managers. Choose the matching exposure mode in
`docs/auth_modes.md` before finalizing auth, cookie, and CORS settings.

- `AIDM_ENV=production`
- `FLASK_SECRET_KEY=<strong explicit secret>`
- `AIDM_DATABASE_URI=postgresql+psycopg://...` with a reachable PostgreSQL
  database; hosted readiness rejects implicit or SQLite databases
- `AIDM_AUTH_REQUIRED=true`
- `AIDM_API_AUTH_TOKENS` or `AIDM_API_AUTH_TOKEN_WORKSPACES` configured
- `AIDM_AUTO_CREATE_SCHEMA=false`
- `AIDM_RATE_LIMIT_STORE=database`
- `AIDM_TURN_COORDINATOR_STORE=database`
- `AIDM_SOCKETIO_WORKER_MODEL=single`, `sticky`, or `message_queue`
- `AIDM_SOCKETIO_MESSAGE_QUEUE=<queue-url>` when the worker model is
  `message_queue`
- `AIDM_OBSERVABILITY_PROVIDER=<provider-name>`
- `AIDM_ALERT_OWNER=<team-or-person>`
- `AIDM_SECURITY_HEADERS_ENABLED=true`
- `AIDM_ADMIN_ENABLED=false`; the Flask-Admin model UI is local/development
  tooling and is not part of the hosted production surface
- Explicit REST and Socket.IO CORS allowlists, unless same-origin deployment
  intentionally leaves them empty
- For hosted cookie-only account auth:
  `AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=true`,
  `AIDM_ACCOUNT_COOKIE_SECURE=true`, and
  `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false`

## Startup

1. Install runtime dependencies from `requirements.runtime.txt` with
   `requirements.constraints.txt`.
2. Apply migrations with `make db-upgrade`.
3. Run `.venv/bin/python scripts/deploy_bootstrap.py --check-only`.
4. Run deployment readiness against the target environment and, when available,
   the deployed target URL:
   `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file /path/to/env --target-url https://aidm.example.com --auth-token <token> --evidence-report tmp/release/deployment-readiness-evidence.md"`.
   Add `--same-origin-deployment`, `--auth-storage-exception`, or
   `--socketio-staging-proof` only when those deployment choices are
   intentionally documented. Use `.json` as the evidence report suffix when a
   structured artifact is better for CI or release automation.
   The environment check opens `AIDM_DATABASE_URI` and runs `SELECT 1`; a
   syntactically valid but unreachable database does not pass readiness.
5. Start AIDM with a production Socket.IO-capable server. Do not use
   `deploy_bootstrap.py` as the production server process. For the first
   closed-beta single-worker deployment, use the decision in
   `docs/socketio_worker_model.md`:

   ```bash
   AIDM_ENV=production \
   AIDM_SOCKETIO_WORKER_MODEL=single \
   AIDM_SOCKETIO_ASYNC_MODE=eventlet \
   WEB_CONCURRENCY=1 \
   PORT=5050 \
   scripts/run_production_server.sh
   ```

   To inspect the exact Gunicorn command without starting a server, run
   `scripts/run_production_server.sh --print`. A real start always runs
   migrations and the deployment bootstrap preflight before Gunicorn execs.

## CI Gates

- Secret scan: `python scripts/scan_secrets.py`
- Python dependency audit: `python -m pip_audit -r requirements.runtime.txt`
- Python correctness lint: `python -m ruff check --select E9,F63,F7,F82 aidm_server tests scripts`
- Backend tests: `python -m pytest`
- PostgreSQL production rehearsal: the `postgres-integration` GitHub Actions
  job applies the migration chain, runs production bootstrap, starts the real
  Gunicorn/eventlet entrypoint, checks live health, metrics, Prometheus output,
  and security headers, exercises concurrency fencing, and runs the cookie-auth,
  forbidden-response, and export/import smokes against PostgreSQL. The job
  uploads the resulting Markdown as the `postgres-production-rehearsal`
  artifact. This remote rehearsal complements, but does not replace, proof
  against the actual hosted staging target and its managed backup/telemetry
  providers.
- Backup/restore drill for local/private SQLite beta data:
  `python scripts/backup_restore_drill.py --database-uri sqlite:////absolute/path/to/dnd_ai_dm.db`
- Migration chain drill:
  `python scripts/migration_chain_drill.py`
- Hosted cookie-only account auth smoke:
  `python scripts/hosted_cookie_auth_smoke.py --evidence-report tmp/release/hosted-cookie-auth-evidence.md`
- Hosted cookie-only account auth smoke against the deployed target:
  `make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_SMOKE_ARGS="--target-url https://aidm.example.com --account-intent signup --evidence-report tmp/release/hosted-cookie-auth-evidence.md"`
- Non-admin forbidden-response smoke:
  `python scripts/security_forbidden_smoke.py --evidence-report tmp/release/security-forbidden-evidence.md`
- Non-admin forbidden-response smoke against the deployed target:
  `make security-forbidden-smoke SECURITY_FORBIDDEN_SMOKE_ARGS="--target-url https://aidm.example.com --account-token <non-admin-token> --workspace-id <workspace-id> --campaign-id <campaign-id> --session-id <session-id> --evidence-report tmp/release/security-forbidden-evidence.md"`
- Session export/import smoke:
  `python scripts/session_export_import_smoke.py --evidence-report tmp/release/export-import-evidence.md`
- Session export/import smoke against the deployed target:
  `make session-export-import-smoke SESSION_EXPORT_IMPORT_SMOKE_ARGS="--target-url https://aidm.example.com --auth-token <operator-token> --workspace-id <workspace-id> --session-id <session-id> --player-id <player-id> --evidence-report tmp/release/export-import-evidence.md"`
- API type drift: `python scripts/generate_api_types.py` plus a clean
  `git diff --exit-code aidm_frontend/src/apiContract.generated.ts`
- Frontend tests, build, bundle budget, single-origin browser smoke against the built frontend, visual smoke screenshots, and visual-smoke review evidence
- Hosted RC evidence via `make hosted-rc-evidence` against the target URL, including deployment readiness, hosted cookie auth, non-admin forbidden responses, session export/import, beta SLO baseline, and the manual backup/restore, worker-process, and source-archive attachment proof flags needed to avoid `manual-evidence-required`
- Final operator sign-off via `make rc-finalize-signoff` after filling and merging `tmp/release/external-proof-values.json` with GitHub Actions URLs, hosted proof links, target env evidence, backup/restore proof, worker-process proof, telemetry receipt, source-archive attachment, issue-closure review, and packaging command evidence. Manual signoff edits still need `make operator-signoff-status OPERATOR_SIGNOFF_STATUS_ARGS="--require-complete"` before issue closure.
- Socket.IO worker-model decision:
  `python scripts/check_socketio_worker_model_decision.py`
- Observability bundle:
  `python scripts/check_observability_bundle.py`, plus
  `python scripts/check_observability_bundle.py --check-docker-compose --require-docker`
  on machines that should prove Docker Compose config
- Local beta SLO renderer proof:
  `make local-beta-slo-baseline`
- Deployment readiness:
  `python scripts/deployment_readiness_check.py --env-file /path/to/env --evidence-report tmp/release/deployment-readiness-evidence.md`
- Beta SLO baseline:
  `make beta-slo-baseline BETA_SLO_BASELINE_ARGS="--target-url https://aidm.example.com --auth-token <token> --workspace-id <workspace-id> --release RC1 --environment staging --output tmp/release/beta-slo-baseline.md"`

## Beta SLOs

Track these before inviting a wider group:

- DM response p95 latency
- AI provider failure rate
- Canon job failure rate
- Turn persistence failure rate
- Socket unauthorized and rate-limited event counts
- Average coherence feedback score
- Bad-turn report count by provider/model

Alert thresholds are owned by `AIDM_ALERT_OWNER` in the chosen
`AIDM_OBSERVABILITY_PROVIDER`. The local Prometheus/Grafana bundle under
`observability/` is useful for development and smoke testing; hosted beta
deployments should configure the managed destination named in production env.

## Operational Notes

- SQLite, disabled auth, wildcard CORS, in-memory rate limiting, in-memory turn
  coordination, local `.env.local` writes, and module-global Socket.IO state are
  local/private deployment conveniences. Hosted production uses an explicit
  `postgresql+psycopg` URI.
- `scripts/run_production_server.sh` requires `AIDM_ENV=production`. When that
  boundary is already present in the process environment, repo-local
  `.env.local` is ignored and an explicit `AIDM_ENV_FILE` is rejected if it
  attempts to downgrade the process to a non-production environment. Use an
  explicit secret-manager export or a production-only env file.
- For multiple backend workers, apply migrations through
  `0028_session_turn_lock_fencing`, use database-backed turn coordination and
  rate limiting, then choose `AIDM_SOCKETIO_WORKER_MODEL=sticky` with load
  balancer affinity or `message_queue` with `AIDM_SOCKETIO_MESSAGE_QUEUE`, and
  prove both stale-lease commit rejection and client event delivery in a
  staging smoke test.
- Session storage is acceptable for local/private beta. Hosted same-origin
  deployments can use the server-issued `HttpOnly` account cookie mode,
  suppress raw account tokens in JSON responses, and rely on the companion
  `aidm_csrf_token` double-submit header for unsafe REST requests.
