from __future__ import annotations

import json
from datetime import timedelta

from aidm_server.database import db
from aidm_server.models import Campaign, Session, SessionLogEntry, SessionState, World
from aidm_server.time_utils import utc_now
from tests.helpers import seed_world_campaign_player_session

SUMMARY_FIELDS = {
    'session_id',
    'campaign_id',
    'name',
    'display_name',
    'status',
    'is_current',
    'is_archived',
    'chapter_number',
    'chapter_recap',
    'ended_at',
    'created_at',
    'updated_at',
    'latest_activity_at',
    'turn_count',
    'scene_title',
    'summary_excerpt',
    'continue_line',
    'imported',
    'play_now',
}
LARGE_SNAPSHOT_PADDING = 'x' * 20_000


def _seed_chapters(app, *, count: int, current_index: int = -1) -> dict:
    """A campaign of ``count`` chapters, one minute apart; only ``current_index`` is open.

    Every snapshot is large, as a long campaign's would be.
    """

    current_index %= count

    base = utc_now() - timedelta(days=30)
    with app.app_context():
        world = World(name='Paging World', description='paging')
        db.session.add(world)
        db.session.flush()
        campaign = Campaign(title='Paging Campaign', world_id=world.world_id, location='Old Road')
        db.session.add(campaign)
        db.session.flush()
        session_ids = []
        for index in range(count):
            moment = base + timedelta(minutes=index)
            session_obj = Session(
                campaign_id=campaign.campaign_id,
                name=f'Chapter {index + 1}',
                status='active' if index == current_index else 'ended',
                state_snapshot=json.dumps(
                    {
                        'chapter': {'number': index + 1, 'recap': f'Recap {index + 1}.'},
                        'currentScene': {'name': f'Scene {index + 1}'},
                        'ledgerPadding': LARGE_SNAPSHOT_PADDING,
                    }
                ),
                created_at=moment,
                updated_at=moment,
            )
            db.session.add(session_obj)
            db.session.flush()
            session_ids.append(session_obj.session_id)
        db.session.commit()
        return {'campaign_id': campaign.campaign_id, 'session_ids': session_ids}


def test_session_list_items_are_summaries_with_every_field(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        session_obj = db.session.get(Session, ids['session_id'])
        session_obj.state_snapshot = json.dumps(
            {
                'currentScene': {'name': 'The Drowned Lantern ' + 'and its long cellar ' * 10},
                'playNow': {'source': 'play_now'},
                'recap': 'The party ' + 'searched the inn. ' * 40,
                'ledgerPadding': LARGE_SNAPSHOT_PADDING,
            }
        )
        db.session.add(SessionState(session_id=ids['session_id'], rolling_summary='T1 | P1: search | DM: the inn'))
        db.session.commit()

    listed = client.get(f"/api/sessions/campaigns/{ids['campaign_id']}/sessions").get_json()['sessions'][0]
    workspace_item = client.get(f"/api/campaigns/{ids['campaign_id']}/workspace").get_json()['sessions'][0]

    assert set(listed) == SUMMARY_FIELDS
    assert workspace_item == listed
    assert listed['display_name'] == 'Session 1'
    assert listed['is_current'] is True
    assert listed['scene_title'].startswith('The Drowned Lantern')
    assert len(listed['scene_title']) <= 120
    # The saved recap, cut to fit; never the narrator's rolling summary.
    assert listed['summary_excerpt'].startswith('The party searched the inn.')
    assert len(listed['summary_excerpt']) <= 280
    assert listed['summary_excerpt'].endswith('…')
    assert listed['play_now'] is True
    assert listed['imported'] is False
    assert LARGE_SNAPSHOT_PADDING not in json.dumps(listed)


def test_display_name_falls_back_to_snapshot_title_then_chapter_then_session_number(client, app):
    ids = seed_world_campaign_player_session(app)
    with app.app_context():
        titled = Session(campaign_id=ids['campaign_id'], state_snapshot=json.dumps({'title': 'Snapshot Title'}))
        chapter = Session(campaign_id=ids['campaign_id'], state_snapshot=json.dumps({'chapter': {'number': 7}}))
        archive = Session(
            campaign_id=ids['campaign_id'],
            name='Old save',
            status='ended',
            state_snapshot=json.dumps({'imported': True}),
        )
        db.session.add_all([titled, chapter, archive])
        db.session.commit()
        titled_id, chapter_id, archive_id = titled.session_id, chapter.session_id, archive.session_id

    listed = {
        item['session_id']: item
        for item in client.get(f"/api/sessions/campaigns/{ids['campaign_id']}/sessions").get_json()['sessions']
    }

    assert listed[ids['session_id']]['display_name'] == 'Session 1'
    assert listed[titled_id]['display_name'] == 'Snapshot Title'
    assert listed[chapter_id]['display_name'] == 'Chapter 7'
    assert listed[archive_id]['display_name'] == 'Archive — Old save'
    assert listed[archive_id]['name'] == 'Old save'
    assert listed[archive_id]['imported'] is True


def test_hundred_session_workspace_stays_small_and_includes_the_current_chapter(client, app):
    seeded = _seed_chapters(app, count=100, current_index=0)
    current_chapter_id = seeded['session_ids'][0]

    response = client.get(f"/api/campaigns/{seeded['campaign_id']}/workspace")

    assert response.status_code == 200
    assert len(response.get_data()) < 60_000
    payload = response.get_json()
    listed_ids = [item['session_id'] for item in payload['sessions']]
    assert listed_ids[:30] == list(reversed(seeded['session_ids']))[:30]
    assert listed_ids[30:] == [current_chapter_id]
    assert payload['sessions'][-1]['is_current'] is True
    assert payload['summary']['session_count'] == 100
    assert payload['summary']['latest_session_id'] == seeded['session_ids'][-1]
    assert payload['has_more']['sessions'] is True
    assert isinstance(payload['next_cursor']['sessions'], str)
    assert payload['limits']['sessions'] == 30


def test_session_cursor_paging_visits_every_session_once_while_sessions_change(client, app):
    seeded = _seed_chapters(app, count=100)
    campaign_id = seeded['campaign_id']
    current_chapter_id = seeded['session_ids'][-1]
    visited: list[int] = []
    cursor = None
    pages = 0

    while True:
        query = f'?limit=30&cursor={cursor}' if cursor else '?limit=30'
        page = client.get(f'/api/sessions/campaigns/{campaign_id}/sessions{query}').get_json()
        pages += 1
        visited.extend(item['session_id'] for item in page['sessions'])
        assert page['has_more'] is (page['next_cursor'] is not None)
        if page['next_cursor'] is None:
            break
        cursor = page['next_cursor']
        # Between pages the table keeps playing the current chapter, renames a
        # chapter it has already seen, and a save is imported at the top of the list.
        with app.app_context():
            db.session.add(SessionLogEntry(session_id=current_chapter_id, message='A new turn.', entry_type='dm'))
            renamed = db.session.get(Session, visited[pages])
            renamed.name = f'Renamed {pages}'
            renamed.updated_at = utc_now()
            db.session.add(
                Session(
                    campaign_id=campaign_id,
                    name=f'Imported {pages}',
                    status='ended',
                    state_snapshot=json.dumps({'imported': True}),
                )
            )
            db.session.commit()

    assert pages == 4
    assert len(visited) == len(set(visited)) == 100
    assert set(visited) == set(seeded['session_ids'])


def test_an_unseen_chapter_updated_between_pages_is_not_skipped(client, app):
    seeded = _seed_chapters(app, count=40)
    campaign_id = seeded['campaign_id']
    first = client.get(f'/api/sessions/campaigns/{campaign_id}/sessions?limit=10').get_json()
    visited = [item['session_id'] for item in first['sessions']]
    unseen_id = seeded['session_ids'][0]
    assert unseen_id not in visited
    with app.app_context():
        chapter = db.session.get(Session, unseen_id)
        chapter.name = 'Updated before it was viewed'
        chapter.updated_at = utc_now()
        db.session.commit()
    cursor = first['next_cursor']
    while cursor:
        page = client.get(f'/api/sessions/campaigns/{campaign_id}/sessions?limit=10&cursor={cursor}').get_json()
        visited.extend(item['session_id'] for item in page['sessions'])
        cursor = page['next_cursor']
    assert len(visited) == len(set(visited)) == 40
    assert set(visited) == set(seeded['session_ids'])


def test_workspace_accepts_a_sessions_cursor_and_rejects_a_forged_one(client, app):
    seeded = _seed_chapters(app, count=40)
    first = client.get(f"/api/campaigns/{seeded['campaign_id']}/workspace?sessions_limit=25").get_json()
    second = client.get(
        f"/api/campaigns/{seeded['campaign_id']}/workspace?sessions_limit=25"
        f"&sessions_cursor={first['next_cursor']['sessions']}"
    ).get_json()
    forged = client.get(f"/api/campaigns/{seeded['campaign_id']}/workspace?sessions_cursor=not-a-cursor")

    newest_first = list(reversed(seeded['session_ids']))
    assert first['limits']['sessions'] == 25
    assert [item['session_id'] for item in first['sessions']] == newest_first[:25]
    # The current chapter rides along with every workspace page.
    assert [item['session_id'] for item in second['sessions']] == [newest_first[0], *newest_first[25:]]
    assert second['has_more']['sessions'] is False
    assert forged.status_code == 400
    assert forged.get_json()['error_code'] == 'validation_error'


def test_session_list_limit_is_bounded(client, app):
    seeded = _seed_chapters(app, count=3)

    small = client.get(f"/api/sessions/campaigns/{seeded['campaign_id']}/sessions?limit=0").get_json()
    large = client.get(f"/api/sessions/campaigns/{seeded['campaign_id']}/sessions?limit=5000").get_json()

    assert small['limit'] == 1
    assert len(small['sessions']) == 1
    assert large['limit'] == 100


def _seed_r2_campaign_order(app) -> list[int]:
    """The R2 reproduction: three campaigns, the oldest touched last, so the order is [1, 3, 2]."""

    with app.app_context():
        world = World(name='Order World')
        db.session.add(world)
        db.session.flush()
        base = utc_now()
        campaign_ids = []
        for index in range(3):
            campaign = Campaign(
                title=f'C{index + 1}',
                world_id=world.world_id,
                created_at=base + timedelta(seconds=index),
                updated_at=base + timedelta(seconds=index),
            )
            db.session.add(campaign)
            db.session.flush()
            campaign_ids.append(campaign.campaign_id)
        db.session.commit()
        oldest = db.session.get(Campaign, campaign_ids[0])
        oldest.updated_at = base + timedelta(seconds=60)
        db.session.commit()
    return campaign_ids


def test_campaign_list_pages_without_gaps(client, app):
    first, second, third = _seed_r2_campaign_order(app)
    full_order = [item['campaign_id'] for item in client.get('/api/campaigns').get_json()]
    visited: list[int] = []
    has_more_headers: list[str] = []
    cursor = None

    while True:
        response = client.get(f'/api/campaigns?limit=1&cursor={cursor}' if cursor else '/api/campaigns?limit=1')
        assert response.status_code == 200
        visited.extend(item['campaign_id'] for item in response.get_json())
        has_more_headers.append(response.headers['X-AIDM-Has-More'])
        cursor = response.headers.get('X-AIDM-Next-Cursor')
        if not cursor:
            break

    assert full_order == [first, third, second]
    assert visited == full_order
    assert has_more_headers == ['true', 'true', 'false']


def test_campaign_list_translates_deprecated_before_id_to_a_cursor(client, app):
    first, second, third = _seed_r2_campaign_order(app)

    page_one = client.get('/api/campaigns?limit=1')
    page_two = client.get(f'/api/campaigns?limit=1&before_id={first}')
    page_three = client.get(f"/api/campaigns?limit=1&cursor={page_two.headers['X-AIDM-Next-Cursor']}")
    unknown = client.get('/api/campaigns?limit=1&before_id=99999')
    forged = client.get('/api/campaigns?cursor=%%%')
    cursor_wins = client.get(
        f"/api/campaigns?limit=1&before_id={third}&cursor={page_one.headers['X-AIDM-Next-Cursor']}"
    )

    assert [item['campaign_id'] for item in page_one.get_json()] == [first]
    assert [item['campaign_id'] for item in page_two.get_json()] == [third]
    assert [item['campaign_id'] for item in page_three.get_json()] == [second]
    assert [item['campaign_id'] for item in cursor_wins.get_json()] == [third]
    assert unknown.status_code == 400
    assert unknown.get_json()['error_code'] == 'validation_error'
    assert forged.status_code == 400
