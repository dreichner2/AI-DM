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
  `turn_narration.py`, `turn_segments.py`, and `turn_events.py`.
- `aidm_server/player_rolls.py` owns player dice generation. Clients may request
  a die, mode, reason, and permitted ability selection, but faces, modifiers,
  totals, and persisted provenance are server-owned. The committed result is
  emitted as `roll_resolved`; frontend dice physics are presentation only.
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
  through `player_rolls.py`. Action-economy labels are turn-order-derived because
  the current schema does not persist sub-turn action, movement, or reaction
  counters. The HUD renders unavailable targets as disabled choices with the
  server-issued legality reason so validation remains visible to the player.
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
- Codex CLI execution uses a disposable isolated workspace, a constrained
  environment, and fail-closed structured-event handling. It does not expose the
  repository or host tools to model-generated commands.
- Deepgram TTS is optional and is exposed through the system blueprint when its
  API key is configured.

## Frontend

- `aidm_frontend/src/App.tsx` remains the top-level shell for selected
  campaign/session/player state, socket lifecycle, layout, and some dialogs.
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
