#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import pathlib
import tarfile
from dataclasses import dataclass
from typing import Any


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_REPORT = REPO_ROOT / 'tmp' / 'release' / 'rc-evidence.md'
DEFAULT_OUTPUT_DIR = REPO_ROOT / 'tmp' / 'release' / 'issue-evidence'
DEFAULT_VISUAL_SMOKE_ROOT = REPO_ROOT / 'tmp' / 'verification_artifacts' / 'visual-smoke'
DEFAULT_VISUAL_SMOKE_REVIEW = REPO_ROOT / 'tmp' / 'release' / 'visual-smoke-review.md'
DEFAULT_GITHUB_ACTIONS_EVIDENCE = REPO_ROOT / 'tmp' / 'release' / 'github-actions-evidence.md'
DEFAULT_SECURITY_FORBIDDEN_EVIDENCE = REPO_ROOT / 'tmp' / 'release' / 'security-forbidden-evidence.md'
DEFAULT_EXPORT_IMPORT_EVIDENCE = REPO_ROOT / 'tmp' / 'release' / 'export-import-evidence.md'
DEFAULT_HOSTED_RC_EVIDENCE = REPO_ROOT / 'tmp' / 'release' / 'hosted-rc-evidence.md'
DEFAULT_BETA_TESTER_ONBOARDING = REPO_ROOT / 'docs' / 'beta_tester_onboarding.md'
DEFAULT_BETA_TESTER_LINK_SOURCES = (
    REPO_ROOT / 'README.md',
    REPO_ROOT / 'docs' / 'beta_runbook.md',
)
DEFAULT_GITATTRIBUTES = REPO_ROOT / '.gitattributes'
EXPECTED_VISUAL_SMOKE_SCREENSHOTS = ('desktop-shell.png', 'mobile-full.png', 'short-height-composer.png')
LARGE_ARCHIVE_MEMBER_THRESHOLD_BYTES = 50 * 1024 * 1024
GITHUB_ACTIONS_RUN_URL_EXCEPTION = 'Attach GitHub Actions `AIDM CI` and `Closed Beta RC` run URLs before closing.'
HOSTED_HEALTH_EXCEPTION = 'Attach live hosted/staging `/api/health` evidence when this issue is used for hosted RC sign-off.'
HOSTED_DEPLOYMENT_READINESS_EXCEPTION = 'Run deployment-readiness against the actual hosted/staging URL before closing.'
HOSTED_FORBIDDEN_EXCEPTION = 'Attach non-admin forbidden-response evidence for internet-exposed combat/bestiary/admin endpoints if the issue requires hosted proof.'
HOSTED_BACKUP_RESTORE_EXCEPTION = 'Attach provider-specific hosted database backup/restore evidence for hosted beta.'
HOSTED_EXPORT_IMPORT_EXCEPTION = 'Attach target-environment export/import smoke evidence when signing off hosted data integrity.'
HOSTED_WORKER_PROCESS_EXCEPTION = 'Attach hosted process evidence showing the documented single-worker model is running with exactly one backend worker.'
HOSTED_SINGLE_WORKER_POLICY_EXCEPTION = (
    'Confirm the hosted target uses the supported single-worker Socket.IO topology; '
    'sticky/message_queue production is unsupported even with staging proof.'
)
HOSTED_METRICS_EXCEPTION = 'Run deployment-readiness live checks for `/api/metrics` and `/api/metrics/prometheus` against the hosted target.'
HOSTED_SLO_EXCEPTION = 'Fill `docs/beta_slo_baseline.md` with target-environment metrics before tester expansion.'
SOURCE_ARCHIVE_ATTACHMENT_EXCEPTION = 'Attach the source archive to the RC issue or GitHub Release before closing.'

HOSTED_BACKUP_RESTORE_LABEL = 'Hosted database backup/restore proof'
HOSTED_WORKER_PROCESS_LABEL = 'Hosted Socket.IO worker process proof'
SOURCE_ARCHIVE_ATTACHMENT_LABEL = 'Source archive attached to RC issue or release'
UNUSABLE_HOSTED_RC_STATUSES = {'missing', 'planned', 'missing-input', 'failed', 'invalid', 'invalid-evidence', 'stale'}

FORBIDDEN_ARCHIVE_PARTS = {
    '.venv',
    'venv',
    'env',
    'node_modules',
    'dist',
    'tmp',
    '.env',
    '.env.local',
    '__pycache__',
    '.pytest_cache',
    '.ruff_cache',
    '.mypy_cache',
    '.vite',
    'playwright-report',
    'test-results',
    'htmlcov',
}
FORBIDDEN_ARCHIVE_PATHS = {
    ('aidm_server', 'instance'),
    ('ai_dm', 'instance'),
}
FORBIDDEN_ARCHIVE_SUFFIXES = ('.db', '.sqlite', '.sqlite3', '.pid', '.log')


@dataclass(frozen=True)
class IssueEvidenceSpec:
    issue_number: int
    slug: str
    title: str
    gate: str
    criteria: tuple[tuple[str, tuple[str, ...]], ...]
    external_exceptions: tuple[str, ...] = ()


ISSUE_SPECS: tuple[IssueEvidenceSpec, ...] = (
    IssueEvidenceSpec(
        issue_number=3,
        slug='preflight',
        title='Closed Beta RC1: Preflight gates',
        gate='Preflight',
        criteria=(
            ('Deploy bootstrap check-only passes', ('Deploy bootstrap check-only',)),
            ('Backend tests pass', ('Backend tests',)),
            ('Smoke and scenario regressions pass', ('Isolated beta smoke flow', 'Scenario quality regressions')),
            ('Backup/restore and migration drills pass', ('SQLite backup/restore drill', 'Migration chain drill')),
            ('API contract drift check passes', ('API type drift check',)),
        ),
        external_exceptions=(
            GITHUB_ACTIONS_RUN_URL_EXCEPTION,
            HOSTED_HEALTH_EXCEPTION,
        ),
    ),
    IssueEvidenceSpec(
        issue_number=4,
        slug='frontend',
        title='Closed Beta RC1: Frontend gates',
        gate='Frontend',
        criteria=(
            ('Frontend typecheck/lint/unit suite passes', ('Frontend tests',)),
            ('Frontend production build passes', ('Frontend build',)),
            ('Bundle budget passes', ('Frontend bundle budget',)),
            ('Production dependency audit passes', ('Frontend production dependency audit',)),
            ('Built single-origin browser smoke passes', ('Browser smoke (single-origin build)',)),
            ('Visual smoke screenshots pass', ('Visual smoke screenshots',)),
            ('Visual smoke artifact review passes', ('Visual smoke artifact review',)),
        ),
    ),
    IssueEvidenceSpec(
        issue_number=5,
        slug='security',
        title='Closed Beta RC1: Security gates',
        gate='Security',
        criteria=(
            ('Deploy bootstrap rejects unsafe production startup config', ('Deploy bootstrap check-only',)),
            ('Secret scan passes', ('Secret scan',)),
            ('Python and frontend dependency audits pass', ('Python dependency audit', 'Frontend production dependency audit')),
            ('Hosted cookie-auth smoke passes', ('Hosted cookie auth smoke',)),
            ('Non-admin forbidden-response smoke passes', ('Security forbidden smoke',)),
            ('Built browser smoke verifies required security headers/CSP', ('Browser smoke (single-origin build)',)),
        ),
        external_exceptions=(
            HOSTED_DEPLOYMENT_READINESS_EXCEPTION,
            HOSTED_FORBIDDEN_EXCEPTION,
        ),
    ),
    IssueEvidenceSpec(
        issue_number=6,
        slug='data-integrity',
        title='Closed Beta RC1: Data-integrity gates',
        gate='Data Integrity',
        criteria=(
            ('Backup/restore drill passes', ('SQLite backup/restore drill',)),
            ('Migration chain drill passes', ('Migration chain drill',)),
            ('State snapshot writer inventory passes', ('State snapshot writer inventory',)),
            ('Backend data-integrity regression suite passes', ('Backend tests',)),
            ('Session export/import smoke passes', ('Session export/import smoke',)),
            ('Smoke/scenario/browser flows cover session state and import paths', (
                'Isolated beta smoke flow',
                'Scenario quality regressions',
                'Browser smoke (single-origin build)',
            )),
        ),
        external_exceptions=(
            HOSTED_BACKUP_RESTORE_EXCEPTION,
            HOSTED_EXPORT_IMPORT_EXCEPTION,
        ),
    ),
    IssueEvidenceSpec(
        issue_number=7,
        slug='runtime-quality',
        title='Closed Beta RC1: Runtime-quality gates',
        gate='Runtime Quality',
        criteria=(
            ('Deploy bootstrap worker-model/config checks pass', ('Deploy bootstrap check-only',)),
            ('Hosted worker-model decision is documented and checked', ('Socket.IO worker model decision',)),
            ('Socket concurrency smoke passes', ('Socket concurrency smoke',)),
            ('Hosted cookie socket auth smoke passes', ('Hosted cookie auth smoke',)),
            ('Scenario quality regressions pass', ('Scenario quality regressions',)),
            ('Browser smoke exercises live session runtime flows', ('Browser smoke (single-origin build)',)),
        ),
        external_exceptions=(
            HOSTED_WORKER_PROCESS_EXCEPTION,
            HOSTED_SINGLE_WORKER_POLICY_EXCEPTION,
        ),
    ),
    IssueEvidenceSpec(
        issue_number=8,
        slug='observability',
        title='Closed Beta RC1: Observability gates',
        gate='Observability',
        criteria=(
            ('Observability bundle check passes', ('Observability bundle check',)),
            ('Local beta SLO baseline renders', ('Local beta SLO baseline',)),
            ('Backend observability and telemetry tests pass', ('Backend tests',)),
            ('Hosted cookie/auth smoke records auth and socket behavior', ('Hosted cookie auth smoke',)),
            ('Browser smoke exercises operator-visible runtime paths', ('Browser smoke (single-origin build)',)),
        ),
        external_exceptions=(
            HOSTED_METRICS_EXCEPTION,
            HOSTED_SLO_EXCEPTION,
        ),
    ),
    IssueEvidenceSpec(
        issue_number=9,
        slug='packaging',
        title='Closed Beta RC1: Packaging gates',
        gate='Packaging',
        criteria=(
            ('Frontend build and bundle budget pass before packaging', ('Frontend build', 'Frontend bundle budget')),
            ('Dependency audits pass before release archive', ('Python dependency audit', 'Frontend production dependency audit')),
            ('Browser and visual smoke pass against release UI', ('Browser smoke (single-origin build)', 'Visual smoke screenshots')),
            ('Source archive exists and excludes generated/runtime artifacts', ('Source archive clean',)),
            ('Beta tester onboarding guide exists and is linked', ('Beta tester onboarding linked',)),
        ),
        external_exceptions=(SOURCE_ARCHIVE_ATTACHMENT_EXCEPTION,),
    ),
)


def _resolve_repo_path(path: pathlib.Path) -> pathlib.Path:
    return path if path.is_absolute() else REPO_ROOT / path


def _strip_ticks(value: str) -> str:
    value = value.strip()
    if value.startswith('`') and value.endswith('`'):
        return value[1:-1]
    return value


def _parse_markdown_evidence(path: pathlib.Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {'commands': []}
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if line.startswith('- Status:'):
            metadata['status'] = line.removeprefix('- Status:').strip()
        elif line.startswith('- Started:'):
            metadata['started_at'] = line.removeprefix('- Started:').strip()
        elif line.startswith('- Finished:'):
            metadata['finished_at'] = line.removeprefix('- Finished:').strip()
        elif line.startswith('- Commit:'):
            metadata['commit'] = line.removeprefix('- Commit:').strip()
        elif line.startswith('- Worktree:'):
            metadata['worktree'] = line.removeprefix('- Worktree:').strip()
        elif line.startswith('- Repo:'):
            metadata['repo_root'] = _strip_ticks(line.removeprefix('- Repo:').strip())
        elif line.startswith('- Python:'):
            metadata['python'] = _strip_ticks(line.removeprefix('- Python:').strip())
        elif line.startswith('- Browser smoke:'):
            metadata['include_browser_smoke'] = line.removeprefix('- Browser smoke:').strip() == 'included'
        elif line.startswith('- Dependency audits:'):
            metadata['include_dependency_audits'] = line.removeprefix('- Dependency audits:').strip() == 'included'
        elif line.startswith('|') and not line.startswith('| ---') and not line.startswith('| Gate '):
            cells = [cell.strip() for cell in line.strip('|').split('|')]
            if len(cells) != 5:
                continue
            label, status, exit_code, seconds, command = cells
            metadata['commands'].append(
                {
                    'label': label,
                    'status': status,
                    'returncode': int(exit_code) if exit_code else None,
                    'duration_seconds': float(seconds) if seconds else None,
                    'command': _strip_ticks(command),
                }
            )
    return metadata


def load_evidence(path: pathlib.Path) -> dict[str, Any]:
    evidence_path = _resolve_repo_path(path)
    if evidence_path.suffix.lower() == '.json':
        return json.loads(evidence_path.read_text(encoding='utf-8'))
    return _parse_markdown_evidence(evidence_path)


def _file_sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _latest_source_archive() -> pathlib.Path | None:
    release_dir = REPO_ROOT / 'tmp' / 'release'
    if not release_dir.exists():
        return None
    archives = sorted(release_dir.glob('aidm-source-*.tar.gz'), key=lambda path: path.stat().st_mtime)
    return archives[-1] if archives else None


def latest_visual_smoke_dir(root: pathlib.Path | None = None) -> pathlib.Path | None:
    smoke_root = _resolve_repo_path(root or DEFAULT_VISUAL_SMOKE_ROOT)
    if not smoke_root.exists():
        return None
    candidates = [path for path in smoke_root.iterdir() if path.is_dir()]
    if not candidates:
        return None
    return sorted(candidates, key=lambda path: (path.stat().st_mtime, path.name))[-1]


def inspect_visual_smoke(path: pathlib.Path | None = None) -> dict[str, Any]:
    smoke_dir = _resolve_repo_path(path) if path is not None else latest_visual_smoke_dir()
    if smoke_dir is None:
        return {'status': 'missing', 'path': '', 'screenshots': [], 'missing': list(EXPECTED_VISUAL_SMOKE_SCREENSHOTS)}
    if not smoke_dir.exists() or not smoke_dir.is_dir():
        return {
            'status': 'missing',
            'path': str(smoke_dir),
            'screenshots': [],
            'missing': list(EXPECTED_VISUAL_SMOKE_SCREENSHOTS),
        }
    screenshots = [name for name in EXPECTED_VISUAL_SMOKE_SCREENSHOTS if (smoke_dir / name).exists()]
    missing = [name for name in EXPECTED_VISUAL_SMOKE_SCREENSHOTS if name not in screenshots]
    return {
        'status': 'passed' if not missing else 'incomplete',
        'path': str(smoke_dir),
        'screenshots': screenshots,
        'missing': missing,
    }


def inspect_visual_smoke_review(path: pathlib.Path | None = None) -> dict[str, Any]:
    report_path = _resolve_repo_path(path or DEFAULT_VISUAL_SMOKE_REVIEW)
    if not report_path.exists():
        return {
            'status': 'missing',
            'path': str(report_path),
            'screenshots': '',
            'failures': 'review report missing',
        }
    metadata: dict[str, str] = {}
    for raw_line in report_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if line.startswith('- Status:'):
            metadata['status'] = line.removeprefix('- Status:').strip()
        elif line.startswith('- Artifact dir:'):
            metadata['artifact_dir'] = _strip_ticks(line.removeprefix('- Artifact dir:').strip())
        elif line.startswith('- Screenshots:'):
            metadata['screenshots'] = line.removeprefix('- Screenshots:').strip()
        elif line.startswith('- Failures:'):
            metadata['failures'] = line.removeprefix('- Failures:').strip()
    return {
        'status': metadata.get('status') or 'present',
        'path': str(report_path),
        'artifact_dir': metadata.get('artifact_dir') or '',
        'screenshots': metadata.get('screenshots') or '',
        'failures': metadata.get('failures') or '',
    }


def inspect_github_actions_evidence(path: pathlib.Path | None = None) -> dict[str, Any]:
    report_path = _resolve_repo_path(path or DEFAULT_GITHUB_ACTIONS_EVIDENCE)
    if not report_path.exists():
        return {
            'status': 'missing',
            'path': str(report_path),
            'aidm_ci_run_url': '',
            'closed_beta_rc_run_url': '',
            'worktree': '',
            'git_worktree': {},
            'missing': 'evidence report missing',
        }
    metadata: dict[str, str] = {}
    for raw_line in report_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if line.startswith('- Status:'):
            metadata['status'] = line.removeprefix('- Status:').strip()
        elif line.startswith('- Repository:'):
            metadata['repository'] = line.removeprefix('- Repository:').strip()
        elif line.startswith('- AIDM CI run URL:'):
            metadata['aidm_ci_run_url'] = _strip_ticks(line.removeprefix('- AIDM CI run URL:').strip())
        elif line.startswith('- Closed Beta RC run URL:'):
            metadata['closed_beta_rc_run_url'] = _strip_ticks(line.removeprefix('- Closed Beta RC run URL:').strip())
        elif line.startswith('- Closed Beta RC artifact status:'):
            metadata['closed_beta_rc_artifact_status'] = line.removeprefix('- Closed Beta RC artifact status:').strip()
        elif line.startswith('- Closed Beta RC artifact content status:'):
            metadata['closed_beta_rc_artifact_content_status'] = line.removeprefix(
                '- Closed Beta RC artifact content status:'
            ).strip()
        elif line.startswith('- Closed Beta RC artifact expected name:'):
            metadata['closed_beta_rc_artifact_expected_name'] = _strip_ticks(
                line.removeprefix('- Closed Beta RC artifact expected name:').strip()
            )
        elif line.startswith('- Closed Beta RC artifact name:'):
            metadata['closed_beta_rc_artifact_name'] = _strip_ticks(
                line.removeprefix('- Closed Beta RC artifact name:').strip()
            )
        elif line.startswith('- Closed Beta RC artifact URL:'):
            metadata['closed_beta_rc_artifact_url'] = _strip_ticks(
                line.removeprefix('- Closed Beta RC artifact URL:').strip()
            )
        elif line.startswith('- Worktree:'):
            metadata['worktree'] = line.removeprefix('- Worktree:').strip()
        elif line.startswith('- Missing:'):
            metadata['missing'] = line.removeprefix('- Missing:').strip()
        elif line.startswith('- Validation errors:'):
            metadata['validation_errors'] = line.removeprefix('- Validation errors:').strip()
    json_payload: dict[str, Any] = {}
    json_path = report_path if report_path.suffix.lower() == '.json' else report_path.with_suffix('.json')
    if json_path.exists():
        try:
            parsed = json.loads(json_path.read_text(encoding='utf-8'))
            if isinstance(parsed, dict):
                json_payload = parsed
        except json.JSONDecodeError:
            json_payload = {}

    status = str(json_payload.get('status') or metadata.get('status') or 'present')
    validation_errors_value = json_payload.get('validation_errors')
    if isinstance(validation_errors_value, list):
        validation_errors = ', '.join(str(error) for error in validation_errors_value if str(error).strip())
    else:
        validation_errors = metadata.get('validation_errors') or str(validation_errors_value or '')
    missing_value = json_payload.get('missing')
    missing = ', '.join(str(item) for item in missing_value) if isinstance(missing_value, list) else metadata.get('missing') or str(missing_value or '')
    missing_details = json_payload.get('missing_details') if isinstance(json_payload.get('missing_details'), dict) else {}
    next_actions = json_payload.get('next_actions') if isinstance(json_payload.get('next_actions'), list) else []
    git_worktree = json_payload.get('worktree') if isinstance(json_payload.get('worktree'), dict) else {}
    closed_beta_rc_artifact = json_payload.get('closed_beta_rc_artifact')
    if not isinstance(closed_beta_rc_artifact, dict):
        closed_beta_rc_artifact = {
            'status': metadata.get('closed_beta_rc_artifact_status') or 'not-checked',
            'content_status': metadata.get('closed_beta_rc_artifact_content_status') or 'not-checked',
            'expected_name': metadata.get('closed_beta_rc_artifact_expected_name') or 'closed-beta-rc-evidence',
            'name': metadata.get('closed_beta_rc_artifact_name') or '',
            'url': metadata.get('closed_beta_rc_artifact_url') or '',
        }
    if git_worktree:
        worktree = str(git_worktree.get('summary') or git_worktree.get('state') or metadata.get('worktree') or '')
    else:
        worktree = metadata.get('worktree') or str(json_payload.get('worktree') or '')
    if validation_errors and validation_errors != 'None.' and status == 'passed':
        status = 'invalid'
    return {
        'status': status,
        'path': str(report_path),
        'repository': str(json_payload.get('repository') or metadata.get('repository') or ''),
        'aidm_ci_run_url': str(json_payload.get('aidm_ci_run_url') or metadata.get('aidm_ci_run_url') or ''),
        'closed_beta_rc_run_url': str(
            json_payload.get('closed_beta_rc_run_url') or metadata.get('closed_beta_rc_run_url') or ''
        ),
        'closed_beta_rc_artifact_status': str(
            json_payload.get('closed_beta_rc_artifact_status')
            or closed_beta_rc_artifact.get('status')
            or 'not-checked'
        ),
        'closed_beta_rc_artifact_content_status': str(
            json_payload.get('closed_beta_rc_artifact_content_status')
            or closed_beta_rc_artifact.get('content_status')
            or 'not-checked'
        ),
        'closed_beta_rc_artifact_name': str(
            closed_beta_rc_artifact.get('name') or metadata.get('closed_beta_rc_artifact_name') or ''
        ),
        'closed_beta_rc_artifact_url': str(
            closed_beta_rc_artifact.get('url') or metadata.get('closed_beta_rc_artifact_url') or ''
        ),
        'closed_beta_rc_artifact': {
            str(key): value for key, value in closed_beta_rc_artifact.items()
        },
        'worktree': worktree,
        'git_worktree': git_worktree,
        'missing': missing,
        'missing_details': {str(key): str(value) for key, value in missing_details.items()},
        'next_actions': [str(action) for action in next_actions],
        'validation_errors': validation_errors,
    }


def inspect_security_forbidden_evidence(path: pathlib.Path | None = None) -> dict[str, Any]:
    report_path = _resolve_repo_path(path or DEFAULT_SECURITY_FORBIDDEN_EVIDENCE)
    if not report_path.exists():
        return {'status': 'missing', 'path': str(report_path), 'mode': '', 'target_url': ''}
    metadata: dict[str, str] = {}
    for raw_line in report_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if line.startswith('- Status:'):
            metadata['status'] = line.removeprefix('- Status:').strip()
        elif line.startswith('- Mode:'):
            metadata['mode'] = line.removeprefix('- Mode:').strip()
        elif line.startswith('- Target URL:'):
            metadata['target_url'] = _strip_ticks(line.removeprefix('- Target URL:').strip())
    return {
        'status': metadata.get('status') or 'present',
        'path': str(report_path),
        'mode': metadata.get('mode') or '',
        'target_url': metadata.get('target_url') or '',
    }


def inspect_export_import_evidence(path: pathlib.Path | None = None) -> dict[str, Any]:
    report_path = _resolve_repo_path(path or DEFAULT_EXPORT_IMPORT_EVIDENCE)
    if not report_path.exists():
        return {'status': 'missing', 'path': str(report_path), 'mode': '', 'target_url': ''}
    metadata: dict[str, str] = {}
    for raw_line in report_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if line.startswith('- Status:'):
            metadata['status'] = line.removeprefix('- Status:').strip()
        elif line.startswith('- Mode:'):
            metadata['mode'] = line.removeprefix('- Mode:').strip()
        elif line.startswith('- Target URL:'):
            metadata['target_url'] = _strip_ticks(line.removeprefix('- Target URL:').strip())
    return {
        'status': metadata.get('status') or 'present',
        'path': str(report_path),
        'mode': metadata.get('mode') or '',
        'target_url': metadata.get('target_url') or '',
    }


def inspect_hosted_rc_evidence(path: pathlib.Path | None = None) -> dict[str, Any]:
    report_path = _resolve_repo_path(path or DEFAULT_HOSTED_RC_EVIDENCE)
    if not report_path.exists():
        return {
            'status': 'missing',
            'path': str(report_path),
            'target_url': '',
            'checks': {},
            'manual_evidence': {},
            'manual_required': [],
            'manual_provided': [],
        }
    metadata: dict[str, str] = {}
    checks: dict[str, dict[str, str]] = {}
    manual_evidence: dict[str, dict[str, str]] = {}
    in_checks = False
    in_manual = False
    for raw_line in report_path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if line.startswith('- Status:'):
            metadata['status'] = line.removeprefix('- Status:').strip()
        elif line.startswith('- Automated status:'):
            metadata['automated_status'] = line.removeprefix('- Automated status:').strip()
        elif line.startswith('- Target URL:'):
            metadata['target_url'] = _strip_ticks(line.removeprefix('- Target URL:').strip())
        elif line.startswith('- Socket.IO worker model:'):
            metadata['socketio_worker_model'] = line.removeprefix('- Socket.IO worker model:').strip()
        elif line.startswith('- Socket.IO staging proof:'):
            metadata['socketio_staging_proof'] = _strip_ticks(line.removeprefix('- Socket.IO staging proof:').strip())
        elif line == '## Automated Checks':
            in_checks = True
            in_manual = False
            continue
        elif line == '## Manual Evidence Still Required':
            in_checks = False
            in_manual = True
            continue
        elif line.startswith('## '):
            in_checks = False
            in_manual = False

        if not line.startswith('|') or line.startswith('| ---') or line.startswith('| Check |') or line.startswith('| Evidence |'):
            continue
        cells = [cell.strip() for cell in line.strip('|').split('|')]
        if in_checks and len(cells) >= 5:
            label, status, exit_code, evidence_path, missing_inputs = cells[:5]
            checks[label] = {
                'status': status,
                'exit': exit_code,
                'evidence_path': _strip_ticks(evidence_path),
                'missing_inputs': missing_inputs,
            }
        elif in_manual and len(cells) >= 3:
            label, status, evidence = cells[:3]
            manual_evidence[label] = {
                'status': status,
                'evidence': evidence,
            }

    manual_required = [label for label, item in manual_evidence.items() if item.get('status') == 'required']
    manual_provided = [label for label, item in manual_evidence.items() if item.get('status') == 'provided']
    return {
        'status': metadata.get('status') or 'present',
        'automated_status': metadata.get('automated_status') or '',
        'path': str(report_path),
        'target_url': metadata.get('target_url') or '',
        'socketio_worker_model': metadata.get('socketio_worker_model') or '',
        'socketio_staging_proof': metadata.get('socketio_staging_proof') or '',
        'checks': checks,
        'manual_evidence': manual_evidence,
        'manual_required': manual_required,
        'manual_provided': manual_provided,
    }


def _path_has_forbidden_archive_part(parts: tuple[str, ...]) -> bool:
    if any(part in FORBIDDEN_ARCHIVE_PARTS for part in parts):
        return True
    return any(forbidden in zip(parts, parts[1:]) for forbidden in FORBIDDEN_ARCHIVE_PATHS)


def _link_target_is_forbidden(linkname: str) -> bool:
    target = pathlib.PurePosixPath(linkname.strip())
    if not linkname.strip() or target.is_absolute() or '..' in target.parts:
        return True
    parts = tuple(part for part in target.parts if part not in {'', '.'})
    return (
        _path_has_forbidden_archive_part(parts)
        or target.suffix in FORBIDDEN_ARCHIVE_SUFFIXES
    )


def _load_lfs_patterns(path: pathlib.Path = DEFAULT_GITATTRIBUTES) -> list[str]:
    if not path.exists():
        return []
    patterns: list[str] = []
    for raw_line in path.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        tokens = line.split()
        if len(tokens) < 2 or 'filter=lfs' not in tokens[1:]:
            continue
        patterns.append(tokens[0].lstrip('/'))
    return patterns


def _archive_project_path(member_name: str) -> str:
    parts = tuple(part for part in pathlib.PurePosixPath(member_name.strip('/')).parts if part not in {'', '.'})
    # Source archives are wrapped in the checkout directory name. That name can
    # differ from the current checkout after a repository rename or when an
    # artifact is inspected on another machine, so do not key solely on
    # REPO_ROOT.name. Preserve genuinely unwrapped project paths whose first
    # component exists at the repository root.
    if len(parts) > 1 and (parts[0] == REPO_ROOT.name or not (REPO_ROOT / parts[0]).exists()):
        parts = parts[1:]
    return '/'.join(parts)


def _matches_lfs_pattern(project_path: str, patterns: list[str]) -> bool:
    normalized = project_path.strip('/')
    basename = pathlib.PurePosixPath(normalized).name
    for pattern in patterns:
        normalized_pattern = pattern.strip('/')
        if fnmatch.fnmatchcase(normalized, normalized_pattern):
            return True
        if '/' not in normalized_pattern and fnmatch.fnmatchcase(basename, normalized_pattern):
            return True
    return False


def inspect_source_archive(path: pathlib.Path | None) -> dict[str, Any]:
    archive_path = _resolve_repo_path(path) if path is not None else _latest_source_archive()
    if archive_path is None:
        return {
            'status': 'missing',
            'path': '',
            'forbidden': [],
            'sha256': '',
            'bytes': 0,
            'large_members': [],
            'large_member_count': 0,
            'large_untracked': [],
        }
    forbidden: list[str] = []
    large_members: list[dict[str, Any]] = []
    large_untracked: list[str] = []
    if not archive_path.exists():
        return {
            'status': 'missing',
            'path': str(archive_path),
            'forbidden': [],
            'sha256': '',
            'bytes': 0,
            'large_members': [],
            'large_member_count': 0,
            'large_untracked': [],
        }
    sha256 = _file_sha256(archive_path)
    byte_count = archive_path.stat().st_size
    lfs_patterns = _load_lfs_patterns()
    try:
        with tarfile.open(archive_path, mode='r:*') as archive:
            for member in archive.getmembers():
                name = member.name.strip('/')
                parts = tuple(part for part in pathlib.PurePosixPath(name).parts if part not in {'', '.'})
                if (
                    _path_has_forbidden_archive_part(parts)
                    or pathlib.PurePosixPath(name).suffix in FORBIDDEN_ARCHIVE_SUFFIXES
                ):
                    forbidden.append(name)
                if (member.issym() or member.islnk()) and _link_target_is_forbidden(member.linkname):
                    forbidden.append(f'{name} -> {member.linkname}')
                if member.isfile() and member.size >= LARGE_ARCHIVE_MEMBER_THRESHOLD_BYTES:
                    project_path = _archive_project_path(name)
                    lfs_tracked = _matches_lfs_pattern(project_path, lfs_patterns)
                    large_members.append(
                        {
                            'path': name,
                            'project_path': project_path,
                            'bytes': member.size,
                            'lfs_tracked': lfs_tracked,
                        }
                    )
                    if not lfs_tracked:
                        large_untracked.append(name)
    except (tarfile.TarError, EOFError) as exc:
        return {
            'status': 'invalid',
            'path': str(archive_path),
            'forbidden': [str(exc)],
            'sha256': sha256,
            'bytes': byte_count,
            'large_members': [],
            'large_member_count': 0,
            'large_untracked': [],
            'lfs_patterns': lfs_patterns,
        }
    return {
        'status': 'passed' if not forbidden and not large_untracked else 'failed',
        'path': str(archive_path),
        'forbidden': forbidden[:20],
        'sha256': sha256,
        'bytes': byte_count,
        'large_members': large_members[:20],
        'large_member_count': len(large_members),
        'large_untracked': large_untracked[:20],
        'large_member_threshold_bytes': LARGE_ARCHIVE_MEMBER_THRESHOLD_BYTES,
        'lfs_patterns': lfs_patterns,
    }


def _archive_identity(source_archive: dict[str, Any]) -> str:
    sha256 = str(source_archive.get('sha256') or '')
    byte_count = int(source_archive.get('bytes') or 0)
    details: list[str] = []
    if source_archive.get('status'):
        details.append(str(source_archive.get('status')))
    if sha256:
        details.append(f'sha256: {sha256}')
    if byte_count:
        details.append(f'bytes: {byte_count}')
    large_member_count = int(source_archive.get('large_member_count') or 0)
    if large_member_count:
        large_untracked_count = len(source_archive.get('large_untracked') or [])
        details.append(f'large files: {large_member_count} ({large_untracked_count} not LFS-tracked)')
    return '; '.join(details) if details else 'missing'


def inspect_beta_tester_onboarding(
    path: pathlib.Path | None = None,
    link_sources: tuple[pathlib.Path, ...] | None = None,
) -> dict[str, Any]:
    onboarding_path = _resolve_repo_path(path or DEFAULT_BETA_TESTER_ONBOARDING)
    sources = tuple(link_sources or DEFAULT_BETA_TESTER_LINK_SOURCES)
    link_target = _relative_or_absolute(str(onboarding_path))
    missing_link_sources: list[str] = []
    linked_from: list[str] = []

    if not onboarding_path.exists():
        return {
            'status': 'missing',
            'path': str(onboarding_path),
            'linked_from': [],
            'missing_link_sources': [_relative_or_absolute(str(source)) for source in sources],
        }

    for source in sources:
        resolved_source = _resolve_repo_path(source)
        source_label = _relative_or_absolute(str(resolved_source))
        if not resolved_source.exists():
            missing_link_sources.append(source_label)
            continue
        source_text = resolved_source.read_text(encoding='utf-8')
        if link_target in source_text or onboarding_path.name in source_text:
            linked_from.append(source_label)
        else:
            missing_link_sources.append(source_label)

    return {
        'status': 'passed' if not missing_link_sources else 'incomplete',
        'path': str(onboarding_path),
        'linked_from': linked_from,
        'missing_link_sources': missing_link_sources,
    }


def _command_index(
    evidence: dict[str, Any],
    source_archive: dict[str, Any],
    beta_tester_onboarding: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    commands = {
        str(command.get('label') or ''): command
        for command in evidence.get('commands', [])
        if isinstance(command, dict) and command.get('label')
    }
    commands['Source archive clean'] = {
        'label': 'Source archive clean',
        'status': 'passed' if source_archive.get('status') == 'passed' else 'failed',
        'returncode': 0 if source_archive.get('status') == 'passed' else 1,
        'duration_seconds': None,
        'command': f"make source-archive; scan {source_archive.get('path') or 'latest source archive'}",
    }
    onboarding_status = beta_tester_onboarding.get('status')
    commands['Beta tester onboarding linked'] = {
        'label': 'Beta tester onboarding linked',
        'status': 'passed' if onboarding_status == 'passed' else 'failed',
        'returncode': 0 if onboarding_status == 'passed' else 1,
        'duration_seconds': None,
        'command': (
            f"check {beta_tester_onboarding.get('path') or DEFAULT_BETA_TESTER_ONBOARDING} "
            'and README/runbook links'
        ),
    }
    return commands


def _criterion_status(labels: tuple[str, ...], commands: dict[str, dict[str, Any]]) -> tuple[str, str]:
    missing = [label for label in labels if label not in commands]
    if missing:
        return 'missing', 'Missing gate evidence: ' + ', '.join(missing)
    failed = [
        label
        for label in labels
        if commands[label].get('status') != 'passed' or commands[label].get('returncode') not in (0, None)
    ]
    if failed:
        return 'failed', 'Failed gate evidence: ' + ', '.join(failed)
    return 'passed', ', '.join(labels)


def _overall_issue_status(spec: IssueEvidenceSpec, commands: dict[str, dict[str, Any]]) -> str:
    statuses = [_criterion_status(labels, commands)[0] for _, labels in spec.criteria]
    if 'failed' in statuses:
        return 'failed'
    if 'missing' in statuses:
        return 'incomplete'
    return 'passed'


def _hosted_check_passed(hosted_rc_evidence: dict[str, Any] | None, label: str) -> bool:
    if not hosted_rc_evidence:
        return False
    check = (hosted_rc_evidence.get('checks') or {}).get(label)
    return isinstance(check, dict) and check.get('status') == 'passed'


def _hosted_manual_provided(hosted_rc_evidence: dict[str, Any] | None, label: str) -> bool:
    if not hosted_rc_evidence:
        return False
    item = (hosted_rc_evidence.get('manual_evidence') or {}).get(label)
    return isinstance(item, dict) and item.get('status') == 'provided' and bool(str(item.get('evidence') or '').strip())


def _remove_exception(exceptions: list[str], value: str) -> None:
    try:
        exceptions.remove(value)
    except ValueError:
        pass


def _apply_hosted_rc_exception_evidence(
    spec: IssueEvidenceSpec,
    remaining_exceptions: list[str],
    hosted_rc_evidence: dict[str, Any] | None,
    *,
    security_forbidden: dict[str, Any] | None = None,
    export_import: dict[str, Any] | None = None,
) -> None:
    if not hosted_rc_evidence or hosted_rc_evidence.get('status') in UNUSABLE_HOSTED_RC_STATUSES:
        return
    if _hosted_check_passed(hosted_rc_evidence, 'Hosted deployment readiness'):
        if spec.issue_number == 3:
            _remove_exception(remaining_exceptions, HOSTED_HEALTH_EXCEPTION)
        elif spec.issue_number == 5:
            _remove_exception(remaining_exceptions, HOSTED_DEPLOYMENT_READINESS_EXCEPTION)
        elif spec.issue_number == 8:
            _remove_exception(remaining_exceptions, HOSTED_METRICS_EXCEPTION)
    if spec.issue_number == 5 and (
        _hosted_check_passed(hosted_rc_evidence, 'Hosted non-admin forbidden smoke')
        or (security_forbidden and security_forbidden.get('status') == 'passed' and security_forbidden.get('mode') == 'live-target')
    ):
        _remove_exception(remaining_exceptions, HOSTED_FORBIDDEN_EXCEPTION)
    if spec.issue_number == 6:
        if _hosted_manual_provided(hosted_rc_evidence, HOSTED_BACKUP_RESTORE_LABEL):
            _remove_exception(remaining_exceptions, HOSTED_BACKUP_RESTORE_EXCEPTION)
        if _hosted_check_passed(hosted_rc_evidence, 'Hosted session export/import smoke') or (
            export_import and export_import.get('status') == 'passed' and export_import.get('mode') == 'live-target'
        ):
            _remove_exception(remaining_exceptions, HOSTED_EXPORT_IMPORT_EXCEPTION)
    if spec.issue_number == 7:
        worker_proof = _hosted_manual_provided(hosted_rc_evidence, HOSTED_WORKER_PROCESS_LABEL)
        worker_model = str(hosted_rc_evidence.get('socketio_worker_model') or '').strip()
        if worker_proof:
            _remove_exception(remaining_exceptions, HOSTED_WORKER_PROCESS_EXCEPTION)
        if worker_proof and worker_model == 'single':
            _remove_exception(remaining_exceptions, HOSTED_SINGLE_WORKER_POLICY_EXCEPTION)
    if spec.issue_number == 8 and _hosted_check_passed(hosted_rc_evidence, 'Hosted beta SLO baseline'):
        _remove_exception(remaining_exceptions, HOSTED_SLO_EXCEPTION)
    if spec.issue_number == 9 and _hosted_manual_provided(hosted_rc_evidence, SOURCE_ARCHIVE_ATTACHMENT_LABEL):
        _remove_exception(remaining_exceptions, SOURCE_ARCHIVE_ATTACHMENT_EXCEPTION)


def _relative_or_absolute(path: str) -> str:
    if not path:
        return ''
    candidate = pathlib.Path(path)
    try:
        return str(candidate.relative_to(REPO_ROOT))
    except ValueError:
        return str(candidate)


def render_issue(
    spec: IssueEvidenceSpec,
    *,
    evidence: dict[str, Any],
    evidence_report_path: pathlib.Path,
    source_archive: dict[str, Any],
    visual_smoke: dict[str, Any] | None = None,
    visual_smoke_review: dict[str, Any] | None = None,
    github_actions: dict[str, Any] | None = None,
    hosted_rc_evidence: dict[str, Any] | None = None,
    security_forbidden: dict[str, Any] | None = None,
    export_import: dict[str, Any] | None = None,
    beta_tester_onboarding: dict[str, Any] | None = None,
) -> str:
    beta_tester_onboarding = beta_tester_onboarding or inspect_beta_tester_onboarding()
    commands = _command_index(evidence, source_archive, beta_tester_onboarding)
    local_status = _overall_issue_status(spec, commands)
    remaining_exceptions = list(spec.external_exceptions)
    if spec.issue_number == 3 and github_actions and github_actions.get('status') == 'passed':
        remaining_exceptions = [
            exception for exception in remaining_exceptions if exception != GITHUB_ACTIONS_RUN_URL_EXCEPTION
        ]
    _apply_hosted_rc_exception_evidence(
        spec,
        remaining_exceptions,
        hosted_rc_evidence,
        security_forbidden=security_forbidden,
        export_import=export_import,
    )
    if source_archive.get('status') == 'failed':
        remaining_exceptions.append(
            'Source archive scan found forbidden paths: ' + ', '.join(source_archive.get('forbidden') or [])
        )
    elif source_archive.get('status') in {'missing', 'invalid'} and spec.issue_number == 9:
        remaining_exceptions.append('Source archive evidence is missing or invalid.')
    if spec.issue_number == 9 and beta_tester_onboarding.get('status') != 'passed':
        missing_links = ', '.join(beta_tester_onboarding.get('missing_link_sources') or [])
        if missing_links:
            remaining_exceptions.append(f'Beta tester onboarding guide is not linked from: {missing_links}.')
        else:
            remaining_exceptions.append('Beta tester onboarding guide is missing.')

    decision = (
        'Local RC evidence is ready; close only after the remaining external exceptions are attached.'
        if remaining_exceptions
        else 'Local RC evidence satisfies this gate.'
    )
    result = local_status if local_status != 'passed' else ('passed with external exceptions' if remaining_exceptions else 'passed')
    command_run = 'scripts/closed_beta_rc_check.py --evidence-report ' + _relative_or_absolute(str(evidence_report_path))

    rows = [
        '| Criterion | Evidence | Status |',
        '| --- | --- | --- |',
    ]
    for criterion, labels in spec.criteria:
        status, evidence_text = _criterion_status(labels, commands)
        rows.append(f'| {criterion} | {evidence_text} | {status} |')

    exceptions = remaining_exceptions or ['None.']
    artifact_lines: list[str] = []
    issue_comment_artifact_lines: list[str] = []
    if spec.issue_number == 3 and github_actions:
        github_actions_path = _relative_or_absolute(str(github_actions.get('path') or '')) or 'not found'
        artifact_status = github_actions.get('closed_beta_rc_artifact_status') or 'not-checked'
        github_actions_line = (
            f"- GitHub Actions evidence: `{github_actions_path}` "
            f"({github_actions.get('status')}; AIDM CI: "
            f"{github_actions.get('aidm_ci_run_url') or 'missing'}; Closed Beta RC: "
            f"{github_actions.get('closed_beta_rc_run_url') or 'missing'}; "
            f"artifact: {artifact_status})"
        )
        artifact_lines.append(github_actions_line)
        issue_comment_artifact_lines.append(github_actions_line)
    if hosted_rc_evidence and spec.issue_number in {3, 5, 6, 7, 8, 9}:
        hosted_path = _relative_or_absolute(str(hosted_rc_evidence.get('path') or '')) or 'not found'
        hosted_checks = hosted_rc_evidence.get('checks') or {}
        manual_required = ', '.join(hosted_rc_evidence.get('manual_required') or []) or 'none'
        hosted_line = (
            f"- Hosted RC evidence: `{hosted_path}` "
            f"({hosted_rc_evidence.get('status')}; checks: {len(hosted_checks)}; "
            f"manual required: {manual_required})"
        )
        artifact_lines.append(hosted_line)
        issue_comment_artifact_lines.append(hosted_line)
    if spec.issue_number == 4 and visual_smoke:
        smoke_path = _relative_or_absolute(str(visual_smoke.get('path') or '')) or 'not found'
        screenshots = ', '.join(visual_smoke.get('screenshots') or []) or 'none'
        visual_line = f"- Visual smoke artifacts: `{smoke_path}` ({visual_smoke.get('status')}; screenshots: {screenshots})"
        artifact_lines.append(visual_line)
        issue_comment_artifact_lines.append(visual_line)
    if spec.issue_number == 4 and visual_smoke_review:
        review_path = _relative_or_absolute(str(visual_smoke_review.get('path') or '')) or 'not found'
        review_line = (
            f"- Visual smoke review: `{review_path}` "
            f"({visual_smoke_review.get('status')}; screenshots: "
            f"{visual_smoke_review.get('screenshots') or 'unknown'}; "
            f"failures: {visual_smoke_review.get('failures') or 'unknown'})"
        )
        artifact_lines.append(review_line)
        issue_comment_artifact_lines.append(review_line)
    if spec.issue_number == 5 and security_forbidden:
        security_path = _relative_or_absolute(str(security_forbidden.get('path') or '')) or 'not found'
        security_line = (
            f"- Security forbidden evidence: `{security_path}` "
            f"({security_forbidden.get('status')}; mode: {security_forbidden.get('mode') or 'unknown'})"
        )
        artifact_lines.append(security_line)
        issue_comment_artifact_lines.append(security_line)
    if spec.issue_number == 6 and export_import:
        export_import_path = _relative_or_absolute(str(export_import.get('path') or '')) or 'not found'
        export_import_line = (
            f"- Export/import evidence: `{export_import_path}` "
            f"({export_import.get('status')}; mode: {export_import.get('mode') or 'unknown'})"
        )
        artifact_lines.append(export_import_line)
        issue_comment_artifact_lines.append(export_import_line)
    if spec.issue_number == 9:
        onboarding_path = _relative_or_absolute(str(beta_tester_onboarding.get('path') or '')) or 'not found'
        linked_from = ', '.join(beta_tester_onboarding.get('linked_from') or []) or 'none'
        onboarding_line = (
            f"- Beta tester onboarding: `{onboarding_path}` "
            f"({beta_tester_onboarding.get('status')}; linked from: {linked_from})"
        )
        artifact_lines.append(onboarding_line)
        issue_comment_artifact_lines.append(onboarding_line)
    return '\n'.join(
        [
            f'# {spec.title}',
            '',
            'Gate evidence:',
            '',
            f'- Issue: #{spec.issue_number}',
            f'- Gate: {spec.gate}',
            f'- Command run: `{command_run}`',
            f'- Result: {result}',
            f"- Environment: repo `{evidence.get('repo_root') or REPO_ROOT}`, python `{evidence.get('python') or 'unknown'}`",
            f"- Commit SHA: {evidence.get('commit') or 'unknown'}",
            f"- Worktree: {evidence.get('worktree') or evidence.get('git_worktree', {}).get('state') or 'unknown'}",
            f'- Evidence/log path: `{_relative_or_absolute(str(evidence_report_path))}`',
            f"- Date/time UTC: {evidence.get('finished_at') or 'unknown'}",
            f"- Source archive: `{_relative_or_absolute(str(source_archive.get('path') or '')) or 'not found'}` ({_archive_identity(source_archive)})",
            *artifact_lines,
            '- Remaining exceptions: ' + ('; '.join(exceptions)),
            f'- Decision: {decision}',
            '',
            '## Acceptance Criteria',
            '',
            *rows,
            '',
            '## Issue Comment',
            '',
            '```markdown',
            'Gate evidence:',
            '',
            f'- Command run: `{command_run}`',
            f'- Result: {result}',
            f"- Environment: repo `{evidence.get('repo_root') or REPO_ROOT}`, python `{evidence.get('python') or 'unknown'}`",
            f"- Commit SHA: {evidence.get('commit') or 'unknown'}",
            f"- Worktree: {evidence.get('worktree') or evidence.get('git_worktree', {}).get('state') or 'unknown'}",
            f'- Evidence/log path: `{_relative_or_absolute(str(evidence_report_path))}`',
            f"- Source archive: `{_relative_or_absolute(str(source_archive.get('path') or '')) or 'not found'}` ({_archive_identity(source_archive)})",
            *issue_comment_artifact_lines,
            '- Remaining exceptions: ' + ('; '.join(exceptions)),
            f'- Decision: {decision}',
            '```',
            '',
        ]
    )


def render_all_issue_evidence(
    *,
    evidence_report_path: pathlib.Path,
    output_dir: pathlib.Path,
    source_archive_path: pathlib.Path | None = None,
    visual_smoke_dir: pathlib.Path | None = None,
    visual_smoke_review_path: pathlib.Path | None = None,
    github_actions_evidence_path: pathlib.Path | None = None,
    hosted_rc_evidence_path: pathlib.Path | None = None,
    security_forbidden_evidence_path: pathlib.Path | None = None,
    export_import_evidence_path: pathlib.Path | None = None,
) -> list[pathlib.Path]:
    resolved_report_path = _resolve_repo_path(evidence_report_path)
    resolved_output_dir = _resolve_repo_path(output_dir)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    evidence = load_evidence(resolved_report_path)
    source_archive = inspect_source_archive(source_archive_path)
    visual_smoke = inspect_visual_smoke(visual_smoke_dir)
    visual_smoke_review = inspect_visual_smoke_review(visual_smoke_review_path)
    github_actions = inspect_github_actions_evidence(github_actions_evidence_path)
    hosted_rc_evidence = inspect_hosted_rc_evidence(hosted_rc_evidence_path)
    security_forbidden = inspect_security_forbidden_evidence(security_forbidden_evidence_path)
    export_import = inspect_export_import_evidence(export_import_evidence_path)
    beta_tester_onboarding = inspect_beta_tester_onboarding()
    written: list[pathlib.Path] = []
    for spec in ISSUE_SPECS:
        output_path = resolved_output_dir / f'issue-{spec.issue_number:02d}-{spec.slug}.md'
        output_path.write_text(
            render_issue(
                spec,
                evidence=evidence,
                evidence_report_path=resolved_report_path,
                source_archive=source_archive,
                visual_smoke=visual_smoke,
                visual_smoke_review=visual_smoke_review,
                github_actions=github_actions,
                hosted_rc_evidence=hosted_rc_evidence,
                security_forbidden=security_forbidden,
                export_import=export_import,
                beta_tester_onboarding=beta_tester_onboarding,
            ),
            encoding='utf-8',
        )
        written.append(output_path)
    return written


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Render RC gate evidence snippets for GitHub issues #3-#9.')
    parser.add_argument(
        '--evidence-report',
        type=pathlib.Path,
        default=DEFAULT_EVIDENCE_REPORT,
        help='Markdown or JSON report produced by scripts/closed_beta_rc_check.py.',
    )
    parser.add_argument(
        '--output-dir',
        type=pathlib.Path,
        default=DEFAULT_OUTPUT_DIR,
        help='Directory for rendered issue evidence Markdown files.',
    )
    parser.add_argument(
        '--source-archive',
        type=pathlib.Path,
        default=None,
        help='Source archive to verify for packaging evidence. Defaults to the newest tmp/release/aidm-source-*.tar.gz.',
    )
    parser.add_argument(
        '--visual-smoke-dir',
        type=pathlib.Path,
        default=None,
        help='Visual-smoke screenshot directory to reference for frontend evidence. Defaults to the newest visual-smoke run.',
    )
    parser.add_argument(
        '--visual-smoke-review',
        type=pathlib.Path,
        default=None,
        help='Visual-smoke artifact review evidence report. Defaults to tmp/release/visual-smoke-review.md.',
    )
    parser.add_argument(
        '--github-actions-evidence',
        type=pathlib.Path,
        default=None,
        help='GitHub Actions run URL evidence report. Defaults to tmp/release/github-actions-evidence.md.',
    )
    parser.add_argument(
        '--hosted-rc-evidence',
        type=pathlib.Path,
        default=None,
        help='Hosted/staging RC evidence report. Defaults to tmp/release/hosted-rc-evidence.md.',
    )
    parser.add_argument(
        '--security-forbidden-evidence',
        type=pathlib.Path,
        default=None,
        help='Security forbidden-response evidence report to reference. Defaults to tmp/release/security-forbidden-evidence.md.',
    )
    parser.add_argument(
        '--export-import-evidence',
        type=pathlib.Path,
        default=None,
        help='Session export/import smoke evidence report to reference. Defaults to tmp/release/export-import-evidence.md.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    evidence_report_path = _resolve_repo_path(args.evidence_report)
    if not evidence_report_path.exists():
        print(f'[rc-issue-evidence][error] Evidence report not found: {evidence_report_path}')
        return 2
    written = render_all_issue_evidence(
        evidence_report_path=evidence_report_path,
        output_dir=args.output_dir,
        source_archive_path=args.source_archive,
        visual_smoke_dir=args.visual_smoke_dir,
        visual_smoke_review_path=args.visual_smoke_review,
        github_actions_evidence_path=args.github_actions_evidence,
        hosted_rc_evidence_path=args.hosted_rc_evidence,
        security_forbidden_evidence_path=args.security_forbidden_evidence,
        export_import_evidence_path=args.export_import_evidence,
    )
    for path in written:
        print(f'[rc-issue-evidence] Wrote {path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
