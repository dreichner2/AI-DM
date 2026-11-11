"""Sessions as chapters of one continuing campaign adventure.

A campaign is one adventure for one party and each session is a chapter of it.
Exactly one chapter is current. Starting a chapter carries the world forward
from the previous one (or, when asked, starts the world fresh) and closes the
chapters before it: they stay readable but no longer accept live play.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
from typing import Any

from sqlalchemy import func, or_

from aidm_server.auth import DEFAULT_WORKSPACE_ID
from aidm_server.combat.state import ensure_combat_state
from aidm_server.database import db
from aidm_server.models import (
    Campaign,
    CampaignPack,
    CampaignPackSession,
    DmTurn,
    InstalledCampaignPack,
    Session,
    SessionState,
    get_or_create_session_state,
    safe_json_dumps,
    safe_json_loads,
)
from aidm_server.response_dtos import (
    ACTIVE_STATUS,
    ARCHIVED_STATUS,
    DELETED_STATUS,
    ENDED_STATUS,
    current_chapter_session_id,
    open_sessions_filter,
    session_continue_line,
    session_is_open,
    session_latest_activity_at,
    session_recap_line,
    session_snapshot,
    session_summaries,
)
from aidm_server.services import campaign_pack as pack_importer
from aidm_server.services.campaign_pack_progress import CampaignPackProgressError, campaign_pack_progress_payload
from aidm_server.services.campaign_pack_storage import sync_campaign_pack_progress
from aidm_server.services.tie_lines import TIE_LINES_KEY, normalize_tie_lines
from aidm_server.time_utils import utc_now
from aidm_server.turn_coordinator import SessionTurnTargetMissingError, TurnLeaseLostError, session_turn_coordinator
from aidm_server.turn_events import SESSION_ENDED_EVENT, SESSION_STARTED_EVENT, record_turn_event


CHAPTER_CONTINUE = 'continue'
CHAPTER_FRESH = 'fresh'
CHAPTER_CONTINUITY_MODES = (CHAPTER_CONTINUE, CHAPTER_FRESH)
CHAPTER_RECAP_MAX_LENGTH = 1_200
FIRST_CHAPTER_WELCOME = '**Welcome to the table. Choose your opening move when you are ready.**'
CHAPTER_CLOSED_MESSAGE = 'Chapter closed. The adventure continues in the next chapter.'
CHAPTER_UNSETTLED_MESSAGE = 'Finish the current fight or roll before starting a new chapter.'
SESSION_BUSY_MESSAGE = 'A turn is still running in this chapter. Try again when it finishes.'

# What a continued chapter inherits: everything that describes the world and
# the party. Everything else in the previous snapshot belongs to one sitting or
# to that chapter (active combat, turn control, recovery gates, presence, the
# party mirror rebuilt from the characters, revision and audit bookkeeping,
# recaps, import and display metadata).
CHAPTER_CARRIED_KEYS = (
    'schemaVersion',
    'campaignId',
    'currentScene',
    'locations',
    'locationSceneStates',
    'knownNpcs',
    'partyNpcs',
    'quests',
    'flags',
    'clues',
    'factions',
    'maps',
    'handouts',
    'lore',
    'campaignPack',
    'clock',
    'merchants',
    'contentSettings',
    'playNow',
    'stateChangeLedger',
    'gameplayEventLedger',
    'combatRewardLedger',
    'interactableActionLedger',
    # The heroes' word, the players' plans and the director's clocks and used nudges outlast a chapter.
    'promises',
    'plans',
    'clocks',
    'director',
    # What past fights left (stake states), the leads they gave and the clocks a plan started.
    'stakes',
    'leads',
    'clocksStarted',
)
SCENE_COMBAT_LAYOUT_KEYS = ('playerPositions', 'playerZones', 'characterPositions', 'characterZones')
UNSETTLED_TURN_STATUSES = ('pending', 'processing', 'awaiting_clarification')
ACTIVE_COMBAT_STATUSES = frozenset({'starting', 'active'})
_SENTENCE_END_RE = re.compile(r'[.!?…]+["\'”’)\]]*')


class ChapterStartError(RuntimeError):
    def __init__(self, error_code: str, message: str, status_code: int, details: dict | None = None):
        super().__init__(message)
        self.error_code = error_code
        self.public_message = message
        self.status_code = status_code
        self.details = details or {}


@dataclass(frozen=True)
class ChapterStartResult:
    session_id: int
    idempotent_replay: bool


@dataclass(frozen=True)
class CampaignPackSeed:
    snapshot: dict[str, Any]
    campaign_pack: CampaignPack | None
    installed_pack: InstalledCampaignPack | None


def chapter_snapshot_from(
    previous_snapshot: dict,
    *,
    new_client_session_id: str | None,
    chapter_number: int,
    previous_session_id: int | None,
    recap_text: str,
    now_iso: str,
) -> dict:
    """Build a continued chapter's starting snapshot from the previous chapter's."""

    snapshot = {
        key: deepcopy(previous_snapshot[key])
        for key in CHAPTER_CARRIED_KEYS
        if key in previous_snapshot
    }
    scene = snapshot.get('currentScene')
    if isinstance(scene, dict):
        scene['combatState'] = 'none'
        for key in SCENE_COMBAT_LAYOUT_KEYS:
            scene[key] = {}
    ensure_combat_state(snapshot)
    if new_client_session_id:
        snapshot['client_session_id'] = new_client_session_id
    snapshot['lastUpdatedAt'] = now_iso
    snapshot['chapter'] = {
        'number': chapter_number,
        'previousSessionId': previous_session_id,
        'startedAt': now_iso,
        'recap': recap_text,
    }
    return snapshot


def chapter_recap_text(previous_snapshot: dict, continue_line: str | None) -> str:
    """The ``Previously:`` text a new chapter opens with ('' when there is none).

    See :func:`session_recap_line`; the rolling summary is never used.
    """

    return _trim_to_sentence(session_recap_line(previous_snapshot, continue_line), CHAPTER_RECAP_MAX_LENGTH)


def _trim_to_sentence(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    boundary = 0
    for match in _SENTENCE_END_RE.finditer(text, 0, limit):
        end = match.end()
        if end == len(text) or text[end].isspace():
            boundary = end
    if boundary:
        return text[:boundary]
    # No sentence ends inside the budget: fall back to the last word boundary.
    window = text[:limit]
    words = window.rsplit(None, 1)
    return (words[0] if len(words) > 1 else window).rstrip()


def chapter_welcome_message(chapter_number: int, recap_text: str) -> str:
    message = f'**Chapter {chapter_number} begins.**'
    return f'{message}\n\nPreviously: {recap_text}' if recap_text else message


def initial_campaign_pack_snapshot(manifest: dict, *, campaign_id: int, imported_at: str) -> dict:
    """Return the opening snapshot ``import_campaign_pack`` builds for ``manifest``.

    The manifest was validated when the pack was imported; it is parsed again
    with the importer's own readers so a fresh chapter opens where the pack does.
    """

    first = pack_importer._first
    pack_id = pack_importer._required_slug(first(manifest, 'packId', 'pack_id'), field='packId')

    def records(
        field: str,
        aliases: tuple[str, ...],
        prefix: str,
        *,
        required_name: bool = False,
        limit: int = pack_importer.MAX_PACK_RECORDS,
    ) -> list[dict]:
        return pack_importer._pack_records(
            first(manifest, *aliases),
            field=field,
            pack_id=pack_id,
            fallback_prefix=prefix,
            required_name=required_name,
            limit=limit,
        )

    starting_state = pack_importer._bounded_json_value(
        pack_importer._record(first(manifest, 'startingState', 'starting_state', 'start')),
        path='startingState',
    )
    locations = records('locations', ('locations',), 'location')
    world_settings = pack_importer.pack_world_settings(manifest, pack_id=pack_id)
    pack_importer._place_locations(locations, world_settings)
    npcs = records('npcs', ('npcs', 'knownNpcs', 'known_npcs'), 'npc', required_name=True)
    quests = records('quests', ('quests',), 'quest')
    starting_location_id = pack_importer._clean_id(
        first(starting_state, 'locationId', 'location_id', 'startingLocationId', 'starting_location_id')
        or first(manifest, 'startingLocationId', 'starting_location_id')
    )
    if not starting_location_id and locations:
        starting_location_id = locations[0]['id']
    starting_quest_id = pack_importer._clean_id(
        first(starting_state, 'questId', 'quest_id', 'startingQuestId', 'starting_quest_id')
        or first(manifest, 'startingQuestId', 'starting_quest_id')
    )
    if not starting_quest_id and quests:
        starting_quest_id = quests[0]['id']
    return pack_importer._initial_snapshot(
        pack_id=pack_id,
        title=pack_importer._required_text(
            first(manifest, 'title', 'name'),
            field='title',
            max_length=pack_importer.MAX_TITLE_LENGTH,
        ),
        schema_version=pack_importer._schema_version(first(manifest, 'schemaVersion', 'schema_version')),
        version=pack_importer._optional_text(first(manifest, 'version'), max_length=80) or '1.0.0',
        starting_state=starting_state,
        starting_location=pack_importer._record_by_id(locations, starting_location_id),
        starting_location_id=starting_location_id,
        starting_quest=pack_importer._record_by_id(quests, starting_quest_id),
        starting_quest_id=starting_quest_id,
        locations=locations,
        npcs=npcs,
        quests=quests,
        enemies=pack_importer._enemy_records(first(manifest, 'enemies', 'bestiary'), pack_id=pack_id),
        checkpoints=records(
            'checkpoints',
            ('checkpoints',),
            'checkpoint',
            limit=pack_importer.MAX_PACK_CHECKPOINTS,
        ),
        encounters=records('encounters', ('encounters',), 'encounter'),
        director_rules=pack_importer._bounded_json_value(
            pack_importer._record(first(manifest, 'directorRules', 'director_rules')),
            path='directorRules',
        ),
        multi_session_group_key=pack_importer._optional_text(
            first(manifest, 'multiSessionGroupKey', 'multi_session_group_key'),
            max_length=pack_importer.MAX_ID_LENGTH,
        ),
        gm_notes=pack_importer._optional_bounded_json(
            first(manifest, 'gmNotes', 'gm_notes', 'hiddenNotes', 'hidden_notes'),
            path='gmNotes',
        ),
        hidden_scene_notes=pack_importer._optional_bounded_json(
            first(manifest, 'hiddenSceneNotes', 'hidden_scene_notes'),
            path='hiddenSceneNotes',
        ),
        dependencies=pack_importer._record_list(first(manifest, 'dependencies'), path='dependencies'),
        mods=pack_importer._record_list(first(manifest, 'mods'), path='mods'),
        marketplace=pack_importer._optional_bounded_json(first(manifest, 'marketplace', 'library'), path='marketplace'),
        extra_catalog_records=pack_importer._future_records_by_type(manifest, pack_id=pack_id),
        world_settings=world_settings,
        segments=pack_importer._segment_records(first(manifest, 'segments'), pack_id=pack_id),
        tie_lines=normalize_tie_lines(first(manifest, TIE_LINES_KEY)),
        session_id=None,
        campaign_id=campaign_id,
        imported_at=imported_at,
    )


def campaign_pack_seed(campaign_id: int, *, imported_at: str) -> CampaignPackSeed | None:
    """Return the opening state of the pack the campaign was imported from, if any."""

    progress_row = (
        CampaignPackSession.query.filter(
            CampaignPackSession.campaign_id == campaign_id,
            CampaignPackSession.campaign_pack_id.isnot(None),
        )
        .order_by(CampaignPackSession.campaign_pack_session_id.asc())
        .first()
    )
    if progress_row is None or progress_row.campaign_pack is None:
        return None
    manifest = safe_json_loads(progress_row.campaign_pack.manifest_json, None)
    if not isinstance(manifest, dict):
        return None
    return CampaignPackSeed(
        snapshot=initial_campaign_pack_snapshot(manifest, campaign_id=campaign_id, imported_at=imported_at),
        campaign_pack=progress_row.campaign_pack,
        installed_pack=progress_row.installed_pack,
    )


def find_idempotent_session(campaign_id: int, client_session_id: str) -> Session | None:
    direct_match = Session.query.filter_by(campaign_id=campaign_id, client_session_id=client_session_id).first()
    if direct_match:
        return direct_match

    for session_obj in Session.query.filter_by(campaign_id=campaign_id, client_session_id=None).all():
        if session_snapshot(session_obj).get('client_session_id') == client_session_id:
            return session_obj
    return None


def current_chapter_session(campaign_id: int) -> Session | None:
    session_id = current_chapter_session_id(campaign_id)
    return db.session.get(Session, session_id) if session_id is not None else None


def chapter_source_session(campaign_id: int) -> Session | None:
    """Return the chapter a new chapter continues from.

    That is the most recently active non-archived session, ranked the way the
    workspace list ranks sessions, with open chapters ahead of ended ones.
    """

    candidates = (
        Session.query.filter(
            Session.campaign_id == campaign_id,
            or_(Session.status.is_(None), Session.status.notin_((ARCHIVED_STATUS, DELETED_STATUS))),
        )
        .populate_existing()
        .all()
    )
    if not candidates:
        return None
    summaries = session_summaries([session_obj.session_id for session_obj in candidates])
    return max(
        candidates,
        key=lambda session_obj: (
            session_is_open(session_obj.status),
            session_latest_activity_at(
                session_obj,
                session_snapshot(session_obj),
                summaries.get(session_obj.session_id, {}),
            ) or '',
            session_obj.session_id,
        ),
    )


def unplayed_open_chapter(campaign_id: int) -> Session | None:
    """The campaign's latest chapter when it is still open and no turn has been played in it, else None."""

    source = chapter_source_session(campaign_id)
    if source is None or not session_is_open(source.status):
        return None
    if DmTurn.query.filter(DmTurn.session_id == source.session_id).first() is not None:
        return None
    return source


def chapter_unsettled_reason(session_obj: Session) -> str | None:
    """Return why the chapter cannot be left behind yet, or None when it is settled."""

    turns = DmTurn.query.filter(DmTurn.session_id == session_obj.session_id)
    if turns.filter(DmTurn.outcome_status == 'deferred').first() is not None:
        return 'pending_roll'
    if turns.filter(DmTurn.status.in_(UNSETTLED_TURN_STATUSES)).first() is not None:
        return 'turn_in_progress'
    combat = session_snapshot(session_obj).get('combat')
    if isinstance(combat, dict) and str(combat.get('status') or '').strip().lower() in ACTIVE_COMBAT_STATUSES:
        return 'active_combat'
    return None


def start_campaign_chapter(
    campaign_id: int,
    *,
    workspace_id: str | None,
    continuity: str = CHAPTER_CONTINUE,
    force: bool = False,
    name: str | None = None,
    client_session_id: str | None = None,
) -> ChapterStartResult:
    """Start the campaign's next chapter and commit it, or replay a started one.

    A latest chapter that is still open and has no turn yet is that replay too:
    it is returned as it is, so beginning an adventure never closes an unplayed
    chapter to open another.

    The chapters this closes and the chapter it reads from are fenced with the
    per-session turn coordinator, as session archive is, and the transition
    commits while those leases are held. A turn holding one of them rejects the
    start with ``session_busy`` instead of making it wait.
    """

    try:
        _locked_active_campaign(campaign_id, workspace_id)
        replay = find_idempotent_session(campaign_id, client_session_id) if client_session_id else None
        if replay is not None:
            return ChapterStartResult(session_id=replay.session_id, idempotent_replay=True)
        fence_session_ids = _chapter_fence_session_ids(campaign_id)
    finally:
        db.session.rollback()

    try:
        with session_turn_coordinator.try_serialized_many(fence_session_ids) as held:
            if held is None:
                raise _session_busy_error()
            try:
                campaign = _locked_active_campaign(campaign_id, workspace_id)
                replay = find_idempotent_session(campaign_id, client_session_id) if client_session_id else None
                if replay is None:
                    replay = unplayed_open_chapter(campaign_id)
                if replay is not None:
                    result = ChapterStartResult(session_id=replay.session_id, idempotent_replay=True)
                    db.session.rollback()
                    return result
                if _chapter_fence_session_ids(campaign_id) != fence_session_ids:
                    raise _session_busy_error()
                session_obj = _start_chapter_locked(
                    campaign,
                    continuity=continuity,
                    force=force,
                    name=name,
                    client_session_id=client_session_id,
                )
                result = ChapterStartResult(session_id=session_obj.session_id, idempotent_replay=False)
                # Database-backed coordinator leases fence this commit.
                db.session.commit()
                return result
            except Exception:
                db.session.rollback()
                raise
    except (SessionTurnTargetMissingError, TurnLeaseLostError):
        db.session.rollback()
        raise _session_busy_error() from None


def _session_busy_error() -> ChapterStartError:
    return ChapterStartError('session_busy', SESSION_BUSY_MESSAGE, 409)


def _locked_active_campaign(campaign_id: int, workspace_id: str | None) -> Campaign:
    campaign = db.session.get(Campaign, campaign_id, populate_existing=True, with_for_update=True)
    if campaign is None or (campaign.workspace_id or DEFAULT_WORKSPACE_ID) != (workspace_id or DEFAULT_WORKSPACE_ID):
        raise ChapterStartError('campaign_not_found', 'Campaign not found.', 404)
    if str(campaign.status or ACTIVE_STATUS).strip().lower() != ACTIVE_STATUS:
        raise ChapterStartError(
            'campaign_archived',
            'This campaign is archived. Restore it before starting a session.',
            409,
        )
    return campaign


def _open_chapters_query(campaign_id: int):
    return Session.query.filter(Session.campaign_id == campaign_id, open_sessions_filter())


def _chapter_fence_session_ids(campaign_id: int) -> list[int]:
    session_ids = {session_obj.session_id for session_obj in _open_chapters_query(campaign_id).all()}
    source = chapter_source_session(campaign_id)
    if source is not None:
        session_ids.add(source.session_id)
    return sorted(session_ids)


def _next_chapter_number(campaign_id: int) -> int:
    session_count = (
        db.session.query(func.count(Session.session_id))
        .filter(
            Session.campaign_id == campaign_id,
            or_(Session.status.is_(None), Session.status != DELETED_STATUS),
        )
        .scalar()
    )
    return int(session_count or 0) + 1


def _start_chapter_locked(
    campaign: Campaign,
    *,
    continuity: str,
    force: bool,
    name: str | None,
    client_session_id: str | None,
) -> Session:
    source = chapter_source_session(campaign.campaign_id)
    if source is None:
        return _create_chapter_session(
            campaign,
            name=name,
            client_session_id=client_session_id,
            snapshot={'client_session_id': client_session_id} if client_session_id else None,
            welcome_message=FIRST_CHAPTER_WELCOME,
        )

    # An ended source was settled (or forced) when it closed and cannot change.
    if not force and session_is_open(source.status):
        reason = chapter_unsettled_reason(source)
        if reason:
            raise ChapterStartError(
                'chapter_unsettled',
                CHAPTER_UNSETTLED_MESSAGE,
                409,
                {'session_id': source.session_id, 'reason': reason},
            )

    now = utc_now()
    now_iso = now.isoformat()
    chapter_number = _next_chapter_number(campaign.campaign_id)
    source_state = SessionState.query.filter_by(session_id=source.session_id).first()
    pack_seed = None
    if continuity == CHAPTER_CONTINUE:
        previous_snapshot = session_snapshot(source)
        latest_narration = session_summaries([source.session_id])[source.session_id]['latest_narration']
        recap_text = chapter_recap_text(previous_snapshot, session_continue_line(previous_snapshot, latest_narration))
        snapshot = chapter_snapshot_from(
            previous_snapshot,
            new_client_session_id=client_session_id,
            chapter_number=chapter_number,
            previous_session_id=source.session_id,
            recap_text=recap_text,
            now_iso=now_iso,
        )
    else:
        recap_text = ''
        pack_seed = campaign_pack_seed(campaign.campaign_id, imported_at=now_iso)
        snapshot = pack_seed.snapshot if pack_seed else {}
        if client_session_id:
            snapshot['client_session_id'] = client_session_id
        snapshot['chapter'] = {
            'number': chapter_number,
            'previousSessionId': source.session_id,
            'startedAt': now_iso,
            'recap': '',
            'fresh': True,
        }

    _close_open_chapters(campaign.campaign_id, now=now)
    session_obj = _create_chapter_session(
        campaign,
        name=name or f'Chapter {chapter_number}',
        client_session_id=client_session_id,
        snapshot=snapshot,
        welcome_message=chapter_welcome_message(chapter_number, recap_text),
        welcome_metadata={'chapter_number': chapter_number, 'continuity': continuity},
    )
    if continuity == CHAPTER_CONTINUE:
        _copy_session_state(source_state, session_obj, campaign)
        _carry_campaign_pack_progress(source, session_obj)
    else:
        get_or_create_session_state(session_obj.session_id, campaign)
        if pack_seed is not None:
            _seed_campaign_pack_progress(session_obj, pack_seed)
    return session_obj


def _close_open_chapters(campaign_id: int, *, now) -> None:
    now_iso = now.isoformat()
    open_chapters = (
        _open_chapters_query(campaign_id)
        .order_by(Session.session_id.asc())
        .populate_existing()
        .with_for_update()
        .all()
    )
    for session_obj in open_chapters:
        snapshot = session_snapshot(session_obj)
        snapshot['ended_at'] = now_iso
        session_obj.status = ENDED_STATUS
        session_obj.state_snapshot = safe_json_dumps(snapshot, {})
        session_obj.updated_at = now
        record_turn_event(
            session_id=session_obj.session_id,
            campaign_id=campaign_id,
            event_type=SESSION_ENDED_EVENT,
            payload={
                'message': CHAPTER_CLOSED_MESSAGE,
                'metadata': {'kind': 'chapter_closed', 'ended_at': now_iso},
            },
        )


def _create_chapter_session(
    campaign: Campaign,
    *,
    name: str | None,
    client_session_id: str | None,
    snapshot: dict | None,
    welcome_message: str,
    welcome_metadata: dict | None = None,
) -> Session:
    session_obj = Session(
        campaign_id=campaign.campaign_id,
        name=name,
        client_session_id=client_session_id,
        state_snapshot=(safe_json_dumps(snapshot, {}) if snapshot else None),
    )
    db.session.add(session_obj)
    db.session.flush()
    record_turn_event(
        session_id=session_obj.session_id,
        campaign_id=campaign.campaign_id,
        event_type=SESSION_STARTED_EVENT,
        payload={
            'message': welcome_message,
            'metadata': {
                'kind': 'session_welcome',
                'client_session_id': client_session_id,
                **(welcome_metadata or {}),
            },
        },
    )
    return session_obj


def _copy_session_state(source_state: SessionState | None, session_obj: Session, campaign: Campaign) -> None:
    if source_state is None:
        get_or_create_session_state(session_obj.session_id, campaign)
        return
    db.session.add(
        SessionState(
            session_id=session_obj.session_id,
            current_location=source_state.current_location,
            current_quest=source_state.current_quest,
            rolling_summary=source_state.rolling_summary,
            active_segments=source_state.active_segments,
            memory_snippets=source_state.memory_snippets,
        )
    )


def _carry_campaign_pack_progress(source: Session, session_obj: Session) -> None:
    """Give the new chapter durable pack-progress rows matching its copied mirror."""

    try:
        progress = campaign_pack_progress_payload(session_id=session_obj.session_id, include_hidden=True)
    except CampaignPackProgressError:
        return
    source_progress = CampaignPackSession.query.filter_by(session_id=source.session_id).first()
    sync_campaign_pack_progress(
        session=session_obj,
        pack=session_snapshot(session_obj)['campaignPack'],
        checkpoints=progress['checkpoints'],
        active_checkpoint_id=progress['activeCheckpointId'],
        completed_ids=progress['completedCheckpointIds'],
        skipped_ids=progress['skippedCheckpointIds'],
        failed_ids=progress['failedCheckpointIds'],
        progress_revision=progress['progressRevision'],
        campaign_pack=source_progress.campaign_pack if source_progress else None,
        installed_pack=source_progress.installed_pack if source_progress else None,
    )


def _seed_campaign_pack_progress(session_obj: Session, pack_seed: CampaignPackSeed) -> None:
    pack = pack_seed.snapshot['campaignPack']
    sync_campaign_pack_progress(
        session=session_obj,
        pack=pack,
        checkpoints=pack['checkpoints'],
        active_checkpoint_id=pack.get('activeCheckpointId'),
        completed_ids=[],
        skipped_ids=[],
        failed_ids=[],
        progress_revision=0,
        campaign_pack=pack_seed.campaign_pack,
        installed_pack=pack_seed.installed_pack,
    )
