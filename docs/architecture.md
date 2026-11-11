# AIDM Architecture

This document is the current high-level map of the application. Detailed route,
provider, and state contracts live in [API surface](api_surface.md),
[LLM provider routing](llm_provider_routing.md), and
[runtime state boundaries](runtime_state_boundaries.md).

## Application Composition

- `aidm_server/main.py` builds the Flask application, request middleware,
  authentication and workspace context, CORS, rate limiting, telemetry, schema
  guardrails, blueprints, and Socket.IO runtime.
- `aidm_server/config.py` owns environment parsing and deployment validation.
  Production rejects an ephemeral `FLASK_SECRET_KEY`, automatic schema creation,
  and other unsafe runtime combinations.
- `aidm_server/deploy_bootstrap.py` runs preflight checks and migrations. It may
  serve local or test runs, but production uses `--check-only` before starting
  the production Socket.IO server.
- The React/Vite frontend lives in `aidm_frontend/`. Development normally uses a
  Vite origin with API and Socket.IO proxies; unified builds are served by Flask
  from one origin.

## Access Control And Workspaces

- Account sessions use bearer account tokens stored as hashes server-side.
- Workspace access can come from configured workspace tokens or saved account
  workspace membership. Browser credentials are attached only to the configured
  backend origin.
- `aidm_server/capabilities.py` is the fail-closed authorization inventory for
  HTTP methods and incoming Socket.IO events. It separates player reads/actions,
  DM authoring/runtime control, debug reads, and workspace administration.
- The application validates the HTTP capability inventory during construction;
  a newly exposed route must be classified as public, self-service, or assigned
  a capability.
- Existing accounts with no password hash are legacy accounts. They cannot log
  in or set a password from username/name fields alone. Password setup requires
  a valid saved account token or a high-entropy replacement issued by the
  operator; recovery use rotates that token after the password is set.
- Player visibility flows through workspace and account helpers rather than
  route-local filters.
- Authored map visibility is enforced by the shared map query/direct-lookup
  policy: players receive revealed maps, while DM-only records stay in
  authoring views and are absent from realtime and session-export contracts.

## HTTP And Realtime Boundaries

- REST blueprints are registered in `aidm_server/main.py`; response DTOs are
  declared in `aidm_server/api_type_contract.py` and built through
  `aidm_server/response_dtos.py`.
- `scripts/generate_api_types.py` generates
  `aidm_frontend/src/apiContract.generated.ts`. `make api-types` refreshes it,
  and `make dev-check` verifies that it is current.
- GET endpoints are read-only. Repair, import, lifecycle, and other mutations use
  explicit write endpoints, migrations, or operator tools.
- `aidm_server/blueprints/socketio_events.py` wires the Socket.IO handlers.
  Presence, typing, music, turn control, player messages, and clarifications are
  implemented in the split `aidm_server/socket_*.py` modules.
- `aidm_server/socket_contracts.py` parses incoming payloads.
  `aidm_server/socket_runtime.py` resolves authenticated account/workspace
  context and connection capabilities; `aidm_server/blueprints/socketio_events.py`
  wires the capability and workspace checks into each event handler.
- On reconnect, the frontend rejoins the room and reloads both the current
  persisted session snapshot and the selected player's authoritative detail,
  so missed HP, inventory, and other character events do not leave stale UI.
  An uncertain retry reuses the exact original
  `client_message_id`. A completed row emits `turn_duplicate`; an incomplete
  `processing` row replays its persisted private roll receipt to the requester
  and resumes from its pipeline state without another incoming event, roll,
  peer rebroadcast, or already-recorded pre-DM application.
- Live Socket.IO operations fail closed when either the session or its campaign
  is archived or deleted. Join, turn submission, clarification resolution, and
  turn-control handlers return stable lifecycle error codes until an operator
  restores the target.

## Gameplay And Runtime State

- Player turns enter through Socket.IO, then flow through turn coordination,
  durable player events, rules and roll policy, DM generation, state extraction,
  validation, application, and canon queueing.
- `aidm_server/turn_engine.py` orchestrates a turn. Supporting behavior is split
  across `turn_coordinator.py`, `turn_rules.py`, `turn_roll_policy.py`,
  `turn_narration.py`, `turn_segments.py`, and `turn_events.py`; the pre- and
  post-narration pipeline lives in `game_state/orchestration/turn_pipeline.py`.
- Every turn carries a durable lifecycle marker (`metadata.lifecycle.phase`:
  `accepted`, `rules_applied`, `narration_saved`, `effects_applied`,
  `finished`), written in the same transaction as the step that reaches it.
  An unfinished turn resumes from its phase without repeating a step: on a
  retry of the same `client_message_id`, before any new submission to the
  session (at most three abandoned turns, oldest first), or in a background task
  started by `join_session` that takes the turn locks only when they are free.
  After three failed resumes the turn fails behind the recovery gate. Finished
  turns drop their pre-turn snapshot copies (`snapshotsPruned`).
- Turns serialize per campaign. A turn or state mutation locks its session, its
  campaign-pack siblings and every other live session of the same campaign
  (`campaign_turn_lock_session_ids` in `services/session_state_mutation.py`), in
  ascending session-id order, because those sessions share Player rows.
  Post-narration effects are applied onto freshly read Player rows.
- A player who disconnects while holding the structured turn is skipped after
  `AIDM_ABSENT_TURN_GRACE_SECONDS` (default 60); the opening of an adventure is
  a marked turn (`message_kind: "opening"`) that asks for no roll and makes no
  helper-model call.
- Free-text words that clearly name one action (a pickup, an object action,
  travel, a rest) become the same typed intent its button sends
  (`game_state/extraction/free_text_actions.py`). After every turn the server
  computes story beats from the before and after player projections
  (`game_state/orchestration/story_beats.py`) and stores them on the turn, its
  log entry and the final `turn_status` event.
- After narration, `post_dm_pipeline` reads the narration for state (the
  post-narration helper `extract_post_dm_outcomes`, then
  `prepare_combat_from_dm_response` for a fight it may have started) except on
  two kinds of turn. A turn waiting on a roll is skipped as `pending_roll`. A
  turn the engine resolved before narration is skipped as `engine_resolved`:
  every declared action was named without the pre-narration helper or a
  clarification and needs no Dungeon Master resolution, every action
  validated, every change was applied with none left pending, and no fight is
  running or was started (for example a search, a pickup or travel, from a
  button or typed words). Its stored `postDmExtraction` is empty with
  `debug.source` `skipped`, and every later step (confirmed changes,
  validation, engine-played turns, defeat recovery, persistence, story beats)
  runs as before. Each skip counts in `state_pipeline.post_dm.skipped_total`,
  tagged with its `reason`.
- `aidm_server/player_rolls.py` owns player dice generation. Clients may request
  a die, mode, reason, and permitted ability selection, but faces, modifiers,
  totals, and persisted provenance are server-owned. The committed result is
  emitted as `roll_resolved`. Every roll of a turn, the engine's and the
  player's, is recorded as `storyRolls`
  (`aidm_server/game_state/orchestration/story_rolls.py`) and shown as roll
  cards whose number flicker is presentation only: they land on the server's
  values.
- Named skill checks keep their exact skill identity through the pending-roll
  lifecycle, so one related proficiency cannot qualify a different skill.
  Saving throws use the requested ability plus class-derived or explicitly
  persisted save proficiency; curated ancestry skill traits and persisted
  expertise also participate in the server-owned modifier breakdown. Known
  spellcasting classes use their class ability and proficiency instead of a
  client-selected replacement.
- A blocked turn emits `roll_required` with the pending turn ID, rule/prompt,
  remaining player IDs, and a viewer-safe roll specification. It can name the
  die, mode, reason, visibility, and public ability key/label, but never exposes
  DC or modifier provenance.
- `aidm_server/combat/legal_actions.py` derives viewer-scoped combat HUD actions
  from the persisted actor, turn index, weapons, target health, cover, zones,
  and range bands. The turn engine revalidates every submitted action/target ID
  and canonicalizes its text before rules handling; attack outcomes still flow
  through `player_rolls.py`. Action, bonus action, reaction and movement budgets
  persist in `combat.flags.turnEconomy`; every action carries its cost and, when
  unavailable, a plain reason, so validation remains visible to the player.
  Range bands, engagement, reactions and the condition engine live in
  `combat/positioning.py`, `combat/reactions.py` and `combat/modifiers.py`.
- Scene objects carry server-computed `offeredActions` for the hero the view is
  told from (`services/campaign_pack_visibility.py`), so the client offers only
  what the server would accept.
- Player snapshot projections expose only public enemy combat facts (identity,
  HP, AC, conditions, position, and an explicit visible telegraph). Server-side
  intent reasoning, targeting, planner provenance, abilities, and other
  Dungeon Master-only fields remain in the operator snapshot.
- Realtime roll and `new_message` delivery has two projections: the initiating
  socket receives private roll provenance, while the rest of the room receives
  only the shared aggregate result. Player-readable REST events use the same
  account-aware projection; persisted operator records remain complete.
- Inventory clarification actions/options are emitted only to the acting
  socket. Party peers receive a neutral waiting status, and their persisted
  event/log/export projections remove clarification and state-pipeline detail.
- Structured owned-item actions preserve the selected persisted item ID from
  the composer through validation and confirmed mutation, so identically named
  inventory entries do not silently collapse to name-only targeting.
- `aidm_server/game_state/` owns structured action/state schemas, extraction,
  validation, application, combat resolution, and state-change logging.
- Narration is persisted before post-DM state application. If both the primary
  pipeline and compatibility fallback fail, the narration remains auditable but
  the turn is marked failed. Recovery derives `none` versus `partial` from the
  persisted pre-DM applied-change lists; partial changes remain authoritative
  and must not be replayed. Structured turn advancement, clarification
  completion, and canon enqueue are blocked and the room receives
  `turn_state_apply_failed` for operator recovery.
- `Session.state_snapshot` is live runtime truth once present. Projection,
  authored-content, campaign-pack, and long-term canon tables have distinct
  responsibilities documented in [runtime state boundaries](runtime_state_boundaries.md).
- A campaign is one continuing adventure and each session is a chapter of it
  (`services/session_chapters.py`). Exactly one chapter is current; starting the
  next one carries the world forward (or starts it fresh) and closes the
  previous chapter as `ended`, readable but no longer playable.
- Session lists, the campaign workspace and lifecycle responses return
  `SessionSummary` payloads without the snapshot; the full snapshot comes only
  from the state and export endpoints. The export is one versioned save document
  (`aidm.save` version 2) built under the turn lock; import either archives it
  or restores it as the next chapter. See [API surface](api_surface.md).
- `aidm_server/canon_jobs.py` owns queued, running, and terminal canon extraction
  jobs. `canon_projection.py` and related canon modules refresh durable story
  memory and projections.

## Authored Content And Lifecycle Services

- Campaign-pack manifests are linted, forged, imported, stored, projected into
  a session snapshot, and advanced by the modules under
  `aidm_server/services/campaign_pack*.py` and the runtime schema in
  `docs/campaign_pack.schema.json`.
- Campaign-pack database records and progress events are durable authored and
  progress data; the snapshot `campaignPack` object is the runtime mirror used
  while playing.
- Shared campaign-pack progress, foreground turns, state mutations, and queued
  canon projection use one acquire-refresh-revalidate boundary. Lock discovery
  never leaves a clean pre-wait snapshot in the ORM identity map, and changed
  group membership is reacquired before a mutation begins.
- Raw `CampaignSegment` records are DM-authoring data. Player campaign
  workspaces expose only triggered segment ID/title/description fields, and the
  player room event omits the private trigger recipe.
- Campaign, player, and session archive/restore/delete behavior is implemented
  in `aidm_server/services/campaign_lifecycle.py`,
  `player_lifecycle.py`, and `session_lifecycle.py`. Session lifecycle writes
  run under the session turn coordinator; campaign lifecycle writes acquire all
  affected session fences in deterministic order before row locking and commit.
- Bestiary, creature generation, combat state, enemy planning, morale, and
  encounter resolution are owned by `aidm_server/creatures/`,
  `aidm_server/combat/`, the creature REST blueprint, and the game-state combat
  orchestration layer.

## LLM And TTS Integrations

- `aidm_server/provider_registry.py` is the configured provider/model catalog;
  `aidm_server/llm_providers.py` implements Gemini, DeepSeek, NVIDIA/Kimi,
  isolated Codex CLI, and deterministic fallback providers.
- The main narration provider and task-specific helper providers are configured
  separately. Helper routing uses task defaults, named profiles, and scoped
  environment overrides; see [LLM provider routing](llm_provider_routing.md).
- Safe Mode (`AIDM_LLM_PROVIDER=fallback`) narrates with the scripted narrator
  (`aidm_server/scripted_narrator.py`), which tells exactly what the engine
  resolved and the campaign pack authored. In Safe Mode every helper takes its
  deterministic path and canon extraction uses its heuristic patch, so no
  network or CLI model is ever called.
- The narrator's context is built by `aidm_server/llm_context.py` within a
  30,000-character budget; when it is over, low-value sections are dropped in a
  fixed order and the drop is recorded in telemetry. The turn-conductor helper
  runs only when at least two players are connected and the narrator is not the
  scripted one.
- Codex CLI execution uses a disposable isolated workspace, a constrained
  environment, and fail-closed structured-event handling. It does not expose the
  repository or host tools to model-generated commands.
- The narrator voice is optional and is exposed through the system blueprint
  when an OpenAI or Deepgram key is configured; `aidm_server/tts_openai.py` owns
  the OpenAI Realtime session (see `docs/beta_runbook.md`, "Optional TTS").

## Frontend

- `aidm_frontend/src/App.tsx` remains the top-level shell for selected
  campaign/session/player state, socket lifecycle, layout, and some dialogs.
- Two play screens share that state. The Story view (`aidm_frontend/src/story/`)
  is the default: the title screen (`TitleScreen.tsx`: continue, the ready-made
  adventure with a hero choice, new adventure), the story feed with beats, the
  composer with server-offered suggestions and the action tray, the party dock,
  the combat ribbon, the drawer (character, pack, magic, journal, map), and
  sheets for settings, account, adventures, new adventure and hero creation. It
  reads every projection with `view=player&player_id=<hero>`. Table tools (the
  classic three-column console, "Classic view" for players) is lazy-loaded from
  the Story menu, reads without `view=player` (the full projection for an
  operator), and holds the operator surfaces.
- Extracted dialogs share `aidm_frontend/src/ModalShell.tsx` and
  `aidm_frontend/src/useModalFocusTrap.ts` for dialog semantics, focus movement,
  Escape close, Tab looping, and focus return.
- API requests are centralized in `aidm_frontend/src/api.ts`; realtime behavior
  is split across the socket hooks and event-contract code under
  `aidm_frontend/src`.
- `useComposerActions.ts` retains exact in-flight turn payloads for safe recovery
  and treats only a validated `roll_resolved` event as a dice result. A
  `roll_required` response removes the rejected optimistic action, restores its
  text as a queued draft, opens the server-specified roll, and returns to the
  preserved draft after the authoritative result lands.
- Frontend operator surfaces use the server-advertised capability to hide
  campaign/session lifecycle, campaign-pack progress, director, authoring, and
  admin composer controls from players. Server-side capability checks remain
  the authority; UI gating is defense in depth and interaction clarity.
- The inspector calls compatibility `SessionState` snippets "Recent Memory";
  durable story facts remain the separate canon store.
- CSS is split by surface under `aidm_frontend/src/styles/`; responsive changes
  should preserve desktop behavior unless a change explicitly targets desktop.

## Persistence And Deployment

- SQLAlchemy models are declared in `aidm_server/models.py`; Alembic migrations
  under `migrations/` are the schema history. Local and test runs support SQLite,
  with the default local database at `~/.aidm/dnd_ai_dm.db`.
- Production configuration requires a `postgresql+psycopg` database URI,
  database-backed rate limiting and turn coordination, one threaded Socket.IO
  worker, an explicit CORS policy (exact allowlists or an intentionally empty
  same-origin policy), security headers, and configured observability ownership.
- Production schema changes are applied with migrations before startup;
  `AIDM_AUTO_CREATE_SCHEMA=true` is rejected. The current Alembic head is
  `0031_authored_map_visibility`.
- Destructive lifecycle flows are covered by tests for archive preservation,
  restore scope, force-delete cleanup, and turn-history readability after player
  deletion.
- RC source-archive inspection rejects unresolved Git LFS pointer files as
  incomplete artifacts. Hosted beta-SLO evidence is accepted only when it has a
  real target, positive DM/provider samples, and an explicit tester-expansion
  decision; these gates do not replace external deployment or operator proof.
