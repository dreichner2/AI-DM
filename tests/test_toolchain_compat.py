from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from scripts.check_toolchain import check, is_compatible, main


REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK_TOOLCHAIN = REPO_ROOT / 'scripts' / 'check_toolchain.py'
LAUNCH_SCRIPTS = tuple(sorted((REPO_ROOT / 'scripts').glob('*.sh')))
NODE_SHELL_SCRIPTS = ('launch_desktop_app.sh', 'launch_frontend_service.sh', 'run_unified_local.sh')
PYTHON_SHELL_CHECKS = {
    'launch_backend_service.sh': 2,
    'launch_desktop_app.sh': 2,
    'run_local_backend.sh': 1,
    'run_production_server.sh': 1,
}

CASES = [
    ('python', '3.14.5', False, 'Python 3.14 (3.14.6 or newer) is required; found 3.14.5.'),
    ('python', '3.14.6', True, 'Python 3.14.6 satisfies Python 3.14 (3.14.6 or newer).'),
    ('python', '3.14.10', True, 'Python 3.14.10 satisfies Python 3.14 (3.14.6 or newer).'),
    ('python', '3.15.0', False, 'Python 3.14 (3.14.6 or newer) is required; found 3.15.0.'),
    ('python', '3.13.9', False, 'Python 3.14 (3.14.6 or newer) is required; found 3.13.9.'),
    ('node', '24.17.9', False, 'Node.js 24 (24.18.0 or newer) is required; found 24.17.9.'),
    ('node', '24.18.0', True, 'Node.js 24.18.0 satisfies Node.js 24 (24.18.0 or newer).'),
    ('node', '24.20.1', True, 'Node.js 24.20.1 satisfies Node.js 24 (24.18.0 or newer).'),
    ('node', '25.0.0', False, 'Node.js 24 (24.18.0 or newer) is required; found 25.0.0.'),
    ('npm', '11.9.0', False, 'npm 12 is required; found 11.9.0.'),
    ('npm', '12.0.0', True, 'npm 12.0.0 satisfies npm 12.'),
    ('npm', '12.0.2', True, 'npm 12.0.2 satisfies npm 12.'),
    ('npm', '13.0.0', False, 'npm 12 is required; found 13.0.0.'),
]
CASE_IDS = [f'{tool}-{version}' for tool, version, _, _ in CASES]


@pytest.mark.parametrize(('tool', 'version', 'compatible', 'message'), CASES, ids=CASE_IDS)
def test_rule_accepts_and_rejects_versions_with_exact_messages(tool, version, compatible, message):
    assert is_compatible(tool, version) is compatible
    assert check(tool, version) == (compatible, message)


@pytest.mark.parametrize(('tool', 'version', 'compatible', 'message'), CASES, ids=CASE_IDS)
def test_cli_reports_one_line_and_exit_code(tool, version, compatible, message):
    result = subprocess.run(
        [sys.executable, str(CHECK_TOOLCHAIN), tool, version],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == (0 if compatible else 1)
    assert (result.stdout if compatible else result.stderr) == f'{message}\n'
    assert (result.stderr if compatible else result.stdout) == ''


def test_rule_rejects_unparseable_versions_and_unknown_tools():
    assert check('npm', 'twelve') == (False, 'npm 12 is required; found twelve.')
    assert is_compatible('node', 'v24.18.0') is False
    with pytest.raises(ValueError):
        is_compatible('ruby', '3.4.0')


@pytest.mark.parametrize('argv', [[], ['ruby'], ['python', '3.14.6', 'extra'], ['node', 'npm', '24.18.0']])
def test_cli_rejects_bad_usage(argv, capsys):
    assert main(argv) == 2
    assert capsys.readouterr().err.startswith('usage: check_toolchain.py')


def test_cli_accepts_the_interpreter_running_it():
    result = subprocess.run([sys.executable, str(CHECK_TOOLCHAIN), 'python'], capture_output=True, text=True)

    version = '.'.join(str(part) for part in sys.version_info[:3])
    assert result.returncode == 0, result.stderr
    assert result.stdout == f'Python {version} satisfies Python 3.14 (3.14.6 or newer).\n'


def _fake_bin(tmp_path: Path, *, node: str | None, npm: str | None) -> dict[str, str]:
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir(parents=True)
    for name, output in (('node', node), ('npm', npm)):
        if output is not None:
            (bin_dir / name).write_text(f'#!/bin/sh\necho {output}\n', encoding='utf-8')
            (bin_dir / name).chmod(0o755)
    # Only the fakes and the system shell tools: a real node/npm elsewhere on PATH must not leak in.
    return {**os.environ, 'PATH': f'{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin'}


def test_cli_checks_installed_node_and_npm(tmp_path):
    env = _fake_bin(tmp_path, node='v24.20.1', npm='13.0.0')

    result = subprocess.run(
        [sys.executable, str(CHECK_TOOLCHAIN), 'node', 'npm'], env=env, capture_output=True, text=True
    )

    assert result.returncode == 1
    assert result.stdout == 'Node.js 24.20.1 satisfies Node.js 24 (24.18.0 or newer).\n'
    assert result.stderr == 'npm 12 is required; found 13.0.0.\n'


def test_cli_reports_missing_executables(tmp_path):
    env = _fake_bin(tmp_path, node=None, npm=None)
    if shutil.which('node', path=env['PATH']) or shutil.which('npm', path=env['PATH']):
        pytest.skip('a system node or npm is installed in /usr/bin or /bin')

    result = subprocess.run(
        [sys.executable, str(CHECK_TOOLCHAIN), 'node', 'npm'], env=env, capture_output=True, text=True
    )

    assert result.returncode == 1
    assert result.stderr == (
        'Node.js 24 (24.18.0 or newer) is required; could not run node --version.\n'
        'npm 12 is required; could not run npm --version.\n'
    )


requires_make = pytest.mark.skipif(shutil.which('make') is None, reason='requires make')


def _make(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shutil.which('make') or 'make', '--no-print-directory', *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


@requires_make
def test_make_verify_python_accepts_the_current_interpreter():
    dry_run = _make('-n', 'verify-python', f'PYTHON={sys.executable}')
    result = _make('verify-python', f'PYTHON={sys.executable}')

    assert f'{sys.executable} scripts/check_toolchain.py python' in dry_run.stdout
    assert result.returncode == 0, result.stderr
    assert 'satisfies Python 3.14 (3.14.6 or newer).' in result.stdout


@requires_make
def test_make_frontend_toolchain_uses_the_shared_rule(tmp_path):
    accepted = _make(
        'verify-frontend-toolchain',
        f'PYTHON={sys.executable}',
        env=_fake_bin(tmp_path / 'ok', node='v24.18.0', npm='12.0.2'),
    )
    rejected = _make(
        'verify-frontend-toolchain',
        f'PYTHON={sys.executable}',
        env=_fake_bin(tmp_path / 'old', node='v24.17.9', npm='12.0.2'),
    )

    assert accepted.returncode == 0, accepted.stderr
    assert rejected.returncode != 0
    assert 'Node.js 24 (24.18.0 or newer) is required; found 24.17.9.' in rejected.stderr


@requires_make
def test_make_install_checks_the_bootstrap_toolchain_before_creating_the_virtualenv():
    dry_run = _make('-n', 'install', 'PYTHON_BOOTSTRAP=python-bootstrap')
    commands = dry_run.stdout.splitlines()

    check_index = commands.index('python-bootstrap scripts/check_toolchain.py python node npm')
    assert check_index < commands.index('python-bootstrap -m venv .venv')


def test_launch_scripts_check_python_through_the_shared_rule():
    for script in LAUNCH_SCRIPTS:
        source = script.read_text(encoding='utf-8')
        assert 'sys.version_info' not in source, script.name
        assert source.count('/scripts/check_toolchain.py" python') == PYTHON_SHELL_CHECKS.get(script.name, 0), script.name


def _inline_node_checks() -> list[str]:
    checks = []
    for name in NODE_SHELL_SCRIPTS:
        source = (REPO_ROOT / 'scripts' / name).read_text(encoding='utf-8')
        found = re.findall(r"node -e '([^']*process\.versions\.node[^']*)'", source)
        assert len(found) == 1, name
        checks.extend(found)
    return checks


@pytest.mark.skipif(shutil.which('node') is None, reason='requires Node.js')
def test_inline_shell_node_checks_match_the_rule():
    inline_checks = _inline_node_checks()
    assert len(set(inline_checks)) == 1
    inline_check = inline_checks[0]

    for tool, version, compatible, message in CASES:
        if tool != 'node':
            continue
        result = subprocess.run(
            ['node', '-e', inline_check.replace('process.versions.node', json.dumps(version))],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == (0 if compatible else 1), version
        assert result.stderr == ('' if compatible else f'{message}\n'), version


def test_inline_shell_npm_checks_match_the_rule():
    for name in NODE_SHELL_SCRIPTS:
        source = (REPO_ROOT / 'scripts' / name).read_text(encoding='utf-8')
        patterns = re.findall(r'\[\[ "\$\{npm_version\}" == (\S+) \]\]', source)
        assert len(patterns) == 1, name
        assert 'npm 12 is required; found ${npm_version}.' in source, name
        for tool, version, compatible, _ in CASES:
            if tool != 'npm':
                continue
            result = subprocess.run(['bash', '-c', f'[[ "$1" == {patterns[0]} ]]', '_', version], check=False)
            assert (result.returncode == 0) is compatible, (name, version)
