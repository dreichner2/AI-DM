"""Backend-owned TypeScript contract for public REST DTOs.

The Flask blueprints build these shapes through ``response_dtos.py``.  The
React client consumes the generated TypeScript from this contract so response
shape changes have one backend-owned place to update.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TypeField:
    name: str
    ts_type: str
    optional: bool = False


@dataclass(frozen=True)
class TypeContract:
    name: str
    fields: tuple[TypeField, ...] = ()
    alias: str | None = None
    comment: str | None = None


def field(name: str, ts_type: str, *, optional: bool = False) -> TypeField:
    return TypeField(name=name, ts_type=ts_type, optional=optional)


API_TYPE_CONTRACTS: tuple[TypeContract, ...] = (
    TypeContract('JsonRecord', alias='Record<string, unknown>'),
    TypeContract(
        'World',
        fields=(
            field('world_id', 'number'),
            field('name', 'string'),
            field('description', 'string | null'),
            field('created_at', 'string | null'),
        ),
    ),
    TypeContract(
        'Campaign',
        fields=(
            field('campaign_id', 'number'),
            field('title', 'string'),
            field('description', 'string | null'),
            field('world_id', 'number'),
            field('world_name', 'string | null'),
            field('created_at', 'string | null'),
            field('updated_at', 'string | null'),
            field('status', 'string'),
            field('is_archived', 'boolean'),
            field('current_quest', 'string | null'),
            field('location', 'string | null'),
            field('session_count', 'number'),
            field('latest_session_id', 'number | null'),
            field('latest_activity_at', 'string | null'),
        ),
    ),
    TypeContract(
        'SessionSummary',
        fields=(
            field('session_id', 'number'),
            field('campaign_id', 'number'),
            field('created_at', 'string | null'),
            field('status', 'string'),
            field('deleted_at', 'string | null'),
            field('updated_at', 'string | null'),
            field('latest_activity_at', 'string | null'),
            field('display_name', 'string'),
            field('turn_count', 'number'),
            field('latest_summary', 'string'),
            field('is_archived', 'boolean'),
            field('state_snapshot', 'JsonRecord | null'),
        ),
    ),
    TypeContract(
        'SessionImportResponse',
        fields=(
            field('imported', 'boolean'),
            field('session_id', 'number'),
            field('session', 'SessionSummary'),
            field(
                'counts',
                '{ turn_events: number; projected_log_entries: number; log_entries: number; session_state: number }',
            ),
        ),
    ),
    TypeContract(
        'CampaignWorkspace',
        fields=(
            field('campaign', 'Campaign'),
            field('sessions', 'SessionSummary[]'),
            field('players', 'Player[]'),
            field('maps', 'MapItem[]'),
            field('segments', 'CampaignSegment[]'),
            field(
                'summary',
                '{ session_count: number; player_count: number; map_count: number; segment_count: number; latest_session_id: number | null; latest_activity_at: string | null }',
            ),
            field(
                'has_more',
                '{ sessions: boolean; players: boolean; maps: boolean; segments: boolean }',
            ),
            field(
                'next_cursor',
                '{ sessions: number | null; players: number | null; maps: number | null; segments: number | null }',
            ),
            field(
                'limits',
                '{ sessions: number | null; players: number | null; maps: number | null; segments: number | null }',
            ),
        ),
    ),
    TypeContract(
        'SessionLogEntry',
        fields=(
            field('id', 'number'),
            field('message', 'string'),
            field('entry_type', 'string'),
            field('metadata', 'JsonRecord'),
            field('timestamp', 'string | null'),
        ),
    ),
    TypeContract(
        'SessionLogResponse',
        fields=(
            field('session_id', 'number'),
            field('limit', 'number', optional=True),
            field('has_more', 'boolean', optional=True),
            field('next_cursor', 'number | null', optional=True),
            field('entries', 'SessionLogEntry[]'),
        ),
    ),
    TypeContract(
        'TurnEventPayload',
        fields=(
            field('event_id', 'number'),
            field('session_id', 'number'),
            field('campaign_id', 'number'),
            field('turn_id', 'number | null'),
            field('player_id', 'number | null'),
            field('event_type', 'string'),
            field('payload', 'JsonRecord'),
            field('created_at', 'string | null'),
        ),
    ),
    TypeContract(
        'SessionEventsResponse',
        fields=(
            field('session_id', 'number'),
            field('limit', 'number', optional=True),
            field('has_more', 'boolean', optional=True),
            field('next_cursor', 'number | null', optional=True),
            field('events', 'TurnEventPayload[]'),
        ),
    ),
    TypeContract(
        'SessionState',
        fields=(
            field('session_id', 'number'),
            field('campaign_id', 'number'),
            field('current_location', 'string | null'),
            field('current_quest', 'string | null'),
            field('rolling_summary', 'string'),
            field('active_segments', 'unknown[]'),
            field('memory_snippets', 'unknown[]'),
            field('updated_at', 'string | null'),
        ),
    ),
    TypeContract(
        'Player',
        fields=(
            field('player_id', 'number'),
            field('workspace_id', 'string'),
            field('campaign_id', 'number | null'),
            field('name', 'string'),
            field('character_name', 'string'),
            field('race', 'string | null'),
            field('sex', 'string | null'),
            field('profile_image', 'string'),
            field('class_', 'string | null'),
            field('char_class', 'string | null'),
            field('level', 'number'),
            field('created_at', 'string | null'),
            field('updated_at', 'string | null'),
        ),
    ),
    TypeContract(
        'PlayerDetail',
        fields=(
            field('player_id', 'number'),
            field('workspace_id', 'string'),
            field('campaign_id', 'number | null'),
            field('name', 'string'),
            field('character_name', 'string'),
            field('race', 'string | null'),
            field('sex', 'string | null'),
            field('profile_image', 'string'),
            field('class_', 'string | null'),
            field('char_class', 'string | null'),
            field('level', 'number'),
            field('created_at', 'string | null'),
            field('updated_at', 'string | null'),
            field('stats', 'unknown'),
            field('inventory', 'unknown'),
            field('character_sheet', 'unknown'),
        ),
    ),
    TypeContract(
        'MapItem',
        fields=(
            field('map_id', 'number'),
            field('world_id', 'number | null'),
            field('campaign_id', 'number | null'),
            field('title', 'string'),
            field('description', 'string | null'),
            field('map_data', 'JsonRecord'),
            field('created_at', 'string | null'),
            field('updated_at', 'string | null'),
        ),
    ),
    TypeContract(
        'CampaignSegment',
        fields=(
            field('segment_id', 'number'),
            field('campaign_id', 'number'),
            field('title', 'string'),
            field('description', 'string | null'),
            field('trigger_condition', 'string | null'),
            field('tags', 'string | null'),
            field('is_triggered', 'boolean'),
            field('created_at', 'string | null'),
            field('updated_at', 'string | null'),
        ),
    ),
)


API_TYPE_CONTRACT_BY_NAME = {contract.name: contract for contract in API_TYPE_CONTRACTS}
