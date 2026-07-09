# Socket.IO Worker Model Decision

Decision: single-worker hosted closed beta.

Production policy: single-worker only.

For RC1 and the first hosted closed-beta target, run exactly one backend worker:

```bash
AIDM_ENV=production
AIDM_SOCKETIO_WORKER_MODEL=single
AIDM_SOCKETIO_ASYNC_MODE=threading
AIDM_GUNICORN_THREADS=100
WEB_CONCURRENCY=1
scripts/run_production_server.sh
```

Why this is the default:

- It keeps Socket.IO connection state, room membership, and live event delivery in one backend process.
- It uses Gunicorn's supported `gthread` worker with `simple-websocket`, avoiding the removed Gunicorn eventlet worker and Eventlet's deprecated runtime.
- It avoids load-balancer affinity and message-queue delivery as release variables while the beta group is small.
- It still uses database-backed rate limiting and turn coordination so the hosted environment does not depend on in-memory request gates.
- It matches `scripts/run_production_server.sh`, which defaults to `single` and rejects `WEB_CONCURRENCY` values other than `1` for that model.

Each connected WebSocket occupies one gthread for the life of the connection.
Size `AIDM_GUNICORN_THREADS` for expected connected clients plus REST,
readiness, and operational headroom; the closed-beta default is 100. The
reverse proxy must preserve HTTP/1.1 WebSocket `Upgrade`/`Connection` headers
and keep idle connections alive longer than the Socket.IO heartbeat window.

Hosted RC evidence required for this model:

- `scripts/run_production_server.sh --print` output or platform process configuration showing `--worker-class gthread`, `--workers 1`, and the configured thread count.
- `make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> --target-url <target-url> --auth-token <token> --evidence-report tmp/release/deployment-readiness-evidence.md"`.
- One hosted browser or Socket.IO smoke proving a player can connect, send a turn, receive streamed events, and persist the turn.

Deferred multi-worker models:

- `sticky` and `message_queue` remain reserved configuration names, but hosted
  production rejects both today.
- Fencing and database-backed coordination protect durable turn writes, but
  `SocketState` presence, connection, and music data are still process-local.
- Future multi-worker support requires shared presence/music state, a supported
  queue client and queue health proof, load-balancer affinity, and shared
  Socket.IO delivery. Both affinity and queueing are required; neither is an
  alternative to the other.
- Keep `WEB_CONCURRENCY=1` until that implementation and staging proof land.
