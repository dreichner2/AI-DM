# AI-DM Beta Runbook

## Startup
1. Set environment variables (`AIDM_ENV`, `AIDM_DATABASE_URI`, `AIDM_AUTH_REQUIRED`, `AIDM_API_AUTH_TOKENS`, and the selected provider configuration). Supported provider choices are `gemini`, `deepseek`, `nvidia`, `kimi`, `codex_cli`, and the deterministic local `fallback`; use the matching key such as `GOOGLE_GENAI_API_KEY`, `AIDM_DEEPSEEK_API_KEY`, or `AIDM_NVIDIA_API_KEY` when the provider requires one.
   Choose the exposure/auth posture from `docs/auth_modes.md` before sharing a
   non-loopback URL.
2. Keep `AIDM_SOCKETIO_ASYNC_MODE=threading`. Hosted production requires
   `AIDM_SOCKETIO_WORKER_MODEL=single`; the other reserved model names remain
   deferred until shared presence/music state is implemented. See
   `docs/socketio_worker_model.md`.
3. Install dependencies: `python3.14 -m venv .venv && .venv/bin/python -m pip install -c requirements.constraints.txt --upgrade pip && .venv/bin/python -m pip install --require-hashes -r requirements-dev.lock.txt` for local development, or use `requirements.runtime.lock.txt` for a minimal runtime without pytest/admin UI tooling. The lockfiles pin and hash every transitive package; runtime dependencies include the migration CLI and PostgreSQL driver.
4. Apply migrations: `make db-upgrade` (or run the bootstrap command below).
   The current repository head is `0031_authored_map_visibility`; do not treat
   backup/restore evidence captured at an earlier head as current release proof.
5. Bootstrap check/start command:
   - Check only: `.venv/bin/python scripts/deploy_bootstrap.py --check-only`
   - Local/private start after checks: `.venv/bin/python scripts/deploy_bootstrap.py`
   - Hosted single-worker start after checks:
     `AIDM_ENV=production AIDM_SOCKETIO_WORKER_MODEL=single AIDM_SOCKETIO_ASYNC_MODE=threading AIDM_GUNICORN_THREADS=100 WEB_CONCURRENCY=1 PORT=5050 scripts/run_production_server.sh`
     The production launcher resolves executables from `.venv` when available
     and always runs migration/bootstrap preflight before starting Gunicorn.
6. For local/private SQLite beta data, run `make backup-restore-drill` before real play sessions or pass `BACKUP_RESTORE_DRILL_ARGS="--database-uri sqlite:////absolute/path/to/dnd_ai_dm.db"` for a specific database. The drill creates a backup and verifies a restored copy without writing to the source DB.
7. For PostgreSQL, use the guarded custom-archive drill with a separately supplied empty target: `make postgres-backup-restore-drill POSTGRES_BACKUP_RESTORE_DRILL_ARGS="--source-uri-file /secure/source-uri --empty-target-uri-file /secure/empty-target-uri"`. The target must be disposable and empty; never point both inputs at the same database. Provider-managed backup/PITR evidence remains a separate deployment requirement.
8. Run `make migration-chain-drill` to prove Alembic can apply the full chain, downgrade to base, and re-apply the full chain against an isolated SQLite database.
9. Run `make socketio-worker-model-decision` to verify the hosted RC1
   worker-model decision, production env template, production server command,
   and docs agree.
10. For a release-candidate rehearsal, run `make closed-beta-rc`. For local iteration without browser/dependency gates, run `make closed-beta-rc-fast`. To save gate evidence for an issue or release note, run the checker directly with `--evidence-report` or a specific path such as `tmp/release/rc-evidence.md`. The manual GitHub Actions `Closed Beta RC` workflow checks out Git LFS objects and uploads the `closed-beta-rc-evidence` artifact with the RC report, issue snippets, release evidence packet, source archive, security/export-import evidence, visual-smoke screenshots/review evidence, and GitHub Actions run URL evidence when available. Archive inspection fails if any Git LFS pointer text remains in place of its object; re-fetch the LFS object and regenerate the archive rather than waiving that failure. Before dispatching the manual workflow, run `make github-actions-rc-plan`; after the signed-off candidate is clean, use `GITHUB_ACTIONS_RC_PLAN_ARGS="--dispatch-closed-beta-rc"` to dispatch from the same helper. The `make rc-handoff-artifacts` target refreshes GitHub Actions evidence with read-only `gh` discovery; after CI or the manual RC workflow changes, rerun `make github-actions-evidence GITHUB_ACTIONS_EVIDENCE_ARGS="--auto-gh --include-gh-details --verify-closed-beta-rc-artifact-contents"` directly or pass the run URLs manually. Use `docs/rc_issue_evidence_template.md` when closing gate issues.
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
11. For operator incident evidence, review the selected-session Session Quality card
   in the Ops tab or request `/api/beta/session-quality?session_id=<session-id>`,
   then export a support bundle from the Ops tab or run:
   `make export-support-bundle EXPORT_SUPPORT_BUNDLE_ARGS="--target-url <target-url> --auth-token <token> --workspace-id <workspace-id> --session-id <session-id>"`
   The session-quality response and support bundle include an
   `operator_summary` headline/details block for quick incident handoff.
12. Verify health: `GET /api/health`.
13. For the canonical local UI, start `aidm_frontend` with `VITE_AIDM_API_BASE_URL` pointed at the backend.

For `AIDM_LLM_PROVIDER=codex_cli`, install a compatible Codex executable and
provide either a dedicated persistent, AIDM-only `AIDM_CODEX_HOME` containing
`auth.json` or `AIDM_CODEX_ACCESS_TOKEN`. The production launcher rejects Codex
startup when neither authentication source is present. The default catalog
selection is `AIDM_LLM_MODEL=gpt-5.6-sol-medium`; that display profile routes to
the `gpt-5.6-sol` runtime model with medium reasoning. AIDM ignores the requested
Codex workdir for gameplay calls and invokes Codex in a disposable empty
workspace with read-only minimal filesystem access, network/search, shell,
apps, plugins, skills, MCP, computer/browser use, and multi-agent features
disabled. Unexpected tool events or malformed structured output fail closed.

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
5. Submit a dice request and confirm one `roll_resolved` event arrives after the
   incoming turn is persisted. Its `client_message_id`, faces, modifier
   breakdown, and total must match the durable roll event; client-supplied
   outcome fields must not affect it.
6. Retry the same `client_message_id` and confirm `turn_duplicate` names the
   existing `turn_id` without another roll or state mutation. Reconnect and
   confirm the room rejoin reloads the current session snapshot.
7. Confirm `turn_id` appears in logs (`/api/sessions/<id>/log`).
8. Confirm improvised entities/threads are being written to `story_entities` / `story_threads` for active sessions.
9. Render local-only SLO evidence with `make local-beta-slo-baseline`, then
   render hosted target SLO evidence with `make beta-slo-baseline
   BETA_SLO_BASELINE_ARGS="--target-url <target-url> --auth-token <token>
   --workspace-id <workspace-id> --release RC1 --commit-sha
   <signed-off-commit-sha> --environment staging --invite-more-testers
   <yes-or-no>"` before inviting more testers. Hosted SLO proof passes only
   with an HTTP(S) target, the current RC commit, RC-relative freshness, a
   positive DM response sample count, at least one positive real provider/model
   row, and `Invite more testers` explicitly set to `yes`. A stale, zero-sample,
   or undecided report remains incomplete; an explicit `no` fails.
10. Share `docs/beta_tester_onboarding.md` with invited testers after target
   deployment readiness passes.

## Turn Lifecycle
1. The socket receives `send_message` under the per-session coordinator. A
   repeated `client_message_id` returns `turn_duplicate` for the existing turn
   instead of re-executing it.
2. For a roll submission, the server derives the roll specification from
   persisted player/pending-roll state, generates the result, and commits the
   canonical incoming `dm_turns` row plus durable roll event together. Only then
   does it broadcast `roll_resolved`; the browser animation is presentation.
3. Narration streams through `dm_response_start`, one or more `dm_chunk` events, and `dm_response_end`; `response_complete` means visible streaming ended, not that persistence or canon work finished.
4. The per-session coordinator remains locked while post-turn processing first persists `dm_output` and the `dm_response` timeline event, then emits `saved` with `stage=dm_response`. It continues through immediate validated state changes and durable canon-job enqueue, emits a second `saved` with `stage=post_turn`, and finally emits `session_log_update` after returning from post-turn persistence.
5. Outside tests, canon extraction/validation/application and projection refresh run through one bounded, wakeable worker over the durable canon queue. Foreground narration receives provider priority, while the canon worker releases database connections during provider waits and revalidates each attempt before apply. Watch `canon_pending`, `canon_applied`, or `failed` independently of the already-saved narration, plus the `aidm_canon_job_queue_depth` and `aidm_canon_job_oldest_queued_age_seconds` Prometheus gauges for sustained starvation. Tests process jobs inline for deterministic assertions. A canon failure should not erase a saved visible DM response.
6. Watch `turn_status` events for `received`, `narrating`, `response_complete`, `saving`, `saved`, `canon_pending`, `canon_applied`, and `failed`. `canon_applied` can also carry immediate state-application details before the background canon extraction completes, so inspect its detail payload and the canon-job status during incident review.
7. If `turn_state_apply_failed` arrives, narration is saved but the post-DM
   mechanics phase failed. Check `mechanics_status` before correcting state:
   `none` means no pre-DM mechanic committed, while `partial` means the reported
   number of authoritative pre-DM changes already committed and must not be
   replayed. Inspect the failed turn's privileged `state_pipeline` metadata and
   audits for exact applied-change evidence, correct only the missing or wrong
   remainder through an operator control, then call
   `POST /api/sessions/<session_id>/recovery/resolve` with the matching
   `turn_id`, an explicit `state_corrected` or
   `no_mechanical_change_required` resolution, and a nonempty operator note.
   Until that succeeds, new player turns fail with
   `session_recovery_required`; joining and read paths remain available. The
   failed turn row enforces that pause even if the redundant
   `turnRecoveryGate` snapshot write failed, and the resolve endpoint can use
   the matching failed row directly. Structured turn advance and canon enqueue are
   blocked for the failed turn, which remains failed after recovery for audit.
   An idempotent retry must repeat the same normalized operator note as well as
   the same turn and resolution; changed note text returns 409.
   The successful resolve broadcasts `session_recovery_resolved`; each client
   should reload session state at the advertised revision before re-enabling
   the composer.
8. Treat `turn_events` as the turn transcript audit trail. `dm_turns`, `session_log_entries`, `PlayerAction`, and `SessionState` are projections or convenience tables that should agree with the event spine. Use `/api/beta/audits` as a workspace admin when investigating manual/operator changes; it includes recent session-state mutation diffs and bestiary/operator authoring actions.
9. If a future change rewrites projection logic, verify both the event rows and the projected session log/state before assuming the UI is wrong.

The per-session turn coordinator defaults to an in-memory store for local single-process play. Hosted production uses the database store and migrations through `0028_session_turn_lock_fencing`, which gives every lease owner a persistent monotonic fencing token and rejects commits after ownership changes. Tune `AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS` high enough for the longest expected provider turn, and keep `AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS` low enough that queued players are not left waiting after a lock releases. Multi-worker Socket.IO remains deferred because presence and music are process-local; future support also requires both load-balancer affinity and a shared Socket.IO message queue.

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
11. Roll or turn looked duplicated after a disconnect: compare
    `client_message_id` across the browser event, `dm_turns`, and
    `turn_duplicate`. Do not submit a new key while persistence is uncertain;
    use the visible safe-retry action, which resends the original payload, then
    verify the reloaded session snapshot.
12. `session_archived`, `session_deleted`, `campaign_archived`, or
    `campaign_deleted`: stop the client retry loop. An authorized operator must
    restore the lifecycle target before room join, turn submission,
    clarification resolution, or turn-control updates can resume.

## Safe Flags for Closed Beta
- `AIDM_AUTH_REQUIRED=true`
- `AIDM_RULES_ENGINE_ENABLED=true`
- `AIDM_SEGMENT_EVALUATOR_ENABLED=true`
- `AIDM_SOCKETIO_ASYNC_MODE=threading`
- `AIDM_RATE_LIMIT_WINDOW_SECONDS=30`
- `AIDM_RATE_LIMIT_MAX_API_REQUESTS=120`
- `AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES=40`
- `AIDM_PREAUTH_RATE_LIMIT_WINDOW_SECONDS=60`
- `AIDM_PREAUTH_RATE_LIMIT_MAX_IP_TARGET_ATTEMPTS=5`
- `AIDM_PREAUTH_RATE_LIMIT_MAX_IP_ATTEMPTS=20`
- `AIDM_PREAUTH_RATE_LIMIT_MAX_TARGET_ATTEMPTS=20`
- `AIDM_RATE_LIMIT_STORE=memory` for local/private runs. Production requires `database` even for the supported single-worker topology.
- `AIDM_TURN_COORDINATOR_STORE=memory` for local single-process runs, or `database` for production/multi-worker runs.
- `AIDM_SOCKETIO_WORKER_MODEL=single`, `WEB_CONCURRENCY=1`, and at least 16 Gunicorn threads; other worker models are rejected in hosted production today.
- `AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=true` and `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false` for hosted same-origin cookie-only account auth. Unsafe REST requests then use the companion `aidm_csrf_token` cookie with `X-AIDM-CSRF-Token`.
- `AIDM_OBSERVABILITY_PROVIDER=<provider-name>` and `AIDM_ALERT_OWNER=<team-or-person>` for production bootstrap.
- `AIDM_TELEMETRY_ENABLED=true` with a working external endpoint so privacy-safe
  pre-auth abuse signals reach the named alert owner.
- Only `auth.preauth_rate_limited` events leave the process. They retain the stable
  `event`, `severity`, nested `payload`, `ts`, and `service` fields, add `env`, and
  require a complete privacy-safe payload containing only action, dimension, and
  bounded reset seconds. Extra fields are stripped; missing or invalid required fields
  reject delivery. Every other event remains local and increments
  `telemetry.external.filtered`, so raw IPs, request/socket/record IDs, previews,
  exception text, and caller-controlled request IDs are never sent externally.
- Delivery retries at most twice after request exceptions, HTTP 408/425/429, or 5xx
  responses using short shutdown-aware backoff. Other 4xx responses are not retried,
  and logs never include response bodies, authorization tokens, or exception text.
- For Better Stack HTTP Logs, use the source's ingest-host root as
  `AIDM_TELEMETRY_ENDPOINT` and its dedicated source token as
  `AIDM_TELEMETRY_API_KEY`. Alert on
  `event="auth.preauth_rate_limited"`, `payload.dimension="target"`, and either
  `payload.action="account-legacy-claim"` or
  `payload.action="workspace-password"`. Route any match to the named alert owner
  and retain the source receipt plus alert-test evidence with the release packet.
- `AIDM_SECURITY_HEADERS_ENABLED=true` so Flask-served responses include CSP and standard browser hardening headers.

## Legacy Recovery And Workspace Target Isolation

First/last-name matching is not account-recovery proof. A passwordless legacy
account can set its password only with its saved high-entropy account token or a
replacement issued by an operator. If the saved token is gone:

1. Verify the requester through an approved out-of-band channel. The repository
   does not provide email/phone identity proof, so do not infer ownership from
   names, character knowledge, or possession of a username.
2. In an authorized shell using the target environment/database, run:

   ```bash
   .venv/bin/python scripts/issue_legacy_recovery_code.py --username <username>
   ```

   Add `--confirm-production` when `AIDM_ENV=production`. The command replaces
   the old account-token hash and prints the new raw code once.
3. Deliver the code privately. Do not paste it into a ticket, chat transcript,
   telemetry payload, command log, or tracked file.
4. The user enters the code in the recovery field with a new password. The
   frontend sends the code only as the bearer credential, never in the JSON
   body or browser storage. Successful setup rotates it to a fresh account
   session token; cookie-only deployments return only the HttpOnly cookie.

Workspace-password joins retain the IP+workspace and IP-wide hard limits. The
cross-IP target bucket is scoped to authenticated account plus canonical
workspace, so one account can exhaust only its own join allowance. A different
account's correct password from a source with available IP buckets is still
verified and can create membership. Users sharing a saturated source IP can
still receive 429 because the IP+workspace and IP-wide protections remain
shared. Rotating both accounts and IPs can spread guessing across more
principals; keep signup exposure controlled and review repeated
`workspace-password` target events before any public expansion.

Continue routing privacy-safe `auth.preauth_rate_limited` events with
`dimension=target` and action `account-legacy-claim` or `workspace-password` to
`AIDM_ALERT_OWNER`. The legacy action now represents recovery traffic without
valid account proof. The workspace action represents an account-scoped
canonical-workspace bucket. These are abuse signals, not evidence that another
user's recovery or join was locked out.

## Local-Only Boundaries
- `.env.local` writes from `/api/llm/config` are for local runtime switching.
- `AIDM_AUTH_REQUIRED=false`, wildcard CORS, SQLite, Flask admin, in-memory rate limiting, the in-memory turn coordinator, and module-global socket state are local/private deployment conveniences.
- SQLite databases/backups are developer runtime data. Local defaults use `~/.aidm/`; keep real DBs and backups outside `aidm_server/instance/` before packaging or sharing.
- `scripts/backup_restore_drill.py` supports file-backed SQLite. `scripts/postgres_backup_restore_drill.py` creates and verifies a guarded PostgreSQL custom archive against a distinct empty target, but hosted databases still need provider-specific backup/PITR evidence and restore proof before wider beta.
- Structured JSON-like fields intentionally remain JSON text while SQLite is supported; see `docs/json_storage_policy.md` before changing these columns to native JSON.
- Browser QA screenshots and traces should be written under ignored paths such as `tmp/verification_artifacts/` and cleaned with `scripts/cleanup_artifacts.sh`.
- Production bootstrap rejects wildcard CORS and requires auth, declared observability ownership, an explicit Socket.IO worker model, database-backed rate limiting and turn coordination, security headers, and secure cookie settings when cookie auth is enabled.
- Bootstrap tightens `.env.local`, local SQLite data directories such as `~/.aidm` or `instance`, and SQLite DB/backups when present.
