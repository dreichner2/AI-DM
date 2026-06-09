"""add structured race storage

Revision ID: 0017_structured_races
Revises: 0016_player_sex
Create Date: 2026-06-09 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0017_structured_races'
down_revision = '0016_player_sex'
branch_labels = None
depends_on = None


def _columns(table_name):
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column['name'] for column in inspector.get_columns(table_name)}


def upgrade():
    player_columns = _columns('players')
    if 'race_selection' not in player_columns:
        with op.batch_alter_table('players', schema=None) as batch_op:
            batch_op.add_column(sa.Column('race_selection', sa.Text(), nullable=True))

    if 'custom_races' not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            'custom_races',
            sa.Column('custom_race_id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('workspace_id', sa.String(length=80), server_default='owner', nullable=False),
            sa.Column('race_id', sa.String(length=120), nullable=False),
            sa.Column('version', sa.Integer(), nullable=False),
            sa.Column('name', sa.String(length=80), nullable=False),
            sa.Column('approval_status', sa.String(length=40), nullable=False),
            sa.Column('race_definition', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('custom_race_id', name=op.f('pk_custom_races')),
            sa.UniqueConstraint('workspace_id', 'race_id', 'version', name='uq_custom_races_workspace_race_version'),
        )
        op.create_index(op.f('ix_custom_races_workspace_id'), 'custom_races', ['workspace_id'], unique=False)
        op.create_index('ix_custom_races_workspace_race', 'custom_races', ['workspace_id', 'race_id'], unique=False)


def downgrade():
    if 'custom_races' in sa.inspect(op.get_bind()).get_table_names():
        op.drop_index('ix_custom_races_workspace_race', table_name='custom_races')
        op.drop_index(op.f('ix_custom_races_workspace_id'), table_name='custom_races')
        op.drop_table('custom_races')

    if 'race_selection' in _columns('players'):
        with op.batch_alter_table('players', schema=None) as batch_op:
            batch_op.drop_column('race_selection')
