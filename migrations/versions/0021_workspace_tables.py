"""workspace table access records

Revision ID: 0021_workspace_tables
Revises: 0020_combat_bestiary
Create Date: 2026-06-11 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = '0021_workspace_tables'
down_revision = '0020_combat_bestiary'
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table_name):
    if table_name not in _tables():
        return set()
    return {index['name'] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade():
    if 'workspaces' not in _tables():
        op.create_table(
            'workspaces',
            sa.Column('workspace_id', sa.String(length=80), nullable=False),
            sa.Column('name', sa.String(length=120), nullable=False),
            sa.Column('name_key', sa.String(length=120), nullable=False),
            sa.Column('password_hash', sa.String(length=255), nullable=True),
            sa.Column('token_hash', sa.String(length=64), nullable=True),
            sa.Column('created_by_account_id', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ['created_by_account_id'],
                ['accounts.account_id'],
                name=op.f('fk_workspaces_created_by_account_id_accounts'),
                ondelete='SET NULL',
            ),
            sa.PrimaryKeyConstraint('workspace_id', name=op.f('pk_workspaces')),
        )
        op.create_index('ix_workspaces_name_key', 'workspaces', ['name_key'], unique=True)
        op.create_index('ix_workspaces_token_hash', 'workspaces', ['token_hash'], unique=True)
        op.create_index(
            'ix_workspaces_created_by_account',
            'workspaces',
            ['created_by_account_id', 'created_at'],
            unique=False,
        )


def downgrade():
    if 'workspaces' not in _tables():
        return
    for index_name in [
        'ix_workspaces_created_by_account',
        'ix_workspaces_token_hash',
        'ix_workspaces_name_key',
    ]:
        if index_name in _index_names('workspaces'):
            op.drop_index(index_name, table_name='workspaces')
    op.drop_table('workspaces')
