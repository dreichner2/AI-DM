---
name: Security hardening
about: Track auth, capability, exposure, or abuse-control work
title: "[Security]: "
labels: security, hardening
---

> Never post live secrets, tokens, credentials, private data, or a working
> exploit against a hosted target. Coordinate privately with the repository
> owner before filing details that would create avoidable exposure.

## Risk and boundary

Describe the trust boundary, attacker or failure precondition, affected asset,
and impact. Distinguish a validated issue from a defense-in-depth proposal.

## Affected surface

- [ ] Account, workspace, cookie/CSRF, or pre-auth flow
- [ ] REST route or capability matrix
- [ ] Socket.IO auth, room, turn, presence, or rate limit
- [ ] Import/export, campaign content, or state mutation
- [ ] Provider/TTS/Codex subprocess or external telemetry
- [ ] Frontend credential/privacy boundary
- [ ] Database, migration, backup, deployment, or release artifact

## Sanitized evidence

- Commit/RC and environment:
- Source-to-sink path or reproduction preconditions:
- Relevant test/log/evidence reference:
- Data or credentials exposed (do not include values):

## Proposed acceptance criteria

- [ ] Unauthorized/non-admin negative test
- [ ] Authorized admin/operator positive test
- [ ] Wrong-workspace/account test where relevant
- [ ] Logs, responses, and evidence remain sanitized
- [ ] Deployment/readiness or release gate updated when relevant
- [ ] Threat assumption and residual risk documented

## Operational response

- Immediate containment:
- Release impact:
- Owner and review/expiry date for any temporary acceptance:
