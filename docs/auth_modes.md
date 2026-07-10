# AIDM Auth Mode Matrix

Use this matrix when choosing runtime settings for local development, private
testing, Tailscale exposure, or hosted closed beta. When a mode is exposed beyond
loopback, prefer the stricter setting if there is any uncertainty.

| Mode | Intended exposure | Required auth posture | Token/cookie storage | CORS posture | Notes |
| --- | --- | --- | --- | --- | --- |
| Local loopback development | `127.0.0.1` or `localhost` only | `AIDM_AUTH_REQUIRED=false` is acceptable for solo dev. | Browser session/local storage is acceptable for local account convenience. | Localhost-only or wildcard during isolated dev. | Do not reuse this mode for public tunnels. |
| Private LAN testing | Trusted private network only | `AIDM_AUTH_REQUIRED=true`; configure `AIDM_API_AUTH_TOKENS` or workspace token mappings. | Bearer tokens are acceptable for private/manual testing. | Explicit LAN origin allowlists. | Treat as temporary; use real accounts for meaningful beta play. |
| Tailscale private beta | Tailnet users, optionally Funnel when intentionally exposed | `AIDM_AUTH_REQUIRED=true`; non-loopback exposure must not run auth-disabled. | Prefer account login; bearer tokens are acceptable for operator checks. | Explicit Funnel or tailnet origins. | Verify `/api/health` shows auth required before sharing links. |
| Hosted same-origin closed beta | Public HTTPS app and API on one origin | `AIDM_AUTH_REQUIRED=true`; strong API/admin tokens; account auth enabled. | `AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=true`, `AIDM_ACCOUNT_COOKIE_SECURE=true`, `AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false`. | Empty same-origin CORS or exact hosted origin only. | Unsafe REST writes must send `X-AIDM-CSRF-Token` from the companion `aidm_csrf_token` cookie. |
| Hosted cross-origin closed beta | Public HTTPS frontend and API on different origins | `AIDM_AUTH_REQUIRED=true`; strong API/admin tokens; document why cross-origin is needed. | Prefer HTTP-only cookie auth only when cookie domain/SameSite rules are proven; otherwise document bearer-token exception. | Exact frontend/API origins only; no wildcard. | Run deployment-readiness with the documented exception flags when applicable. |
| API/operator automation | CLI, CI, or admin-only scripts | `AIDM_AUTH_REQUIRED=true`; scoped token or workspace token mapping. | Bearer token from secret manager, not browser storage. | Not browser-facing unless explicitly needed. | Keep operator capabilities narrower than normal player/session actions. |

## Capability Enforcement Matrix

`aidm_server/capabilities.py` is the executable source of truth. The REST guard
uses Flask endpoint names plus HTTP methods, and the Socket.IO guard uses event
names. Application construction fails when any `/api` method is unclassified or
when the inventory contains a stale method; tests also cover the application
Socket.IO event inventory.

| Surface | Operations | Required capability |
| --- | --- | --- |
| Worlds | Create, update, delete | `dm_authoring` |
| Campaigns | Create, update, archive, restore, delete | `dm_authoring` |
| Campaign packs | Lint, forge, import, list/get installed packs | `dm_authoring` |
| Sessions | Start, end, rename, archive, restore, delete | `dm_runtime_control` |
| Session imports | Import player-owned saved data; hidden operator state is stripped | `player_action` |
| Session controls | Update content settings or campaign-pack progress | `dm_runtime_control` |
| Director commentary | Read hidden campaign-pack commentary | `debug_read` |
| Maps | Create or update | `dm_authoring` |
| Segments | Create, update, delete | `dm_authoring` |
| Segments | Activate for the live session | `dm_runtime_control` |
| Bestiary | Create entries or generate-and-save packs | `dm_authoring` |
| Combat | Start, plan, mutate, end, or inspect debug state | `dm_runtime_control` |
| Telemetry | Read JSON, Prometheus, beta summary, or beta SLO metrics | `debug_read` |
| Beta operations | Read incidents, session quality, audits, or support bundles | `debug_read` |
| Socket.IO | Join/read a session | `player_read` |
| Socket.IO | Send turns, typing/music updates, leave, resolve clarification | `player_action` |
| Socket.IO | Change turn-control mode or active player | `dm_runtime_control` |

Workspace-admin accounts and unscoped bootstrap/operator tokens listed only in
`AIDM_API_AUTH_TOKENS` receive DM authoring, runtime-control, debug, and
workspace-administration capabilities. Operator tokens do not receive
`local_operator_only`. Player accounts, dynamic workspace tokens, and tokens
listed in `AIDM_API_AUTH_TOKEN_WORKSPACES` receive only player read/action
capabilities; a workspace mapping takes precedence if a token is present in
both settings. When authentication is disabled, a credential-free local
request keeps the full local-operator capability set.

The frontend namespaces stored account/workspace credentials by configured
backend origin and attaches them only to that origin. Cookie-authenticated
unsafe REST calls copy the companion CSRF cookie into `X-AIDM-CSRF-Token`.
Changing this origin-scoping is an authentication-boundary change, not a
general fetch refactor.

All `/api/accounts/*` requests pass through the general API limiter. Account
login, invalid legacy-recovery attempts, workspace-password joins, and
workspace-token checks additionally consume opaque HMAC pre-auth buckets: 5
attempts per IP+target and 20 attempts per IP or target over the default
60-second window. The IP-wide bucket blocks target rotation. Production
persists all limiter types through `AIDM_RATE_LIMIT_STORE=database`. Socket
admin-mode messages pass through the per-player/session Socket.IO limiter
before the admin-passcode comparison.

Passwordless legacy accounts require a saved account token or a high-entropy
replacement issued with `scripts/issue_legacy_recovery_code.py`; first/last
names are no longer authority to claim an account. A valid recovery code
bypasses invalid weak-claim saturation and is rotated after password setup.
The operator must verify the requester out of band before issuing it, and the
raw code must be delivered privately because only its hash is stored.

For workspace-password joins, only the target-wide bucket is scoped to the
authenticated account plus canonical workspace. IP+workspace and IP-wide
buckets remain unchanged. One account can exhaust its own cross-IP allowance
but cannot consume a different account's cross-IP target allowance. A correct
join from the same saturated source IP can still return 429 because the shared
IP+workspace and IP-wide protections intentionally remain active. Account
rotation can distribute guessing across more principals; keep signup exposure
and privacy-safe `workspace-password` target telemetry under review before
public exposure. Saved workspace membership continues to use the separate
selection path.

## Baseline Env By Exposure

The production snippets below are exposure-specific overlays, not complete
standalone environments. Start from `.env.production.example`, replace every
placeholder in the deployment provider's secret/env manager, and then apply the
matching auth/CORS choices below. Production startup also requires the explicit
PostgreSQL, migration, database-backed rate-limit/turn-coordinator,
single-worker Socket.IO, security-header, and observability settings documented
in `docs/production-readiness.md`.

### Loopback-only development

```bash
AIDM_ENV=development
AIDM_AUTH_REQUIRED=false
AIDM_CORS_ALLOWLIST=http://127.0.0.1:5173,http://localhost:5173
AIDM_SOCKET_CORS_ALLOWLIST=http://127.0.0.1:5173,http://localhost:5173
```

### Tailscale or LAN closed beta

Apply these values on top of the production template. A Tailscale Funnel is
public exposure, not tailnet-only exposure, so use the exact public UI origin
when Funnel is enabled.

```bash
AIDM_ENV=production
AIDM_AUTH_REQUIRED=true
AIDM_API_AUTH_TOKENS=<strong-token>
AIDM_CORS_ALLOWLIST=<exact-ui-origin>
AIDM_SOCKET_CORS_ALLOWLIST=<exact-ui-origin>
AIDM_SECURITY_HEADERS_ENABLED=true
```

### Hosted same-origin closed beta

Apply these values on top of the production template. Empty CORS allowlists are
valid only when the browser UI and API are intentionally served from the same
origin; otherwise set both allowlists to the exact UI origin.

```bash
AIDM_ENV=production
AIDM_AUTH_REQUIRED=true
AIDM_API_AUTH_TOKENS=<strong-operator-token>
AIDM_AUTO_CREATE_SCHEMA=false
AIDM_RATE_LIMIT_STORE=database
AIDM_TURN_COORDINATOR_STORE=database
AIDM_SOCKETIO_WORKER_MODEL=single
AIDM_SECURITY_HEADERS_ENABLED=true
AIDM_ACCOUNT_COOKIE_AUTH_ENABLED=true
AIDM_ACCOUNT_COOKIE_SECURE=true
AIDM_ACCOUNT_TOKEN_RESPONSE_ENABLED=false
```

## Required Proof Before Wider Beta

- `GET /api/health` reports the expected environment, auth-required state, and
  provider/model.
- `scripts/deployment_readiness_check.py` passes against the target env and URL.
- `make hosted-cookie-auth-smoke` proves cookie-only login, CSRF on unsafe
  writes, logout cleanup, workspace role refresh, and Socket.IO auth in an
  isolated local hosted-mode runtime, and can write
  `tmp/release/hosted-cookie-auth-evidence.md` with `--evidence-report`.
  Run a browser smoke against the real hosted URL before inviting external testers.
- For hosted/staging proof, run
  `make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_SMOKE_ARGS="--target-url <target-url> --account-intent signup --evidence-report tmp/release/hosted-cookie-auth-evidence.md"`.
  Use `--account-intent login --username <user> --password <pass>` when the
  target requires a pre-provisioned test account.
- Any bearer-token browser exception is documented with a reason and reviewed
  before testers are invited.
