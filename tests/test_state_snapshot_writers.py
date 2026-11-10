from __future__ import annotations

import scripts.check_state_snapshot_writers as writer_check
from scripts.check_state_snapshot_writers import check_inventory, parse_inventory, scan_state_snapshot_writers


def test_state_snapshot_writer_inventory_matches_current_code():
    result = check_inventory()

    assert result.errors == []


def test_state_snapshot_writer_inventory_documents_all_detected_scopes():
    writers = scan_state_snapshot_writers()
    inventory = parse_inventory()
    detected_scopes = {(writer.path, writer.scope) for writer in writers}

    assert detected_scopes == set(inventory)
    assert all(entry.category for entry in inventory.values())
    assert all(entry.action for entry in inventory.values())


def test_state_snapshot_writer_inventory_includes_constructor_seeds():
    writers = scan_state_snapshot_writers()

    assert any(
        writer.path == 'aidm_server/services/session_chapters.py'
        and writer.scope == '_create_chapter_session'
        and writer.source.startswith('state_snapshot=')
        for writer in writers
    )
    assert sum(
        1
        for writer in writers
        if writer.path == 'aidm_server/services/campaign_pack.py' and writer.scope == 'import_campaign_pack'
    ) == 2
    assert sum(
        1
        for writer in writers
        if writer.path == 'aidm_server/services/session_import.py' and writer.scope == 'import_session_export'
    ) == 2


def test_state_snapshot_writer_scan_detects_destructured_assignment_targets(tmp_path, monkeypatch):
    server = tmp_path / 'aidm_server'
    server.mkdir()
    source = server / 'example.py'
    source.write_text(
        '\n'.join(
            [
                'def update_snapshot(session):',
                '    session.state_snapshot, marker = "{}", True',
                '    [prefix, session.state_snapshot] = [None, "{}"]',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setattr(writer_check, 'REPO_ROOT', tmp_path)
    monkeypatch.setattr(writer_check, 'SCAN_ROOTS', (server,))

    writers = writer_check.scan_state_snapshot_writers()

    assert [(writer.path, writer.scope, writer.line) for writer in writers] == [
        ('aidm_server/example.py', 'update_snapshot', 2),
        ('aidm_server/example.py', 'update_snapshot', 3),
    ]
