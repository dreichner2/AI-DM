"""Shared API response DTO builders.

These helpers keep backend JSON shapes in one place so blueprints do not drift
from each other or from the React client types.
"""

from __future__ import annotations

from dataclasses import dataclass
import re

from sqlalchemy import func, or_, select

from aidm_server.armor_class import armor_class_details
from aidm_server.canon_inventory import inventory_payload
from aidm_server.character_backgrounds import background_from_character_sheet
from aidm_server.character_state import character_state_for_player
from aidm_server.combat.legal_actions import with_combat_legal_actions
from aidm_server.database import db
from aidm_server.auth import account_display_name
from aidm_server.models import (
    Campaign,
    CampaignSegment,
    DmTurn,
    Map,
    Player,
    Session,
    SessionLogEntry,
    SessionState,
    TurnEvent,
    World,
    safe_json_loads,
)
from aidm_server.profile_icons import profile_icon_src_for_character
from aidm_server.race_system import profile_race_from_selection, race_selection_from_json
from aidm_server.roll_visibility import (
    public_segment_triggered_payload,
    public_turn_event_payload as player_visible_turn_event_body,
    public_turn_metadata_payload,
)
from aidm_server.services.campaign_pack_visibility import (
    filter_session_snapshot_for_player,
    hide_enemy_hit_points,
    with_scene_offers,
)
from aidm_server.turn_events import DM_RESPONSE_EVENT
from aidm_server.weapon_proficiency import normalize_weapon_proficiencies

ACTIVE_STATUS = 'active'
ARCHIVED_STATUS = 'archived'
ENDED_STATUS = 'ended'
DELETED_STATUS = 'deleted'

ARCHIVE_NAME_PREFIX = 'Archive — '
SESSION_SCENE_TITLE_MAX_LENGTH = 120
SESSION_SUMMARY_EXCERPT_MAX_LENGTH = 280
CONTINUE_LINE_MAX_LENGTH = 160
# How many of a session's latest DM responses are read to find its latest narration.
CONTINUE_LINE_RESPONSE_WINDOW = 10
FIRST_SENTENCE_RE = re.compile(r'.+?[.!?…]+["”’)\]]*(?=\s|$)', re.S)
PLAYER_VIEW = 'player'

PARTY_PLAYER_PUBLIC_KEYS = (
    'player_id',
    'campaign_id',
    'name',
    'character_name',
    'race',
    'background',
    'sex',
    'profile_image',
    'class_',
    'char_class',
    'level',
)


def isoformat(value):
    return value.isoformat() if value else None


def latest_isoformat(*values):
    iso_values = []
    for value in values:
        if not value:
            continue
        if isinstance(value, str):
            iso_values.append(value)
        else:
            iso_values.append(value.isoformat())
    return max(iso_values) if iso_values else None


def active_sessions_query(campaign_id: int):
    return Session.query.filter(
        Session.campaign_id == campaign_id,
        or_(Session.status.is_(None), Session.status != ARCHIVED_STATUS),
    )


def _active_sessions_filter():
    return or_(Session.status.is_(None), Session.status != ARCHIVED_STATUS)


def open_sessions_filter():
    """Sessions that are still playable chapters: not ended, archived or deleted."""

    return or_(Session.status.is_(None), Session.status == ACTIVE_STATUS)


def session_is_open(status) -> bool:
    return str(status or ACTIVE_STATUS).strip().lower() == ACTIVE_STATUS


def session_status_label(status) -> str:
    normalized = str(status or ACTIVE_STATUS).strip().lower()
    if normalized == ENDED_STATUS:
        return ENDED_STATUS
    if normalized in (ARCHIVED_STATUS, DELETED_STATUS):
        return ARCHIVED_STATUS
    return ACTIVE_STATUS


def current_chapter_session_id(campaign_id: int) -> int | None:
    """Return the campaign's current chapter: its newest open session."""

    return (
        db.session.query(func.max(Session.session_id))
        .filter(Session.campaign_id == campaign_id, open_sessions_filter())
        .scalar()
    )


def world_payload(world: World) -> dict:
    return {
        'world_id': world.world_id,
        'name': world.name,
        'description': world.description,
        'created_at': isoformat(world.created_at),
    }


def campaign_is_archived(campaign: Campaign) -> bool:
    return campaign.status == ARCHIVED_STATUS


def campaign_session_summaries(campaign_ids: list[int]) -> dict[int, dict]:
    ids = list(dict.fromkeys(int(campaign_id) for campaign_id in campaign_ids if campaign_id is not None))
    summaries = {
        campaign_id: {
            'session_count': 0,
            'latest_session_id': None,
            'latest_session_created_at': None,
            'latest_session_updated_at': None,
            'latest_log_at': None,
            'latest_state_at': None,
            'latest_turn_created_at': None,
            'latest_turn_completed_at': None,
        }
        for campaign_id in ids
    }
    if not ids:
        return summaries

    session_rows = (
        db.session.query(
            Session.campaign_id,
            func.count(Session.session_id),
            func.max(Session.session_id),
            func.max(Session.created_at),
            func.max(Session.updated_at),
        )
        .filter(Session.campaign_id.in_(ids), _active_sessions_filter())
        .group_by(Session.campaign_id)
        .all()
    )
    for campaign_id, count, latest_session_id, latest_created_at, latest_updated_at in session_rows:
        summary = summaries[int(campaign_id)]
        summary['session_count'] = int(count or 0)
        summary['latest_session_id'] = latest_session_id
        summary['latest_session_created_at'] = latest_created_at
        summary['latest_session_updated_at'] = latest_updated_at

    log_rows = (
        db.session.query(Session.campaign_id, func.max(SessionLogEntry.timestamp))
        .join(Session, Session.session_id == SessionLogEntry.session_id)
        .filter(Session.campaign_id.in_(ids), _active_sessions_filter())
        .group_by(Session.campaign_id)
        .all()
    )
    for campaign_id, latest_log_at in log_rows:
        summaries[int(campaign_id)]['latest_log_at'] = latest_log_at

    state_rows = (
        db.session.query(Session.campaign_id, func.max(SessionState.updated_at))
        .join(Session, Session.session_id == SessionState.session_id)
        .filter(Session.campaign_id.in_(ids), _active_sessions_filter())
        .group_by(Session.campaign_id)
        .all()
    )
    for campaign_id, latest_state_at in state_rows:
        summaries[int(campaign_id)]['latest_state_at'] = latest_state_at

    turn_rows = (
        db.session.query(
            DmTurn.campaign_id,
            func.max(DmTurn.created_at),
            func.max(DmTurn.completed_at),
        )
        .join(Session, Session.session_id == DmTurn.session_id)
        .filter(DmTurn.campaign_id.in_(ids), _active_sessions_filter())
        .group_by(DmTurn.campaign_id)
        .all()
    )
    for campaign_id, latest_created_at, latest_completed_at in turn_rows:
        summary = summaries[int(campaign_id)]
        summary['latest_turn_created_at'] = latest_created_at
        summary['latest_turn_completed_at'] = latest_completed_at

    return summaries


def campaign_session_summary(campaign: Campaign, summary: dict | None = None) -> dict:
    summary = summary or campaign_session_summaries([campaign.campaign_id]).get(campaign.campaign_id, {})

    return {
        'session_count': int(summary.get('session_count') or 0),
        'latest_session_id': summary.get('latest_session_id'),
        'latest_activity_at': latest_isoformat(
            campaign.created_at,
            campaign.updated_at,
            summary.get('latest_session_created_at'),
            summary.get('latest_session_updated_at'),
            summary.get('latest_log_at'),
            summary.get('latest_state_at'),
            summary.get('latest_turn_created_at'),
            summary.get('latest_turn_completed_at'),
        ),
    }


def campaign_payload(campaign: Campaign, session_summary: dict | None = None) -> dict:
    return {
        'campaign_id': campaign.campaign_id,
        'title': campaign.title,
        'description': campaign.description,
        'world_id': campaign.world_id,
        'world_name': campaign.world.name if campaign.world else None,
        'created_at': isoformat(campaign.created_at),
        'updated_at': isoformat(campaign.updated_at),
        'status': campaign.status or ACTIVE_STATUS,
        'is_archived': campaign_is_archived(campaign),
        'current_quest': campaign.current_quest,
        'location': campaign.location,
        **campaign_session_summary(campaign, session_summary),
    }


def campaign_payloads(campaigns: list[Campaign]) -> list[dict]:
    summaries = campaign_session_summaries([campaign.campaign_id for campaign in campaigns])
    return [campaign_payload(campaign, summaries.get(campaign.campaign_id)) for campaign in campaigns]


def session_snapshot(session_obj: Session) -> dict:
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    return snapshot if isinstance(snapshot, dict) else {}


def session_campaign_ordinal(session_obj: Session) -> int:
    if not session_obj.campaign_id or not session_obj.session_id:
        return int(session_obj.session_id or 1)
    count = (
        db.session.query(func.count(Session.session_id))
        .filter(
            Session.campaign_id == session_obj.campaign_id,
            Session.session_id <= session_obj.session_id,
        )
        .scalar()
    )
    return int(count or 1)


def session_is_imported(snapshot: dict) -> bool:
    return snapshot.get('imported') is True


def _snapshot_chapter(snapshot: dict) -> dict:
    chapter = snapshot.get('chapter')
    return chapter if isinstance(chapter, dict) else {}


def _snapshot_chapter_number(snapshot: dict) -> int | None:
    number = _snapshot_chapter(snapshot).get('number')
    if isinstance(number, bool) or not isinstance(number, int) or number < 1:
        return None
    return number


def session_display_name(
    session_obj: Session,
    snapshot: dict | None = None,
    campaign_ordinal: int | None = None,
) -> str:
    source_snapshot = snapshot if snapshot is not None else session_snapshot(session_obj)
    raw_name = session_obj.name or source_snapshot.get('name') or source_snapshot.get('title')
    name = str(raw_name or '').strip()
    if not name:
        chapter_number = _snapshot_chapter_number(source_snapshot)
        name = (
            f'Chapter {chapter_number}'
            if chapter_number
            else f'Session {campaign_ordinal or session_campaign_ordinal(session_obj)}'
        )
    if session_is_imported(source_snapshot) and not name.startswith(ARCHIVE_NAME_PREFIX):
        return f'{ARCHIVE_NAME_PREFIX}{name}'
    return name


def text_excerpt(value, limit: int) -> str:
    """Collapse whitespace and cut at a word boundary so the result fits in ``limit``."""

    text = ' '.join(str(value or '').split())
    if len(text) <= limit:
        return text
    window = text[: limit - 1]
    cut = window.rsplit(' ', 1)[0] if ' ' in window else window
    return f'{cut.rstrip()}…'


def session_scene_title(snapshot: dict) -> str | None:
    scene = snapshot.get('currentScene')
    name = scene.get('name') if isinstance(scene, dict) else None
    title = text_excerpt(name, SESSION_SCENE_TITLE_MAX_LENGTH) if isinstance(name, str) else ''
    return title or None


def session_chapter_positions(session_ids: list[int]) -> dict[int, dict]:
    """Return each session's 1-based creation order and its campaign's current chapter."""

    ids = {int(session_id) for session_id in session_ids if session_id is not None}
    if not ids:
        return {}
    campaign_ids = select(Session.campaign_id).where(Session.session_id.in_(ids))
    rows = (
        db.session.query(Session.session_id, Session.campaign_id, Session.status)
        .filter(Session.campaign_id.in_(campaign_ids))
        .order_by(Session.campaign_id.asc(), Session.session_id.asc())
        .all()
    )
    ordinals: dict[int, int] = {}
    campaign_by_session: dict[int, int] = {}
    session_counts: dict[int, int] = {}
    current_by_campaign: dict[int, int] = {}
    for session_id, campaign_id, status in rows:
        session_counts[campaign_id] = session_counts.get(campaign_id, 0) + 1
        ordinals[session_id] = session_counts[campaign_id]
        campaign_by_session[session_id] = campaign_id
        if session_is_open(status):
            current_by_campaign[campaign_id] = session_id
    return {
        session_id: {
            'campaign_ordinal': ordinals[session_id],
            'current_session_id': current_by_campaign.get(campaign_by_session[session_id]),
        }
        for session_id in ids
        if session_id in ordinals
    }


def session_summaries(session_ids: list[int]) -> dict[int, dict]:
    ids = list(dict.fromkeys(int(session_id) for session_id in session_ids if session_id is not None))
    summaries = {
        session_id: {
            'state_updated_at': None,
            'latest_log_at': None,
            'latest_turn_created_at': None,
            'latest_turn_completed_at': None,
            'turn_count': 0,
            'campaign_ordinal': None,
            'current_session_id': None,
            'latest_narration': '',
        }
        for session_id in ids
    }
    if not ids:
        return summaries

    for session_id, positions in session_chapter_positions(ids).items():
        summaries[session_id].update(positions)

    states = SessionState.query.filter(SessionState.session_id.in_(ids)).all()
    for session_state in states:
        summaries[int(session_state.session_id)]['state_updated_at'] = session_state.updated_at

    log_rows = (
        db.session.query(SessionLogEntry.session_id, func.max(SessionLogEntry.timestamp))
        .filter(SessionLogEntry.session_id.in_(ids))
        .group_by(SessionLogEntry.session_id)
        .all()
    )
    for session_id, latest_log_at in log_rows:
        summaries[int(session_id)]['latest_log_at'] = latest_log_at

    turn_rows = (
        db.session.query(
            DmTurn.session_id,
            func.max(DmTurn.created_at),
            func.max(DmTurn.completed_at),
            func.count(DmTurn.turn_id),
        )
        .filter(DmTurn.session_id.in_(ids))
        .group_by(DmTurn.session_id)
        .all()
    )
    for session_id, latest_created_at, latest_completed_at, turn_count in turn_rows:
        summary = summaries[int(session_id)]
        summary['latest_turn_created_at'] = latest_created_at
        summary['latest_turn_completed_at'] = latest_completed_at
        summary['turn_count'] = int(turn_count or 0)

    recent_responses = (
        db.session.query(
            TurnEvent.session_id.label('session_id'),
            TurnEvent.payload_json.label('payload_json'),
            func.row_number()
            .over(partition_by=TurnEvent.session_id, order_by=TurnEvent.event_id.desc())
            .label('recency'),
        )
        .filter(TurnEvent.session_id.in_(ids), TurnEvent.event_type == DM_RESPONSE_EVENT)
        .subquery()
    )
    for session_id, payload_json in (
        db.session.query(recent_responses.c.session_id, recent_responses.c.payload_json)
        .filter(recent_responses.c.recency <= CONTINUE_LINE_RESPONSE_WINDOW)
        .order_by(recent_responses.c.session_id.asc(), recent_responses.c.recency.asc())
        .all()
    ):
        summary = summaries[int(session_id)]
        if not summary['latest_narration']:
            summary['latest_narration'] = _narration_text(payload_json)

    return summaries


def _narration_text(payload_json) -> str:
    """A DM response's story text; refusals and roll-gate notices are not narration."""

    payload = safe_json_loads(payload_json, {})
    payload = payload if isinstance(payload, dict) else {}
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    if metadata.get('rules_rejection') or metadata.get('roll_gate_waiting'):
        return ''
    message = payload.get('message')
    return message.strip() if isinstance(message, str) else ''


def first_sentence(text) -> str:
    plain = ' '.join(str(text or '').replace('*', '').split())
    match = FIRST_SENTENCE_RE.match(plain)
    return match.group(0) if match else plain


def session_continue_line(snapshot: dict, latest_narration: str) -> str | None:
    """``{current location} — {first sentence of the latest narration}`` for the title's Continue card.

    ``None`` before anything has been narrated; the rolling summary is never used.
    """

    sentence = first_sentence(latest_narration)
    if not sentence:
        return None
    scene = snapshot.get('currentScene') if isinstance(snapshot.get('currentScene'), dict) else {}
    location_id = str(scene.get('locationId') or '').strip()
    location = next(
        (
            record
            for record in (snapshot.get('locations') if isinstance(snapshot.get('locations'), list) else [])
            if isinstance(record, dict) and location_id and str(record.get('id') or '').strip() == location_id
        ),
        {},
    )
    place = str(location.get('name') or scene.get('name') or '').strip()
    return text_excerpt(f'{place} — {sentence}' if place else sentence, CONTINUE_LINE_MAX_LENGTH)


def session_recap_line(snapshot: dict, continue_line: str | None) -> str:
    """What a player reads as a session's story so far: its saved recap, else its continue line; '' when neither exists.

    The rolling summary is the narrator's memory and is never shown to a player.
    """

    recap = snapshot.get('recap')
    if isinstance(recap, str) and recap.strip():
        return recap.strip()
    return continue_line or ''


def account_player_ids_by_campaign(
    campaign_ids: list[int] | set[int],
    account_id: int | None,
) -> dict[int, frozenset[int]]:
    ids = {int(campaign_id) for campaign_id in campaign_ids if campaign_id is not None}
    result = {campaign_id: set() for campaign_id in ids}
    if account_id is None or not ids:
        return {campaign_id: frozenset() for campaign_id in ids}

    rows = (
        db.session.query(Player.campaign_id, Player.player_id)
        .filter(
            Player.campaign_id.in_(ids),
            Player.account_id == account_id,
        )
        .all()
    )
    for campaign_id, player_id in rows:
        result[int(campaign_id)].add(int(player_id))
    return {campaign_id: frozenset(player_ids) for campaign_id, player_ids in result.items()}


class ViewerProjectionError(ValueError):
    def __init__(self, error_code: str, message: str, status_code: int):
        super().__init__(message)
        self.error_code = error_code
        self.public_message = message
        self.status_code = status_code


@dataclass(frozen=True)
class ViewerProjection:
    """Which projection a read returns, and the hero it is told from.

    ``player_id`` names the hero whose private data is included and for whom
    legality is computed; without it a read keeps today's choice of heroes.
    """

    include_hidden_state: bool
    player_id: int | None = None

    @property
    def private_player_ids(self) -> frozenset[int] | None:
        return frozenset({self.player_id}) if self.player_id is not None else None


def viewer_projection(
    *,
    campaign_id: int,
    operator: bool,
    view: str | None,
    player_id: str | None,
    account_id: int | None,
) -> ViewerProjection:
    """Decide a read's projection: the full one only for an operator who did not ask for ``view=player``.

    With ``view=player``, ``player_id`` may name any hero of the campaign for an
    operator and only one of the caller's own heroes for anyone else.
    """

    requested_view = str(view or '').strip().lower()
    if requested_view and requested_view != PLAYER_VIEW:
        raise ViewerProjectionError('validation_error', 'view must be player.', 400)
    if requested_view != PLAYER_VIEW:
        return ViewerProjection(include_hidden_state=operator)
    if player_id in (None, ''):
        return ViewerProjection(include_hidden_state=False)
    try:
        hero_id = int(str(player_id).strip())
    except ValueError:
        hero_id = 0
    if hero_id <= 0:
        raise ViewerProjectionError('validation_error', 'player_id must be a positive integer.', 400)
    heroes = Player.query.filter_by(player_id=hero_id, campaign_id=campaign_id)
    if not operator:
        heroes = heroes.filter(Player.account_id.is_not(None), Player.account_id == account_id)
    if heroes.first() is None:
        raise ViewerProjectionError('forbidden', 'You can only view the story as one of your own heroes.', 403)
    return ViewerProjection(include_hidden_state=False, player_id=hero_id)


def session_payload(
    session_obj: Session,
    summary: dict | None = None,
    *,
    include_hidden_state: bool = True,
    viewer_account_id: int | None = None,
    private_player_ids: set[int] | frozenset[int] | None = None,
) -> dict:
    """A session list item (``SessionSummary``): a summary, never the snapshot.

    Nothing in a summary depends on the viewer. The viewer keywords are still
    accepted because lifecycle, Play Now and pack-import callers pass them.
    """

    snapshot = session_snapshot(session_obj)
    summary = summary or session_summaries([session_obj.session_id]).get(session_obj.session_id, {})
    latest_activity = session_activity_at(session_obj.created_at, session_obj.updated_at, summary)
    continue_line = session_continue_line(snapshot, summary.get('latest_narration') or '')
    play_now = snapshot.get('playNow')

    return {
        'session_id': session_obj.session_id,
        'campaign_id': session_obj.campaign_id,
        'name': session_obj.name,
        'display_name': session_display_name(session_obj, snapshot, summary.get('campaign_ordinal')),
        **session_chapter_fields(session_obj, snapshot, summary),
        'is_archived': session_obj.status == ARCHIVED_STATUS or bool(snapshot.get('is_archived') or snapshot.get('archived')),
        'created_at': isoformat(session_obj.created_at),
        'updated_at': latest_activity,
        'latest_activity_at': latest_activity,
        'turn_count': int(summary.get('turn_count') or 0),
        'scene_title': session_scene_title(snapshot),
        'summary_excerpt': text_excerpt(session_recap_line(snapshot, continue_line), SESSION_SUMMARY_EXCERPT_MAX_LENGTH),
        'continue_line': continue_line,
        'imported': session_is_imported(snapshot),
        'play_now': isinstance(play_now, dict) and bool(play_now),
    }


def session_activity_at(created_at, updated_at, summary: dict) -> str | None:
    """A session's latest activity from its columns and ``session_summaries`` aggregates.

    Session lists sort and page by this value.
    """

    return latest_isoformat(
        created_at,
        updated_at,
        summary.get('state_updated_at'),
        summary.get('latest_log_at'),
        summary.get('latest_turn_created_at'),
        summary.get('latest_turn_completed_at'),
    )


def session_latest_activity_at(session_obj: Session, snapshot: dict, summary: dict) -> str | None:
    """``session_activity_at`` plus the legacy snapshot ``updated_at`` key, for chapter ranking."""

    snapshot_updated_at = snapshot.get('updated_at')
    return latest_isoformat(
        session_activity_at(session_obj.created_at, session_obj.updated_at, summary),
        snapshot_updated_at if isinstance(snapshot_updated_at, str) else None,
    )


def session_chapter_fields(session_obj: Session, snapshot: dict, positions: dict) -> dict:
    """Chapter view of a session; ``positions`` comes from ``session_chapter_positions``."""

    chapter = _snapshot_chapter(snapshot)
    chapter_number = (
        _snapshot_chapter_number(snapshot)
        or positions.get('campaign_ordinal')
        or session_campaign_ordinal(session_obj)
    )
    ended_at = snapshot.get('ended_at')
    chapter_recap = chapter.get('recap')
    return {
        'status': session_status_label(session_obj.status),
        'is_current': (
            session_obj.session_id is not None
            and session_obj.session_id == positions.get('current_session_id')
        ),
        'chapter_number': chapter_number,
        'ended_at': ended_at if isinstance(ended_at, str) and ended_at else None,
        'chapter_recap': chapter_recap if isinstance(chapter_recap, str) else '',
    }


def session_start_payload(session_obj: Session, *, idempotent_replay: bool) -> dict:
    snapshot = session_snapshot(session_obj)
    chapter = snapshot.get('chapter') if isinstance(snapshot.get('chapter'), dict) else {}
    previous_session_id = chapter.get('previousSessionId')
    continued = isinstance(previous_session_id, int) and not chapter.get('fresh')
    positions = session_chapter_positions([session_obj.session_id]).get(session_obj.session_id, {})
    return {
        'session_id': session_obj.session_id,
        'idempotent_replay': idempotent_replay,
        **session_chapter_fields(session_obj, snapshot, positions),
        'continued_from_session_id': previous_session_id if continued else None,
    }


def session_payloads(session_objs: list[Session]) -> list[dict]:
    summaries = session_summaries([session_obj.session_id for session_obj in session_objs])
    return [session_payload(session_obj, summaries.get(session_obj.session_id)) for session_obj in session_objs]


def session_state_payload(
    session_obj: Session,
    session_state: SessionState | None,
    *,
    include_hidden_state: bool = True,
    viewer_account_id: int | None = None,
    private_player_ids: set[int] | frozenset[int] | None = None,
    acting_player_id: int | None = None,
) -> dict:
    """A session's state; ``acting_player_id`` is the hero scene-object legality is computed for."""

    raw_snapshot = safe_json_loads(session_obj.state_snapshot, None)
    player_ids = private_player_ids
    if include_hidden_state:
        # Operators receive the full snapshot, but the Story map must still use
        # the same player-safe projection as every other viewer. It is derived
        # on read and must not be written back into the persisted snapshot.
        payload_snapshot = with_scene_offers(raw_snapshot, acting_player_id=acting_player_id)
        if isinstance(payload_snapshot, dict):
            payload_snapshot = dict(payload_snapshot)
            payload_snapshot.pop('worldMap', None)
            player_view = filter_session_snapshot_for_player(raw_snapshot)
            if isinstance(player_view, dict) and isinstance(player_view.get('worldMap'), dict):
                payload_snapshot['worldMap'] = player_view['worldMap']
    else:
        if player_ids is None:
            player_ids = account_player_ids_by_campaign(
                [session_obj.campaign_id],
                viewer_account_id,
            ).get(session_obj.campaign_id, frozenset())
        payload_snapshot = filter_session_snapshot_for_player(
            raw_snapshot,
            private_player_ids=frozenset(player_ids),
            acting_player_id=acting_player_id,
            enemy_hit_points=True,
        )
    combat = payload_snapshot.get('combat') if isinstance(payload_snapshot, dict) else None
    if isinstance(combat, dict) and str(combat.get('status') or '').strip().lower() in {'starting', 'active'}:
        player_query = Player.query.filter_by(campaign_id=session_obj.campaign_id)
        if not include_hidden_state:
            player_query = player_query.filter(Player.player_id.in_(frozenset(player_ids or ())))
        payload_snapshot = with_combat_legal_actions(
            payload_snapshot,
            player_query.order_by(Player.player_id.asc()).all(),
        )
    if not include_hidden_state:
        payload_snapshot = hide_enemy_hit_points(payload_snapshot)
    chapter_fields = session_chapter_fields(
        session_obj,
        raw_snapshot if isinstance(raw_snapshot, dict) else {},
        session_chapter_positions([session_obj.session_id]).get(session_obj.session_id, {}),
    )
    # The rolling summary is the narrator's memory and is never shown at the
    # table, so a state read always sends ``rolling_summary`` empty.
    if session_state is None:
        return {
            'session_id': session_obj.session_id,
            'campaign_id': session_obj.campaign_id,
            **chapter_fields,
            'current_location': session_obj.campaign.location,
            'current_quest': session_obj.campaign.current_quest,
            'rolling_summary': '',
            'active_segments': [],
            'memory_snippets': [],
            'state_snapshot': payload_snapshot,
            'updated_at': None,
        }

    active_segments = safe_json_loads(session_state.active_segments, [])
    active_segments = active_segments if isinstance(active_segments, list) else []
    if not include_hidden_state:
        active_segments = [
            public_segment_triggered_payload(segment)
            for segment in active_segments
            if isinstance(segment, dict)
        ]

    return {
        'session_id': session_obj.session_id,
        'campaign_id': session_obj.campaign_id,
        **chapter_fields,
        'current_location': session_state.current_location,
        'current_quest': session_state.current_quest,
        'rolling_summary': '',
        'active_segments': active_segments,
        'memory_snippets': safe_json_loads(session_state.memory_snippets, []),
        'state_snapshot': payload_snapshot,
        'updated_at': isoformat(session_state.updated_at),
    }


def turn_event_payload(
    event: TurnEvent,
    *,
    include_private: bool = True,
    private_player_ids: set[int] | frozenset[int] | None = None,
) -> dict:
    event_body = safe_json_loads(event.payload_json, {})
    if not include_private and event.player_id not in frozenset(private_player_ids or ()):
        event_body = player_visible_turn_event_body(event_body)
    return {
        'event_id': event.event_id,
        'session_id': event.session_id,
        'campaign_id': event.campaign_id,
        'turn_id': event.turn_id,
        'player_id': event.player_id,
        'event_type': event.event_type,
        'payload': event_body,
        'created_at': isoformat(event.created_at),
    }


def session_log_entry_payload(
    entry: SessionLogEntry,
    *,
    include_private: bool = True,
    private_player_ids: set[int] | frozenset[int] | None = None,
    turn_player_ids: dict[int, int | None] | None = None,
) -> dict:
    metadata = safe_json_loads(entry.metadata_json, {})
    metadata = metadata if isinstance(metadata, dict) else {}
    try:
        turn_id = int(metadata.get('turn_id')) if metadata.get('turn_id') is not None else None
    except (TypeError, ValueError):
        turn_id = None
    actor_player_id = (turn_player_ids or {}).get(turn_id) if turn_id is not None else None
    if not include_private and actor_player_id not in frozenset(private_player_ids or ()):
        metadata = public_turn_metadata_payload(metadata)
    return {
        'id': entry.id,
        'message': entry.message,
        'entry_type': entry.entry_type,
        'metadata': metadata,
        'timestamp': isoformat(entry.timestamp),
    }


def structured_payload(raw_value):
    return safe_json_loads(raw_value, raw_value)


def player_derived_payload(player: Player) -> dict:
    stats = structured_payload(player.stats)
    stats_record = stats if isinstance(stats, dict) else {}
    inventory = inventory_payload(player.inventory)
    armor_details = armor_class_details(stats_record, inventory, class_name=player.class_)
    character_state = character_state_for_player(player)
    return {
        'armorClass': armor_details['armorClass'],
        'armor_class': armor_details['armorClass'],
        'armorClassBreakdown': armor_details,
        'background': character_state.get('background'),
        'skillProficiencies': list(character_state.get('skill_proficiencies') or []),
        'skillExpertise': list(character_state.get('skill_expertise') or []),
        'toolProficiencies': list(character_state.get('tool_proficiencies') or []),
        'toolExpertise': list(character_state.get('tool_expertise') or []),
        'languages': list(character_state.get('languages') or []),
        'hitDie': character_state.get('hit_die'),
        'proficiencyBonus': character_state.get('proficiency_bonus'),
        'savingThrowProficiencies': list(character_state.get('saving_throw_proficiencies') or []),
    }


def player_summary_payload(player: Player) -> dict:
    race_selection = race_selection_from_json(player.race_selection, player.race)
    profile_race = profile_race_from_selection(race_selection, player.race)
    account = player.account
    player_name = account_display_name(account) if account else player.name
    return {
        'player_id': player.player_id,
        'workspace_id': player.workspace_id,
        'account_id': player.account_id,
        'username': account.username if account else None,
        'campaign_id': player.campaign_id,
        'name': player_name,
        'character_name': player.character_name,
        'race': player.race,
        'race_selection': race_selection,
        'background': background_from_character_sheet(player.character_sheet),
        'sex': player.sex,
        'profile_image': profile_icon_src_for_character(profile_race, player.sex),
        'class_': player.class_,
        'char_class': player.class_,
        'level': player.level,
        'created_at': isoformat(player.created_at),
        'updated_at': isoformat(player.updated_at),
    }


def player_detail_payload(player: Player) -> dict:
    derived = player_derived_payload(player)
    return {
        **player_summary_payload(player),
        'stats': structured_payload(player.stats),
        'inventory': inventory_payload(player.inventory),
        'weapon_proficiencies': normalize_weapon_proficiencies(player.weapon_proficiencies),
        'character_sheet': structured_payload(player.character_sheet),
        'skill_proficiencies': derived['skillProficiencies'],
        'skill_expertise': derived['skillExpertise'],
        'tool_proficiencies': derived['toolProficiencies'],
        'tool_expertise': derived['toolExpertise'],
        'languages': derived['languages'],
        'derived': derived,
    }


def party_player_payload(player: Player, *, include_private: bool = False) -> dict:
    if include_private:
        return player_detail_payload(player)
    summary = player_summary_payload(player)
    return {key: summary[key] for key in PARTY_PLAYER_PUBLIC_KEYS}


def map_payload(map_obj: Map) -> dict:
    return {
        'map_id': map_obj.map_id,
        'world_id': map_obj.world_id,
        'campaign_id': map_obj.campaign_id,
        'title': map_obj.title,
        'description': map_obj.description,
        'map_data': safe_json_loads(map_obj.map_data, {}),
        'visibility': map_obj.visibility,
        'created_at': isoformat(map_obj.created_at),
        'updated_at': isoformat(map_obj.updated_at),
    }


def segment_payload(segment: CampaignSegment) -> dict:
    return {
        'segment_id': segment.segment_id,
        'campaign_id': segment.campaign_id,
        'title': segment.title,
        'description': segment.description,
        'trigger_condition': segment.trigger_condition,
        'tags': segment.tags,
        'external_id': segment.external_id,
        'source': segment.source,
        'source_pack_id': segment.source_pack_id,
        'metadata': safe_json_loads(segment.metadata_json, {}),
        'is_triggered': segment.is_triggered,
        'created_at': isoformat(segment.created_at),
        'updated_at': isoformat(segment.updated_at),
    }


def public_segment_payload(segment: CampaignSegment) -> dict:
    """Triggered story facts safe to include in a player campaign workspace."""

    return {
        'segment_id': segment.segment_id,
        'campaign_id': segment.campaign_id,
        'title': segment.title,
        'description': segment.description,
        'is_triggered': bool(segment.is_triggered),
    }
