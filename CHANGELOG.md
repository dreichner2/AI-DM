# Changelog

All notable AIDM closed-beta changes should be recorded here.

## Unreleased

### Added

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
- Recorded the completed hosted-staging PostgreSQL 17-to-18 upgrade and its
  rollback/forward-restore evidence in the dated technology report; production
  cutover remains a separate operator decision.

### Security

- Added shared capability gates for operator combat, bestiary, campaign,
  session, map, segment, telemetry, and Socket.IO actions.
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
