"""database-enforced DM turn idempotency

Revision ID: 0027_dm_turn_client_message_id
Revises: 0026_operator_action_audits
Create Date: 2026-07-09 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = '0027_dm_turn_client_message_id'
down_revision = '0026_operator_action_audits'
branch_labels = None
depends_on = None


INDEX_NAME = 'uq_dm_turns_session_player_client_message'


def _column_names():
    return {column['name'] for column in sa.inspect(op.get_bind()).get_columns('dm_turns')}


def _index_names():
    return {index['name'] for index in sa.inspect(op.get_bind()).get_indexes('dm_turns')}


def upgrade():
    if 'client_message_id' not in _column_names():
        with op.batch_alter_table('dm_turns', schema=None) as batch_op:
            batch_op.add_column(sa.Column('client_message_id', sa.String(length=80), nullable=True))

    if INDEX_NAME not in _index_names():
        op.create_index(
            INDEX_NAME,
            'dm_turns',
            ['session_id', 'player_id', 'client_message_id'],
            unique=True,
        )


def downgrade():
    if INDEX_NAME in _index_names():
        op.drop_index(INDEX_NAME, table_name='dm_turns')
    if 'client_message_id' in _column_names():
        with op.batch_alter_table('dm_turns', schema=None) as batch_op:
            batch_op.drop_column('client_message_id')
