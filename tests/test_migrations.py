from __future__ import annotations

import os
import pathlib
import subprocess
import sys

from sqlalchemy import create_engine, inspect


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _run_flask_db(args: list[str], env: dict):
    cmd = [sys.executable, '-m', 'flask', 'db', *args]
    result = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Command failed: {' '.join(cmd)}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"


def _inspect_db(db_uri: str):
    engine = create_engine(db_uri)
    try:
        return inspect(engine)
    finally:
        engine.dispose()


def _fk_ondelete(inspector, table_name: str, constrained_column: str) -> str | None:
    for foreign_key in inspector.get_foreign_keys(table_name):
        if constrained_column in foreign_key.get('constrained_columns', []):
            return (foreign_key.get('options') or {}).get('ondelete')
    return None


def test_migration_chain_upgrade_and_downgrade(tmp_path):
    db_path = tmp_path / 'migration_chain.db'
    db_uri = f'sqlite:///{db_path}'

    env = os.environ.copy()
    env['FLASK_APP'] = 'aidm_server.main:create_app'
    env['PYTHONPATH'] = str(REPO_ROOT)
    env['AIDM_ENV'] = 'test'
    env['AIDM_DEBUG'] = 'false'
    env['AIDM_SOCKETIO_ASYNC_MODE'] = 'threading'
    env['AIDM_AUTO_CREATE_SCHEMA'] = 'false'
    env['AIDM_DATABASE_URI'] = db_uri
    env['AIDM_TELEMETRY_ENABLED'] = 'false'

    _run_flask_db(['upgrade', '0001_initial_core'], env)

    inspector = _inspect_db(db_uri)
    tables = set(inspector.get_table_names())
    assert 'worlds' in tables
    assert 'campaigns' in tables
    assert 'session_log_entries' in tables
    assert 'dm_turns' not in tables
    assert 'session_states' not in tables

    entry_cols_v1 = {col['name'] for col in inspector.get_columns('session_log_entries')}
    assert 'metadata_json' not in entry_cols_v1

    _run_flask_db(['upgrade', 'head'], env)

    inspector = _inspect_db(db_uri)
    tables = set(inspector.get_table_names())
    assert 'dm_turns' in tables
    assert 'session_states' in tables
    assert 'dm_coherence_feedback' in tables
    assert 'story_entities' in tables
    assert 'story_facts' in tables
    assert 'story_threads' in tables
    assert 'turn_canon_updates' in tables
    assert 'turn_events' in tables
    assert 'rate_limit_events' in tables
    assert 'canon_jobs' in tables
    assert 'session_state_mutation_audits' in tables
    assert 'operator_action_audits' in tables
    entry_cols_head = {col['name'] for col in inspector.get_columns('session_log_entries')}
    assert 'metadata_json' in entry_cols_head
    dm_turn_cols_head = {col['name'] for col in inspector.get_columns('dm_turns')}
    assert {'confidence', 'roll_value', 'outcome_status', 'client_message_id'}.issubset(dm_turn_cols_head)
    dm_turn_indexes_head = {index['name']: index for index in inspector.get_indexes('dm_turns')}
    assert bool(dm_turn_indexes_head['uq_dm_turns_session_player_client_message']['unique']) is True
    turn_lock_cols_head = {col['name'] for col in inspector.get_columns('session_turn_locks')}
    assert {'owner_token', 'fencing_token', 'acquired_at', 'expires_at'}.issubset(turn_lock_cols_head)
    feedback_cols_head = {col['name'] for col in inspector.get_columns('dm_coherence_feedback')}
    assert {'feedback_type', 'category', 'provider', 'model', 'metadata_json'}.issubset(feedback_cols_head)
    campaign_cols_head = {col['name'] for col in inspector.get_columns('campaigns')}
    assert {'updated_at', 'status'}.issubset(campaign_cols_head)
    session_cols_head = {col['name'] for col in inspector.get_columns('sessions')}
    assert {'name', 'status', 'updated_at', 'deleted_at', 'client_session_id', 'archived_by_campaign_id'}.issubset(session_cols_head)
    assert _fk_ondelete(inspector, 'dm_turns', 'session_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'session_log_entries', 'session_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'session_states', 'session_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'story_entities', 'session_id') == 'SET NULL'
    assert _fk_ondelete(inspector, 'story_entities', 'first_seen_turn_id') == 'SET NULL'
    assert _fk_ondelete(inspector, 'story_facts', 'source_turn_id') == 'SET NULL'
    assert _fk_ondelete(inspector, 'story_threads', 'origin_turn_id') == 'SET NULL'
    assert _fk_ondelete(inspector, 'turn_canon_updates', 'turn_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'canon_jobs', 'turn_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'canon_jobs', 'session_id') == 'CASCADE'
    assert _fk_ondelete(inspector, 'sessions', 'archived_by_campaign_id') == 'SET NULL'
    session_indexes_head = {index['name'] for index in inspector.get_indexes('sessions')}
    assert 'ix_sessions_campaign_id_status_updated_at' in session_indexes_head
    assert 'ix_sessions_archived_by_campaign_id' in session_indexes_head
    assert 'uq_sessions_campaign_client_session_id' in session_indexes_head
    map_checks_head = {constraint['name'] for constraint in inspector.get_check_constraints('maps')}
    assert 'ck_maps_maps_has_owner' in map_checks_head
    log_indexes_head = {index['name'] for index in inspector.get_indexes('session_log_entries')}
    assert 'ix_session_log_entries_session_id_timestamp_id' in log_indexes_head
    fact_indexes_head = {index['name'] for index in inspector.get_indexes('story_facts')}
    assert 'ix_story_facts_campaign_subject_predicate' in fact_indexes_head
    mutation_audit_cols_head = {col['name'] for col in inspector.get_columns('session_state_mutation_audits')}
    assert {
        'source',
        'actor',
        'actor_account_id',
        'actor_role',
        'previous_revision',
        'state_revision',
        'applied_change_ids_json',
        'diff_json',
        'metadata_json',
    }.issubset(mutation_audit_cols_head)
    mutation_audit_indexes_head = {index['name'] for index in inspector.get_indexes('session_state_mutation_audits')}
    assert 'ix_state_mutation_audits_session_created' in mutation_audit_indexes_head
    assert 'ix_state_mutation_audits_campaign_created' in mutation_audit_indexes_head
    operator_audit_cols_head = {col['name'] for col in inspector.get_columns('operator_action_audits')}
    assert {
        'workspace_id',
        'campaign_id',
        'session_id',
        'action',
        'resource_type',
        'resource_id',
        'actor',
        'actor_account_id',
        'actor_role',
        'status',
        'details_json',
    }.issubset(operator_audit_cols_head)
    operator_audit_indexes_head = {index['name'] for index in inspector.get_indexes('operator_action_audits')}
    assert 'ix_operator_action_audits_workspace_created' in operator_audit_indexes_head
    assert 'ix_operator_action_audits_action_created' in operator_audit_indexes_head

    _run_flask_db(['downgrade', '0001_initial_core'], env)

    inspector = _inspect_db(db_uri)
    tables = set(inspector.get_table_names())
    assert 'dm_turns' not in tables
    assert 'session_states' not in tables
    assert 'dm_coherence_feedback' not in tables
    assert 'story_entities' not in tables
    assert 'story_facts' not in tables
    assert 'story_threads' not in tables
    assert 'turn_canon_updates' not in tables
    assert 'turn_events' not in tables
    assert 'rate_limit_events' not in tables
    assert 'canon_jobs' not in tables
    assert 'session_state_mutation_audits' not in tables
    assert 'operator_action_audits' not in tables
    entry_cols_after_down = {col['name'] for col in inspector.get_columns('session_log_entries')}
    assert 'metadata_json' not in entry_cols_after_down
    campaign_cols_after_down = {col['name'] for col in inspector.get_columns('campaigns')}
    assert 'updated_at' not in campaign_cols_after_down
    session_cols_after_down = {col['name'] for col in inspector.get_columns('sessions')}
    assert 'name' not in session_cols_after_down

    _run_flask_db(['upgrade', 'head'], env)

    inspector = _inspect_db(db_uri)
    tables = set(inspector.get_table_names())
    assert 'dm_turns' in tables
    assert 'session_states' in tables
    assert 'dm_coherence_feedback' in tables
    assert 'story_entities' in tables
    assert 'story_facts' in tables
    assert 'story_threads' in tables
    assert 'turn_canon_updates' in tables
    assert 'turn_events' in tables
    assert 'rate_limit_events' in tables
    assert 'canon_jobs' in tables
    assert 'session_state_mutation_audits' in tables
    assert 'operator_action_audits' in tables
    feedback_cols_after_reupgrade = {col['name'] for col in inspector.get_columns('dm_coherence_feedback')}
    assert {'feedback_type', 'category', 'provider', 'model', 'metadata_json'}.issubset(
        feedback_cols_after_reupgrade
    )
    session_cols_after_reupgrade = {col['name'] for col in inspector.get_columns('sessions')}
    assert {'name', 'status', 'updated_at', 'deleted_at', 'client_session_id', 'archived_by_campaign_id'}.issubset(
        session_cols_after_reupgrade
    )


def test_migration_chain_downgrade_to_base_and_reupgrade(tmp_path):
    db_path = tmp_path / 'migration_chain_full_reset.db'
    db_uri = f'sqlite:///{db_path}'

    env = os.environ.copy()
    env['FLASK_APP'] = 'aidm_server.main:create_app'
    env['PYTHONPATH'] = str(REPO_ROOT)
    env['AIDM_ENV'] = 'test'
    env['AIDM_DEBUG'] = 'false'
    env['AIDM_SOCKETIO_ASYNC_MODE'] = 'threading'
    env['AIDM_AUTO_CREATE_SCHEMA'] = 'false'
    env['AIDM_DATABASE_URI'] = db_uri
    env['AIDM_TELEMETRY_ENABLED'] = 'false'

    _run_flask_db(['upgrade', 'head'], env)
    _run_flask_db(['downgrade', 'base'], env)

    inspector = _inspect_db(db_uri)
    tables = set(inspector.get_table_names())
    assert tables == {'alembic_version'}

    _run_flask_db(['upgrade', 'head'], env)
    inspector = _inspect_db(db_uri)
    tables = set(inspector.get_table_names())
    assert 'dm_turns' in tables
    assert 'session_states' in tables
    assert 'dm_coherence_feedback' in tables
    assert 'story_entities' in tables
    assert 'story_facts' in tables
    assert 'story_threads' in tables
    assert 'turn_canon_updates' in tables
    assert 'turn_events' in tables
    assert 'rate_limit_events' in tables
    assert 'canon_jobs' in tables
    feedback_cols = {col['name'] for col in inspector.get_columns('dm_coherence_feedback')}
    assert {'feedback_type', 'category', 'provider', 'model', 'metadata_json'}.issubset(feedback_cols)
    campaign_cols = {col['name'] for col in inspector.get_columns('campaigns')}
    session_cols = {col['name'] for col in inspector.get_columns('sessions')}
    assert {'updated_at', 'status'}.issubset(campaign_cols)
    assert {'name', 'status', 'updated_at', 'deleted_at', 'client_session_id', 'archived_by_campaign_id'}.issubset(
        session_cols
    )
