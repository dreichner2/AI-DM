from __future__ import annotations

from sqlalchemy import inspect

from aidm_server.database import db


def _fk_ondelete(inspector, table_name: str, constrained_column: str) -> str | None:
    for foreign_key in inspector.get_foreign_keys(table_name):
        if constrained_column in foreign_key.get('constrained_columns', []):
            return (foreign_key.get('options') or {}).get('ondelete')
    return None


def test_schema_contains_new_beta_tables(app):
    with app.app_context():
        db.create_all()
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())

    assert 'dm_turns' in tables
    assert 'session_states' in tables
    assert 'dm_coherence_feedback' in tables
    assert 'session_log_entries' in tables
    assert 'story_entities' in tables
    assert 'story_facts' in tables
    assert 'story_threads' in tables
    assert 'turn_canon_updates' in tables
    assert 'turn_events' in tables
    assert 'canon_jobs' in tables
    assert 'installed_campaign_packs' in tables
    assert 'campaign_packs' in tables
    assert 'campaign_pack_records' in tables
    assert 'campaign_pack_sessions' in tables
    assert 'campaign_pack_checkpoint_progress' in tables
    assert 'campaign_pack_progress_events' in tables

    session_cols = {col['name'] for col in inspector.get_columns('sessions')}
    assert {'name', 'status', 'updated_at', 'deleted_at', 'client_session_id', 'archived_by_campaign_id'}.issubset(session_cols)
    campaign_cols = {col['name'] for col in inspector.get_columns('campaigns')}
    assert {'updated_at', 'status'}.issubset(campaign_cols)
    segment_cols = {col['name'] for col in inspector.get_columns('campaign_segments')}
    assert {'external_id', 'source', 'source_pack_id', 'metadata_json'}.issubset(segment_cols)
    dm_turn_cols = {col['name'] for col in inspector.get_columns('dm_turns')}
    assert 'client_message_id' in dm_turn_cols
    dm_turn_indexes = {index['name']: index for index in inspector.get_indexes('dm_turns')}
    assert bool(dm_turn_indexes['uq_dm_turns_session_player_client_message']['unique']) is True
    session_indexes = {index['name'] for index in inspector.get_indexes('sessions')}
    assert 'ix_sessions_campaign_id_status_updated_at' in session_indexes
    assert 'ix_sessions_archived_by_campaign_id' in session_indexes
    assert 'uq_sessions_campaign_client_session_id' in session_indexes
    segment_indexes = {index['name'] for index in inspector.get_indexes('campaign_segments')}
    assert 'ix_campaign_segments_campaign_source_external' in segment_indexes
    installed_pack_cols = {col['name'] for col in inspector.get_columns('installed_campaign_packs')}
    assert {
        'workspace_id',
        'pack_id',
        'pack_version',
        'schema_version',
        'pack_hash',
        'source_filename',
        'manifest_json',
        'validated_at',
    }.issubset(installed_pack_cols)
    installed_pack_indexes = {index['name'] for index in inspector.get_indexes('installed_campaign_packs')}
    assert 'ix_installed_campaign_packs_workspace_pack' in installed_pack_indexes
    assert 'ix_installed_campaign_packs_workspace_hash' in installed_pack_indexes
    campaign_pack_cols = {col['name'] for col in inspector.get_columns('campaign_packs')}
    assert {'installed_pack_id', 'pack_id', 'pack_version', 'schema_version', 'pack_hash', 'manifest_json'}.issubset(
        campaign_pack_cols
    )
    campaign_pack_indexes = {index['name'] for index in inspector.get_indexes('campaign_packs')}
    assert 'ix_campaign_packs_workspace_pack' in campaign_pack_indexes
    record_cols = {col['name'] for col in inspector.get_columns('campaign_pack_records')}
    assert {'campaign_pack_id', 'record_type', 'record_id', 'visibility', 'record_json'}.issubset(record_cols)
    session_pack_cols = {col['name'] for col in inspector.get_columns('campaign_pack_sessions')}
    assert {
        'campaign_pack_id',
        'installed_pack_id',
        'session_id',
        'pack_id',
        'active_checkpoint_id',
        'progress_revision',
        'multi_session_group_key',
        'gm_notes_json',
    }.issubset(session_pack_cols)
    progress_cols = {col['name'] for col in inspector.get_columns('campaign_pack_checkpoint_progress')}
    assert {'campaign_pack_session_id', 'checkpoint_id', 'status', 'progress_revision'}.issubset(progress_cols)
    progress_event_cols = {col['name'] for col in inspector.get_columns('campaign_pack_progress_events')}
    assert {
        'campaign_pack_session_id',
        'turn_event_id',
        'action',
        'from_checkpoint_id',
        'to_checkpoint_id',
        'idempotency_key',
        'payload_json',
    }.issubset(progress_event_cols)
    map_cols = {column['name'] for column in inspector.get_columns('maps')}
    assert 'visibility' in map_cols
    map_checks = {constraint['name'] for constraint in inspector.get_check_constraints('maps')}
    assert {'ck_maps_maps_has_owner', 'ck_maps_maps_visibility'} <= map_checks
    assert _fk_ondelete(inspector, 'dm_turns', 'session_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'sessions', 'archived_by_campaign_id') == 'SET NULL'
    assert _fk_ondelete(inspector, 'session_log_entries', 'session_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'session_states', 'session_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'story_entities', 'session_id') == 'SET NULL'
    assert _fk_ondelete(inspector, 'story_entities', 'first_seen_turn_id') == 'SET NULL'
    assert _fk_ondelete(inspector, 'story_facts', 'source_turn_id') == 'SET NULL'
    assert _fk_ondelete(inspector, 'story_threads', 'origin_turn_id') == 'SET NULL'
    assert _fk_ondelete(inspector, 'turn_canon_updates', 'turn_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'canon_jobs', 'turn_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'canon_jobs', 'session_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'installed_campaign_packs', 'imported_by_account_id') == 'SET NULL'
    assert _fk_ondelete(inspector, 'campaign_packs', 'installed_pack_id') == 'SET NULL'
    assert _fk_ondelete(inspector, 'campaign_pack_records', 'campaign_pack_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'campaign_pack_sessions', 'session_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'campaign_pack_sessions', 'campaign_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'campaign_pack_sessions', 'campaign_pack_id') == 'SET NULL'
    assert _fk_ondelete(inspector, 'campaign_pack_checkpoint_progress', 'campaign_pack_session_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'campaign_pack_progress_events', 'campaign_pack_session_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'campaign_pack_progress_events', 'turn_event_id') == 'SET NULL'


def test_schema_create_all_idempotent(app):
    with app.app_context():
        db.create_all()
        db.create_all()
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())

    assert 'worlds' in tables
    assert 'campaigns' in tables
    assert 'players' in tables
