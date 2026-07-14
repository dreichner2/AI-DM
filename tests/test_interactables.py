from __future__ import annotations

from copy import deepcopy

import pytest

from aidm_server.interactables import (
    available_actions_for_target,
    project_interactable_event,
    project_scene_interactables,
    resolve_interactable_action,
    validate_interactable_catalog,
)


def _interactables() -> list[dict]:
    return [
        {
            "id": "gate",
            "name": "Iron Gate",
            "kind": "door",
            "open": False,
            "locked": True,
            "breakable": True,
            "requirements": {
                "unlock": {
                    "anyOf": [
                        {"allItemIds": ["key_gate"]},
                        {
                            "allToolIds": ["tool_lockpicks"],
                            "check": {
                                "id": "pick_gate",
                                "dc": 15,
                                "skill": "sleight_of_hand",
                            },
                        },
                    ]
                }
            },
            "revision": 0,
        },
        {
            "id": "side_door",
            "name": "Side Door",
            "kind": "door",
            "open": False,
            "locked": True,
            "breakable": True,
            "revision": 0,
        },
        {
            "id": "side_lock",
            "name": "Side Door Lock",
            "kind": "lock",
            "locked": True,
            "breakable": True,
            "controlsTargetId": "side_door",
            "requirements": {"unlock": {"allItemIds": ["key_side"]}},
            "revision": 0,
        },
        {
            "id": "chest",
            "name": "Carved Chest",
            "kind": "container",
            "open": False,
            "locked": False,
            "searchable": True,
            "contents": [
                {"id": "rope", "name": "Silk Rope", "quantity": 1, "type": "gear"},
                {
                    "id": "hidden_gem",
                    "name": "Moon Gem",
                    "quantity": 1,
                    "type": "treasure",
                    "hidden": True,
                },
                {"id": "gm_note", "name": "False Bottom Notes", "gmOnly": True},
            ],
            "reveals": {"search": ["spike_trap"]},
            "revealFields": {"search": ["makerMark"]},
            "makerMark": "Guild of Ash",
            "secrets": {"falseBottom": True},
            "gmNotes": "The gem is cursed.",
            "revision": 0,
        },
        {
            "id": "lever",
            "name": "Bronze Lever",
            "kind": "object",
            "usable": True,
            "depletable": True,
            "usesRemaining": 2,
            "transitions": {
                "use": [
                    {"targetId": "side_door", "set": {"locked": False, "open": True}}
                ]
            },
            "revision": 0,
        },
        {
            "id": "shrine",
            "name": "Wayside Shrine",
            "kind": "object",
            "usable": True,
            "mechanicalEffects": {"use": {"kind": "blessing", "uses": 1}},
            "revision": 0,
        },
        {
            "id": "seal",
            "name": "Rune Seal",
            "kind": "object",
            "usable": True,
            "requirements": {
                "use": {
                    "allCapabilities": ["read_runes"],
                    "flags": {"moon_aligned": True},
                    "objectStates": [
                        {"targetId": "lever", "field": "used", "equals": True}
                    ],
                }
            },
            "revision": 0,
        },
    ]


def _hazards() -> list[dict]:
    return [
        {
            "id": "spike_trap",
            "name": "Floor Spikes",
            "kind": "hazard",
            "hidden": True,
            "playerKnown": False,
            "active": True,
            "triggered": False,
            "disarmed": False,
            "disarmable": True,
            "resettable": True,
            "oneShot": True,
            "requirements": {
                "disarm": {
                    "allToolIds": ["tool_thieves"],
                    "check": {"id": "disarm_spikes", "dc": 13, "ability": "dexterity"},
                }
            },
            "mechanicalEffects": {
                "trigger": {"damage": {"dice": "1d6", "type": "piercing"}}
            },
            "gmNotes": "Targets everyone in the front rank.",
            "revision": 0,
        }
    ]


def _state() -> dict:
    interactables = _interactables()
    hazards = _hazards()
    return {
        "currentScene": {
            "locationId": "old_keep",
            "name": "Old Keep",
            "interactables": deepcopy(interactables),
            "hazards": deepcopy(hazards),
        },
        "locations": [
            {
                "id": "old_keep",
                "name": "Old Keep",
                "sceneState": {
                    "interactables": deepcopy(interactables),
                    "hazards": deepcopy(hazards),
                },
            },
            {
                "id": "village",
                "name": "Village",
                "sceneState": {"interactables": [], "hazards": []},
            },
        ],
    }


def _actor(**overrides) -> dict:
    actor = {
        "id": "player_1",
        "itemIds": [],
        "toolIds": [],
        "capabilities": [],
        "flags": {},
    }
    actor.update(overrides)
    return actor


def _request(
    action: str, target_id: str, *, action_id: str | None = None, **overrides
) -> dict:
    request = {
        "actionId": action_id or f"action_{action}_{target_id}",
        "action": action,
        "targetId": target_id,
        "locationId": "old_keep",
        "turnId": 7,
    }
    request.update(overrides)
    return request


def _target(state: dict, target_id: str) -> dict:
    scene = state["currentScene"]
    return next(
        item
        for collection in ("interactables", "hazards")
        for item in scene[collection]
        if item["id"] == target_id
    )


def _persisted_target(state: dict, target_id: str) -> dict:
    scene_state = state["locations"][0]["sceneState"]
    return next(
        item
        for collection in ("interactables", "hazards")
        for item in scene_state[collection]
        if item["id"] == target_id
    )


def test_unlock_with_exact_key_is_pure_and_persists_to_location_scene_state():
    state = _state()
    original = deepcopy(state)

    result = resolve_interactable_action(
        state,
        _request("unlock", "gate", expectedRevision=0),
        _actor(items=[{"id": "key_gate", "name": "Iron Key"}]),
    )

    assert result["ok"] is True
    assert result["code"] == "applied"
    assert state == original
    assert _target(result["nextState"], "gate")["locked"] is False
    assert _persisted_target(result["nextState"], "gate")["locked"] is False
    assert _target(result["nextState"], "gate")["revision"] == 1
    assert result["events"][0]["type"] == "interactable.unlocked"
    assert result["events"][0]["targetId"] == "gate"
    assert any(
        change["type"] == "interactable.action.recorded" for change in result["changes"]
    )


def test_locked_door_cannot_open_and_rejection_does_not_mutate_state():
    state = _state()
    result = resolve_interactable_action(state, _request("open", "gate"), _actor())

    assert result["ok"] is False
    assert result["code"] == "invalid_transition"
    assert result["changes"] == []
    assert result["events"] == []
    assert result["nextState"] == state


def test_lockpick_alternative_requires_matching_authoritative_check():
    state = _state()
    actor = _actor(toolIds=["tool_lockpicks"])

    required = resolve_interactable_action(state, _request("unlock", "gate"), actor)
    assert required["code"] == "check_required"
    assert required["details"]["check"]["id"] == "pick_gate"

    mismatch = resolve_interactable_action(
        state,
        _request("unlock", "gate"),
        _actor(
            toolIds=["tool_lockpicks"],
            resolvedChecks={
                "pick_gate": {"passed": True, "dc": 14, "skill": "sleight_of_hand"}
            },
        ),
    )
    assert mismatch["code"] == "authoritative_check_mismatch"

    failed = resolve_interactable_action(
        state,
        _request("unlock", "gate"),
        _actor(
            toolIds=["tool_lockpicks"],
            resolvedChecks={
                "pick_gate": {"passed": False, "dc": 15, "skill": "sleight_of_hand"}
            },
        ),
    )
    assert failed["code"] == "check_failed"

    passed = resolve_interactable_action(
        state,
        _request("unlock", "gate"),
        _actor(
            toolIds=["tool_lockpicks"],
            resolvedChecks={
                "pick_gate": {"passed": True, "dc": 15, "skill": "sleight_of_hand"}
            },
        ),
    )
    assert passed["ok"] is True
    assert _target(passed["nextState"], "gate")["locked"] is False


def test_separate_lock_updates_controlled_door_atomically():
    result = resolve_interactable_action(
        _state(),
        _request("unlock", "side_lock"),
        _actor(itemIds=["key_side"]),
    )

    assert result["ok"] is True
    assert _target(result["nextState"], "side_lock")["locked"] is False
    assert _target(result["nextState"], "side_door")["locked"] is False
    updated_ids = {change.get("targetId") for change in result["changes"]}
    assert {"side_lock", "side_door"} <= updated_ids


def test_unlocked_door_can_open_and_close_then_separate_lock_can_relock_it():
    unlocked = resolve_interactable_action(
        _state(),
        _request("unlock", "side_lock", action_id="unlock_side"),
        _actor(itemIds=["key_side"]),
    )
    opened = resolve_interactable_action(
        unlocked["nextState"],
        _request("open", "side_door", action_id="open_side"),
        _actor(),
    )
    assert _target(opened["nextState"], "side_door")["open"] is True

    closed = resolve_interactable_action(
        opened["nextState"],
        _request("close", "side_door", action_id="close_side"),
        _actor(),
    )
    relocked = resolve_interactable_action(
        closed["nextState"],
        _request("lock", "side_lock", action_id="relock_side"),
        _actor(),
    )
    assert relocked["ok"] is True
    assert _target(relocked["nextState"], "side_lock")["locked"] is True
    assert _target(relocked["nextState"], "side_door")["locked"] is True


def test_lock_cannot_make_controlled_open_door_locked_and_action_is_atomic():
    state = _state()
    _target(state, "side_lock")["locked"] = False
    _target(state, "side_door").update({"locked": False, "open": True})
    _persisted_target(state, "side_lock")["locked"] = False
    _persisted_target(state, "side_door").update({"locked": False, "open": True})

    result = resolve_interactable_action(state, _request("lock", "side_lock"), _actor())

    assert result["code"] == "invalid_authored_transition"
    assert result["nextState"] == state
    assert _target(state, "side_lock")["locked"] is False


def test_container_open_and_search_reveal_contents_in_stages():
    opened = resolve_interactable_action(_state(), _request("open", "chest"), _actor())
    assert opened["ok"] is True
    opened_projection = project_scene_interactables(opened["nextState"], _actor())
    chest = next(
        item for item in opened_projection["interactables"] if item["id"] == "chest"
    )
    assert [item["id"] for item in chest["contents"]] == ["rope"]
    assert "makerMark" not in chest
    assert opened_projection["hazards"] == []

    searched = resolve_interactable_action(
        opened["nextState"],
        _request("search", "chest", action_id="action_search_chest"),
        _actor(),
    )
    assert searched["ok"] is True
    projection = project_scene_interactables(searched["nextState"], _actor())
    chest = next(item for item in projection["interactables"] if item["id"] == "chest")
    assert [item["id"] for item in chest["contents"]] == ["rope", "hidden_gem"]
    assert chest["makerMark"] == "Guild of Ash"
    assert "secrets" not in chest
    assert "gmNotes" not in chest
    assert [hazard["id"] for hazard in projection["hazards"]] == ["spike_trap"]

    repeated = resolve_interactable_action(
        searched["nextState"],
        _request("search", "chest", action_id="action_search_chest_again"),
        _actor(),
    )
    assert repeated["code"] == "invalid_transition"


def test_searching_closed_container_fails():
    result = resolve_interactable_action(
        _state(), _request("search", "chest"), _actor()
    )
    assert result["code"] == "invalid_transition"
    assert "opened" in result["message"]


def test_breaking_door_opens_and_unlocks_it_but_broken_door_cannot_close():
    broken = resolve_interactable_action(_state(), _request("break", "gate"), _actor())
    gate = _target(broken["nextState"], "gate")
    assert gate["broken"] is True
    assert gate["open"] is True
    assert gate["locked"] is False
    assert broken["events"][0]["type"] == "interactable.broken"

    close = resolve_interactable_action(
        broken["nextState"],
        _request("close", "gate", action_id="close_broken_gate"),
        _actor(),
    )
    assert close["code"] == "invalid_transition"


def test_depletable_use_applies_exact_secondary_transition_and_exhausts():
    first = resolve_interactable_action(
        _state(), _request("use", "lever", action_id="lever_1"), _actor()
    )
    assert first["ok"] is True
    assert _target(first["nextState"], "lever")["usesRemaining"] == 1
    assert _target(first["nextState"], "side_door")["open"] is True
    assert _target(first["nextState"], "side_door")["locked"] is False

    second = resolve_interactable_action(
        first["nextState"],
        _request("use", "lever", action_id="lever_2"),
        _actor(),
    )
    lever = _target(second["nextState"], "lever")
    assert lever["usesRemaining"] == 0
    assert lever["depleted"] is True
    assert lever["usedCount"] == 2

    exhausted = resolve_interactable_action(
        second["nextState"],
        _request("use", "lever", action_id="lever_3"),
        _actor(),
    )
    assert exhausted["code"] == "resource_exhausted"


def test_use_emits_trusted_mechanical_effect_without_projecting_it():
    result = resolve_interactable_action(_state(), _request("use", "shrine"), _actor())
    assert result["events"][0]["mechanicalEffects"] == {"kind": "blessing", "uses": 1}

    projected = project_scene_interactables(result["nextState"], _actor())
    shrine = next(item for item in projected["interactables"] if item["id"] == "shrine")
    assert "mechanicalEffects" not in shrine


def test_internal_event_projection_enforces_visibility_and_strips_mechanical_payload():
    result = resolve_interactable_action(_state(), _request("use", "shrine"), _actor())
    event = result["events"][0]

    player_event = project_interactable_event(event, _actor())
    assert player_event is not None
    assert "mechanicalEffects" not in player_event
    assert player_event["targetId"] == "shrine"

    gm_event = project_interactable_event(event, {"id": "gm", "isGm": True})
    assert gm_event["mechanicalEffects"] == {"kind": "blessing", "uses": 1}

    gm_only_event = {**event, "visibility": "gm"}
    assert project_interactable_event(gm_only_event, _actor()) is None
    assert (
        project_interactable_event(gm_only_event, {"id": "gm", "isGm": True})
        is not None
    )

    actor_only_event = {**event, "visibility": "actor"}
    assert project_interactable_event(actor_only_event, {"id": "player_2"}) is None
    assert project_interactable_event(actor_only_event, _actor()) is not None


def test_capability_flag_and_object_state_prerequisites_all_must_hold():
    state = _state()
    missing = resolve_interactable_action(
        state,
        _request("use", "seal"),
        _actor(capabilities=["read_runes"], flags={"moon_aligned": True}),
    )
    assert missing["code"] == "prerequisite_missing"
    assert missing["details"]["targetId"] == "lever"

    _target(state, "lever")["used"] = True
    _persisted_target(state, "lever")["used"] = True
    passed = resolve_interactable_action(
        state,
        _request("use", "seal"),
        _actor(capabilities=["read_runes"], flags={"moon_aligned": True}),
    )
    assert passed["ok"] is True


def test_hidden_and_stale_targets_share_fail_closed_error():
    hidden = resolve_interactable_action(
        _state(), _request("trigger", "spike_trap"), _actor()
    )
    stale = resolve_interactable_action(
        _state(), _request("trigger", "does_not_exist"), _actor()
    )

    assert hidden["code"] == stale["code"] == "interactable_not_found"
    assert hidden["message"] == stale["message"]


def test_revealed_hazard_requires_tools_and_authoritative_check_to_disarm():
    state = _state()
    hazard = _target(state, "spike_trap")
    hazard.update({"hidden": False, "playerKnown": True})
    _persisted_target(state, "spike_trap").update(
        {"hidden": False, "playerKnown": True}
    )

    missing = resolve_interactable_action(
        state, _request("disarm", "spike_trap"), _actor()
    )
    assert missing["code"] == "prerequisite_missing"

    passed = resolve_interactable_action(
        state,
        _request("disarm", "spike_trap"),
        _actor(
            toolIds=["tool_thieves"],
            resolvedChecks={
                "disarm_spikes": {"passed": True, "dc": 13, "ability": "dexterity"}
            },
        ),
    )
    hazard = _target(passed["nextState"], "spike_trap")
    assert hazard["active"] is False
    assert hazard["disarmed"] is True
    assert passed["events"][0]["type"] == "hazard.disarmed"


def test_one_shot_hazard_trigger_and_reset_lifecycle():
    actor = _actor(canTargetHidden=True)
    triggered = resolve_interactable_action(
        _state(), _request("trigger", "spike_trap"), actor
    )
    hazard = _target(triggered["nextState"], "spike_trap")
    assert hazard["triggered"] is True
    assert hazard["active"] is False
    assert hazard["depleted"] is True
    assert triggered["events"][0]["mechanicalEffects"]["damage"]["dice"] == "1d6"

    reset = resolve_interactable_action(
        triggered["nextState"],
        _request("reset", "spike_trap", action_id="reset_spikes"),
        actor,
    )
    hazard = _target(reset["nextState"], "spike_trap")
    assert hazard["triggered"] is False
    assert hazard["active"] is True
    assert hazard["depleted"] is False
    assert reset["events"][0]["type"] == "hazard.reset"


def test_action_id_replay_is_exactly_once_and_conflicts_fail_closed():
    request = _request("use", "shrine", action_id="stable_action")
    first = resolve_interactable_action(_state(), request, _actor())
    replay = resolve_interactable_action(first["nextState"], request, _actor())

    assert replay["ok"] is True
    assert replay["code"] == "already_applied"
    assert replay["replayed"] is True
    assert replay["changes"] == []
    assert replay["events"] == []
    assert _target(replay["nextState"], "shrine")["usedCount"] == 1
    assert replay["details"]["eventIds"] == [first["events"][0]["id"]]

    conflict = resolve_interactable_action(
        first["nextState"],
        _request("inspect", "shrine", action_id="stable_action"),
        _actor(),
    )
    assert conflict["code"] == "action_id_conflict"


def test_deterministic_event_and_change_ids():
    state = _state()
    request = _request("use", "shrine", action_id="deterministic")
    first = resolve_interactable_action(state, request, _actor())
    second = resolve_interactable_action(
        deepcopy(state), deepcopy(request), deepcopy(_actor())
    )

    assert first["events"] == second["events"]
    assert [change["id"] for change in first["changes"]] == [
        change["id"] for change in second["changes"]
    ]


def test_expected_revision_rejects_stale_selection():
    result = resolve_interactable_action(
        _state(),
        _request("use", "shrine", expectedRevision=4),
        _actor(),
    )
    assert result["code"] == "stale_interactable_revision"
    assert result["details"] == {"expectedRevision": 4, "currentRevision": 0}


@pytest.mark.parametrize(
    ("request_patch", "code"),
    [
        ({"targetId": "missing"}, "interactable_not_found"),
        ({"locationId": "village"}, "location_mismatch"),
        ({"actionId": ""}, "invalid_request"),
        ({"expectedRevision": True}, "invalid_request"),
    ],
)
def test_invalid_or_stale_request_fails_closed(request_patch, code):
    request = _request("inspect", "shrine")
    request.update(request_patch)
    result = resolve_interactable_action(_state(), request, _actor())
    assert result["code"] == code
    assert result["changes"] == []


def test_duplicate_id_in_current_scene_is_rejected():
    state = _state()
    state["currentScene"]["interactables"].append(deepcopy(_target(state, "gate")))

    result = resolve_interactable_action(state, _request("break", "gate"), _actor())
    assert result["code"] == "duplicate_interactable_id"


def test_duplicate_id_in_remote_location_is_rejected():
    state = _state()
    state["locations"][1]["sceneState"]["interactables"].append(
        {
            "id": "gate",
            "name": "Another Gate",
            "kind": "door",
            "open": False,
            "locked": False,
        }
    )

    result = resolve_interactable_action(state, _request("break", "gate"), _actor())
    assert result["code"] == "duplicate_interactable_id"


def test_duplicate_id_in_persisted_current_location_is_rejected_even_with_clean_scene_mirror():
    state = _state()
    state["locations"][0]["sceneState"]["interactables"].append(
        deepcopy(_persisted_target(state, "gate"))
    )

    result = resolve_interactable_action(state, _request("break", "gate"), _actor())
    assert result["code"] == "duplicate_interactable_id"


def test_missing_authored_secondary_target_rejects_without_partial_use():
    state = _state()
    _target(state, "lever")["transitions"]["use"][0]["targetId"] = "removed_door"
    _persisted_target(state, "lever")["transitions"]["use"][0]["targetId"] = (
        "removed_door"
    )

    result = resolve_interactable_action(state, _request("use", "lever"), _actor())
    assert result["code"] == "secondary_target_not_found"
    assert _target(result["nextState"], "lever")["usesRemaining"] == 2


def test_unsafe_authored_transition_field_is_rejected():
    state = _state()
    _target(state, "lever")["transitions"]["use"][0]["set"] = {"id": "renamed_door"}
    _persisted_target(state, "lever")["transitions"]["use"][0]["set"] = {
        "id": "renamed_door"
    }

    result = resolve_interactable_action(state, _request("use", "lever"), _actor())
    assert result["code"] == "malformed_interactable"


def test_available_actions_reports_transitions_and_checks_without_mutation():
    state = _state()
    original = deepcopy(state)
    result = available_actions_for_target(
        state,
        location_id="old_keep",
        target_id="gate",
        actor=_actor(toolIds=["tool_lockpicks"]),
    )

    assert result["ok"] is True
    by_action = {entry["action"]: entry for entry in result["actions"]}
    assert by_action["open"]["code"] == "invalid_transition"
    assert by_action["unlock"]["requiresCheck"] is True
    assert by_action["break"]["legal"] is True
    assert state == original


def test_available_actions_fails_closed_for_globally_duplicated_target_id():
    state = _state()
    state["locations"][1]["sceneState"]["interactables"].append(
        {"id": "gate", "kind": "door"}
    )
    result = available_actions_for_target(
        state, location_id="old_keep", target_id="gate", actor=_actor()
    )
    assert result == {"ok": False, "code": "interactable_not_found", "actions": []}


def test_player_projection_strips_gm_fields_requirements_and_hidden_status_fields():
    state = _state()
    chest = _target(state, "chest")
    chest["hiddenFields"] = ["locked"]
    projection = project_scene_interactables(state, _actor())
    projected = next(
        item for item in projection["interactables"] if item["id"] == "chest"
    )

    assert "locked" not in projected
    assert "requirements" not in projected
    assert "reveals" not in projected
    assert "gmNotes" not in projected
    assert "secrets" not in projected
    assert projection["hazards"] == []

    gm_projection = project_scene_interactables(state, {"id": "gm", "isGm": True})
    gm_chest = next(
        item for item in gm_projection["interactables"] if item["id"] == "chest"
    )
    assert gm_chest["gmNotes"] == "The gem is cursed."
    assert gm_projection["hazards"][0]["id"] == "spike_trap"


def test_projection_omits_ambiguous_ids_instead_of_guessing():
    state = _state()
    state["currentScene"]["interactables"].append(deepcopy(_target(state, "gate")))
    projection = project_scene_interactables(state, _actor())
    assert "gate" not in {entry["id"] for entry in projection["interactables"]}


def test_projection_omits_globally_duplicated_remote_identity():
    state = _state()
    state["locations"][1]["sceneState"]["interactables"].append(
        {"id": "gate", "kind": "door"}
    )
    projection = project_scene_interactables(state, _actor())
    assert "gate" not in {entry["id"] for entry in projection["interactables"]}


def test_scene_hydrates_missing_collections_from_exact_location_and_then_persists():
    state = _state()
    state["currentScene"].pop("interactables")
    state["currentScene"].pop("hazards")

    result = resolve_interactable_action(
        state,
        _request("unlock", "gate"),
        _actor(itemIds=["key_gate"]),
    )

    assert result["ok"] is True
    assert _target(result["nextState"], "gate")["locked"] is False
    assert _persisted_target(result["nextState"], "gate")["locked"] is False


def test_catalog_validation_accepts_coherent_fixture():
    assert validate_interactable_catalog(_state()) == []


def test_catalog_validation_reports_duplicates_contradictions_stale_references_and_bad_transitions():
    state = _state()
    persisted = state["locations"][0]["sceneState"]["interactables"]
    persisted.append({"id": "gate", "kind": "door", "open": True, "locked": True})
    _persisted_target(state, "lever")["transitions"]["use"][0]["set"] = {
        "name": "Unsafe"
    }
    _persisted_target(state, "side_lock")["controlsTargetId"] = "missing_door"
    state["locations"][1]["sceneState"]["interactables"].append(
        {"id": "shrine", "kind": "object"}
    )

    errors = validate_interactable_catalog(state)
    codes = {error["code"] for error in errors}
    assert "duplicate_interactable_id" in codes
    assert "contradictory_state" in codes
    assert "stale_secondary_target" in codes
    assert "malformed_transition" in codes


def test_catalog_validation_includes_active_scene_and_requirement_shape():
    state = _state()
    state["currentScene"]["interactables"].append(deepcopy(_target(state, "gate")))
    _target(state, "seal")["requirements"] = {"use": {"anyOf": []}}

    errors = validate_interactable_catalog(state)
    assert any(
        error["code"] == "duplicate_interactable_id"
        and error["path"].startswith("currentScene")
        for error in errors
    )
    assert any(
        error["code"] == "malformed_requirements"
        and error["path"].startswith("currentScene")
        for error in errors
    )


def test_string_boolean_state_is_rejected_instead_of_being_treated_as_false():
    state = _state()
    _target(state, "shrine")["used"] = "false"
    _persisted_target(state, "shrine")["used"] = "false"

    result = resolve_interactable_action(state, _request("use", "shrine"), _actor())
    assert result["code"] == "malformed_interactable"


def test_missing_or_ambiguous_persistent_location_fails_closed():
    missing = _state()
    missing["locations"] = [
        location for location in missing["locations"] if location["id"] != "old_keep"
    ]
    missing_result = resolve_interactable_action(
        missing, _request("use", "shrine"), _actor()
    )
    assert missing_result["code"] == "location_not_found"

    ambiguous = _state()
    ambiguous["locations"].append(deepcopy(ambiguous["locations"][0]))
    ambiguous_result = resolve_interactable_action(
        ambiguous, _request("use", "shrine"), _actor()
    )
    assert ambiguous_result["code"] == "duplicate_location_id"


@pytest.mark.parametrize(
    "malformed",
    [
        {"id": "bad", "kind": "object", "usable": True, "depletable": True},
        {"id": "bad", "kind": "object", "usable": True, "usesRemaining": -1},
        {"id": "bad", "kind": "object", "usable": True, "usedCount": "many"},
        {"id": "bad", "kind": "hazard", "active": True, "disarmed": True},
    ],
)
def test_malformed_resource_or_hazard_state_fails_closed(malformed):
    state = _state()
    state["currentScene"]["interactables"] = [malformed]
    state["currentScene"]["hazards"] = []
    state["locations"][0]["sceneState"] = {
        "interactables": [deepcopy(malformed)],
        "hazards": [],
    }
    action = "trigger" if malformed.get("kind") == "hazard" else "use"

    result = resolve_interactable_action(
        state,
        _request(action, "bad"),
        _actor(canTargetHidden=True),
    )
    assert result["code"] == "malformed_interactable"
