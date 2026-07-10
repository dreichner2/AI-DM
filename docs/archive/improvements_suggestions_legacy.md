# AIDM Improvement Suggestions

> **Archived historical review:** This document preserves findings from an
> earlier repository state. Its present-tense descriptions, paths, commands,
> and open-item labels are not current guidance. Check `../index.md`,
> `../roadmap.md`, and the current implementation before acting on an item.

This file captures repo-wide cleanup and product improvement notes from the current frontend/backend pass. Items marked "fixed in this pass" were addressed directly; larger items are intentionally left as suggestions so they can be planned without a risky overhaul.

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

## Fixed in this pass

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

## High-priority follow-up improvements

- Replace prompt/confirm dialogs for campaign creation, session rename, and session delete with proper in-app modals. Browser prompts work as a quick fix, but they feel bolted on and are hard to style, validate, or test.
- Add a real `name` and `updated_at` column to the `Session` model. The quick rename fix stores the display name in `state_snapshot`; that avoids a migration today, but the long-term API should not hide core session metadata inside JSON.
- Define product-safe delete semantics for sessions. The current delete endpoint removes session-owned rows and nulls canon references where needed. Before heavy use, decide whether deletion should archive, soft-delete, hard-delete, or preserve canon-linked turns.
- Add frontend tests for the exact broken flows the user found: mode switching, long DM response display, turn expansion, new session count, session menu actions, View All Canon, and scroll behavior.
- Split `aidm_frontend/src/App.tsx` into focused components. It is about 2,400 lines and mixes data loading, socket lifecycle, layout, derived game state, and UI event handling. This is now the biggest source of regression risk.
- Split `aidm_frontend/src/App.css` into layout, shell, rails, session feed, composer, inspector, and responsive files or CSS modules. It is about 2,100 lines, and old breakpoint rules already masked newer behavior.
- Replace local component state scatter with a small workspace/session state layer. The current app has many interdependent `useState` hooks, which makes session refreshes, optimistic turns, and selected IDs easy to desync.
- Generate or share API types between Flask responses and the React client. Right now the TypeScript types must manually match backend JSON, which will keep creating small drift bugs.

## Backend/API improvements

- Add first-class endpoints for session archive/restore if hard deletion is not the desired default.
- Add an activity timestamp endpoint or include `updated_at` in session list responses. The frontend currently infers activity from session state and snapshot fields.
- Add campaign rename/delete/update endpoints to match the now-clickable frontend affordances.
- Add world lookup/selection data to campaign creation. The temporary UI asks for a raw world ID because there is no friendly world picker wired into the React app.
- Add a canon endpoint that returns structured campaign/session facts instead of relying only on session memory snippets.
- Add request/response schema validation around session updates and campaign creation. The backend has helper validation, but several endpoints still accept loosely shaped payloads.
- Tighten socket event contracts. `send_message`, `session_log_update`, streaming chunks, and errors should have shared schemas and tests so the frontend can be less defensive.
- Revisit `aidm_server/blueprints/socketio_events.py`; it is one of the larger backend files and likely wants smaller units around join/leave, turn submit, streaming, and error handling.
- Revisit `aidm_server/llm.py`; provider clients and runtime selection are concentrated in one module. Splitting provider adapters would make future model/provider changes safer.
- Add API-level tests for bad rename/delete cases: empty name, overlong name, missing session, deleting a session with canon-linked entities, and repeated delete.

## Frontend UX improvements

- Add click-outside and Escape-key handling for the account and session menus.
- Add real profile/settings panels behind the account controls instead of only refresh/reconnect actions.
- Make the top backend/provider/model controls usable on mobile. The mobile CSS currently hides most of the ops bar, so important runtime state disappears on small screens.
- Add an explicit "jump to latest" affordance in the turn feed after the user scrolls upward.
- Add loading skeletons or compact loading states for campaign/session switching so stale content is not mistaken for the new selection.
- Make composer tools submit structured intent metadata, not just text prefixes. Prefixes are useful visually, but the backend should know whether a message is OOC, roll, item use, ability check, or emote.
- Add proper roll execution instead of only inserting text like "I roll a d20". The UI should support die type, modifier, advantage/disadvantage, reason, and result visibility.
- Add item and ability selection from the actual character sheet/inventory. The new buttons are wired, but they still produce text templates rather than selecting real game objects.
- Rename visible "Canon" consistently and audit old "Cannon" copy if any remains.

## Repo cleanup and bloat

- Remove generated folders from the working tree before packaging or committing: `aidm_frontend/node_modules`, `aidm_frontend/dist`, Python `__pycache__` folders, and `.pytest_cache`.
- Keep runtime artifacts out of source snapshots: `tmp/*.png`, `tmp/*.log`, launcher PID files, and local browser verification screenshots.
- Move local SQLite backups out of the app source folder or document them as developer-only fixtures. The `aidm_server/instance` directory contains large local database files that should not ship as normal source.
- Decide whether `aidm_server/static/codex` is legacy or still supported. The repo now has both the React frontend and an older Flask-served static frontend, which invites fixes landing in the wrong UI.
- Add a root-level cleanup script or Make target for cache/build artifacts.
- Keep the root `.gitignore` current. It exists now, but it should also cover `tmp/`, launcher PID files/logs, `.pytest_cache/`, and accidental SQLite scratch files such as `aidm_server/:memory:`.

## Build, quality, and release process

- Add CI steps for backend tests, frontend build, frontend lint, and a browser smoke test.
- Add a Playwright smoke test for the local playable route: load campaigns, select a campaign, start/select a session, send a sample action, verify streaming/log update, and inspect the turn feed.
- Add a lightweight accessibility pass for menus, labels, selected states, focus rings, and keyboard navigation.
- Add visual regression screenshots for desktop and mobile around the shell, turn feed, composer, and inspector.
- Add a release checklist item that verifies the React app, not only the Flask static frontend, because both are present in the tree.

## Larger reworks to plan

- Move session display metadata from inferred frontend calculations into backend response DTOs. The frontend should receive `display_name`, `turn_count`, `latest_activity_at`, `latest_summary`, and `is_archived`.
- Create a proper "campaign workspace" endpoint that returns campaign, sessions, players, maps, segments, and summary metadata in one response. The current frontend fans out several requests and then stitches state together.
- Introduce a normalized client data cache. Current reload behavior works, but repeated campaign/session refreshes can cause redundant requests and make optimistic UI fragile.
- Build a real game action model. Rolls, abilities, items, emotes, and OOC should become typed actions with backend validation, not plain messages with conventions in the text.
- Consolidate frontend design tokens and responsive layout rules. The UI has a strong direction, but large CSS files and repeated breakpoint overrides make small changes riskier than they should be.

## Deep audit additions - 2026-06-04

This pass looked across the backend, frontend, scripts, tests, docs, local artifacts, and runtime shape. I did not make large code changes in this pass; these are backlog candidates and cleanup targets.

### Highest-leverage fixes to prioritize next

- Split `aidm_frontend/src/App.tsx` immediately. It is now roughly 3,269 lines and owns socket lifecycle, TTS streaming, campaign/session loading, modal state, derived game state, layout rendering, and UI helpers. Start with `useWorkspaceData`, `useSessionSocket`, `useTtsNarration`, `CampaignRail`, `SessionBoard`, `InspectorPanel`, and `ActionComposer`.
- Split `aidm_frontend/src/App.css`. It is roughly 2,325 lines with many fixed grid rows, short-height overrides, and mobile overrides. Layout bugs are likely because fixes require chasing the same component through multiple breakpoint blocks.
- Split `aidm_server/emergent_memory.py`. It is roughly 1,577 lines and combines provider extraction, regex fallback extraction, validation, entity/fact/thread writes, inventory mutation, projection updates, and retrieval scoring.
- Split `aidm_server/llm.py`. It is roughly 1,066 lines and mixes provider adapters, rate-limit state, context assembly, prompt construction, fallback narration, and stream chunking.
- Turn the post-turn lifecycle into explicit persisted states. The user sees `dm_response_end` before canon extraction/projection/save finishes. The UI needs a visible saved/pending/error state so "narration displayed" is not mistaken for "turn fully persisted."
- Add a real campaign workspace endpoint. The React app currently loads root data, then performs one sessions request per campaign, then fans out campaign/session/player/map/segment requests. This creates extra latency, request races, and more state stitching than the client should own.
- Move session metadata out of `Session.state_snapshot`. Session names, activity time, archive/delete state, and turn counts should be real columns or response DTO fields, not hidden JSON conventions.
- Decide whether the Flask-served Codex UI is still supported. `aidm_server/static/codex` and `aidm_server/templates/codex_frontend.html` are a second frontend that can drift from the React app and attract fixes to the wrong place.

### Backend/API correctness and data model

- Add `updated_at` to `Campaign`, `Session`, `Player`, `Map`, and `CampaignSegment` where user-facing ordering depends on recency. Right now several "Updated" labels are inferred indirectly.
- Add `Session.name`, `Session.status`, `Session.updated_at`, and possibly `Session.deleted_at`. This would let rename/delete/archive behavior be clean and queryable.
- Prefer soft delete/archive for sessions. Hard delete currently removes session-owned rows and nulls canon references. That can be okay locally, but campaign canon may become hard to audit after deleting historical sessions.
- Add campaign update/delete/archive endpoints. The frontend now has campaign creation, but the API surface is asymmetric.
- Add player update endpoints for stats, inventory, sheet, active character, and profile metadata. Inventory changes happen through canon extraction, but the user has no direct correction path.
- Add first-class canon endpoints. The frontend still derives "Canon Facts" mostly from `SessionState.memory_snippets`; it should be able to query `story_entities`, `story_facts`, `story_threads`, and `turn_canon_updates` directly.
- Add pagination/cursors for logs, turns, canon entities, facts, and threads. `/api/sessions/<id>/log` caps at 500 and returns newest entries reversed; this is fine now but not enough for long campaigns.
- Add response DTO builders instead of hand-rolled dictionaries in each blueprint. Campaign/session/player/map/segment JSON shapes are repeated and easy to drift.
- Strengthen write validation. Several endpoints accept arbitrary payload values for strings, JSON text, booleans, and integers. Add length limits, type coercion, enum checks, and consistent `Expected JSON request body` handling.
- Consider SQLAlchemy JSON columns for structured fields when not limited by SQLite portability. `stats`, `inventory`, `character_sheet`, `map_data`, `metadata_json`, `rules_hint`, and `state_snapshot` are mostly JSON text with manual parsing.
- Add indexes for common list/detail paths: `sessions(campaign_id, created_at)`, `session_log_entries(session_id, timestamp, id)`, `campaign_segments(campaign_id, is_triggered)`, and canon lookup combinations.
- Revisit `db.create_all()` in runtime startup. It is convenient locally, but migrations should be the production source of truth. Keep `AIDM_AUTO_CREATE_SCHEMA` local-only and make production fail loudly if migrations are not applied.
- Add DB cascade/delete semantics at the model/migration level instead of relying only on manual delete code. The session delete endpoint has to know too much about canon references.
- Add a schema/data integrity check that compares SQLAlchemy metadata with Alembic head in CI, not just during bootstrap.
- Add a small repository/service layer for session deletion, turn persistence, canon application, and workspace loading. Current blueprints call models directly and own too much transaction detail.
- Make turn event projection rebuildable. `turn_events` is a good append-only spine, but legacy projections (`PlayerAction`, `SessionLogEntry`) should be regenerable or at least auditable from the event stream.
- Add idempotency keys for `send_message` and session creation. Double-clicks, reconnect retries, and socket retries can otherwise create duplicate turns or sessions.
- Add optimistic concurrency checks for session/turn updates. A stale client should not overwrite a newer session rename or runtime state without detection.
- Treat `world_id` in socket `send_message` as informational or remove it from the required payload. The engine correctly uses `campaign.world_id`, so requiring a client-provided `world_id` adds confusion.
- Move module-global socket state (`active_players`, `socketio_connections`) behind an interface. It is fine for one local process, but it will not work correctly with multiple workers or distributed Socket.IO.
- Replace in-memory rate limiting with a pluggable store before multi-process deployment. The current limiter is process-local and resets on restart.
- Do not allow auth tokens in admin query strings long-term. Query tokens get into browser history and logs; keep bearer/header/session-based admin access.
- Add production guardrails around `/api/llm/config`. Writing `.env.local` from an API route is useful locally, but it should be disabled or admin-only outside development.
- Add API auth coverage for all mutating endpoints with `AIDM_AUTH_REQUIRED=true`, including campaign/session/map/segment/player writes and TTS.

### Turn pipeline, LLM, canon, and performance

- Move canon extraction into a real background job queue with durable status. Starting a Flask background task is better than blocking the socket, but it is still process-local and not retryable after a crash.
- Persist the DM response before running canon extraction. If extraction fails, the narration should still be durably saved with `canon_status=failed` or similar.
- Add a `turn_status` event for `received`, `narrating`, `response_complete`, `saving`, `saved`, `canon_pending`, `canon_applied`, and `failed`. The UI can then show accurate state.
- Add latency metrics by phase: context build, provider time to first token, provider total, DM response emitted, DB save, canon extraction, canon validation, projection refresh, TTS request, TTS first audio byte.
- Put hard budgets around `build_emergent_context`. It currently pulls all campaign entities, facts, and threads into Python and sorts them there. That will degrade as canon grows.
- Move more retrieval filtering into SQL or a small local index. At minimum, cap candidate pools before Python scoring.
- Track prompt/context token estimates server-side. The frontend estimates context from loaded text, but the backend knows what was actually sent.
- Extract prompt templates into versioned files or constants with tests. DM narration and canon extraction prompts are high-impact runtime behavior and should not be buried in large functions.
- Add prompt/context snapshot tests. When context shape changes, tests should show exactly what the model receives.
- Add provider capability metadata. Streaming support, reasoning/thinking flags, max tokens, timeout, and default temperature should come from one provider registry.
- Remove duplication between `SUPPORTED_LLM_PROVIDERS`, `LLM_PROVIDER_CATALOG`, defaults in `config.py`, and provider defaults in `llm.py`.
- Make DeepSeek/NVIDIA compatibility explicit. DeepSeek is partially modeled as its own provider and partially as NVIDIA-compatible fallback behavior. That should be easy to reason about from one registry.
- Add rate-limit/cooldown behavior to non-Gemini providers if needed. Gemini has cooldown tracking; OpenAI-compatible providers only attempt fallbacks.
- Consider connection reuse for HTTP providers. `requests.post` per LLM/TTS call is simple, but `requests.Session` with sane lifecycle could reduce handshake overhead.
- Add provider request timeouts by phase where possible. One global timeout hides "connected but no first byte" versus "slow full completion."
- Capture provider/model on stream start as well as on final persistence. If the post-turn save fails, telemetry should still know which provider generated the visible response.
- Sanitize `<thought>`/reasoning output on the backend, not only the frontend TTS path. The UI strips it for speech, but stored logs and exported JSON may still contain reasoning tags if a provider emits them.
- Revisit rule detection and roll parsing as a typed domain service. Right now rolls are detected from natural language and text prefixes; typed roll payloads would be more reliable.
- Add deterministic tests for item loss, quantity merging, and invalid inventory phrases. Inventory extraction has careful heuristics but should be protected as it grows.
- Add a repair/reprojection command that rebuilds `SessionState` from turns/events/canon. This will help when experimental extraction logic changes.

### TTS-specific improvements

- Measure current TTS latency with timestamps in the client and backend before more tuning. Track text visible time, TTS request start, response headers, first audio byte, first `audio.play`, and first audible playback if possible.
- Do not wait for a whole long narrator response before starting TTS. The current chunking starts from streaming text, but the system should be verified end-to-end with real provider timing and browser support.
- Make TTS chunks sentence-aware but smaller for the first chunk. A short first clause/sentence can get speech started quickly while later chunks are queued.
- Prefetch/request the next TTS chunk while the current one is playing. The backend currently requests Deepgram chunks serially while streaming the response body.
- Add a small TTS queue status UI: off, queued, requesting, speaking, failed. The icon alone does not explain delays.
- Add a "stop speech" affordance separate from disabling TTS. Users may want to interrupt one narration without turning the feature off.
- Cache TTS audio by turn ID and text hash for replay. If the same DM response is expanded or revisited, it should not pay provider latency again.
- Add server-side TTS text normalization shared with the client. Markdown/thought stripping currently lives primarily in the React app.
- Consider a dedicated `/api/tts/stream` or WebSocket audio path if browser `MediaSource` support for `audio/mpeg` proves inconsistent. The fallback path waits for the full blob and can feel slow.
- Add automated tests for TTS streaming behavior beyond "response bytes equal fake audio." Tests should verify first chunk streaming, multi-chunk ordering, upstream error handling after chunk one, and client fallback behavior.

### Frontend architecture and UX

- Add a route/settings UI for backend URL and auth token. `baseUrl` is read from `localStorage`, but the React app does not expose a clear way to edit it.
- Replace `window.prompt` and `window.confirm` for session rename/delete with app modals. This was already noted, and it should happen soon because browser dialogs feel jarring in fullscreen.
- Add click-outside and Escape handling for account and session menus. The current menus toggle, but they are not full menu primitives yet.
- Add focus management for dialogs and menus. The create campaign dialog should trap focus, restore focus on close, and submit on Enter predictably.
- Add keyboard shortcuts only after the controls are stable: send, focus composer, stop TTS, toggle fullscreen, refresh, and jump to latest.
- Add a "jump to latest" button when the user scrolls up in the turn feed.
- Auto-scroll policy should be explicit. Streaming should keep the latest text in view unless the user intentionally scrolled away.
- Add loaded-history pagination in the main feed. The backend can cap log size, but the UI should make it clear when older turns are not loaded.
- Add structured loading states per panel. A global "Loading" badge is not enough when campaign, session, player detail, map, and canon data load independently.
- Add empty-state actions. "No sessions yet" should include a start-session action; "No players" should offer create/import player; "No map" should offer create/select map.
- Connect Ability and Item controls to real player data. They currently create text templates instead of selecting from stats, proficiencies, spells, features, or inventory.
- Build a real roll panel. It should support die, modifier, advantage/disadvantage, reason, target pending turn, and result visibility.
- Send typed action metadata to the backend. The composer mode should not only rewrite text prefixes.
- Add player creation/editing to the React frontend. The older Flask static UI has player/campaign dialogs; the React app mostly assumes players exist.
- Add map/segment management to the React frontend if those features remain part of the product.
- Add a real profile/settings panel behind the account controls.
- Make top runtime controls usable on mobile. The mobile CSS hides ops segments, which hides backend/provider/model state.
- Use a reducer or client store for workspace state. There are many interdependent `useState` hooks, and selected campaign/session/player can desync during refreshes.
- Move derived timeline/canon/inventory selectors into pure tested functions. These are business logic now, not just rendering helpers.
- Handle optimistic DM entries carefully. The client adds a synthetic streamed DM entry, then clears it after `session_log_update`; failures can leave a visible response that is not saved unless state is explicit.
- Add frontend error categories. Connection failures, TTS failures, persistence failures, and validation errors are all pushed into one small `errors` array.
- Add toast/history for errors instead of only the latest rail footer message. Some important errors disappear quickly.
- Add browser persistence for selected campaign/session/player with validation. The URL stores campaign/session, but selected player is not similarly durable.
- Revisit the default "prefer Ember" selection logic. It is useful for local testing, but product behavior should not prioritize one campaign title.
- Make exports include turn events and canon tables, not just current UI state.
- Add import/restore flow if export is meant to be more than a debug snapshot.
- Replace hidden live-data text with visible diagnostics or remove it. Hidden operational strings can be useful for tests, but they should be intentional and documented.

### Frontend build, testing, and accessibility

- Add Vitest/unit tests for pure helpers in `App.tsx`: date formatting, composer prefix rewriting, session display metadata, inventory normalization, stat normalization, and TTS flush sizing.
- Add React Testing Library tests for create campaign, session rename/delete, mode switching, TTS toggle, fullscreen fallback, and menu interactions.
- Add Playwright smoke tests for the actual local UI: load campaigns, create campaign, start session, send action, receive streamed DM response, delete session, toggle TTS, and verify no console errors.
- Add visual regression screenshots for desktop, short-height desktop, and mobile. The bottom composer and DM card expansion bugs are exactly the kind visual checks catch.
- Add accessibility checks for icon-only buttons, menu roles, selected states, focus rings, modal focus trap, and color contrast in light mode.
- Add a bundle analyzer budget. The app is small today, but the single component pattern hides growth until it is painful.
- Avoid keeping unused starter assets like `src/assets/react.svg`, `src/assets/vite.svg`, and possibly `src/assets/hero.png` if they are not used by the product UI.

### Security, config, and operations

- Pin Python dependencies or add a lock/constraints file. `requirements.txt` is fully unpinned, which makes test and runtime behavior drift over time.
- Split runtime dependencies from dev/test dependencies. `pytest`, Flask-Migrate, and admin tooling do not necessarily belong in a minimal production install.
- Add a `Makefile` or `justfile` for common commands: install, backend, frontend, test, lint, build, smoke, clean, db-upgrade.
- Add a root health script that checks backend health, frontend availability, TTS config, provider config, and the active database path.
- Add log rotation or bounded logs for `tmp/launcher_logs`. Local logs should not grow forever.
- Add startup checks that warn if the backend binds `0.0.0.0` with auth disabled. This is acceptable for trusted local testing but dangerous on shared networks.
- Require explicit CORS allowlists when `AIDM_ENV=production`. The config defaults already tighten production, but bootstrap/docs should keep this loud.
- Make the admin UI opt-in outside local development. It exposes every model and should be deliberately enabled.
- Avoid token-in-query admin access. It is convenient, but URLs leak more easily than headers.
- Make `.env.local` permissions and presence checks part of local bootstrap. It is currently `600` in this checkout, which is good; keep it enforced.
- Add secret scanning to CI/pre-commit. The user has worked with provider keys locally, so accidental key commits are a realistic risk.
- Update README/runbook for DeepSeek and TTS. README still leans Kimi/Gemini and does not describe the newer TTS/frontend runtime path well.
- Update release checklist to include the React frontend, not only backend/bootstrap checks.
- Add a production deployment section that states what is local-only: `.env.local` writes, wildcard CORS, auth disabled, SQLite, Flask admin, in-memory rate limiting, module-global socket state.

### Repo hygiene and bloat

- Current checkout size is roughly 475M: `.venv` about 303M and `aidm_frontend/node_modules` about 161M. That is normal locally but should never be part of shared archives.
- Remove local runtime artifacts before handoff: `.pytest_cache`, `tmp/*.png`, `tmp/*.mp3`, `tmp/*.headers`, `tmp/launcher_logs/*.pid`, `tmp/launcher_logs/*.log`, and local backend logs.
- Remove or move local SQLite files from `aidm_server/instance` before packaging. The active DB and several backups are local data, not source.
- Delete accidental SQLite scratch file `aidm_server/:memory:` or add it to cleanup rules. A filename like that usually comes from a misinterpreted SQLite memory URI.
- Keep `.env.local` ignored and never copy it into docs, screenshots, or issue text.
- Add `tmp/`, `.pytest_cache/`, launcher logs, and `aidm_server/:memory:` to root ignore/cleanup rules.
- Decide whether the local DB backups should become explicit fixtures. If not, keep them outside the app package.
- Consider moving screenshots used for verification into a separate ignored artifact folder with a cleanup command.

### Documentation and product decisions

- Document which frontend is canonical. Right now docs and routes mention both the hosted/client flow and the local React app.
- Document the turn lifecycle after the async post-turn change: streamed narration, background persistence, canon extraction, projection refresh, session log update.
- Document delete semantics before users rely on it. "Delete session" can mean hide, archive, hard delete, or remove only local transcript.
- Document TTS setup with environment variables, expected latency, failure modes, and how the frontend toggle behaves.
- Document provider switching risks. Changing runtime model mid-session affects continuity and should probably be recorded as a system event.
- Add troubleshooting entries for "DM response visible but not saved", "TTS icon on but silent", "frontend connected to wrong backend port", and "created campaign has no players/sessions."
- Add architecture notes for the event spine and projections so future agents know whether to read `turn_events`, `dm_turns`, or `session_log_entries` as source of truth.

## Exhaustive Senior Engineering Review - 2026-06-04

This review was executed in six sequential passes across backend source, frontend source, scripts, migrations, tests, docs, and local runtime artifacts. No source code was changed in this pass; this section was appended only.

Verification performed during the review:
- `.venv/bin/python -m pytest` passed: 94 tests.
- `npm run build` passed.
- `npm run lint` passed.
- `.venv/bin/python scripts/deploy_bootstrap.py --check-only` passed with an auth-disabled warning.
- `.venv/bin/python scripts/smoke_beta_flow.py` passed, but it used the active local runtime config/database and took roughly 44 seconds.
- `npm audit --omit=dev` reported 0 frontend production vulnerabilities.

### Pass 1 - Architecture & System Design

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

### Highest-Impact Recommendations

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

---

## Review Addendum - 2026-06-06

This addendum was appended after a fresh six-pass review of the current repository state. It intentionally avoids repeating earlier findings that have already been fixed or narrowed by the current implementation.

### Pass 1 - Architecture & System Design

[Priority: Critical] Response Completion Is Not The Same As Durable Turn Completion

* File/Module: `aidm_server/turn_engine.py`, `aidm_frontend/src/useSessionSocket.ts`, `aidm_frontend/src/App.tsx`
* Category: Architecture / Bug
* Current Implementation: `TurnEngine.process(...)` holds the per-session coordinator only while narration runs. `_narrate_turn(...)` emits `dm_response_end`, then `_process_serialized(...)` starts `_background_post_turn(...)`; the background path later saves the DM response, enqueues/applies canon work, and emits `session_log_update`. The React hook clears `sendPending` immediately on `dm_response_end`, and `submitAction(...)` does not require a saved/canon-applied turn status before sending the next message.
* Issue & Why It Matters: A user can submit the next action after seeing the streamed response but before the previous DM response has been persisted into `dm_turns`, `turn_events`, `session_log_entries`, or session projection state. The next context build can miss the immediately previous turn, and a background persistence failure after the user continues is hard to reconcile.
* Recommended Fix: Make turn lifecycle state explicit and server-owned. At minimum, keep the per-session coordinator locked until the DM response event and `DmTurn.dm_output` are durable, then emit a distinct `saved` state. Decide separately whether canon may lag. The client should keep the composer disabled or show a guarded "saving" state until the server confirms the durable stage.
* Difficulty: Hard
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Confirm whether gameplay may intentionally continue before canon projection finishes; even if canon may lag, the DM response itself should be durable before accepting the next turn.

[Priority: High] Workspace Read Models Are Built From Unbounded Hydration Instead Of Query-Bounded Projections

* File/Module: `aidm_server/services/workspace.py`, `aidm_server/response_dtos.py`, `aidm_server/blueprints/campaigns.py`
* Category: Architecture / Performance
* Current Implementation: `campaign_workspace_payload(...)` loads all sessions, players, maps, and segments for a campaign, then each `campaign_payload(...)` and `session_payload(...)` performs additional summary queries. `list_campaigns()` also renders every campaign through `campaign_payload(...)`.
* Issue & Why It Matters: DTO helpers are doing read-model orchestration and aggregate query work. As campaign/session counts grow, the API boundary becomes difficult to reason about because a "simple payload" can issue many hidden queries and hydrate large collections.
* Recommended Fix: Introduce explicit read-model query functions for root campaign cards and campaign workspace. Use grouped aggregate queries for counts/latest activity, keyset pagination for long lists, and lightweight DTO builders that only format already-selected data.
* Difficulty: Moderate
* Requires Further Investigation: No

[Priority: Medium] Maps Can Be Created Without Any Owning World Or Campaign

* File/Module: `aidm_server/models.py`, `aidm_server/blueprints/maps.py`
* Category: Architecture / Data Safety
* Current Implementation: `Map.world_id` and `Map.campaign_id` are both nullable. `create_map()` validates provided IDs, but does not require at least one owner before inserting:
```py
world_id, world_id_error = positive_int(payload.get('world_id'), field='world_id')
campaign_id, campaign_id_error = positive_int(payload.get('campaign_id'), field='campaign_id')
...
new_map = Map(world_id=world_id, campaign_id=campaign_id, ...)
```
* Issue & Why It Matters: A valid API request can create an orphan map that will not appear in campaign- or world-filtered workflows. That creates invisible data and makes cleanup/import/export semantics ambiguous.
* Recommended Fix: Enforce ownership at the API and database layer. Require `campaign_id` or `world_id`, and if both are present keep the existing campaign/world consistency check. Add a check constraint where supported or a migration-level invariant test.
* Difficulty: Easy
* Requires Further Investigation: No

[Priority: Medium] Runtime Provider Switching Is Process-Local Even When It Persists A File

* File/Module: `aidm_server/blueprints/runtime_config.py`, `aidm_server/llm_providers.py`, `scripts/run_local_backend.sh`
* Category: Architecture / Configuration
* Current Implementation: `/api/llm/config` mutates `os.environ`, `current_app.config`, and optionally `.env.local`. Provider instances are recreated from process-local config in `get_provider()`.
* Issue & Why It Matters: The API implies the active runtime has changed globally, but in a multi-process server only the process handling the request sees the in-memory config change. Persisting `.env.local` affects future starts, not already-running workers. This is acceptable for a single local dev process, but fragile if the UI is reused against a long-running or multi-worker backend.
* Recommended Fix: Keep this endpoint explicitly local-only in docs/UI copy, and move active provider settings into a single runtime settings store if live switching is expected beyond one process. Include a worker-broadcast or restart-required response when multiple workers are supported.
* Difficulty: Moderate
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Confirm whether hosted/beta deployments will ever expose `/api/llm/config`; if not, document it as a local-only tool and keep it disabled outside local/test.

### Pass 2 - Bugs, Robustness & Error Handling

[Priority: High] Restoring A Campaign Does Not Restore Sessions Archived By Campaign Archive

* File/Module: `aidm_server/blueprints/campaigns.py`, `aidm_server/services/session_lifecycle.py`
* Category: Bug
* Current Implementation: `archive_campaign()` sets the campaign to `archived` and bulk-updates all child sessions to `archived` with `deleted_at`. `restore_campaign()` only sets the campaign back to `active`; it does not update any child sessions.
* Issue & Why It Matters: A restored campaign can appear empty because all of its sessions remain archived. Since there is no archive-source marker, the backend cannot distinguish sessions archived by the campaign action from sessions the user had already archived intentionally.
* Recommended Fix: Add archive provenance before changing behavior, such as `archived_by_campaign_id` or archived metadata, then restore only sessions archived by that campaign-level operation. If product semantics should restore all child sessions, make that explicit and add regression tests.
* Difficulty: Moderate
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Confirm desired restore semantics for sessions that were already archived before the campaign was archived.

[Priority: High] Session Start Idempotency Is Scan-Based And Not Atomic

* File/Module: `aidm_server/blueprints/sessions.py`, `aidm_server/models.py`, `migrations/versions/`
* Category: Bug / Robustness
* Current Implementation: `_find_idempotent_session(...)` loads every session for a campaign, parses `state_snapshot`, and compares `snapshot["client_session_id"]`. The key is stored inside JSON text rather than a uniquely indexed column.
* Issue & Why It Matters: Two concurrent retries with the same `client_session_id` can both miss each other and create duplicate sessions. The lookup also slows down as a campaign accumulates sessions because it cannot use an index.
* Recommended Fix: Add a nullable `client_session_id` column to `sessions` and a unique constraint/index on `(campaign_id, client_session_id)` for non-null keys. On conflict, return the existing session. Keep reading the JSON key during a migration window if needed.
* Difficulty: Moderate
* Requires Further Investigation: No

[Priority: Medium] Client Treats Failed Or Partial DM Streams As Successful Timeline Entries

* File/Module: `aidm_server/turn_engine.py`, `aidm_frontend/src/useSessionSocket.ts`
* Category: Bug / Robustness
* Current Implementation: The server includes `ok=stream_error is None` in `dm_response_end_payload(...)`. The client registers `socket.on('dm_response_end', () => { ... })`, ignores the payload, clears `sendPending`, creates a normal synthetic DM entry, and may queue TTS for the partial text.
* Issue & Why It Matters: If generation fails after some chunks, the UI can present a partial response as a completed turn. The later `turn_status` or persistence failure becomes secondary to a user-visible success state.
* Recommended Fix: Accept and inspect the `dm_response_end` payload. For `ok: false`, mark the optimistic entry as failed/partial, suppress TTS, keep the composer guarded until a saved or failed status arrives, and show a retry/refresh path.
* Difficulty: Easy
* Requires Further Investigation: No

[Priority: Medium] Ending A Session Drops Existing State Snapshot Metadata

* File/Module: `aidm_server/blueprints/sessions.py`, `aidm_server/services/session_import.py`
* Category: Bug / Data Safety
* Current Implementation: `end_game_session()` replaces `session_obj.state_snapshot` with only `{"recap": ..., "ended_at": ...}`. Imported sessions and idempotently-started sessions can have existing snapshot metadata such as import provenance or `client_session_id`.
* Issue & Why It Matters: Ending a session silently discards metadata that may be needed for auditing, troubleshooting imports, or preserving source-export context. It also makes the state snapshot contract harder to trust because unrelated lifecycle actions erase keys.
* Recommended Fix: Merge recap fields into the existing metadata-cleaned snapshot instead of replacing it. Preserve import/source/idempotency metadata unless there is a specific reason to remove it.
* Difficulty: Easy
* Requires Further Investigation: No

### Pass 3 - Security, Data Safety & Configuration

[Priority: High] DeepSeek Provider Can Reuse An NVIDIA API Key Against The Official DeepSeek Endpoint

* File/Module: `aidm_server/blueprints/runtime_config.py`, `aidm_server/llm_providers.py`, `aidm_server/provider_registry.py`, `scripts/run_local_backend.sh`
* Category: Security / Configuration
* Current Implementation: `provider_configured('deepseek')` returns true when `AIDM_NVIDIA_API_KEY` is present. `_apply_llm_runtime(...)`, `get_provider()`, and `scripts/run_local_backend.sh` also fall back from `AIDM_NVIDIA_API_KEY` to `AIDM_DEEPSEEK_API_KEY` for the `deepseek` provider, even though the provider catalog separates official `deepseek` from `deepseek-v4-pro` via `nvidia`.
* Issue & Why It Matters: Selecting the official DeepSeek provider can send an NVIDIA bearer token to `https://api.deepseek.com/chat/completions`, or mark DeepSeek as configured when only NVIDIA credentials exist. This is a credential-boundary bug and makes provider diagnostics misleading.
* Recommended Fix: Remove `AIDM_NVIDIA_API_KEY` fallback from the official DeepSeek provider path. If DeepSeek via NVIDIA is desired, require provider `nvidia` with model `deepseek-v4-pro`. Add tests covering `provider_configured`, `/api/llm/config`, and `get_provider()` credential selection.
* Difficulty: Easy
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Check whether existing local `.env.local` setups rely on this fallback and provide a migration note for those users.

[Priority: High] API Rate Limiting And Metrics Use Raw Request Paths As Bucket Keys

* File/Module: `aidm_server/main.py`, `aidm_server/rate_limiter.py`, `aidm_server/telemetry.py`
* Category: Security / Performance
* Current Implementation: `before_request` records metrics with `tags={'path': request.path, ...}` and builds the limiter key as `f'{client_ip}:{request.path}'`.
* Issue & Why It Matters: Paths containing IDs create separate metric series and separate rate-limit buckets for every session/campaign/log URL. A client can spread requests across many resource IDs to bypass an intended per-client or per-route limit, and telemetry cardinality grows with user data.
* Recommended Fix: Use normalized route templates from `request.url_rule.rule` or endpoint names for rate-limit buckets and metric tags. Keep raw paths only in structured event payloads where needed for debugging.
* Difficulty: Moderate
* Requires Further Investigation: No

[Priority: Medium] Admin Authorization Is Cached In A Flask Session After One Bearer Success

* File/Module: `aidm_server/blueprints/admin.py`, `aidm_server/auth.py`
* Category: Security
* Current Implementation: `_admin_request_authorized()` returns true whenever `session['aidm_admin_authorized']` is set. A single valid bearer request sets that cookie-backed flag, and subsequent admin requests do not re-check the current bearer token list.
* Issue & Why It Matters: Token rotation or token removal does not immediately revoke existing admin browser sessions. For a local tool this may be fine, but for any exposed beta/admin deployment it weakens revocation semantics.
* Recommended Fix: Either require a valid bearer token on every admin request, or store a short-lived session with an issued-at timestamp and invalidate it when token configuration changes. Add an explicit logout/clear-admin-session route if session caching is kept.
* Difficulty: Moderate
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Confirm whether Flask-Admin is ever enabled in auth-required hosted environments; if it is strictly local, document the local-only revocation tradeoff.

[Priority: Medium] Session Import Bounds Top-Level Events But Not Nested Session-State Lists

* File/Module: `aidm_server/services/session_import.py`, `aidm_server/response_dtos.py`, `aidm_server/llm_context.py`
* Category: Security / Data Safety
* Current Implementation: Session import caps `turnEvents`, `logEntries`, and individual text fields, but persists `active_segments` and `memory_snippets` by dumping `_list(...)` directly into `SessionState`.
* Issue & Why It Matters: A crafted import can pack large or deeply nested arrays into session state within the request-size limit. Those arrays are later returned by state endpoints and partially considered during context building, creating avoidable memory, payload, and UI costs.
* Recommended Fix: Add explicit maximum item counts, per-item shape validation, and per-string length limits for imported `active_segments` and `memory_snippets`. Reject or truncate with an import warning count in the response.
* Difficulty: Easy
* Requires Further Investigation: No

### Pass 4 - Performance & Efficiency

[Priority: High] Campaign And Session DTOs Perform Multiple Aggregate Queries Per Item

* File/Module: `aidm_server/response_dtos.py`, `aidm_server/blueprints/campaigns.py`, `aidm_server/services/workspace.py`
* Category: Performance
* Current Implementation: `campaign_session_summary(...)` runs separate queries for session count, latest session, latest log, latest state, latest turn created, and latest turn completed. `session_payload(...)` also queries session state, latest log, latest turn timestamps, and turn count per session.
* Issue & Why It Matters: Rendering root campaigns or a campaign workspace scales as many queries per visible campaign/session. On a campaign with many sessions, refreshes can become slow and database-heavy even before any LLM work starts.
* Recommended Fix: Replace per-item summary queries with grouped aggregate queries keyed by campaign/session ID. For workspace lists, preload session state and aggregate turn/log counts in one or a small fixed number of queries.
* Difficulty: Moderate
* Requires Further Investigation: No

[Priority: Medium] SQLAlchemy Uses `NullPool` For Every Database Backend

* File/Module: `aidm_server/database.py`
* Category: Performance / Scalability
* Current Implementation: `init_db()` always sets `SQLALCHEMY_ENGINE_OPTIONS` to `{'poolclass': NullPool, 'connect_args': {'check_same_thread': False, 'timeout': 30}}`.
* Issue & Why It Matters: `NullPool` can be reasonable for local SQLite, but for PostgreSQL/MySQL-style deployments it opens and closes a database connection for each use and bypasses normal pooling. SQLite-only `connect_args` are also being applied globally.
* Recommended Fix: Apply `NullPool`, `check_same_thread`, and SQLite timeout options only when the resolved database URI is SQLite. Use SQLAlchemy defaults or configurable `pool_size`/`max_overflow` for non-SQLite backends.
* Difficulty: Easy
* Requires Further Investigation: No

[Priority: Medium] Full List And Workspace Endpoints Lack Pagination Or Field Selection

* File/Module: `aidm_server/blueprints/worlds.py`, `aidm_server/blueprints/maps.py`, `aidm_server/blueprints/segments.py`, `aidm_server/blueprints/players.py`, `aidm_server/services/workspace.py`
* Category: Performance / Scalability
* Current Implementation: `list_worlds()`, `list_maps()`, `list_segments()`, `get_players()`, `list_campaign_session_payloads()`, and `campaign_workspace_payload()` all materialize full result sets with `.all()`.
* Issue & Why It Matters: These endpoints are fine for small local campaigns, but they become progressively slower and larger as the app accumulates worlds, maps, authored segments, players, and session history. The frontend has no way to ask for only the first page or omit heavy sections.
* Recommended Fix: Add consistent `limit` and cursor parameters to list endpoints, and let `/workspace` accept section/limit options such as `?sessions_limit=50&include=players,maps,segments`. Keep current defaults for compatibility but return `has_more` metadata.
* Difficulty: Moderate
* Requires Further Investigation: No

[Priority: Medium] Database Rate Limiter Runs Global Garbage Collection On Every Hit

* File/Module: `aidm_server/rate_limiter.py`, `aidm_server/models.py`
* Category: Performance / Robustness
* Current Implementation: `DatabaseRateLimitStore.hit(...)` opens a transaction, deletes every `rate_limit_events` row older than the current window, counts the current bucket, then inserts the new event.
* Issue & Why It Matters: Under load, every API request can trigger a global delete scan before counting its own bucket. Concurrent count-then-insert operations can also allow brief over-admission on databases with normal concurrent transactions.
* Recommended Fix: Move cleanup to periodic/batched maintenance, or delete only occasionally per process. For stronger limits, use atomic bucket counters with expiry windows or database-specific upsert/locking semantics.
* Difficulty: Moderate
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Confirm whether the database-backed limiter is expected to protect multi-worker deployments; if yes, add concurrency tests against the target database.

[Priority: Low] Segment Activation Sends One PATCH Per Segment From The Client

* File/Module: `aidm_frontend/src/App.tsx`, `aidm_server/blueprints/segments.py`
* Category: Performance / API Design
* Current Implementation: `activateSegment(...)` loops through all loaded segments, PATCHing the selected segment on and every currently triggered segment off one request at a time.
* Issue & Why It Matters: Activating a single segment can fan out into many sequential network requests. This is slow and leaves partial state if the loop fails mid-way.
* Recommended Fix: Add a bulk endpoint such as `POST /api/segments/activate` with `{campaign_id, segment_id, exclusive: true}` and perform the state change in one transaction.
* Difficulty: Moderate
* Requires Further Investigation: No

### Pass 5 - Maintainability, Readability & Cleanup

[Priority: Medium] TypeScript API Contracts Check Keys But Not Runtime Nullability

* File/Module: `aidm_server/api_type_contract.py`, `aidm_server/response_dtos.py`, `tests/test_api_type_contract.py`, `aidm_frontend/src/apiContract.generated.ts`
* Category: Maintainability / Testing
* Current Implementation: Contracts declare fields such as `World.description`, `Campaign.description`, `Player.race`, `Player.class_`, `MapItem.description`, and `CampaignSegment.description/trigger_condition/tags` as `string`. The ORM columns and DTOs can return `None`, and the contract test only compares object keys.
* Issue & Why It Matters: The generated frontend types can be "current" while still lying about nullable values. Future strict frontend code may call string methods on `null` and pass typecheck.
* Recommended Fix: Update the contract to use `string | null` where DTOs can return null. Extend `test_api_type_contract.py` to assert representative value types/nullability, or generate the contract from a schema that encodes nullability.
* Difficulty: Easy
* Requires Further Investigation: No

[Priority: Medium] Request Validation Helpers Are Duplicated Across Blueprints

* File/Module: `aidm_server/validation.py`, `aidm_server/blueprints/campaigns.py`, `aidm_server/blueprints/players.py`
* Category: Maintainability
* Current Implementation: `validation.py` provides `optional_text(...)` and `required_text(...)`, but campaigns and players define local versions with slightly different defaults and behavior.
* Issue & Why It Matters: Validation rules drift by endpoint. A future change to max-length handling, whitespace trimming, null behavior, or error wording can be fixed in one place while similar endpoints remain inconsistent.
* Recommended Fix: Move endpoint-specific defaults into calls to the shared validation helpers, or add named wrappers in `validation.py` for common text-field patterns. Delete the local duplicates after endpoint regression tests pass.
* Difficulty: Easy
* Requires Further Investigation: No

[Priority: Medium] `App.tsx` Still Owns Too Many Async Workflows

* File/Module: `aidm_frontend/src/App.tsx`, `aidm_frontend/src/useWorkspaceStore.ts`, `aidm_frontend/src/useSessionSocket.ts`, `aidm_frontend/src/useTtsNarration.ts`
* Category: Maintainability
* Current Implementation: `App.tsx` is over 3,000 lines and still owns root refresh, workspace refresh, session load, import/export, runtime switching, campaign/session/player dialogs, map/segment writes, socket submission, and selection side effects.
* Issue & Why It Matters: The component is no longer a single blob, but it is still the integration point for many unrelated async state machines. Small changes to one workflow can unintentionally affect selection, optimistic turns, TTS, or workspace cache state.
* Recommended Fix: Extract domain hooks for `useWorkspaceQueries`, `useCampaignActions`, `useSessionActions`, and `useRuntimeSettings`, each with unit tests around request ordering and stale-response handling. Keep `App.tsx` focused on composition and layout state.
* Difficulty: Moderate
* Requires Further Investigation: No

[Priority: Low] Emergent Memory Wrapper Mutates Constants In Another Module At Call Time

* File/Module: `aidm_server/emergent_memory.py`, `aidm_server/canon_retrieval.py`
* Category: Maintainability
* Current Implementation: `emergent_memory.build_emergent_context(...)` assigns `EMERGENT_*_CANDIDATE_LIMIT` values into the imported `canon_retrieval` module before delegating to `canon_retrieval.build_emergent_context(...)`.
* Issue & Why It Matters: Runtime mutation of module-level constants makes tests and concurrent calls harder to reason about. A caller changing limits affects global retrieval behavior, not just one call.
* Recommended Fix: Pass candidate limits as explicit parameters into `canon_retrieval.build_emergent_context(...)`, or move the constants to a shared immutable configuration object.
* Difficulty: Easy
* Requires Further Investigation: No

### Pass 6 - Testing, Documentation & Developer Experience

[Priority: High] CI Does Not Run The Existing Secret Scan Or Production Dependency Audit

* File/Module: `.github/workflows/ci.yml`, `scripts/scan_secrets.py`, `tests/test_secret_scan.py`, `aidm_frontend/package.json`, `docs/release_checklist.md`
* Category: Security / Developer Experience
* Current Implementation: CI runs backend pytest and frontend test/build/bundle/browser-smoke. The Makefile has `make secrets`, tests cover `scripts/scan_secrets.py`, and the release checklist asks for `npm audit --omit=dev`, but neither check is enforced in GitHub Actions.
* Issue & Why It Matters: Secret scanning and dependency audit are manual release chores. A leaked key pattern or production npm advisory can land in a PR even though local scripts already exist to catch them.
* Recommended Fix: Add CI steps for `python scripts/scan_secrets.py` and `cd aidm_frontend && npm audit --omit=dev` with an explicit audit policy. Consider `pip-audit` once Python dependency policy is defined.
* Difficulty: Easy
* Requires Further Investigation: No

[Priority: Medium] README Migration Chain Is Stale

* File/Module: `README.md`, `migrations/versions/`
* Category: Documentation / Developer Experience
* Current Implementation: The README migration chain lists `0001` through `0005_turn_event_spine`. The repository now has migrations through `0009_canon_jobs`, including metadata/status indexes, rate-limit events, session delete semantics, and canon jobs.
* Issue & Why It Matters: Developers using the README as a schema map will miss four migrations and newer tables. That is especially risky for production bootstrap, backup/restore checks, and debugging canon job behavior.
* Recommended Fix: Update the README chain through `0009`, or replace the static list with a generated/current reference to `migrations/versions/` plus a short table of major schema additions.
* Difficulty: Easy
* Requires Further Investigation: No

[Priority: Medium] Projection Repair CLI Creates Missing Schema Instead Of Enforcing Migrations

* File/Module: `scripts/reproject_session.py`, `aidm_server/database.py`, `aidm_server/reprojection.py`
* Category: Developer Experience / Data Safety
* Current Implementation: `scripts/reproject_session.py` loads runtime env, creates the app, and calls `ensure_schema(app)` before repairing projections.
* Issue & Why It Matters: A repair tool should operate on the intended migrated database. Calling `db.create_all()` can hide a missing migration state or create partial tables in the wrong environment, which conflicts with the stricter migration-first bootstrap path.
* Recommended Fix: Remove unconditional `ensure_schema(app)` from the repair CLI. Require migrations to be applied first, or add an explicit `--create-schema-for-local-dev` flag that is rejected when `AIDM_ENV=production`.
* Difficulty: Easy
* Requires Further Investigation: No

[Priority: Medium] High-Risk Concurrency Paths Lack Regression Coverage

* File/Module: `tests/test_canon_jobs.py`, `tests/test_sessions_endpoints.py`, `tests/test_rate_limiter.py`, `aidm_server/canon_jobs.py`, `aidm_server/blueprints/sessions.py`, `aidm_server/rate_limiter.py`
* Category: Testing
* Current Implementation: Tests cover normal canon job success/failure/retry, normal session idempotent replay, and rate limiter behavior. They do not simulate two workers claiming the same canon job, two simultaneous `/api/sessions/start` requests with the same idempotency key, or concurrent database-backed limiter hits.
* Issue & Why It Matters: The riskiest failures here are race/order failures that unit tests with a single Flask app context will not expose. These paths affect duplicate sessions, duplicate canon application, and rate-limit enforcement under load.
* Recommended Fix: Add concurrency-focused tests using independent SQLAlchemy sessions or multiprocessing/threading against a temporary file SQLite database, and run a subset against the intended production database if one is supported.
* Difficulty: Moderate
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Confirm which database backends and worker models are supported before choosing the exact locking/upsert assertions.

[Priority: Low] Visual Smoke Exists But Is Not Part Of Automated CI

* File/Module: `.github/workflows/ci.yml`, `aidm_frontend/scripts/visual-smoke.cjs`, `aidm_frontend/package.json`, `README.md`
* Category: Testing / Developer Experience
* Current Implementation: `npm run smoke:visual` exists and README documents it, but CI only runs the browser smoke script.
* Issue & Why It Matters: Layout regressions, horizontal overflow, and screenshot-only UI breakage can pass CI even though the project already has a visual smoke tool designed to catch them.
* Recommended Fix: Add a separate optional CI job for `npm run smoke:visual`, uploading screenshots on failure. If runtime is a concern, run it on pull requests that touch frontend files or nightly.
* Difficulty: Easy
* Requires Further Investigation: No

### Highest-Impact Recommendations

1. * Recommendation: Make turn durability a server-enforced lifecycle state before accepting the next action.
   * Why it matters: Prevents continuity bugs where the next prompt misses the previous DM response or canon state.
   * Affected area/files: `aidm_server/turn_engine.py`, `aidm_frontend/src/useSessionSocket.ts`, `aidm_frontend/src/App.tsx`
   * Suggested first step: Keep the turn coordinator locked until `DmTurn.dm_output` and the DM response event are committed, then emit a `saved` status the client must observe before re-enabling input.
   * Related finding title: Response Completion Is Not The Same As Durable Turn Completion

2. * Recommendation: Fix provider credential boundaries.
   * Why it matters: Avoids sending an NVIDIA credential to the official DeepSeek endpoint and makes runtime diagnostics trustworthy.
   * Affected area/files: `aidm_server/blueprints/runtime_config.py`, `aidm_server/llm_providers.py`, `scripts/run_local_backend.sh`
   * Suggested first step: Remove `AIDM_NVIDIA_API_KEY` fallback from the `deepseek` provider path and add credential-selection tests.
   * Related finding title: DeepSeek Provider Can Reuse An NVIDIA API Key Against The Official DeepSeek Endpoint

3. * Recommendation: Replace JSON-scanned session idempotency with a database-enforced key.
   * Why it matters: Prevents duplicate sessions under retry/concurrency and speeds session start checks.
   * Affected area/files: `aidm_server/blueprints/sessions.py`, `aidm_server/models.py`, `migrations/versions/`
   * Suggested first step: Add `sessions.client_session_id` plus a unique `(campaign_id, client_session_id)` index for non-null values.
   * Related finding title: Session Start Idempotency Is Scan-Based And Not Atomic

4. * Recommendation: Normalize API rate-limit and telemetry tags to route templates.
   * Why it matters: Prevents ID-spray rate-limit bypass and unbounded metric cardinality.
   * Affected area/files: `aidm_server/main.py`, `aidm_server/rate_limiter.py`, `aidm_server/telemetry.py`
   * Suggested first step: Replace `request.path` in limiter keys and metric tags with `request.url_rule.rule` or endpoint names.
   * Related finding title: API Rate Limiting And Metrics Use Raw Request Paths As Bucket Keys

5. * Recommendation: Build bounded read models for campaign/session summaries.
   * Why it matters: Keeps common refresh paths fast as campaign history grows.
   * Affected area/files: `aidm_server/response_dtos.py`, `aidm_server/services/workspace.py`, `aidm_server/blueprints/campaigns.py`
   * Suggested first step: Create grouped aggregate queries for root campaign cards and workspace session summaries.
   * Related finding title: Campaign And Session DTOs Perform Multiple Aggregate Queries Per Item

6. * Recommendation: Align generated API contracts with real runtime nullability.
   * Why it matters: Prevents frontend type confidence from masking null crashes.
   * Affected area/files: `aidm_server/api_type_contract.py`, `aidm_server/response_dtos.py`, `tests/test_api_type_contract.py`, `aidm_frontend/src/apiContract.generated.ts`
   * Suggested first step: Update nullable string fields and expand contract tests beyond key equality.
   * Related finding title: TypeScript API Contracts Check Keys But Not Runtime Nullability

7. * Recommendation: Add archive provenance for campaign-level session archival.
   * Why it matters: Makes campaign restore predictable without resurrecting sessions the user deliberately archived earlier.
   * Affected area/files: `aidm_server/blueprints/campaigns.py`, `aidm_server/services/session_lifecycle.py`, `migrations/versions/`
   * Suggested first step: Store whether a session was archived by a campaign-level operation, then restore only those children.
   * Related finding title: Restoring A Campaign Does Not Restore Sessions Archived By Campaign Archive

8. * Recommendation: Move existing security and visual checks into CI.
   * Why it matters: Turns documented/manual release checks into enforceable regressions.
   * Affected area/files: `.github/workflows/ci.yml`, `scripts/scan_secrets.py`, `aidm_frontend/scripts/visual-smoke.cjs`, `aidm_frontend/package.json`
   * Suggested first step: Add CI steps for secret scan and `npm audit --omit=dev`, then add a conditional or nightly visual smoke job.
   * Related finding title: CI Does Not Run The Existing Secret Scan Or Production Dependency Audit

---

## Implementation Verification Addendum - 2026-06-06

This addendum was appended after checking the existing suggestion set against the current repository. Duplicate and overlapping suggestions were grouped by implementation area during verification.

Verification performed:
- `.venv/bin/python -m pytest -q` passed: 244 tests.
- `cd aidm_frontend && npm test` passed: typecheck, lint, and 33 Vitest tests.
- `cd aidm_frontend && npm run build` passed.
- `cd aidm_frontend && npm run bundle:budget` passed.
- `cd aidm_frontend && npm audit --omit=dev` reported 0 vulnerabilities.

Implemented or verified in this pass:
- Campaign creation now loads `/api/worlds` and provides an existing-world selector plus a separate new-world field.
- Campaign workspace summary counts now report full collection counts even when workspace lists are limited.
- README/API docs now include `GET /api/worlds`, campaign archive/restore/delete/update routes, session archive/restore, current DeepSeek provider modules, and DeepSeek/TTS table-of-contents entries.
- Frontend README now uses `npm ci`, includes `npm test` and `npm audit --omit=dev`, documents runtime Backend Settings, and notes session-scoped auth-token storage.
- The frontend launch service now installs from the lockfile with `npm ci`.

### Remaining Implementation Gaps

[Priority: Medium] `App.tsx` Still Owns Several Async Product Workflows

* File/Module: `aidm_frontend/src/App.tsx`, `aidm_frontend/src/useWorkspaceQueries.ts`, `aidm_frontend/src/useWorkspaceStore.ts`, `aidm_frontend/src/useSessionSocket.ts`, `aidm_frontend/src/useTtsNarration.ts`
* Category: Maintainability
* Current Implementation: The app has been split into presentational components, selector modules, socket/TTS/runtime/workspace hooks, and shared CSS files. `App.tsx` is still about 3,000 lines and owns several async workflows: player creation/editing, map and segment writes, session import/export, campaign create/rename/archive, session rename/delete, runtime switching, fullscreen fallback, and composer/dice submission orchestration.
* Issue & Why It Matters: The largest single-component risk has been reduced, but `App.tsx` remains the integration hub for multiple unrelated async state machines. Future changes can still accidentally couple persistence, selection, socket, TTS, and dialog state.
* Recommended Fix: Continue extraction into focused action hooks, starting with `useCampaignActions`, `useSessionActions`, and `useWorldMapSegmentActions`. Keep the current test coverage as a regression harness while moving one workflow family at a time.
* Difficulty: Moderate
* Requires Further Investigation: No
* If yes, explain what must be checked or confirmed: N/A

[Priority: Medium] Multi-Worker Turn Serialization Remains A Deployment Constraint

* File/Module: `aidm_server/turn_coordinator.py`, `aidm_server/turn_engine.py`, `docs/beta_runbook.md`, `README.md`
* Category: Architecture / Scalability
* Current Implementation: The per-session coordinator now keeps turn processing locked through durable DM-response persistence and prunes idle locks. Docs clearly state that the coordinator is single-process and that multi-worker deployments need a durable queue or database/advisory-lock strategy.
* Issue & Why It Matters: Local and single-process beta play are protected, but multiple Socket.IO workers can still process turns without shared serialization. This is a remaining scalability boundary, not a missing local-play fix.
* Recommended Fix: Before multi-worker deployment, move turn execution into a durable per-session queue or add database/advisory locks around the turn lifecycle. Add concurrency tests against the selected production database.
* Difficulty: Hard
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Confirm the intended hosted worker model and database backend before choosing queue, advisory-lock, or job-runner semantics.

[Priority: Low] Managed External Observability Stack Is Still Not Bundled

* File/Module: `aidm_server/telemetry.py`, `README.md`, `docs/release_checklist.md`, `.github/workflows/ci.yml`
* Category: Developer Experience / Operations
* Current Implementation: The app has local `/api/metrics`, optional outbound telemetry delivery, release checklist entries, and tests for external telemetry failure handling. It does not bundle or provision a managed metrics/logging dashboard such as Prometheus/Grafana or a hosted observability provider.
* Issue & Why It Matters: Code-level telemetry hooks are present, but production-style beta operations still require selecting and configuring the external observability destination. Without that operational step, metrics remain local or best-effort outbound events.
* Recommended Fix: Pick the beta observability backend, document required environment variables, and add a smoke check that verifies a real event reaches that backend in staging.
* Difficulty: Moderate
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Confirm the desired observability provider and whether provisioning belongs in this repository or external deployment infrastructure.

### Highest-Impact Recommendations

1. * Recommendation: Finish extracting async action hooks from `App.tsx`.
   * Why it matters: This is the biggest remaining maintainability risk after the current frontend split.
   * Affected area/files: `aidm_frontend/src/App.tsx`
   * Suggested first step: Extract campaign create/rename/archive into `useCampaignActions` with the existing App tests as guardrails.
   * Related finding title: `App.tsx` Still Owns Several Async Product Workflows

2. * Recommendation: Decide the production turn-serialization strategy before running multiple workers.
   * Why it matters: Single-process turn ordering is now clear and tested, but distributed ordering still needs a real coordination primitive.
   * Affected area/files: `aidm_server/turn_coordinator.py`, `aidm_server/turn_engine.py`
   * Suggested first step: Choose durable queue vs database/advisory lock for the target deployment.
   * Related finding title: Multi-Worker Turn Serialization Remains A Deployment Constraint

3. * Recommendation: Wire the optional telemetry client to a concrete beta observability backend.
   * Why it matters: Local metrics and outbound hooks are useful, but beta operations need a destination dashboard and alerting path.
   * Affected area/files: `aidm_server/telemetry.py`, `README.md`, `docs/release_checklist.md`
   * Suggested first step: Select the provider and add a staging telemetry smoke check.
   * Related finding title: Managed External Observability Stack Is Still Not Bundled

---

## Implementation Verification Addendum 2 - 2026-06-06

This addendum was appended after implementing the remaining concrete gaps from the prior addendum. The earlier findings are preserved above for history.

Verification performed:
- `.venv/bin/python -m pytest tests/test_turn_coordinator.py tests/test_deploy_bootstrap.py tests/test_telemetry.py tests/test_socketio_flow.py -q` passed: 49 tests.
- `cd aidm_frontend && npm test` passed: typecheck, lint, and 33 Vitest tests.
- `ruby -e 'require "yaml"; ...'` parsed the bundled observability YAML files.
- `.venv/bin/python -m json.tool observability/grafana/dashboards/aidm-overview.json` parsed the Grafana dashboard JSON.
- `docker compose -f observability/docker-compose.yml config` could not be run because Docker is not installed in this local environment.

Implemented in this pass:
- Extracted the requested frontend action-hook families from `App.tsx`:
  - `aidm_frontend/src/useCampaignActions.ts` owns campaign create/rename/archive and new/existing world selection behavior.
  - `aidm_frontend/src/useSessionActions.ts` owns session start, import/export, share-link, rename, and delete workflows.
  - `aidm_frontend/src/useWorldMapSegmentActions.ts` owns default player creation, map create/update, segment create/activate/delete, and their pending states.
- Reduced `aidm_frontend/src/App.tsx` from roughly 3,000 lines at the prior addendum to 2,517 lines while keeping existing App regression tests passing.
- Added `GET /api/metrics/prometheus` and Prometheus text exposition rendering for telemetry counters, timing aggregates, and beta summary gauges.
- Added a local observability bundle under `observability/` with Prometheus scrape config, Grafana provisioning, and an `AIDM Beta Overview` dashboard.
- Added an opt-in database-backed per-session turn coordinator for multi-worker deployments:
  - `AIDM_TURN_COORDINATOR_STORE=database`
  - `AIDM_TURN_COORDINATOR_LOCK_TTL_SECONDS`
  - `AIDM_TURN_COORDINATOR_POLL_INTERVAL_MS`
  - migration `0011_session_turn_locks`
- Added coordinator tests proving database locks serialize across coordinator instances, reclaim expired locks, and are selected by configuration.
- Added bootstrap validation for invalid turn coordinator store, lock TTL, and polling interval values.
- Updated README, beta runbook, and release checklist for the Prometheus/Grafana bundle and database turn coordinator.

### Remaining Follow-Up Items

[Priority: Low] App Runtime And Composer Orchestration Still Live In `App.tsx`

* File/Module: `aidm_frontend/src/App.tsx`
* Category: Maintainability
* Current Implementation: Campaign, session, world/map/segment, workspace, socket, TTS, and runtime-settings workflows now have dedicated hooks. `App.tsx` still owns player edit dialog submission, runtime provider/model switching, fullscreen fallback, and composer/dice orchestration.
* Issue & Why It Matters: The highest-risk action workflow clusters have been extracted, but `App.tsx` remains the top-level integration component. Further extraction would make it easier to modify character profile editing and dice/composer behavior independently.
* Recommended Fix: In a future cleanup pass, extract `usePlayerProfileActions` and `useComposerActions` once their desired boundaries are clear.
* Difficulty: Moderate
* Requires Further Investigation: No
* If yes, explain what must be checked or confirmed: N/A

[Priority: Low] Production Observability Still Needs Hosted Alert Routing

* File/Module: `observability/`, `aidm_server/telemetry.py`, deployment configuration
* Category: Developer Experience / Operations
* Current Implementation: The repository now bundles a local Prometheus/Grafana stack and exposes Prometheus scrape metrics. Optional outbound telemetry delivery remains provider-agnostic.
* Issue & Why It Matters: Local dashboards cover beta smoke testing, but production alert routing and ownership still depend on the chosen hosting environment or managed observability provider.
* Recommended Fix: During deployment setup, choose the hosted observability destination, configure alerts, and add a staging smoke check that proves dashboard ingestion and alert routing.
* Difficulty: Moderate
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Confirm the production hosting platform and alerting owner.

[Priority: Low] Multi-Worker Socket Deployment Still Needs Deployment-Level Affinity Or Queueing

* File/Module: `aidm_server/turn_coordinator.py`, Socket.IO deployment configuration
* Category: Architecture / Scalability
* Current Implementation: The repository now includes an opt-in database-backed per-session turn lock that coordinates turn execution across backend workers. Socket.IO connection state is still module-local.
* Issue & Why It Matters: Shared turn locks protect turn ordering, but a multi-worker Socket.IO deployment still needs sticky sessions or a shared message queue so events reach the connected clients reliably.
* Recommended Fix: Pair `AIDM_TURN_COORDINATOR_STORE=database` with the selected Socket.IO deployment strategy, then run a staging two-worker smoke test.
* Difficulty: Moderate
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Confirm the production Socket.IO worker model and whether sticky sessions or a shared message queue will be used.

### Highest-Impact Recommendations

1. * Recommendation: Run a two-worker staging smoke test with `AIDM_TURN_COORDINATOR_STORE=database`.
   * Why it matters: Code-level shared turn locks are implemented, but deployment-level socket routing must also be validated.
   * Affected area/files: `aidm_server/turn_coordinator.py`, deployment configuration
   * Suggested first step: Apply migration `0011_session_turn_locks`, enable the database coordinator, and send concurrent turns to the same session from two workers.
   * Related finding title: Multi-Worker Socket Deployment Still Needs Deployment-Level Affinity Or Queueing

2. * Recommendation: Start the local observability bundle during beta rehearsal.
   * Why it matters: The new Prometheus/Grafana stack should be exercised against real beta traffic before relying on it for triage.
   * Affected area/files: `observability/`, `aidm_server/blueprints/system.py`, `aidm_server/telemetry.py`
   * Suggested first step: Run the backend on port `5050`, then run `cd observability && docker compose up` on a machine with Docker installed.
   * Related finding title: Production Observability Still Needs Hosted Alert Routing

3. * Recommendation: Keep future frontend cleanup focused on remaining orchestration hooks.
   * Why it matters: The largest persistence workflows have been extracted; remaining cleanup should avoid broad rewrites.
   * Affected area/files: `aidm_frontend/src/App.tsx`
   * Suggested first step: Extract player profile editing or composer/dice orchestration as a separate, tested hook.
   * Related finding title: App Runtime And Composer Orchestration Still Live In `App.tsx`

### Final Verification Note

Additional verification after the addendum:
- `.venv/bin/python -m pytest -q` passed: 251 tests.
- `cd aidm_frontend && npm test` passed: typecheck, lint, and 33 Vitest tests.
- `cd aidm_frontend && npm run build` passed.
- `cd aidm_frontend && npm run bundle:budget` passed.
- `cd aidm_frontend && npm audit --omit=dev` reported 0 vulnerabilities.
- `docker compose -f observability/docker-compose.yml config` remains unverified locally because Docker is not installed in this environment.

---

## Implementation Verification Addendum 3 - 2026-06-06

This addendum was appended after checking the remaining concrete follow-up items from Addendum 2. Earlier findings are preserved above for history.

Verification performed:
- `cd aidm_frontend && npm run typecheck` passed.
- `cd aidm_frontend && npm run lint` passed.
- `cd aidm_frontend && npm run test:unit` passed: 5 test files and 34 Vitest tests.
- `cd aidm_frontend && npm test` passed: typecheck, lint, and 34 Vitest tests.
- `cd aidm_frontend && npm run build` passed.
- `cd aidm_frontend && npm run bundle:budget` passed.
- `cd aidm_frontend && npm audit --omit=dev` reported 0 vulnerabilities.
- `.venv/bin/python -m pytest -q` passed: 251 tests.

Implemented in this pass:
- Addressed the concrete code follow-up titled `App Runtime And Composer Orchestration Still Live In App.tsx`.
- Added `aidm_frontend/src/usePlayerProfileActions.ts` for player edit dialog state, validation, API update, cache update, and persistence errors.
- Added `aidm_frontend/src/useComposerActions.ts` for composer text state, mode switching, ability/item targeting, pending roll targeting, dice roll lifecycle, optimistic player turn creation, and Socket.IO send payloads.
- Preserved the existing dice-roll behavior where the roll result stays hidden until the dice landing animation completes.
- Reduced `aidm_frontend/src/App.tsx` from 2,517 lines at Addendum 2 to 2,317 lines while keeping the frontend and backend verification suites passing.

Implementation coverage status:
- All concrete local code, test, documentation, and configuration items reviewed in this pass now have local verification.
- The remaining unproven items are deployment-specific and cannot be fully confirmed from this local checkout without the target hosting environment.

### Remaining Follow-Up Items

[Priority: Low] Hosted Observability Alert Routing Still Requires Deployment Configuration

* File/Module: `observability/`, `aidm_server/telemetry.py`, deployment configuration
* Category: Developer Experience / Operations
* Current Implementation: The repository exposes Prometheus-format metrics, bundles a local Prometheus/Grafana stack, and keeps provider-agnostic outbound telemetry hooks.
* Issue & Why It Matters: Local metrics and dashboards are implemented, but production alert routing still depends on the eventual hosting platform or managed observability provider.
* Recommended Fix: Configure the hosted metrics/logging destination, define alert ownership, and add a staging smoke check that proves a real backend event reaches the dashboard and alert route.
* Difficulty: Moderate
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Confirm the production hosting platform, observability provider, alert ownership, and whether provisioning belongs in this repository or external infrastructure.

[Priority: Low] Multi-Worker Socket Routing Still Requires Staging Verification

* File/Module: `aidm_server/turn_coordinator.py`, Socket.IO deployment configuration
* Category: Architecture / Scalability
* Current Implementation: The repository includes an opt-in database-backed per-session turn lock for shared turn execution coordination across backend workers. Socket.IO connection state remains process-local unless the deployment adds affinity or shared message routing.
* Issue & Why It Matters: Shared turn locks protect turn ordering, but connected clients in a multi-worker deployment still need sticky sessions or a Socket.IO message queue so emitted events reach the right clients reliably.
* Recommended Fix: Pair `AIDM_TURN_COORDINATOR_STORE=database` with the selected Socket.IO deployment strategy, then run a two-worker staging smoke test that sends concurrent turns and verifies both turn persistence and client event delivery.
* Difficulty: Moderate
* Requires Further Investigation: Yes
* If yes, explain what must be checked or confirmed: Confirm the production Socket.IO worker model, database backend, and whether sticky sessions or a shared message queue will be used.

### Highest-Impact Recommendations

1. * Recommendation: Run a two-worker staging smoke test with `AIDM_TURN_COORDINATOR_STORE=database`.
   * Why it matters: Local shared-lock tests pass, but production socket routing must be proven with the actual worker model.
   * Affected area/files: `aidm_server/turn_coordinator.py`, Socket.IO deployment configuration
   * Suggested first step: Apply migration `0011_session_turn_locks`, enable the database coordinator, start two backend workers, and send concurrent turns to the same session.
   * Related finding title: Multi-Worker Socket Routing Still Requires Staging Verification

2. * Recommendation: Configure hosted alert routing for beta operations.
   * Why it matters: The local observability bundle works as repository infrastructure, but beta triage needs a live alert destination and owner.
   * Affected area/files: `observability/`, `aidm_server/telemetry.py`, deployment configuration
   * Suggested first step: Choose the hosted observability provider and add a staging smoke check for dashboard ingestion and alert delivery.
   * Related finding title: Hosted Observability Alert Routing Still Requires Deployment Configuration

3. * Recommendation: Keep future frontend cleanup incremental and hook-focused.
   * Why it matters: The main App orchestration is substantially smaller now, and broad rewrites would add more risk than value.
   * Affected area/files: `aidm_frontend/src/App.tsx`, `aidm_frontend/src/useComposerActions.ts`, `aidm_frontend/src/usePlayerProfileActions.ts`
   * Suggested first step: Only extract another hook when a clearly bounded workflow remains duplicated or difficult to test.
   * Related finding title: App Runtime And Composer Orchestration Still Live In `App.tsx`

### Rendered Smoke Verification Note

Additional verification after Addendum 3:
- `cd aidm_frontend && npm run smoke:browser` passed after updating the smoke selector for the renamed `New World Name` field. The flow covered create campaign, create player, start session, manage map/segments, unavailable TTS state, send action, receive DM response, delete session, import session, and delete imported session.
- `cd aidm_frontend && npm run smoke:visual` passed after narrowing the character assertion to the unique right-inspector heading. The run wrote screenshots under `tmp/verification_artifacts/visual-smoke/2026-06-06T22-02-15-821Z/`.
- `cd aidm_frontend && npm run lint` passed after the smoke-script selector updates.
- Line count correction: the final status check showed `aidm_frontend/src/App.tsx` at 2,322 lines.
