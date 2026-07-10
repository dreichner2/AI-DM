from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.check_release_artifact_consistency import build_report, main


def _write_archive(path: Path, data: bytes = b'archive bytes') -> str:
    path.write_bytes(data)
    sha256 = hashlib.sha256(data).hexdigest()
    path.with_name(path.name + '.sha256').write_text(f'{sha256}  {path.name}\n', encoding='utf-8')
    return sha256


def _write_text_artifact(path: Path, sha256: str) -> None:
    path.write_text(f'# Artifact\n\n- Source archive SHA256: `{sha256}`\n', encoding='utf-8')


def _packet(tmp_path: Path, *, sha256: str, archive: Path, signoff_sha256: str | None = None) -> dict:
    signoff = tmp_path / 'operator-signoff-status.md'
    from_inputs = tmp_path / 'operator-signoff.from-inputs-status.md'
    action_plan = tmp_path / 'operator-signoff-action-plan.md'
    external_inputs = tmp_path / 'external-proof-inputs.md'
    execution_plan = tmp_path / 'external-proof-execution-plan.md'
    recommendation = tmp_path / 'rc-recommendation-matrix.md'
    for path in (signoff, from_inputs, action_plan, external_inputs, execution_plan, recommendation):
        _write_text_artifact(path, sha256)
    observed_signoff_sha = signoff_sha256 or sha256
    return {
        'source_archive': {
            'status': 'passed',
            'path': str(archive),
            'sha256': sha256,
            'bytes': archive.stat().st_size,
        },
        'operator_signoff': {
            'status': 'missing',
            'path': str(signoff),
            'source_archive_sha256': observed_signoff_sha,
        },
        'operator_signoff_from_inputs': {
            'status': 'incomplete',
            'path': str(from_inputs),
            'source_archive_sha256': sha256,
        },
        'operator_signoff_action_plan': {'path': str(action_plan)},
        'external_proof_inputs': {'path': str(external_inputs)},
        'external_proof_execution_plan': {'path': str(execution_plan)},
        'recommendation_matrix': {'path': str(recommendation)},
    }


def test_build_report_passes_when_release_artifacts_are_consistent(tmp_path):
    archive = tmp_path / 'aidm-source.tar.gz'
    sha256 = _write_archive(archive)
    packet = _packet(tmp_path, sha256=sha256, archive=archive)
    packet_path = tmp_path / 'release-evidence-packet.json'
    packet_markdown = tmp_path / 'release-evidence-packet.md'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')
    _write_text_artifact(packet_markdown, sha256)

    report = build_report(
        packet_json=packet_path,
        release_packet_markdown=packet_markdown,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'passed'
    assert report['errors'] == []
    assert report['source_archive_sha256'] == sha256


def test_build_report_rejects_operator_signoff_archive_sha_mismatch(tmp_path):
    archive = tmp_path / 'aidm-source.tar.gz'
    sha256 = _write_archive(archive)
    packet = _packet(tmp_path, sha256=sha256, archive=archive, signoff_sha256='b' * 64)
    packet_path = tmp_path / 'release-evidence-packet.json'
    packet_markdown = tmp_path / 'release-evidence-packet.md'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')
    _write_text_artifact(packet_markdown, sha256)

    report = build_report(
        packet_json=packet_path,
        release_packet_markdown=packet_markdown,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'failed'
    assert any('operator_signoff.source_archive_sha256' in error for error in report['errors'])


def test_build_report_rejects_stale_packet_archive_sha(tmp_path):
    archive = tmp_path / 'aidm-source.tar.gz'
    actual_sha256 = _write_archive(archive)
    packet_sha256 = 'c' * 64
    packet = _packet(tmp_path, sha256=packet_sha256, archive=archive)
    packet_path = tmp_path / 'release-evidence-packet.json'
    packet_markdown = tmp_path / 'release-evidence-packet.md'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')
    _write_text_artifact(packet_markdown, packet_sha256)

    report = build_report(
        packet_json=packet_path,
        release_packet_markdown=packet_markdown,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'failed'
    assert any(actual_sha256 in error and packet_sha256 in error for error in report['errors'])
    assert any('sidecar sha256' in error for error in report['errors'])


def test_build_report_rejects_nonportable_absolute_sidecar_target(tmp_path):
    archive = tmp_path / 'aidm-source.tar.gz'
    sha256 = _write_archive(archive)
    archive.with_name(archive.name + '.sha256').write_text(f'{sha256}  {archive}\n', encoding='utf-8')
    packet = _packet(tmp_path, sha256=sha256, archive=archive)
    packet_path = tmp_path / 'release-evidence-packet.json'
    packet_markdown = tmp_path / 'release-evidence-packet.md'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')
    _write_text_artifact(packet_markdown, sha256)

    report = build_report(
        packet_json=packet_path,
        release_packet_markdown=packet_markdown,
        generated_at='2026-06-19T00:00:00+00:00',
    )

    assert report['status'] == 'failed'
    assert any('portable and relative' in error for error in report['errors'])


def test_main_writes_markdown_and_json(tmp_path):
    archive = tmp_path / 'aidm-source.tar.gz'
    sha256 = _write_archive(archive)
    packet = _packet(tmp_path, sha256=sha256, archive=archive)
    packet_path = tmp_path / 'release-evidence-packet.json'
    packet_markdown = tmp_path / 'release-evidence-packet.md'
    output = tmp_path / 'release-artifact-consistency.md'
    json_output = tmp_path / 'release-artifact-consistency.json'
    packet_path.write_text(json.dumps(packet), encoding='utf-8')
    _write_text_artifact(packet_markdown, sha256)

    exit_code = main(
        [
            '--packet-json',
            str(packet_path),
            '--release-packet-markdown',
            str(packet_markdown),
            '--output',
            str(output),
            '--json-output',
            str(json_output),
            '--generated-at',
            '2026-06-19T00:00:00+00:00',
        ]
    )

    assert exit_code == 0
    assert '# Release Artifact Consistency' in output.read_text(encoding='utf-8')
    payload = json.loads(json_output.read_text(encoding='utf-8'))
    assert payload['status'] == 'passed'
