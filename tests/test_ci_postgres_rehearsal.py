import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

ACTION_PIN_PATTERN = re.compile(r'uses: actions/[\w-]+@[0-9a-f]{40} # v\d')


def test_postgres_ci_job_covers_production_rehearsal_contract():
    workflow = (REPO_ROOT / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')

    required_fragments = (
        'postgres-integration:',
        'image: postgres:18.4-bookworm@sha256:d9c83446333daec3f0588cc709adb80c26090b7f9f0f7ec8d43c243385d79818',
        'Verify PostgreSQL server version',
        "server_version.startswith('18.4')",
        'python -m aidm_server.deploy_bootstrap --check-only',
        'python -m flask --app aidm_server.main:create_app db check',
        'AIDM_SOCKETIO_ASYNC_MODE: threading',
        "AIDM_GUNICORN_THREADS: '16'",
        "transports=['websocket']",
        'websocket_extra_options',
        'ci-account-token',
        'ThreadPoolExecutor',
        'Barrier',
        "metrics_path = '/api/metrics/prometheus' if index % 2 else '/api/metrics'",
        "'account_token': 'ci-account-token'",
        "'X-AIDM-Workspace-Id': 'owner'",
        '--socketio-origin https://aidm-ci.example.test',
        'scripts/run_production_server.sh',
        '--target-url http://127.0.0.1:5099',
        'tests/test_postgres_runtime.py',
        'scripts/hosted_cookie_auth_smoke.py',
        'scripts/security_forbidden_smoke.py',
        'scripts/session_export_import_smoke.py',
        'name: postgres-production-rehearsal',
    )

    missing = [fragment for fragment in required_fragments if fragment not in workflow]
    assert not missing, f'PostgreSQL CI production rehearsal is missing: {missing}'


def test_ci_workflows_pin_upgraded_toolchains_and_dependencies():
    workflow_paths = (
        REPO_ROOT / '.github' / 'workflows' / 'ci.yml',
        REPO_ROOT / '.github' / 'workflows' / 'closed-beta-rc.yml',
    )
    combined = ''
    for workflow_path in workflow_paths:
        workflow = workflow_path.read_text(encoding='utf-8')
        combined += workflow
        uses_lines = [line.strip() for line in workflow.splitlines() if 'uses: actions/' in line]
        assert uses_lines
        assert all(ACTION_PIN_PATTERN.search(line) for line in uses_lines), uses_lines
        assert 'runs-on: ubuntu-24.04' in workflow
        assert "python-version: '3.14.6'" in workflow
        assert (
            'python -m pip install --require-hashes --constraint '
            'requirements.constraints.txt -r requirements-dev.lock.txt'
        ) in workflow
        assert 'ubuntu-latest' not in workflow

    required_fragments = (
        'actions/checkout@9c091bb21b7c1c1d1991bb908d89e4e9dddfe3e0 # v7.0.0',
        'actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0',
        'actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e # v6.4.0',
        'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1',
        'node-version-file: .nvmrc',
        'npm install --global npm@12.0.0',
        'test "$(npm --version)" = "12.0.0"',
    )
    missing = [fragment for fragment in required_fragments if fragment not in combined]
    assert not missing, f'CI toolchain contract is missing: {missing}'

    ci_workflow = workflow_paths[0].read_text(encoding='utf-8')
    backend_job = ci_workflow.split('  backend:', 1)[1].split('\n  postgres-integration:', 1)[0]
    backend_toolchain_fragments = (
        'actions/setup-node@48b55a011bda9f5d6aeb4c2d9c7362e8dae4041e # v6.4.0',
        'node-version-file: .nvmrc',
        'npm install --global npm@12.0.0',
        'test "$(npm --version)" = "12.0.0"',
    )
    missing_backend_toolchain = [
        fragment for fragment in backend_toolchain_fragments if fragment not in backend_job
    ]
    assert not missing_backend_toolchain, (
        'Backend CI must provision the exact frontend toolchain used by the full pytest suite: '
        f'{missing_backend_toolchain}'
    )


def test_dependabot_tracks_supported_ecosystems_and_compatibility_holds():
    dependabot = (REPO_ROOT / '.github' / 'dependabot.yml').read_text(encoding='utf-8')

    assert 'package-ecosystem: github-actions' in dependabot
    assert 'package-ecosystem: docker-compose' in dependabot
    assert 'package-ecosystem: docker\n' not in dependabot
    assert """- dependency-name: pydantic-core
        versions:
          - '>2.46.4'""" in dependabot
    assert """- dependency-name: '@types/node'
        update-types:
          - version-update:semver-major""" in dependabot
