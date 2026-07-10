from __future__ import annotations

import json
import sys

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


def test_default_npm_command_records_exact_toolchain(tmp_path):
    frontend = _frontend_dir(tmp_path)

    evidence = build_evidence(
        frontend_dir=frontend,
        command=['npm', 'ci', '--ignore-scripts'],
        generated_at='2026-07-10T00:00:00+00:00',
        run=True,
    )

    assert evidence['status'] == 'passed'
    assert evidence['node_version'] == '24.18.0'
    assert evidence['npm_version'] == '12.0.0'
