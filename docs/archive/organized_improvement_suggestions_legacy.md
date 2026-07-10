# Organized AIDM Improvement Suggestions

> **Archived historical review:** This organized copy preserves findings from
> an earlier repository state. Its present-tense descriptions, paths, commands,
> and open-item labels are not current guidance. Check `../index.md`,
> `../roadmap.md`, and the current implementation before acting on an item.

This is a sorted copy of `docs/archive/improvements_suggestions_legacy.md`. The original recommendation and finding text is preserved verbatim, with only organization headings and source labels added here.

## Table of Contents
- [0. Source Notes And Verification](#0-source-notes-and-verification)
- [1. Completed Work](#1-completed-work)
- [2. Organized Recommendation Backlog](#2-organized-recommendation-backlog)
  - [Architecture & System Design](#architecture--system-design)
  - [Backend API & Data Model](#backend-api--data-model)
  - [Turn Pipeline, LLM, Canon & Gameplay](#turn-pipeline-llm-canon--gameplay)
  - [Frontend UX & Client Architecture](#frontend-ux--client-architecture)
  - [TTS & Audio](#tts--audio)
  - [Security, Configuration & Operations](#security-configuration--operations)
  - [Performance & Scalability](#performance--scalability)
  - [Maintainability & Repo Cleanup](#maintainability--repo-cleanup)
  - [Testing, QA & Developer Experience](#testing-qa--developer-experience)
  - [Documentation & Product Decisions](#documentation--product-decisions)
- [3. Detailed Six-Pass Senior Review Findings](#3-detailed-six-pass-senior-review-findings)
- [4. Highest-Impact Recommendations](#4-highest-impact-recommendations)
- [Appendix A. Original Source Navigation Block](#appendix-a-original-source-navigation-block)

## 0. Source Notes And Verification

### Original Header Preserved From Source
```markdown
# AIDM Improvement Suggestions

This file captures repo-wide cleanup and product improvement notes from the current frontend/backend pass. Items marked "fixed in this pass" were addressed directly; larger items are intentionally left as suggestions so they can be planned without a risky overhaul.

```

### Deep Audit Note
This pass looked across the backend, frontend, scripts, tests, docs, local artifacts, and runtime shape. I did not make large code changes in this pass; these are backlog candidates and cleanup targets.

### Senior Review Verification

This review was executed in six sequential passes across backend source, frontend source, scripts, migrations, tests, docs, and local runtime artifacts. No source code was changed in this pass; this section was appended only.

Verification performed during the review:
- `.venv/bin/python -m pytest` passed: 94 tests.
- `npm run build` passed.
- `npm run lint` passed.
- `.venv/bin/python scripts/deploy_bootstrap.py --check-only` passed with an auth-disabled warning.
- `.venv/bin/python scripts/smoke_beta_flow.py` passed, but it used the active local runtime config/database and took roughly 44 seconds.
- `npm audit --omit=dev` reported 0 frontend production vulnerabilities.


## 1. Completed Work

### Fixed in this pass
- Frontend: DM response cards now grow with long narrator output instead of hiding text inside a fixed-height card.
- Frontend: Previous turn cards now include working expand/collapse controls so long player, character, and narrator entries can be read.
- Frontend: The composer mode controls now rewrite the input prefix when switching between in-character, roll, ability, item, emote, and OOC modes.
- Frontend: Roll mode now has selectable dice options: d4, d6, d8, d10, d12, d20, and d100.
- Frontend: The session timer now updates from the active session creation time instead of staying static.
- Frontend: Campaign rail session count and "Updated" metadata now derive from live session data instead of a stale display value.
- Frontend: New sessions now receive a welcome log entry and the empty DM panel shows useful starting copy.
- Frontend: The session overflow menu now exposes refresh, rename, and delete actions.
- Frontend: The left rail add campaign button, burger menu, theme toggle, account icon, account dropdown, Turns tab, sync button, and View All Canon control are now wired to real actions.
- Frontend: The main turn feed renders the full loaded history and allows scrolling, instead of only showing the last few turns.
- Frontend: Composer tool spacing was loosened so the bottom action row is not jammed against the screen edge.
- Backend: Added lightweight `PATCH /api/sessions/<id>` support for renaming sessions.
- Backend: Added lightweight `DELETE /api/sessions/<id>` support for deleting a session and owned session rows.
- Backend: Added a welcome system log entry when a session starts.

## 2. Organized Recommendation Backlog

### Architecture & System Design

#### Source: High-priority follow-up improvements
- Generate or share API types between Flask responses and the React client. Right now the TypeScript types must manually match backend JSON, which will keep creating small drift bugs.

#### Source: Backend/API improvements
- Revisit `aidm_server/blueprints/socketio_events.py`; it is one of the larger backend files and likely wants smaller units around join/leave, turn submit, streaming, and error handling.
- Revisit `aidm_server/llm.py`; provider clients and runtime selection are concentrated in one module. Splitting provider adapters would make future model/provider changes safer.

#### Source: Repo cleanup and bloat
- Decide whether `aidm_server/static/codex` is legacy or still supported. The repo now has both the React frontend and an older Flask-served static frontend, which invites fixes landing in the wrong UI.

#### Source: Highest-leverage fixes to prioritize next
- Split `aidm_server/emergent_memory.py`. It is roughly 1,577 lines and combines provider extraction, regex fallback extraction, validation, entity/fact/thread writes, inventory mutation, projection updates, and retrieval scoring.
- Split `aidm_server/llm.py`. It is roughly 1,066 lines and mixes provider adapters, rate-limit state, context assembly, prompt construction, fallback narration, and stream chunking.
- Decide whether the Flask-served Codex UI is still supported. `aidm_server/static/codex` and `aidm_server/templates/codex_frontend.html` are a second frontend that can drift from the React app and attract fixes to the wrong place.

#### Source: Backend/API correctness and data model
- Add a small repository/service layer for session deletion, turn persistence, canon application, and workspace loading. Current blueprints call models directly and own too much transaction detail.
- Move module-global socket state (`active_players`, `socketio_connections`) behind an interface. It is fine for one local process, but it will not work correctly with multiple workers or distributed Socket.IO.

### Backend API & Data Model

#### Source: High-priority follow-up improvements
- Add a real `name` and `updated_at` column to the `Session` model. The quick rename fix stores the display name in `state_snapshot`; that avoids a migration today, but the long-term API should not hide core session metadata inside JSON.

#### Source: Backend/API improvements
- Add first-class endpoints for session archive/restore if hard deletion is not the desired default.
- Add an activity timestamp endpoint or include `updated_at` in session list responses. The frontend currently infers activity from session state and snapshot fields.
- Add campaign rename/delete/update endpoints to match the now-clickable frontend affordances.
- Add world lookup/selection data to campaign creation. The temporary UI asks for a raw world ID because there is no friendly world picker wired into the React app.
- Add a canon endpoint that returns structured campaign/session facts instead of relying only on session memory snippets.
- Add request/response schema validation around session updates and campaign creation. The backend has helper validation, but several endpoints still accept loosely shaped payloads.
- Tighten socket event contracts. `send_message`, `session_log_update`, streaming chunks, and errors should have shared schemas and tests so the frontend can be less defensive.

#### Source: Larger reworks to plan
- Move session display metadata from inferred frontend calculations into backend response DTOs. The frontend should receive `display_name`, `turn_count`, `latest_activity_at`, `latest_summary`, and `is_archived`.
- Create a proper "campaign workspace" endpoint that returns campaign, sessions, players, maps, segments, and summary metadata in one response. The current frontend fans out several requests and then stitches state together.

#### Source: Highest-leverage fixes to prioritize next
- Add a real campaign workspace endpoint. The React app currently loads root data, then performs one sessions request per campaign, then fans out campaign/session/player/map/segment requests. This creates extra latency, request races, and more state stitching than the client should own.
- Move session metadata out of `Session.state_snapshot`. Session names, activity time, archive/delete state, and turn counts should be real columns or response DTO fields, not hidden JSON conventions.

#### Source: Backend/API correctness and data model
- Add `updated_at` to `Campaign`, `Session`, `Player`, `Map`, and `CampaignSegment` where user-facing ordering depends on recency. Right now several "Updated" labels are inferred indirectly.
- Add `Session.name`, `Session.status`, `Session.updated_at`, and possibly `Session.deleted_at`. This would let rename/delete/archive behavior be clean and queryable.
- Prefer soft delete/archive for sessions. Hard delete currently removes session-owned rows and nulls canon references. That can be okay locally, but campaign canon may become hard to audit after deleting historical sessions.
- Add campaign update/delete/archive endpoints. The frontend now has campaign creation, but the API surface is asymmetric.
- Add player update endpoints for stats, inventory, sheet, active character, and profile metadata. Inventory changes happen through canon extraction, but the user has no direct correction path.
- Add first-class canon endpoints. The frontend still derives "Canon Facts" mostly from `SessionState.memory_snippets`; it should be able to query `story_entities`, `story_facts`, `story_threads`, and `turn_canon_updates` directly.
- Add response DTO builders instead of hand-rolled dictionaries in each blueprint. Campaign/session/player/map/segment JSON shapes are repeated and easy to drift.
- Strengthen write validation. Several endpoints accept arbitrary payload values for strings, JSON text, booleans, and integers. Add length limits, type coercion, enum checks, and consistent `Expected JSON request body` handling.
- Consider SQLAlchemy JSON columns for structured fields when not limited by SQLite portability. `stats`, `inventory`, `character_sheet`, `map_data`, `metadata_json`, `rules_hint`, and `state_snapshot` are mostly JSON text with manual parsing.
- Revisit `db.create_all()` in runtime startup. It is convenient locally, but migrations should be the production source of truth. Keep `AIDM_AUTO_CREATE_SCHEMA` local-only and make production fail loudly if migrations are not applied.
- Add DB cascade/delete semantics at the model/migration level instead of relying only on manual delete code. The session delete endpoint has to know too much about canon references.
- Add idempotency keys for `send_message` and session creation. Double-clicks, reconnect retries, and socket retries can otherwise create duplicate turns or sessions.
- Add optimistic concurrency checks for session/turn updates. A stale client should not overwrite a newer session rename or runtime state without detection.
- Treat `world_id` in socket `send_message` as informational or remove it from the required payload. The engine correctly uses `campaign.world_id`, so requiring a client-provided `world_id` adds confusion.

### Turn Pipeline, LLM, Canon & Gameplay

#### Source: Frontend UX improvements
- Make composer tools submit structured intent metadata, not just text prefixes. Prefixes are useful visually, but the backend should know whether a message is OOC, roll, item use, ability check, or emote.
- Add proper roll execution instead of only inserting text like "I roll a d20". The UI should support die type, modifier, advantage/disadvantage, reason, and result visibility.
- Add item and ability selection from the actual character sheet/inventory. The new buttons are wired, but they still produce text templates rather than selecting real game objects.
- Rename visible "Canon" consistently and audit old "Cannon" copy if any remains.

#### Source: Larger reworks to plan
- Build a real game action model. Rolls, abilities, items, emotes, and OOC should become typed actions with backend validation, not plain messages with conventions in the text.

#### Source: Highest-leverage fixes to prioritize next
- Turn the post-turn lifecycle into explicit persisted states. The user sees `dm_response_end` before canon extraction/projection/save finishes. The UI needs a visible saved/pending/error state so "narration displayed" is not mistaken for "turn fully persisted."

#### Source: Backend/API correctness and data model
- Make turn event projection rebuildable. `turn_events` is a good append-only spine, but legacy projections (`PlayerAction`, `SessionLogEntry`) should be regenerable or at least auditable from the event stream.

#### Source: Turn pipeline, LLM, canon, and performance
- Persist the DM response before running canon extraction. If extraction fails, the narration should still be durably saved with `canon_status=failed` or similar.
- Add a `turn_status` event for `received`, `narrating`, `response_complete`, `saving`, `saved`, `canon_pending`, `canon_applied`, and `failed`. The UI can then show accurate state.
- Extract prompt templates into versioned files or constants with tests. DM narration and canon extraction prompts are high-impact runtime behavior and should not be buried in large functions.
- Add provider capability metadata. Streaming support, reasoning/thinking flags, max tokens, timeout, and default temperature should come from one provider registry.
- Remove duplication between `SUPPORTED_LLM_PROVIDERS`, `LLM_PROVIDER_CATALOG`, defaults in `config.py`, and provider defaults in `llm.py`.
- Make DeepSeek/NVIDIA compatibility explicit. DeepSeek is partially modeled as its own provider and partially as NVIDIA-compatible fallback behavior. That should be easy to reason about from one registry.
- Add rate-limit/cooldown behavior to non-Gemini providers if needed. Gemini has cooldown tracking; OpenAI-compatible providers only attempt fallbacks.
- Capture provider/model on stream start as well as on final persistence. If the post-turn save fails, telemetry should still know which provider generated the visible response.
- Sanitize `<thought>`/reasoning output on the backend, not only the frontend TTS path. The UI strips it for speech, but stored logs and exported JSON may still contain reasoning tags if a provider emits them.
- Revisit rule detection and roll parsing as a typed domain service. Right now rolls are detected from natural language and text prefixes; typed roll payloads would be more reliable.
- Add a repair/reprojection command that rebuilds `SessionState` from turns/events/canon. This will help when experimental extraction logic changes.

#### Source: Frontend architecture and UX
- Connect Ability and Item controls to real player data. They currently create text templates instead of selecting from stats, proficiencies, spells, features, or inventory.
- Build a real roll panel. It should support die, modifier, advantage/disadvantage, reason, target pending turn, and result visibility.
- Send typed action metadata to the backend. The composer mode should not only rewrite text prefixes.
- Move derived timeline/canon/inventory selectors into pure tested functions. These are business logic now, not just rendering helpers.

### Frontend UX & Client Architecture

#### Source: High-priority follow-up improvements
- Replace prompt/confirm dialogs for campaign creation, session rename, and session delete with proper in-app modals. Browser prompts work as a quick fix, but they feel bolted on and are hard to style, validate, or test.
- Split `aidm_frontend/src/App.tsx` into focused components. It is about 2,400 lines and mixes data loading, socket lifecycle, layout, derived game state, and UI event handling. This is now the biggest source of regression risk.
- Split `aidm_frontend/src/App.css` into layout, shell, rails, session feed, composer, inspector, and responsive files or CSS modules. It is about 2,100 lines, and old breakpoint rules already masked newer behavior.
- Replace local component state scatter with a small workspace/session state layer. The current app has many interdependent `useState` hooks, which makes session refreshes, optimistic turns, and selected IDs easy to desync.

#### Source: Frontend UX improvements
- Add click-outside and Escape-key handling for the account and session menus.
- Add real profile/settings panels behind the account controls instead of only refresh/reconnect actions.
- Make the top backend/provider/model controls usable on mobile. The mobile CSS currently hides most of the ops bar, so important runtime state disappears on small screens.
- Add an explicit "jump to latest" affordance in the turn feed after the user scrolls upward.
- Add loading skeletons or compact loading states for campaign/session switching so stale content is not mistaken for the new selection.

#### Source: Larger reworks to plan
- Introduce a normalized client data cache. Current reload behavior works, but repeated campaign/session refreshes can cause redundant requests and make optimistic UI fragile.
- Consolidate frontend design tokens and responsive layout rules. The UI has a strong direction, but large CSS files and repeated breakpoint overrides make small changes riskier than they should be.

#### Source: Highest-leverage fixes to prioritize next
- Split `aidm_frontend/src/App.tsx` immediately. It is now roughly 3,269 lines and owns socket lifecycle, TTS streaming, campaign/session loading, modal state, derived game state, layout rendering, and UI helpers. Start with `useWorkspaceData`, `useSessionSocket`, `useTtsNarration`, `CampaignRail`, `SessionBoard`, `InspectorPanel`, and `ActionComposer`.
- Split `aidm_frontend/src/App.css`. It is roughly 2,325 lines with many fixed grid rows, short-height overrides, and mobile overrides. Layout bugs are likely because fixes require chasing the same component through multiple breakpoint blocks.

#### Source: Frontend architecture and UX
- Add a route/settings UI for backend URL and auth token. `baseUrl` is read from `localStorage`, but the React app does not expose a clear way to edit it.
- Replace `window.prompt` and `window.confirm` for session rename/delete with app modals. This was already noted, and it should happen soon because browser dialogs feel jarring in fullscreen.
- Add click-outside and Escape handling for account and session menus. The current menus toggle, but they are not full menu primitives yet.
- Add focus management for dialogs and menus. The create campaign dialog should trap focus, restore focus on close, and submit on Enter predictably.
- Add keyboard shortcuts only after the controls are stable: send, focus composer, stop TTS, toggle fullscreen, refresh, and jump to latest.
- Add a "jump to latest" button when the user scrolls up in the turn feed.
- Auto-scroll policy should be explicit. Streaming should keep the latest text in view unless the user intentionally scrolled away.
- Add structured loading states per panel. A global "Loading" badge is not enough when campaign, session, player detail, map, and canon data load independently.
- Add empty-state actions. "No sessions yet" should include a start-session action; "No players" should offer create/import player; "No map" should offer create/select map.
- Add player creation/editing to the React frontend. The older Flask static UI has player/campaign dialogs; the React app mostly assumes players exist.
- Add map/segment management to the React frontend if those features remain part of the product.
- Add a real profile/settings panel behind the account controls.
- Make top runtime controls usable on mobile. The mobile CSS hides ops segments, which hides backend/provider/model state.
- Use a reducer or client store for workspace state. There are many interdependent `useState` hooks, and selected campaign/session/player can desync during refreshes.
- Handle optimistic DM entries carefully. The client adds a synthetic streamed DM entry, then clears it after `session_log_update`; failures can leave a visible response that is not saved unless state is explicit.
- Add frontend error categories. Connection failures, TTS failures, persistence failures, and validation errors are all pushed into one small `errors` array.
- Add toast/history for errors instead of only the latest rail footer message. Some important errors disappear quickly.
- Add browser persistence for selected campaign/session/player with validation. The URL stores campaign/session, but selected player is not similarly durable.
- Revisit the default "prefer Ember" selection logic. It is useful for local testing, but product behavior should not prioritize one campaign title.
- Make exports include turn events and canon tables, not just current UI state.
- Add import/restore flow if export is meant to be more than a debug snapshot.
- Replace hidden live-data text with visible diagnostics or remove it. Hidden operational strings can be useful for tests, but they should be intentional and documented.

### TTS & Audio

#### Source: TTS-specific improvements
- Measure current TTS latency with timestamps in the client and backend before more tuning. Track text visible time, TTS request start, response headers, first audio byte, first `audio.play`, and first audible playback if possible.
- Do not wait for a whole long narrator response before starting TTS. The current chunking starts from streaming text, but the system should be verified end-to-end with real provider timing and browser support.
- Make TTS chunks sentence-aware but smaller for the first chunk. A short first clause/sentence can get speech started quickly while later chunks are queued.
- Prefetch/request the next TTS chunk while the current one is playing. The backend currently requests Deepgram chunks serially while streaming the response body.
- Add a small TTS queue status UI: off, queued, requesting, speaking, failed. The icon alone does not explain delays.
- Add a "stop speech" affordance separate from disabling TTS. Users may want to interrupt one narration without turning the feature off.
- Cache TTS audio by turn ID and text hash for replay. If the same DM response is expanded or revisited, it should not pay provider latency again.
- Add server-side TTS text normalization shared with the client. Markdown/thought stripping currently lives primarily in the React app.
- Consider a dedicated `/api/tts/stream` or WebSocket audio path if browser `MediaSource` support for `audio/mpeg` proves inconsistent. The fallback path waits for the full blob and can feel slow.

### Security, Configuration & Operations

#### Source: Repo cleanup and bloat
- Move local SQLite backups out of the app source folder or document them as developer-only fixtures. The `aidm_server/instance` directory contains large local database files that should not ship as normal source.

#### Source: Backend/API correctness and data model
- Replace in-memory rate limiting with a pluggable store before multi-process deployment. The current limiter is process-local and resets on restart.
- Do not allow auth tokens in admin query strings long-term. Query tokens get into browser history and logs; keep bearer/header/session-based admin access.
- Add production guardrails around `/api/llm/config`. Writing `.env.local` from an API route is useful locally, but it should be disabled or admin-only outside development.
- Add API auth coverage for all mutating endpoints with `AIDM_AUTH_REQUIRED=true`, including campaign/session/map/segment/player writes and TTS.

#### Source: Security, config, and operations
- Split runtime dependencies from dev/test dependencies. `pytest`, Flask-Migrate, and admin tooling do not necessarily belong in a minimal production install.
- Add log rotation or bounded logs for `tmp/launcher_logs`. Local logs should not grow forever.
- Add startup checks that warn if the backend binds `0.0.0.0` with auth disabled. This is acceptable for trusted local testing but dangerous on shared networks.
- Require explicit CORS allowlists when `AIDM_ENV=production`. The config defaults already tighten production, but bootstrap/docs should keep this loud.
- Make the admin UI opt-in outside local development. It exposes every model and should be deliberately enabled.
- Avoid token-in-query admin access. It is convenient, but URLs leak more easily than headers.
- Make `.env.local` permissions and presence checks part of local bootstrap. It is currently `600` in this checkout, which is good; keep it enforced.
- Add secret scanning to CI/pre-commit. The user has worked with provider keys locally, so accidental key commits are a realistic risk.

#### Source: Repo hygiene and bloat
- Remove or move local SQLite files from `aidm_server/instance` before packaging. The active DB and several backups are local data, not source.
- Keep `.env.local` ignored and never copy it into docs, screenshots, or issue text.
- Decide whether the local DB backups should become explicit fixtures. If not, keep them outside the app package.

### Performance & Scalability

#### Source: Backend/API correctness and data model
- Add pagination/cursors for logs, turns, canon entities, facts, and threads. `/api/sessions/<id>/log` caps at 500 and returns newest entries reversed; this is fine now but not enough for long campaigns.
- Add indexes for common list/detail paths: `sessions(campaign_id, created_at)`, `session_log_entries(session_id, timestamp, id)`, `campaign_segments(campaign_id, is_triggered)`, and canon lookup combinations.

#### Source: Turn pipeline, LLM, canon, and performance
- Move canon extraction into a real background job queue with durable status. Starting a Flask background task is better than blocking the socket, but it is still process-local and not retryable after a crash.
- Add latency metrics by phase: context build, provider time to first token, provider total, DM response emitted, DB save, canon extraction, canon validation, projection refresh, TTS request, TTS first audio byte.
- Put hard budgets around `build_emergent_context`. It currently pulls all campaign entities, facts, and threads into Python and sorts them there. That will degrade as canon grows.
- Move more retrieval filtering into SQL or a small local index. At minimum, cap candidate pools before Python scoring.
- Track prompt/context token estimates server-side. The frontend estimates context from loaded text, but the backend knows what was actually sent.
- Consider connection reuse for HTTP providers. `requests.post` per LLM/TTS call is simple, but `requests.Session` with sane lifecycle could reduce handshake overhead.
- Add provider request timeouts by phase where possible. One global timeout hides "connected but no first byte" versus "slow full completion."

#### Source: Frontend architecture and UX
- Add loaded-history pagination in the main feed. The backend can cap log size, but the UI should make it clear when older turns are not loaded.

### Maintainability & Repo Cleanup

#### Source: Repo cleanup and bloat
- Remove generated folders from the working tree before packaging or committing: `aidm_frontend/node_modules`, `aidm_frontend/dist`, Python `__pycache__` folders, and `.pytest_cache`.
- Keep runtime artifacts out of source snapshots: `tmp/*.png`, `tmp/*.log`, launcher PID files, and local browser verification screenshots.
- Add a root-level cleanup script or Make target for cache/build artifacts.
- Keep the root `.gitignore` current. It exists now, but it should also cover `tmp/`, launcher PID files/logs, `.pytest_cache/`, and accidental SQLite scratch files such as `aidm_server/:memory:`.

#### Source: Frontend build, testing, and accessibility
- Avoid keeping unused starter assets like `src/assets/react.svg`, `src/assets/vite.svg`, and possibly `src/assets/hero.png` if they are not used by the product UI.

#### Source: Repo hygiene and bloat
- Current checkout size is roughly 475M: `.venv` about 303M and `aidm_frontend/node_modules` about 161M. That is normal locally but should never be part of shared archives.
- Remove local runtime artifacts before handoff: `.pytest_cache`, `tmp/*.png`, `tmp/*.mp3`, `tmp/*.headers`, `tmp/launcher_logs/*.pid`, `tmp/launcher_logs/*.log`, and local backend logs.
- Delete accidental SQLite scratch file `aidm_server/:memory:` or add it to cleanup rules. A filename like that usually comes from a misinterpreted SQLite memory URI.
- Add `tmp/`, `.pytest_cache/`, launcher logs, and `aidm_server/:memory:` to root ignore/cleanup rules.
- Consider moving screenshots used for verification into a separate ignored artifact folder with a cleanup command.

### Testing, QA & Developer Experience

#### Source: High-priority follow-up improvements
- Add frontend tests for the exact broken flows the user found: mode switching, long DM response display, turn expansion, new session count, session menu actions, View All Canon, and scroll behavior.

#### Source: Backend/API improvements
- Add API-level tests for bad rename/delete cases: empty name, overlong name, missing session, deleting a session with canon-linked entities, and repeated delete.

#### Source: Build, quality, and release process
- Add CI steps for backend tests, frontend build, frontend lint, and a browser smoke test.
- Add a Playwright smoke test for the local playable route: load campaigns, select a campaign, start/select a session, send a sample action, verify streaming/log update, and inspect the turn feed.
- Add a lightweight accessibility pass for menus, labels, selected states, focus rings, and keyboard navigation.
- Add visual regression screenshots for desktop and mobile around the shell, turn feed, composer, and inspector.
- Add a release checklist item that verifies the React app, not only the Flask static frontend, because both are present in the tree.

#### Source: Backend/API correctness and data model
- Add a schema/data integrity check that compares SQLAlchemy metadata with Alembic head in CI, not just during bootstrap.

#### Source: Turn pipeline, LLM, canon, and performance
- Add prompt/context snapshot tests. When context shape changes, tests should show exactly what the model receives.
- Add deterministic tests for item loss, quantity merging, and invalid inventory phrases. Inventory extraction has careful heuristics but should be protected as it grows.

#### Source: TTS-specific improvements
- Add automated tests for TTS streaming behavior beyond "response bytes equal fake audio." Tests should verify first chunk streaming, multi-chunk ordering, upstream error handling after chunk one, and client fallback behavior.

#### Source: Frontend build, testing, and accessibility
- Add Vitest/unit tests for pure helpers in `App.tsx`: date formatting, composer prefix rewriting, session display metadata, inventory normalization, stat normalization, and TTS flush sizing.
- Add React Testing Library tests for create campaign, session rename/delete, mode switching, TTS toggle, fullscreen fallback, and menu interactions.
- Add Playwright smoke tests for the actual local UI: load campaigns, create campaign, start session, send action, receive streamed DM response, delete session, toggle TTS, and verify no console errors.
- Add visual regression screenshots for desktop, short-height desktop, and mobile. The bottom composer and DM card expansion bugs are exactly the kind visual checks catch.
- Add accessibility checks for icon-only buttons, menu roles, selected states, focus rings, modal focus trap, and color contrast in light mode.
- Add a bundle analyzer budget. The app is small today, but the single component pattern hides growth until it is painful.

#### Source: Security, config, and operations
- Pin Python dependencies or add a lock/constraints file. `requirements.txt` is fully unpinned, which makes test and runtime behavior drift over time.
- Add a `Makefile` or `justfile` for common commands: install, backend, frontend, test, lint, build, smoke, clean, db-upgrade.
- Add a root health script that checks backend health, frontend availability, TTS config, provider config, and the active database path.
- Update release checklist to include the React frontend, not only backend/bootstrap checks.

### Documentation & Product Decisions

#### Source: High-priority follow-up improvements
- Define product-safe delete semantics for sessions. The current delete endpoint removes session-owned rows and nulls canon references where needed. Before heavy use, decide whether deletion should archive, soft-delete, hard-delete, or preserve canon-linked turns.

#### Source: Security, config, and operations
- Update README/runbook for DeepSeek and TTS. README still leans Kimi/Gemini and does not describe the newer TTS/frontend runtime path well.
- Add a production deployment section that states what is local-only: `.env.local` writes, wildcard CORS, auth disabled, SQLite, Flask admin, in-memory rate limiting, module-global socket state.

#### Source: Documentation and product decisions
- Document which frontend is canonical. Right now docs and routes mention both the hosted/client flow and the local React app.
- Document the turn lifecycle after the async post-turn change: streamed narration, background persistence, canon extraction, projection refresh, session log update.
- Document delete semantics before users rely on it. "Delete session" can mean hide, archive, hard delete, or remove only local transcript.
- Document TTS setup with environment variables, expected latency, failure modes, and how the frontend toggle behaves.
- Document provider switching risks. Changing runtime model mid-session affects continuity and should probably be recorded as a system event.
- Add troubleshooting entries for "DM response visible but not saved", "TTS icon on but silent", "frontend connected to wrong backend port", and "created campaign has no players/sessions."
- Add architecture notes for the event spine and projections so future agents know whether to read `turn_events`, `dm_turns`, or `session_log_entries` as source of truth.

## 3. Detailed Six-Pass Senior Review Findings

### Pass 1 - Architecture & System Design

#### High Priority

[Priority: High] Transport Layer Owns Core Turn Rules
	•	File/Module: `aidm_server/turn_engine.py`, `aidm_server/blueprints/socketio_events.py`
	•	Category: Architecture
	•	Current Implementation: `TurnEngine` is constructed inside the Socket.IO handler with socket-local callbacks for pending-turn lookup, DC extraction, roll prompt construction, and response inspection:
```python
engine = TurnEngine(
    socketio=socketio,
    emit_fn=emit,
    stream_fn=query_dm_function_stream,
    latest_pending_turn_fn=_latest_pending_turn,
    dc_hint_from_turn_fn=_dc_hint_from_turn,
    apply_pending_resolution_hint_fn=_apply_pending_resolution_hint,
    build_roll_prompt_fn=_build_roll_prompt,
    response_mentions_roll_request_fn=_response_mentions_roll_request,
)
```
	•	Issue & Why It Matters: Core gameplay rules and pending-roll state are defined in the socket adapter instead of the domain layer. That makes the turn engine hard to reuse from REST, tests, CLI smoke flows, or future job workers without importing transport-specific functions.
	•	Recommended Fix: Move pending-turn resolution, roll prompt generation, and roll-response detection into a domain module such as `aidm_server/turn_rules.py`. Have `TurnEngine` depend on that domain service directly and return typed turn events; keep Socket.IO responsible only for validating socket identity and emitting returned events.
	•	Difficulty: Moderate
	•	Requires Further Investigation: No

[Priority: High] Turn Event Spine Is Not The Only History Write Path
	•	File/Module: `aidm_server/turn_events.py`, `aidm_server/turn_engine.py`, `aidm_server/blueprints/sessions.py`
	•	Category: Architecture
	•	Current Implementation: Most player and DM messages go through `record_turn_event(...)`, but other session history writes bypass the event spine:
```python
db.session.add(SessionLogEntry(
    session_id=new_session.session_id,
    entry_type='system',
    message='**Welcome to the table. Choose your opening move when you are ready.**',
))
```
	•	Issue & Why It Matters: `turn_events`, `dm_turns`, and `session_log_entries` are all plausible sources of truth. Bypassing the event spine for welcome messages, recaps, and some projection updates means future replay/reprojection logic cannot rebuild the full user-visible transcript from one canonical stream.
	•	Recommended Fix: Add event types such as `SESSION_STARTED_EVENT`, `SESSION_ENDED_EVENT`, and `SESSION_RECAP_EVENT`, and route all transcript-affecting writes through `record_turn_event(...)`. Keep direct `SessionLogEntry` writes only inside projection code.
	•	Difficulty: Moderate
	•	Requires Further Investigation: No

#### Medium Priority

[Priority: Medium] Per-Session Turn Locks Are Process-Local And Never Evicted
	•	File/Module: `aidm_server/turn_coordinator.py`
	•	Category: Architecture
	•	Current Implementation: `SessionTurnCoordinator` keeps a module-global dictionary of locks keyed by session ID:
```python
self._locks: dict[int, Lock] = {}
return self._locks.setdefault(session_id, Lock())
```
	•	Issue & Why It Matters: The lock map grows for every session ID ever seen in the process and does not coordinate across multiple workers. This is fine for local single-process use, but fragile for long-lived or multi-worker deployments where ordering guarantees would be inconsistent.
	•	Recommended Fix: Add lock lifecycle cleanup after session deletion or after idle time, and document that this coordinator is single-process only. For multi-worker deployment, move turn serialization to a durable queue or database advisory-lock abstraction.
	•	Difficulty: Moderate
	•	Requires Further Investigation: Yes
	•	If yes, explain what needs to be checked or confirmed: Confirm expected deployment topology and whether Flask-SocketIO will ever run with more than one worker/process.

[Priority: Medium] Runtime Config Mutation Is Mixed Into The System API Surface
	•	File/Module: `aidm_server/blueprints/system.py`, `aidm_server/config.py`, `aidm_server/llm.py`
	•	Category: Architecture
	•	Current Implementation: `PATCH /api/llm/config` mutates `os.environ`, `current_app.config`, and `.env.local` from inside a blueprint:
```python
for key, value in updates.items():
    os.environ[key] = value
current_app.config['AIDM_LLM_PROVIDER'] = provider
if persist:
    _persist_env_updates(updates)
```
	•	Issue & Why It Matters: The API route owns config persistence, in-process runtime switching, provider catalog policy, and local-file writes. That makes it hard to apply different local, test, and production policies without adding more route-level conditionals.
	•	Recommended Fix: Extract a `RuntimeConfigService` with explicit methods for `validate_provider_model`, `apply_in_process`, and `persist_local_env`. Gate persistence at the service boundary based on environment and auth policy.
	•	Difficulty: Moderate
	•	Requires Further Investigation: No

### Pass 2 - Bugs, Robustness & Error Handling

#### High Priority

[Priority: High] Smoke Flow Does Not Force Deterministic Fallback Or An Isolated Database
	•	File/Module: `scripts/smoke_beta_flow.py`
	•	Category: Bug / Developer Experience
	•	Current Implementation: The script loads `.env.local` before calling `os.environ.setdefault('AIDM_LLM_PROVIDER', 'fallback')`, then builds the app against the default database URI:
```python
load_runtime_env(REPO_ROOT)
...
os.environ.setdefault('AIDM_ENV', 'test')
os.environ.setdefault('AIDM_LLM_PROVIDER', 'fallback')
app = create_app()
```
	•	Issue & Why It Matters: On this machine, the smoke flow used the active local provider configuration and active SQLite database. It passed, but took roughly 44 seconds and created smoke data in the local runtime database. A preflight smoke script should be deterministic, fast, and isolated.
	•	Recommended Fix: Set `AIDM_ENV=test`, `AIDM_LLM_PROVIDER=fallback`, and `AIDM_DATABASE_URI=sqlite:///:memory:` before loading runtime env, or add a `--use-local-env` flag for the current behavior. Prefer explicit overrides over `setdefault`.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

[Priority: High] TTS Multi-Chunk Streaming Can Return Partial Audio As Success
	•	File/Module: `aidm_server/blueprints/system.py`
	•	Category: Bug
	•	Current Implementation: `_stream_tts_chunks(...)` streams the first Deepgram response, then requests remaining chunks serially. If a later chunk returns non-OK, it logs and breaks while the HTTP response remains a successful audio stream:
```python
upstream = _deepgram_tts_request(api_key, model, next_chunk, stream=True)
if not upstream.ok:
    current_app.logger.warning(...)
    break
```
	•	Issue & Why It Matters: The client can receive truncated narration with HTTP 200 and no structured failure signal. If a later request raises `requests.RequestException`, the generator can terminate abruptly after headers were already sent.
	•	Recommended Fix: Preflight all chunks before starting the response, or expose TTS as chunk-level events/status over WebSocket or SSE. At minimum, catch `RequestException` inside `_stream_tts_chunks`, emit a trailer/status mechanism if supported, and include a client-visible "partial TTS failed" state.
	•	Difficulty: Moderate
	•	Requires Further Investigation: Yes
	•	If yes, explain what needs to be checked or confirmed: Confirm browser/client behavior when a streaming audio response terminates after partial MP3 bytes.

#### Medium Priority

[Priority: Medium] String Boolean Inputs Are Coerced Incorrectly
	•	File/Module: `aidm_server/blueprints/segments.py`, `aidm_server/blueprints/system.py`
	•	Category: Bug
	•	Current Implementation: Route handlers use Python truthiness for request booleans:
```python
is_triggered=bool(payload.get('is_triggered', False))
...
persist = bool(payload.get('persist', True))
```
	•	Issue & Why It Matters: JSON values like `"false"` or `"0"` become `True`. A client, form, or test helper that sends string booleans can accidentally trigger a segment or persist a runtime config change.
	•	Recommended Fix: Add a request-level `coerce_bool(value, default)` helper that accepts real booleans and known string values (`true/false`, `1/0`, `yes/no`) and rejects ambiguous values with a validation error.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

[Priority: Medium] Missing Parent Campaigns Can Look Like Empty Collections
	•	File/Module: `aidm_server/blueprints/sessions.py`, `aidm_server/blueprints/players.py`, `aidm_server/blueprints/maps.py`, `aidm_server/blueprints/segments.py`
	•	Category: Bug
	•	Current Implementation: Several collection endpoints filter by campaign ID without confirming the campaign exists:
```python
sessions = Session.query.filter_by(campaign_id=campaign_id).order_by(Session.created_at.desc()).all()
players = Player.query.filter_by(campaign_id=campaign_id).all()
```
	•	Issue & Why It Matters: A stale or mistyped campaign ID returns `[]`, which is indistinguishable from a valid campaign with no sessions, players, maps, or segments. The frontend can show misleading empty states instead of a clear "campaign not found" error.
	•	Recommended Fix: For nested campaign routes, load the parent `Campaign` first and return `404 campaign_not_found` when absent. For query-filtered endpoints, validate `campaign_id` when supplied.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

[Priority: Medium] JSON API Clients Treat Non-JSON Error Responses As Unhandled Parse Failures
	•	File/Module: `aidm_frontend/src/api.ts`, `aidm_server/static/codex/app.js`
	•	Category: Bug
	•	Current Implementation: Both frontend API clients parse every non-empty response body as JSON before checking `response.ok`:
```ts
const text = await response.text()
const payload = text ? (JSON.parse(text) as unknown) : null
```
	•	Issue & Why It Matters: Reverse proxies, Flask debugger pages, upstream HTML errors, and malformed responses produce a `SyntaxError` instead of an `ApiClientError` with status and fallback message. That makes failures harder to debug and can bypass existing error UI paths.
	•	Recommended Fix: Wrap JSON parsing in `try/catch`, inspect `Content-Type`, and preserve raw text in the error payload when parsing fails. Return a consistent client error object for non-JSON server responses.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

#### Low Priority

[Priority: Low] Corrupt Map JSON Can Break List And Detail Endpoints
	•	File/Module: `aidm_server/blueprints/maps.py`
	•	Category: Bug
	•	Current Implementation: Map response builders call `json.loads(...)` directly:
```python
'map_data': json.loads(m.map_data) if m.map_data else {},
```
	•	Issue & Why It Matters: Normal API writes use `json.dumps`, but manual DB edits, migrations, or old rows with invalid `map_data` will raise and turn list/detail requests into 500s.
	•	Recommended Fix: Use `safe_json_loads(m.map_data, {})` for map response serialization and log/repair invalid map rows separately.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

[Priority: Low] Browser TTS Playback Errors Are Swallowed
	•	File/Module: `aidm_frontend/src/App.tsx`
	•	Category: Bug
	•	Current Implementation: `processTtsQueue` catches all playback errors and assumes fetch-layer code already reported them:
```ts
} catch {
  // Errors are handled inside the fetch promise
}
```
	•	Issue & Why It Matters: `audio.play()` rejections, unsupported object URLs, and decode errors happen after the fetch promise and can fail silently. Users may see TTS enabled with no audio and no useful error.
	•	Recommended Fix: Catch playback errors separately, report a concise TTS playback error through the existing error queue, and clear `ttsSpeaking`/URL state deterministically.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

### Pass 3 - Security, Data Safety & Configuration

#### High Priority

[Priority: High] Local SQLite Databases And Backups Are World-Readable
	•	File/Module: `aidm_server/instance/dnd_ai_dm.db`, `aidm_server/instance/dnd_ai_dm.backup-*.db`
	•	Category: Security / Data Safety
	•	Current Implementation: The active DB and backups are stored with `-rw-r--r--` permissions, while `.env.local` is correctly `-rw-------`.
	•	Issue & Why It Matters: Campaign transcripts, player state, canon memory, and potentially user-authored private content are readable by any local account on the machine. This is a data-safety mismatch with the stricter secret-file handling.
	•	Recommended Fix: Ensure `aidm_server/instance` is `0700` and SQLite files/backups are `0600` during bootstrap/startup. Add a bootstrap warning or repair step when database permissions are too broad.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

[Priority: High] Socket Tokens Are Accepted In URLs/Event Payloads And Stored In Memory
	•	File/Module: `aidm_server/auth.py`, `aidm_server/blueprints/socketio_events.py`, `README.md`
	•	Category: Security
	•	Current Implementation: Socket auth accepts `token` from auth payload, event payload, bearer header, or query string. The connect handler stores the token in `socketio_connections`:
```python
query_token = request.args.get("token") if request.args else None
...
'token': token,
```
	•	Issue & Why It Matters: Query tokens can leak through URLs, browser history, reverse-proxy logs, and referrers. Event-payload tokens are visible in client-side socket traffic/debug tooling. Storing plaintext tokens in module-global state increases exposure without a clear need after authorization.
	•	Recommended Fix: Prefer Socket.IO auth payload or bearer headers only, remove query-token support, stop sending tokens on each event, and store only `authorized=True` plus non-secret identity/session metadata.
	•	Difficulty: Moderate
	•	Requires Further Investigation: Yes
	•	If yes, explain what needs to be checked or confirmed: Confirm whether any hosted or legacy clients currently rely on query-string or per-event socket tokens.

#### Medium Priority

[Priority: Medium] Alembic Migration URL Rendering Can Expose Database Passwords
	•	File/Module: `migrations/env.py`
	•	Category: Security / Configuration
	•	Current Implementation: Alembic sets `sqlalchemy.url` using `hide_password=False`:
```python
return get_engine().url.render_as_string(hide_password=False).replace('%', '%%')
```
	•	Issue & Why It Matters: SQLite has no password, but a future Postgres/MySQL deployment would place credentials in the Alembic config value and potentially logs or generated output. This is an avoidable secret exposure risk.
	•	Recommended Fix: Use `hide_password=True` for logging/display, and pass the live connection directly in online migrations. If offline migrations need a full URL, document that they must be run with a non-secret local URL or masked logging.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

[Priority: Medium] Frontend Stores Auth Token In `localStorage`
	•	File/Module: `aidm_frontend/src/App.tsx`, `aidm_frontend/src/api.ts`
	•	Category: Security
	•	Current Implementation: The React app initializes `authToken` from local storage and uses it for REST and socket auth:
```ts
const [authToken] = useState(() => localStorage.getItem('aidm:authToken') ?? '')
headers.set('Authorization', `Bearer ${token.trim()}`)
```
	•	Issue & Why It Matters: Any XSS or malicious browser extension can read a durable bearer token from `localStorage`. The risk is limited in local development, but it matters if this frontend is reused for hosted beta access.
	•	Recommended Fix: For hosted or auth-required deployments, store short-lived tokens in memory or `sessionStorage`, prefer httpOnly cookies where feasible, and add a visible token-clear/logout path. Avoid sending the token in both socket auth and per-event payloads.
	•	Difficulty: Moderate
	•	Requires Further Investigation: Yes
	•	If yes, explain what needs to be checked or confirmed: Confirm whether the React app is intended for hosted authenticated beta use or local-only play.

### Pass 4 - Performance & Efficiency

#### High Priority

[Priority: High] Session Recap Sends The Entire Transcript To The LLM
	•	File/Module: `aidm_server/blueprints/sessions.py`, `aidm_server/models.py`
	•	Category: Performance
	•	Current Implementation: Ending a session loads every log row and includes the full text in one recap prompt:
```python
full_log = get_full_session_log(session_id)
recap_prompt = (... f'{full_log}')
recap = query_gpt(prompt=recap_prompt, system_message='You are a D&D session summarizer.')
```
	•	Issue & Why It Matters: Long sessions will create very large prompts, increasing latency/cost and risking provider context limits. It also makes session end sensitive to old log volume rather than current session complexity.
	•	Recommended Fix: Build recap from bounded recent turns plus `SessionState.rolling_summary`, or maintain incremental summaries throughout play. Add a hard character/token budget before calling the provider.
	•	Difficulty: Moderate
	•	Requires Further Investigation: No

[Priority: High] Emergent Context Ranking Can Trigger N+1 Relationship Loads
	•	File/Module: `aidm_server/emergent_memory.py`
	•	Category: Performance
	•	Current Implementation: `build_emergent_context(...)` loads all accepted facts, then scoring/payload code accesses related entities:
```python
all_facts = StoryFact.query.filter(...).all()
...
subject_name = fact.subject_entity.name if fact.subject_entity else None
object_name = fact.object_entity.name if fact.object_entity else None
```
	•	Issue & Why It Matters: Each `subject_entity` or `object_entity` access can issue a lazy query. As canon grows, every DM turn can pay many extra database round trips during context building.
	•	Recommended Fix: Use `selectinload`/`joinedload` for `StoryFact.subject_entity` and `StoryFact.object_entity`, and add a regression test that counts queries for a seeded canon set.
	•	Difficulty: Moderate
	•	Requires Further Investigation: No

#### Medium Priority

[Priority: Medium] Canon Validation Repeats Predicate Queries Per Incoming Fact
	•	File/Module: `aidm_server/emergent_memory.py`
	•	Category: Performance
	•	Current Implementation: For every incoming fact, validation queries accepted facts with the same predicate:
```python
accepted_facts = (
    StoryFact.query.filter(
        StoryFact.campaign_id == campaign.campaign_id,
        StoryFact.predicate == predicate,
        StoryFact.fact_status == 'accepted',
    )
    .order_by(StoryFact.fact_id.desc())
    .all()
)
```
	•	Issue & Why It Matters: A model patch with several facts sharing predicates repeats identical queries. This is wasted work on every post-turn canon extraction.
	•	Recommended Fix: Group incoming facts by predicate, prefetch accepted facts once per unique predicate, and reuse the grouped rows during validation.
	•	Difficulty: Moderate
	•	Requires Further Investigation: No

[Priority: Medium] Thread Upserts Query One Title At A Time
	•	File/Module: `aidm_server/emergent_memory.py`
	•	Category: Performance
	•	Current Implementation: `apply_canon_patch(...)` performs a case-insensitive thread lookup inside the loop for every thread payload:
```python
thread = (
    StoryThread.query.filter(
        StoryThread.campaign_id == campaign.campaign_id,
        func.lower(StoryThread.title) == title.lower(),
    )
    .order_by(StoryThread.thread_id.asc())
    .first()
)
```
	•	Issue & Why It Matters: Patches with multiple thread updates produce one query per thread. This is manageable now, but unnecessary and likely to slow post-turn processing as authored and emergent threads grow.
	•	Recommended Fix: Normalize all incoming titles first, prefetch matching campaign threads with one query, and upsert from an in-memory map keyed by normalized title.
	•	Difficulty: Moderate
	•	Requires Further Investigation: No

[Priority: Medium] Session Delete Materializes All Turn IDs Before Bulk Updates
	•	File/Module: `aidm_server/blueprints/sessions.py`
	•	Category: Performance
	•	Current Implementation: Delete builds a Python list of all turn IDs, then uses it for several bulk operations:
```python
turn_ids = [turn_id for (turn_id,) in db.session.query(DmTurn.turn_id).filter_by(session_id=session_id).all()]
if turn_ids:
    TurnCanonUpdate.query.filter(TurnCanonUpdate.turn_id.in_(turn_ids)).delete(...)
```
	•	Issue & Why It Matters: Very long sessions can allocate large ID lists and produce large `IN (...)` clauses. Delete is not hot-path, but it can become slow or fail for sessions with many turns.
	•	Recommended Fix: Use a subquery for turn IDs, or delete/update in bounded batches. Add a regression test with a larger synthetic session once delete semantics are finalized.
	•	Difficulty: Moderate
	•	Requires Further Investigation: No

[Priority: Medium] Root Refresh Fans Out Unbounded Session Metadata Requests
	•	File/Module: `aidm_frontend/src/App.tsx`
	•	Category: Performance
	•	Current Implementation: `refreshRoot` loads campaigns, then starts one sessions request per campaign:
```ts
void Promise.all(
  campaignData.map(async (item) => {
    const sessionData = await apiFetch<SessionSummary[]>(
      baseUrl,
      `/api/sessions/campaigns/${item.campaign_id}/sessions`,
      auth,
    )
```
	•	Issue & Why It Matters: Campaign count directly multiplies backend requests on initial load and refresh. This can create avoidable latency and request bursts even when the UI only needs count/latest metadata.
	•	Recommended Fix: Add a compact backend endpoint such as `GET /api/campaigns/summary` returning campaign rows with session count/latest activity, or limit concurrency and lazy-load session metadata as campaigns scroll into view.
	•	Difficulty: Moderate
	•	Requires Further Investigation: No

### Pass 5 - Maintainability, Readability & Cleanup

#### Medium Priority

[Priority: Medium] Unused Contract Classes Create False Architecture Signals
	•	File/Module: `aidm_server/contracts.py`, `README.md`
	•	Category: Maintainability
	•	Current Implementation: `ProviderRequest`, `ProviderResponse`, and `SegmentTriggerSpec` are used, but `DmTurnContract` and `SessionStateContract` are not referenced by runtime code or tests. The README still describes `contracts.py` as "Internal typed contracts."
	•	Issue & Why It Matters: Unused contract classes make the codebase look more typed/contract-driven than it is. Future contributors may update these dataclasses expecting runtime behavior to change, but the ORM/API paths will ignore them.
	•	Recommended Fix: Delete unused contract dataclasses, or wire them into real DTO validation/serialization. If they are intended as design targets, mark them explicitly as TODO/design-only and keep them out of runtime contract docs.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

[Priority: Medium] TypeScript Strictness Is Not Enabled
	•	File/Module: `aidm_frontend/tsconfig.app.json`, `aidm_frontend/src/types.ts`, `aidm_frontend/src/App.tsx`
	•	Category: Maintainability
	•	Current Implementation: `tsconfig.app.json` enables unused checks but not `strict`, while many API fields are `unknown`, `JsonRecord`, or optional:
```json
"noUnusedLocals": true,
"noUnusedParameters": true
```
	•	Issue & Why It Matters: The project gets a build, but it does not get strict null checking or stronger API-shape guarantees. This increases drift risk between Flask response dictionaries and React assumptions.
	•	Recommended Fix: Enable `strict: true` in a staged branch, then tighten the API types most used by workspace/session/player state. If full strictness is too large, start with `strictNullChecks` and `noUncheckedIndexedAccess`.
	•	Difficulty: Moderate
	•	Requires Further Investigation: No

#### Low Priority

[Priority: Low] `utc_now` Is Duplicated Across Runtime Modules
	•	File/Module: `aidm_server/models.py`, `aidm_server/llm.py`, `aidm_server/turn_engine.py`, `aidm_server/emergent_memory.py`, `aidm_server/blueprints/sessions.py`
	•	Category: Maintainability
	•	Current Implementation: Multiple modules define their own `utc_now()` wrapper around `datetime.now(timezone.utc)`.
	•	Issue & Why It Matters: The helper is simple today, but duplicated time sources make future changes harder if the app needs consistent timestamp truncation, test-time freezing, or timezone serialization policy.
	•	Recommended Fix: Keep one helper in a small module such as `aidm_server/time_utils.py` and import it everywhere, or use the model helper consistently outside modules that cannot import models.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

[Priority: Low] JSON Text Helpers Are Not Used Consistently For JSON Text Columns
	•	File/Module: `aidm_server/models.py`, `aidm_server/blueprints/maps.py`, `aidm_server/blueprints/sessions.py`, `aidm_server/emergent_memory.py`, `aidm_server/turn_events.py`
	•	Category: Maintainability
	•	Current Implementation: The codebase has `safe_json_loads`/`safe_json_dumps`, but some routes still use raw `json.loads`/`json.dumps` directly for structured text columns.
	•	Issue & Why It Matters: Mixed serialization style makes malformed data behavior inconsistent. Some endpoints degrade gracefully, while others can 500 or persist unexpected shapes.
	•	Recommended Fix: Define one JSON text-column policy: safe loads for reads, explicit schema validation for writes, and typed helper functions per column where needed.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

### Pass 6 - Testing, Documentation & Developer Experience

#### High Priority

[Priority: High] Documented Python Commands Do Not Match This macOS Runtime
	•	File/Module: `README.md`, `docs/beta_runbook.md`, `docs/release_checklist.md`
	•	Category: Developer Experience / Documentation
	•	Current Implementation: Docs and checklist commands use `python`:
```bash
python scripts/deploy_bootstrap.py --check-only
python scripts/smoke_beta_flow.py
```
	•	Issue & Why It Matters: In this environment, `python` is not on PATH (`zsh: command not found: python`), while `python3` and `.venv/bin/python` work. New contributors following docs will fail before reaching app-specific setup.
	•	Recommended Fix: Update docs to use `python3` for venv creation and `.venv/bin/python` for repo scripts, matching `scripts/run_local_backend.sh`. Add a note that `python` may not exist on macOS.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

[Priority: High] Smoke Preflight Pollutes The Active Local Database
	•	File/Module: `scripts/smoke_beta_flow.py`, `docs/release_checklist.md`
	•	Category: Testing / Developer Experience
	•	Current Implementation: The release checklist asks users to run `python scripts/smoke_beta_flow.py`, and the script creates worlds, campaigns, players, sessions, turns, and recaps using the currently configured database.
	•	Issue & Why It Matters: A smoke test should be safe to run repeatedly. This one can add "Smoke World"/"Smoke Campaign" records to the real local play database and can call the active LLM provider.
	•	Recommended Fix: Make the default smoke command use an in-memory or temporary SQLite database and fallback provider. Add a separate `--against-local-db` or `--live-provider` option for intentional live checks.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

#### Medium Priority

[Priority: Medium] Frontend README Describes A Backend URL Control That The React App Does Not Expose
	•	File/Module: `aidm_frontend/README.md`, `aidm_frontend/src/App.tsx`
	•	Category: Documentation / Developer Experience
	•	Current Implementation: The README says "You can change [the backend URL] in the left sidebar," but `App.tsx` initializes `baseUrl` from localStorage with no setter or visible input:
```ts
const [baseUrl] = useState(() =>
  normalizeBaseUrl(localStorage.getItem('aidm:baseUrl') ?? DEFAULT_BASE_URL),
)
```
	•	Issue & Why It Matters: Users who land on the wrong backend port have to know the localStorage key or rebuild with `VITE_AIDM_API_BASE_URL`. This contradicts onboarding docs and slows troubleshooting.
	•	Recommended Fix: Either add the documented backend URL control with validation/test coverage, or update the README to explain the current `localStorage`/env-var override path.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

[Priority: Medium] No Frontend Test Script Exists For The Complex React Surface
	•	File/Module: `aidm_frontend/package.json`, `aidm_frontend/src/App.tsx`
	•	Category: Testing
	•	Current Implementation: Frontend scripts include only `dev`, `build`, `lint`, and `preview`; there is no `test` script.
	•	Issue & Why It Matters: The React app contains complex socket, TTS, optimistic rendering, workspace loading, and modal/menu behavior. Build and lint passed, but they do not verify user flows or state transitions.
	•	Recommended Fix: Add Vitest/React Testing Library for pure helpers and key UI flows, then add a Playwright smoke test for campaign/session selection and a single local fallback turn.
	•	Difficulty: Moderate
	•	Requires Further Investigation: No

[Priority: Medium] Release Checklist Omits Frontend Build, Lint, And Audit
	•	File/Module: `docs/release_checklist.md`, `aidm_frontend/package.json`
	•	Category: Developer Experience / Documentation
	•	Current Implementation: The release checklist includes backend bootstrap, pytest, smoke flow, health, and migrations, but not `npm run build`, `npm run lint`, or `npm audit --omit=dev`.
	•	Issue & Why It Matters: The frontend can regress while the documented release preflight still passes. In this review, frontend build/lint/audit passed, but they had to be discovered and run manually.
	•	Recommended Fix: Add a frontend section to the release checklist with `cd aidm_frontend && npm ci`, `npm run lint`, `npm run build`, and `npm audit --omit=dev`.
	•	Difficulty: Easy
	•	Requires Further Investigation: No

## 4. Highest-Impact Recommendations


1.	•	Recommendation: Make smoke tests deterministic and isolated.
	•	Why it matters: Prevents local data pollution, provider spend/latency, and misleading release checks.
	•	Affected area/files: `scripts/smoke_beta_flow.py`, `docs/release_checklist.md`
	•	Suggested first step: Override `AIDM_DATABASE_URI=sqlite:///:memory:` and `AIDM_LLM_PROVIDER=fallback` before loading `.env.local`.
	•	Related finding title: Smoke Flow Does Not Force Deterministic Fallback Or An Isolated Database

2.	•	Recommendation: Move turn rules out of the Socket.IO adapter.
	•	Why it matters: Makes gameplay processing reusable, testable, and less fragile as REST, job workers, or alternate clients are added.
	•	Affected area/files: `aidm_server/turn_engine.py`, `aidm_server/blueprints/socketio_events.py`
	•	Suggested first step: Extract pending-roll lookup/resolution and roll prompt generation into `aidm_server/turn_rules.py`.
	•	Related finding title: Transport Layer Owns Core Turn Rules

3.	•	Recommendation: Treat `turn_events` as the canonical transcript mutation path.
	•	Why it matters: Enables reliable replay, projection rebuilds, and clearer source-of-truth reasoning.
	•	Affected area/files: `aidm_server/turn_events.py`, `aidm_server/blueprints/sessions.py`, `aidm_server/turn_engine.py`
	•	Suggested first step: Add session lifecycle event types and route welcome/recap writes through `record_turn_event(...)`.
	•	Related finding title: Turn Event Spine Is Not The Only History Write Path

4.	•	Recommendation: Fix local data-file permissions.
	•	Why it matters: Protects campaign transcripts and player/canon data at rest on shared local machines.
	•	Affected area/files: `aidm_server/instance/*.db`, bootstrap/startup scripts
	•	Suggested first step: Add a bootstrap permission check that chmods instance dir to `0700` and DB files to `0600`.
	•	Related finding title: Local SQLite Databases And Backups Are World-Readable

5.	•	Recommendation: Add hard budgets and eager loading to context/canon retrieval.
	•	Why it matters: Reduces turn latency growth as canon facts, entities, and threads accumulate.
	•	Affected area/files: `aidm_server/emergent_memory.py`, `aidm_server/llm.py`
	•	Suggested first step: Add `selectinload` for fact entities and query-count tests around `build_emergent_context(...)`.
	•	Related finding title: Emergent Context Ranking Can Trigger N+1 Relationship Loads

6.	•	Recommendation: Make TTS failures observable after streaming starts.
	•	Why it matters: Prevents truncated narration from looking like a successful audio response.
	•	Affected area/files: `aidm_server/blueprints/system.py`, `aidm_frontend/src/App.tsx`
	•	Suggested first step: Catch subsequent chunk failures inside `_stream_tts_chunks(...)` and surface a client-visible partial-failure state.
	•	Related finding title: TTS Multi-Chunk Streaming Can Return Partial Audio As Success

7.	•	Recommendation: Remove socket token leakage paths.
	•	Why it matters: Avoids bearer tokens appearing in URLs, event payloads, debug tooling, and in-memory connection records.
	•	Affected area/files: `aidm_server/auth.py`, `aidm_server/blueprints/socketio_events.py`, `README.md`, `aidm_frontend/src/App.tsx`
	•	Suggested first step: Remove query-token support and stop storing raw tokens in `socketio_connections`.
	•	Related finding title: Socket Tokens Are Accepted In URLs/Event Payloads And Stored In Memory

8.	•	Recommendation: Add frontend tests and include frontend checks in release preflight.
	•	Why it matters: The app's most complex user-facing behavior is currently protected only by build/lint and manual checks.
	•	Affected area/files: `aidm_frontend/package.json`, `aidm_frontend/src/App.tsx`, `docs/release_checklist.md`
	•	Suggested first step: Add Vitest for helper/state functions, then a Playwright smoke test for one fallback turn.
	•	Related finding title: No Frontend Test Script Exists For The Complex React Surface

## Appendix A. Original Source Navigation Block

The source navigation block is preserved verbatim below so no source text is dropped. Its links were written for `docs/improvements_suggestions.md`; use the table of contents at the top of this copy for this organized file.

```markdown
## Navigation

No existing suggestion text below has been edited, rewritten, or truncated. This section is only a navigation layer for the existing notes.

### Start Here

- [Fixed in this pass](#fixed-in-this-pass)
- [High-priority follow-up improvements](#high-priority-follow-up-improvements)
- [Highest-leverage fixes to prioritize next](#highest-leverage-fixes-to-prioritize-next)
- [Highest-Impact Recommendations](#highest-impact-recommendations)
- [Exhaustive Senior Engineering Review - 2026-06-04](#exhaustive-senior-engineering-review---2026-06-04)

### Navigate By Area

- Backend/API/data model:
  - [Backend/API improvements](#backendapi-improvements)
  - [Backend/API correctness and data model](#backendapi-correctness-and-data-model)
  - [Pass 1 - Architecture & System Design](#pass-1---architecture--system-design)
  - [Pass 2 - Bugs, Robustness & Error Handling](#pass-2---bugs-robustness--error-handling)
- Turn pipeline, LLM, canon, and TTS:
  - [Turn pipeline, LLM, canon, and performance](#turn-pipeline-llm-canon-and-performance)
  - [TTS-specific improvements](#tts-specific-improvements)
  - [Pass 4 - Performance & Efficiency](#pass-4---performance--efficiency)
- Frontend product, UX, and architecture:
  - [Frontend UX improvements](#frontend-ux-improvements)
  - [Frontend architecture and UX](#frontend-architecture-and-ux)
  - [Frontend build, testing, and accessibility](#frontend-build-testing-and-accessibility)
- Security, configuration, and operations:
  - [Security, config, and operations](#security-config-and-operations)
  - [Pass 3 - Security, Data Safety & Configuration](#pass-3---security-data-safety--configuration)
- Testing, documentation, release, and developer experience:
  - [Build, quality, and release process](#build-quality-and-release-process)
  - [Documentation and product decisions](#documentation-and-product-decisions)
  - [Pass 6 - Testing, Documentation & Developer Experience](#pass-6---testing-documentation--developer-experience)
- Repo hygiene and cleanup:
  - [Repo cleanup and bloat](#repo-cleanup-and-bloat)
  - [Repo hygiene and bloat](#repo-hygiene-and-bloat)
  - [Pass 5 - Maintainability, Readability & Cleanup](#pass-5---maintainability-readability--cleanup)

### Chronological Review Blocks

- [Initial frontend/backend pass](#fixed-in-this-pass)
- [Deep audit additions - 2026-06-04](#deep-audit-additions---2026-06-04)
- [Exhaustive Senior Engineering Review - 2026-06-04](#exhaustive-senior-engineering-review---2026-06-04)

### Detailed Six-Pass Finding Index

- Pass 1 - Architecture & System Design:
  - [Priority: High] Transport Layer Owns Core Turn Rules
  - [Priority: High] Turn Event Spine Is Not The Only History Write Path
  - [Priority: Medium] Per-Session Turn Locks Are Process-Local And Never Evicted
  - [Priority: Medium] Runtime Config Mutation Is Mixed Into The System API Surface
- Pass 2 - Bugs, Robustness & Error Handling:
  - [Priority: High] Smoke Flow Does Not Force Deterministic Fallback Or An Isolated Database
  - [Priority: High] TTS Multi-Chunk Streaming Can Return Partial Audio As Success
  - [Priority: Medium] String Boolean Inputs Are Coerced Incorrectly
  - [Priority: Medium] Missing Parent Campaigns Can Look Like Empty Collections
  - [Priority: Medium] JSON API Clients Treat Non-JSON Error Responses As Unhandled Parse Failures
  - [Priority: Low] Corrupt Map JSON Can Break List And Detail Endpoints
  - [Priority: Low] Browser TTS Playback Errors Are Swallowed
- Pass 3 - Security, Data Safety & Configuration:
  - [Priority: High] Local SQLite Databases And Backups Are World-Readable
  - [Priority: High] Socket Tokens Are Accepted In URLs/Event Payloads And Stored In Memory
  - [Priority: Medium] Alembic Migration URL Rendering Can Expose Database Passwords
  - [Priority: Medium] Frontend Stores Auth Token In `localStorage`
- Pass 4 - Performance & Efficiency:
  - [Priority: High] Session Recap Sends The Entire Transcript To The LLM
  - [Priority: High] Emergent Context Ranking Can Trigger N+1 Relationship Loads
  - [Priority: Medium] Canon Validation Repeats Predicate Queries Per Incoming Fact
  - [Priority: Medium] Thread Upserts Query One Title At A Time
  - [Priority: Medium] Session Delete Materializes All Turn IDs Before Bulk Updates
  - [Priority: Medium] Root Refresh Fans Out Unbounded Session Metadata Requests
- Pass 5 - Maintainability, Readability & Cleanup:
  - [Priority: Medium] Unused Contract Classes Create False Architecture Signals
  - [Priority: Medium] TypeScript Strictness Is Not Enabled
  - [Priority: Low] `utc_now` Is Duplicated Across Runtime Modules
  - [Priority: Low] JSON Text Helpers Are Not Used Consistently For JSON Text Columns
- Pass 6 - Testing, Documentation & Developer Experience:
  - [Priority: High] Documented Python Commands Do Not Match This macOS Runtime
  - [Priority: High] Smoke Preflight Pollutes The Active Local Database
  - [Priority: Medium] Frontend README Describes A Backend URL Control That The React App Does Not Expose
  - [Priority: Medium] No Frontend Test Script Exists For The Complex React Surface
  - [Priority: Medium] Release Checklist Omits Frontend Build, Lint, And Audit

```
