from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aidm_server.auth import DEFAULT_WORKSPACE_ID, normalize_workspace_name_key
from aidm_server.database import db
from aidm_server.game_state.models import state_snapshot_for_session
from aidm_server.models import Campaign, Player, Session, SessionState, Workspace, safe_json_dumps, safe_json_loads
from aidm_server.response_dtos import campaign_payload, player_detail_payload, session_payload
from aidm_server.services.campaign_pack import CampaignPackImportError, import_campaign_pack
from aidm_server.services.campaign_pack_examples import get_example_campaign_pack
from aidm_server.services.pregen_characters import (
    build_player_from_preset,
    default_pregenerated_character_id,
    player_matches_preset,
    pregenerated_character_payload,
    pregenerated_character_preset,
)
from aidm_server.time_utils import utc_now
from aidm_server.turn_coordinator import session_turn_coordinator


DEFAULT_PLAY_NOW_EXAMPLE_PACK_ID = 'original_fantasy.road_of_unremembered_kings'
PLAY_NOW_VERSION = 1


class PlayNowOnboardingError(ValueError):
    def __init__(self, message: str, *, error_code: str = 'validation_error', status_code: int = 400):
        super().__init__(message)
        self.public_message = message
        self.error_code = error_code
        self.status_code = status_code


@dataclass(frozen=True)
class PlayNowOnboardingResult:
    payload: dict[str, Any]
    status_code: int


def ensure_play_now_adventure(
    *,
    workspace_id: str | None = None,
    account_id: int | None = None,
    character_id: str | None = None,
    example_pack_id: str | None = None,
) -> PlayNowOnboardingResult:
    target_workspace_id = workspace_id or DEFAULT_WORKSPACE_ID
    requested_pack_id = str(example_pack_id or DEFAULT_PLAY_NOW_EXAMPLE_PACK_ID).strip()
    if not requested_pack_id:
        requested_pack_id = DEFAULT_PLAY_NOW_EXAMPLE_PACK_ID

    preset = pregenerated_character_preset(character_id or default_pregenerated_character_id())
    if not preset:
        raise PlayNowOnboardingError(
            'Pregenerated character not found.',
            error_code='pregen_character_not_found',
            status_code=404,
        )

    ensure_local_workspace_context(target_workspace_id)

    session_obj = _find_existing_play_now_session(
        workspace_id=target_workspace_id,
        requested_pack_id=requested_pack_id,
    )
    created_session = session_obj is None
    imported_pack_id = None
    source_filename = None
    if session_obj is None:
        session_obj, imported_pack_id, source_filename = _import_play_now_campaign_pack(
            workspace_id=target_workspace_id,
            requested_pack_id=requested_pack_id,
            account_id=account_id,
        )

    def prepare_result() -> PlayNowOnboardingResult:
        campaign = session_obj.campaign
        if not campaign:
            raise PlayNowOnboardingError('Play Now campaign could not be loaded.', error_code='play_now_campaign_missing')

        resolved_pack_id = imported_pack_id or _imported_pack_id(session_obj) or requested_pack_id
        resolved_source_filename = source_filename or _play_now_metadata(session_obj).get('sourceFilename')
        player, created_player = _ensure_preset_player(
            workspace_id=target_workspace_id,
            campaign_id=campaign.campaign_id,
            account_id=account_id,
            preset=preset,
        )
        _sync_play_now_snapshot(
            session_obj=session_obj,
            campaign=campaign,
            selected_player=player,
            requested_pack_id=requested_pack_id,
            imported_pack_id=str(resolved_pack_id),
            source_filename=str(resolved_source_filename or ''),
        )

        created = created_session or created_player
        return PlayNowOnboardingResult(
            payload=_play_now_payload(
                workspace_id=target_workspace_id,
                campaign=campaign,
                session_obj=session_obj,
                player=player,
                preset_payload=pregenerated_character_payload(preset),
                requested_pack_id=requested_pack_id,
                imported_pack_id=str(resolved_pack_id),
                source_filename=str(resolved_source_filename or ''),
                idempotent_replay=not created,
            ),
            status_code=201 if created else 200,
        )

    if created_session:
        # The imported session is still private to this transaction. Extend its
        # initial snapshot with the selected starter hero and Play Now metadata,
        # then publish the complete onboarding transaction atomically.
        try:
            result = prepare_result()
            db.session.commit()
            return result
        except Exception:
            db.session.rollback()
            raise

    # Idempotent replay may target a live table. Reload after acquiring the same
    # coordinator used by turns and commit before releasing it so onboarding
    # repair cannot replace a concurrent turn snapshot.
    with session_turn_coordinator.serialized(session_obj.session_id):
        try:
            db.session.refresh(session_obj)
            result = prepare_result()
            db.session.commit()
            return result
        except Exception:
            db.session.rollback()
            raise


def ensure_local_workspace_context(workspace_id: str) -> Workspace:
    workspace = db.session.get(Workspace, workspace_id)
    if workspace:
        return workspace

    name = 'Local Table' if workspace_id == DEFAULT_WORKSPACE_ID else f'Local Table {workspace_id}'
    name_key = normalize_workspace_name_key(name)
    conflicting = Workspace.query.filter(Workspace.name_key == name_key, Workspace.workspace_id != workspace_id).first()
    if conflicting:
        name = f'Local Table {workspace_id}'
        name_key = normalize_workspace_name_key(name)

    workspace = Workspace(
        workspace_id=workspace_id,
        name=name,
        name_key=name_key,
    )
    db.session.add(workspace)
    db.session.flush()
    return workspace


def _find_existing_play_now_session(*, workspace_id: str, requested_pack_id: str) -> Session | None:
    sessions = (
        Session.query.join(Campaign, Campaign.campaign_id == Session.campaign_id)
        .filter(Campaign.workspace_id == workspace_id)
        .order_by(Session.created_at.asc(), Session.session_id.asc())
        .all()
    )
    for session_obj in sessions:
        if session_obj.status == 'archived':
            continue
        campaign = session_obj.campaign
        if campaign and campaign.status == 'archived':
            continue
        metadata = _play_now_metadata(session_obj)
        if metadata.get('source') == 'play_now' and metadata.get('examplePackId') == requested_pack_id:
            return session_obj
    return None


def _import_play_now_campaign_pack(
    *,
    workspace_id: str,
    requested_pack_id: str,
    account_id: int | None,
) -> tuple[Session, str, str]:
    example_pack = get_example_campaign_pack(requested_pack_id)
    if not example_pack:
        raise PlayNowOnboardingError(
            'Example campaign pack not found.',
            error_code='example_campaign_pack_not_found',
            status_code=404,
        )

    try:
        result = import_campaign_pack(
            {
                'pack': example_pack['manifest'],
                'sourceFilename': example_pack.get('source_filename'),
                'sessionName': 'Play Now',
            },
            workspace_id=workspace_id,
            dry_run=False,
            imported_by_account_id=account_id,
        )
    except CampaignPackImportError:
        raise

    session_id = result.payload.get('session_id')
    session_obj = db.session.get(Session, session_id)
    if not session_obj:
        raise PlayNowOnboardingError('Play Now session could not be created.', error_code='play_now_session_missing')
    imported_pack_id = str(result.payload.get('pack_id') or requested_pack_id)
    source_filename = str(example_pack.get('source_filename') or '')
    return session_obj, imported_pack_id, source_filename


def _ensure_preset_player(
    *,
    workspace_id: str,
    campaign_id: int,
    account_id: int | None,
    preset,
) -> tuple[Player, bool]:
    players = (
        Player.query.filter_by(workspace_id=workspace_id, campaign_id=campaign_id)
        .order_by(Player.created_at.asc(), Player.player_id.asc())
        .all()
    )
    for player in players:
        if player_matches_preset(player, preset):
            return player, False

    fallback = next((player for player in players if player.character_name == preset.character_name), None)
    if fallback:
        return fallback, False

    player = build_player_from_preset(
        preset,
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        account_id=account_id,
    )
    db.session.add(player)
    db.session.flush()
    return player, True


def _sync_play_now_snapshot(
    *,
    session_obj: Session,
    campaign: Campaign,
    selected_player: Player,
    requested_pack_id: str,
    imported_pack_id: str,
    source_filename: str,
) -> None:
    players = (
        Player.query.filter_by(workspace_id=campaign.workspace_id, campaign_id=campaign.campaign_id)
        .order_by(Player.created_at.asc(), Player.player_id.asc())
        .all()
    )
    snapshot = state_snapshot_for_session(
        session_obj=session_obj,
        campaign=campaign,
        players=players,
        active_player_ids=[selected_player.player_id],
    )
    _apply_play_now_metadata(
        snapshot,
        requested_pack_id=requested_pack_id,
        imported_pack_id=imported_pack_id,
        source_filename=source_filename,
    )
    session_obj.state_snapshot = safe_json_dumps(snapshot, {})
    session_obj.client_session_id = _play_now_client_session_id(imported_pack_id)
    session_obj.updated_at = utc_now()

    session_state = SessionState.query.filter_by(session_id=session_obj.session_id).first()
    if session_state:
        session_state.current_location = session_state.current_location or campaign.location
        session_state.current_quest = session_state.current_quest or campaign.current_quest
        if not session_state.rolling_summary:
            session_state.rolling_summary = 'Play Now is ready. Begin from the opening scene.'
        session_state.updated_at = utc_now()


def _apply_play_now_metadata(
    snapshot: dict[str, Any],
    *,
    requested_pack_id: str,
    imported_pack_id: str,
    source_filename: str,
) -> None:
    now = utc_now().isoformat()
    existing = snapshot.get('playNow') if isinstance(snapshot.get('playNow'), dict) else {}
    snapshot['playNow'] = {
        **existing,
        'source': 'play_now',
        'version': PLAY_NOW_VERSION,
        'examplePackId': requested_pack_id,
        'importedPackId': imported_pack_id,
        'sourceFilename': source_filename or None,
        'updatedAt': now,
    }
    snapshot['client_session_id'] = _play_now_client_session_id(imported_pack_id)
    flags = snapshot.get('flags') if isinstance(snapshot.get('flags'), dict) else {}
    flags['playNow'] = True
    flags['playNowExamplePackId'] = requested_pack_id
    snapshot['flags'] = flags


def _play_now_payload(
    *,
    workspace_id: str,
    campaign: Campaign,
    session_obj: Session,
    player: Player,
    preset_payload: dict[str, Any],
    requested_pack_id: str,
    imported_pack_id: str,
    source_filename: str,
    idempotent_replay: bool,
) -> dict[str, Any]:
    join_payload = {
        'workspace_id': workspace_id,
        'session_id': session_obj.session_id,
        'player_id': player.player_id,
    }
    send_message_payload = {
        'workspace_id': workspace_id,
        'session_id': session_obj.session_id,
        'campaign_id': campaign.campaign_id,
        'world_id': campaign.world_id,
        'player_id': player.player_id,
    }
    return {
        'mode': 'play_now',
        'workspace_id': workspace_id,
        'campaign_id': campaign.campaign_id,
        'session_id': session_obj.session_id,
        'player_id': player.player_id,
        'world_id': campaign.world_id,
        'idempotent_replay': idempotent_replay,
        'campaign': campaign_payload(campaign),
        'session': session_payload(
            session_obj,
            include_hidden_state=False,
            viewer_account_id=player.account_id,
            private_player_ids={player.player_id},
        ),
        'player': player_detail_payload(player),
        'pregen': preset_payload,
        'example_pack': {
            'example_pack_id': requested_pack_id,
            'pack_id': imported_pack_id,
            'source_filename': source_filename or None,
            'source': 'bundled_example',
        },
        'join_context': {
            'workspace_id': workspace_id,
            'campaign_id': campaign.campaign_id,
            'session_id': session_obj.session_id,
            'player_id': player.player_id,
            'world_id': campaign.world_id,
            'socket': {
                'event': 'join_session',
                'payload': join_payload,
            },
            'send_message': {
                'event': 'send_message',
                'payload': send_message_payload,
            },
        },
    }


def _play_now_metadata(session_obj: Session) -> dict[str, Any]:
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    if not isinstance(snapshot, dict):
        return {}
    metadata = snapshot.get('playNow')
    return metadata if isinstance(metadata, dict) else {}


def _imported_pack_id(session_obj: Session) -> str | None:
    metadata = _play_now_metadata(session_obj)
    if metadata.get('importedPackId'):
        return str(metadata.get('importedPackId'))
    snapshot = safe_json_loads(session_obj.state_snapshot, {})
    if isinstance(snapshot, dict):
        campaign_pack = snapshot.get('campaignPack')
        if isinstance(campaign_pack, dict) and campaign_pack.get('packId'):
            return str(campaign_pack.get('packId'))
    return None


def _play_now_client_session_id(imported_pack_id: str) -> str:
    suffix = ''.join(
        character if character.isalnum() or character in {'_', '-'} else '_'
        for character in imported_pack_id
    )
    suffix = suffix.strip('_-') or 'pack'
    return f'play-now-{suffix}'[:80]
