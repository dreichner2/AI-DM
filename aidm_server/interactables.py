"""Pure rules for persistent scene interactables.

The module deliberately does not know about Flask, SQLAlchemy, Socket.IO, or the
DM.  Callers pass an authoritative state snapshot and receive a deep-copied next
state plus deterministic changes and events.  Narration can describe those
results, but it is never an input to the transition.

Canonical scene shape::

    {
        "currentScene": {
            "locationId": "old_keep",
            "interactables": [{"id": "gate", "kind": "door", "open": False}],
            "hazards": [{"id": "spikes", "kind": "hazard", "active": True}],
        },
        "locations": [{
            "id": "old_keep",
            "sceneState": {"interactables": [...], "hazards": [...]},
        }],
    }

``currentScene`` is authoritative while a location is active.  A successful
action mirrors both collections into that location's ``sceneState`` so travel
can restore the same mechanical truth.  Existing top-level location collections
are updated as compatibility mirrors, but new state is written to ``sceneState``.

Requirements are action-scoped under ``requirements``.  They may contain
``allOf``/``anyOf`` groups and exact ``allItemIds``, ``anyItemIds``,
``allToolIds``, ``anyToolIds``, ``allCapabilities``, ``anyCapabilities``,
``flags``, ``objectStates``, and ``check`` predicates.  Check results in the
actor context must have been authoritatively resolved by the caller.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
import hashlib
import json
from typing import Any


__all__ = [
    "ACTION_LEDGER_KEY",
    "INTERACTABLE_ACTIONS",
    "INTERACTABLE_KINDS",
    "available_actions_for_target",
    "project_interactable_event",
    "project_scene_interactables",
    "resolve_interactable_action",
    "validate_interactable_catalog",
]


COLLECTIONS = ("interactables", "hazards")
INTERACTABLE_KINDS = frozenset({"door", "lock", "container", "object", "hazard"})
INTERACTABLE_ACTIONS = frozenset(
    {
        "inspect",
        "open",
        "close",
        "lock",
        "unlock",
        "search",
        "break",
        "use",
        "disarm",
        "trigger",
        "reset",
    }
)
ACTION_LEDGER_KEY = "interactableActionLedger"
ACTION_LEDGER_LIMIT = 256

_SAFE_TRANSITION_FIELDS = frozenset(
    {
        "open",
        "locked",
        "broken",
        "searched",
        "inspected",
        "used",
        "usedCount",
        "depleted",
        "usesRemaining",
        "active",
        "triggered",
        "disarmed",
        "playerKnown",
        "hidden",
        "contentsKnown",
    }
)
_BOOLEAN_STATE_FIELDS = frozenset(
    {
        "open",
        "locked",
        "broken",
        "searched",
        "inspected",
        "used",
        "depleted",
        "active",
        "triggered",
        "disarmed",
        "playerKnown",
        "hidden",
        "contentsKnown",
    }
)
_TRACKING_FIELDS = frozenset(
    {
        "revision",
        "lastInteractionAction",
        "lastInteractionActionId",
        "lastInteractionActorId",
        "lastInteractionTurn",
    }
)
_PUBLIC_BASE_FIELDS = frozenset(
    {
        "id",
        "name",
        "kind",
        "description",
        "open",
        "locked",
        "broken",
        "searched",
        "inspected",
        "used",
        "usedCount",
        "depleted",
        "usesRemaining",
        "active",
        "triggered",
        "disarmed",
        "playerKnown",
        "contentsKnown",
        "revision",
        "publicEffect",
        "playerTags",
    }
)
_NEVER_PUBLIC_FIELDS = frozenset(
    {
        "gmOnly",
        "gmNotes",
        "secrets",
        "requirements",
        "reveals",
        "revealFields",
        "transitions",
        "mechanicalEffects",
        "hiddenFields",
        "playerKnownFields",
        "eventVisibility",
        "controlsTargetId",
        "allowedActions",
        "disabledActions",
    }
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _string_set(value: Any) -> set[str]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return set()
    return {_text(item) for item in value if _text(item)}


def _ids_from_records(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        _text(item.get("id") or item.get("itemId") or item.get("toolId"))
        for item in value
        if isinstance(item, dict)
        and _text(item.get("id") or item.get("itemId") or item.get("toolId"))
    }


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "|".join(_text(part) for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:20]}"


def _fingerprint(
    *, action: str, target_id: str, location_id: str, actor_id: str
) -> str:
    payload = json.dumps(
        {
            "action": action,
            "actorId": actor_id,
            "locationId": location_id,
            "targetId": target_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _result(
    state: Mapping[str, Any] | Any,
    *,
    ok: bool,
    code: str,
    message: str,
    action_id: str = "",
    action: str = "",
    target_id: str = "",
    location_id: str = "",
    changes: list[dict[str, Any]] | None = None,
    events: list[dict[str, Any]] | None = None,
    details: dict[str, Any] | None = None,
    next_state: dict[str, Any] | None = None,
    replayed: bool = False,
) -> dict[str, Any]:
    safe_state = deepcopy(dict(state)) if isinstance(state, Mapping) else {}
    payload: dict[str, Any] = {
        "ok": ok,
        "code": code,
        "message": message,
        "actionId": action_id,
        "action": action,
        "targetId": target_id,
        "locationId": location_id,
        "changes": changes or [],
        "events": events or [],
        "nextState": next_state if next_state is not None else safe_state,
    }
    if details:
        payload["details"] = details
    if replayed:
        payload["replayed"] = True
    return payload


def _location_records(state: dict[str, Any]) -> list[dict[str, Any]]:
    locations = state.get("locations")
    if not isinstance(locations, list):
        return []
    return [location for location in locations if isinstance(location, dict)]


def _location_collection(location: dict[str, Any], collection: str) -> list[Any]:
    scene_state = location.get("sceneState")
    if not isinstance(scene_state, dict):
        scene_state = location.get("scene_state")
    if isinstance(scene_state, dict) and collection in scene_state:
        value = scene_state.get(collection)
        return value if isinstance(value, list) else []
    value = location.get(collection)
    return value if isinstance(value, list) else []


def _find_current_location(
    state: dict[str, Any], location_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    matches = [
        location
        for location in _location_records(state)
        if _text(location.get("id")) == location_id
    ]
    if not matches:
        return None, "location_not_found"
    if len(matches) > 1:
        return None, "duplicate_location_id"
    return matches[0], None


def _hydrate_current_scene(
    state: dict[str, Any], location: dict[str, Any]
) -> tuple[dict[str, Any] | None, str | None]:
    scene = state.get("currentScene")
    if not isinstance(scene, dict):
        return None, "invalid_current_scene"
    for collection in COLLECTIONS:
        if collection not in scene:
            scene[collection] = deepcopy(_location_collection(location, collection))
        elif not isinstance(scene.get(collection), list):
            return None, f"invalid_{collection}_collection"
    return scene, None


def _entry_kind(entry: dict[str, Any], collection: str) -> str:
    return _text(entry.get("kind")).lower() or (
        "hazard" if collection == "hazards" else "object"
    )


def _index_scene(
    scene: dict[str, Any],
) -> tuple[dict[str, tuple[dict[str, Any], str]], set[str], str | None]:
    index: dict[str, tuple[dict[str, Any], str]] = {}
    duplicates: set[str] = set()
    for collection in COLLECTIONS:
        records = scene.get(collection)
        if not isinstance(records, list):
            return {}, set(), f"invalid_{collection}_collection"
        for record in records:
            if not isinstance(record, dict):
                return {}, set(), "malformed_interactable"
            record_id = _text(record.get("id"))
            if not record_id:
                return {}, set(), "malformed_interactable"
            if record_id in index:
                duplicates.add(record_id)
            else:
                index[record_id] = (record, collection)
    return index, duplicates, None


def _target_appears_in_remote_location(
    state: dict[str, Any], target_id: str, current_location_id: str
) -> bool:
    for location in _location_records(state):
        if _text(location.get("id")) == current_location_id:
            continue
        appearances = 0
        for collection in COLLECTIONS:
            appearances += sum(
                1
                for entry in _location_collection(location, collection)
                if isinstance(entry, dict) and _text(entry.get("id")) == target_id
            )
        if appearances:
            return True
    return False


def _location_target_appearances(
    location: dict[str, Any], target_id: str
) -> list[tuple[dict[str, Any], str]]:
    matches: list[tuple[dict[str, Any], str]] = []
    for collection in COLLECTIONS:
        matches.extend(
            (entry, collection)
            for entry in _location_collection(location, collection)
            if isinstance(entry, dict) and _text(entry.get("id")) == target_id
        )
    return matches


def _actor_context(actor: Mapping[str, Any]) -> dict[str, Any]:
    items = _string_set(actor.get("itemIds")) | _ids_from_records(actor.get("items"))
    tools = _string_set(actor.get("toolIds")) | _ids_from_records(actor.get("tools"))
    return {
        "id": _text(actor.get("id") or actor.get("actorId")),
        "items": items,
        "tools": tools,
        "capabilities": _string_set(actor.get("capabilities")),
        "flags": dict(actor.get("flags"))
        if isinstance(actor.get("flags"), Mapping)
        else {},
        "checks": dict(actor.get("resolvedChecks") or actor.get("checks"))
        if isinstance(actor.get("resolvedChecks") or actor.get("checks"), Mapping)
        else {},
        "isGm": actor.get("isGm") is True,
        "canTargetHidden": actor.get("canTargetHidden") is True,
        "knownIds": _string_set(actor.get("knownInteractableIds")),
    }


def _target_is_visible(target: dict[str, Any], actor: dict[str, Any]) -> bool:
    if actor["isGm"]:
        return True
    if target.get("gmOnly") is True:
        return False
    target_id = _text(target.get("id"))
    if (
        target.get("hidden") is True
        and target.get("playerKnown") is not True
        and target_id not in actor["knownIds"]
    ):
        return actor["canTargetHidden"]
    return True


def _failure(code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "code": code, "message": message, "details": details}


def _requirement_failure_priority(failure: dict[str, Any]) -> int:
    return {
        "authoritative_check_mismatch": 4,
        "check_failed": 3,
        "check_required": 2,
        "prerequisite_missing": 1,
    }.get(_text(failure.get("code")), 0)


def _evaluate_requirement(
    requirement: Any,
    *,
    actor: dict[str, Any],
    objects: dict[str, tuple[dict[str, Any], str]],
    depth: int = 0,
) -> dict[str, Any]:
    if requirement in (None, {}, []):
        return {"ok": True}
    if not isinstance(requirement, Mapping) or depth > 8:
        return _failure(
            "malformed_interactable", "The interactable has malformed prerequisites."
        )

    all_of = requirement.get("allOf")
    if all_of is not None:
        if not isinstance(all_of, list):
            return _failure(
                "malformed_interactable", "allOf prerequisites must be a list."
            )
        for child in all_of:
            result = _evaluate_requirement(
                child, actor=actor, objects=objects, depth=depth + 1
            )
            if not result["ok"]:
                return result

    any_of = requirement.get("anyOf")
    if any_of is not None:
        if not isinstance(any_of, list) or not any_of:
            return _failure(
                "malformed_interactable",
                "anyOf prerequisites must be a non-empty list.",
            )
        failures: list[dict[str, Any]] = []
        for child in any_of:
            result = _evaluate_requirement(
                child, actor=actor, objects=objects, depth=depth + 1
            )
            if result["ok"]:
                break
            failures.append(result)
        else:
            return max(failures, key=_requirement_failure_priority)

    exact_groups = (
        ("allItemIds", actor["items"], True),
        ("anyItemIds", actor["items"], False),
        ("allToolIds", actor["tools"], True),
        ("anyToolIds", actor["tools"], False),
        ("allCapabilities", actor["capabilities"], True),
        ("anyCapabilities", actor["capabilities"], False),
    )
    for field, possessed, require_all in exact_groups:
        required = _string_set(requirement.get(field))
        if not required:
            continue
        satisfied = (
            required.issubset(possessed) if require_all else bool(required & possessed)
        )
        if not satisfied:
            return _failure(
                "prerequisite_missing",
                "The actor does not meet the action prerequisites.",
                prerequisite=field,
                requiredIds=sorted(required),
            )

    required_flags = requirement.get("flags")
    if required_flags is not None:
        if not isinstance(required_flags, Mapping):
            return _failure(
                "malformed_interactable", "flags prerequisites must be an object."
            )
        for key, expected in required_flags.items():
            if actor["flags"].get(str(key)) != expected:
                return _failure(
                    "prerequisite_missing",
                    "The actor does not meet the action prerequisites.",
                    prerequisite="flags",
                    flag=str(key),
                )

    object_states = requirement.get("objectStates")
    if object_states is not None:
        if not isinstance(object_states, list):
            return _failure(
                "malformed_interactable", "objectStates prerequisites must be a list."
            )
        for predicate in object_states:
            if not isinstance(predicate, Mapping):
                return _failure(
                    "malformed_interactable",
                    "An object state prerequisite is malformed.",
                )
            predicate_id = _text(predicate.get("targetId"))
            field = _text(predicate.get("field"))
            if not predicate_id or not field or predicate_id not in objects:
                return _failure(
                    "prerequisite_missing",
                    "A required scene-object state is not satisfied.",
                    prerequisite="objectStates",
                    targetId=predicate_id,
                )
            expected = predicate.get("equals", True)
            if objects[predicate_id][0].get(field) != expected:
                return _failure(
                    "prerequisite_missing",
                    "A required scene-object state is not satisfied.",
                    prerequisite="objectStates",
                    targetId=predicate_id,
                    field=field,
                )

    check = requirement.get("check")
    if check is not None:
        if not isinstance(check, Mapping) or not _text(check.get("id")):
            return _failure(
                "malformed_interactable",
                "The interactable has a malformed check prerequisite.",
            )
        check_id = _text(check.get("id"))
        resolved = actor["checks"].get(check_id)
        if not isinstance(resolved, Mapping):
            return _failure(
                "check_required",
                "An authoritative check must be resolved before this action.",
                check=dict(check),
            )
        for field in ("dc", "ability", "skill"):
            if field in check and resolved.get(field) != check.get(field):
                return _failure(
                    "authoritative_check_mismatch",
                    "The supplied check result does not match the required check.",
                    checkId=check_id,
                    field=field,
                )
        if resolved.get("passed") is not True:
            return _failure(
                "check_failed", "The authoritative check failed.", checkId=check_id
            )

    return {"ok": True}


def _requirements_for(target: dict[str, Any], action: str) -> dict[str, Any] | None:
    raw = target.get("requirements")
    if not isinstance(raw, Mapping):
        return None if raw in (None, {}) else {"allOf": "malformed"}
    action_names = INTERACTABLE_ACTIONS | {"*"}
    if any(key in action_names for key in raw):
        requirements = [raw[key] for key in ("*", action) if key in raw]
        if not requirements:
            return None
        return {"allOf": requirements}
    return dict(raw)


def _requirement_shape_error(
    requirement: Any, *, depth: int = 0, action_scoped: bool = True
) -> str | None:
    if requirement in (None, {}):
        return None
    if not isinstance(requirement, Mapping) or depth > 8:
        return "requirements must be an object with no more than eight nested groups"
    if action_scoped and any(
        key in (INTERACTABLE_ACTIONS | {"*"}) for key in requirement
    ):
        for action, child in requirement.items():
            if action not in INTERACTABLE_ACTIONS | {"*"}:
                continue
            error = _requirement_shape_error(
                child, depth=depth + 1, action_scoped=False
            )
            if error:
                return error
        return None
    for group in ("allOf", "anyOf"):
        if group not in requirement:
            continue
        children = requirement.get(group)
        if not isinstance(children, list) or (group == "anyOf" and not children):
            return f"{group} must be {'a non-empty' if group == 'anyOf' else 'a'} list"
        for child in children:
            error = _requirement_shape_error(
                child, depth=depth + 1, action_scoped=False
            )
            if error:
                return error
    for field in (
        "allItemIds",
        "anyItemIds",
        "allToolIds",
        "anyToolIds",
        "allCapabilities",
        "anyCapabilities",
    ):
        if field in requirement and (
            not isinstance(requirement.get(field), list)
            or any(not _text(value) for value in requirement.get(field, []))
        ):
            return f"{field} must be a list of non-empty exact IDs"
    if "flags" in requirement and not isinstance(requirement.get("flags"), Mapping):
        return "flags must be an object"
    if "objectStates" in requirement:
        predicates = requirement.get("objectStates")
        if not isinstance(predicates, list):
            return "objectStates must be a list"
        for predicate in predicates:
            if (
                not isinstance(predicate, Mapping)
                or not _text(predicate.get("targetId"))
                or not _text(predicate.get("field"))
            ):
                return "each objectStates predicate requires targetId and field"
    if "check" in requirement:
        check = requirement.get("check")
        if not isinstance(check, Mapping) or not _text(check.get("id")):
            return "check requires an exact id"
        dc = check.get("dc")
        if dc is not None and (
            isinstance(dc, bool) or not isinstance(dc, int) or dc < 0
        ):
            return "check.dc must be a non-negative integer"
    return None


def _candidate_actions(target: dict[str, Any], collection: str) -> set[str]:
    kind = _entry_kind(target, collection)
    actions = {"inspect"}
    if kind in {"door", "container"} or target.get("openable") is True:
        actions.update({"open", "close"})
    if kind == "lock" or target.get("lockable") is True or "locked" in target:
        actions.update({"lock", "unlock"})
    if kind == "container" or target.get("searchable") is True:
        actions.add("search")
    if target.get("breakable") is True:
        actions.add("break")
    if target.get("usable") is True:
        actions.add("use")
    if kind == "hazard":
        actions.add("trigger")
        if target.get("disarmable") is True:
            actions.add("disarm")
        if target.get("resettable") is True:
            actions.add("reset")
    allowed = _string_set(target.get("allowedActions"))
    if allowed:
        actions &= allowed
    actions -= _string_set(target.get("disabledActions"))
    return actions


def _invariant_error(target: dict[str, Any], collection: str) -> str | None:
    kind = _entry_kind(target, collection)
    if kind not in INTERACTABLE_KINDS:
        return "unsupported interactable kind"
    for field in sorted(_BOOLEAN_STATE_FIELDS):
        if field in target and not isinstance(target.get(field), bool):
            return f"{field} must be a boolean"
    if target.get("open") is True and target.get("locked") is True:
        return "an interactable cannot be both open and locked"
    if target.get("broken") is True and target.get("locked") is True:
        return "a broken interactable cannot remain locked"
    uses_remaining = target.get("usesRemaining")
    if uses_remaining is not None and (
        isinstance(uses_remaining, bool)
        or not isinstance(uses_remaining, int)
        or uses_remaining < 0
    ):
        return "usesRemaining must be a non-negative integer"
    if target.get("depletable") is True and uses_remaining is None:
        return "a depletable interactable requires usesRemaining"
    if (
        target.get("depleted") is True
        and isinstance(uses_remaining, int)
        and uses_remaining > 0
    ):
        return "a depleted interactable cannot have remaining uses"
    used_count = target.get("usedCount")
    if used_count is not None and (
        isinstance(used_count, bool)
        or not isinstance(used_count, int)
        or used_count < 0
    ):
        return "usedCount must be a non-negative integer"
    revision = target.get("revision")
    if revision is not None and (
        isinstance(revision, bool) or not isinstance(revision, int) or revision < 0
    ):
        return "revision must be a non-negative integer"
    if (
        kind == "hazard"
        and target.get("disarmed") is True
        and target.get("active") is True
    ):
        return "a disarmed hazard cannot remain active"
    return None


def _action_legality(
    target: dict[str, Any],
    collection: str,
    action: str,
    *,
    actor: dict[str, Any],
    objects: dict[str, tuple[dict[str, Any], str]],
) -> dict[str, Any]:
    invariant_error = _invariant_error(target, collection)
    if invariant_error:
        return _failure(
            "malformed_interactable",
            f"The interactable state is contradictory: {invariant_error}.",
        )
    if action not in _candidate_actions(target, collection):
        return _failure(
            "unsupported_action", "That action is not supported by this interactable."
        )

    if (
        action == "inspect"
        and target.get("inspected") is True
        and target.get("repeatableInspect") is not True
    ):
        return _failure(
            "invalid_transition", "The interactable has already been inspected."
        )
    if action == "open":
        if target.get("broken") is True:
            return _failure(
                "invalid_transition", "A broken interactable cannot be opened normally."
            )
        if target.get("open") is True:
            return _failure("invalid_transition", "The interactable is already open.")
        if target.get("locked") is True:
            return _failure("invalid_transition", "The interactable is locked.")
    if action == "close":
        if target.get("broken") is True:
            return _failure(
                "invalid_transition", "A broken interactable cannot be closed."
            )
        if target.get("open") is not True:
            return _failure("invalid_transition", "The interactable is already closed.")
    if action == "lock":
        if target.get("broken") is True:
            return _failure(
                "invalid_transition", "A broken interactable cannot be locked."
            )
        if target.get("open") is True:
            return _failure(
                "invalid_transition",
                "The interactable must be closed before it can be locked.",
            )
        if target.get("locked") is True:
            return _failure("invalid_transition", "The interactable is already locked.")
    if action == "unlock":
        if target.get("broken") is True:
            return _failure(
                "invalid_transition",
                "A broken interactable cannot be unlocked normally.",
            )
        if target.get("locked") is not True:
            return _failure(
                "invalid_transition", "The interactable is already unlocked."
            )
    if action == "search":
        if target.get("locked") is True:
            return _failure(
                "invalid_transition", "A locked interactable cannot be searched."
            )
        if (
            _entry_kind(target, collection) == "container"
            and target.get("open") is not True
            and target.get("searchWhenClosed") is not True
        ):
            return _failure(
                "invalid_transition",
                "The container must be opened before it can be searched.",
            )
        if (
            target.get("searched") is True
            and target.get("repeatableSearch") is not True
        ):
            return _failure(
                "invalid_transition", "The interactable has already been searched."
            )
    if action == "break" and target.get("broken") is True:
        return _failure("invalid_transition", "The interactable is already broken.")
    if action == "use":
        if target.get("broken") is True:
            return _failure(
                "invalid_transition", "A broken interactable cannot be used."
            )
        if target.get("depleted") is True or target.get("usesRemaining") == 0:
            return _failure(
                "resource_exhausted", "The interactable has no uses remaining."
            )
    if action == "disarm":
        if target.get("disarmed") is True:
            return _failure("invalid_transition", "The hazard is already disarmed.")
        if target.get("active") is not True or target.get("triggered") is True:
            return _failure(
                "invalid_transition",
                "The hazard cannot be disarmed in its current state.",
            )
    if action == "trigger":
        if target.get("disarmed") is True or target.get("active") is not True:
            return _failure("invalid_transition", "The hazard is not active.")
        if target.get("triggered") is True and target.get("repeatable") is not True:
            return _failure("invalid_transition", "The hazard has already triggered.")
    if action == "reset":
        if (
            target.get("active") is True
            and target.get("triggered") is not True
            and target.get("disarmed") is not True
        ):
            return _failure("invalid_transition", "The hazard is already reset.")

    requirement = _requirements_for(target, action)
    return _evaluate_requirement(requirement, actor=actor, objects=objects)


def _mark_contents_known(target: dict[str, Any], *, include_hidden: bool) -> None:
    contents = target.get("contents")
    if not isinstance(contents, list):
        return
    for item in contents:
        if not isinstance(item, dict):
            continue
        if include_hidden or item.get("hidden") is not True:
            item["playerKnown"] = True


def _primary_patch(
    target: dict[str, Any], collection: str, action: str
) -> dict[str, Any]:
    patch: dict[str, Any] = {"playerKnown": True}
    kind = _entry_kind(target, collection)
    if action == "inspect":
        patch["inspected"] = True
    elif action == "open":
        patch["open"] = True
        if kind == "container" and target.get("revealsContentsOnOpen") is not False:
            patch["contentsKnown"] = True
    elif action == "close":
        patch["open"] = False
    elif action == "lock":
        patch["locked"] = True
    elif action == "unlock":
        patch["locked"] = False
    elif action == "search":
        patch.update({"searched": True, "contentsKnown": True})
    elif action == "break":
        patch.update({"broken": True, "locked": False})
        if kind in {"door", "container"}:
            patch["open"] = True
        if "active" in target:
            patch["active"] = False
    elif action == "use":
        patch.update(
            {"used": True, "usedCount": max(0, int(target.get("usedCount") or 0)) + 1}
        )
        if isinstance(target.get("usesRemaining"), int) and not isinstance(
            target.get("usesRemaining"), bool
        ):
            remaining = max(0, target["usesRemaining"] - 1)
            patch.update({"usesRemaining": remaining, "depleted": remaining == 0})
    elif action == "disarm":
        patch.update({"disarmed": True, "active": False, "triggered": False})
    elif action == "trigger":
        patch["triggered"] = True
        if target.get("oneShot") is True:
            patch.update({"active": False, "depleted": True})
    elif action == "reset":
        patch.update(
            {"active": True, "triggered": False, "disarmed": False, "depleted": False}
        )
    return patch


def _action_reveals(target: dict[str, Any], action: str) -> list[str]:
    reveals = target.get("reveals")
    if isinstance(reveals, Mapping):
        return sorted(_string_set(reveals.get(action)))
    return sorted(_string_set(reveals)) if action == "search" else []


def _reveal_fields(target: dict[str, Any], action: str) -> set[str]:
    reveal_fields = target.get("revealFields")
    if isinstance(reveal_fields, Mapping):
        return _string_set(reveal_fields.get(action))
    return set()


def _authored_transitions(
    target: dict[str, Any], action: str
) -> tuple[list[dict[str, Any]], str | None]:
    transitions = target.get("transitions")
    if not isinstance(transitions, Mapping) or action not in transitions:
        return [], None
    raw = transitions.get(action)
    if not isinstance(raw, list):
        return [], "transitions for an action must be a list"
    normalized: list[dict[str, Any]] = []
    for transition in raw:
        if not isinstance(transition, Mapping):
            return [], "a transition must be an object"
        target_id = _text(transition.get("targetId"))
        patch = transition.get("set")
        if not target_id or not isinstance(patch, Mapping) or not patch:
            return [], "a transition requires targetId and a non-empty set object"
        unsupported = sorted(set(patch) - _SAFE_TRANSITION_FIELDS)
        if unsupported:
            return [], f"a transition writes unsupported fields: {unsupported}"
        normalized.append({"targetId": target_id, "patch": dict(patch)})
    return normalized, None


def _controlled_transition(target: dict[str, Any], action: str) -> list[dict[str, Any]]:
    controlled_id = _text(target.get("controlsTargetId"))
    if not controlled_id or _entry_kind(target, "interactables") != "lock":
        return []
    if action == "lock":
        return [{"targetId": controlled_id, "patch": {"locked": True}}]
    if action in {"unlock", "break"}:
        return [{"targetId": controlled_id, "patch": {"locked": False}}]
    return []


def _tracking_patch(
    target: dict[str, Any], *, request: dict[str, Any], actor_id: str
) -> dict[str, Any]:
    patch: dict[str, Any] = {
        "revision": max(0, int(target.get("revision") or 0)) + 1,
        "lastInteractionAction": request["action"],
        "lastInteractionActionId": request["actionId"],
        "lastInteractionActorId": actor_id,
    }
    if request.get("turnId") is not None:
        patch["lastInteractionTurn"] = request["turnId"]
    return patch


def _diff_patch(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    fields = (
        _SAFE_TRANSITION_FIELDS | _TRACKING_FIELDS | {"contents", "playerKnownFields"}
    )
    return {
        field: deepcopy(after.get(field))
        for field in sorted(fields)
        if before.get(field) != after.get(field)
    }


def _sync_location_scene(location: dict[str, Any], scene: dict[str, Any]) -> None:
    existing_camel = location.get("sceneState")
    existing_snake = location.get("scene_state")
    if isinstance(existing_camel, dict):
        scene_state = existing_camel
    elif isinstance(existing_snake, dict):
        scene_state = existing_snake
    else:
        scene_state = {}
        location["sceneState"] = scene_state
    for collection in COLLECTIONS:
        scene_state[collection] = deepcopy(
            scene.get(collection) if isinstance(scene.get(collection), list) else []
        )
        if isinstance(location.get(collection), list):
            location[collection] = deepcopy(scene_state[collection])


def _record_action(
    state: dict[str, Any],
    *,
    action_id: str,
    fingerprint: str,
    event_ids: list[str],
) -> None:
    ledger = state.get(ACTION_LEDGER_KEY)
    if not isinstance(ledger, dict):
        ledger = {}
        state[ACTION_LEDGER_KEY] = ledger
    ledger[action_id] = {"fingerprint": fingerprint, "eventIds": list(event_ids)}
    while len(ledger) > ACTION_LEDGER_LIMIT:
        ledger.pop(next(iter(ledger)))


def resolve_interactable_action(
    state: Mapping[str, Any] | Any,
    request: Mapping[str, Any] | Any,
    actor: Mapping[str, Any] | Any,
) -> dict[str, Any]:
    """Validate and atomically apply one exact-ID scene-object action.

    ``request`` requires ``actionId``, ``action``, ``targetId``, and
    ``locationId``.  ``turnId`` and ``expectedRevision`` are optional.  ``actor``
    requires an exact ``id`` and may provide item/tool/capability IDs, flags, and
    authoritative resolved checks.  The input objects are never mutated.
    """

    if (
        not isinstance(state, Mapping)
        or not isinstance(request, Mapping)
        or not isinstance(actor, Mapping)
    ):
        return _result(
            state,
            ok=False,
            code="invalid_request",
            message="State, request, and actor must be objects.",
        )

    action_id = _text(request.get("actionId"))
    action = _text(request.get("action")).lower()
    target_id = _text(request.get("targetId"))
    location_id = _text(request.get("locationId"))
    actor_ctx = _actor_context(actor)
    actor_id = actor_ctx["id"]
    common = {
        "action_id": action_id,
        "action": action,
        "target_id": target_id,
        "location_id": location_id,
    }
    if (
        not action_id
        or not target_id
        or not location_id
        or not actor_id
        or action not in INTERACTABLE_ACTIONS
    ):
        return _result(
            state,
            ok=False,
            code="invalid_request",
            message="actionId, a supported action, targetId, locationId, and actor.id are required.",
            **common,
        )
    turn_id = request.get("turnId")
    if turn_id is not None and (
        isinstance(turn_id, bool) or not isinstance(turn_id, int) or turn_id < 0
    ):
        return _result(
            state,
            ok=False,
            code="invalid_request",
            message="turnId must be a non-negative integer.",
            **common,
        )
    expected_revision = request.get("expectedRevision")
    if expected_revision is not None and (
        isinstance(expected_revision, bool)
        or not isinstance(expected_revision, int)
        or expected_revision < 0
    ):
        return _result(
            state,
            ok=False,
            code="invalid_request",
            message="expectedRevision must be a non-negative integer.",
            **common,
        )

    fingerprint = _fingerprint(
        action=action, target_id=target_id, location_id=location_id, actor_id=actor_id
    )
    existing_ledger = state.get(ACTION_LEDGER_KEY)
    if isinstance(existing_ledger, Mapping) and action_id in existing_ledger:
        entry = existing_ledger.get(action_id)
        if isinstance(entry, Mapping) and entry.get("fingerprint") == fingerprint:
            return _result(
                state,
                ok=True,
                code="already_applied",
                message="This interactable action was already applied.",
                details={"eventIds": list(entry.get("eventIds") or [])},
                replayed=True,
                **common,
            )
        return _result(
            state,
            ok=False,
            code="action_id_conflict",
            message="The actionId is already associated with a different interactable action.",
            **common,
        )

    next_state = deepcopy(dict(state))
    scene = next_state.get("currentScene")
    if not isinstance(scene, dict) or _text(scene.get("locationId")) != location_id:
        return _result(
            state,
            ok=False,
            code="location_mismatch",
            message="The requested location is not the actor's current scene.",
            **common,
        )
    location, location_error = _find_current_location(next_state, location_id)
    if location_error:
        return _result(
            state,
            ok=False,
            code=location_error,
            message="The current scene does not resolve to one exact persistent location.",
            **common,
        )
    assert location is not None
    scene, scene_error = _hydrate_current_scene(next_state, location)
    if scene_error:
        return _result(
            state,
            ok=False,
            code=scene_error,
            message="The current scene has malformed interactable collections.",
            **common,
        )
    assert scene is not None
    objects, duplicates, index_error = _index_scene(scene)
    if index_error:
        return _result(
            state,
            ok=False,
            code=index_error,
            message="The current scene contains a malformed interactable.",
            **common,
        )
    persisted_matches = _location_target_appearances(location, target_id)
    mirror_conflict = bool(
        len(persisted_matches) == 1
        and target_id in objects
        and _entry_kind(persisted_matches[0][0], persisted_matches[0][1])
        != _entry_kind(objects[target_id][0], objects[target_id][1])
    )
    if (
        target_id in duplicates
        or len(persisted_matches) > 1
        or mirror_conflict
        or _target_appears_in_remote_location(next_state, target_id, location_id)
    ):
        return _result(
            state,
            ok=False,
            code="duplicate_interactable_id",
            message="The target ID is not globally unique.",
            **common,
        )
    target_entry = objects.get(target_id)
    if not target_entry or not _target_is_visible(target_entry[0], actor_ctx):
        return _result(
            state,
            ok=False,
            code="interactable_not_found",
            message="No available interactable exists with that exact ID in the current scene.",
            **common,
        )
    target, collection = target_entry
    target_kind = _entry_kind(target, collection)
    if target_kind not in INTERACTABLE_KINDS or (
        collection == "hazards" and target_kind != "hazard"
    ):
        return _result(
            state,
            ok=False,
            code="malformed_interactable",
            message="The target has an unsupported interactable kind.",
            **common,
        )
    revision = target.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return _result(
            state,
            ok=False,
            code="malformed_interactable",
            message="The target has an invalid revision.",
            **common,
        )
    if expected_revision is not None and revision != expected_revision:
        return _result(
            state,
            ok=False,
            code="stale_interactable_revision",
            message="The interactable changed after the player selected it.",
            details={
                "expectedRevision": expected_revision,
                "currentRevision": revision,
            },
            **common,
        )

    legality = _action_legality(
        target, collection, action, actor=actor_ctx, objects=objects
    )
    if not legality["ok"]:
        return _result(
            state,
            ok=False,
            code=legality["code"],
            message=legality["message"],
            details=legality.get("details"),
            **common,
        )

    authored_transitions, transition_error = _authored_transitions(target, action)
    if transition_error:
        return _result(
            state,
            ok=False,
            code="malformed_interactable",
            message=f"The target has malformed authored transitions: {transition_error}.",
            **common,
        )
    transitions = [*_controlled_transition(target, action), *authored_transitions]
    reveal_ids = _action_reveals(target, action)
    affected_ids = [transition["targetId"] for transition in transitions] + reveal_ids
    if target_id in affected_ids:
        return _result(
            state,
            ok=False,
            code="malformed_interactable",
            message="An interactable cannot transition or reveal itself by target reference.",
            **common,
        )
    if len(affected_ids) != len(set(affected_ids)):
        return _result(
            state,
            ok=False,
            code="malformed_interactable",
            message="An action references the same secondary target more than once.",
            **common,
        )
    for affected_id in affected_ids:
        if affected_id in duplicates or affected_id not in objects:
            return _result(
                state,
                ok=False,
                code="secondary_target_not_found",
                message="A required secondary interactable target is stale or ambiguous.",
                details={"secondaryTargetId": affected_id},
                **common,
            )

    before_by_id = {
        object_id: deepcopy(entry[0])
        for object_id, entry in objects.items()
        if object_id in {target_id, *affected_ids}
    }
    primary_patch = _primary_patch(target, collection, action)
    target.update(primary_patch)
    if (
        action == "open"
        and target_kind == "container"
        and target.get("revealsContentsOnOpen") is not False
    ):
        _mark_contents_known(target, include_hidden=False)
    if action == "search":
        _mark_contents_known(target, include_hidden=True)
    known_fields = _string_set(target.get("playerKnownFields")) | _reveal_fields(
        target, action
    )
    if known_fields:
        target["playerKnownFields"] = sorted(known_fields)
    target.update(_tracking_patch(target, request=dict(request), actor_id=actor_id))

    for transition in transitions:
        secondary, secondary_collection = objects[transition["targetId"]]
        candidate = {**secondary, **deepcopy(transition["patch"])}
        invariant_error = _invariant_error(candidate, secondary_collection)
        if invariant_error:
            return _result(
                state,
                ok=False,
                code="invalid_authored_transition",
                message=f"An authored transition would create contradictory state: {invariant_error}.",
                details={"secondaryTargetId": transition["targetId"]},
                **common,
            )
        secondary.update(deepcopy(transition["patch"]))
        secondary.update(
            _tracking_patch(secondary, request=dict(request), actor_id=actor_id)
        )

    for revealed_id in reveal_ids:
        revealed = objects[revealed_id][0]
        revealed.update({"playerKnown": True, "hidden": False})
        revealed.update(
            _tracking_patch(revealed, request=dict(request), actor_id=actor_id)
        )

    final_invariant = _invariant_error(target, collection)
    if final_invariant:
        return _result(
            state,
            ok=False,
            code="invalid_transition",
            message=f"The action would create contradictory state: {final_invariant}.",
            **common,
        )

    _sync_location_scene(location, scene)
    event_id = _stable_id("evt", action_id, action, target_id, location_id, actor_id)
    changes: list[dict[str, Any]] = []
    for changed_id in [target_id, *affected_ids]:
        changed, changed_collection = objects[changed_id]
        patch = _diff_patch(before_by_id[changed_id], changed)
        if not patch:
            continue
        changes.append(
            {
                "id": _stable_id("chg", event_id, changed_id),
                "type": "scene.interactable.update",
                "locationId": location_id,
                "collection": changed_collection,
                "targetId": changed_id,
                "patch": patch,
            }
        )

    event_type_prefix = "hazard" if target_kind == "hazard" else "interactable"
    event_verb = {
        "inspect": "inspected",
        "open": "opened",
        "close": "closed",
        "lock": "locked",
        "unlock": "unlocked",
        "search": "searched",
        "break": "broken",
        "use": "used",
        "disarm": "disarmed",
        "trigger": "triggered",
        "reset": "reset",
    }[action]
    event: dict[str, Any] = {
        "id": event_id,
        "type": f"{event_type_prefix}.{event_verb}",
        "actionId": action_id,
        "action": action,
        "actorId": actor_id,
        "locationId": location_id,
        "targetId": target_id,
        "targetKind": target_kind,
        "targetName": _text(target.get("name")) or target_id,
        "visibility": _text(target.get("eventVisibility")).lower()
        if _text(target.get("eventVisibility")).lower() in {"players", "actor", "gm"}
        else "players",
        "revealedTargetIds": reveal_ids,
    }
    mechanical_effects = target.get("mechanicalEffects")
    if isinstance(mechanical_effects, Mapping) and action in mechanical_effects:
        event["mechanicalEffects"] = deepcopy(mechanical_effects[action])

    _record_action(
        next_state, action_id=action_id, fingerprint=fingerprint, event_ids=[event_id]
    )
    changes.append(
        {
            "id": _stable_id("chg", event_id, "ledger"),
            "type": "interactable.action.recorded",
            "actionId": action_id,
            "fingerprint": fingerprint,
            "eventIds": [event_id],
        }
    )
    return _result(
        state,
        ok=True,
        code="applied",
        message="The interactable action was applied.",
        changes=changes,
        events=[event],
        next_state=next_state,
        **common,
    )


def available_actions_for_target(
    state: Mapping[str, Any] | Any,
    *,
    location_id: str,
    target_id: str,
    actor: Mapping[str, Any] | Any,
) -> dict[str, Any]:
    """Return action legality for a visible exact-ID target without mutation."""

    if not isinstance(state, Mapping) or not isinstance(actor, Mapping):
        return {"ok": False, "code": "invalid_request", "actions": []}
    working = deepcopy(dict(state))
    scene = working.get("currentScene")
    if not isinstance(scene, dict) or _text(scene.get("locationId")) != _text(
        location_id
    ):
        return {"ok": False, "code": "location_mismatch", "actions": []}
    location, error = _find_current_location(working, _text(location_id))
    if error or location is None:
        return {"ok": False, "code": error or "location_not_found", "actions": []}
    scene, error = _hydrate_current_scene(working, location)
    if error or scene is None:
        return {"ok": False, "code": error or "invalid_current_scene", "actions": []}
    objects, duplicates, error = _index_scene(scene)
    target_entry = objects.get(_text(target_id))
    actor_ctx = _actor_context(actor)
    persisted_matches = _location_target_appearances(location, _text(target_id))
    mirror_conflict = bool(
        len(persisted_matches) == 1
        and target_entry is not None
        and _entry_kind(persisted_matches[0][0], persisted_matches[0][1])
        != _entry_kind(target_entry[0], target_entry[1])
    )
    if (
        error
        or _text(target_id) in duplicates
        or len(persisted_matches) > 1
        or mirror_conflict
        or _target_appears_in_remote_location(
            working, _text(target_id), _text(location_id)
        )
        or target_entry is None
        or not _target_is_visible(target_entry[0], actor_ctx)
    ):
        return {"ok": False, "code": error or "interactable_not_found", "actions": []}
    target, collection = target_entry
    actions = []
    for action in sorted(_candidate_actions(target, collection)):
        legality = _action_legality(
            target, collection, action, actor=actor_ctx, objects=objects
        )
        entry: dict[str, Any] = {
            "action": action,
            "legal": legality["ok"],
            "code": "legal" if legality["ok"] else legality["code"],
        }
        if legality.get("code") == "check_required":
            entry["requiresCheck"] = True
            entry["check"] = deepcopy((legality.get("details") or {}).get("check"))
        actions.append(entry)
    return {
        "ok": True,
        "code": "resolved",
        "targetId": _text(target_id),
        "actions": actions,
    }


def _project_content_item(item: dict[str, Any], *, gm: bool) -> dict[str, Any] | None:
    if not gm and (
        item.get("gmOnly") is True
        or (item.get("hidden") is True and item.get("playerKnown") is not True)
    ):
        return None
    if gm:
        return deepcopy(item)
    projected = {
        key: deepcopy(item.get(key))
        for key in ("id", "name", "quantity", "type", "description", "playerKnown")
        if key in item
    }
    return projected or None


def _project_entry(
    entry: dict[str, Any], collection: str, *, viewer: dict[str, Any]
) -> dict[str, Any] | None:
    if viewer["isGm"]:
        return deepcopy(entry)
    entry_id = _text(entry.get("id"))
    if entry.get("gmOnly") is True:
        return None
    if (
        entry.get("hidden") is True
        and entry.get("playerKnown") is not True
        and entry_id not in viewer["knownIds"]
    ):
        return None
    hidden_fields = _string_set(entry.get("hiddenFields"))
    known_fields = _string_set(entry.get("playerKnownFields"))
    permitted = (
        (_PUBLIC_BASE_FIELDS | known_fields) - hidden_fields - _NEVER_PUBLIC_FIELDS
    )
    projected = {
        key: deepcopy(entry.get(key)) for key in sorted(permitted) if key in entry
    }
    projected["id"] = entry_id
    projected["kind"] = _entry_kind(entry, collection)
    player_description = _text(entry.get("playerDescription"))
    if player_description:
        projected["description"] = player_description
    contents = entry.get("contents")
    if isinstance(contents, list) and (
        entry.get("contentsKnown") is True or entry.get("searched") is True
    ):
        projected_contents = [
            projected_item
            for item in contents
            if isinstance(item, dict)
            for projected_item in [_project_content_item(item, gm=False)]
            if projected_item is not None
        ]
        projected["contents"] = projected_contents
    return projected


def project_scene_interactables(
    state: Mapping[str, Any] | Any,
    viewer: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Return a fail-closed player or GM projection of the active scene objects."""

    empty = {"interactables": [], "hazards": []}
    if not isinstance(state, Mapping):
        return empty
    working = deepcopy(dict(state))
    scene = working.get("currentScene")
    if not isinstance(scene, dict):
        return empty
    location_id = _text(scene.get("locationId"))
    location, error = _find_current_location(working, location_id)
    if error or location is None:
        return empty
    scene, error = _hydrate_current_scene(working, location)
    if error or scene is None:
        return empty
    _, duplicates, error = _index_scene(scene)
    if error:
        return empty
    for collection in COLLECTIONS:
        for entry in scene[collection]:
            entry_id = _text(entry.get("id"))
            persisted_matches = _location_target_appearances(location, entry_id)
            if (
                len(persisted_matches) > 1
                or _target_appears_in_remote_location(working, entry_id, location_id)
                or (
                    len(persisted_matches) == 1
                    and _entry_kind(persisted_matches[0][0], persisted_matches[0][1])
                    != _entry_kind(entry, collection)
                )
            ):
                duplicates.add(entry_id)
    viewer_ctx = _actor_context(viewer or {})
    projected: dict[str, list[dict[str, Any]]] = {"interactables": [], "hazards": []}
    for collection in COLLECTIONS:
        for entry in scene[collection]:
            if _text(entry.get("id")) in duplicates:
                continue
            value = _project_entry(entry, collection, viewer=viewer_ctx)
            if value is not None:
                projected[collection].append(value)
    return projected


def project_interactable_event(
    event: Mapping[str, Any] | Any,
    viewer: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Project one internal event without leaking mechanical or GM-only payloads."""

    if not isinstance(event, Mapping):
        return None
    viewer_ctx = _actor_context(viewer or {})
    visibility = _text(event.get("visibility")).lower() or "players"
    if not viewer_ctx["isGm"]:
        if visibility == "gm":
            return None
        if visibility == "actor" and viewer_ctx["id"] != _text(event.get("actorId")):
            return None
    if viewer_ctx["isGm"]:
        return deepcopy(dict(event))
    public_fields = (
        "id",
        "type",
        "actionId",
        "action",
        "actorId",
        "locationId",
        "targetId",
        "targetKind",
        "targetName",
        "visibility",
        "revealedTargetIds",
    )
    return {
        field: deepcopy(event.get(field)) for field in public_fields if field in event
    }


def validate_interactable_catalog(
    state: Mapping[str, Any] | Any,
) -> list[dict[str, Any]]:
    """Return deterministic authoring errors for active and persisted objects."""

    if not isinstance(state, Mapping):
        return [
            {
                "code": "invalid_state",
                "path": "$",
                "message": "State must be an object.",
            }
        ]
    errors: list[dict[str, Any]] = []
    locations = state.get("locations")
    if not isinstance(locations, list):
        return [
            {
                "code": "invalid_locations",
                "path": "locations",
                "message": "locations must be a list.",
            }
        ]

    location_ids: set[str] = set()
    global_ids: dict[str, str] = {}
    for index, location in enumerate(locations):
        path = f"locations[{index}]"
        if not isinstance(location, dict):
            errors.append(
                {
                    "code": "invalid_location",
                    "path": path,
                    "message": "Location must be an object.",
                }
            )
            continue
        location_id = _text(location.get("id"))
        if not location_id:
            errors.append(
                {
                    "code": "missing_location_id",
                    "path": f"{path}.id",
                    "message": "Location ID is required.",
                }
            )
            continue
        if location_id in location_ids:
            errors.append(
                {
                    "code": "duplicate_location_id",
                    "path": f"{path}.id",
                    "message": "Location IDs must be unique.",
                }
            )
        location_ids.add(location_id)
        scope = {
            collection: _location_collection(location, collection)
            for collection in COLLECTIONS
        }
        local_ids: dict[str, str] = {}
        local_objects: dict[str, dict[str, Any]] = {}
        for collection in COLLECTIONS:
            records = scope[collection]
            for record_index, record in enumerate(records):
                record_path = f"{path}.sceneState.{collection}[{record_index}]"
                if not isinstance(record, dict):
                    errors.append(
                        {
                            "code": "malformed_interactable",
                            "path": record_path,
                            "message": "Entry must be an object.",
                        }
                    )
                    continue
                record_id = _text(record.get("id"))
                if not record_id:
                    errors.append(
                        {
                            "code": "missing_interactable_id",
                            "path": f"{record_path}.id",
                            "message": "Exact ID is required.",
                        }
                    )
                    continue
                if record_id in local_ids:
                    errors.append(
                        {
                            "code": "duplicate_interactable_id",
                            "path": f"{record_path}.id",
                            "message": f"ID duplicates {local_ids[record_id]}.",
                        }
                    )
                else:
                    local_ids[record_id] = record_path
                    local_objects[record_id] = record
                previous_location = global_ids.get(record_id)
                if previous_location and previous_location != location_id:
                    errors.append(
                        {
                            "code": "duplicate_interactable_id",
                            "path": f"{record_path}.id",
                            "message": f"ID is also used in location {previous_location}.",
                        }
                    )
                global_ids[record_id] = location_id
                kind = _entry_kind(record, collection)
                if kind not in INTERACTABLE_KINDS or (
                    collection == "hazards" and kind != "hazard"
                ):
                    errors.append(
                        {
                            "code": "unsupported_kind",
                            "path": f"{record_path}.kind",
                            "message": "Unsupported kind.",
                        }
                    )
                invariant_error = _invariant_error(record, collection)
                if invariant_error:
                    errors.append(
                        {
                            "code": "contradictory_state",
                            "path": record_path,
                            "message": invariant_error,
                        }
                    )
                requirement_error = _requirement_shape_error(record.get("requirements"))
                if requirement_error:
                    errors.append(
                        {
                            "code": "malformed_requirements",
                            "path": f"{record_path}.requirements",
                            "message": requirement_error,
                        }
                    )
                for action in INTERACTABLE_ACTIONS:
                    _, transition_error = _authored_transitions(record, action)
                    if transition_error:
                        errors.append(
                            {
                                "code": "malformed_transition",
                                "path": f"{record_path}.transitions.{action}",
                                "message": transition_error,
                            }
                        )
        for record_id, record in local_objects.items():
            controlled_id = _text(record.get("controlsTargetId"))
            if controlled_id and (
                controlled_id == record_id or controlled_id not in local_objects
            ):
                errors.append(
                    {
                        "code": "stale_secondary_target",
                        "path": f"{local_ids[record_id]}.controlsTargetId",
                        "message": "Controlled target must be another exact ID in the same location.",
                    }
                )
            referenced_ids = set()
            for action in INTERACTABLE_ACTIONS:
                transitions, _ = _authored_transitions(record, action)
                referenced_ids.update(
                    transition["targetId"] for transition in transitions
                )
                referenced_ids.update(_action_reveals(record, action))
            for referenced_id in sorted(referenced_ids):
                if referenced_id == record_id or referenced_id not in local_objects:
                    errors.append(
                        {
                            "code": "stale_secondary_target",
                            "path": local_ids[record_id],
                            "message": f"Secondary target {referenced_id!r} is absent or self-referential.",
                        }
                    )

    scene = state.get("currentScene")
    if scene is not None and not isinstance(scene, Mapping):
        errors.append(
            {
                "code": "invalid_current_scene",
                "path": "currentScene",
                "message": "currentScene must be an object.",
            }
        )
    elif isinstance(scene, Mapping):
        current_location_id = _text(scene.get("locationId"))
        matching_locations = [
            location
            for location in locations
            if isinstance(location, dict)
            and _text(location.get("id")) == current_location_id
        ]
        active_ids: dict[str, str] = {}
        for collection in COLLECTIONS:
            records = scene.get(collection)
            if records is None:
                continue
            if not isinstance(records, list):
                errors.append(
                    {
                        "code": f"invalid_{collection}_collection",
                        "path": f"currentScene.{collection}",
                        "message": f"{collection} must be a list.",
                    }
                )
                continue
            for index, record in enumerate(records):
                record_path = f"currentScene.{collection}[{index}]"
                if not isinstance(record, dict):
                    errors.append(
                        {
                            "code": "malformed_interactable",
                            "path": record_path,
                            "message": "Entry must be an object.",
                        }
                    )
                    continue
                record_id = _text(record.get("id"))
                if not record_id:
                    errors.append(
                        {
                            "code": "missing_interactable_id",
                            "path": f"{record_path}.id",
                            "message": "Exact ID is required.",
                        }
                    )
                    continue
                if record_id in active_ids:
                    errors.append(
                        {
                            "code": "duplicate_interactable_id",
                            "path": f"{record_path}.id",
                            "message": f"ID duplicates {active_ids[record_id]}.",
                        }
                    )
                else:
                    active_ids[record_id] = record_path
                invariant_error = _invariant_error(record, collection)
                if invariant_error:
                    errors.append(
                        {
                            "code": "contradictory_state",
                            "path": record_path,
                            "message": invariant_error,
                        }
                    )
                requirement_error = _requirement_shape_error(record.get("requirements"))
                if requirement_error:
                    errors.append(
                        {
                            "code": "malformed_requirements",
                            "path": f"{record_path}.requirements",
                            "message": requirement_error,
                        }
                    )
                if len(matching_locations) == 1:
                    mirrors = _location_target_appearances(
                        matching_locations[0], record_id
                    )
                    if len(mirrors) == 1 and _entry_kind(
                        mirrors[0][0], mirrors[0][1]
                    ) != _entry_kind(record, collection):
                        errors.append(
                            {
                                "code": "interactable_mirror_conflict",
                                "path": record_path,
                                "message": "Active and persisted copies disagree about the interactable kind.",
                            }
                        )

    return sorted(
        errors, key=lambda error: (error["path"], error["code"], error["message"])
    )
