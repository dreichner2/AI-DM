# Operator Tools

This guide classifies repository scripts by side effect. Run Python tools from
the repository root with `.venv/bin/python`, inspect source or `--help` for tools
that implement argument parsing before a production run, and use the exact Make
target when one exists. Scripts do not all load environment files the same way.

## Configuration And Credential Changes

| Tool | Effect | Safety boundary |
| --- | --- | --- |
| `scripts/add_friend_token.py [workspace] [token]` | Enables auth and adds or replaces a workspace-token mapping in `.env.local` (or `--env-file`) | Mutates the env file, sets mode `0600`, prints the token, and requires a backend restart. Protect terminal logs. |
| `.venv/bin/python scripts/issue_legacy_recovery_code.py --username <username>` | Rotates a passwordless legacy account to a new high-entropy account recovery code and prints it once | Verify the requester out of band first. The command updates the selected database, invalidates any old saved account token, and never stores the raw code. Use `--confirm-production` in production, deliver the code privately, and do not paste it into tickets or logs. |
| `scripts/configure_local_kimi.sh` | Prompts for an NVIDIA/Kimi key and selects Kimi for the main provider | Replaces the entire repository `.env.local`; back up or manually merge any existing settings first. The resulting file contains a secret. |
| `POST`/`PATCH /api/llm/config` | Changes process-wide runtime provider configuration | Requires `admin_workspace` on the default/owner workspace; an admin capability on another workspace is insufficient. In local/test, `persist` defaults to `true` and writes the selected provider/model to `.env.local` or `AIDM_ENV_FILE` with mode `0600`; send `"persist": false` for a process-only change. Production rejects persistence. Use `GET /api/llm/config` to inspect current state. See [LLM provider routing](llm_provider_routing.md). |

Do not commit `.env.local`, access tokens, saved provider authentication, or raw
support/evaluation artifacts.

## Live External Calls

| Tool | External effect |
| --- | --- |
| `scripts/check_llm_provider.py` | Sends a real prompt to the configured provider and can use quota or incur cost. |
| `scripts/list_gemini_models.py` | Calls the Gemini models API using `GOOGLE_GENAI_API_KEY`. |
| `scripts/compare_helper_profiles.py` | Can make many provider calls; optional raw output can contain prompts and campaign data. |
| `scripts/compare_tactics_compilers.py` | Makes provider calls across selected compiler profiles/cases. |
| `scripts/evaluate_combat_helpers.py` | May invoke configured helpers depending on snapshot flags and settings. |
| `scripts/hosted_cookie_auth_smoke.py`, `hosted_rc_evidence_check.py`, `security_forbidden_smoke.py` | With `--target-url`, exercise that hosted service; use only against an authorized environment. Some also support an isolated local database mode. |

Both live provider utilities implement `--help`. Help and invalid arguments are
handled before runtime environment loading or any provider/API call. Running
either utility without arguments performs the live action described above.

Provider/evaluation details and routing precedence are documented in
[LLM provider routing](llm_provider_routing.md).

## Local Runtime And Database Tools

| Command or tool | Effect and boundary |
| --- | --- |
| `make backend` / `scripts/run_local_backend.sh` | Starts the local backend using the repository runtime environment. |
| `make frontend` | Starts the Vite frontend on the local development origin. |
| `make unified` / `scripts/run_unified_local.sh` | Builds/serves the frontend and backend from one local origin. |
| `scripts/run_production_server.sh` | Starts the production Gunicorn/Socket.IO process after configuration and migration preconditions are satisfied. |
| `scripts/deploy_bootstrap.py` | Wrapper for `aidm_server.deploy_bootstrap`; can migrate/check and can serve only in supported non-production modes. Production uses check-only preflight. |
| `make db-upgrade` | Applies Alembic migrations to the configured database. Back up important data first. |
| `scripts/reproject_session.py --session-id ID --dry-run` | Rebuilds legacy projections from `turn_events` and rolls back. Omitting `--dry-run` commits; `--all` targets every session. `--create-schema` is rejected in production. |
| `make backup-restore-drill`, `make postgres-backup-restore-drill`, `make migration-chain-drill` | Creates temporary drill data/artifacts and exercises backup or migration paths. Confirm database arguments and output paths before use. |
| `scripts/session_export_import_smoke.py` | Creates and imports test session data in its configured target; do not point it at an important database casually. |

The default local SQLite database is `~/.aidm/dnd_ai_dm.db`; production requires
a `postgresql+psycopg` URI. A tool that loads runtime configuration can therefore
target production if the shell or env file says so.

## Local Validation

Preferred entry points are:

```bash
make test
make dev-check
make api-types
make state-writers
make socketio-worker-model-decision
make deployment-readiness
make observability-check
```

`make dev-check` compiles Python, runs the configured Ruff error rules, scans for
secrets, verifies generated API types and request parsing, validates
observability/state-writer/Socket.IO decisions, drills the migration chain, and
runs frontend typecheck and lint. It is broader than an individual checker but
does not replace backend or frontend test suites.

Other focused tools include `scripts/scan_secrets.py`,
`check_request_json_parsing.py`, `check_state_snapshot_writers.py`,
`check_socketio_worker_model_decision.py`,
`check_release_artifact_consistency.py`, and
`check_observability_bundle.py`. Generators and renderers can update files under
their requested output path; use `--check` or a temporary output when available
for a read-only run.

## Release-Candidate Evidence And GitHub Writes

The `Makefile` is the command map. Current workflow and evidence requirements
are maintained in [beta runbook](beta_runbook.md),
[production readiness](production-readiness.md), and
[release checklist](release_checklist.md). Avoid copying a complete release
sequence into another document because targets and evidence fields evolve
together.

Most `render_*` and `check_*` RC tools read local/external evidence and write
artifacts under `tmp/release/`; they do not by themselves prove a hosted check
was executed. Preserve the source evidence and distinguish a dry-run plan from a
real result.

`scripts/post_rc_issue_evidence.py` is preview-only by default. `--post` invokes
the GitHub CLI to publish issue comments; `--close` additionally closes issues
and requires `--post`. The tool refuses to close evidence with remaining
exceptions unless `--allow-external-exceptions` is explicitly supplied. Review
the preview and selected `--issues` before authorizing either external write.

## Pack Authoring

`scripts/aidm_pack.py` and the campaign pack lint/forge/import API operate on
campaign-pack manifests. Use [campaign packs](campaign_packs.md) for manifest
schema, authoring, linting, and import behavior. Keep source packs separate from
runtime/export artifacts so validation does not accidentally consume generated
files.
