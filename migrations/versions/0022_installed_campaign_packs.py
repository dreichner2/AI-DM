"""campaign pack durable source and progress tables

Revision ID: 0022_installed_campaign_packs
Revises: 0021_workspace_tables
Create Date: 2026-06-12 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = '0022_installed_campaign_packs'
down_revision = '0021_workspace_tables'
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name):
    if table_name not in _tables():
        return set()
    return {index['name'] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade():
    tables = _tables()
    if 'installed_campaign_packs' not in tables:
        op.create_table(
            'installed_campaign_packs',
            sa.Column('installed_pack_id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('workspace_id', sa.String(length=80), server_default='owner', nullable=False),
            sa.Column('pack_id', sa.String(length=120), nullable=False),
            sa.Column('title', sa.String(length=120), nullable=False),
            sa.Column('pack_version', sa.String(length=80), nullable=False),
            sa.Column('schema_version', sa.String(length=20), nullable=False),
            sa.Column('pack_hash', sa.String(length=64), nullable=False),
            sa.Column('source_filename', sa.String(length=255), nullable=True),
            sa.Column('imported_by_account_id', sa.Integer(), nullable=True),
            sa.Column('manifest_json', sa.Text(), nullable=False),
            sa.Column('validated_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ['imported_by_account_id'],
                ['accounts.account_id'],
                name=op.f('fk_installed_campaign_packs_imported_by_account_id_accounts'),
                ondelete='SET NULL',
            ),
            sa.PrimaryKeyConstraint('installed_pack_id', name=op.f('pk_installed_campaign_packs')),
        )
        op.create_index('ix_installed_campaign_packs_workspace_id', 'installed_campaign_packs', ['workspace_id'])
        op.create_index(
            'ix_installed_campaign_packs_workspace_pack',
            'installed_campaign_packs',
            ['workspace_id', 'pack_id', 'pack_version'],
        )
        op.create_index(
            'ix_installed_campaign_packs_workspace_hash',
            'installed_campaign_packs',
            ['workspace_id', 'pack_hash'],
            unique=True,
        )
        op.create_index(
            'ix_installed_campaign_packs_imported_by',
            'installed_campaign_packs',
            ['imported_by_account_id', 'validated_at'],
        )
        op.create_index('ix_installed_campaign_packs_validated_at', 'installed_campaign_packs', ['validated_at'])

    tables = _tables()
    if 'campaign_packs' not in tables:
        op.create_table(
            'campaign_packs',
            sa.Column('campaign_pack_id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('workspace_id', sa.String(length=80), server_default='owner', nullable=False),
            sa.Column('installed_pack_id', sa.Integer(), nullable=True),
            sa.Column('pack_id', sa.String(length=120), nullable=False),
            sa.Column('title', sa.String(length=120), nullable=False),
            sa.Column('pack_version', sa.String(length=80), nullable=False),
            sa.Column('schema_version', sa.String(length=20), nullable=False),
            sa.Column('pack_hash', sa.String(length=64), nullable=False),
            sa.Column('manifest_json', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ['installed_pack_id'],
                ['installed_campaign_packs.installed_pack_id'],
                name=op.f('fk_campaign_packs_installed_pack_id_installed_campaign_packs'),
                ondelete='SET NULL',
            ),
            sa.PrimaryKeyConstraint('campaign_pack_id', name=op.f('pk_campaign_packs')),
            sa.UniqueConstraint('workspace_id', 'pack_hash', name='uq_campaign_packs_workspace_hash'),
        )
        op.create_index('ix_campaign_packs_workspace_id', 'campaign_packs', ['workspace_id'])
        op.create_index('ix_campaign_packs_workspace_pack', 'campaign_packs', ['workspace_id', 'pack_id', 'pack_version'])
        op.create_index('ix_campaign_packs_installed_pack', 'campaign_packs', ['installed_pack_id'])

    tables = _tables()
    if 'campaign_pack_records' not in tables:
        op.create_table(
            'campaign_pack_records',
            sa.Column('record_pk', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('campaign_pack_id', sa.Integer(), nullable=False),
            sa.Column('workspace_id', sa.String(length=80), server_default='owner', nullable=False),
            sa.Column('pack_id', sa.String(length=120), nullable=False),
            sa.Column('record_type', sa.String(length=40), nullable=False),
            sa.Column('record_id', sa.String(length=120), nullable=False),
            sa.Column('title', sa.String(length=160), nullable=True),
            sa.Column('visibility', sa.String(length=32), nullable=False),
            sa.Column('sort_order', sa.Integer(), nullable=False),
            sa.Column('record_json', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ['campaign_pack_id'],
                ['campaign_packs.campaign_pack_id'],
                name=op.f('fk_campaign_pack_records_campaign_pack_id_campaign_packs'),
                ondelete='CASCADE',
            ),
            sa.PrimaryKeyConstraint('record_pk', name=op.f('pk_campaign_pack_records')),
            sa.UniqueConstraint('campaign_pack_id', 'record_type', 'record_id', name='uq_campaign_pack_records_identity'),
        )
        op.create_index('ix_campaign_pack_records_campaign_pack_id', 'campaign_pack_records', ['campaign_pack_id'])
        op.create_index('ix_campaign_pack_records_workspace_id', 'campaign_pack_records', ['workspace_id'])
        op.create_index('ix_campaign_pack_records_pack_type', 'campaign_pack_records', ['campaign_pack_id', 'record_type'])
        op.create_index('ix_campaign_pack_records_workspace_type', 'campaign_pack_records', ['workspace_id', 'record_type'])

    tables = _tables()
    if 'campaign_pack_sessions' not in tables:
        op.create_table(
            'campaign_pack_sessions',
            sa.Column('campaign_pack_session_id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('campaign_pack_id', sa.Integer(), nullable=True),
            sa.Column('installed_pack_id', sa.Integer(), nullable=True),
            sa.Column('session_id', sa.Integer(), nullable=False),
            sa.Column('campaign_id', sa.Integer(), nullable=False),
            sa.Column('workspace_id', sa.String(length=80), server_default='owner', nullable=False),
            sa.Column('pack_id', sa.String(length=120), nullable=False),
            sa.Column('pack_title', sa.String(length=120), nullable=True),
            sa.Column('pack_version', sa.String(length=80), nullable=True),
            sa.Column('active_checkpoint_id', sa.String(length=120), nullable=True),
            sa.Column('progress_revision', sa.Integer(), nullable=False),
            sa.Column('snapshot_schema_version', sa.Integer(), nullable=False),
            sa.Column('progress_schema_version', sa.Integer(), nullable=False),
            sa.Column('progress_events_version', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(length=32), nullable=False),
            sa.Column('multi_session_group_key', sa.String(length=120), nullable=True),
            sa.Column('gm_notes_json', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ['campaign_pack_id'],
                ['campaign_packs.campaign_pack_id'],
                name=op.f('fk_campaign_pack_sessions_campaign_pack_id_campaign_packs'),
                ondelete='SET NULL',
            ),
            sa.ForeignKeyConstraint(
                ['installed_pack_id'],
                ['installed_campaign_packs.installed_pack_id'],
                name=op.f('fk_campaign_pack_sessions_installed_pack_id_installed_campaign_packs'),
                ondelete='SET NULL',
            ),
            sa.ForeignKeyConstraint(
                ['session_id'],
                ['sessions.session_id'],
                name=op.f('fk_campaign_pack_sessions_session_id_sessions'),
                ondelete='CASCADE',
            ),
            sa.ForeignKeyConstraint(
                ['campaign_id'],
                ['campaigns.campaign_id'],
                name=op.f('fk_campaign_pack_sessions_campaign_id_campaigns'),
                ondelete='CASCADE',
            ),
            sa.PrimaryKeyConstraint('campaign_pack_session_id', name=op.f('pk_campaign_pack_sessions')),
            sa.UniqueConstraint('session_id', name='uq_campaign_pack_sessions_session_id'),
        )
        op.create_index('ix_campaign_pack_sessions_workspace_id', 'campaign_pack_sessions', ['workspace_id'])
        op.create_index('ix_campaign_pack_sessions_campaign_status', 'campaign_pack_sessions', ['campaign_id', 'status'])
        op.create_index('ix_campaign_pack_sessions_pack', 'campaign_pack_sessions', ['campaign_pack_id'])
        op.create_index('ix_campaign_pack_sessions_workspace_pack', 'campaign_pack_sessions', ['workspace_id', 'pack_id'])

    tables = _tables()
    if 'campaign_pack_checkpoint_progress' not in tables:
        op.create_table(
            'campaign_pack_checkpoint_progress',
            sa.Column('checkpoint_progress_id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('campaign_pack_session_id', sa.Integer(), nullable=False),
            sa.Column('checkpoint_id', sa.String(length=120), nullable=False),
            sa.Column('title', sa.String(length=160), nullable=True),
            sa.Column('status', sa.String(length=32), nullable=False),
            sa.Column('sort_order', sa.Integer(), nullable=False),
            sa.Column('progress_revision', sa.Integer(), nullable=False),
            sa.Column('activated_at', sa.DateTime(), nullable=True),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.Column('skipped_at', sa.DateTime(), nullable=True),
            sa.Column('failed_at', sa.DateTime(), nullable=True),
            sa.Column('metadata_json', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ['campaign_pack_session_id'],
                ['campaign_pack_sessions.campaign_pack_session_id'],
                name=op.f('fk_campaign_pack_checkpoint_progress_campaign_pack_session_id_campaign_pack_sessions'),
                ondelete='CASCADE',
            ),
            sa.PrimaryKeyConstraint('checkpoint_progress_id', name=op.f('pk_campaign_pack_checkpoint_progress')),
            sa.UniqueConstraint(
                'campaign_pack_session_id',
                'checkpoint_id',
                name='uq_campaign_pack_checkpoint_progress_identity',
            ),
        )
        op.create_index(
            'ix_campaign_pack_checkpoint_progress_campaign_pack_session_id',
            'campaign_pack_checkpoint_progress',
            ['campaign_pack_session_id'],
        )
        op.create_index(
            'ix_campaign_pack_checkpoint_progress_status',
            'campaign_pack_checkpoint_progress',
            ['campaign_pack_session_id', 'status'],
        )

    tables = _tables()
    if 'campaign_pack_progress_events' not in tables:
        op.create_table(
            'campaign_pack_progress_events',
            sa.Column('progress_event_id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('campaign_pack_session_id', sa.Integer(), nullable=False),
            sa.Column('session_id', sa.Integer(), nullable=False),
            sa.Column('campaign_id', sa.Integer(), nullable=False),
            sa.Column('turn_id', sa.Integer(), nullable=True),
            sa.Column('turn_event_id', sa.Integer(), nullable=True),
            sa.Column('event_type', sa.String(length=80), nullable=False),
            sa.Column('action', sa.String(length=40), nullable=False),
            sa.Column('actor', sa.String(length=120), nullable=True),
            sa.Column('from_checkpoint_id', sa.String(length=120), nullable=True),
            sa.Column('to_checkpoint_id', sa.String(length=120), nullable=True),
            sa.Column('reason', sa.Text(), nullable=True),
            sa.Column('progress_revision', sa.Integer(), nullable=False),
            sa.Column('idempotency_key', sa.String(length=160), nullable=True),
            sa.Column('payload_json', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ['campaign_pack_session_id'],
                ['campaign_pack_sessions.campaign_pack_session_id'],
                name=op.f('fk_campaign_pack_progress_events_campaign_pack_session_id_campaign_pack_sessions'),
                ondelete='CASCADE',
            ),
            sa.ForeignKeyConstraint(
                ['session_id'],
                ['sessions.session_id'],
                name=op.f('fk_campaign_pack_progress_events_session_id_sessions'),
                ondelete='CASCADE',
            ),
            sa.ForeignKeyConstraint(
                ['campaign_id'],
                ['campaigns.campaign_id'],
                name=op.f('fk_campaign_pack_progress_events_campaign_id_campaigns'),
                ondelete='CASCADE',
            ),
            sa.ForeignKeyConstraint(
                ['turn_id'],
                ['dm_turns.turn_id'],
                name=op.f('fk_campaign_pack_progress_events_turn_id_dm_turns'),
                ondelete='SET NULL',
            ),
            sa.ForeignKeyConstraint(
                ['turn_event_id'],
                ['turn_events.event_id'],
                name=op.f('fk_campaign_pack_progress_events_turn_event_id_turn_events'),
                ondelete='SET NULL',
            ),
            sa.PrimaryKeyConstraint('progress_event_id', name=op.f('pk_campaign_pack_progress_events')),
            sa.UniqueConstraint(
                'campaign_pack_session_id',
                'idempotency_key',
                name='uq_campaign_pack_progress_events_idempotency',
            ),
        )
        op.create_index(
            'ix_campaign_pack_progress_events_campaign_pack_session_id',
            'campaign_pack_progress_events',
            ['campaign_pack_session_id'],
        )
        op.create_index('ix_campaign_pack_progress_events_session_id', 'campaign_pack_progress_events', ['session_id'])
        op.create_index('ix_campaign_pack_progress_events_campaign_id', 'campaign_pack_progress_events', ['campaign_id'])
        op.create_index('ix_campaign_pack_progress_events_turn_id', 'campaign_pack_progress_events', ['turn_id'])
        op.create_index('ix_campaign_pack_progress_events_turn_event_id', 'campaign_pack_progress_events', ['turn_event_id'])
        op.create_index('ix_campaign_pack_progress_events_created_at', 'campaign_pack_progress_events', ['created_at'])
        op.create_index(
            'ix_campaign_pack_progress_events_session_revision',
            'campaign_pack_progress_events',
            ['campaign_pack_session_id', 'progress_revision'],
        )
        op.create_index(
            'ix_campaign_pack_progress_events_session_created',
            'campaign_pack_progress_events',
            ['session_id', 'created_at'],
        )


def downgrade():
    for table_name in [
        'campaign_pack_progress_events',
        'campaign_pack_checkpoint_progress',
        'campaign_pack_sessions',
        'campaign_pack_records',
        'campaign_packs',
        'installed_campaign_packs',
    ]:
        if table_name in _tables():
            op.drop_table(table_name)
