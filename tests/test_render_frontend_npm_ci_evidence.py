from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from scripts.check_toolchain import is_compatible
from scripts.render_frontend_npm_ci_evidence import build_evidence, main, render_markdown


def _frontend_dir(tmp_path):
    frontend = tmp_path / 'aidm_frontend'
    frontend.mkdir()
    (frontend / 'package.json').write_text('{"scripts":{}}\n', encoding='utf-8')
    (frontend / 'package-lock.json').write_text('{"lockfileVersion":3}\n', encoding='utf-8')
    return frontend


def test_build_evidence_runs_command_and_records_lockfile_context(tmp_path):
    frontend = _frontend_dir(tmp_path)

    evidence = build_evidence(
        frontend_dir=frontend,
        command=[sys.executable, '-c', 'print("lockfile install ok")'],
        generated_at='2026-06-19T00:00:00+00:00',
        run=True,
    )
    markdown = render_markdown(evidence)

    assert evidence['status'] == 'passed'
    assert evidence['returncode'] == 0
    assert evidence['package_json_present'] is True
    assert evidence['package_lock_present'] is True
    assert 'lockfile install ok' in evidence['stdout_tail']
    assert '# Frontend npm ci Evidence' in markdown
    assert '- Status: passed' in markdown


def test_main_writes_markdown_and_json(tmp_path):
    frontend = _frontend_dir(tmp_path)
    output = tmp_path / 'frontend-npm-ci-evidence.md'
    json_output = tmp_path / 'frontend-npm-ci-evidence.json'

    exit_code = main(
        [
            '--frontend-dir',
            str(frontend),
            '--output',
            str(output),
            '--json-output',
            str(json_output),
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
            '--command',
            sys.executable,
            '-c',
            'print("ok")',
        ]
    )

    assert exit_code == 0
    assert '# Frontend npm ci Evidence' in output.read_text(encoding='utf-8')
    payload = json.loads(json_output.read_text(encoding='utf-8'))
    assert payload['status'] == 'passed'


def _fake_toolchain(tmp_path, monkeypatch, *, node_version: str, npm_version: str):
    bin_dir = tmp_path / 'bin'
    bin_dir.mkdir()
    npm_log = tmp_path / 'npm-commands.log'
    (bin_dir / 'node').write_text(f'#!/bin/sh\necho v{node_version}\n', encoding='utf-8')
    (bin_dir / 'npm').write_text(
        '#!/bin/sh\n'
        f'if [ "$1" = "--version" ]; then echo {npm_version}; exit 0; fi\n'
        f'echo "$@" >> {npm_log}\n',
        encoding='utf-8',
    )
    (bin_dir / 'node').chmod(0o755)
    (bin_dir / 'npm').chmod(0o755)
    monkeypatch.setenv('PATH', f'{bin_dir}{os.pathsep}{os.environ["PATH"]}')
    return npm_log


def test_npm_command_passes_on_compatible_newer_toolchain(tmp_path, monkeypatch):
    frontend = _frontend_dir(tmp_path)
    npm_log = _fake_toolchain(tmp_path, monkeypatch, node_version='24.20.1', npm_version='12.0.2')

    evidence = build_evidence(
        frontend_dir=frontend,
        command=['npm', 'ci'],
        generated_at='2026-11-10T00:00:00+00:00',
        run=True,
    )
    markdown = render_markdown(evidence)

    assert evidence['status'] == 'passed'
    assert evidence['node_version'] == '24.20.1'
    assert evidence['npm_version'] == '12.0.2'
    assert npm_log.read_text(encoding='utf-8') == 'ci\n'
    assert '- Node.js: 24.20.1; required Node.js 24 (24.18.0 or newer)' in markdown
    assert '- npm: 12.0.2; required npm 12' in markdown


def test_npm_command_fails_on_incompatible_toolchain_without_running(tmp_path, monkeypatch):
    frontend = _frontend_dir(tmp_path)
    npm_log = _fake_toolchain(tmp_path, monkeypatch, node_version='25.0.0', npm_version='13.0.0')

    evidence = build_evidence(
        frontend_dir=frontend,
        command=['npm', 'ci'],
        generated_at='2026-11-10T00:00:00+00:00',
        run=True,
    )

    assert evidence['status'] == 'failed'
    assert evidence['returncode'] is None
    assert evidence['node_version'] == '25.0.0'
    assert evidence['npm_version'] == '13.0.0'
    assert evidence['error'] == (
        'Node.js 24 (24.18.0 or newer) is required; found 25.0.0. npm 12 is required; found 13.0.0.'
    )
    assert not npm_log.exists()


@pytest.mark.frontend_toolchain
def test_default_npm_command_records_exact_toolchain(tmp_path):
    frontend = _frontend_dir(tmp_path)
    installed_node = subprocess.run(['node', '--version'], capture_output=True, text=True, check=True)
    installed_npm = subprocess.run(['npm', '--version'], capture_output=True, text=True, check=True)

    evidence = build_evidence(
        frontend_dir=frontend,
        command=['npm', 'ci', '--ignore-scripts'],
        generated_at='2026-07-10T00:00:00+00:00',
        run=True,
    )

    assert evidence['status'] == 'passed'
    assert evidence['node_version'] == installed_node.stdout.strip().removeprefix('v')
    assert evidence['npm_version'] == installed_npm.stdout.strip()
    assert is_compatible('node', evidence['node_version'])
    assert is_compatible('npm', evidence['npm_version'])
