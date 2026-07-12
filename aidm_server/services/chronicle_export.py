"""Self-contained Chronicle HTML exports built from recorded DM turns."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from flask import Response, render_template
from sqlalchemy import or_

from aidm_server.models import (
    Campaign,
    CampaignPackCheckpointProgress,
    CampaignPackProgressEvent,
    CampaignPackSession,
    DmTurn,
    Session,
    SessionLogEntry,
    SessionState,
    TurnEvent,
    safe_json_loads,
)
from aidm_server.response_dtos import session_display_name
from aidm_server.services.campaign_pack_progress import PROGRESS_CHANGED_EVENT
from aidm_server.time_utils import utc_now


ARCHIVED_STATUS = 'archived'
DELETED_STATUS = 'deleted'


@dataclass(frozen=True)
class ChronicleExport:
    html: str
    filename: str


def chronicle_html_response(export: ChronicleExport) -> Response:
    response = Response(export.html, content_type='text/html; charset=utf-8')
    response.headers['Content-Disposition'] = f'attachment; filename="{export.filename}"'
    response.headers['Cache-Control'] = 'no-store'
    return response


@dataclass(frozen=True)
class _TurnEntry:
    turn: DmTurn
    session: Session
    session_name: str


@dataclass(frozen=True)
class _ProgressMarker:
    boundary_index: int
    title: str
    subtitle: str
    event_id: int | None
    session_id: int | None


@dataclass(frozen=True)
class _Chapter:
    title: str
    source: str
    subtitle: str
    turns: tuple[_TurnEntry, ...]


def export_campaign_chronicle_html(
    campaign: Campaign,
    *,
    include_archived_sessions: bool = False,
    include_director_metadata: bool,
) -> ChronicleExport:
    sessions = _campaign_sessions(campaign, include_archived_sessions=include_archived_sessions)
    return _export_chronicle(
        campaign=campaign,
        sessions=sessions,
        scope_label='Campaign',
        filename_scope='campaign',
        resource_id=campaign.campaign_id,
        include_director_metadata=include_director_metadata,
    )


def export_session_chronicle_html(
    session_obj: Session,
    *,
    include_director_metadata: bool,
) -> ChronicleExport:
    return _export_chronicle(
        campaign=session_obj.campaign,
        sessions=[session_obj],
        scope_label='Session',
        filename_scope='session',
        resource_id=session_obj.session_id,
        include_director_metadata=include_director_metadata,
    )


def _campaign_sessions(campaign: Campaign, *, include_archived_sessions: bool) -> list[Session]:
    query = Session.query.filter_by(campaign_id=campaign.campaign_id)
    if not include_archived_sessions:
        query = query.filter(or_(Session.status.is_(None), Session.status.notin_([ARCHIVED_STATUS, DELETED_STATUS])))
    return query.order_by(Session.created_at.asc(), Session.session_id.asc()).all()


def _export_chronicle(
    *,
    campaign: Campaign,
    sessions: list[Session],
    scope_label: str,
    filename_scope: str,
    resource_id: int,
    include_director_metadata: bool,
) -> ChronicleExport:
    session_names = _session_names(sessions)
    turn_entries = _turn_entries(sessions, session_names)
    chapters = _chapters_from_progress_events(
        sessions,
        turn_entries,
        include_director_metadata=include_director_metadata,
    )
    if chapters is None:
        chapters = _chapters_from_session_boundaries(sessions, session_names, turn_entries)

    html = _render_html(
        campaign=campaign,
        scope_label=scope_label,
        chapters=chapters,
        epilogue=_epilogue_text(sessions),
        turn_count=len(turn_entries),
        include_director_metadata=include_director_metadata,
    )
    return ChronicleExport(
        html=html,
        filename=_chronicle_filename(campaign.title, scope=filename_scope, resource_id=resource_id),
    )


def _session_names(sessions: list[Session]) -> dict[int, str]:
    names: dict[int, str] = {}
    for index, session_obj in enumerate(sessions, start=1):
        names[session_obj.session_id] = session_display_name(session_obj, campaign_ordinal=index)
    return names


def _turn_entries(sessions: list[Session], session_names: dict[int, str]) -> list[_TurnEntry]:
    session_by_id = {session_obj.session_id: session_obj for session_obj in sessions}
    session_ids = list(session_by_id)
    if not session_ids:
        return []

    turns = (
        DmTurn.query.filter(DmTurn.session_id.in_(session_ids))
        .order_by(DmTurn.created_at.asc(), DmTurn.turn_id.asc())
        .all()
    )
    entries: list[_TurnEntry] = []
    for turn in turns:
        if not _text(turn.dm_output) and not _text(turn.player_input):
            continue
        session_obj = session_by_id.get(turn.session_id)
        if session_obj is None:
            continue
        entries.append(
            _TurnEntry(
                turn=turn,
                session=session_obj,
                session_name=session_names.get(turn.session_id, f'Session {turn.session_id}'),
            )
        )
    return entries


def _chapters_from_progress_events(
    sessions: list[Session],
    turn_entries: list[_TurnEntry],
    *,
    include_director_metadata: bool,
) -> tuple[_Chapter, ...] | None:
    markers = _progress_markers(
        sessions,
        turn_entries,
        include_director_metadata=include_director_metadata,
    )
    if not markers:
        return None

    chapters: list[_Chapter] = []
    first_boundary = markers[0].boundary_index
    if first_boundary > 0:
        chapters.append(
            _Chapter(
                title='Opening',
                source='campaign-pack-progress',
                subtitle='Before the first recorded campaign pack progress event',
                turns=tuple(turn_entries[:first_boundary]),
            )
        )

    for index, marker in enumerate(markers):
        if turn_entries and marker.boundary_index >= len(turn_entries):
            continue
        next_boundary = markers[index + 1].boundary_index if index + 1 < len(markers) else len(turn_entries)
        chapter_turns = tuple(turn_entries[marker.boundary_index:next_boundary])
        if not chapter_turns and turn_entries:
            continue
        chapters.append(
            _Chapter(
                title=marker.title,
                source='campaign-pack-progress',
                subtitle=marker.subtitle,
                turns=chapter_turns,
            )
        )

    return tuple(chapters) if chapters else None


def _chapters_from_session_boundaries(
    sessions: list[Session],
    session_names: dict[int, str],
    turn_entries: list[_TurnEntry],
) -> tuple[_Chapter, ...]:
    turns_by_session: dict[int, list[_TurnEntry]] = {session_obj.session_id: [] for session_obj in sessions}
    for entry in turn_entries:
        turns_by_session.setdefault(entry.session.session_id, []).append(entry)

    chapters: list[_Chapter] = []
    for session_obj in sessions:
        session_name = session_names.get(session_obj.session_id, f'Session {session_obj.session_id}')
        chapters.append(
            _Chapter(
                title=session_name,
                source='session-boundary',
                subtitle=_session_subtitle(session_obj),
                turns=tuple(turns_by_session.get(session_obj.session_id, [])),
            )
        )

    if not chapters:
        chapters.append(
            _Chapter(
                title='No Sessions Recorded',
                source='session-boundary',
                subtitle='This campaign does not have recorded session boundaries yet.',
                turns=(),
            )
        )
    return tuple(chapters)


def _progress_markers(
    sessions: list[Session],
    turn_entries: list[_TurnEntry],
    *,
    include_director_metadata: bool,
) -> list[_ProgressMarker]:
    session_ids = [session_obj.session_id for session_obj in sessions]
    if not session_ids:
        return []

    checkpoint_titles = _checkpoint_titles(session_ids)
    turn_events = (
        TurnEvent.query.filter(
            TurnEvent.session_id.in_(session_ids),
            TurnEvent.event_type == PROGRESS_CHANGED_EVENT,
        )
        .order_by(TurnEvent.created_at.asc(), TurnEvent.event_id.asc())
        .all()
    )
    markers = [
        _turn_progress_marker(
            event,
            turn_entries=turn_entries,
            checkpoint_titles=checkpoint_titles,
            include_director_metadata=include_director_metadata,
        )
        for event in turn_events
    ]
    markers = [marker for marker in markers if marker is not None]
    if markers:
        return _dedupe_markers(markers)

    durable_events = (
        CampaignPackProgressEvent.query.filter(CampaignPackProgressEvent.session_id.in_(session_ids))
        .order_by(CampaignPackProgressEvent.created_at.asc(), CampaignPackProgressEvent.progress_event_id.asc())
        .all()
    )
    markers = [
        _durable_progress_marker(
            event,
            turn_entries=turn_entries,
            checkpoint_titles=checkpoint_titles,
            include_director_metadata=include_director_metadata,
        )
        for event in durable_events
    ]
    markers = [marker for marker in markers if marker is not None]
    return _dedupe_markers(markers)


def _checkpoint_titles(session_ids: list[int]) -> dict[tuple[str, int | None, str], str]:
    rows = (
        CampaignPackCheckpointProgress.query.join(
            CampaignPackSession,
            CampaignPackCheckpointProgress.campaign_pack_session_id == CampaignPackSession.campaign_pack_session_id,
        )
        .filter(CampaignPackSession.session_id.in_(session_ids))
        .all()
    )
    titles: dict[tuple[str, int | None, str], str] = {}
    for row in rows:
        checkpoint_id = _text(row.checkpoint_id)
        title = _text(row.title)
        if not checkpoint_id or not title:
            continue
        pack_session = row.campaign_pack_session
        titles[('pack_session', row.campaign_pack_session_id, checkpoint_id)] = title
        if pack_session:
            titles[('session', pack_session.session_id, checkpoint_id)] = title
    return titles


def _durable_progress_marker(
    event: CampaignPackProgressEvent,
    *,
    turn_entries: list[_TurnEntry],
    checkpoint_titles: dict[tuple[str, int | None, str], str],
    include_director_metadata: bool,
) -> _ProgressMarker | None:
    boundary = _boundary_index(turn_entries, turn_id=event.turn_id, created_at=event.created_at)
    checkpoint_id = _text(event.to_checkpoint_id) or _text(event.from_checkpoint_id)
    revealed_title = (
        checkpoint_titles.get(('pack_session', event.campaign_pack_session_id, checkpoint_id))
        if checkpoint_id
        else None
    )
    title = revealed_title or (
        _progress_title(checkpoint_id, event.action, event.reason)
        if include_director_metadata
        else 'Campaign Progress'
    )
    subtitle = (
        _progress_subtitle(
            action=event.action,
            reason=event.reason,
            revision=event.progress_revision,
            event_label=f'progress event {event.progress_event_id}',
        )
        if include_director_metadata
        else ''
    )
    return _ProgressMarker(
        boundary_index=boundary,
        title=title,
        subtitle=subtitle,
        event_id=event.progress_event_id,
        session_id=event.session_id,
    )


def _turn_progress_marker(
    event: TurnEvent,
    *,
    turn_entries: list[_TurnEntry],
    checkpoint_titles: dict[tuple[str, int | None, str], str],
    include_director_metadata: bool,
) -> _ProgressMarker | None:
    payload = safe_json_loads(event.payload_json, {})
    if not isinstance(payload, dict):
        return None
    boundary = _boundary_index(turn_entries, turn_id=event.turn_id, created_at=event.created_at)
    checkpoint_id = _text(payload.get('toCheckpointId') or payload.get('to_checkpoint_id'))
    if not checkpoint_id:
        checkpoint_id = _text(payload.get('fromCheckpointId') or payload.get('from_checkpoint_id'))
    revealed_title = checkpoint_titles.get(('session', event.session_id, checkpoint_id)) if checkpoint_id else None
    title = revealed_title or (
        _progress_title(checkpoint_id, payload.get('action'), payload.get('reason'))
        if include_director_metadata
        else 'Campaign Progress'
    )
    subtitle = (
        _progress_subtitle(
            action=payload.get('action'),
            reason=payload.get('reason'),
            revision=payload.get('progressRevision') or payload.get('progress_revision'),
            event_label=f'turn event {event.event_id}',
        )
        if include_director_metadata
        else ''
    )
    return _ProgressMarker(
        boundary_index=boundary,
        title=title,
        subtitle=subtitle,
        event_id=event.event_id,
        session_id=event.session_id,
    )


def _dedupe_markers(markers: list[_ProgressMarker]) -> list[_ProgressMarker]:
    by_boundary: dict[int, _ProgressMarker] = {}
    for marker in markers:
        by_boundary[marker.boundary_index] = marker
    return [by_boundary[key] for key in sorted(by_boundary)]


def _boundary_index(turn_entries: list[_TurnEntry], *, turn_id: int | None, created_at: Any) -> int:
    if not turn_entries:
        return 0

    if turn_id is not None:
        for index, entry in enumerate(turn_entries):
            if entry.turn.turn_id == turn_id:
                return index

    if created_at is not None:
        for index, entry in enumerate(turn_entries):
            if _datetime_gte(entry.turn.created_at, created_at):
                return index
        return len(turn_entries)

    return 0


def _datetime_gte(left: Any, right: Any) -> bool:
    if left is None:
        return False
    try:
        return left >= right
    except TypeError:
        try:
            left_naive = left.replace(tzinfo=None)
            right_naive = right.replace(tzinfo=None)
            return left_naive >= right_naive
        except Exception:
            return False


def _progress_title(checkpoint_id: str, action: Any, reason: Any) -> str:
    if checkpoint_id:
        return _humanize_identifier(checkpoint_id)
    action_text = _humanize_identifier(_text(action))
    reason_text = _humanize_identifier(_text(reason))
    return action_text or reason_text or 'Campaign Pack Progress'


def _progress_subtitle(*, action: Any, reason: Any, revision: Any, event_label: str) -> str:
    parts = [event_label]
    action_text = _text(action)
    if action_text:
        parts.append(_humanize_identifier(action_text))
    revision_text = _text(revision)
    if revision_text:
        parts.append(f'revision {revision_text}')
    reason_text = _text(reason)
    if reason_text:
        parts.append(reason_text)
    return ' | '.join(parts)


def _session_subtitle(session_obj: Session) -> str:
    parts = [f'session {session_obj.session_id}']
    if session_obj.created_at:
        parts.append(f'started {_isoformat(session_obj.created_at)}')
    status = _text(session_obj.status)
    if status and status != 'active':
        parts.append(status)
    return ' | '.join(parts)


def _epilogue_text(sessions: list[Session]) -> str:
    session_ids = [session_obj.session_id for session_obj in sessions]
    if not session_ids:
        return ''

    session_state = (
        SessionState.query.filter(
            SessionState.session_id.in_(session_ids),
            SessionState.rolling_summary.isnot(None),
        )
        .order_by(SessionState.updated_at.desc(), SessionState.state_id.desc())
        .first()
    )
    if session_state and _text(session_state.rolling_summary):
        return _text(session_state.rolling_summary)

    log_entries = (
        SessionLogEntry.query.filter(SessionLogEntry.session_id.in_(session_ids))
        .order_by(SessionLogEntry.timestamp.desc(), SessionLogEntry.id.desc())
        .limit(5)
        .all()
    )
    tail = [_text(entry.message) for entry in reversed(log_entries) if _text(entry.message)]
    return '\n\n'.join(tail)


def _render_html(
    *,
    campaign: Campaign,
    scope_label: str,
    chapters: tuple[_Chapter, ...],
    epilogue: str,
    turn_count: int,
    include_director_metadata: bool,
) -> str:
    exported_at = _isoformat(utc_now())
    chapter_source = 'campaign pack progress events' if any(
        chapter.source == 'campaign-pack-progress' for chapter in chapters
    ) else 'session boundaries'
    title = f'{campaign.title} Chronicle'
    return render_template(
        'chronicle.html',
        title=title,
        scope_label=scope_label,
        campaign=campaign,
        description=_text(campaign.description) or 'Recorded campaign chronicle.',
        chapters=[
            _chapter_view(
                index,
                chapter,
                include_director_metadata=include_director_metadata,
            )
            for index, chapter in enumerate(chapters, start=1)
        ],
        chapter_count=len(chapters),
        turn_count=turn_count,
        chapter_source=chapter_source,
        epilogue=_paragraphs(epilogue),
        exported_at=exported_at,
        include_director_metadata=include_director_metadata,
    )


def _chapter_view(
    index: int,
    chapter: _Chapter,
    *,
    include_director_metadata: bool,
) -> dict[str, Any]:
    return {
        'index': index,
        'title': chapter.title,
        'source': chapter.source,
        'subtitle': chapter.subtitle if include_director_metadata else '',
        'commentary': _chapter_commentary(chapter) if include_director_metadata else [],
        'turns': [
            _turn_view(entry, include_director_metadata=include_director_metadata)
            for entry in chapter.turns
        ],
    }


def _chapter_commentary(chapter: _Chapter) -> list[str]:
    notes = []
    source_label = _humanize_identifier(chapter.source)
    notes.append(f'Chapter boundary: {source_label or chapter.source}.')
    if chapter.source == 'campaign-pack-progress':
        notes.append(f'Director track: this chapter begins at a campaign-pack progress marker ({chapter.subtitle}).')
    elif chapter.source == 'session-boundary':
        notes.append(f'Director track: this chapter follows the recorded session boundary ({chapter.subtitle}).')

    turns = list(chapter.turns)
    if not turns:
        notes.append('No recorded DM turns are attached to this chapter yet.')
        return notes

    session_names = list(dict.fromkeys(entry.session_name for entry in turns if entry.session_name))
    notes.append(
        f'Coverage: {len(turns)} turn{"s" if len(turns) != 1 else ""}'
        f' across {", ".join(session_names[:3])}{", ..." if len(session_names) > 3 else ""}.'
    )
    first_turn = turns[0].turn.turn_id
    last_turn = turns[-1].turn.turn_id
    if first_turn != last_turn:
        notes.append(f'Turn span: {first_turn} to {last_turn}.')
    else:
        notes.append(f'Turn span: {first_turn}.')

    rules_turns = [
        entry.turn
        for entry in turns
        if entry.turn.requires_roll or entry.turn.outcome_status == 'deferred' or _text(entry.turn.rule_type)
    ]
    if rules_turns:
        notes.append(f'Rules pressure: {len(rules_turns)} turn{"s" if len(rules_turns) != 1 else ""} carried roll or resolution metadata.')

    state_pipeline_turns = []
    for entry in turns:
        metadata = safe_json_loads(entry.turn.metadata_json, {})
        metadata = metadata if isinstance(metadata, dict) else {}
        if isinstance(metadata.get('state_pipeline'), dict):
            state_pipeline_turns.append(entry.turn)
    if state_pipeline_turns:
        notes.append(
            f'State continuity: {len(state_pipeline_turns)} turn{"s" if len(state_pipeline_turns) != 1 else ""} recorded state-pipeline metadata.'
        )

    runtime_labels = list(
        dict.fromkeys(
            f'{turn.llm_provider}/{turn.llm_model}'
            for turn in (entry.turn for entry in turns)
            if _text(turn.llm_provider) or _text(turn.llm_model)
        )
    )
    if runtime_labels:
        notes.append(f'Runtime trace: {", ".join(runtime_labels[:3])}{", ..." if len(runtime_labels) > 3 else ""}.')
    return notes


def _turn_view(entry: _TurnEntry, *, include_director_metadata: bool) -> dict[str, Any]:
    turn = entry.turn
    return {
        'turn_id': turn.turn_id if include_director_metadata else None,
        'session_name': entry.session_name if include_director_metadata else '',
        'created_at': _isoformat(turn.created_at) if include_director_metadata else '',
        'player_input': _lines(turn.player_input),
        'dm_paragraphs': _paragraphs(turn.dm_output),
    }


def _paragraphs(value: Any) -> list[list[str]]:
    text = _text(value)
    if not text:
        return []
    return [_lines(paragraph) for paragraph in re.split(r'\n\s*\n', text) if paragraph.strip()]


def _lines(value: Any) -> list[str]:
    return _text(value).splitlines()


def _chronicle_filename(title: str, *, scope: str, resource_id: int) -> str:
    slug = re.sub(r'[^a-z0-9]+', '-', _text(title).lower()).strip('-') or scope
    return f'{slug}-{scope}-{resource_id}-chronicle.html'


def _humanize_identifier(value: str) -> str:
    text = _text(value)
    if not text:
        return ''
    text = re.sub(r'^(cp|checkpoint|chk)[_-]+', '', text, flags=re.IGNORECASE)
    text = text.replace('_', ' ').replace('-', ' ')
    return ' '.join(part.capitalize() for part in text.split())


def _isoformat(value: Any) -> str:
    return value.isoformat() if value else ''


def _text(value: Any) -> str:
    return str(value or '').strip()
