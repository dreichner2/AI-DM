from __future__ import annotations

from pathlib import Path

from scripts.render_github_actions_evidence import DEFAULT_CLOSED_BETA_RC_ARTIFACT_CONTENT_GLOBS


REPO_ROOT = Path(__file__).resolve().parents[1]


def _workflow_upload_paths(workflow: str) -> set[str]:
    return {
        line.strip()
        for line in workflow.splitlines()
        if line.strip().startswith('tmp/')
    }


def _upload_covers_required_glob(upload_path: str, required_glob: str) -> bool:
    if upload_path.endswith('/'):
        return required_glob.startswith(upload_path)
    return upload_path == required_glob


def test_closed_beta_rc_workflow_uploads_full_handoff_bundle():
    workflow = (REPO_ROOT / '.github' / 'workflows' / 'closed-beta-rc.yml').read_text(encoding='utf-8')
    upload_paths = _workflow_upload_paths(workflow)

    assert 'permissions:' in workflow
    assert 'contents: read' in workflow
    assert 'actions: read' in workflow
    assert 'Build RC handoff artifacts' in workflow
    assert 'Record GitHub Actions evidence' in workflow
    assert 'python scripts/render_github_actions_evidence.py' in workflow
    assert 'closed_beta_rc_url="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"' in workflow
    assert '--auto-gh' in workflow
    assert '--closed-beta-rc-run-url "$closed_beta_rc_url"' in workflow
    assert 'make rc-handoff-artifacts PYTHON=python' in workflow
    assert '--include-gh-details' in workflow
    assert workflow.count('GH_TOKEN: ${{ github.token }}') == 2
    assert workflow.count('lfs: true') == 1

    missing_required_globs = [
        required_glob
        for required_glob in DEFAULT_CLOSED_BETA_RC_ARTIFACT_CONTENT_GLOBS
        if not any(_upload_covers_required_glob(upload_path, required_glob) for upload_path in upload_paths)
    ]
    assert missing_required_globs == []

    for expected_path in (
        'tmp/release/frontend-npm-ci-evidence.md',
        'tmp/release/frontend-npm-ci-evidence.json',
        'tmp/release/github-actions-rc-run-plan.md',
        'tmp/release/github-actions-rc-run-plan.json',
        'tmp/release/hosted-rc-evidence.md',
        'tmp/release/hosted-rc-evidence.json',
        'tmp/release/operator-signoff-status.md',
        'tmp/release/operator-signoff-status.json',
        'tmp/release/operator-signoff.draft.json',
        'tmp/release/operator-signoff-action-plan.md',
        'tmp/release/operator-signoff-action-plan.json',
        'tmp/release/operator-signoff.json',
        'tmp/release/beta-slo-baseline.md',
        'tmp/release/beta-slo.json',
        'tmp/release/beta-incidents.json',
        'tmp/release/release-evidence-packet.md',
        'tmp/release/release-evidence-packet.json',
        'tmp/release/release-checklist-status.md',
        'tmp/release/release-checklist-status.json',
        'tmp/release/rc-recommendation-matrix.md',
        'tmp/release/rc-recommendation-matrix.json',
        'tmp/release/rc-issue-closure-evidence.md',
        'tmp/release/rc-issue-closure-evidence.json',
        'tmp/release/external-proof-inputs.md',
        'tmp/release/external-proof-inputs.json',
        'tmp/release/external-proof-execution-plan.md',
        'tmp/release/external-proof-execution-plan.json',
        'tmp/release/external-proof-values.example.json',
        'tmp/release/external-proof-values.hosted-rc.json',
        'tmp/release/operator-signoff.from-inputs.json',
        'tmp/release/operator-signoff.from-inputs-status.md',
        'tmp/release/operator-signoff.from-inputs-status.json',
    ):
        assert expected_path in upload_paths
