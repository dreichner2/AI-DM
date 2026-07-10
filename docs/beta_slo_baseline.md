# Beta SLO Baseline Template

Create one copy of this template for each RC or hosted beta expansion decision.
Use current target-environment evidence, not local fallback-only evidence, when
deciding whether to invite more testers.

## Release Context

- RC or release:
- Commit SHA:
- Environment:
- Target URL:
- Socket.IO worker model:
- Database:
- LLM provider/model:
- Observability provider:
- Alert owner:
- Evidence report:

## Commands

```bash
make closed-beta-rc
make local-beta-slo-baseline
make deployment-readiness DEPLOYMENT_READINESS_ARGS="--env-file <target-env> --target-url <target-url> --auth-token <token> --evidence-report tmp/release/deployment-readiness-evidence.md"
make hosted-cookie-auth-smoke HOSTED_COOKIE_AUTH_SMOKE_ARGS="--target-url <target-url> --account-intent signup --evidence-report tmp/release/hosted-cookie-auth-evidence.md"
make beta-slo-baseline BETA_SLO_BASELINE_ARGS="--target-url <target-url> --auth-token <token> --workspace-id <workspace-id> --release RC1 --environment staging --output tmp/release/beta-slo-baseline.md"
```

For any future multi-worker Socket.IO deployment, also include the staging proof
URL, log path, or runbook link passed to `--socketio-staging-proof`.

The `local-beta-slo-baseline` target writes local-only release evidence from an
isolated fixture. It proves the SLO endpoints and renderer work, but it is not
target-environment evidence for inviting more testers.

The `beta-slo-baseline` target writes `tmp/release/beta-slo-baseline.md` from
the hosted target. It can also render from saved evidence with `--slo-json` and
`--incidents-json`.

The repository defines the measurements but does not define universal pass/fail
thresholds for every deployment. Record the release owner's thresholds and the
named alert owner in the decision copy; the presence of a rendered baseline is
not by itself approval to expand the tester group.

## Baseline Metrics

| Metric | Value | Source | Decision |
| --- | ---: | --- | --- |
| DM response p95 latency |  | `/api/beta/slo` |  |
| DM response sample count |  | `/api/beta/slo` |  |
| AI provider failure rate |  | `/api/beta/slo` |  |
| Canon job failure rate |  | `/api/beta/slo` |  |
| Turn persistence failure rate |  | `/api/beta/slo` |  |
| Socket unauthorized events |  | `/api/beta/slo` or `/api/metrics` |  |
| Socket rate-limited events |  | `/api/beta/slo` or `/api/metrics` |  |
| Average coherence feedback score |  | `/api/beta/slo` |  |
| Bad-turn reports by provider/model |  | `/api/beta/incidents` |  |

## Incident Review

| Session | Turn | Category | Provider/model | Status | Owner | Link/evidence |
| --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |

Use `/api/beta/support-bundle?session_id=<id>` for the support bundle when a
session has failed turns, failed canon jobs, bad-turn reports, or unclear
operator/state mutations.

## Gate Decision

- Invite more testers: yes/no
- Reasons:
- Exceptions:
- Follow-up issues:
- Next review date:
