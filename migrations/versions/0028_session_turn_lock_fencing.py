"""session turn lock fencing

Revision ID: 0028_session_turn_lock_fencing
Revises: 0027_dm_turn_client_message_id
Create Date: 2026-07-09 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0028_session_turn_lock_fencing'
down_revision = '0027_dm_turn_client_message_id'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('session_turn_locks', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('fencing_token', sa.BigInteger(), server_default='0', nullable=False)
        )


def downgrade():
    with op.batch_alter_table('session_turn_locks', schema=None) as batch_op:
        batch_op.drop_column('fencing_token')
