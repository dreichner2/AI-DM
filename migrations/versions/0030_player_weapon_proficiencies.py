"""persist first-class player weapon proficiencies

Revision ID: 0030_player_weapon_proficiencies
Revises: 0029_players_account_fk
Create Date: 2026-07-11 21:55:00.000000

"""

from __future__ import annotations

from contextlib import contextmanager
import json
import re
from typing import Any

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0030_player_weapon_proficiencies'
down_revision = '0029_players_account_fk'
branch_labels = None
depends_on = None


_CLASS_SELECTORS: dict[str, tuple[str, ...]] = {
    'artificer': ('category:simple',),
    'barbarian': ('category:martial', 'category:simple'),
    'bard': (
        'category:simple',
        'weapon:hand crossbow',
        'weapon:longsword',
        'weapon:rapier',
        'weapon:shortsword',
    ),
    'cleric': ('category:simple',),
    'fighter': ('category:martial', 'category:simple'),
    'monk': ('category:simple', 'weapon:shortsword'),
    'paladin': ('category:martial', 'category:simple'),
    'ranger': ('category:martial', 'category:simple'),
    'rogue': (
        'category:simple',
        'weapon:hand crossbow',
        'weapon:longsword',
        'weapon:rapier',
        'weapon:shortsword',
    ),
    'warlock': ('category:simple',),
    'gunslinger': ('category:firearm', 'weapon:dagger'),
    'operative': ('category:firearm', 'weapon:knife'),
    'public safety officer': ('category:firearm',),
}
_SPECIFIC_CLASS_WEAPONS: dict[str, tuple[str, ...]] = {
    'druid': (
        'club',
        'dagger',
        'dart',
        'javelin',
        'mace',
        'quarterstaff',
        'scimitar',
        'sickle',
        'sling',
        'spear',
    ),
    'sorcerer': ('dagger', 'dart', 'light crossbow', 'quarterstaff', 'sling'),
    'wizard': ('dagger', 'dart', 'light crossbow', 'quarterstaff', 'sling'),
}


@contextmanager
def _sqlite_foreign_keys_disabled():
    """Allow SQLite to rebuild ``players`` while child tables reference it."""

    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        yield
        return

    with op.get_context().autocommit_block():
        original = bind.exec_driver_sql('PRAGMA foreign_keys').scalar()
        bind.exec_driver_sql('PRAGMA foreign_keys=OFF')
        try:
            yield
        finally:
            bind.exec_driver_sql(f'PRAGMA foreign_keys={int(bool(original))}')


def _normalized(value: Any) -> str:
    text = re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()
    return re.sub(r'\s+', ' ', text)


def _base_class(value: Any) -> str:
    return _normalized(str(value or '').split('-', 1)[0])


def _legacy_inventory_weapon_selectors(raw_value: Any) -> set[str]:
    try:
        loaded = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
    except (TypeError, ValueError):
        return set()
    if isinstance(loaded, dict):
        loaded = loaded.get('items')
    if not isinstance(loaded, list):
        return set()

    selectors: set[str] = set()
    for item in loaded:
        if not isinstance(item, dict) or _normalized(item.get('type')) != 'weapon':
            continue
        metadata = item.get('metadata') if isinstance(item.get('metadata'), dict) else {}
        explicit = any(
            source.get(key) is True
            for source in (item, metadata)
            for key in ('proficient', 'weaponProficient', 'weapon_proficient')
        )
        tags = {_normalized(tag) for tag in item.get('tags') or []}
        if not explicit and not tags.intersection(
            {'proficient', 'weapon proficient', 'weapon proficiency'}
        ):
            continue
        for value in (item.get('name'), item.get('subtype')):
            label = _normalized(value)
            if label:
                selectors.add(f'weapon:{label}')
    return selectors


def _backfill_profile(class_name: Any, inventory: Any) -> str:
    base_class = _base_class(class_name)
    legacy_selectors = _legacy_inventory_weapon_selectors(inventory)
    selectors = set(_CLASS_SELECTORS.get(base_class, ()))
    selectors.update(f'weapon:{name}' for name in _SPECIFIC_CLASS_WEAPONS.get(base_class, ()))
    # Inventory ownership is not proficiency. Only explicit legacy assertions
    # cross the migration boundary; unflagged off-class loot stays untrained.
    selectors.update(legacy_selectors)
    return json.dumps(sorted(selectors), separators=(',', ':'))


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'players' not in inspector.get_table_names():
        return
    columns = {column['name'] for column in inspector.get_columns('players')}
    if 'weapon_proficiencies' not in columns:
        with op.batch_alter_table('players', schema=None) as batch_op:
            batch_op.add_column(
                sa.Column(
                    'weapon_proficiencies',
                    sa.Text(),
                    nullable=False,
                    server_default='[]',
                )
            )

    players = sa.table(
        'players',
        sa.column('player_id', sa.Integer()),
        sa.column('class_', sa.String()),
        sa.column('inventory', sa.Text()),
        sa.column('weapon_proficiencies', sa.Text()),
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(players.c.player_id, players.c.class_, players.c.inventory)
    ).mappings()
    for row in rows:
        bind.execute(
            players.update()
            .where(players.c.player_id == row['player_id'])
            .values(
                weapon_proficiencies=_backfill_profile(
                    row.get('class_'),
                    row.get('inventory'),
                )
            )
        )


def downgrade():
    inspector = sa.inspect(op.get_bind())
    if 'players' not in inspector.get_table_names():
        return
    columns = {column['name'] for column in inspector.get_columns('players')}
    if 'weapon_proficiencies' not in columns:
        return
    with _sqlite_foreign_keys_disabled():
        with op.batch_alter_table('players', schema=None) as batch_op:
            batch_op.drop_column('weapon_proficiencies')
