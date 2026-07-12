# Changelog

All notable AIDM closed-beta changes should be recorded here.

## Unreleased

### Added

- Added server-authoritative player dice with persisted roll provenance and a
  room-scoped `roll_resolved` event. Advantage, disadvantage, character-sheet
  modifiers, proficiency, and wound penalties are resolved by the backend.
- Added viewer-safe `roll_required` guidance with the pending turn, public roll
  specification, and remaining player IDs. The composer can configure that
  authoritative check in one action while preserving the rejected player draft.
- Added a player combat HUD backed by viewer-scoped, server-issued action IDs,
  persisted weapon choices, current-turn gating, and range-band target checks.
  Submitted action and target IDs are revalidated and attack rolls remain
  server-generated; unpersisted sub-turn counters are labeled untracked.
- Added Play Now onboarding with pregenerated characters, bundled campaign
  selection, idempotent setup, and the authored Road of Unremembered Kings
  starting experience.
- Added campaign-pack lint, report, graph, forge, installed-library, progress,
  commentary, encounter, and bundled-example workflows.
- Added chronicle export, session content settings, richer scene state, campaign
  commentary, and player-safe narrative rendering.
- Added account/workspace sessions, hosted cookie-only authentication with CSRF
  protection, capability enforcement, operator audit records, and beta incident,
  SLO, session-quality, audit, and support-bundle surfaces.
- Added durable canon jobs, fenced database-backed turn coordination, state
  mutation auditing, and a checked inventory of direct session snapshot writers.
- Added the isolated Codex CLI provider, including fail-closed tool-event
  handling, dedicated authentication, model/reasoning routing, and production
  readiness checks.
- Added fixed-input helper-profile and enemy-tactics compiler comparison tools.
- Added SQLite and guarded PostgreSQL backup/restore drills, migration-chain
  rehearsal, deployment-readiness checks, hosted security smokes, scenario
  regressions, and release-evidence generation.
- Added an operator-only legacy recovery-code command that rotates a
  passwordless account to a high-entropy credential stored only as a hash.

### Changed

- Changed the dice composer to submit request-only roll intents. The client now
  animates the committed server result, exposes distinct requesting, rolling,
  resolved, and recoverable-failure states, and removes manual modifier and
  proficiency overrides from player controls.
- Changed the combat HUD to retain unavailable server-issued targets, disable
  them, and show the engine's legality reason instead of silently hiding them.
- Changed player navigation and panels to omit campaign, session, pack,
  director, and admin mutation controls unless the current actor has operator
  capability. Player-safe read, export, and share actions remain available.
- Renamed frontend memory-snippet presentation from "Canon Facts" to "Recent
  Memory" so a compatibility summary is not presented as durable canon truth.
- Docked scene music by default so it cannot cover pending rolls or narration,
  while preserving accessible full floating controls on demand and saved custom
  layouts. Compact desktop actions and scrollable full-label mobile inspector
  tabs prevent header and navigation collisions.
- Added a visible Details disclosure for truncated runtime notices, strengthened
  visual smoke with overlap/mobile-tab assertions, and kept raw Bestiary tools
  out of non-operator navigation.
- Changed turn retry and Socket.IO reconnect handling so an uncertain request
  keeps its original payload and `client_message_id`, duplicate acknowledgments
  reconcile optimistic UI, incomplete committed turns resume from their durable
  pre-narration state, and every rejoin refreshes persisted session state.
- Changed attack checks to derive melee, ranged, or finesse ability choice from
  persisted equipped/named weapons. A migrated, private player proficiency
  profile now supplies server-authored weapon/category selectors; migration
  converts legacy per-item assertions, and roll or inventory requests cannot
  override the persisted proficiency bonus.
- Moved hosted Socket.IO production to one Gunicorn `gthread` worker with
  `simple-websocket`; unsupported multi-worker topologies now fail production
  validation.
- Split Socket.IO presence, music, typing, clarification, message, and turn
  control responsibilities into focused modules, and extracted turn action,
  roll, narration, and segment policies from the main turn engine.
- Split frontend runtime settings, onboarding, workspace queries, session
  actions, dialogs, director panels, and test harnesses out of the main app
  component.
- Centralized provider/model metadata in the provider registry and routed the
  Codex CLI default through GPT-5.6 Sol Medium.
- Upgraded and hash-locked the supported Python, Node/npm, frontend, PostgreSQL,
  CI Action, Prometheus, Grafana, and Mermaid toolchains recorded in the
  technology upgrade report.
- Reduced the shipped exploration soundtrack bitrate while preserving its full
  duration, and resized oversized profile portraits to a still-retina 384-pixel
  source size, removing roughly 66 MiB from static assets without changing
  public asset paths.
- Recorded the completed hosted-staging PostgreSQL 17-to-18 upgrade and its
  rollback/forward-restore evidence in the dated technology report; production
  cutover remains a separate operator decision.

### Security

- Added first-class `player`/`dm` visibility for authored maps. Player map
  lists, details, campaign workspaces, and the map inspector now exclude
  DM-only records; browser JSON exports reuse that viewer-filtered map list,
  while backend session exports and realtime contracts do not serialize
  authored map rows. Administrators can reveal or hide a map without changing
  its content, and existing maps remain player-visible.
- Redacted other players' character sheets, statistics, inventory, spells,
  resources, abilities, armor details, and metadata from non-admin session
  snapshots and exports. A player keeps their own full character data plus
  bounded party identity and combat-status fields; selecting another player's
  export now returns `404` while administrators retain complete inspection.
- Split realtime and persisted roll projections so the acting player and
  administrators retain detailed provenance while party peers receive only the
  shared die, faces, kept value, aggregate modifier, and total. Peer log, event,
  and export copies are redacted through the same boundary.
- Restricted raw campaign-segment routes to DM authoring. Player workspaces show
  only triggered segment story fields, and room events omit keyword/state
  trigger recipes retained in durable operator records.
- Closed accountless table-token player impersonation across direct player
  routes and Socket.IO binding. Shared tokens retain public party summaries but
  cannot select a private character or receive sender-private roll provenance.
- Restricted raw canon and campaign/region bestiary catalogs to operator debug
  access. Player Chronicle exports keep public prose and revealed chapter titles
  while removing progress recipes, runtime traces, and director-only metadata.
- Made inventory clarification payloads owner-only in realtime and persisted
  projections; peers now receive only a neutral waiting status.
- Added shared capability gates for operator combat, bestiary, campaign,
  session, map, segment, telemetry, and Socket.IO actions.
- Archived or deleted sessions and campaigns now reject room joins, turns,
  clarification resolution, and turn-control changes with stable lifecycle
  errors. Lifecycle mutations are serialized against active turns and commit
  under the same fenced coordination boundary.
- Player campaign-pack projections now keep `hiddenToPlayers` checkpoints and
  their IDs out of active/completed/skipped/failed fields unless an author has
  supplied an explicit player-safe title or summary.
- Hardened passwordless legacy-account claims, workspace-password joins,
  pre-auth rate limiting, account-token handling, cookie/CSRF behavior, and
  browser credential retention.
- Prevented forged player attribution during session import and restricted
  hidden/operator state in player-owned imports and responses.
- Restricted external telemetry to validated, privacy-safe pre-auth target
  denial events and removed credential and exception details from delivery and
  database logs.
- Hardened TTS sanitization, Codex subprocess isolation, source-archive link
  inspection, committed-secret detection/redaction, and non-admin negative
  coverage.

### Fixed

- Fixed client-supplied roll faces, modifiers, kept values, totals, and
  natural-language roll claims being able to influence authoritative gameplay.
- Fixed legacy d4 through d100 result syntax and modifier/total forms bypassing
  canonicalization; meaningful action text surrounding a claimed result is now
  preserved.
- Fixed legacy pending checks without a stored roll specification accepting a
  client-selected die or advantage mode; they now use persisted server defaults.
- Fixed turns stranded in `processing` after their incoming commit by allowing
  an exact-key retry to resume narration without another roll, player event, or
  pre-DM state application.
- Fixed a post-narration failure in both state-application paths being treated
  as a completed turn. The saved narration is retained, the turn fails with
  recovery metadata, structured turn advancement and canon enqueue are blocked,
  and the room receives `turn_state_apply_failed`. Recovery now distinguishes
  no committed mechanics from partial pre-DM mechanics, preserves the applied
  count and audit evidence, and warns operators not to replay changes that
  already committed.
- Fixed hosted beta-SLO evidence with zero DM samples, no positive provider/model
  rows, or an unset tester-expansion decision being accepted as release proof.
- Fixed release archives containing unresolved Git LFS pointer files being
  accepted as complete source artifacts; RC archive evidence now fails and
  reports the pointer path and expected object metadata.
- Fixed candidate checklist reports that could present passed RC rows from an
  older commit without a prominent stale or dirty-worktree warning, or treat an
  unsafe short/mismatched signoff commit as current.
- Fixed SQLite migration `0029` so populated databases can rebuild the
  `players` table without breaking live child-table foreign keys, including a
  populated upgrade/downgrade regression.
- Removed name-only legacy account recovery, made issued recovery codes rotate
  after use, and isolated workspace-password target limits per authenticated
  account so one principal cannot consume another principal's cross-IP target
  bucket; shared-source IP limits remain intentional.
- Made provider diagnostic `--help` and invalid-argument handling complete
  before environment loading or live provider/API calls.
- Removed the internal Bleakmoor pack-lint warning by declaring its existing
  self-rejoin checkpoint behavior explicitly.
- Fixed player campaign visibility after workspace/account state changes.
- Fixed PostgreSQL migration and ORM startup portability, Linux dependency-lock
  completeness, and Node provisioning in the full backend CI suite.
- Stabilized hosted cookie WebSocket, browser, visual, asynchronous TTS,
  pending-roll, and release-evidence tests.
- Corrected generated API contract nullability and kept generated TypeScript
  contracts under CI drift checks.

### Documentation and release workflow

- Added repository issue templates, the pull-request checklist, and the
  explicit licensing notice.
- Added the closed-beta release checklist, auth matrix, production and beta
  runbooks, tester onboarding, SLO template, worker-model decision, PostgreSQL
  upgrade runbook, and technology upgrade report.
- Added RC issue evidence, source archives and checksums, release packets,
  GitHub Actions evidence, hosted proof collection, external-proof validation,
  and final operator signoff tooling.
- Updated migration and release documentation for current Alembic head
  `0031_authored_map_visibility`; hosted backup and cutover proof remains an
  external operator requirement.
