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

| Setting | Purpose |
| --- | --- |
| `AIDM_DATABASE_URI` | Database URI. Defaults to SQLite at `~/.aidm/dnd_ai_dm.db`. |
| `AIDM_LOCAL_DATA_DIR` | Directory used to build the default SQLite path. |
| `AIDM_SERVE_FRONTEND` | Serve the built frontend from Flask. Set by `make unified`. |
| `AIDM_FRONTEND_DIST_DIR` | Frontend `dist` path used by unified serving. |
| `AIDM_AUTH_REQUIRED` | Require bearer-token auth for REST and socket traffic. |
| `AIDM_API_AUTH_TOKENS` | Comma-separated bearer tokens. |
| `AIDM_API_AUTH_TOKEN_WORKSPACES` | Optional `workspace_id=token` bindings. |
| `AIDM_CORS_ALLOWLIST` | Comma-separated REST origins. |
| `AIDM_SOCKET_CORS_ALLOWLIST` | Comma-separated Socket.IO origins. |
| `AIDM_RATE_LIMIT_STORE` | `memory` or `database`. Use `database` for multi-process deployments. |
| `AIDM_TURN_COORDINATOR_STORE` | `memory` or `database`. Use `database` for multi-process deployments. |
| `AIDM_RULES_ENGINE_ENABLED` | Enables roll/check fairness helpers. |
| `AIDM_SEGMENT_EVALUATOR_ENABLED` | Enables authored segment trigger evaluation. |
| `AIDM_ADMIN_ENABLED` | Enables Flask-Admin in local/dev contexts. |
| `FLASK_SECRET_KEY` | Required when `AIDM_ENV=production`. |

## Common Commands

| Command | What it does |
| --- | --- |
| `make install` | Create `.venv`, install Python requirements, run frontend `npm ci`. |
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
| `make browser-smoke` | Run the frontend browser smoke script. |
| `make visual-smoke` | Run the frontend visual smoke script. |
| `make secrets` | Run the repo secret scanner. |
| `make api-types` | Regenerate frontend API contract types from backend routes. |
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
- health, metrics, TTS config, feedback, and beta summaries

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
- For multiple backend workers, use database-backed rate limits and turn
  coordination, plus deployment-level Socket.IO affinity or a shared queue.

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

No license file is currently declared in this repository. Treat the code as
private unless the repository owner adds an explicit license.
