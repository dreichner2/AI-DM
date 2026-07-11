from __future__ import annotations

import pytest
from sqlalchemy import text

from aidm_server.database import db, release_clean_scoped_session
from aidm_server.models import SessionLogEntry
from tests.helpers import seed_world_campaign_player_session


def test_release_clean_scoped_session_returns_read_connection(app):
    with app.app_context():
        db.session.execute(text('SELECT 1'))
        assert db.session().in_transaction() is True

        release_clean_scoped_session(boundary='test provider')

        assert db.session().in_transaction() is False


def test_release_clean_scoped_session_rejects_unflushed_writes(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        db.session.add(
            SessionLogEntry(
                session_id=ids['session_id'],
                message='must remain pending',
                entry_type='system',
            )
        )

        with pytest.raises(RuntimeError, match='pending test provider boundary writes'):
            release_clean_scoped_session(boundary='test provider')

        db.session.rollback()


def test_release_clean_scoped_session_rejects_already_flushed_writes(app):
    ids = seed_world_campaign_player_session(app)

    with app.app_context():
        db.session.add(
            SessionLogEntry(
                session_id=ids['session_id'],
                message='autoflushed write must remain pending',
                entry_type='system',
            )
        )
        db.session.flush()
        assert not db.session.new
        assert not db.session.dirty

        with pytest.raises(RuntimeError, match='pending test provider boundary writes'):
            release_clean_scoped_session(boundary='test provider')

        db.session.rollback()
        release_clean_scoped_session(boundary='test provider')
