# API Surface And Contracts

This document maps the current HTTP and Socket.IO surface without duplicating
every request and response field. The implementation remains authoritative:
blueprints define routes, `aidm_server/capabilities.py` defines access policy,
and `aidm_server/api_type_contract.py` defines generated response DTOs.

## Contract Ownership

| Concern | Source of truth |
| --- | --- |
| Route methods and paths | Blueprint decorators under `aidm_server/blueprints/` and registrations in `aidm_server/main.py` |
| HTTP and Socket.IO authorization | Policy in `aidm_server/capabilities.py`; Socket.IO identity/capability resolution in `aidm_server/socket_runtime.py`; enforcement wiring in `aidm_server/blueprints/socketio_events.py` |
| Incoming Socket.IO payloads | `aidm_server/socket_contracts.py` |
| REST response DTOs | `aidm_server/api_type_contract.py` and `aidm_server/response_dtos.py` |
| Frontend response types | Generated `aidm_frontend/src/apiContract.generated.ts` |
| Browser request behavior | `aidm_frontend/src/api.ts` |

Regenerate frontend response types with `make api-types`. Validate without
writing with:

```bash
.venv/bin/python scripts/generate_api_types.py --check
```

## HTTP Route Families

The table groups related paths; consult the named blueprint for exact payloads,
status codes, and less common subroutes.

| Area | Current paths and behavior | Blueprint |
| --- | --- | --- |
| Accounts and workspaces | `/api/accounts/login`, `/api/accounts/workspace`, `/api/accounts/workspaces`, `/api/accounts/workspace/select`, `/api/accounts/me`, and `/api/accounts/session` | `accounts.py` |
| Health and capabilities | Public `GET /api/health`; actor capability discovery at `GET /api/capabilities` | `system.py` |
| Worlds | Collection create/list and item get/patch/delete under `/api/worlds` | `worlds.py` |
| Campaigns | Collection and item CRUD, archive/restore, workspace, chronicle, and canon under `/api/campaigns` | `campaigns.py` |
| Campaign packs | Lint/forge, examples, installed packs, and imports under `/api/campaigns/pack-tools`, `/api/campaigns/example-packs`, `/api/campaigns/installed-packs`, and `/api/campaigns/import-pack` | `campaigns.py` |
| Players | Campaign player collection plus player get/patch/delete, loadout repair, and equipment update under `/api/players` | `players.py` |
| Races and onboarding | Core/custom races under `/api/races` and `/api/custom-races`; pregenerated characters and play-now under `/api/onboarding` | `races.py`, `onboarding.py` |
| Sessions | Start/end, list, import/export, recap/chronicle, update, archive/restore/delete, logs, events, state, content settings, and campaign-pack progress under `/api/sessions` | `sessions.py` |
| Maps and segments | Map create/list/get/update under `/api/maps`; player reads include only `visibility=player` maps while DM authoring sees `player` and `dm`; segment CRUD and activation under `/api/segments` | `maps.py`, `segments.py` |
| Bestiary and creatures | Core, campaign, and region bestiaries plus resolve/generate/variant/evolve/balance operations under `/api/bestiary`, `/api/campaigns/*/bestiary`, and `/api/creatures` | `creatures.py` |
| Combat | Start, plan intents, morale, end checks, state changes, and debug under `/api/sessions/<session_id>/combat` | `creatures.py` |
| Runtime LLM config | `GET`, `PATCH`, or `POST /api/llm/config` | `runtime_config.py` |
| TTS and feedback | TTS config/speak/stream and coherence/bad-turn feedback under `/api/tts` and `/api/feedback` | `system.py` |
| Metrics and beta operations | Metrics, Prometheus text, beta summary/SLO/incidents/quality/audits/support bundle under `/api/metrics*` and `/api/beta/*` | `system.py` |

Private `PlayerDetail` payloads include canonical `weapon_proficiencies`
selectors such as `category:martial` and `weapon:rapier`. Character creation and
class changes derive this list on the server; request payloads cannot directly
assert it. Public party summaries omit the field, while authorized session
exports preserve it for the selected or owned character.

When combat is active, `GET /api/sessions/<session_id>/state` adds viewer-scoped
`state_snapshot.combat.legalActions` bundles. Each bundle contains stable
server-issued action IDs, the current actor, range-band-checked target options,
and a coarse action/movement cost derived from persisted combat state. A HUD
submission sends only `kind: combat`, `combat.action_id`, and optional
`combat.target_id`; the turn engine resolves those IDs again against the current
snapshot, replaces client prose with the canonical action, and rolls attacks on
the server. The contract is explicitly `turn_order_derived`: per-action,
movement, and reaction counters are not yet persisted or claimed as enforced.

## Authorization Model

Every registered API method must be in the capability inventory or the narrow
public/self-service inventory. Application construction fails when the
inventory is incomplete or refers to a missing method.

| Classification | Intended boundary |
| --- | --- |
| `public` | Health only |
| `self_service` | Account login/session and the current account's workspace membership |
| `player_read` | Player-visible campaign and session state |
| `player_action` | Normal player mutations, imports, feedback, TTS, and character actions |
| `dm_authoring` | World, campaign, pack, map, segment, and bestiary authoring |
| `dm_runtime_control` | Session lifecycle and direct mutable runtime controls |
| `debug_read` | Operational metrics, beta evidence, provider diagnostics, and debug views |
| `admin_workspace` | Workspace-wide administrative mutations |
| `local_operator_only`, `server_internal` | Reserved for local tooling or internal hooks; not a substitute for classifying an external route |

Workspace administrators receive the workspace-admin capability set. Account
members and scoped bearer credentials receive player capabilities. When auth is
disabled for an explicitly local run, the local operator path receives the
local capability set.

Player-readable session responses are also object-scoped. Session list,
workspace, state, and export projections keep full character detail only for
player records owned by the requesting account. Party peers are reduced to
public identity and bounded shared combat status. An explicit export request for
another account's player returns `player_not_found`; a workspace administrator
receives the complete inspection/export view. Accountless workspace tokens can
still read public party summaries, but direct player reads/mutations and socket
player bindings return not-found/invalid-player semantics because those tokens
own no private character.

Raw segment collection/detail routes are `dm_authoring`. A player campaign
workspace includes only already-triggered segment ID, title, description, and
triggered status; untriggered rows and trigger conditions are not a player-read
contract.

Authored maps use canonical `visibility` values `player` and `dm` (the write API
also normalizes `public`/`revealed` and `hidden`/`dm_only` aliases). Existing and
unspecified maps default to `player` for compatibility. Player map collections,
direct lookup, campaign workspace counts/cursors, and the map inspector omit
`dm` records entirely and direct lookup returns `map_not_found`; DM authors see
both states and can reveal or hide a map with `PATCH /api/maps/<map_id>`.
Authored `Map` records are not serialized in Socket.IO or the backend session-
export contract. The browser's richer JSON export includes only its defensive
viewer-filtered map list, so a stale DM cache entry cannot bypass the server
projection during an account/role transition.

Raw campaign canon plus campaign and region bestiary catalogs are `debug_read`;
the core bestiary remains `player_read`. Chronicle routes are still
`player_read`, but their HTML is viewer-aware: players receive public narration
and revealed chapter titles without progress event internals, provider/model
traces, state-pipeline notes, turn/event identifiers, or Director's Commentary.
Workspace administrators and local operators retain the full director view.

## Credential Transport

The backend accepts the configured credential through these transports:

- `Authorization: Bearer <token>`;
- `X-AIDM-Workspace-Token: <token>` for a configured workspace-token mapping;
  the mapping itself determines that token's workspace scope;
- the account-session cookie used by the frontend.

`X-AIDM-Workspace-Id` selects one of the authenticated account's saved
workspace memberships. It does not re-scope a configured workspace token.

A passwordless legacy account can set a password only with a saved account
token or an operator-issued replacement. The frontend sends a replacement only
as `Authorization: Bearer`; it never places the raw recovery code in the JSON
body or browser storage. `legacy_recovery=true` tells the successful setup path
that the official client is performing recovery; the server recognizes the
operator-issued credential itself and rotates it to a fresh session token even
if a client omits that advisory flag.

Unsafe methods authenticated by cookie also require the matching CSRF token in
`X-AIDM-CSRF-Token`. The frontend only attaches bearer, workspace, cookie, and
CSRF credentials to the configured backend origin; do not bypass that
origin-scoping in new request code.

## Error Envelope

Shared REST and Socket.IO error helpers use this shape:

```json
{
  "error": "Human-readable message",
  "error_code": "stable_machine_code",
  "details": {}
}
```

Callers should branch on `error_code`, not the prose message. A route that uses
a specialized success DTO may still return this shared error envelope.

## Socket.IO Surface

The server wires events in `aidm_server/blueprints/socketio_events.py` and
implements them in the split `aidm_server/socket_*.py` modules.

| Incoming event | Capability | Purpose |
| --- | --- | --- |
| `connect`, `disconnect` | Lifecycle authentication and cleanup | Establish or remove the socket actor |
| `join_session` | `player_read` | Join a session/player room after workspace checks |
| `leave_session` | `player_action` | Leave the active session room |
| `typing_status` | `player_action` | Broadcast bounded typing presence |
| `music_control` | `player_action` | Update synchronized session music state |
| `send_message` | `player_action` | Submit a player turn |
| `resolve_clarification` | `player_action` | Resolve a pending turn clarification |
| `set_turn_control` | `dm_runtime_control` | Change DM-controlled turn behavior |

Common server events include `error`, `player_joined`, `player_left`,
`active_players`, `new_message`, `dm_response_start`, `dm_chunk`,
`dm_response_end`, `roll_required`, `roll_resolved`, `turn_duplicate`,
`turn_status`, `session_log_update`,
`scene_state`, `segment_triggered`, `clarification_required`, `music_state`, and
`turn_control_updated`. A consumer should tolerate additive fields and unrelated
events but validate the payload for the events it handles.

Player dice are authoritative on the server. `send_message` still accepts legacy
roll payloads, but client-supplied faces, modifiers, kept values, and totals are
ignored. After the canonical result and incoming turn event commit together, the
server broadcasts one room-scoped `roll_resolved` event before narration begins.
The room projection includes the originating `client_message_id`, canonical
faces, kept value, aggregate modifier, and total. Only the initiating socket
receives ability/proficiency/wound provenance. A retry must reuse the same key;
a completed request emits `turn_duplicate` with `session_id`, `turn_id`, and
`client_message_id`, while an incomplete `processing` turn replays its persisted
private roll receipt to the requester and resumes without a second incoming
write, roll, durable roll event, or peer rebroadcast.

`clarification_required` carries the original action and inventory-derived
options only to the acting socket. The room-wide `turn_status` names the player
the table is waiting for but omits the request, action text, and options. The
same owner/admin boundary is applied when persisted event, log, and export
metadata are projected for party peers.

`segment_triggered` exposes the revealed segment ID, title, and description to
the player room. Trigger reason/spec remains in the durable operator event and
is not part of the room payload.

## Change Checklist

When changing an external contract:

1. Add or update the blueprint route and its capability classification.
2. Use the shared error envelope and a response DTO builder where applicable.
3. Update `api_type_contract.py`, run `make api-types`, and update frontend uses.
4. Add route authorization, workspace isolation, payload, and response tests.
5. For Socket.IO, update contracts, capability mapping, handler tests, and
   frontend event handling.
6. Update this route-family map if a new family or externally important event is
   introduced.
