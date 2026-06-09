"""accounts and player ownership

Revision ID: 0018_accounts_and_player_ownership
Revises: 0017_structured_races
Create Date: 2026-06-09 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0018_accounts_and_player_ownership'
down_revision = '0017_structured_races'
branch_labels = None
depends_on = None


def _tables():
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table_name):
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column['name'] for column in inspector.get_columns(table_name)}


def _index_names(table_name):
    if table_name not in _tables():
        return set()
    return {index['name'] for index in sa.inspect(op.get_bind()).get_indexes(table_name)}


def upgrade():
    tables = _tables()
    if 'accounts' not in tables:
        op.create_table(
            'accounts',
            sa.Column('account_id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('username', sa.String(length=80), nullable=False),
            sa.Column('first_name', sa.String(length=80), nullable=False),
            sa.Column('last_name', sa.String(length=80), nullable=False),
            sa.Column('password_hash', sa.String(length=255), nullable=True),
            sa.Column('account_token_hash', sa.String(length=64), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('account_id', name=op.f('pk_accounts')),
        )
        op.create_index('ix_accounts_username', 'accounts', ['username'], unique=True)
        op.create_index('ix_accounts_account_token_hash', 'accounts', ['account_token_hash'], unique=True)

    if 'account_workspace_memberships' not in tables:
        op.create_table(
            'account_workspace_memberships',
            sa.Column('membership_id', sa.Integer(), autoincrement=True, nullable=False),
            sa.Column('account_id', sa.Integer(), nullable=False),
            sa.Column('workspace_id', sa.String(length=80), server_default='owner', nullable=False),
            sa.Column('role', sa.String(length=32), nullable=False),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(['account_id'], ['accounts.account_id'], name=op.f('fk_account_workspace_memberships_account_id_accounts'), ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('membership_id', name=op.f('pk_account_workspace_memberships')),
            sa.UniqueConstraint('account_id', 'workspace_id', name='uq_account_workspace_membership'),
        )
        op.create_index(op.f('ix_account_workspace_memberships_workspace_id'), 'account_workspace_memberships', ['workspace_id'], unique=False)
        op.create_index('ix_account_workspace_memberships_workspace_role', 'account_workspace_memberships', ['workspace_id', 'role'], unique=False)

    player_columns = _columns('players')
    if 'account_id' not in player_columns:
        with op.batch_alter_table('players', schema=None) as batch_op:
            batch_op.add_column(sa.Column('account_id', sa.Integer(), nullable=True))

    indexes = _index_names('players')
    if 'ix_players_account_id' not in indexes:
        op.create_index(op.f('ix_players_account_id'), 'players', ['account_id'], unique=False)
    if 'ix_players_workspace_account_created_at' not in indexes:
        op.create_index(
            'ix_players_workspace_account_created_at',
            'players',
            ['workspace_id', 'account_id', 'created_at'],
            unique=False,
        )


def downgrade():
    indexes = _index_names('players')
    if 'ix_players_workspace_account_created_at' in indexes:
        op.drop_index('ix_players_workspace_account_created_at', table_name='players')
    if 'ix_players_account_id' in indexes:
        op.drop_index(op.f('ix_players_account_id'), table_name='players')

    if 'account_id' in _columns('players'):
        with op.batch_alter_table('players', schema=None) as batch_op:
            batch_op.drop_column('account_id')

    if 'account_workspace_memberships' in _tables():
        indexes = _index_names('account_workspace_memberships')
        if 'ix_account_workspace_memberships_workspace_role' in indexes:
            op.drop_index('ix_account_workspace_memberships_workspace_role', table_name='account_workspace_memberships')
        if op.f('ix_account_workspace_memberships_workspace_id') in indexes:
            op.drop_index(op.f('ix_account_workspace_memberships_workspace_id'), table_name='account_workspace_memberships')
        op.drop_table('account_workspace_memberships')

    if 'accounts' in _tables():
        indexes = _index_names('accounts')
        if 'ix_accounts_account_token_hash' in indexes:
            op.drop_index('ix_accounts_account_token_hash', table_name='accounts')
        if 'ix_accounts_username' in indexes:
            op.drop_index('ix_accounts_username', table_name='accounts')
        op.drop_table('accounts')
