from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_postgres_ci_job_covers_production_rehearsal_contract():
    workflow = (REPO_ROOT / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')

    required_fragments = (
        'postgres-integration:',
        'image: postgres:17',
        'python -m aidm_server.deploy_bootstrap --check-only',
        'python -m flask --app aidm_server.main:create_app db check',
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
