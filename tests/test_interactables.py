from __future__ import annotations

from copy import deepcopy

import pytest

from aidm_server.interactables import (
    available_actions_for_target,
    interactable_followup_changes,
    offered_scene_actions,
    project_interactable_event,
    project_scene_interactables,
    resolve_interactable_action,
    take_label,
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


def test_searching_a_closed_container_opens_it_first_in_the_same_action():
    result = resolve_interactable_action(
        _state(), _request("search", "chest"), _actor()
    )

    assert result["ok"] is True
    assert [
        (change["action"], change["targetId"])
        for change in result["changes"]
        if change["type"] == "scene.interactable.update"
    ] == [("open", "chest"), ("search", "chest"), ("search", "spike_trap")]
    chest = _target(result["nextState"], "chest")
    assert (chest["open"], chest["searched"], chest["revision"]) == (True, True, 2)
    assert result["events"][0]["type"] == "interactable.searched"
    assert result["events"][0]["steps"] == ["open"]
    assert result["message"] == "You open the Carved Chest. You search the Carved Chest."


def test_a_closed_container_that_cannot_be_opened_cannot_be_searched():
    state = _state()
    for chest in (_target(state, "chest"), _persisted_target(state, "chest")):
        chest["allowedActions"] = ["inspect", "search"]

    result = resolve_interactable_action(state, _request("search", "chest"), _actor())

    assert result["code"] == "invalid_transition"
    assert result["message"] == "The Carved Chest is closed."


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


def _set_object(state: dict, target_id: str, **fields) -> None:
    for record in (_target(state, target_id), _persisted_target(state, target_id)):
        record.update(deepcopy(fields))


def _locked_chest(state: dict, **fields) -> None:
    _set_object(
        state,
        "chest",
        locked=True,
        requirements={"unlock": {"anyItemIds": ["chest_key"]}},
        setsFlags={"open": {"chest_opened": True}, "search": {"chest_searched": True}},
        discoversClueIds={"search": ["clue_maker"]},
        **fields,
    )


def test_one_search_unlocks_and_opens_a_container_with_the_key_in_order():
    state = _state()
    _locked_chest(state)

    result = resolve_interactable_action(state, _request("search", "chest"), _actor(itemIds=["chest_key"]))

    assert result["ok"] is True
    assert [
        (change["action"], change["targetId"], change["patch"].get("locked"), change["patch"].get("open"))
        for change in result["changes"]
        if change["type"] == "scene.interactable.update"
    ] == [
        ("unlock", "chest", False, None),
        ("open", "chest", None, True),
        ("search", "chest", None, None),
        ("search", "spike_trap", None, None),
    ]
    assert len({change["id"] for change in result["changes"]}) == len(result["changes"])
    event = result["events"][0]
    assert (event["type"], event["action"], event["steps"]) == ("interactable.searched", "search", ["unlock", "open"])
    assert [item["id"] for item in event["revealedItems"]] == ["rope", "hidden_gem"]
    assert result["message"] == (
        "You unlock the Carved Chest. You open the Carved Chest. You search the Carved Chest."
    )
    chest = _target(result["nextState"], "chest")
    assert (chest["locked"], chest["open"], chest["searched"], chest["revision"]) == (False, True, True, 3)
    assert _persisted_target(result["nextState"], "chest")["searched"] is True

    followups = interactable_followup_changes(
        result["nextState"], {"type": "scene.interactable.action", "turnId": 7, "event": event}
    )
    assert {(change["type"], change.get("flagKey") or change.get("clueId")) for change in followups} == {
        ("flag.set", "chest_opened"),
        ("flag.set", "chest_searched"),
        ("clue.discover", "clue_maker"),
    }


def test_the_step_by_step_route_still_works_after_one_click_chaining():
    state = _state()
    _locked_chest(state)
    actor = _actor(itemIds=["chest_key"])

    unlocked = resolve_interactable_action(state, _request("unlock", "chest"), actor)
    opened = resolve_interactable_action(unlocked["nextState"], _request("open", "chest"), actor)
    searched = resolve_interactable_action(opened["nextState"], _request("search", "chest"), actor)

    assert [result["code"] for result in (unlocked, opened, searched)] == ["applied"] * 3
    assert "steps" not in searched["events"][0]
    assert _target(searched["nextState"], "chest")["searched"] is True


def test_a_prerequisite_that_needs_a_roll_is_never_taken_implicitly():
    state = _state()
    lockpicker = _actor(toolIds=["tool_lockpicks"])

    refused = resolve_interactable_action(state, _request("open", "gate"), lockpicker)
    offers = {
        entry["action"]: entry
        for entry in offered_scene_actions(
            state,
            {"id": "player_1", "inventory": {"items": [{"id": "tool_lockpicks", "name": "Lockpicks", "type": "tool"}]}},
        )["gate"]
    }

    assert (refused["code"], refused["message"]) == ("invalid_transition", "The Iron Gate is locked.")
    assert refused["nextState"] == state
    assert offers["open"] == {
        "action": "open",
        "label": "Open Iron Gate",
        "legal": False,
        "done": False,
        "reason": "The Iron Gate is locked.",
    }
    assert offers["unlock"]["legal"] is True
    assert offers["unlock"]["check"] == {"skill": "sleight_of_hand", "ability": "dexterity"}


def test_a_step_with_authored_effects_is_never_taken_implicitly():
    state = _state()
    _locked_chest(state, mechanicalEffects={"open": {"damage": {"dice": "1d4", "type": "piercing"}}})

    result = resolve_interactable_action(state, _request("search", "chest"), _actor(itemIds=["chest_key"]))

    assert (result["code"], result["message"]) == ("invalid_transition", "The Carved Chest is locked.")


def test_a_failed_check_after_the_steps_keeps_the_steps_and_their_discoveries():
    state = _state()
    _locked_chest(state)
    for record in (_target(state, "chest"), _persisted_target(state, "chest")):
        record["requirements"]["search"] = {"check": {"id": "search_chest", "dc": 30, "skill": "investigation"}}
    rolls = {
        "check": lambda actor_id, check, seed: {**check, "roll": 2, "modifier": 0, "bonus": 0, "total": 2, "outcome": "failure", "passed": False},
    }

    result = resolve_interactable_action(
        state, _request("search", "chest"), _actor(itemIds=["chest_key"], rolls=rolls)
    )

    assert result["code"] == "check_failed"
    assert result["message"].startswith("You unlock the Carved Chest. You open the Carved Chest. Investigation check")
    assert result["message"].endswith("The attempt fails.")
    chest = _target(result["nextState"], "chest")
    assert (chest["locked"], chest["open"], chest.get("searched")) == (False, True, None)
    assert chest["failedCheckActorIds"] == ["player_1"]
    event = result["events"][0]
    assert (event["type"], event["steps"]) == ("interactable.check_failed", ["unlock", "open"])
    assert [change.get("action") for change in result["changes"]] == ["unlock", "open", "search", None]
    followups = interactable_followup_changes(
        result["nextState"], {"type": "scene.interactable.action", "turnId": 7, "event": event}
    )
    assert [(change["type"], change.get("flagKey")) for change in followups] == [("flag.set", "chest_opened")]


REFUSAL_CASES = [
    ("gate", "close", {}, {}, "The Iron Gate is already closed."),
    ("gate", "lock", {}, {}, "The Iron Gate is already locked."),
    ("gate", "use", {}, {}, "That can't be done with the Iron Gate."),
    ("side_lock", "unlock", {}, {}, "It is locked, and you carry no key that fits."),
    ("chest", "unlock", {}, {}, "The Carved Chest is not locked."),
    ("chest", "lock", {"open": True}, {}, "The Carved Chest has to be closed first."),
    ("chest", "open", {"open": True}, {}, "The Carved Chest is already open."),
    ("chest", "search", {"searched": True, "open": True}, {}, "You have already searched the Carved Chest."),
    ("chest", "inspect", {"inspected": True}, {}, "You have already examined the Carved Chest."),
    ("gate", "break", {"broken": True, "locked": False, "open": True}, {}, "The Iron Gate is already broken."),
    ("gate", "close", {"broken": True, "locked": False, "open": True}, {}, "The Iron Gate is broken."),
    ("lever", "use", {"usesRemaining": 0, "depleted": True}, {}, "The Bronze Lever can't be used again."),
    ("seal", "use", {}, {}, "You don't know how to do that with the Rune Seal."),
    ("seal", "use", {}, {"capabilities": ["read_runes"]}, "You don't know enough to do that yet."),
    (
        "seal",
        "use",
        {},
        {"capabilities": ["read_runes"], "flags": {"moon_aligned": True}},
        "Something else has to change first.",
    ),
    ("gate", "unlock", {}, {"toolIds": ["tool_lockpicks"]}, "That takes a roll."),
]


@pytest.mark.parametrize(("target_id", "action", "state_patch", "actor_patch", "sentence"), REFUSAL_CASES)
def test_every_refusal_is_one_plain_sentence_naming_the_object(target_id, action, state_patch, actor_patch, sentence):
    state = _state()
    _set_object(state, target_id, **state_patch)

    result = resolve_interactable_action(state, _request(action, target_id), _actor(**actor_patch))

    assert result["ok"] is False
    assert result["message"] == sentence
    for word in ("interactable", "legal", "supported", "_"):
        assert word not in result["message"].lower()


def test_refusals_for_hidden_targets_and_stale_choices_stay_plain():
    hidden = resolve_interactable_action(_state(), _request("trigger", "spike_trap"), _actor())
    stale = resolve_interactable_action(_state(), _request("use", "shrine", expectedRevision=3), _actor())

    assert hidden["message"] == "There is nothing like that here."
    assert stale["message"] == "The Wayside Shrine changed after the player selected it."


def test_a_proper_name_or_one_starting_with_the_takes_no_article():
    state = _state()
    _set_object(state, "gate", name="The Iron Gate")
    _set_object(state, "side_door", name="Whisperwood", proper=True)

    assert resolve_interactable_action(state, _request("close", "gate"), _actor())["message"] == (
        "The Iron Gate is already closed."
    )
    assert resolve_interactable_action(state, _request("close", "side_door"), _actor())["message"] == (
        "Whisperwood is already closed."
    )


def test_offered_actions_follow_the_contract_order_labels_and_done_flags():
    state = _state()
    _set_object(
        state,
        "chest",
        open=True,
        inspected=True,
        actionLabels={"search": "Rummage through the chest"},
        requirements={"search": {"check": {"id": "rummage", "dc": 12, "skill": "investigation", "showDc": True}}},
    )

    offered = offered_scene_actions(state, {"id": "player_1"})

    assert list(offered) == ["gate", "side_door", "side_lock", "chest", "lever", "shrine", "seal"]
    chest = offered["chest"]
    assert [entry["action"] for entry in chest] == ["search", "open", "unlock", "inspect", "close", "lock"]
    assert chest[0] == {
        "action": "search",
        "label": "Rummage through the chest",
        "authored": True,
        "legal": True,
        "done": False,
        "check": {"skill": "investigation", "ability": "intelligence", "dc": 12},
    }
    by_action = {entry["action"]: entry for entry in chest}
    assert by_action["inspect"] == {
        "action": "inspect",
        "label": "Examine Carved Chest",
        "legal": False,
        "done": True,
        "reason": "You have already examined the Carved Chest.",
    }
    assert (by_action["open"]["done"], by_action["close"]["legal"], by_action["close"]["done"]) == (True, True, False)
    assert offered["shrine"] == [
        {"action": "use", "label": "Use Wayside Shrine", "legal": True, "done": False},
        {"action": "inspect", "label": "Examine Wayside Shrine", "legal": True, "done": False},
    ]
    assert "spike_trap" not in offered


def test_a_used_object_that_tracks_no_uses_is_done_but_still_legal():
    state = _state()
    _set_object(state, "shrine", used=True, usedCount=1)

    use = offered_scene_actions(state, {"id": "player_1"})["shrine"][0]

    assert (use["action"], use["legal"], use["done"]) == ("use", True, True)


def test_offered_actions_count_a_chained_search_as_legal():
    state = _state()
    _locked_chest(state)

    with_key = {entry["action"]: entry for entry in offered_scene_actions(
        state, {"id": "player_1", "inventory": {"items": [{"id": "chest_key", "name": "Chest Key"}]}}
    )["chest"]}
    without_key = {entry["action"]: entry for entry in offered_scene_actions(state, {"id": "player_1"})["chest"]}

    assert (with_key["search"]["legal"], with_key["open"]["legal"], with_key["unlock"]["legal"]) == (True, True, True)
    assert without_key["search"] == {
        "action": "search",
        "label": "Search Carved Chest",
        "legal": False,
        "done": False,
        "reason": "The Carved Chest is locked.",
    }
    assert without_key["unlock"]["reason"] == "It is locked, and you carry no key that fits."


@pytest.mark.parametrize(
    ("item", "label"),
    [
        ({"id": "caravan_strongbox_key", "name": "Caravan Strongbox Key"}, "Take the Caravan Strongbox Key"),
        ({"id": "crown", "name": "The Hollow Crown"}, "Take The Hollow Crown"),
        ({"id": "blade", "name": "Dawnbringer", "proper": True}, "Take Dawnbringer"),
        ({"id": "box_coins", "name": "a purse", "type": "currency", "currency": {"gp": 15}}, "Take 15 gp"),
        ({"id": "box_coins_2", "type": "currency", "currency": {"sp": 4, "gp": 2}}, "Take 2 gp, 4 sp"),
    ],
)
def test_take_labels_name_the_item_or_the_coins(item, label):
    assert take_label(item) == label


def test_action_labels_and_action_text_never_reach_the_player_projection():
    state = _state()
    _set_object(
        state,
        "lever",
        actionLabels={"use": "Pull the bronze lever"},
        actionText={"use": "Somewhere below, chains rattle and a gate grinds up."},
        playerKnownFields=["actionText", "actionLabels"],
    )

    lever = next(item for item in project_scene_interactables(state, _actor())["interactables"] if item["id"] == "lever")

    assert "actionText" not in lever
    assert "actionLabels" not in lever
