# AIDM Technology Upgrade Report

Date: 2026-07-10

Status: repository implementation and local database rehearsals complete;
GitHub CI, hosted redeployment, managed PostgreSQL clone rehearsal, and the live
PostgreSQL major upgrade remain gated. No production merge or database upgrade
was performed while those gates were open.

The complete resolved dependency inventories are
`requirements.runtime.lock.txt`, `requirements-dev.lock.txt`, and
`aidm_frontend/package-lock.json`. The tables below enumerate every changed
declared/toolchain version and every resolved package whose version changed.

## Toolchain, database, CI, and operations

| Component | Before | After | Deployment state |
| --- | --- | --- | --- |
| Python | 3.12.13 locally; `.python-version` allowed 3.12 | 3.14.6 exact | Implemented locally; hosted runtime changes on deploy |
| pip | 25.0.1 on the previous hosted build; local environment already had 26.1.2 | 26.1.2 constrained and locked | Implemented |
| Node.js | Mutable major `24` (resolved locally to 24.18.0) | 24.18.0 exact, latest LTS | Implemented |
| npm | 11.16.0 in CI / 11.18.0 locally | 12.0.0 exact | Implemented locally and in CI config |
| PostgreSQL | 17.10 | 18.4 | CI/local target implemented; hosted database deliberately remains 17 pending cutover gates |
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
| `eventlet` | 0.41.0 stale local install | Removed from clean environment | Threading is the supported production async mode |
| `greenlet` | 3.5.3 transitive install | 3.5.3 explicitly locked | Required by SQLAlchemy on Linux x86-64; explicit inclusion makes the cross-platform hash lock complete |

The following direct Python dependencies were verified current and remain
unchanged: Alembic 1.18.5, Flask 3.1.3, Flask-Admin 2.2.0, flask-cors 6.0.5,
Flask-Migrate 4.1.0, Flask-SocketIO 5.6.1, Flask-SQLAlchemy 3.1.1, Gunicorn
26.0.0, pytest 9.1.1, python-dotenv 1.2.2, python-json-logger 4.1.0,
python-socketio 5.16.3, psycopg and psycopg-binary 3.3.4, pip-audit 2.10.1,
requests 2.34.2, simple-websocket 1.1.0, SQLAlchemy 2.0.51, and
websocket-client 1.9.0.

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

- A live PostgreSQL 17.10 staging snapshot was inspected at Alembic head
  `0029_players_account_fk`: 37 public tables and 74 total rows.
- PostgreSQL 18.4 `pg_dump`/`pg_restore` performed a guarded live 17-to-local-18
  custom-archive drill. The archive was mode `0600`; SHA-256 was
  `b9f752a87f989b72322b7752cf201bb7ae011bed21d9befe1d408ad4851830f5`.
- The PostgreSQL 18 restore matched every table, row count, public sequence,
  Alembic revision, invalid index, and unvalidated constraint.
- A separate PostgreSQL 17.10-native rollback archive was created from live
  staging, mode `0600`, and verified with PostgreSQL 17 tools. SHA-256 was
  `9cfe9ec44753330d1e52c73dca257fb2f724892e51138e328518c09ac452ff32`.
- The native rollback archive restored successfully into an isolated
  PostgreSQL 17.10 server: 37 tables, Alembic head intact, zero invalid indexes,
  zero unvalidated constraints, and no metadata migration drift.
- Production bootstrap and threaded Gunicorn health checks passed against both
  the PostgreSQL 18 forward restore and PostgreSQL 17 rollback restore.

See `docs/postgresql18_upgrade_runbook.md` for the provider clone, maintenance,
cutover, validation, and rollback sequence.

## Validation completed

- Clean Python 3.14.6 environment installed entirely from the hashed
  development lock; Linux CPython 3.14 wheels were also resolved successfully.
- Full backend suite under Python 3.14.6: 1,313 passed, 4 skipped.
- Frontend under Node 24.18.0/npm 12.0.0: 28 files and 208 tests passed;
  TypeScript 7 typecheck, ESLint, Vite production build, and bundle budget passed.
- `npm audit` and `npm audit --omit=dev`: zero vulnerabilities.
- PostgreSQL migration metadata checks passed on both 17.10 and 18.4 restores.
- Six approved CodeQL false positives were dismissed with documented rationale;
  the aggregate CodeQL and pull-request checks returned green before this stack
  upgrade began.

## Intentionally not upgraded or not yet applied

- Node 26 is a Current release, not LTS. Production remains on the newest Node
  24 LTS release, following Node's production guidance.
- `@types/node` intentionally tracks Node 24 rather than the numerically newest
  Node 26 types.
- TypeScript 7 is the compiler, but typescript-eslint still imports the official
  TypeScript 6 compatibility API package. Removing that bridge before upstream
  support would break linting.
- The hosted PostgreSQL instance remains on 17 until a managed clone rehearsal,
  final backup, maintenance window, and all release gates pass. An in-place
  downgrade is not possible.
- Docker is not installed on this Mac, so Prometheus/Grafana image references
  and configuration were statically validated but the upgraded images were not
  pulled and started locally.

## Remaining gates and risks

1. Run the final RC/browser/security suite on the committed tree and require
   the upgraded GitHub Actions workflows to pass.
2. Deploy the exact validated commit to hosted staging using the hash lock and
   npm 12 build command, then repeat hosted auth, WebSocket, export/import,
   security, and production-startup checks.
3. Obtain the external telemetry receipt required by the existing release
   signoff; no endpoint/key is currently configured, so this proof cannot be
   fabricated.
4. Obtain cost approval if the provider's managed PostgreSQL clone creates a
   billable resource, then complete the clone upgrade rehearsal.
5. Schedule the write-freeze/maintenance window and follow the PostgreSQL 18
   runbook. Preserve the PostgreSQL 17 rollback target through the observation
   window.
6. The database currently permits external connections from `0.0.0.0/0`.
   Restrict the provider allowlist after the operator's required access paths
   are known and verified.
