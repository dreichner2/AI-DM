from __future__ import annotations

import argparse
from collections import Counter
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from urllib.parse import urlencode, urlsplit

import requests


DEFAULT_OUTPUT = Path('tmp/release/beta-slo-baseline.md')
DEFAULT_TARGET_URL = 'http://127.0.0.1:5050'
REPO_ROOT = Path(__file__).resolve().parents[1]


def _first_csv_value(value: str | None) -> str:
    return next((item.strip() for item in str(value or '').split(',') if item.strip()), '')


def _normalize_base_url(value: str) -> str:
    return value.strip().rstrip('/')


def _valid_http_target_url(value: str) -> bool:
    if not value or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlsplit(value)
        _ = parsed.port
    except ValueError:
        return False
    return parsed.scheme in {'http', 'https'} and bool(parsed.netloc and parsed.hostname)


def resolve_commit_sha(value: str = '') -> str:
    explicit = value.strip()
    if explicit:
        return explicit
    github_sha = os.getenv('GITHUB_SHA', '').strip()
    if github_sha:
        return github_sha
    try:
        return subprocess.run(
            ['git', 'rev-parse', 'HEAD'],
            cwd=REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ''


def _valid_commit_sha(value: str) -> bool:
    normalized = value.strip().lower()
    return 7 <= len(normalized) <= 64 and all(character in '0123456789abcdef' for character in normalized)


def _json_file(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f'Could not read {label} JSON from {path}: {exc}') from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f'{label} JSON must be an object.')
    return payload


def _headers(*, auth_token: str, workspace_id: str, workspace_token: str) -> dict[str, str]:
    headers = {'Accept': 'application/json'}
    if auth_token.strip():
        headers['Authorization'] = f'Bearer {auth_token.strip()}'
    if workspace_token.strip():
        headers['X-AIDM-Workspace-Token'] = workspace_token.strip()
    elif workspace_id.strip():
        headers['X-AIDM-Workspace-Id'] = workspace_id.strip()
    return headers


def _response_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ('error', 'message', 'error_code'):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return response.text.strip() or f'HTTP {response.status_code}'


def fetch_target_json(
    target_url: str,
    *,
    path: str,
    auth_token: str,
    workspace_id: str,
    workspace_token: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    url = f'{_normalize_base_url(target_url)}/{path.lstrip("/")}'
    response = requests.get(
        url,
        headers=_headers(auth_token=auth_token, workspace_id=workspace_id, workspace_token=workspace_token),
        timeout=timeout_seconds,
    )
    if response.status_code >= 400:
        raise RuntimeError(f'GET {url} failed with {response.status_code}: {_response_error(response)}')
    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(f'GET {url} did not return JSON.') from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f'GET {url} returned JSON {type(payload).__name__}, expected object.')
    return payload


def fetch_target_baseline(
    *,
    target_url: str,
    auth_token: str,
    workspace_id: str,
    workspace_token: str,
    limit: int,
    timeout_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    slo = fetch_target_json(
        target_url,
        path='/api/beta/slo',
        auth_token=auth_token,
        workspace_id=workspace_id,
        workspace_token=workspace_token,
        timeout_seconds=timeout_seconds,
    )
    incident_path = '/api/beta/incidents?' + urlencode({'limit': str(limit)})
    incidents = fetch_target_json(
        target_url,
        path=incident_path,
        auth_token=auth_token,
        workspace_id=workspace_id,
        workspace_token=workspace_token,
        timeout_seconds=timeout_seconds,
    )
    return slo, incidents


def _fmt_value(value: Any) -> str:
    if value is None or value == '':
        return 'not available'
    if isinstance(value, float):
        return f'{value:.3f}'.rstrip('0').rstrip('.')
    return str(value)


def _fmt_rate(value: Any) -> str:
    if value is None or value == '':
        return 'not available'
    try:
        return f'{float(value) * 100:.2f}%'
    except (TypeError, ValueError):
        return str(value)


def _fmt_latency(value: Any) -> str:
    if value is None or value == '':
        return 'not available'
    try:
        return f'{float(value):.0f} ms'
    except (TypeError, ValueError):
        return str(value)


def _metric_rows(slo: dict[str, Any], incidents: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    bad_turn_summary = _bad_turn_reports_summary(incidents)
    return [
        ('DM response p95 latency', _fmt_latency(slo.get('dm_response_latency_ms_p95')), '/api/beta/slo', ''),
        ('DM response sample count', _fmt_value(slo.get('dm_response_latency_sample_count')), '/api/beta/slo', ''),
        ('AI provider failure rate', _fmt_rate(slo.get('ai_provider_failure_rate')), '/api/beta/slo', ''),
        ('Canon job failure rate', _fmt_rate(slo.get('canon_job_failure_rate')), '/api/beta/slo', ''),
        ('Turn persistence failure rate', _fmt_rate(slo.get('turn_persistence_failure_rate')), '/api/beta/slo', ''),
        ('Socket unauthorized events', _fmt_value(slo.get('socket_unauthorized_event_count')), '/api/beta/slo', ''),
        ('Socket rate-limited events', _fmt_value(slo.get('socket_rate_limited_event_count')), '/api/beta/slo', ''),
        ('Average coherence feedback score', _fmt_value(slo.get('coherence_feedback_avg')), '/api/beta/slo', ''),
        ('Bad-turn reports by provider/model', bad_turn_summary, '/api/beta/incidents', ''),
    ]


def _bad_turn_reports_summary(incidents: dict[str, Any]) -> str:
    counter: Counter[str] = Counter()
    for item in incidents.get('incidents') or []:
        if not isinstance(item, dict) or item.get('type') != 'bad_turn_report':
            continue
        provider = str(item.get('provider') or 'unknown')
        model = str(item.get('model') or 'unknown')
        counter[f'{provider}/{model}'] += 1
    if not counter:
        summary = incidents.get('summary') if isinstance(incidents.get('summary'), dict) else {}
        count = summary.get('bad_turn_report_count')
        return f'{count} total' if count else '0'
    return ', '.join(f'{label}: {count}' for label, count in sorted(counter.items()))


def _provider_rows(slo: dict[str, Any]) -> list[str]:
    rows = ['| Provider | Model | Turns |', '| --- | --- | ---: |']
    values = [item for item in (slo.get('provider_model_turn_counts') or []) if isinstance(item, dict)]
    if not values:
        rows.append('| not available | not available | 0 |')
        return rows
    for item in values:
        rows.append(f"| {item.get('provider') or 'unknown'} | {item.get('model') or 'unknown'} | {item.get('turn_count') or 0} |")
    return rows


def _incident_rows(incidents: dict[str, Any]) -> list[str]:
    rows = ['| Session | Turn | Category | Provider/model | Status | Owner | Link/evidence |', '| --- | --- | --- | --- | --- | --- | --- |']
    values = [item for item in (incidents.get('incidents') or []) if isinstance(item, dict)]
    if not values:
        rows.append('|  |  | none reported |  |  |  |  |')
        return rows
    for item in values:
        provider_model = '/'.join(str(value) for value in (item.get('provider'), item.get('model')) if value) or ''
        status = str(item.get('status') or item.get('severity') or '')
        rows.append(
            '| '
            + ' | '.join(
                [
                    _fmt_value(item.get('session_id')),
                    _fmt_value(item.get('turn_id')),
                    str(item.get('category') or item.get('type') or ''),
                    provider_model,
                    status,
                    '',
                    '',
                ]
            )
            + ' |'
        )
    return rows


def render_baseline(
    *,
    slo: dict[str, Any],
    incidents: dict[str, Any],
    generated_at: str,
    release: str,
    commit_sha: str,
    environment: str,
    target_url: str,
    socketio_worker_model: str,
    database: str,
    llm_provider_model: str,
    observability_provider: str,
    alert_owner: str,
    evidence_report: str,
    invite_more_testers: str = 'yes/no',
) -> str:
    metric_rows = ['| Metric | Value | Source | Decision |', '| --- | ---: | --- | --- |']
    metric_rows.extend(f'| {metric} | {value} | `{source}` | {decision} |' for metric, value, source, decision in _metric_rows(slo, incidents))
    lines = [
        '# Beta SLO Baseline',
        '',
        f'- Generated: {generated_at}',
        '',
        '## Release Context',
        '',
        f'- RC or release: {release or ""}',
        f'- Commit SHA: {commit_sha or ""}',
        f'- Environment: {environment or ""}',
        f'- Target URL: {target_url or ""}',
        f'- Socket.IO worker model: {socketio_worker_model or ""}',
        f'- Database: {database or ""}',
        f'- LLM provider/model: {llm_provider_model or ""}',
        f'- Observability provider: {observability_provider or ""}',
        f'- Alert owner: {alert_owner or ""}',
        f'- Evidence report: {evidence_report or ""}',
        '',
        '## Baseline Metrics',
        '',
        *metric_rows,
        '',
        '## Provider/Model Turn Mix',
        '',
        *_provider_rows(slo),
        '',
        '## Incident Review',
        '',
        *_incident_rows(incidents),
        '',
        '## Gate Decision',
        '',
        f'- Invite more testers: {invite_more_testers or "yes/no"}',
        '- Reasons:',
        '- Exceptions:',
        '- Follow-up issues:',
        '- Next review date:',
        '',
    ]
    return '\n'.join(lines)


def write_baseline(markdown: str, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(markdown, encoding='utf-8')
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Render a beta SLO baseline from /api/beta/slo and /api/beta/incidents evidence.')
    parser.add_argument('--target-url', default=os.getenv('AIDM_API_BASE_URL') or '', help='Backend base URL to fetch live beta SLO evidence.')
    parser.add_argument('--auth-token', default=os.getenv('AIDM_API_AUTH_TOKEN') or _first_csv_value(os.getenv('AIDM_API_AUTH_TOKENS')), help='Bearer token for hosted/operator incident evidence.')
    parser.add_argument('--workspace-id', default=os.getenv('AIDM_WORKSPACE_ID') or '', help='Workspace id header for account-token or bearer-token requests.')
    parser.add_argument('--workspace-token', default=os.getenv('AIDM_WORKSPACE_TOKEN') or '', help='Workspace token header. Takes precedence over --workspace-id.')
    parser.add_argument('--slo-json', type=Path, default=None, help='Saved /api/beta/slo JSON to render instead of fetching it.')
    parser.add_argument('--incidents-json', type=Path, default=None, help='Saved /api/beta/incidents JSON to render instead of fetching it.')
    parser.add_argument('--limit', type=int, default=25, help='Incident fetch limit. Default: 25.')
    parser.add_argument('--timeout-seconds', type=float, default=15.0, help='HTTP request timeout. Default: 15.')
    parser.add_argument('--output', type=Path, default=DEFAULT_OUTPUT, help=f'Markdown output path. Default: {DEFAULT_OUTPUT}.')
    parser.add_argument('--release', default='', help='RC or release label to render.')
    parser.add_argument('--commit-sha', default='', help='Commit SHA to render.')
    parser.add_argument('--environment', default='', help='Environment name to render.')
    parser.add_argument('--socketio-worker-model', default='', help='Socket.IO worker model to render.')
    parser.add_argument('--database', default='', help='Database target/details to render.')
    parser.add_argument('--llm-provider-model', default='', help='LLM provider/model to render.')
    parser.add_argument('--observability-provider', default='', help='Observability provider to render.')
    parser.add_argument('--alert-owner', default='', help='Alert owner to render.')
    parser.add_argument('--evidence-report', default='', help='Related evidence report path or URL to render.')
    parser.add_argument(
        '--invite-more-testers',
        choices=('yes', 'no'),
        default='',
        help='Explicit tester-expansion decision. Omit to leave the generated artifact incomplete for operator review.',
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.limit < 1:
        print('--limit must be positive.', file=sys.stderr)
        return 2
    if not args.target_url:
        print('--target-url is required so the evidence is bound to its source environment.', file=sys.stderr)
        return 2
    if not _valid_http_target_url(args.target_url):
        print('--target-url must be an absolute http(s) URL.', file=sys.stderr)
        return 2
    commit_sha = resolve_commit_sha(args.commit_sha)
    if not _valid_commit_sha(commit_sha):
        print('--commit-sha must resolve to a 7-64 character hexadecimal Git SHA.', file=sys.stderr)
        return 2
    try:
        if args.slo_json:
            slo = _json_file(args.slo_json, label='SLO')
            incidents = _json_file(args.incidents_json, label='incidents') if args.incidents_json else {}
            target_url = args.target_url
        else:
            target_url = args.target_url or DEFAULT_TARGET_URL
            slo, incidents = fetch_target_baseline(
                target_url=target_url,
                auth_token=args.auth_token,
                workspace_id=args.workspace_id,
                workspace_token=args.workspace_token,
                limit=args.limit,
                timeout_seconds=args.timeout_seconds,
            )
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    markdown = render_baseline(
        slo=slo,
        incidents=incidents,
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        release=args.release,
        commit_sha=commit_sha,
        environment=args.environment,
        target_url=target_url,
        socketio_worker_model=args.socketio_worker_model,
        database=args.database,
        llm_provider_model=args.llm_provider_model,
        observability_provider=args.observability_provider,
        alert_owner=args.alert_owner,
        evidence_report=args.evidence_report,
        invite_more_testers=args.invite_more_testers,
    )
    output_path = write_baseline(markdown, args.output)
    print(f'Wrote beta SLO baseline: {output_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
