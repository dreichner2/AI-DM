# AI-DM

AI-DM is a local-first AI Dungeon Master and tabletop companion for D&D-style
campaigns. It combines a Flask backend, a React/Vite frontend, Socket.IO live
play, persistent campaign state, campaign-pack tooling, combat helpers, maps,
music, and optional TTS into one playtestable app.

The project is actively developed for local and closed-beta use. It is not a
hosted SaaS product by default: the usual source of truth is your local runtime,
your local SQLite database, and the exact provider configuration loaded by the
running backend.

Updated for the current codebase in June 2026.

## What It Does

- Runs a full local DM session with worlds, campaigns, players, sessions, maps,
  story segments, inventory, equipment, XP, and persistent canon.
- Streams live play over Socket.IO with joined-session presence, typing status,
  turn control, music controls, clarification flows, and message submission.
- Uses an AI provider when configured, or a deterministic fallback provider when
  no key is available.
- Keeps session continuity through turn logs, state snapshots, rolling summary,
  emergent memory, campaign-pack progress, and projection/reprojection tools.
- Supports campaign packs with authored locations, NPCs, encounters, optional
  branches, checkpoints, visibility rules, and example packs in `docs/examples`.
- Includes D&D-lite fairness rails for rolls, checks, combat state, creature
  resolution, health, encounter flow, and authored-vs-discovered content.
- Provides local operator scripts for unified serving, launchd-backed backend
  restarts, health checks, Tailscale Funnel, smoke tests, secret scans, and
  bundle-budget checks.

## Quick Start

Prerequisites:

- Python 3.12, matching `.python-version`
- Node.js 24, matching `.nvmrc`
- npm
- macOS or another Unix-like shell environment

Install everything:

```bash
make install
```

Start the recommended single-origin local app:

```bash
make unified
```

Open:

```text
http://127.0.0.1:5050/
```

Verify the backend:

```bash
curl http://127.0.0.1:5050/api/health
```

`make unified` builds the frontend when needed, serves it from Flask, and keeps
the UI, `/api/*`, `/socket.io/*`, TTS, and shared session links on one origin.

## Development Mode

Run backend and frontend separately when you want Vite hot reload:

```bash
make backend
```

In another terminal:

```bash
make frontend
```

The Vite dev server proxies `/api/*` and `/socket.io/*` to the backend. Override
the proxy target when needed:

```bash
VITE_AIDM_PROXY_TARGET=http://127.0.0.1:5050 make frontend
```

## Configuration

Copy the example file when you want a persistent local provider setup:

```bash
cp .env.local.example .env.local
```

`scripts/run_local_backend.sh` loads `.env.local` automatically and selects a
provider in this order when `AIDM_LLM_PROVIDER` is not set:

1. Gemini, when `GOOGLE_GENAI_API_KEY` is present
2. DeepSeek, when `AIDM_DEEPSEEK_API_KEY` or `DEEPSEEK_API_KEY` is present
3. NVIDIA/Kimi, when `AIDM_NVIDIA_API_KEY` or `NVIDIA_API_KEY` is present
4. deterministic fallback, when no provider key is present

Common provider examples:

```bash
# Local fallback, no external AI call
AIDM_LLM_PROVIDER=fallback
AIDM_LLM_MODEL=deterministic-v1
```

```bash
# NVIDIA-hosted Kimi
AIDM_LLM_PROVIDER=nvidia
AIDM_NVIDIA_API_KEY=YOUR_KEY
AIDM_LLM_MODEL=moonshotai/kimi-k2.5
AIDM_NVIDIA_INVOKE_URL=https://integrate.api.nvidia.com/v1
```

```bash
# Gemini
AIDM_LLM_PROVIDER=gemini
GOOGLE_GENAI_API_KEY=YOUR_KEY
AIDM_LLM_MODEL=models/gemini-3-flash-preview
AIDM_LLM_FALLBACK_MODELS=models/gemini-2.5-flash
```

```bash
# DeepSeek
AIDM_LLM_PROVIDER=deepseek
AIDM_DEEPSEEK_API_KEY=YOUR_KEY
AIDM_LLM_MODEL=deepseek-v4-pro
```

Optional TTS:

```bash
AIDM_DEEPGRAM_API_KEY=YOUR_KEY
AIDM_DEEPGRAM_TTS_MODEL=aura-2-draco-en
```

Important runtime settings:

For hosted closed-beta deployments, start from `.env.production.example` and
replace every placeholder in the deployment provider's secret/env manager.

| Setting | Purpose |
| --- | --- |
| `AIDM_DATABASE_URI` | Database URI. Defaults to SQLite at `~/.aidm/dnd_ai_dm.db`. |
| `AIDM_LOCAL_DATA_DIR` | Directory used to build the default SQLite path. |
| `AIDM_SERVE_FRONTEND` | Serve the built frontend from Flask. Set by `make unified`. |
| `AIDM_FRONTEND_DIST_DIR` | Frontend `dist` path used by unified serving. |
| `AIDM_AUTH_REQUIRED` | Require bearer-token auth for REST and socket traffic. |
| `AIDM_API_AUTH_TOKENS` | Comma-separated bearer tokens. |
| `AIDM_API_AUTH_TOKEN_WORKSPACES` | Optional `workspace_id=token` bindings. |
| `AIDM_ACCOUNT_COOKIE_AUTH_ENABLED` | Enables server-issued `HttpOnly` account-session cookies. |
| `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED` | Return raw account tokens in account JSON responses. Set `false` for cookie-only hosted auth. |
| `AIDM_ACCOUNT_COOKIE_SAMESITE` | Account/CSRF cookie SameSite mode: `Lax`, `Strict`, or `None`. |
| `AIDM_SECURITY_HEADERS_ENABLED` | Emits CSP, frame, content-type, referrer, and permissions-policy headers. Required in production. |
| `AIDM_CONTENT_SECURITY_POLICY` | Optional CSP override for hosted frontend serving. |
| `AIDM_CORS_ALLOWLIST` | Comma-separated REST origins. |
| `AIDM_SOCKET_CORS_ALLOWLIST` | Comma-separated Socket.IO origins. |
| `AIDM_SOCKETIO_WORKER_MODEL` | Hosted production currently requires `single`; deferred values are documented in `docs/socketio_worker_model.md`. |
| `AIDM_SOCKETIO_ASYNC_MODE` | Hosted production requires `threading`. |
| `AIDM_GUNICORN_THREADS` | Gunicorn gthread capacity; defaults to 100 and production rejects values below 16. |
| `WEB_CONCURRENCY` | Must be `1` for the supported Socket.IO production topology. |
| `AIDM_SOCKETIO_MESSAGE_QUEUE` | Reserved for the deferred multi-worker design; not sufficient by itself to make process-local presence/music safe. |
| `AIDM_RATE_LIMIT_STORE` | `memory` or `database`. Use `database` for multi-process deployments. |
| `AIDM_TURN_COORDINATOR_STORE` | `memory` or `database`. Use `database` for multi-process deployments. |
| `AIDM_OBSERVABILITY_PROVIDER` | Required by production bootstrap to name the beta metrics/logging destination. |
| `AIDM_ALERT_OWNER` | Required by production bootstrap to name the beta alert owner. |
| `AIDM_RULES_ENGINE_ENABLED` | Enables roll/check fairness helpers. |
| `AIDM_SEGMENT_EVALUATOR_ENABLED` | Enables authored segment trigger evaluation. |
| `AIDM_ADMIN_ENABLED` | Enables Flask-Admin in local/dev contexts. |
| `FLASK_SECRET_KEY` | Required when `AIDM_ENV=production`. |

## Common Commands

| Command | What it does |
| --- | --- |
| `make install` | Create `.venv` with Python 3.12, install Python requirements, run frontend `npm ci`. Override `PYTHON_BOOTSTRAP` only with another Python 3.12 executable. |
| `make backend` | Start Flask through `scripts/run_local_backend.sh`. |
| `make frontend` | Start Vite on `127.0.0.1` for frontend development. |
| `make unified` | Build/reuse the frontend and serve the whole app from Flask on port 5050. |
| `make health` | Check local backend health with `scripts/check_local_health.sh`. |
| `make test` | Run the backend pytest suite. |
| `make lint` | Run frontend ESLint. |
| `make typecheck` | Run frontend TypeScript checks. |
| `make build` | Run TypeScript checks and Vite production build. |
| `make bundle-budget` | Check frontend bundle budget. |
| `make smoke` | Run the backend beta smoke flow. |
| `make scenario-regression` | Run deterministic scenario quality checks for narration, rules, state, and memory. |
| `make hosted-cookie-auth-smoke` | Prove hosted-mode cookie account auth, CSRF, role refresh, socket auth, and logout cleanup in an isolated runtime; add `HOSTED_COOKIE_AUTH_SMOKE_ARGS="--target-url <target-url> ..."` for hosted/staging proof. |
| `make security-forbidden-smoke` | Prove a non-admin account is rejected by combat operator, bestiary authoring/save, and beta operator endpoints; add `SECURITY_FORBIDDEN_SMOKE_ARGS="--target-url <target-url> --account-token <token> --workspace-id <id> --campaign-id <id> --session-id <id> --evidence-report tmp/release/security-forbidden-evidence.md"` for hosted/staging proof. |
| `make session-export-import-smoke` | Prove session export/import restores turn events into a new active session without duplicating raw source logs; add `SESSION_EXPORT_IMPORT_SMOKE_ARGS="--target-url <target-url> --auth-token <token> --workspace-id <id> --session-id <id> --player-id <id> --evidence-report tmp/release/export-import-evidence.md"` for hosted/staging proof. |
| `make hosted-rc-evidence` | Run the hosted/staging RC evidence plan as one command; pass `HOSTED_RC_EVIDENCE_ARGS="--target-url <url> --auth-token <operator-token> --workspace-id <id> --non-admin-token <token> --campaign-id <id> --session-id <id> ..."`. The command reports `manual-evidence-required` until backup/restore proof, worker-process proof, and source-archive attachment proof are supplied with the matching evidence flags, and rejects placeholder/example/localhost manual proof values. |
| `make beta-slo-baseline` | Render `tmp/release/beta-slo-baseline.md` from `/api/beta/slo` and `/api/beta/incidents`; pass `BETA_SLO_BASELINE_ARGS="--target-url <target-url> --auth-token <token> ..."`. |
| `make backup-restore-drill` | Create a non-destructive SQLite backup and verify a restored copy; pass `BACKUP_RESTORE_DRILL_ARGS="--database-uri sqlite:////path/to/dnd_ai_dm.db"` for a specific DB. |
| `make migration-chain-drill` | Run a non-destructive Alembic `upgrade head -> downgrade base -> upgrade head` drill against an isolated SQLite database. |
| `make observability-check` | Validate the Prometheus/Grafana observability bundle; pass `OBSERVABILITY_CHECK_ARGS="--check-docker-compose --require-docker"` for a Docker-backed compose check. |
| `make browser-smoke` | Run the frontend browser smoke script. |
| `make visual-smoke` | Run the frontend visual smoke script. |
| `make visual-smoke-review` | Review the latest visual-smoke screenshots and write `tmp/release/visual-smoke-review.md` plus JSON evidence. |
| `make frontend-npm-ci-evidence` | Run `npm ci` in `aidm_frontend` and write `tmp/release/frontend-npm-ci-evidence.md`/`.json` for final frontend lockfile-install signoff. |
| `make packaging-cleanup-evidence` | Verify `make clean`/`make clean-deps` cleanup coverage and source-archive exclusions without deleting `tmp/release`; writes `tmp/release/packaging-cleanup-evidence.md`/`.json`. |
| `make secrets` | Run the repo secret scanner. |
| `make api-types` | Regenerate frontend API contract types from backend routes. |
| `make socketio-worker-model-decision` | Verify the RC1 Socket.IO worker-model decision, production env template, production server command, and docs agree. |
| `make closed-beta-rc` | Run the full local closed-beta release-candidate gate. |
| `make closed-beta-rc-fast` | Run the RC gate without browser smoke or dependency audits for local iteration. |
| `make github-actions-evidence` | Render `tmp/release/github-actions-evidence.md`/`.json`; pass `GITHUB_ACTIONS_EVIDENCE_ARGS="--auto-gh --include-gh-details"` to discover run URLs plus read-only workflow/run diagnostics with `gh`, or pass `--ci-run-url <url> --closed-beta-rc-run-url <url>` manually. |
| `make rc-issue-evidence` | Render local RC evidence snippets for issues `#3`-`#9`. |
| `make rc-issue-closure-evidence` | Read generated issue snippets and GitHub issue state for `#3`-`#9`, then write `tmp/release/rc-issue-closure-evidence.md`/`.json` without posting or closing issues. |
| `make release-evidence-packet` | Render `tmp/release/release-evidence-packet.md`, a single handoff manifest for RC evidence, issue snippets, source archive, visual smoke, GitHub Actions evidence, hosted RC evidence, external proof inputs/execution plan, signoff draft/status, security/export-import evidence, deployment-readiness evidence, and beta SLO status. |
| `make rc-recommendation-matrix` | Render `tmp/release/rc-recommendation-matrix.md`/`.json`, mapping the original RC recommendations to current evidence and separating local implementation from hosted/manual proof. |
| `make external-proof-inputs` | Render `tmp/release/external-proof-inputs.md`/`.json`, a fillable hosted/GitHub/operator proof template generated from the release packet, recommendation matrix, and operator signoff action plan. |
| `make external-proof-execution-plan` | Render `tmp/release/external-proof-execution-plan.md`/`.json`, grouping remaining external proof into ordered candidate, GitHub Actions, hosted-readiness, hosted-smoke, manual-provider, and final-signoff phases. |
| `make operator-signoff-values-template` | Render `tmp/release/external-proof-values.example.json`; copy it locally to `tmp/release/external-proof-values.json` when filling proof links/paths. Sensitive token fields are intentionally omitted from the template; pass live auth tokens only through commands or a secret manager. |
| `make operator-signoff-from-inputs` | Render `tmp/release/operator-signoff.from-inputs.json` plus status artifacts from a filled `tmp/release/external-proof-values.json` for review before final operator signoff. The renderer rejects persisted token fields such as `operator_auth_token` or `non_admin_token`. |
| `make operator-signoff-draft` | Seed `tmp/release/operator-signoff.draft.json` from `tmp/release/release-evidence-packet.json`; only proven GitHub/hosted/manual evidence is marked provided, and local or placeholder proof remains pending. |
| `make operator-signoff-action-plan` | Render `tmp/release/operator-signoff-action-plan.md`/`.json` from the signoff draft and release packet, listing the exact commands, inputs, and evidence fields still needed for final signoff. |
| `make operator-signoff-status` | Render `tmp/release/operator-signoff-status.md`/`.json` from `tmp/release/operator-signoff.json`; start from `docs/rc_operator_signoff_manifest.example.json` and add `OPERATOR_SIGNOFF_STATUS_ARGS="--require-complete"` before final RC issue closure. Final signoff rejects placeholder commit/operator metadata, non-hosted or example `target_url` values, and provided evidence that still points at placeholder/example/localhost sources. |
| `make rc-handoff-artifacts` | Build the full local RC handoff bundle: source archive, issue snippets, recommendation matrix, external proof inputs, external proof execution plan, signoff values template, signoff-from-inputs preview, operator signoff status/draft/action plan, release evidence packet, and checklist status. |
| `make post-rc-issue-evidence` | Preview generated RC issue comments; add `POST_RC_ISSUE_EVIDENCE_ARGS="--post"` to post with `gh`. |
| `make deployment-readiness DEPLOYMENT_READINESS_ARGS="..."` | Validate hosted closed-beta env choices, require a PostgreSQL/psycopg round trip, and optionally check live `/api/health`/metrics/security headers; add `--evidence-report` to save a Markdown/JSON artifact. |
| `make db-upgrade` | Run Flask database migrations. |
| `make reproject-session SESSION_ID=...` | Rebuild projections for one session. |
| `make reproject-all` | Rebuild projections for all sessions. |

## Local Runtime Operations

The default local backend runs on:

```text
http://127.0.0.1:5050
```

The health endpoint is the quickest truth check for the running app:

```bash
curl http://127.0.0.1:5050/api/health
```

On this development machine, the launchd backend service is managed as
`local.aidm.backend`. The repo includes launch helpers and plists in
`scripts/launchd/`:

```bash
scripts/launch_backend_service.sh
launchctl list | grep local.aidm.backend
```

For Tailscale Funnel:

```bash
scripts/aidm_tailscale.sh status
scripts/aidm_tailscale.sh login
scripts/aidm_tailscale.sh funnel-on
scripts/aidm_tailscale.sh url
scripts/aidm_tailscale.sh funnel-off
```

Only expose the unified port (`5050`). For internet play, enable auth and share
tokens out of band:

```bash
AIDM_AUTH_REQUIRED=true \
AIDM_API_AUTH_TOKENS=choose-a-long-random-token \
make unified
```

## Architecture

AI-DM has four main runtime layers:

- `aidm_server/`: Flask REST API, Socket.IO handlers, SQLAlchemy models,
  migrations, provider registry, state engine, campaign-pack services, combat
  helpers, validation, telemetry, and deployment bootstrap.
- `aidm_frontend/`: React 19 + Vite 8 app with the session board, campaign rail,
  action composer, dice dialog, inspector, music/TTS controls, import panels,
  and workspace/session state hooks.
- `~/.aidm/dnd_ai_dm.db`: default local SQLite database for accounts,
  workspaces, campaigns, players, sessions, turn events, canon, state snapshots,
  campaign-pack progress, and projections.
- `docs/` and `scripts/`: authoring docs, campaign-pack schema/examples,
  operator runbooks, smoke tests, launch helpers, reprojection tools, and
  release checks.

The deeper design docs live here:

- `docs/architecture.md`
- `docs/runtime_state_boundaries.md`
- `docs/block_diagram.md`
- `docs/production-readiness.md`
- `docs/beta_runbook.md`
- `docs/release_checklist.md`
- `docs/roadmap.md`

## API Overview

The backend exposes REST endpoints under `/api` for:

- accounts, workspace login, workspace selection, and current account context
- worlds
- campaigns, campaign packs, example-pack import, canon, archive/restore/delete
- players, inventory, equipment, starting-loadout repair, and profile updates
- sessions, imported sessions, live logs, events, state, and pack progress
- maps and world-map segments
- runtime configuration
- creature generation, bestiary, and balance helpers
- health, metrics, TTS config, coherence/bad-turn feedback, beta summaries, beta incident feeds, and operator audit feeds

Socket.IO supports live play events including:

- `join_session`
- `leave_session`
- `send_message`
- `typing_status`
- `set_turn_control`
- `music_control`
- `resolve_clarification`

Regenerate the frontend API contract after backend route changes:

```bash
make api-types
```

## Campaign Packs

Campaign packs are authored JSON adventures with schema validation, visibility
rules, optional paths, encounter definitions, checkpoints, NPCs, items, and
stateful progress tracking.

Key files:

- `docs/campaign_packs.md`
- `docs/campaign_pack.schema.json`
- `docs/examples/bleakmoor_intro_campaign_pack.json`
- `docs/examples/shadow_over_the_greenway_campaign_pack.json`
- `docs/examples/shadow_under_eryn_luin_campaign_pack.json`
- `docs/examples/the_road_of_unremembered_kings_campaign.json`

Useful pack commands:

```bash
.venv/bin/python scripts/aidm_pack.py lint docs/examples/the_road_of_unremembered_kings_campaign.json
.venv/bin/python scripts/aidm_pack.py preview docs/examples/the_road_of_unremembered_kings_campaign.json
.venv/bin/python scripts/aidm_pack.py graph docs/examples/the_road_of_unremembered_kings_campaign.json
.venv/bin/python scripts/aidm_pack.py test-checkpoints docs/examples/the_road_of_unremembered_kings_campaign.json
```

## Testing And Verification

Backend:

```bash
.venv/bin/python -m pytest
```

Frontend:

```bash
cd aidm_frontend
npm run test
npm run build
npm run bundle:budget
```

Smoke and safety checks:

```bash
make smoke
make browser-smoke
make visual-smoke
make visual-smoke-review
make secrets
pip-audit -r requirements.txt
```

Before a beta or public playtest, also run:

```bash
make health
curl http://127.0.0.1:5050/api/health
curl http://127.0.0.1:5050/api/tts/config
```

The CI workflow mirrors the important local gates: backend tests, frontend
tests/build, bundle budget, secret scanning, and dependency/security checks.
The manual `Closed Beta RC` workflow also uploads a `closed-beta-rc-evidence`
artifact with the RC report, issue snippets, release evidence packet, source
archive, visual-smoke screenshots/review evidence, and GitHub Actions run URL
evidence when those files are produced.

Closed-beta handoff docs:

- `docs/release_checklist.md` tracks the RC1 gate criteria.
- `docs/beta_runbook.md` covers operator startup, incident review, and safe
  beta flags.
- `docs/beta_tester_onboarding.md` is the invite-ready tester guide.
- `docs/auth_modes.md` maps local, private, and hosted auth modes.

## Database And Migrations

The default local database is:

```text
~/.aidm/dnd_ai_dm.db
```

Override it with:

```bash
AIDM_DATABASE_URI=sqlite:////absolute/path/to/dnd_ai_dm.db
```

Run migrations:

```bash
make db-upgrade
```

Projection repair:

```bash
make reproject-session SESSION_ID=<session-id>
make reproject-all
```

Use the live DB when investigating gameplay issues. Repo-local SQLite files are
not necessarily what the running backend is serving.

## Security Notes

- Do not commit `.env.local` or real API keys.
- Rotate any API key that appears in logs, chat, screenshots, commits, or issue
  comments.
- Set `AIDM_ENV=production` and `FLASK_SECRET_KEY` before production-style use.
- Set `AIDM_AUTH_REQUIRED=true` before exposing the app outside your machine.
- Use HTTPS for public play. Tailscale Funnel or a production reverse proxy can
  provide the public TLS edge.
- Keep `AIDM_CORS_ALLOWLIST` and `AIDM_SOCKET_CORS_ALLOWLIST` narrow outside
  local development.
- Production bootstrap requires `AIDM_OBSERVABILITY_PROVIDER`, `AIDM_ALERT_OWNER`,
  and an explicit `AIDM_SOCKETIO_WORKER_MODEL`.
- For the first hosted closed beta, use the `single` worker-model decision in
  `docs/socketio_worker_model.md`: `AIDM_SOCKETIO_WORKER_MODEL=single`,
  `AIDM_SOCKETIO_ASYNC_MODE=threading`, `AIDM_GUNICORN_THREADS=100`, and
  `WEB_CONCURRENCY=1`.
- For hosted same-origin auth, enable `AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=true`,
  keep `AIDM_ACCOUNT_COOKIE_SECURE=true`, and set
  `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false` when browser JavaScript should not
  receive raw account tokens. Cookie-authenticated unsafe REST requests use the
  companion `aidm_csrf_token` cookie and `X-AIDM-CSRF-Token` header.
- Multiple backend workers are not yet a supported production topology. Before
  enabling them, move presence/music state to shared storage and prove both
  load-balancer affinity and shared Socket.IO queue delivery in staging.

## Troubleshooting

Backend virtualenv is missing:

```bash
make install
```

Frontend dependencies are stale:

```bash
cd aidm_frontend
npm ci
```

Unified app serves an old frontend:

```bash
AIDM_FRONTEND_BUILD_MODE=always make unified
```

Health check fails:

```bash
make health
tail -n 200 tmp/launcher_logs/backend.log tmp/launcher_logs/launcher.log
```

Provider does not match what you expected:

```bash
curl http://127.0.0.1:5050/api/health
.venv/bin/python scripts/check_llm_provider.py
```

Tailscale URL is not available:

```bash
scripts/aidm_tailscale.sh login
scripts/aidm_tailscale.sh funnel-on
scripts/aidm_tailscale.sh url
```

## Project Map

```text
aidm_server/                 Flask app, API, sockets, state engine, providers
aidm_frontend/               React/Vite frontend
docs/                        Architecture, runbooks, schema, examples
docs/examples/               Campaign-pack examples
migrations/                  Alembic migrations
scripts/                     Local runtime, CI, authoring, repair tools
tests/                       Backend tests
requirements*.txt            Python runtime/dev/constraint files
Makefile                     Main local command surface
```

## License

This repository is public for visibility and closed-beta collaboration only.
See [LICENSE](LICENSE). No open-source license is granted unless the repository
owner replaces that notice with an explicit open-source license.
