"""add player sex column

Revision ID: 0016_player_sex
Revises: 0015_repair_campaign_world_workspaces
Create Date: 2026-06-08 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0016_player_sex'
down_revision = '0015_repair_campaign_world_workspaces'
branch_labels = None
depends_on = None


def _player_columns():
    return {column['name'] for column in sa.inspect(op.get_bind()).get_columns('players')}


def upgrade():
    if 'sex' not in _player_columns():
        with op.batch_alter_table('players', schema=None) as batch_op:
            batch_op.add_column(sa.Column('sex', sa.String(), nullable=True))


def downgrade():
    if 'sex' in _player_columns():
        with op.batch_alter_table('players', schema=None) as batch_op:
            batch_op.drop_column('sex')
