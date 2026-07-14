from __future__ import annotations

from aidm_server.game_state.application.applier import apply_state_changes
from aidm_server.game_state.validation.validator import (
    validate_state_changes,
    validated_changes_for_application,
)


def _state() -> dict:
    return {
        "sessionId": 44,
        "campaignId": 9,
        "activePlayerIds": [1],
        "playerCharacters": [
            {
                "id": "player_1",
                "playerId": 1,
                "name": "Aria",
                "level": 2,
                "xp": {"current": 10, "nextLevelAt": 300},
                "inventory": {"items": [], "currency": {"gp": 1}},
                "health": {
                    "currentHp": 7,
                    "maxHp": 12,
                    "tempHp": 0,
                    "conditions": [],
                },
            }
        ],
        "currentScene": {
            "locationId": "wolf_bridge",
            "name": "Wolf Bridge",
            "sceneType": "combat",
            "combatState": "active",
            "activeNpcIds": [],
            "activeQuestIds": [],
            "items": [],
        },
        "locations": [
            {
                "id": "wolf_bridge",
                "name": "Wolf Bridge",
                "status": "visited",
            }
        ],
        "combat": {
            "status": "active",
            "round": 3,
            "turnIndex": 0,
            "participants": [
                {
                    "id": "player_1",
                    "name": "Aria",
                    "team": "player",
                    "isPresent": True,
                    "isAlive": True,
                    "isConscious": True,
                    "conditions": [],
                    "hp": {"current": 7, "max": 12},
                },
                {
                    "id": "enemy_alpha_wolf",
                    "name": "Alpha Wolf",
                    "team": "enemy",
                    "isPresent": True,
                    "isAlive": False,
                    "isConscious": False,
                    "conditions": ["defeated"],
                    "hp": {"current": 0, "max": 18},
                },
            ],
            "flags": {"campaignPackEncounterId": "enc_wolf_bridge"},
        },
        "campaignPack": {
            "packId": "pack_wolf_road",
            "catalog": {
                "encounters": [
                    {
                        "id": "enc_wolf_bridge",
                        "title": "The Alpha at Wolf Bridge",
                        "rewards": {
                            "xp": 25,
                            "gp": 4,
                            "items": [
                                {
                                    "id": "alpha_fang",
                                    "name": "Alpha Fang",
                                    "type": "trophy",
                                    "quantity": 1,
                                }
                            ],
                            "flags": {"wolf_bridge_secured": True},
                        },
                        "outcomes": {
                            "victory": {
                                "consequences": [
                                    {
                                        "type": "flag.set",
                                        "flagKey": "road_reopened",
                                        "flagValue": True,
                                    }
                                ]
                            }
                        },
                    }
                ]
            },
        },
        "flags": {},
        "quests": [],
        "stateChangeLedger": [],
    }


def _end_change() -> dict:
    return {
        "id": "end-wolf-bridge-turn-30",
        "turnId": 30,
        "type": "combat.end",
        "status": "ended",
        "endReason": "all_enemies_defeated",
        "encounterId": "enc_wolf_bridge",
        "reason": "The alpha wolf is defeated and the bridge is secured.",
    }


def test_combat_end_applies_authored_reward_transaction_exactly_once_on_replay() -> (
    None
):
    state = _state()
    end_change = _end_change()
    validation = validate_state_changes(state=state, changes=[end_change])

    assert validation["rejected"] == []
    first = apply_state_changes(
        state,
        validated_changes_for_application(validation),
    )
    first_state = first["nextState"]
    actor = first_state["playerCharacters"][0]

    assert first_state["combat"]["status"] == "ended"
    assert first_state["combat"]["flags"]["endReason"] == "all_enemies_defeated"
    assert actor["xp"]["current"] == 35
    assert actor["inventory"]["currency"]["gp"] == 5
    assert [item["name"] for item in actor["inventory"]["items"]] == ["Alpha Fang"]
    assert first_state["flags"]["wolf_bridge_secured"] is True
    assert first_state["flags"]["road_reopened"] is True
    assert len(first_state["combatRewardLedger"]) == 1
    reward_record = first_state["combatRewardLedger"][0]
    assert reward_record["encounterId"] == "enc_wolf_bridge"
    assert reward_record["combatOutcome"] == "victory"
    assert reward_record["rewardChangeIds"]
    assert len(reward_record["rewardChangeIds"]) == len(
        set(reward_record["rewardChangeIds"])
    )

    replay = apply_state_changes(first_state, [end_change])
    replay_state = replay["nextState"]
    replay_actor = replay_state["playerCharacters"][0]

    assert replay["appliedChanges"] == []
    assert replay["skippedChanges"] == [
        {"change": end_change, "reason": "State change was already applied."}
    ]
    assert replay_actor["xp"]["current"] == 35
    assert replay_actor["inventory"]["currency"]["gp"] == 5
    assert [item["name"] for item in replay_actor["inventory"]["items"]] == [
        "Alpha Fang"
    ]
    assert len(replay_state["combatRewardLedger"]) == 1
    assert len({entry["id"] for entry in replay_state["stateChangeLedger"]}) == len(
        replay_state["stateChangeLedger"]
    )
