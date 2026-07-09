# Socket.IO Worker Model Decision

Decision: single-worker hosted closed beta.

For RC1 and the first hosted closed-beta target, run exactly one backend worker:

```bash
AIDM_ENV=production
AIDM_SOCKETIO_WORKER_MODEL=single
AIDM_SOCKETIO_ASYNC_MODE=eventlet
WEB_CONCURRENCY=1
scripts/run_production_server.sh
```

Why this is the default:

- It keeps Socket.IO connection state, room membership, and live event delivery in one backend process.
- It avoids load-balancer affinity and message-queue delivery as release variables while the beta group is small.
- It still uses database-backed rate limiting and turn coordination so the hosted environment does not depend on in-memory request gates.
- It matches `scripts/run_production_server.sh`, which defaults to `single` and rejects `WEB_CONCURRENCY` values other than `1` for that model.

Hosted RC evidence required for this model:

- `scripts/run_production_server.sh --print` output or platform process configuration showing `--workers 1`.
- `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> --target-url <target-url> --auth-token <token> --evidence-report tmp/release/deployment-readiness-evidence.md"`.
- One hosted browser or Socket.IO smoke proving a player can connect, send a turn, receive streamed events, and persist the turn.

Deferred multi-worker models:

- Both models require migrations through `0028_session_turn_lock_fencing` and
  database-backed coordination so a worker that loses its lease generation
  cannot commit stale turn state.
- `AIDM_SOCKETIO_WORKER_MODEL=sticky` requires load-balancer affinity and `--socketio-staging-proof`.
- `AIDM_SOCKETIO_WORKER_MODEL=message_queue` requires `AIDM_SOCKETIO_MESSAGE_QUEUE`, database-backed turn coordination, and `--socketio-staging-proof`.
- Do not use sticky or message-queue mode for RC1 unless staging proof shows Socket.IO client event delivery and turn persistence under the actual deployment topology.
