"""global custom race creator metadata

Revision ID: 0019_custom_race_global_creator
Revises: 0018_accounts_and_player_ownership
Create Date: 2026-06-10 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0019_custom_race_global_creator'
down_revision = '0018_accounts_and_player_ownership'
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
    if 'custom_races' not in _tables():
        return

    columns = _columns('custom_races')
    with op.batch_alter_table('custom_races', schema=None) as batch_op:
        if 'account_id' not in columns:
            batch_op.add_column(
                sa.Column('account_id', sa.Integer(), sa.ForeignKey('accounts.account_id', ondelete='SET NULL'), nullable=True)
            )
        if 'creator_username' not in columns:
            batch_op.add_column(sa.Column('creator_username', sa.String(length=80), nullable=True))
        if 'creator_display_name' not in columns:
            batch_op.add_column(sa.Column('creator_display_name', sa.String(length=180), nullable=True))

    indexes = _index_names('custom_races')
    if 'ix_custom_races_account_id' not in indexes:
        op.create_index(op.f('ix_custom_races_account_id'), 'custom_races', ['account_id'], unique=False)
    if 'ix_custom_races_account_created_at' not in indexes:
        op.create_index(
            'ix_custom_races_account_created_at',
            'custom_races',
            ['account_id', 'created_at'],
            unique=False,
        )


def downgrade():
    if 'custom_races' not in _tables():
        return

    indexes = _index_names('custom_races')
    if 'ix_custom_races_account_created_at' in indexes:
        op.drop_index('ix_custom_races_account_created_at', table_name='custom_races')
    if 'ix_custom_races_account_id' in indexes:
        op.drop_index(op.f('ix_custom_races_account_id'), table_name='custom_races')

    columns = _columns('custom_races')
    with op.batch_alter_table('custom_races', schema=None) as batch_op:
        if 'creator_display_name' in columns:
            batch_op.drop_column('creator_display_name')
        if 'creator_username' in columns:
            batch_op.drop_column('creator_username')
        if 'account_id' in columns:
            batch_op.drop_column('account_id')
