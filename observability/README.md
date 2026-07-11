# AIDM Observability

This directory provides a local Prometheus and Grafana stack for beta smoke testing.

## Prerequisites

- Start the backend on port `5050`:
  ```bash
  ./scripts/run_local_backend.sh
  ```
- Confirm Prometheus-format metrics are available:
  ```bash
  curl http://127.0.0.1:5050/api/metrics/prometheus
  ```

## Run the stack

```bash
cd observability
docker compose up
```

- Prometheus: http://localhost:9090
- Grafana: http://localhost:3001
- Grafana credentials: `aidm` / `aidm`

Grafana provisions the `AIDM Beta Overview` dashboard automatically.

## Canon queue pressure

The Prometheus endpoint exposes current durable canon queue gauges:

- `aidm_canon_job_queue_depth`
- `aidm_canon_job_running`
- `aidm_canon_job_failed`
- `aidm_canon_job_oldest_queued_age_seconds`

An increasing oldest-queued age with foreground-pressure counters indicates
intentional foreground narration preference is starving background canon work.
The worker remains durable and wakeable; investigate sustained pressure rather
than weakening narration priority.

## Scrape target

`prometheus.yml` uses `host.docker.internal:5050`, which works with Docker Desktop on macOS.
If Docker cannot resolve that hostname, update the target to the host address that can reach the backend.

## Stop and remove local data

```bash
cd observability
docker compose down -v
```
