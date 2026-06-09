from __future__ import annotations

import os
import logging
import pathlib
import stat

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import MetaData, event, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.pool import NullPool

from aidm_server.logging_context import configure_logging

configure_logging()
logger = logging.getLogger(__name__)

try:
    from flask_migrate import Migrate
except ImportError:  # pragma: no cover - exercised only in minimal runtime installs.
    class Migrate:  # type: ignore[no-redef]
        def init_app(self, *_args, **_kwargs):
            logger.warning('Flask-Migrate is not installed; migration CLI integration is disabled.')


convention = {
    'ix': 'ix_%(column_0_label)s',
    'uq': 'uq_%(table_name)s_%(column_0_name)s',
    'ck': 'ck_%(table_name)s_%(constraint_name)s',
    'fk': 'fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s',
    'pk': 'pk_%(table_name)s',
}

metadata = MetaData(naming_convention=convention)
db = SQLAlchemy(metadata=metadata)
migrate = Migrate()


@event.listens_for(Engine, 'connect')
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    if 'sqlite' not in type(dbapi_connection).__module__:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute('PRAGMA foreign_keys=ON')
    finally:
        cursor.close()


def _resolve_sqlite_uri(database_uri: str, root_path: str) -> str:
    if not database_uri.startswith('sqlite:///'):
        return database_uri

    relative_path = database_uri.replace('sqlite:///', '', 1)
    if relative_path == ':memory:' or relative_path.startswith(':memory:?'):
        return database_uri
    if os.path.isabs(relative_path):
        os.makedirs(os.path.dirname(relative_path), exist_ok=True)
        return database_uri

    absolute_path = os.path.join(root_path, relative_path)
    os.makedirs(os.path.dirname(absolute_path), exist_ok=True)
    return f'sqlite:///{absolute_path}'


def sqlite_database_path(database_uri: str, root_path: str | os.PathLike | None = None) -> pathlib.Path | None:
    try:
        url = make_url(database_uri)
    except Exception:
        return None

    if not url.drivername.startswith('sqlite'):
        return None
    if not url.database or url.database == ':memory:':
        return None

    path = pathlib.Path(url.database)
    if not path.is_absolute() and root_path is not None:
        path = pathlib.Path(root_path) / path
    return path


def _chmod_private(path: pathlib.Path, mode: int) -> bool:
    if not path.exists():
        return False
    current_mode = stat.S_IMODE(path.stat().st_mode)
    if current_mode != mode:
        path.chmod(mode)
        return True
    return False


def harden_sqlite_permissions(database_uri: str, root_path: str | os.PathLike | None = None) -> list[str]:
    database_path = sqlite_database_path(database_uri, root_path)
    if database_path is None:
        return []

    changed: list[str] = []
    database_path.parent.mkdir(parents=True, exist_ok=True)
    local_data_dir = database_path.parent.name in {'instance', '.aidm'}
    if local_data_dir and _chmod_private(database_path.parent, 0o700):
        changed.append(str(database_path.parent))

    sqlite_files = {database_path}
    if local_data_dir:
        for pattern in ('*.db', '*.sqlite', '*.sqlite3'):
            sqlite_files.update(database_path.parent.glob(pattern))

    for sqlite_file in sorted(sqlite_files):
        if sqlite_file.is_file() and _chmod_private(sqlite_file, 0o600):
            changed.append(str(sqlite_file))

    return changed


def engine_options_for_database_uri(database_uri: str) -> dict:
    try:
        url = make_url(database_uri)
    except Exception:
        return {}
    if not url.drivername.startswith('sqlite'):
        return {}
    return {
        'poolclass': NullPool,
        'connect_args': {
            'check_same_thread': False,
            'timeout': 30,
        },
    }


def init_db(app):
    """Initialize database and migrations for the Flask app."""
    try:
        configured_uri = app.config.get('SQLALCHEMY_DATABASE_URI', 'sqlite:///instance/dnd_ai_dm.db')
        database_uri = _resolve_sqlite_uri(configured_uri, app.root_path)
        harden_sqlite_permissions(database_uri)

        app.config['SQLALCHEMY_DATABASE_URI'] = database_uri
        app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_options_for_database_uri(database_uri)
        app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

        db.init_app(app)
        migrate.init_app(app, db, render_as_batch=True)

        logger.info('Database initialized: %s', database_uri)
    except Exception as exc:
        logger.error('Error initializing database: %s', str(exc))
        raise


def ensure_schema(app):
    with app.app_context():
        db.create_all()
        _ensure_legacy_sqlite_columns()
        harden_sqlite_permissions(app.config.get('SQLALCHEMY_DATABASE_URI', ''), app.root_path)


def _ensure_legacy_sqlite_columns():
    if db.engine.dialect.name != 'sqlite':
        return
    inspector = inspect(db.engine)
    table_names = set(inspector.get_table_names())
    if 'accounts' not in table_names:
        from aidm_server.models import Account

        Account.__table__.create(db.engine, checkfirst=True)
    if 'account_workspace_memberships' not in table_names:
        from aidm_server.models import AccountWorkspaceMembership

        AccountWorkspaceMembership.__table__.create(db.engine, checkfirst=True)
    if 'players' not in table_names:
        return
    player_columns = {column['name'] for column in inspector.get_columns('players')}
    with db.engine.begin() as connection:
        if 'account_id' not in player_columns:
            connection.execute(text('ALTER TABLE players ADD COLUMN account_id INTEGER'))
        if 'sex' not in player_columns:
            connection.execute(text('ALTER TABLE players ADD COLUMN sex VARCHAR'))
        if 'race_selection' not in player_columns:
            connection.execute(text('ALTER TABLE players ADD COLUMN race_selection TEXT'))
        connection.execute(text("UPDATE players SET sex = 'male' WHERE sex IS NULL OR TRIM(sex) = ''"))


def get_engine():
    return db.engine


def get_session():
    from sqlalchemy.orm import sessionmaker

    session_factory = sessionmaker(bind=db.engine)
    return session_factory()
