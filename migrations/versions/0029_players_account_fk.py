"""enforce player account ownership foreign key

Revision ID: 0029_players_account_fk
Revises: 0028_session_turn_lock_fencing
Create Date: 2026-07-09 00:00:00.000000

"""

from contextlib import contextmanager

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0029_players_account_fk'
down_revision = '0028_session_turn_lock_fencing'
branch_labels = None
depends_on = None


CONSTRAINT_NAME = 'fk_players_account_id_accounts'


@contextmanager
def _sqlite_foreign_keys_disabled():
    """Allow SQLite to rebuild ``players`` while child tables reference it."""
    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        yield
        return

    # SQLite ignores PRAGMA foreign_keys changes while a transaction is active.
    # Alembic's autocommit block gives the table rebuild a connection state in
    # which both the disable and the restore take effect on this same connection.
    with op.get_context().autocommit_block():
        original = bind.exec_driver_sql('PRAGMA foreign_keys').scalar()
        bind.exec_driver_sql('PRAGMA foreign_keys=OFF')
        try:
            yield
        finally:
            bind.exec_driver_sql(f'PRAGMA foreign_keys={int(bool(original))}')


def _player_account_foreign_key():
    inspector = sa.inspect(op.get_bind())
    if 'players' not in inspector.get_table_names():
        return None
    for foreign_key in inspector.get_foreign_keys('players'):
        if (
            foreign_key.get('constrained_columns') == ['account_id']
            and foreign_key.get('referred_table') == 'accounts'
            and foreign_key.get('referred_columns') == ['account_id']
        ):
            return foreign_key
    return None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if 'players' not in tables or 'accounts' not in tables:
        return
    player_columns = {column['name'] for column in inspector.get_columns('players')}
    if 'account_id' not in player_columns or _player_account_foreign_key() is not None:
        return

    with _sqlite_foreign_keys_disabled():
        # Older databases could contain orphaned IDs because the original column
        # migration omitted the model-declared constraint. Preserve those players
        # and restore the intended nullable ownership semantics before adding it.
        op.execute(
            sa.text(
                'UPDATE players SET account_id = NULL '
                'WHERE account_id IS NOT NULL '
                'AND NOT EXISTS ('
                'SELECT 1 FROM accounts WHERE accounts.account_id = players.account_id'
                ')'
            )
        )
        with op.batch_alter_table('players', schema=None) as batch_op:
            batch_op.create_foreign_key(
                CONSTRAINT_NAME,
                'accounts',
                ['account_id'],
                ['account_id'],
                ondelete='SET NULL',
            )


def downgrade():
    foreign_key = _player_account_foreign_key()
    if foreign_key is None:
        return
    with _sqlite_foreign_keys_disabled():
        with op.batch_alter_table('players', schema=None) as batch_op:
            batch_op.drop_constraint(
                foreign_key.get('name') or CONSTRAINT_NAME,
                type_='foreignkey',
            )
