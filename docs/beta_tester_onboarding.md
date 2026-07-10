# Closed Beta Tester Onboarding

Use this guide when inviting a controlled tester group. Keep the group small
until the current RC gates, hosted readiness proof, and backup/restore proof are
all attached to the release evidence.

## Before Inviting Testers

- Run `make closed-beta-rc` and attach `tmp/release/rc-evidence.md` to the RC
  issue or release notes.
- Run the hosted deployment-readiness command against the actual target URL.
- Run a backup/restore drill for the target database or document the managed
  provider restore proof.
- Confirm hosted auth mode from `docs/auth_modes.md`.
- Confirm `AIDM_ALERT_OWNER` and `AIDM_OBSERVABILITY_PROVIDER` are set.
- Confirm `/api/health`, `/api/metrics`, and `/api/metrics/prometheus` respond
  on the target environment.

## Tester Invite Template

Send testers:

- Beta URL.
- Account/table setup instructions.
- Known limitations link.
- What data may be logged: account name, workspace/table ID, campaign/session
  IDs, player ID, turn IDs, provider/model, latency, bad-turn reports, state
  mutation/audit references, and operational errors.
- Support request format:
  - What happened:
  - What you expected:
  - Campaign/session/player:
  - Approximate time:
  - Turn text or screenshot if safe to share:
  - Did you press a bad-turn/feedback button:

## Tester Rules

- Do not enter private secrets, payment details, medical/legal/financial
  personal data, or real-world credentials into game text.
- Expect occasional degraded responses while provider/model settings are being
  validated.
- Report bad turns when the DM contradicts known state, applies rules
  incorrectly, loses continuity, or stalls.
- Use the Beta feedback prompt after meaningful DM responses. It records
  coherence, fun, and rules scores for the active turn.
- Keep sessions short enough that operators can review incidents after each run.
- Ask an operator before using the same account/table from multiple browsers.

## Known Limitations

- Hosted beta is not a public SaaS launch.
- Hosted beta is single-worker only. Sticky and message-queue worker models are
  unsupported for RC1 even with staging proof because presence and music state
  remain process-local.
- Cookie-only account auth is the hosted default, but the real hosted browser
  flow still needs target-specific proof for domain, HTTPS, SameSite, and secure
  cookie behavior.
- The deterministic fallback provider is for safety/testing, not final DM
  quality.
- TTS depends on provider configuration and browser autoplay behavior.
- The top runtime notice strip calls out fallback provider use, missing provider
  keys, unavailable TTS, local/private auth-disabled mode, and process-local
  provider changes. Use its Beta Notes control for the current in-app known
  limitations list.
- Campaign packs can contain hidden authored content; players should not assume
  all authored NPCs, locations, or branches are visible at session start.
- Operator tools and support bundles are admin-only and may expose session IDs,
  provider/model metadata, and audit references.

## Operator Follow-Up After Each Session

- Review `/api/beta/slo`.
- Review `/api/beta/incidents`.
- Review the selected-session Session Quality card in the operator Ops tab, or
  request `/api/beta/session-quality?session_id=<id>`, for provider/model,
  latency, failed turns, canon failures, reports, clarification, and audit counts.
- Export a session support bundle from the operator Ops tab, or request
  `/api/beta/support-bundle?session_id=<id>`, for sessions with reported issues.
  For hosted/operator automation, use `make export-support-bundle
  EXPORT_SUPPORT_BUNDLE_ARGS="--target-url <target-url> --auth-token <token>
  --workspace-id <workspace-id> --session-id <session-id>"`.
- Record unresolved issues in the beta incident log.
- Note whether the issue was provider behavior, rules/state extraction,
  persistence, Socket.IO delivery, auth/session, frontend UX, or tester setup.
