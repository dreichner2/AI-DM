from __future__ import annotations

from aidm_server.database import db
from aidm_server.models import Campaign, CampaignSegment, DmTurn, Player, Session, World
from aidm_server.time_utils import utc_now


def seed_world_campaign_player_session(app):
    with app.app_context():
        world = World(name='Test World', description='A realm for tests')
        db.session.add(world)
        db.session.flush()

        campaign = Campaign(
            title='Test Campaign',
            description='Campaign for tests',
            world_id=world.world_id,
            current_quest='Find the relic',
            location='Old Ruins',
        )
        db.session.add(campaign)
        db.session.flush()

        player = Player(
            campaign_id=campaign.campaign_id,
            name='Alice',
            character_name='Seraphina',
            race='Elf',
            class_='Ranger',
            level=3,
        )
        db.session.add(player)
        db.session.flush()

        session = Session(campaign_id=campaign.campaign_id)
        db.session.add(session)
        db.session.commit()

        return {
            'world_id': world.world_id,
            'campaign_id': campaign.campaign_id,
            'player_id': player.player_id,
            'session_id': session.session_id,
        }


def seed_segment(app, campaign_id: int, trigger_condition: str):
    with app.app_context():
        segment = CampaignSegment(
            campaign_id=campaign_id,
            title='Hidden Chamber Unlocked',
            description='The chamber awakens.',
            trigger_condition=trigger_condition,
            tags='chamber,secret',
            is_triggered=False,
        )
        db.session.add(segment)
        db.session.commit()
        return segment.segment_id


def seed_played_turn(app, session_id: int) -> int:
    """A completed turn in the session, so it is a chapter that has been played."""

    with app.app_context():
        session_obj = db.session.get(Session, session_id)
        turn = DmTurn(
            session_id=session_id,
            campaign_id=session_obj.campaign_id,
            player_input='I look around.',
            dm_output='The road runs on into the dusk.',
            status='completed',
            completed_at=utc_now(),
        )
        db.session.add(turn)
        db.session.commit()
        return turn.turn_id
