# AI-DM Tabletop Console

Canonical local frontend for the AI-DM Flask backend.

## Run

From the repo root, start the backend:

```bash
./scripts/run_local_backend.sh
```

Then run the frontend:

```bash
cd aidm_frontend
npm ci
npm run dev -- --host 127.0.0.1
```

Open:

```text
http://127.0.0.1:5173/
```

The default backend URL is blank, which means same-origin. In dev, Vite proxies
`/api/*` and `/socket.io/*` to `http://127.0.0.1:5050`. Override that proxy with
`VITE_AIDM_PROXY_TARGET` when the backend runs somewhere else:

```bash
VITE_AIDM_PROXY_TARGET=http://127.0.0.1:5050 npm run dev -- --host 127.0.0.1
```

For a one-link playtest, serve the built frontend from Flask instead of running
Vite separately:

```bash
cd ..
make unified
```

When using the unified server, leave Backend Settings' Backend URL blank. Share
links then only need the public app URL plus `campaign` and `session`; players do
not need to paste a separate backend URL.

## Checks

```bash
npm test
npm run lint
npm run typecheck
npm run build
npm run bundle:budget
npm audit --omit=dev
```

## Notes

- The client reads campaigns, sessions, players, maps, player-safe triggered
  segments, session state, logs, worlds, turn events, and permitted diagnostics
  from the REST API. Raw segments, canon diagnostics, and campaign/region
  Bestiary catalogs are operator-only.
- Live play uses Socket.IO events: `join_session`, `send_message`,
  `dm_response_start`, `dm_chunk`, `dm_response_end`, `roll_required`,
  `roll_resolved`, `turn_duplicate`, `turn_status`, and `session_log_update`.
- Dice controls send only a roll request. The backend supplies the authoritative
  faces, modifier breakdown, and total; the 3D animation presents that committed
  result. Party peers see the shared result without private ability/proficiency
  provenance. An uncertain retry reuses the original request identity.
- Active combat renders a compact horizontal HUD from viewer-scoped
  `combat.legalActions`. The browser sends only the chosen server action and
  target IDs; it never supplies attack rolls, damage, or action legality.
- Every Socket.IO rejoin refreshes the persisted session snapshot so missed
  events are not the sole recovery mechanism.
- Scene music is docked by default so it cannot cover pending rolls or the turn
  feed. Desktop users can open full floating controls and dock them again;
  short-height and mobile layouts remain inline.
- The UI intentionally does not send a test turn automatically, so it will not
  mutate a live campaign just by loading.
- Auth tokens are kept in `sessionStorage` for the current tab session. The
  legacy `localStorage` key is migrated and cleared when the app loads.
