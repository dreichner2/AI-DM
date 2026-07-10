# Observability Operations

## Scope

AIDM exposes operator metrics as JSON at `/api/metrics`, Prometheus text at
`/api/metrics/prometheus`, beta SLO data at `/api/beta/slo`, and incident data at
`/api/beta/incidents`. The tracked `observability/` Compose bundle is a
trusted-local development and smoke-test aid. It is not the hosted production
observability boundary.

Hosted deployments must use the externally configured provider named by
`AIDM_OBSERVABILITY_PROVIDER`, assign `AIDM_ALERT_OWNER`, and retain
target-specific ingestion/query and alert evidence. The repository cannot prove
that a managed destination is configured or receiving data.

## Trusted-Local Defaults

The checked-in Compose file intentionally favors local convenience:

- Prometheus is published as host port `9090` and Grafana as host port `3001`.
  The short Compose port syntax does not restrict these listeners to loopback.
- Grafana anonymous Viewer access is enabled.
- The development Grafana administrator credentials are `aidm` / `aidm`.
- Prometheus scrapes `host.docker.internal:5050/api/metrics/prometheus` every 15
  seconds and does not send an AIDM bearer token.
- Prometheus and Grafana data persist in named Docker volumes.

Do not expose these defaults to a LAN, Tailscale Funnel, public tunnel, or
hosted network. If the backend has `AIDM_AUTH_REQUIRED=true`, the default scrape
will be unauthorized because metrics require operator/debug capability. Use a
local auth-disabled backend for this bundle or create a private, untracked
Prometheus override that supplies an operator credential. Never commit the
credential or place it in the shared dashboard JSON.

## Validate And Run Locally

Static validation does not require Docker:

```bash
make observability-check
```

On a Docker-capable machine, also validate the resolved Compose configuration:

```bash
make observability-check \
  OBSERVABILITY_CHECK_ARGS="--check-docker-compose --require-docker"
```

Start and inspect the trusted-local bundle:

```bash
docker compose -f observability/docker-compose.yml up -d
docker compose -f observability/docker-compose.yml ps
docker compose -f observability/docker-compose.yml logs prometheus grafana
```

Then inspect Prometheus at `http://127.0.0.1:9090` and Grafana at
`http://127.0.0.1:3001`. Confirm the `aidm-backend` Prometheus target is up and
the provisioned AIDM dashboard has data before treating the bundle as useful
local evidence.

Stop the containers while preserving their named volumes:

```bash
docker compose -f observability/docker-compose.yml down
```

`docker compose ... down -v` also deletes the named metric/dashboard volumes;
use it only when that local data loss is intentional.

## Hosted Boundary

The Compose bundle does not prove any of the following:

- continuous hosted metric ingestion;
- receipt of privacy-filtered external telemetry events;
- alert routing or notification delivery;
- retention, access control, or backup policy;
- target-specific SLO thresholds.

Before tester expansion, use `make deployment-readiness` for the live API and
Prometheus endpoints, render the target baseline with `make beta-slo-baseline`,
verify a real managed query/receipt and alert path, and attach those external
artifacts to the release evidence. See `docs/production-readiness.md` and
`docs/beta_slo_baseline.md` for the remaining release workflow.
