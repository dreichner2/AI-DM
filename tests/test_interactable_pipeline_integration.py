from __future__ import annotations

from copy import deepcopy

from aidm_server.action_intent import validate_action_intent
from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.extraction.pre_dm_action_extractor import (
    extract_pre_dm_actions,
)
from aidm_server.game_state.validation.validator import (
    validate_declared_actions,
    validate_state_changes,
    validated_changes_for_application,
)
from aidm_server.models import safe_json_dumps, safe_json_loads
from aidm_server.services.campaign_pack_visibility import (
    filter_session_snapshot_for_player,
)


def _state() -> dict:
    interactables = [
        {
            "id": "moon_shrine",
            "name": "Moon Shrine",
            "kind": "object",
            "description": "A pale stone shrine waits for an offering.",
            "usable": True,
            "depletable": True,
            "usesRemaining": 1,
            "revision": 0,
            "requirements": {"use": {"flags": {"moon_visible": True}}},
            "mechanicalEffects": {"use": {"kind": "beacon_lit"}},
            "gmNotes": "The light warns the marsh witch.",
        },
        {
            "id": "secret_switch",
            "name": "Secret Switch",
            "kind": "object",
            "usable": True,
            "hidden": True,
            "playerKnown": False,
            "gmOnly": False,
            "revision": 0,
            "gmNotes": "Opens the reliquary.",
        },
    ]
    hazards = [
        {
            "id": "moon_ward",
            "name": "Moon Ward",
            "kind": "hazard",
            "hidden": True,
            "playerKnown": False,
            "active": True,
            "triggered": False,
            "disarmed": False,
            "revision": 0,
            "gmNotes": "Triggers if the shrine is defaced.",
        }
    ]
    return {
        "schemaVersion": 1,
        "sessionId": 71,
        "campaignId": 19,
        "activePlayerIds": [1],
        "playerCharacters": [
            {
                "id": "player_1",
                "playerId": 1,
                "name": "Aria",
                "class": "Wizard",
                "level": 2,
                "inventory": {"items": [], "currency": {"gp": 0}},
                "health": {"currentHp": 12, "maxHp": 12, "tempHp": 0, "conditions": []},
                "xp": {"current": 0, "nextLevelAt": 300},
                "classFeatures": [],
            }
        ],
        "currentScene": {
            "locationId": "moon_crypt",
            "name": "Moon Crypt",
            "sceneType": "exploration",
            "combatState": "none",
            "activeNpcIds": [],
            "activeQuestIds": ["quest_light_shrine"],
            "items": [],
            "interactables": deepcopy(interactables),
            "hazards": deepcopy(hazards),
        },
        "locations": [
            {
                "id": "moon_crypt",
                "name": "Moon Crypt",
                "status": "visited",
                "sceneState": {
                    "interactables": deepcopy(interactables),
                    "hazards": deepcopy(hazards),
                },
            }
        ],
        "quests": [
            {
                "id": "quest_light_shrine",
                "title": "Light the Moon Shrine",
                "status": "active",
                "completionPolicy": "all",
                "rewards": {"gp": 3},
                "onComplete": [
                    {
                        "type": "flag.set",
                        "flagKey": "moon_beacon_lit",
                        "flagValue": True,
                    }
                ],
                "objectives": [
                    {
                        "id": "use_moon_shrine",
                        "description": "Use the shrine while the moon is visible.",
                        "status": "open",
                        "completeWhen": {
                            "eventType": "interactable.used",
                            "actorId": "player_1",
                            "targetId": "moon_shrine",
                            "locationId": "moon_crypt",
                        },
                    }
                ],
            }
        ],
        "flags": {"moon_visible": True},
        "stateChangeLedger": [],
    }


def _typed_intent(*, revision: int, client_message_id: str) -> dict:
    raw = {
        "kind": "object",
        "text": "I place my hand on the moon shrine and awaken it.",
        "source": "scene_panel",
        "client_message_id": client_message_id,
        "object": {"id": "moon_shrine", "action": "use", "revision": revision},
    }
    normalized, error = validate_action_intent(raw)
    assert error is None
    assert normalized is not None
    return normalized


def _extract_and_validate(
    state: dict, *, revision: int, current_turn: int
) -> tuple[dict, dict, dict]:
    intent = _typed_intent(
        revision=revision,
        client_message_id=f"use-shrine-{current_turn}",
    )
    extraction = extract_pre_dm_actions(
        current_state=state,
        player_message=intent["text"],
        recent_timeline=[],
        actor_id="player_1",
        action_intent=intent,
    )
    declaration_validation = validate_declared_actions(
        state=state,
        declared_actions=extraction["declaredActions"],
        current_turn=current_turn,
        expected_actor_id="player_1",
    )
    change_validation = validate_state_changes(
        state=state,
        changes=declaration_validation["immediateChanges"],
        expected_actor_id="player_1",
    )
    return extraction, declaration_validation, change_validation


def _scene_object(state: dict, object_id: str) -> dict:
    return next(
        value
        for collection in ("interactables", "hazards")
        for value in state["currentScene"][collection]
        if value["id"] == object_id
    )


def _persisted_object(state: dict, object_id: str) -> dict:
    scene_state = state["locations"][0]["sceneState"]
    return next(
        value
        for collection in ("interactables", "hazards")
        for value in scene_state[collection]
        if value["id"] == object_id
    )


def test_typed_object_action_crosses_parse_extract_validate_apply_and_quest_pipeline() -> (
    None
):
    state = _state()
    original = deepcopy(state)

    extraction, declaration_validation, change_validation = _extract_and_validate(
        state,
        revision=0,
        current_turn=11,
    )

    assert state == original
    assert extraction["debug"]["source"] == "action_intent"
    assert extraction["declaredActions"] == [
        {
            "id": "act_001",
            "type": "scene.interactable.action",
            "actorId": "player_1",
            "confidence": 1.0,
            "sourceText": "I place my hand on the moon shrine and awaken it.",
            "requiresDMResolution": False,
            "targetId": "moon_shrine",
            "objectAction": "use",
            "expectedRevision": 0,
        }
    ]
    validated_action = declaration_validation["validatedActions"][0]
    assert validated_action["status"] == "valid"
    assert declaration_validation["pendingRolls"] == []
    assert len(declaration_validation["immediateChanges"]) == 1
    assert change_validation["rejected"] == []
    assert len(change_validation["accepted"]) == 1

    applied = apply_state_changes(
        state,
        validated_changes_for_application(change_validation),
    )
    next_state = applied["nextState"]

    active_shrine = _scene_object(next_state, "moon_shrine")
    persisted_shrine = _persisted_object(next_state, "moon_shrine")
    assert active_shrine["used"] is True
    assert active_shrine["usedCount"] == 1
    assert active_shrine["usesRemaining"] == 0
    assert active_shrine["depleted"] is True
    assert active_shrine["revision"] == 1
    assert persisted_shrine == active_shrine

    event = next(
        entry
        for entry in next_state["gameplayEventLedger"]
        if entry["type"] == "interactable.used"
    )
    assert event["actorId"] == "player_1"
    assert event["targetId"] == "moon_shrine"
    assert event["locationId"] == "moon_crypt"

    quest = next_state["quests"][0]
    assert quest["status"] == "completed"
    assert quest["objectives"][0]["status"] == "completed"
    assert next_state["playerCharacters"][0]["inventory"]["currency"]["gp"] == 3
    assert next_state["flags"]["moon_beacon_lit"] is True
    assert "quest_light_shrine" not in next_state["currentScene"]["activeQuestIds"]

    restored = safe_json_loads(safe_json_dumps(next_state, {}), {})
    assert _scene_object(restored, "moon_shrine") == active_shrine
    assert _persisted_object(restored, "moon_shrine") == active_shrine
    assert restored["quests"][0]["status"] == "completed"
    assert restored["playerCharacters"][0]["inventory"]["currency"]["gp"] == 3


def test_object_revision_and_replayed_change_are_exactly_once() -> None:
    state = _state()
    _, _, initial_validation = _extract_and_validate(state, revision=0, current_turn=11)
    accepted_change = validated_changes_for_application(initial_validation)[0]
    first = apply_state_changes(state, [accepted_change])
    first_state = first["nextState"]

    replay = apply_state_changes(first_state, [accepted_change])
    replay_state = replay["nextState"]

    assert replay["appliedChanges"] == []
    assert replay["skippedChanges"][0]["reason"] == "State change was already applied."
    assert _scene_object(replay_state, "moon_shrine")["usedCount"] == 1
    assert _scene_object(replay_state, "moon_shrine")["usesRemaining"] == 0
    assert replay_state["playerCharacters"][0]["inventory"]["currency"]["gp"] == 3
    assert (
        sum(
            1
            for entry in replay_state["gameplayEventLedger"]
            if entry["type"] == "interactable.used"
        )
        == 1
    )
    assert len({entry["id"] for entry in replay_state["stateChangeLedger"]}) == len(
        replay_state["stateChangeLedger"]
    )

    _, stale_declaration, stale_change_validation = _extract_and_validate(
        first_state,
        revision=0,
        current_turn=12,
    )
    assert stale_declaration["validatedActions"][0]["status"] == "invalid"
    assert (
        "changed after the player selected it"
        in stale_declaration["validatedActions"][0]["reason"]
    )
    assert stale_declaration["immediateChanges"] == []
    assert stale_change_validation["accepted"] == []


def test_state_change_validation_rejects_tampered_interactable_event() -> None:
    state = _state()
    _, declaration, _ = _extract_and_validate(state, revision=0, current_turn=11)
    tampered = deepcopy(declaration["immediateChanges"][0])
    tampered["event"]["targetId"] = "secret_switch"

    validation = validate_state_changes(
        state=state,
        changes=[tampered],
        expected_actor_id="player_1",
    )

    assert validation["accepted"] == []
    assert validation["rejected"][0]["reason"] == (
        "Scene-object event does not match the authoritative transition."
    )


def test_player_snapshot_projection_hides_unknown_objects_hazards_and_gm_fields() -> (
    None
):
    projected = filter_session_snapshot_for_player(
        _state(),
        private_player_ids={1},
    )

    scene = projected["currentScene"]
    assert [entry["id"] for entry in scene["interactables"]] == ["moon_shrine"]
    assert scene["hazards"] == []
    shrine = scene["interactables"][0]
    assert "requirements" not in shrine
    assert "mechanicalEffects" not in shrine
    assert "gmNotes" not in shrine
    assert "secret_switch" not in str(projected)
    assert "moon_ward" not in str(projected)
