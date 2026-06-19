# AI-DM Beta Runbook

## Startup
1. Set environment variables (`AIDM_ENV`, `AIDM_DATABASE_URI`, `AIDM_AUTH_REQUIRED`, `AIDM_API_AUTH_TOKENS`, and the selected provider key such as `GOOGLE_GENAI_API_KEY`, `AIDM_DEEPSEEK_API_KEY`, or `AIDM_NVIDIA_API_KEY`).
   Choose the exposure/auth posture from `docs/auth_modes.md` before sharing a
   non-loopback URL.
2. For local/private socket runtime, keep `AIDM_SOCKETIO_ASYNC_MODE=threading`
   unless you intentionally switch modes, and explicitly choose
   `AIDM_SOCKETIO_WORKER_MODEL=single`, `sticky`, or `message_queue`.
   For hosted RC1, use the single-worker decision in
   `docs/socketio_worker_model.md`.
3. Install dependencies: `python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt` for local development, or use `requirements.runtime.txt` for a minimal runtime without pytest/admin/migration UI tooling. Both paths apply `requirements.constraints.txt` for repeatable direct dependency versions.
4. Apply migrations: `flask db upgrade` (or run bootstrap command below).
5. Bootstrap check/start command:
   - Check only: `.venv/bin/python scripts/deploy_bootstrap.py --check-only`
   - Local/private start after checks: `.venv/bin/python scripts/deploy_bootstrap.py`
   - Hosted single-worker start after checks:
     `AIDM_SOCKETIO_WORKER_MODEL=single AIDM_SOCKETIO_ASYNC_MODE=eventlet WEB_CONCURRENCY=1 PORT=5050 scripts/run_production_server.sh`
6. For local/private SQLite beta data, run `make backup-restore-drill` before real play sessions or pass `BACKUP_RESTORE_DRILL_ARGS="--database-uri sqlite:////absolute/path/to/dnd_ai_dm.db"` for a specific database. The drill creates a backup and verifies a restored copy without writing to the source DB.
7. Run `make migration-chain-drill` to prove Alembic can apply the full chain, downgrade to base, and re-apply the full chain against an isolated SQLite database.
8. Run `make socketio-worker-model-decision` to verify the hosted RC1
   worker-model decision, production env template, production server command,
   and docs agree.
9. For a release-candidate rehearsal, run `make closed-beta-rc`. For local iteration without browser/dependency gates, run `make closed-beta-rc-fast`. To save gate evidence for an issue or release note, run the checker directly with `--evidence-report` or a specific path such as `tmp/release/rc-evidence.md`. The manual GitHub Actions `Closed Beta RC` workflow uploads the `closed-beta-rc-evidence` artifact with the RC report, issue snippets, release evidence packet, source archive, security/export-import evidence, visual-smoke screenshots/review evidence, and GitHub Actions run URL evidence when available. Before dispatching the manual workflow, run `make github-actions-rc-plan`; after the signed-off candidate is clean, use `GITHUB_ACTIONS_RC_PLAN_ARGS="--dispatch-closed-beta-rc"` to dispatch from the same helper. The `make rc-handoff-artifacts` target refreshes GitHub Actions evidence with read-only `gh` discovery; after CI or the manual RC workflow changes, rerun `make github-actions-evidence GITHUB_ACTIONS_EVIDENCE_ARGS="--auto-gh --include-gh-details --verify-closed-beta-rc-artifact-contents"` directly or pass the run URLs manually. Use `docs/rc_issue_evidence_template.md` when closing gate issues.
   For hosted/staging sign-off, run `make hosted-rc-evidence` with
   `HOSTED_RC_EVIDENCE_ARGS` set for the target URL, env file, operator token,
   workspace/session/player IDs, and non-admin token. The report at
   `tmp/release/hosted-rc-evidence.md` records automated hosted proof plus any
   manual evidence still required for provider backup/restore, worker process
   proof, source-archive attachment, and external telemetry receipt. The hosted
   RC evidence command exits with `manual-evidence-required` until those four
   manual proof links or paths
   are passed through `--hosted-backup-restore-evidence`,
   `--hosted-worker-process-evidence`, and
   `--source-archive-attachment-evidence`, and
   `--external-telemetry-receipt`. Placeholder, example, localhost, and
   isolated-runtime manual proof values are rejected as invalid.
   The same command also writes a non-sensitive values fragment to
   `tmp/release/external-proof-values.hosted-rc.json`. After the hosted RC
   evidence status is `passed` and
   `tmp/release/external-proof-values.json` has been created from the template
   with operator proof values, run `make external-proof-values-merge` to merge
   that fragment into the values file. The merge helper refuses
   planned/unusable fragments, rejects persisted token fields, and requires the
   existing operator values file unless `--allow-missing-existing` is passed
   deliberately for a one-off bootstrap. After the merge and any remaining
   manual proof links or paths are filled, run `make rc-finalize-signoff`; it
   writes the final `tmp/release/operator-signoff.json`, records the generated
   signoff status back into `tmp/release/external-proof-values.json`, requires
   the external proof values check to pass, and refreshes the release
   packet/checklist.
   Before closing the RC gate issues, run `make rc-handoff-artifacts` after the
   latest `make closed-beta-rc` evidence pass. This records frontend `npm ci`
   evidence, creates the source archive, refreshes a planned hosted RC command
   artifact when no real hosted evidence exists, preserves any existing real
   hosted RC evidence, and renders issue snippets, recommendation matrix,
   external proof input template, external proof execution plan, signoff
   values template, external proof values status, signoff-from-inputs preview,
   release evidence packet, operator signoff status, draft, and action plan.
   Use the matrix for the high-level original-recommendation status, then use
   `tmp/release/external-proof-inputs.md` as the fillable list of hosted,
   GitHub, and operator evidence fields. If you want a structured local fill-in
   file, copy `tmp/release/external-proof-values.example.json` to
   `tmp/release/external-proof-values.json`, keep or update any pre-seeded
   non-secret evidence from the current packet, fill remaining proof links/paths
   only, leave the intentionally omitted token fields out of that file, and run
   `make external-proof-values-check` before `make operator-signoff-from-inputs`
   to catch missing required fields, placeholder metadata, conditional Socket.IO
   staging proof, and accidentally persisted token values. The signoff renderer
   also rejects persisted `operator_auth_token` and `non_admin_token` values.
   GitHub Actions URLs are intentionally not pre-seeded as final signoff proof
   until the packet shows the release candidate was regenerated from a clean
   signed-off worktree.
   Review the draft/action plan, then prefer `make rc-finalize-signoff` once
   the external proof values file has the remaining GitHub Actions URLs, hosted
   proof links, backup/restore proof, worker-process proof, telemetry receipt,
   source-archive attachment, issue-closure review, `npm ci`, `make clean`,
   and `make clean-deps` evidence. If you manually copy reconciled values into
   `tmp/release/operator-signoff.json`, still run:
   `make operator-signoff-status OPERATOR_SIGNOFF_STATUS_ARGS="--require-complete"`.
   Final signoff also requires a real hosted/staging `target_url`, signed-off
   commit SHA, operator name, and ISO timestamp; placeholder or example values
   are treated as invalid. Provided evidence rows are also rejected when they
   still point at placeholder, example, localhost, or isolated-runtime sources.
10. For operator incident evidence, review the selected-session Session Quality card
   in the Ops tab or request `/api/beta/session-quality?session_id=<session-id>`,
   then export a support bundle from the Ops tab or run:
   `make export-support-bundle EXPORT_SUPPORT_BUNDLE_ARGS="--target-url <target-url> --auth-token <token> --workspace-id <workspace-id> --session-id <session-id>"`
   The session-quality response and support bundle include an
   `operator_summary` headline/details block for quick incident handoff.
11. Verify health: `GET /api/health`.
12. For the canonical local UI, start `aidm_frontend` with `VITE_AIDM_API_BASE_URL` pointed at the backend.

## Optional TTS
1. Set `AIDM_DEEPGRAM_API_KEY`.
2. Optionally set `AIDM_DEEPGRAM_TTS_MODEL` (default: `aura-2-draco-en`).
3. Tune `AIDM_DEEPGRAM_TTS_CONNECT_TIMEOUT_SECONDS` and `AIDM_DEEPGRAM_TTS_READ_TIMEOUT_SECONDS` only when provider/network timing needs local adjustment.
4. Confirm `GET /api/tts/config` returns `configured: true` and reports the expected model plus connect/read timeouts.
5. Toggle TTS in the React frontend. DM responses should be queued for speech; playback or provider failures should surface as visible frontend errors.
6. For direct checks, prefer `POST /api/tts/stream`; `/api/tts/speak` remains a compatible alias. Inspect `X-AIDM-TTS-Chunk-Count` and `X-AIDM-TTS-First-Chunk-Chars` on long responses.

## Operational Checks
1. Confirm `/api/health` returns `status: ok`.
2. Confirm `/api/metrics` exposes counters/timings.
3. Confirm session creation and state retrieval (`/api/sessions/<id>/state`).
4. Confirm socket `send_message` emits `dm_response_start`, `dm_chunk`, `dm_response_end`.
5. Confirm `turn_id` appears in logs (`/api/sessions/<id>/log`).
6. Confirm improvised entities/threads are being written to `story_entities` / `story_threads` for active sessions.
7. Render local-only SLO evidence with `make local-beta-slo-baseline`, then
   render hosted target SLO evidence with `make beta-slo-baseline
   BETA_SLO_BASELINE_ARGS="--target-url <target-url> --auth-token <token>
   --workspace-id <workspace-id> --release RC1 --environment staging"` before
   inviting more testers.
8. Share `docs/beta_tester_onboarding.md` with invited testers after target
   deployment readiness passes.

## Turn Lifecycle
1. The socket receives `send_message` and records the player action in `dm_turns` plus the `turn_events` event spine.
2. Narration streams through `dm_response_start`, one or more `dm_chunk` events, and `dm_response_end`.
3. After visible narration finishes, post-turn work persists `dm_output`, records the `dm_response` event, extracts/validates canon, applies canon tables, refreshes `SessionState`, and emits `session_log_update`.
4. Watch `turn_status` events for `received`, `narrating`, `response_complete`, `saving`, `saved`, `canon_pending`, `canon_applied`, and `failed`. A canon failure should not erase a saved visible DM response.
5. Treat `turn_events` as the turn transcript audit trail. `dm_turns`, `session_log_entries`, `PlayerAction`, and `SessionState` are projections or convenience tables that should agree with the event spine. Use `/api/beta/audits` as a workspace admin when investigating manual/operator changes; it includes recent session-state mutation diffs and bestiary/operator authoring actions.
6. If a future change rewrites projection logic, verify both the event rows and the projected session log/state before assuming the UI is wrong.

The per-session turn coordinator defaults to an in-memory store for local single-process play. For multi-worker deployments, set `AIDM_TURN_COORDINATOR_STORE=database` so workers share `session_turn_locks`; tune `AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS` high enough for the longest expected provider turn, and keep `AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS` low enough that queued players are not left waiting after a lock releases. Multi-worker Socket.IO delivery also needs either `AIDM_SOCKETIO_WORKER_MODEL=sticky` with load balancer affinity or `AIDM_SOCKETIO_WORKER_MODEL=message_queue` plus `AIDM_SOCKETIO_MESSAGE_QUEUE`.

## Provider Switching
1. Changing provider/model mid-session can alter tone, continuity, latency, and rules behavior.
2. Prefer switching between sessions or immediately after a session recap when possible.
3. For beta debugging, record provider/model changes in notes or a system log so later turn quality can be tied back to runtime changes.
4. Persistent provider changes through `/api/llm/config` are local/test only; production-like environments should use environment variables and restart/redeploy.
5. OpenAI-compatible providers reuse HTTP sessions and support phase timeout tuning through `AIDM_DEEPSEEK_CONNECT_TIMEOUT_SECONDS`, `AIDM_DEEPSEEK_READ_TIMEOUT_SECONDS`, `AIDM_NVIDIA_CONNECT_TIMEOUT_SECONDS`, and `AIDM_NVIDIA_READ_TIMEOUT_SECONDS`.
6. Gemini and OpenAI-compatible providers skip cooled-down models after repeated 429/rate-limit responses; tune with `AIDM_LLM_RATE_LIMIT_THRESHOLD` and `AIDM_LLM_RATE_LIMIT_COOLDOWN_SECONDS`.
7. Runtime provider mutation is owned by `aidm_server.blueprints.runtime_config`; the generic system blueprint should stay read-only health/metrics plus operational utilities.

## Incident Playbook
1. `error_code=unauthorized`: verify bearer token, HTTP-only account cookie, or socket connect auth payload; tokens are not accepted in event payloads or query strings.
2. `error_code=rate_limited`: increase limits or reduce client burst rate.
3. `error_code=dm_generation_failed`: switch to fallback provider or verify provider key/model.
4. Segment not triggering: inspect segment `trigger_condition` JSON and session/campaign state.
5. Missing external telemetry: verify `AIDM_TELEMETRY_ENABLED`, endpoint URL, API key, timeout, plus `AIDM_OBSERVABILITY_PROVIDER` and `AIDM_ALERT_OWNER` in production.
6. DM response visible but not saved: inspect `dm_turns.status`, the matching `turn_events` rows, backend logs after `dm_response_end`, and whether canon extraction/projection failed before `session_log_update`.
7. Tester reports a bad turn: use the operator-only inspector Ops tab, `/api/beta/session-quality?session_id=<id>`, or `/api/beta/incidents` as a workspace admin to inspect the report, failed-turn row, provider/model snapshot, latency, related canon-job status, unresolved clarification count, and state/audit counts. Beta feedback prompt submissions store coherence plus fun/rules scores on the turn feedback record. Use the Ops tab bundle export, `make export-support-bundle`, or `/api/beta/support-bundle?session_id=<id>` when attaching support evidence to an RC issue or incident note.
8. TTS icon on but silent: verify `/api/tts/config`, browser autoplay policy, visible frontend TTS errors, and direct `/api/tts/stream` behavior with a short sentence.
9. Frontend connected to wrong backend: restart Vite with `VITE_AIDM_API_BASE_URL=http://127.0.0.1:5050`, then verify the backend URL displayed in the top bar.
10. Created campaign has no players/sessions: create or select a player for the campaign, then start a session; the campaign workspace endpoint should show `player_count` and `session_count`.

## Safe Flags for Closed Beta
- `AIDM_AUTH_REQUIRED=true`
- `AIDM_RULES_ENGINE_ENABLED=true`
- `AIDM_SEGMENT_EVALUATOR_ENABLED=true`
- `AIDM_SOCKETIO_ASYNC_MODE=threading`
- `AIDM_RATE_LIMIT_WINDOW_SECONDS=30`
- `AIDM_RATE_LIMIT_MAX_API_REQUESTS=120`
- `AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES=40`
- `AIDM_RATE_LIMIT_STORE=memory` for local runs, or `database` when multiple workers must share one limit window.
- `AIDM_TURN_COORDINATOR_STORE=memory` for local single-process runs, or `database` for production/multi-worker runs.
- `AIDM_SOCKETIO_WORKER_MODEL=single` for one backend worker, `sticky` when the load balancer owns affinity, or `message_queue` when Socket.IO uses `AIDM_SOCKETIO_MESSAGE_QUEUE`.
- `AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=true` and `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false` for hosted same-origin cookie-only account auth. Unsafe REST requests then use the companion `aidm_csrf_token` cookie with `X-AIDM-CSRF-Token`.
- `AIDM_OBSERVABILITY_PROVIDER=<provider-name>` and `AIDM_ALERT_OWNER=<team-or-person>` for production bootstrap.
- `AIDM_TELEMETRY_ENABLED=true` (if external telemetry endpoint is available)
- `AIDM_SECURITY_HEADERS_ENABLED=true` so Flask-served responses include CSP and standard browser hardening headers.

## Local-Only Boundaries
- `.env.local` writes from `/api/llm/config` are for local runtime switching.
- `AIDM_AUTH_REQUIRED=false`, wildcard CORS, SQLite, Flask admin, in-memory rate limiting, the in-memory turn coordinator, and module-global socket state are local/private deployment conveniences.
- SQLite databases/backups are developer runtime data. Local defaults use `~/.aidm/`; keep real DBs and backups outside `aidm_server/instance/` before packaging or sharing.
- `scripts/backup_restore_drill.py` supports file-backed SQLite. Hosted databases need a provider-specific backup/restore runbook and restore proof before wider beta.
- Structured JSON-like fields intentionally remain JSON text while SQLite is supported; see `docs/json_storage_policy.md` before changing these columns to native JSON.
- Browser QA screenshots and traces should be written under ignored paths such as `tmp/verification_artifacts/` and cleaned with `scripts/cleanup_artifacts.sh`.
- Production bootstrap rejects wildcard CORS and requires auth, declared observability ownership, an explicit Socket.IO worker model, database-backed rate limiting and turn coordination, security headers, and secure cookie settings when cookie auth is enabled.
- Bootstrap tightens `.env.local`, local SQLite data directories such as `~/.aidm` or `instance`, and SQLite DB/backups when present.
