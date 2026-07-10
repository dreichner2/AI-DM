# RC Issue Evidence Template

Use this when closing RC gate issues. Close the issue only when the evidence
matches the issue acceptance criteria.

To generate issue-ready Markdown from the latest local RC report and source
archive scan:

```bash
make closed-beta-rc
make source-archive
make rc-issue-evidence
make release-evidence-packet
```

The generated snippets are written to `tmp/release/issue-evidence/issue-*.md`.
The four commands above produce local evidence only. They intentionally keep
hosted/GitHub/manual proof as remaining exceptions until those external
artifacts are attached.

For a final candidate handoff, prefer the complete same-commit refresh after the
local RC passes:

```bash
make rc-handoff-artifacts
make release-checklist-status
make operator-signoff-status OPERATOR_SIGNOFF_STATUS_ARGS="--require-complete"
```

If `HEAD` changes after the RC report, regenerate the RC and handoff artifacts.
Do not close issues from an older ignored `tmp/release/` bundle merely because
the files still exist. Final signoff also requires the external proof values and
`make rc-finalize-signoff` workflow documented in `docs/beta_runbook.md`.

The generated release packet is written to
`tmp/release/release-evidence-packet.md`. Use it as the short RC handoff index:
it records the RC report, issue snippets, source archive, visual-smoke
screenshots and review evidence, GitHub Actions run URL evidence,
hosted RC evidence, security/export-import evidence, deployment-readiness
evidence, beta SLO baseline status, and the remaining external exceptions in
one place.
The manual GitHub Actions `Closed Beta RC` workflow uploads the same handoff
bundle as the `closed-beta-rc-evidence` artifact when those files are produced.

Preview the exact GitHub issue comments without mutating GitHub:

```bash
make post-rc-issue-evidence
```

Post the comments after reviewing them:

```bash
make post-rc-issue-evidence POST_RC_ISSUE_EVIDENCE_ARGS="--post"
```

Closing issues is intentionally separate. `--close` requires `--post`, and it
refuses to close snippets that still list remaining exceptions unless
`--allow-external-exceptions` is passed intentionally.

## Evidence

- Issue:
- Gate:
- Commit SHA:
- Environment:
- Command run:
- Result:
- Evidence/log path:
- Operator:
- Date/time UTC:

## Acceptance Criteria

| Criterion | Evidence | Status |
| --- | --- | --- |
|  |  |  |

## Exceptions

- Remaining exceptions:
- Risk owner:
- Follow-up issue:
- Decision:

## Suggested Comment

```markdown
Gate evidence:

- Command run:
- Result:
- Environment:
- Commit SHA:
- Evidence/log path:
- Remaining exceptions:
- Decision:
```
