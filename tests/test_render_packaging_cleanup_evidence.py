from __future__ import annotations

import io
import json
import tarfile

from scripts import render_rc_issue_evidence
from scripts.render_packaging_cleanup_evidence import build_evidence, main, render_markdown


def _write_tar(path, members):
    with tarfile.open(path, mode='w:gz') as archive:
        for member in members:
            data = b'ok'
            info = tarfile.TarInfo(member)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))


def _write_tar_member(path, member, data):
    with tarfile.open(path, mode='w:gz') as archive:
        info = tarfile.TarInfo(member)
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))


def test_build_evidence_verifies_cleanup_coverage_and_archive_exclusions(tmp_path):
    cleanup = tmp_path / 'cleanup_artifacts.sh'
    cleanup.write_text(
        '\n'.join(
            [
                '$ROOT_DIR/.git',
                '$ROOT_DIR/.pytest_cache',
                '$ROOT_DIR/tmp',
                '! -name "release"',
                '$ROOT_DIR/aidm_server/:memory:',
                '$ROOT_DIR/aidm_frontend/.vite',
                '$ROOT_DIR/aidm_frontend/dist',
                '__pycache__',
                '.DS_Store',
            ]
        ),
        encoding='utf-8',
    )
    makefile = tmp_path / 'Makefile'
    makefile.write_text('clean-deps: clean\n\trm -rf .venv $(FRONTEND_DIR)/node_modules\n', encoding='utf-8')
    archive = tmp_path / 'aidm-source.tar.gz'
    _write_tar(archive, ['AIDM-main/README.md', 'AIDM-main/.env.local.example'])

    evidence = build_evidence(
        cleanup_script=cleanup,
        makefile=makefile,
        source_archive=archive,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    markdown = render_markdown(evidence)

    assert evidence['status'] == 'passed'
    assert evidence['source_archive']['status'] == 'passed'
    assert evidence['archive_policy']['allowed_template_members_found'] == ['AIDM-main/.env.local.example']
    assert evidence['failures'] == []
    assert any(check['label'] == 'git metadata preserved' for check in evidence['clean_checks'])
    assert any(check['label'] == 'macOS Finder metadata' for check in evidence['clean_checks'])
    assert '# Packaging Cleanup Evidence' in markdown
    assert 'make clean-deps Coverage' in markdown
    assert 'Source Archive Exclusion Policy' in markdown
    assert 'Large Archive Members' in markdown


def test_build_evidence_fails_when_archive_contains_forbidden_paths(tmp_path):
    cleanup = tmp_path / 'cleanup_artifacts.sh'
    cleanup.write_text(
        '$ROOT_DIR/.git\n$ROOT_DIR/.pytest_cache\n$ROOT_DIR/tmp\n! -name "release"\n$ROOT_DIR/aidm_server/:memory:\n'
        '$ROOT_DIR/aidm_frontend/.vite\n$ROOT_DIR/aidm_frontend/dist\n__pycache__\n.DS_Store\n',
        encoding='utf-8',
    )
    makefile = tmp_path / 'Makefile'
    makefile.write_text('clean-deps: clean\n\trm -rf .venv $(FRONTEND_DIR)/node_modules\n', encoding='utf-8')
    archive = tmp_path / 'aidm-source.tar.gz'
    _write_tar(archive, ['AIDM-main/aidm_frontend/node_modules/pkg/index.js', 'AIDM-main/.env.local'])

    evidence = build_evidence(
        cleanup_script=cleanup,
        makefile=makefile,
        source_archive=archive,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert evidence['status'] == 'failed'
    assert 'source archive contains forbidden paths' in evidence['failures']
    assert 'AIDM-main/.env.local' in evidence['source_archive']['forbidden']


def test_build_evidence_fails_when_archive_contains_large_non_lfs_file(tmp_path, monkeypatch):
    monkeypatch.setattr(render_rc_issue_evidence, 'LARGE_ARCHIVE_MEMBER_THRESHOLD_BYTES', 1)
    cleanup = tmp_path / 'cleanup_artifacts.sh'
    cleanup.write_text(
        '$ROOT_DIR/.git\n$ROOT_DIR/.pytest_cache\n$ROOT_DIR/tmp\n! -name "release"\n$ROOT_DIR/aidm_server/:memory:\n'
        '$ROOT_DIR/aidm_frontend/.vite\n$ROOT_DIR/aidm_frontend/dist\n__pycache__\n.DS_Store\n',
        encoding='utf-8',
    )
    makefile = tmp_path / 'Makefile'
    makefile.write_text('clean-deps: clean\n\trm -rf .venv $(FRONTEND_DIR)/node_modules\n', encoding='utf-8')
    archive = tmp_path / 'aidm-source.tar.gz'
    _write_tar(archive, ['AIDM-main/docs/large-reference.txt'])

    evidence = build_evidence(
        cleanup_script=cleanup,
        makefile=makefile,
        source_archive=archive,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert evidence['status'] == 'failed'
    assert 'source archive contains large files not tracked by Git LFS' in evidence['failures']
    assert evidence['source_archive']['large_untracked'] == ['AIDM-main/docs/large-reference.txt']


def test_build_evidence_exposes_and_fails_unresolved_lfs_pointer(tmp_path):
    cleanup = tmp_path / 'cleanup_artifacts.sh'
    cleanup.write_text(
        '$ROOT_DIR/.git\n$ROOT_DIR/.pytest_cache\n$ROOT_DIR/tmp\n! -name "release"\n$ROOT_DIR/aidm_server/:memory:\n'
        '$ROOT_DIR/aidm_frontend/.vite\n$ROOT_DIR/aidm_frontend/dist\n__pycache__\n.DS_Store\n',
        encoding='utf-8',
    )
    makefile = tmp_path / 'Makefile'
    makefile.write_text('clean-deps: clean\n\trm -rf .venv $(FRONTEND_DIR)/node_modules\n', encoding='utf-8')
    archive = tmp_path / 'aidm-source.tar.gz'
    pointer = (
        b'version https://git-lfs.github.com/spec/v1\n'
        b'oid sha256:113495375c81bc9a25acc0b0aa5a9fa8032c397bb4338ea99f2571677659fdfc\n'
        b'size 111217434\n'
    )
    _write_tar_member(
        archive,
        'AIDM-main/aidm_frontend/public/music/theme.mp3',
        pointer,
    )

    evidence = build_evidence(
        cleanup_script=cleanup,
        makefile=makefile,
        source_archive=archive,
        generated_at='2026-06-19T00:00:00+00:00',
    )
    markdown = render_markdown(evidence)

    assert evidence['status'] == 'failed'
    assert 'source archive contains unresolved Git LFS pointer files' in evidence['failures']
    assert evidence['source_archive']['unresolved_lfs_pointers'][0]['expected_bytes'] == 111217434
    assert '- Source archive unresolved Git LFS pointers: 1' in markdown
    assert '## Unresolved Git LFS Pointers' in markdown
    assert 'aidm_frontend/public/music/theme.mp3' in markdown


def test_main_writes_markdown_and_json(tmp_path):
    cleanup = tmp_path / 'cleanup_artifacts.sh'
    cleanup.write_text(
        '$ROOT_DIR/.git\n$ROOT_DIR/.pytest_cache\n$ROOT_DIR/tmp\n! -name "release"\n$ROOT_DIR/aidm_server/:memory:\n'
        '$ROOT_DIR/aidm_frontend/.vite\n$ROOT_DIR/aidm_frontend/dist\n__pycache__\n.DS_Store\n',
        encoding='utf-8',
    )
    makefile = tmp_path / 'Makefile'
    makefile.write_text('clean-deps: clean\n\trm -rf .venv $(FRONTEND_DIR)/node_modules\n', encoding='utf-8')
    archive = tmp_path / 'aidm-source.tar.gz'
    output = tmp_path / 'packaging-cleanup-evidence.md'
    json_output = tmp_path / 'packaging-cleanup-evidence.json'
    _write_tar(archive, ['AIDM-main/README.md'])

    exit_code = main(
        [
            '--cleanup-script',
            str(cleanup),
            '--makefile',
            str(makefile),
            '--source-archive',
            str(archive),
            '--output',
            str(output),
            '--json-output',
            str(json_output),
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )

    assert exit_code == 0
    assert '# Packaging Cleanup Evidence' in output.read_text(encoding='utf-8')
    payload = json.loads(json_output.read_text(encoding='utf-8'))
    assert payload['status'] == 'passed'
