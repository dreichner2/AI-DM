"""combat bestiary and debug tables

Revision ID: 0020_combat_bestiary
Revises: 0019_custom_race_global_creator
Create Date: 2026-06-10 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = '0020_combat_bestiary'
down_revision = '0019_custom_race_global_creator'
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name):
    if table_name not in _tables():
        return set()
    return {index['name'] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def _create_index_once(name, table_name, columns):
    if name not in _index_names(table_name):
        op.create_index(name, table_name, columns, unique=False)


def upgrade():
    tables = _tables()
    if 'bestiary_entries' not in tables:
        op.create_table(
            'bestiary_entries',
            sa.Column('bestiary_entry_id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('workspace_id', sa.String(length=80), server_default='owner', nullable=False),
            sa.Column('campaign_id', sa.Integer(), nullable=True),
            sa.Column('session_id', sa.Integer(), nullable=True),
            sa.Column('scope', sa.String(length=32), nullable=False),
            sa.Column('creature_id', sa.String(length=120), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=120), nullable=False),
            sa.Column('source', sa.String(length=32), nullable=False),
            sa.Column('persistence', sa.String(length=32), nullable=False),
            sa.Column('region_id', sa.String(length=120), nullable=True),
            sa.Column('location_ids_json', sa.Text(), nullable=True),
            sa.Column('faction_ids_json', sa.Text(), nullable=True),
            sa.Column('tags_json', sa.Text(), nullable=True),
            sa.Column('creature_json', sa.Text(), nullable=False),
            sa.Column('balance_json', sa.Text(), nullable=True),
            sa.Column('created_because', sa.Text(), nullable=True),
            sa.Column('base_creature_id', sa.String(length=120), nullable=True),
            sa.Column('variant_reason', sa.Text(), nullable=True),
            sa.Column('created_at_turn', sa.Integer(), nullable=True),
            sa.Column('created_by_model', sa.String(length=160), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.campaign_id'], name=op.f('fk_bestiary_entries_campaign_id_campaigns'), ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['session_id'], ['sessions.session_id'], name=op.f('fk_bestiary_entries_session_id_sessions'), ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('bestiary_entry_id', name=op.f('pk_bestiary_entries')),
        )
        op.create_index(op.f('ix_bestiary_entries_workspace_id'), 'bestiary_entries', ['workspace_id'], unique=False)
        op.create_index(op.f('ix_bestiary_entries_campaign_id'), 'bestiary_entries', ['campaign_id'], unique=False)
        op.create_index(op.f('ix_bestiary_entries_session_id'), 'bestiary_entries', ['session_id'], unique=False)
        op.create_index('ix_bestiary_entries_workspace_scope_name', 'bestiary_entries', ['workspace_id', 'scope', 'name'], unique=False)
        op.create_index('ix_bestiary_entries_campaign_scope_region', 'bestiary_entries', ['campaign_id', 'scope', 'region_id'], unique=False)
        op.create_index('ix_bestiary_entries_session_scope', 'bestiary_entries', ['session_id', 'scope'], unique=False)
        op.create_index('ix_bestiary_entries_creature_id', 'bestiary_entries', ['creature_id'], unique=False)

    if 'combat_encounters' not in tables:
        op.create_table(
            'combat_encounters',
            sa.Column('combat_encounter_id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('session_id', sa.Integer(), nullable=False),
            sa.Column('campaign_id', sa.Integer(), nullable=False),
            sa.Column('status', sa.String(length=32), nullable=False),
            sa.Column('round', sa.Integer(), nullable=False),
            sa.Column('encounter_goal_json', sa.Text(), nullable=True),
            sa.Column('battlefield_json', sa.Text(), nullable=True),
            sa.Column('participant_ids_json', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.Column('ended_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.campaign_id'], name=op.f('fk_combat_encounters_campaign_id_campaigns'), ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['session_id'], ['sessions.session_id'], name=op.f('fk_combat_encounters_session_id_sessions'), ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('combat_encounter_id', name=op.f('pk_combat_encounters')),
        )
        op.create_index(op.f('ix_combat_encounters_session_id'), 'combat_encounters', ['session_id'], unique=False)
        op.create_index(op.f('ix_combat_encounters_campaign_id'), 'combat_encounters', ['campaign_id'], unique=False)
        op.create_index('ix_combat_encounters_session_status', 'combat_encounters', ['session_id', 'status'], unique=False)
        op.create_index('ix_combat_encounters_campaign_status_updated', 'combat_encounters', ['campaign_id', 'status', 'updated_at'], unique=False)

    if 'combat_debug_events' not in tables:
        op.create_table(
            'combat_debug_events',
            sa.Column('debug_event_id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('session_id', sa.Integer(), nullable=False),
            sa.Column('campaign_id', sa.Integer(), nullable=False),
            sa.Column('turn_id', sa.Integer(), nullable=True),
            sa.Column('combat_encounter_id', sa.Integer(), nullable=True),
            sa.Column('event_type', sa.String(length=80), nullable=False),
            sa.Column('payload_json', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.campaign_id'], name=op.f('fk_combat_debug_events_campaign_id_campaigns'), ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['combat_encounter_id'], ['combat_encounters.combat_encounter_id'], name=op.f('fk_combat_debug_events_combat_encounter_id_combat_encounters'), ondelete='SET NULL'),
            sa.ForeignKeyConstraint(['session_id'], ['sessions.session_id'], name=op.f('fk_combat_debug_events_session_id_sessions'), ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['turn_id'], ['dm_turns.turn_id'], name=op.f('fk_combat_debug_events_turn_id_dm_turns'), ondelete='SET NULL'),
            sa.PrimaryKeyConstraint('debug_event_id', name=op.f('pk_combat_debug_events')),
        )
        op.create_index(op.f('ix_combat_debug_events_session_id'), 'combat_debug_events', ['session_id'], unique=False)
        op.create_index(op.f('ix_combat_debug_events_campaign_id'), 'combat_debug_events', ['campaign_id'], unique=False)
        op.create_index(op.f('ix_combat_debug_events_turn_id'), 'combat_debug_events', ['turn_id'], unique=False)
        op.create_index(op.f('ix_combat_debug_events_combat_encounter_id'), 'combat_debug_events', ['combat_encounter_id'], unique=False)
        op.create_index(op.f('ix_combat_debug_events_created_at'), 'combat_debug_events', ['created_at'], unique=False)
        op.create_index('ix_combat_debug_events_session_created', 'combat_debug_events', ['session_id', 'created_at'], unique=False)
        op.create_index('ix_combat_debug_events_turn_type', 'combat_debug_events', ['turn_id', 'event_type'], unique=False)

    if 'bestiary_entries' in _tables():
        _create_index_once('ix_bestiary_entries_workspace_scope_name', 'bestiary_entries', ['workspace_id', 'scope', 'name'])
    if 'combat_encounters' in _tables():
        _create_index_once('ix_combat_encounters_session_status', 'combat_encounters', ['session_id', 'status'])
    if 'combat_debug_events' in _tables():
        _create_index_once('ix_combat_debug_events_session_created', 'combat_debug_events', ['session_id', 'created_at'])


def downgrade():
    tables = _tables()
    if 'combat_debug_events' in tables:
        for index_name in [
            'ix_combat_debug_events_turn_type',
            'ix_combat_debug_events_session_created',
            op.f('ix_combat_debug_events_created_at'),
            op.f('ix_combat_debug_events_combat_encounter_id'),
            op.f('ix_combat_debug_events_turn_id'),
            op.f('ix_combat_debug_events_campaign_id'),
            op.f('ix_combat_debug_events_session_id'),
        ]:
            if index_name in _index_names('combat_debug_events'):
                op.drop_index(index_name, table_name='combat_debug_events')
        op.drop_table('combat_debug_events')

    if 'combat_encounters' in tables:
        for index_name in [
            'ix_combat_encounters_campaign_status_updated',
            'ix_combat_encounters_session_status',
            op.f('ix_combat_encounters_campaign_id'),
            op.f('ix_combat_encounters_session_id'),
        ]:
            if index_name in _index_names('combat_encounters'):
                op.drop_index(index_name, table_name='combat_encounters')
        op.drop_table('combat_encounters')

    if 'bestiary_entries' in tables:
        for index_name in [
            'ix_bestiary_entries_creature_id',
            'ix_bestiary_entries_session_scope',
            'ix_bestiary_entries_campaign_scope_region',
            'ix_bestiary_entries_workspace_scope_name',
            op.f('ix_bestiary_entries_session_id'),
            op.f('ix_bestiary_entries_campaign_id'),
            op.f('ix_bestiary_entries_workspace_id'),
        ]:
            if index_name in _index_names('bestiary_entries'):
                op.drop_index(index_name, table_name='bestiary_entries')
        op.drop_table('bestiary_entries')
