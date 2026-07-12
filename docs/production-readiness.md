# Production Readiness

Use this as the closed-beta deployment checklist. Local launcher behavior is
useful for development, but it is not the production boundary.

The tracked repository defines the application entrypoint, environment
contract, migrations, CI rehearsals, and release-evidence tooling. It does not
contain a provider-specific application deployment manifest (for example a
Render service blueprint), so dashboard build/start commands, replica count,
network allowlists, managed backups, and telemetry destinations must be
captured as external release evidence rather than inferred from this document.

## Required Configuration

Use `.env.production.example` as the placeholder template for deployment
secret/env managers. Choose the matching exposure mode in
`docs/auth_modes.md` before finalizing auth, cookie, and CORS settings.

- `AIDM_ENV=production`
- `AIDM_DEBUG=false`
- `FLASK_SECRET_KEY=<strong explicit secret>`
- `AIDM_DATABASE_URI=postgresql+psycopg://...` with a reachable PostgreSQL
  database; hosted readiness rejects implicit or SQLite databases
- `AIDM_AUTH_REQUIRED=true`
- `AIDM_API_AUTH_TOKENS` or `AIDM_API_AUTH_TOKEN_WORKSPACES` configured
- `AIDM_AUTO_CREATE_SCHEMA=false`
- `AIDM_RATE_LIMIT_STORE=database`
- `AIDM_TURN_COORDINATOR_STORE=database`
- `AIDM_SOCKETIO_WORKER_MODEL=single`; other values are rejected in hosted
  production until presence/music state is shared across processes
- `AIDM_SOCKETIO_ASYNC_MODE=threading`
- `AIDM_GUNICORN_THREADS=100` (production minimum: 16)
- `WEB_CONCURRENCY=1`
- `AIDM_OBSERVABILITY_PROVIDER=<provider-name>`
- `AIDM_ALERT_OWNER=<team-or-person>`
- `AIDM_SECURITY_HEADERS_ENABLED=true`
- `AIDM_ADMIN_ENABLED=false`; the Flask-Admin model UI is local/development
  tooling and is not part of the hosted production surface
- Explicit REST and Socket.IO CORS allowlists, unless same-origin deployment
  intentionally leaves them empty
- `AIDM_LLM_PROVIDER=<configured-live-provider>` plus the provider-specific
  credential/runtime settings described below; deterministic `fallback` is an
  explicit safe-mode exception, not hosted release proof
- `AIDM_SERVE_FRONTEND=true` only when the production build includes
  `aidm_frontend/dist`; otherwise serve the frontend separately and leave it
  false
- For hosted cookie-only account auth:
  `AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=true`,
  `AIDM_ACCOUNT_COOKIE_SECURE=true`, and
  `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false`

## Startup

1. Provision exact Python 3.14.6. For the repository-managed virtual
   environment, create it and install the constrained, hash-locked runtime:

   ```bash
   python3.14 -m venv .venv
   .venv/bin/python -m pip install --constraint requirements.constraints.txt --upgrade pip
   .venv/bin/python -m pip install --require-hashes -r requirements.runtime.lock.txt
   ```

   `make install` installs the larger development/test environment instead.
2. When `AIDM_SERVE_FRONTEND=true`, provision Node 24.18.0 and npm 12.0.0,
   then build the tracked frontend:

   ```bash
   (cd aidm_frontend && npm ci && npm run build)
   ```

   The default served directory is `aidm_frontend/dist`. Set
   `AIDM_FRONTEND_DIST_DIR` only when the deployment copies the build elsewhere.
3. Apply migrations with `make db-upgrade`.
4. Run `.venv/bin/python scripts/deploy_bootstrap.py --check-only`.
5. Run deployment readiness against the target environment and, when available,
   the deployed target URL:
   `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file /path/to/env --target-url https://aidm.example.com --auth-token <token> --evidence-report tmp/release/deployment-readiness-evidence.md"`.
   Add `--same-origin-deployment`, `--auth-storage-exception`, or
   `--socketio-staging-proof` only when those deployment choices are
   intentionally documented. Use `.json` as the evidence report suffix when a
   structured artifact is better for CI or release automation.
   The environment check opens `AIDM_DATABASE_URI` and runs `SELECT 1`; a
   syntactically valid but unreachable database does not pass readiness.
6. Start AIDM with a production Socket.IO-capable server. Do not use
   `deploy_bootstrap.py` as the production server process. For the first
   closed-beta single-worker deployment, use the decision in
   `docs/socketio_worker_model.md`:

   ```bash
   AIDM_ENV=production \
   AIDM_SOCKETIO_WORKER_MODEL=single \
   AIDM_SOCKETIO_ASYNC_MODE=threading \
   AIDM_GUNICORN_THREADS=100 \
   WEB_CONCURRENCY=1 \
   PORT=5050 \
   scripts/run_production_server.sh
   ```

   To inspect the exact Gunicorn command without starting a server, run
   `scripts/run_production_server.sh --print`. A real start always runs
   migrations and the deployment bootstrap preflight before Gunicorn execs.
7. After startup, verify `GET /api/health`, authenticated metrics, and the forced
   WebSocket probe through `make deployment-readiness`. When
   `AIDM_SERVE_FRONTEND=true`, also verify `GET /` returns the built SPA rather
   than `frontend_not_built`, and request one emitted `/assets/*` file through
   the deployed edge. The current deployment-readiness script does not perform
   these two frontend checks.

## LLM Provider Requirements

- `gemini` uses `GOOGLE_GENAI_API_KEY`; `deepseek` uses
  `AIDM_DEEPSEEK_API_KEY`; `nvidia` and `kimi` use `AIDM_NVIDIA_API_KEY` with
  their configured OpenAI-compatible endpoint.
- `codex_cli` requires an available Codex executable and either a dedicated
  persistent, AIDM-only `AIDM_CODEX_HOME` containing `auth.json` or
  `AIDM_CODEX_ACCESS_TOKEN`. The production launcher rejects startup when these
  prerequisites are missing. Gameplay calls run in a disposable empty,
  read-only, network-disabled, tool-free workspace; do not point
  `AIDM_CODEX_HOME` at an operator's general-purpose Codex profile.
- `fallback` is deterministic and requires no key. Deployment readiness rejects
  it unless `--allow-fallback-provider` is passed for an intentional safe-mode
  drill.

Provider/model availability and third-party service guarantees are external
facts. Record the selected provider/model from `/api/health` and retain the
provider-specific deployment evidence for the signed-off commit.

## CI Gates

- Secret scan: `.venv/bin/python scripts/scan_secrets.py`
- Python dependency audit:
  `.venv/bin/python -m pip_audit -r requirements.runtime.lock.txt`
- Python correctness lint:
  `.venv/bin/python -m ruff check --select E9,F63,F7,F82 aidm_server tests scripts`
- Backend tests: `.venv/bin/python -m pytest`
- Current-candidate backend/frontend regressions prove server-authoritative dice,
  one durable roll per idempotency key, duplicate/reconnect reconciliation, and
  account-scoped session/export player projections. These rows only pass in
  `make release-checklist-status` when the full RC packet matches the clean,
  signed-off current commit; the forbidden-response smoke is not substitute
  evidence for successful `200` response redaction.
- PostgreSQL production rehearsal: the `postgres-integration` GitHub Actions
  job applies the migration chain, runs production bootstrap, starts the real
  Gunicorn threaded entrypoint with `simple-websocket`, checks live health, metrics, Prometheus output,
  and security headers, exercises concurrency fencing, and runs the cookie-auth,
  forbidden-response, and export/import smokes against PostgreSQL. The job
  uploads the resulting Markdown as the `postgres-production-rehearsal`
  artifact. This remote rehearsal complements, but does not replace, proof
  against the actual hosted staging target and its managed backup/telemetry
  providers.
- Backup/restore drill for local/private SQLite beta data:
  `.venv/bin/python scripts/backup_restore_drill.py --database-uri sqlite:////absolute/path/to/dnd_ai_dm.db`
- Guarded PostgreSQL custom-archive restore drill against a separately supplied
  empty database: `make postgres-backup-restore-drill POSTGRES_BACKUP_RESTORE_DRILL_ARGS="--source-uri-file /secure/source-uri --empty-target-uri-file /secure/empty-target-uri"`
- Migration chain drill:
  `.venv/bin/python scripts/migration_chain_drill.py`
- Hosted cookie-only account auth smoke:
  `.venv/bin/python scripts/hosted_cookie_auth_smoke.py --evidence-report tmp/release/hosted-cookie-auth-evidence.md`
- Hosted cookie-only account auth smoke against the deployed target:
  `make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_SMOKE_ARGS="--target-url https://aidm.example.com --account-intent signup --evidence-report tmp/release/hosted-cookie-auth-evidence.md"`
- Preferred credential-minimizing hosted proof suite:
  `make hosted-cookie-release-proof HOSTED_COOKIE_RELEASE_PROOF_ARGS="--target-url https://aidm.example.com --account-intent signup"`.
  This reuses the two throwaway cookie sessions to generate cookie-auth,
  non-admin forbidden, export/import, and beta SLO/incident evidence without
  bearer-token arguments, then removes the proof sessions and workspace. The
  current API has no account-deletion endpoint, so signup-mode account rows
  remain after their memberships and game data are removed; use dedicated
  pre-provisioned login accounts when that residue is unacceptable.
- Non-admin forbidden-response smoke:
  `.venv/bin/python scripts/security_forbidden_smoke.py --evidence-report tmp/release/security-forbidden-evidence.md`
- Non-admin forbidden-response smoke against the deployed target:
  `make security-forbidden-smoke SECURITY_FORBIDDEN_SMOKE_ARGS="--target-url https://aidm.example.com --account-token <non-admin-token> --workspace-id <workspace-id> --campaign-id <campaign-id> --session-id <session-id> --evidence-report tmp/release/security-forbidden-evidence.md"`
- Session export/import smoke:
  `.venv/bin/python scripts/session_export_import_smoke.py --evidence-report tmp/release/export-import-evidence.md`
- Session export/import smoke against the deployed target:
  `make session-export-import-smoke SESSION_EXPORT_IMPORT_SMOKE_ARGS="--target-url https://aidm.example.com --auth-token <operator-token> --workspace-id <workspace-id> --session-id <session-id> --player-id <player-id> --evidence-report tmp/release/export-import-evidence.md"`
- API type drift: `.venv/bin/python scripts/generate_api_types.py` plus a clean
  `git diff --exit-code aidm_frontend/src/apiContract.generated.ts`
- Frontend tests, build, bundle budget, single-origin browser smoke against the built frontend, visual smoke screenshots, and visual-smoke review evidence
- Hosted RC evidence via `make hosted-rc-evidence` against the target URL, including deployment readiness, hosted cookie auth, non-admin forbidden responses, session export/import, beta SLO baseline, and the manual backup/restore, worker-process, and source-archive attachment proof flags needed to avoid `manual-evidence-required`
- Before wider beta, capture hosted two-account evidence that session
  state/export responses preserve the requester's character, redact the peer,
  reject explicit peer export selection, and keep admin inspection complete.
- Final operator sign-off via `make rc-finalize-signoff` after filling and merging `tmp/release/external-proof-values.json` with GitHub Actions URLs, hosted proof links, target env evidence, backup/restore proof, worker-process proof, telemetry receipt, source-archive attachment, issue-closure review, and packaging command evidence. Manual signoff edits still need `make operator-signoff-status OPERATOR_SIGNOFF_STATUS_ARGS="--require-complete"` before issue closure.
- Socket.IO worker-model decision:
  `.venv/bin/python scripts/check_socketio_worker_model_decision.py`
- Observability bundle:
  `.venv/bin/python scripts/check_observability_bundle.py`, plus
  `.venv/bin/python scripts/check_observability_bundle.py --check-docker-compose --require-docker`
  on machines that should prove Docker Compose config
- Local beta SLO renderer proof:
  `make local-beta-slo-baseline`
- Deployment readiness:
  `.venv/bin/python scripts/deployment_readiness_check.py --env-file /path/to/env --evidence-report tmp/release/deployment-readiness-evidence.md`
- Beta SLO baseline:
  `make beta-slo-baseline BETA_SLO_BASELINE_ARGS="--target-url https://aidm.example.com --auth-token <token> --workspace-id <workspace-id> --release RC1 --commit-sha <signed-off-commit-sha> --environment staging --invite-more-testers <yes-or-no> --output tmp/release/beta-slo-baseline.md"`

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
See `docs/observability.md` for the bundle's trusted-local assumptions and
insecure development defaults.

## Operational Notes

- Passwordless legacy recovery is operator-mediated. Verify the requester out
  of band, then run `.venv/bin/python scripts/issue_legacy_recovery_code.py
  --username <username> --confirm-production` in an authorized target shell.
  The command rotates the stored account-token hash and prints the raw recovery
  code once; deliver it privately and never retain it in tickets or logs.
- SQLite, disabled auth, wildcard CORS, in-memory rate limiting, in-memory turn
  coordination, local `.env.local` writes, and module-global Socket.IO state are
  local/private deployment conveniences. Hosted production uses an explicit
  `postgresql+psycopg` URI.
- `scripts/run_production_server.sh` requires `AIDM_ENV=production`. When that
  boundary is already present in the process environment, repo-local
  `.env.local` is ignored and an explicit `AIDM_ENV_FILE` is rejected if it
  attempts to downgrade the process to a non-production environment. Use an
  explicit secret-manager export or a production-only env file.
- Multiple backend workers remain deferred. Fencing and database-backed rate
  limits are necessary but not sufficient: presence/music state must move out
  of process, and staging must prove both load-balancer affinity and shared
  Socket.IO queue delivery before production accepts this topology.
- Session storage is acceptable for local/private beta. Hosted same-origin
  deployments can use the server-issued `HttpOnly` account cookie mode,
  suppress raw account tokens in JSON responses, and rely on the companion
  `aidm_csrf_token` double-submit header for unsafe REST requests.
