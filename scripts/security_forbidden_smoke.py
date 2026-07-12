from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from urllib.parse import urljoin

import requests


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_EVIDENCE_REPORT = REPO_ROOT / 'tmp' / 'release' / 'security-forbidden-evidence.md'
DEFAULT_WORKSPACE_ID = 'security-forbidden-smoke'
DEFAULT_ACCOUNT_TOKEN = 'security-forbidden-smoke-player-token'
REDACTED_VALUE = '<redacted>'
SENSITIVE_TEXT_PATTERNS = (
    re.compile(r'(Authorization\s*[:=]\s*Bearer\s+)[^,\s}"\']+', re.IGNORECASE),
    re.compile(r'(Bearer\s+)[A-Za-z0-9._~+/=-]{8,}', re.IGNORECASE),
    re.compile(r'("?(?:account_token|auth_token|password|token)"?\s*[:=]\s*"?)[^,"\s}]+("? )?', re.IGNORECASE),
)


@dataclass(frozen=True)
class SeededForbiddenRuntime:
    workspace_id: str
    account_token: str
    campaign_id: int
    session_id: int


@dataclass(frozen=True)
class ForbiddenCheckSpec:
    label: str
    method: str
    path_template: str
    expected_capability: str
    payload: dict | None = None


@dataclass(frozen=True)
class ForbiddenCheckResult:
    label: str
    method: str
    path: str
    expected_capability: str
    status_code: int
    error_code: str
    required_capability: str
    ok: bool
    response_excerpt: str


class RequestsHttpClient:
    def __init__(self, base_url: str, *, timeout_seconds: float):
        self.base_url = base_url.rstrip('/') + '/'
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()

    def request(self, method: str, path: str, *, headers: dict[str, str], json_payload: dict | None):
        return self.session.request(
            method,
            urljoin(self.base_url, path.lstrip('/')),
            headers=headers,
            json=json_payload,
            timeout=self.timeout_seconds,
        )


class FlaskTestHttpClient:
    def __init__(self, client):
        self.client = client

    def request(self, method: str, path: str, *, headers: dict[str, str], json_payload: dict | None):
        return self.client.open(path, method=method, headers=headers, json=json_payload)


def check_specs(*, campaign_id: int, session_id: int) -> list[ForbiddenCheckSpec]:
    return [
        ForbiddenCheckSpec(
            'Combat start',
            'POST',
            f'/api/sessions/{session_id}/combat/start',
            'dm_runtime_control',
            {'creature': {'id': 'wolf', 'name': 'Wolf'}, 'enemyCount': 1},
        ),
        ForbiddenCheckSpec(
            'Combat plan enemy intents',
            'POST',
            f'/api/sessions/{session_id}/combat/plan-enemy-intents',
            'dm_runtime_control',
            {},
        ),
        ForbiddenCheckSpec(
            'Combat morale event',
            'POST',
            f'/api/sessions/{session_id}/combat/apply-morale-event',
            'dm_runtime_control',
            {'participantId': 'enemy_wolf_1', 'event': 'took_heavy_damage'},
        ),
        ForbiddenCheckSpec(
            'Combat apply state changes',
            'POST',
            f'/api/sessions/{session_id}/combat/apply-state-changes',
            'dm_runtime_control',
            {'changes': []},
        ),
        ForbiddenCheckSpec(
            'Combat check end apply',
            'POST',
            f'/api/sessions/{session_id}/combat/check-end',
            'dm_runtime_control',
            {'apply': True},
        ),
        ForbiddenCheckSpec(
            'Combat debug log',
            'GET',
            f'/api/sessions/{session_id}/combat/debug',
            'dm_runtime_control',
        ),
        ForbiddenCheckSpec(
            'Campaign bestiary create',
            'POST',
            f'/api/campaigns/{campaign_id}/bestiary',
            'dm_authoring',
            {'creature': {'id': 'wolf', 'name': 'Wolf'}},
        ),
        ForbiddenCheckSpec(
            'Campaign bestiary generate pack',
            'POST',
            f'/api/campaigns/{campaign_id}/bestiary/generate-pack',
            'dm_authoring',
            {'themes': ['ash'], 'count': 1},
        ),
        ForbiddenCheckSpec(
            'Creature resolve save',
            'POST',
            '/api/creatures/resolve',
            'dm_authoring',
            {
                'campaignId': campaign_id,
                'descriptionHint': 'wolf',
                'themeTags': ['wolf'],
                'allowGeneration': False,
                'allowVariants': False,
            },
        ),
        ForbiddenCheckSpec(
            'Creature evolve save',
            'POST',
            '/api/creatures/evolve',
            'dm_authoring',
            {
                'campaignId': campaign_id,
                'sessionId': session_id,
                'baseCreature': {'id': 'goblin_skirmisher', 'name': 'Goblin Skirmisher'},
                'eventContext': {'eventTags': ['fire']},
            },
        ),
        ForbiddenCheckSpec(
            'Beta audits',
            'GET',
            '/api/beta/audits',
            'debug_read',
        ),
        ForbiddenCheckSpec(
            'Beta support bundle',
            'GET',
            f'/api/beta/support-bundle?session_id={session_id}',
            'debug_read',
        ),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Verify non-admin accounts cannot use operator-only REST endpoints.')
    parser.add_argument('--database-uri', default='', help='Optional database URI for isolated mode.')
    parser.add_argument('--target-url', default='', help='Hosted/staging base URL. Omit for isolated local mode.')
    parser.add_argument('--account-token', default=os.getenv('AIDM_FORBIDDEN_SMOKE_ACCOUNT_TOKEN', ''))
    parser.add_argument('--workspace-id', default=os.getenv('AIDM_FORBIDDEN_SMOKE_WORKSPACE_ID', ''))
    parser.add_argument('--campaign-id', type=int, default=int(os.getenv('AIDM_FORBIDDEN_SMOKE_CAMPAIGN_ID') or 0))
    parser.add_argument('--session-id', type=int, default=int(os.getenv('AIDM_FORBIDDEN_SMOKE_SESSION_ID') or 0))
    parser.add_argument('--timeout-seconds', type=float, default=10.0)
    parser.add_argument(
        '--evidence-report',
        nargs='?',
        const=DEFAULT_EVIDENCE_REPORT,
        default=None,
        type=pathlib.Path,
        help='Write Markdown or JSON forbidden-response evidence.',
    )
    return parser


def configure_runtime(database_uri: str) -> None:
    os.environ.update(
        {
            'AIDM_ENV': 'test',
            'AIDM_DATABASE_URI': database_uri,
            'AIDM_AUTO_CREATE_SCHEMA': 'true',
            'AIDM_AUTH_REQUIRED': 'true',
            'AIDM_API_AUTH_TOKENS': 'security-forbidden-smoke-operator-token',
            'AIDM_LLM_PROVIDER': 'fallback',
            'AIDM_LLM_MODEL': 'security-forbidden-smoke-v1',
            'AIDM_LLM_FALLBACK_MODELS': '',
            'AIDM_SOCKETIO_ASYNC_MODE': 'threading',
            'AIDM_TELEMETRY_ENABLED': 'false',
            'AIDM_RATE_LIMIT_MAX_API_REQUESTS': '1000',
            'AIDM_RATE_LIMIT_MAX_SOCKET_MESSAGES': '1000',
        }
    )


def _auth_headers(*, account_token: str, workspace_id: str) -> dict[str, str]:
    auth_value = account_token if account_token.lower().startswith('bearer ') else f'Bearer {account_token}'
    return {'Authorization': auth_value, 'X-AIDM-Workspace-Id': workspace_id}


def _response_payload(response) -> dict:
    if hasattr(response, 'get_json'):
        payload = response.get_json(silent=True)
    else:
        try:
            payload = response.json()
        except ValueError:
            payload = None
    return payload if isinstance(payload, dict) else {}


def _response_text(response, payload: dict) -> str:
    if payload:
        return _redact_sensitive_text(json.dumps(payload, sort_keys=True))[:500]
    if hasattr(response, 'get_data'):
        return _redact_sensitive_text(response.get_data(as_text=True))[:500]
    return _redact_sensitive_text(str(getattr(response, 'text', '')))[:500]


def _redact_sensitive_text(value: str) -> str:
    text = str(value or '')
    for pattern in SENSITIVE_TEXT_PATTERNS:
        text = pattern.sub(lambda match: f'{match.group(1)}{REDACTED_VALUE}', text)
    return text


def run_forbidden_checks(
    http,
    *,
    workspace_id: str,
    campaign_id: int,
    session_id: int,
    account_token: str = '',
    headers: dict[str, str] | None = None,
) -> list[ForbiddenCheckResult]:
    request_headers = dict(headers) if headers is not None else _auth_headers(
        account_token=account_token,
        workspace_id=workspace_id,
    )
    results: list[ForbiddenCheckResult] = []
    for spec in check_specs(campaign_id=campaign_id, session_id=session_id):
        response = http.request(
            spec.method,
            spec.path_template,
            headers=request_headers,
            json_payload=spec.payload,
        )
        payload = _response_payload(response)
        details = payload.get('details') if isinstance(payload.get('details'), dict) else {}
        required_capability = str(details.get('required_capability') or '')
        error_code = str(payload.get('error_code') or '')
        ok = response.status_code == 403 and error_code == 'forbidden' and required_capability == spec.expected_capability
        results.append(
            ForbiddenCheckResult(
                label=spec.label,
                method=spec.method,
                path=spec.path_template,
                expected_capability=spec.expected_capability,
                status_code=int(response.status_code),
                error_code=error_code,
                required_capability=required_capability,
                ok=ok,
                response_excerpt=_response_text(response, payload),
            )
        )
    return results


def _seed_isolated_runtime(database_uri: str) -> tuple[object, SeededForbiddenRuntime]:
    configure_runtime(database_uri)

    from aidm_server.auth import hash_secret
    from aidm_server.database import db, ensure_schema
    from aidm_server.main import create_app
    from aidm_server.models import Account, AccountWorkspaceMembership, Campaign, Player, Session, World, safe_json_dumps

    app = create_app()
    ensure_schema(app)
    with app.app_context():
        account = Account(
            username='security-forbidden-player',
            first_name='Security',
            last_name='Player',
            password_hash='configured',
            account_token_hash=hash_secret(DEFAULT_ACCOUNT_TOKEN),
        )
        db.session.add(account)
        db.session.flush()
        db.session.add(
            AccountWorkspaceMembership(
                account_id=account.account_id,
                workspace_id=DEFAULT_WORKSPACE_ID,
                role='player',
            )
        )
        world = World(name='Security Forbidden World', description='security forbidden smoke')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(
            title='Security Forbidden Campaign',
            world_id=world.world_id,
            workspace_id=DEFAULT_WORKSPACE_ID,
        )
        db.session.add(campaign)
        db.session.flush()
        player = Player(
            campaign_id=campaign.campaign_id,
            workspace_id=DEFAULT_WORKSPACE_ID,
            name='Security Player',
            character_name='Security Player',
        )
        db.session.add(player)
        session = Session(campaign_id=campaign.campaign_id)
        session.state_snapshot = safe_json_dumps(
            {
                'combat': {
                    'status': 'active',
                    'participants': [
                        {'id': 'enemy_wolf_1', 'team': 'enemy', 'hp': {'current': 0, 'max': 11}, 'isAlive': False}
                    ],
                    'flags': {},
                }
            },
            {},
        )
        db.session.add(session)
        db.session.commit()
        seeded = SeededForbiddenRuntime(
            workspace_id=DEFAULT_WORKSPACE_ID,
            account_token=DEFAULT_ACCOUNT_TOKEN,
            campaign_id=int(campaign.campaign_id),
            session_id=int(session.session_id),
        )
    return app, seeded


def evidence_payload(
    *,
    mode: str,
    target_url: str,
    workspace_id: str,
    campaign_id: int,
    session_id: int,
    generated_at: str,
    results: list[ForbiddenCheckResult],
) -> dict:
    status = 'passed' if all(result.ok for result in results) else 'failed'
    return {
        'status': status,
        'generated_at': generated_at,
        'mode': mode,
        'target_url': target_url,
        'workspace_id': workspace_id,
        'campaign_id': campaign_id,
        'session_id': session_id,
        'checks': [asdict(result) for result in results],
    }


def render_evidence_markdown(payload: dict) -> str:
    rows = ['| Check | Method | Path | Expected capability | HTTP | Error | Required capability | Result |', '| --- | --- | --- | --- | ---: | --- | --- | --- |']
    for check in payload['checks']:
        rows.append(
            f"| {check['label']} | {check['method']} | `{check['path']}` | {check['expected_capability']} | "
            f"{check['status_code']} | {check['error_code'] or ''} | {check['required_capability'] or ''} | "
            f"{'passed' if check['ok'] else 'failed'} |"
        )

    failures = [check for check in payload['checks'] if not check['ok']]
    failure_lines = ['- None.']
    if failures:
        failure_lines = [
            f"- {check['label']}: HTTP {check['status_code']}, error={check['error_code'] or 'missing'}, "
            f"required={check['required_capability'] or 'missing'}"
            for check in failures
        ]
    return '\n'.join(
        [
            '# Security Forbidden Evidence',
            '',
            f"- Status: {payload['status']}",
            f"- Generated: {payload['generated_at']}",
            f"- Mode: {payload['mode']}",
            f"- Target URL: `{payload['target_url'] or 'isolated local runtime'}`",
            f"- Workspace ID: `{payload['workspace_id']}`",
            f"- Campaign ID: {payload['campaign_id']}",
            f"- Session ID: {payload['session_id']}",
            '',
            '## Checks',
            '',
            *rows,
            '',
            '## Failures',
            '',
            *failure_lines,
            '',
        ]
    )


def write_evidence_report(path: pathlib.Path, payload: dict) -> pathlib.Path:
    output_path = path if path.is_absolute() else REPO_ROOT / path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.suffix.lower() == '.json':
        output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    else:
        output_path.write_text(render_evidence_markdown(payload), encoding='utf-8')
    return output_path


def run_isolated_smoke(*, database_uri: str) -> tuple[dict, list[ForbiddenCheckResult]]:
    app, seeded = _seed_isolated_runtime(database_uri)
    http = FlaskTestHttpClient(app.test_client())
    results = run_forbidden_checks(
        http,
        account_token=seeded.account_token,
        workspace_id=seeded.workspace_id,
        campaign_id=seeded.campaign_id,
        session_id=seeded.session_id,
    )
    payload = evidence_payload(
        mode='isolated',
        target_url='',
        workspace_id=seeded.workspace_id,
        campaign_id=seeded.campaign_id,
        session_id=seeded.session_id,
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        results=results,
    )
    return payload, results


def run_live_target_smoke(
    *,
    target_url: str,
    account_token: str,
    workspace_id: str,
    campaign_id: int,
    session_id: int,
    timeout_seconds: float,
) -> tuple[dict, list[ForbiddenCheckResult]]:
    http = RequestsHttpClient(target_url, timeout_seconds=timeout_seconds)
    results = run_forbidden_checks(
        http,
        account_token=account_token,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        session_id=session_id,
    )
    payload = evidence_payload(
        mode='live-target',
        target_url=target_url,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        session_id=session_id,
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        results=results,
    )
    return payload, results


def _print_summary(payload: dict) -> None:
    failed = [check for check in payload['checks'] if not check['ok']]
    if not failed:
        print(
            'Security forbidden smoke passed: non-admin account was rejected by '
            'combat operator, bestiary authoring/save, and beta operator endpoints.'
        )
        return
    print('[security-forbidden-smoke][error] Forbidden-response checks failed:', file=sys.stderr)
    for check in failed:
        print(
            f"  - {check['label']}: HTTP {check['status_code']}, "
            f"error={check['error_code'] or 'missing'}, required={check['required_capability'] or 'missing'}",
            file=sys.stderr,
        )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.target_url and args.database_uri:
        parser.error('--database-uri cannot be combined with --target-url.')
    if args.target_url:
        missing = []
        if not args.account_token:
            missing.append('--account-token')
        if not args.workspace_id:
            missing.append('--workspace-id')
        if args.campaign_id <= 0:
            missing.append('--campaign-id')
        if args.session_id <= 0:
            missing.append('--session-id')
        if missing:
            parser.error('live target mode requires ' + ', '.join(missing))
        payload, _results = run_live_target_smoke(
            target_url=args.target_url,
            account_token=args.account_token,
            workspace_id=args.workspace_id,
            campaign_id=args.campaign_id,
            session_id=args.session_id,
            timeout_seconds=args.timeout_seconds,
        )
    elif args.database_uri:
        payload, _results = run_isolated_smoke(database_uri=args.database_uri)
    else:
        with tempfile.TemporaryDirectory(prefix='aidm-security-forbidden-') as tmp:
            db_path = pathlib.Path(tmp) / 'security-forbidden.sqlite'
            payload, _results = run_isolated_smoke(database_uri=f'sqlite:///{db_path}')

    _print_summary(payload)
    if args.evidence_report is not None:
        output_path = write_evidence_report(args.evidence_report, payload)
        print(f'[security-forbidden-smoke] Evidence report written to {output_path}.')
    return 0 if payload['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
