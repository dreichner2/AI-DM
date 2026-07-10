# AIDM Technology Upgrade Report

Date: 2026-07-10

Status: repository implementation, GitHub CI/CodeQL, full RC, hash-locked hosted
redeployment, and the managed staging PostgreSQL 17-to-18 upgrade are complete.
The staging cutover was performed under maintenance mode only after two fresh,
verified rollback/forward backups were created. The upgrade is merged to
`main`; production release signoff remains separate from this staging
validation.

The complete resolved dependency inventories are
`requirements.runtime.lock.txt`, `requirements-dev.lock.txt`, and
`aidm_frontend/package-lock.json`. The tables below enumerate every changed
declared/toolchain version and every resolved package whose version changed.

## Toolchain, database, CI, and operations

| Component | Before | After | Deployment state |
| --- | --- | --- | --- |
| Python | 3.12.13 locally; `.python-version` allowed 3.12 | 3.14.6 exact | Live locally, in CI, and on hosted staging |
| pip | 25.0.1 on the previous hosted build; local environment already had 26.1.2 | 26.1.2 constrained and locked | Implemented |
| Node.js | Mutable major `24` (resolved locally to 24.18.0) | 24.18.0 exact, latest LTS | Implemented |
| npm | 11.16.0 in CI / 11.18.0 locally | 12.0.0 exact | Live locally, in CI, and on hosted staging |
| PostgreSQL | 17.10 | 18.4 | Live in CI, local rehearsals, and managed hosted staging |
| PostgreSQL CI image | Mutable `postgres:17` | `postgres:18.4-bookworm` pinned by multi-architecture digest | Implemented |
| Ubuntu Actions runner | Mutable `ubuntu-latest` | `ubuntu-24.04` | Implemented |
| `actions/checkout` | Mutable `v6` alias, effective 6.0.3 | 7.0.0 at immutable commit SHA | Implemented |
| `actions/setup-python` | Mutable `v6` alias, effective 6.3.0 | 6.3.0 at immutable commit SHA | Implemented |
| `actions/setup-node` | Mutable `v6` alias, effective 6.4.0 | 6.4.0 at immutable commit SHA | Implemented |
| `actions/upload-artifact` | Mutable `v7` alias, effective 7.0.1 | 7.0.1 at immutable commit SHA | Implemented |
| Prometheus | Mutable `latest` | 3.13.1 plus immutable image digest | Implemented |
| Grafana | Mutable `latest` | 13.1.0 plus immutable image digest | Implemented |
| Mermaid documentation runtime | Mutable Mermaid 10 major (resolved as 10.9.6) | 11.16.0 exact | Implemented |
| Render CLI | 2.21.0 | 2.21.0, already latest | Unchanged |
| OpenAI Codex CLI in Render build | 0.144.1 | 0.144.1, already latest stable | Unchanged |

## Python dependency changes

| Package | Before | After | Note |
| --- | --- | --- | --- |
| `google-genai` | 2.10.0 | 2.11.0 | Runtime upgrade |
| `ruff` | 0.15.20 | 0.15.21 | Development tool upgrade |
| `pip-tools` | Not declared | 7.5.3 | Generates deterministic hash locks |
| `Flask-Migrate` | 4.1.0, development declaration only | 4.1.0, runtime declaration | Moved into the runtime dependency set so deployment migrations do not depend on development packages |
| `appdirs` | 1.4.4 | Removed | No repository import or runtime use |
| `pywin32` | Unpinned Windows-only declaration | Removed | No repository import or runtime use |
| `cffi` | 2.0.0 | 2.1.0 | Resolved transitive upgrade |
| `charset-normalizer` | 3.4.7 | 3.4.9 | Resolved transitive upgrade |
| `filelock` | 3.29.4 | 3.29.7 | Resolved transitive upgrade |
| `google-auth` | 2.55.1 | 2.55.2 | Resolved transitive upgrade |
| `pyasn1` | 0.6.3 | 0.6.4 | Resolved transitive upgrade |
| `typing-extensions` | 4.15.0 | 4.16.0 | Resolved transitive upgrade |
| `websockets` | 15.0.1 | 16.1 | Resolved transitive upgrade |
| `build` | Not present | 1.5.1 | Development-lock dependency of pip-tools |
| `pyproject-hooks` | Not present | 1.2.0 | Development-lock dependency of pip-tools |
| `setuptools` | Not present in the old environment inventory | 83.0.0 | Explicitly locked build tool |
| `wheel` | Not present in the old environment inventory | 0.47.0 | Explicitly locked build tool |
| `dnspython` | 2.8.0 stale local install | Removed from clean environment | No declared dependency after threading-only Socket.IO |
| `eventlet` | 0.41.0 declared runtime dependency and constraint | Removed | Threading is the supported production Socket.IO async mode |
| `greenlet` | 3.5.3 transitive install | 3.5.3 explicitly locked | Required by SQLAlchemy on Linux x86-64; explicit inclusion makes the cross-platform hash lock complete |

The following direct Python dependencies were verified current and remain
unchanged: Alembic 1.18.5, Flask 3.1.3, Flask-Admin 2.2.0, flask-cors 6.0.5,
Flask-SocketIO 5.6.1, Flask-SQLAlchemy 3.1.1, Gunicorn 26.0.0, pytest 9.1.1,
python-dotenv 1.2.2, python-json-logger 4.1.0, python-socketio 5.16.3,
psycopg and psycopg-binary 3.3.4, pip-audit 2.10.1, requests 2.34.2,
simple-websocket 1.1.0, SQLAlchemy 2.0.51, and websocket-client 1.9.0.

## Frontend dependency changes

| Package | Before | After | Note |
| --- | --- | --- | --- |
| React declaration | `^19.2.6` (lock 19.2.7) | `^19.2.7` (lock 19.2.7) | Declared floor aligned with installed stable release |
| React DOM declaration | `^19.2.6` (lock 19.2.7) | `^19.2.7` (lock 19.2.7) | Declared floor aligned with installed stable release |
| `lucide-react` | 1.22.0 | 1.24.0 | Runtime upgrade |
| `three` | 0.185.0 | 0.185.1 | Runtime upgrade |
| `@types/three` | 0.185.0 | 0.185.1 | Type upgrade |
| `@types/node` | 26.0.1 | 24.13.3 | Intentionally aligned with production Node 24 LTS |
| TypeScript compiler | 6.0.3 | 7.0.2 | Stable compiler upgrade |
| TypeScript compiler API bridge | Not present | `@typescript/typescript6` 6.0.3 | Required by typescript-eslint until it supports the TypeScript 7 API |
| `typescript-eslint` and all `@typescript-eslint/*` packages | 8.62.0 | 8.63.0 | Tooling upgrade |
| Vite | 8.1.0 | 8.1.4 | Build-tool upgrade |
| Vitest and all `@vitest/*` packages | 4.1.9 | 4.1.10 | Test-tool upgrade |
| Rolldown and all `@rolldown/binding-*` packages | 1.1.3 | 1.1.5 | Resolved build transitive upgrade |
| `@oxc-project/types` | 0.137.0 | 0.139.0 | Resolved build transitive upgrade |
| `postcss` | 8.5.15 | 8.5.16 | Resolved build transitive upgrade |
| `picomatch` | 4.0.4 | 4.0.5 | Resolved build transitive upgrade |
| `nanoid` | 3.3.12 | 3.3.15 | Resolved build transitive upgrade |
| `undici-types` | 8.3.0 | 7.18.2 | Follows the intentional Node 24 type alignment |
| `@typescript/typescript-*` platform packages | Not present | 7.0.2 | Native TypeScript 7 compiler packages for supported platforms |

The following direct frontend dependencies were verified current and remain
unchanged: cannon-es 0.20.0, socket.io-client 4.8.3, `@eslint/js` 10.0.1,
ESLint 10.6.0, Playwright 1.61.1, `@testing-library/jest-dom` 6.9.1,
`@testing-library/react` 16.3.2, `@types/react` 19.2.17,
`@types/react-dom` 19.2.3, `@vitejs/plugin-react` 6.0.3,
`eslint-plugin-react-hooks` 7.1.1, `eslint-plugin-react-refresh` 0.5.3,
globals 17.7.0, and jsdom 29.1.1.

## Implementation changes

- Added complete, hash-verified Python runtime and development lockfiles and a
  reproducible `make lock` workflow.
- Enforced Python 3.14.6, Node 24.18.0, and npm 12.0.0 in Make targets,
  production startup, local/desktop launchers, RC evidence, and CI.
- Moved Flask-Migrate 4.1.0 from the development-only declaration into the
  runtime dependency set used by deployment migration commands.
- Closed two clean-Linux-runner portability gaps: `greenlet` 3.5.3 is now an
  explicit cross-platform runtime lock entry, and the backend CI job provisions
  and verifies Node 24.18.0/npm 12.0.0 before running the full pytest suite.
- Updated npm's lockfile and explicitly approved only the two required macOS
  `fsevents` install scripts.
- Added PostgreSQL connection liveness checks with SQLAlchemy `pool_pre_ping`.
- Added a credential-safe PostgreSQL custom-archive drill with exact source,
  target, and client major-version guards, read-only exported snapshots,
  twice-checked empty targets, checksum/list validation, transactional restore,
  and structural/data comparison evidence.
- Pinned Actions and container images immutably and added Dependabot coverage
  for GitHub Actions and Docker images.
- Updated local, CI, deployment, operations, and upgrade documentation.

## Database backup, migration, and rollback evidence

- The frozen live PostgreSQL 17.10 staging snapshot was inspected at Alembic
  head `0029_players_account_fk`: 37 public tables and 57 total rows.
- PostgreSQL 18.4 `pg_dump`/`pg_restore` performed a guarded frozen-state
  17-to-local-18 custom-archive drill. The archive was mode `0600`; SHA-256 was
  `807918496f9d5e946c049eb98b2dd0cf56b8ebc61281c99fab9ff061b568a387`.
- The PostgreSQL 18 restore matched every table, row count, public sequence,
  Alembic revision, invalid index, and unvalidated constraint.
- A separate PostgreSQL 17.10-native rollback archive was created during the
  same write freeze, mode `0600`, and verified with PostgreSQL 17 tools.
  SHA-256 was
  `0fe338ada7896a659d2e8c23acfa22e1b84d89227fefcdef6f219b98f49156cc`.
- The native rollback archive restored successfully into an isolated
  PostgreSQL 17.10 server: 37 tables, Alembic head intact, zero invalid indexes,
  zero unvalidated constraints, and no metadata migration drift.
- Production bootstrap and threaded Gunicorn health checks passed against both
  the PostgreSQL 18 forward restore and PostgreSQL 17 rollback restore.
- Render completed the managed in-place upgrade successfully. A fresh
  post-upgrade PostgreSQL 18 archive restored into another PostgreSQL 18.4
  target with SHA-256
  `5dd8d7c3330985bc8c6de26631e802169600fac3cc948ed69fcaca4970ddf725`.
- Pre- and post-upgrade tables, row counts, sequences, extensions, Alembic
  revision, public-object count, invalid indexes, and unvalidated constraints
  matched exactly. PostgreSQL statistics were refreshed with `ANALYZE`.

The final archives, JSON/Markdown drill evidence, post-upgrade archive, and
hosted smoke evidence are retained in a mode-`0700` off-repository operator
backup directory; each file is mode `0600` and every archive passed
`pg_restore --list`. Retain this set and the verified local PostgreSQL 17
rollback target through the observation window. A production cutover still
requires a fresh encrypted/off-site backup set as required by the runbook.

See `docs/postgresql18_upgrade_runbook.md` for the provider clone, maintenance,
cutover, validation, and rollback sequence.

## Validation completed

- Clean Python 3.14.6 environment installed entirely from the hashed
  development lock; Linux CPython 3.14 wheels were also resolved successfully.
- Full backend suite under Python 3.14.6: 1,314 passed, 4 skipped.
- Frontend under Node 24.18.0/npm 12.0.0: 28 files and 208 tests passed;
  TypeScript 7 typecheck, ESLint, Vite production build, and bundle budget passed.
- `npm audit` and `npm audit --omit=dev`: zero vulnerabilities.
- PostgreSQL migration metadata checks passed on both 17.10 and 18.4 restores.
- A committed-tree `make closed-beta-rc` run at `f68a3dd` completed all 28
  gates, including migration and backup/restore drills, secret and dependency
  audits, production-startup, scenario, concurrency, cookie-auth, forbidden,
  export/import, observability, frontend, browser, and visual checks.
- Clean Linux CI exposed and drove fixes for two portability gaps: SQLAlchemy's
  platform-dependent `greenlet` runtime requirement and the full backend test
  job's dependency on the exact Node/npm toolchain.
- GitHub Actions run `29086953693` passed backend, frontend/browser, and
  PostgreSQL 18.4 integration on `6557994`; CodeQL run `29086952082` passed for
  Actions, Python, and JavaScript/TypeScript with zero open PR-ref alerts.
- Closed Beta RC run `29087222415` passed all 28 gates on `6557994`: 1,314
  backend tests plus 4 skips, 208 frontend tests, browser E2E, visual review,
  migrations, security/audits, backup/restore, and production startup. Its
  `closed-beta-rc-evidence` artifact ID is `8225386642`.
- Render deploy `dep-d98cpr3tqb8s73fejvcg` ran the hash-locked Python install,
  exact npm 12 assertion, TypeScript 7/Vite build, and production Gunicorn
  startup at `6557994`. Live runtime probes reported Python 3.14.6, pip 26.1.2,
  Node 24.18.0, and npm 12.0.0.
- Before and after the managed PostgreSQL 18 cutover, hosted readiness,
  cookie/CSRF/WebSocket auth, non-admin forbidden behavior, session
  export/import, beta SLO, metadata drift, restart, and health checks passed.
- Six approved CodeQL false positives were dismissed with documented rationale;
  the aggregate CodeQL and pull-request checks remained green after the stack
  upgrade.

## Intentionally not upgraded or not yet applied

- Node 26 is a Current release, not LTS. Production remains on the newest Node
  24 LTS release, following Node's production guidance.
- `@types/node` intentionally tracks Node 24 rather than the numerically newest
  Node 26 types. Dependabot continues to offer Node 24 minor/patch updates but
  ignores type-definition major updates until the production runtime moves.
- `pydantic-core` 2.47.0 is intentionally not installed independently because
  the newest stable Pydantic, 2.13.4, requires `pydantic-core==2.46.4` exactly.
  All hash-locked CI install jobs correctly rejected that isolated update, so
  Dependabot ignores newer standalone core updates until a matching stable
  Pydantic release is available and the pair can move together.
- TypeScript 7 is the compiler, but typescript-eslint still imports the official
  TypeScript 6 compatibility API package. Removing that bridge before upstream
  support would break linting.
- Render's recommended managed clone was intentionally not created because the
  dashboard states that clones are billed as a separate database. Instead, the
  staging cutover used repeated local PostgreSQL 18 forward rehearsals, two
  PostgreSQL 17-native rollback restores, a final maintenance-mode write freeze,
  and exact pre/post data comparison. An in-place downgrade remains impossible.
- Docker is not installed on this Mac, so Prometheus/Grafana image references
  and configuration were statically validated but the upgraded images were not
  pulled and started locally.
- Browser/visual smoke can log a false WebSocket HTTP 500 when Werkzeug's
  development server tears down a healthy upgraded socket. Production uses
  Gunicorn `gthread`, and forced production WebSocket checks pass. The harnesses
  now explicitly disable debug/reloader mode to remove leaked-semaphore noise;
  no production dependency was rolled back for this upstream development-server
  issue.

## Remaining gates and risks

1. Obtain the external telemetry receipt required by the existing release
   signoff; no endpoint/key is currently configured, so this proof cannot be
   fabricated.
2. Obtain authentication/security-owner and release-owner signoff for the two
   open Low/P3 target-lockout acceptances,
   `preauth-target-lockout-legacy-claim` and
   `preauth-target-lockout-workspace-password`. Neither finding is fixed or
   closed; the acceptance expires on 2026-08-10 or earlier if exposure expands.
3. Preserve the PostgreSQL 17 rollback target and all three verified archives
   through the PostgreSQL 18 observation window. A production database upgrade
   still requires its own fresh backups and maintenance/cutover approval.
4. The database currently permits external connections from `0.0.0.0/0`.
   Restrict the provider allowlist after the operator's required access paths
   are known and verified.
