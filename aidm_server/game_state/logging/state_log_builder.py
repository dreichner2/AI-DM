from __future__ import annotations

from typing import Any

from aidm_server.canon_text import int_or_default

GENERIC_EXTRACTED_REASON = 'Extracted from DM response.'


def _change_type(change: dict[str, Any]) -> str:
    return str(change.get('type') or '').strip()


def _item_name(change: dict[str, Any]) -> str:
    item = change.get('item') if isinstance(change.get('item'), dict) else {}
    return str(change.get('itemName') or change.get('item_name') or item.get('name') or 'Item')


def _amount(change: dict[str, Any], key: str = 'amount') -> int:
    return max(0, int_or_default(change.get('actualAmount', change.get(key)), default=0))


def _location_name(change: dict[str, Any]) -> str:
    return str(change.get('locationName') or change.get('name') or change.get('locationId') or 'Location')


def _quest_title(change: dict[str, Any]) -> str:
    return str(change.get('questTitle') or change.get('title') or change.get('name') or change.get('questId') or 'Quest')


def _npc_name(change: dict[str, Any]) -> str:
    return str(change.get('npcName') or change.get('name') or change.get('npcId') or 'NPC')


def _flag_key(change: dict[str, Any]) -> str:
    return str(change.get('flagKey') or change.get('key') or 'flag')


def _transfer_message(change: dict[str, Any], *, quantity: int) -> str:
    from_name = str(change.get('fromActorName') or change.get('from_actor_name') or '').strip()
    to_name = str(change.get('toActorName') or change.get('to_actor_name') or '').strip()
    if not from_name or not to_name:
        return ''
    change_type = _change_type(change)
    if change_type in {'inventory.add', 'inventory.remove'}:
        return f"Transferred {_item_name(change)} x{quantity} from {from_name} to {to_name}."
    if change_type in {'currency.add', 'currency.remove'}:
        return f"Transferred {_amount(change)} {str(change.get('currency') or '').lower()} from {from_name} to {to_name}."
    return ''


def _change_message(change: dict[str, Any], *, status: str, reason: str | None = None) -> str:
    change_type = _change_type(change)
    quantity = max(1, int_or_default(change.get('quantity'), default=1))
    if change.get('transferId'):
        transfer_message = _transfer_message(change, quantity=quantity)
        if transfer_message:
            return transfer_message
        change_reason = str(change.get('reason') or '').strip()
        if change_reason and change_reason != GENERIC_EXTRACTED_REASON:
            return change_reason
    if change_type == 'inventory.add':
        return f"Added {_item_name(change)} x{quantity}."
    if change_type == 'inventory.remove':
        return f"Removed {_item_name(change)} x{quantity}."
    if change_type == 'inventory.equip':
        slot = str(change.get('slotLabel') or change.get('slot') or 'equipment').replace('_', ' ')
        conflicts = change.get('conflictItemNames') if isinstance(change.get('conflictItemNames'), list) else []
        conflict_suffix = f" Unequipped {', '.join(str(item) for item in conflicts if item)}." if conflicts else ''
        return f"Equipped {_item_name(change)} in {slot}.{conflict_suffix}"
    if change_type == 'inventory.unequip':
        return f"Unequipped {_item_name(change)}."
    if change_type == 'currency.add':
        return f"Added {_amount(change)} {str(change.get('currency') or '').lower()}."
    if change_type == 'currency.remove':
        return f"Removed {_amount(change)} {str(change.get('currency') or '').lower()}."
    if change_type == 'health.heal':
        suffix = ' (capped at max).' if status == 'modified' else '.'
        return f"Restored {_amount(change)} HP{suffix}"
    if change_type == 'health.damage':
        return f"Took {_amount(change)} damage."
    if change_type == 'xp.add':
        return f"Added {_amount(change)} XP."
    if change_type == 'xp.remove':
        suffix = ' (capped at current XP).' if status == 'modified' else '.'
        return f"Removed {_amount(change)} XP{suffix}"
    if change_type == 'spell.learn':
        spell_payload = change.get('spell') if isinstance(change.get('spell'), dict) else {}
        spell_name = str(change.get('spellName') or change.get('spell_name') or spell_payload.get('name') or 'spell').strip()
        if change.get('alreadyKnown'):
            return f"Already knew {spell_name}."
        return f"Learned spell: {spell_name}."
    if change_type == 'inventory.mark_used':
        return f"Marked {_item_name(change)} as recently used."
    if change_type == 'scene.update':
        scene_name = str(change.get('name') or change.get('sceneName') or '').strip()
        if scene_name:
            return f"Updated scene: {scene_name}."
        mood = str(change.get('mood') or '').strip()
        if mood:
            return f"Updated scene mood: {mood}."
        return 'Updated scene.'
    if change_type == 'scene.move_location':
        return f"Moved scene to {_location_name(change)}."
    if change_type == 'location.discover':
        return f"Discovered location: {_location_name(change)}."
    if change_type == 'location.update':
        return f"Updated location: {_location_name(change)}."
    if change_type == 'location.connect':
        return f"Connected locations: {_location_name(change)} and {change.get('connectedLocationName') or change.get('connectedLocationId') or 'Location'}."
    if change_type == 'quest.add':
        return f"Added quest: {_quest_title(change)}."
    if change_type == 'quest.update':
        stage = str(change.get('stage') or '').strip()
        return f"Updated quest: {_quest_title(change)}{f' - {stage}' if stage else ''}."
    if change_type == 'quest.objective.add':
        return f"Added objective for quest: {_quest_title(change)}."
    if change_type == 'quest.objective.update':
        return f"Updated objective for quest: {_quest_title(change)}."
    if change_type == 'quest.complete':
        return f"Completed quest: {_quest_title(change)}."
    if change_type == 'quest.fail':
        return f"Failed quest: {_quest_title(change)}."
    if change_type == 'npc.discover':
        return f"Discovered NPC: {_npc_name(change)}."
    if change_type == 'npc.update':
        return f"Updated NPC: {_npc_name(change)}."
    if change_type == 'npc.move':
        return f"Moved NPC: {_npc_name(change)}."
    if change_type == 'npc.relationship.update':
        return f"Updated NPC relationship: {_npc_name(change)}."
    if change_type == 'flag.set':
        return f"Set flag: {_flag_key(change)}."
    if change_type == 'flag.unset':
        return f"Unset flag: {_flag_key(change)}."
    if reason:
        return reason
    return f"{change_type or 'state'} update applied."


def _line(status: str, change: dict[str, Any], message: str, *, visible: bool | None = None) -> dict[str, Any]:
    return {
        'status': status,
        'message': message,
        'changeType': _change_type(change),
        'visibleToPlayer': bool(change.get('visible', True) if visible is None else visible),
        'changeId': change.get('id'),
    }


def _lines_from_validation(validation: dict[str, Any]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    visible_transfer_ids: set[str] = set()
    for entry in validation.get('accepted') or []:
        if isinstance(entry, dict) and isinstance(entry.get('change'), dict):
            change = entry['change']
            transfer_id = str(change.get('transferId') or '').strip()
            if transfer_id:
                if transfer_id in visible_transfer_ids:
                    continue
                visible_transfer_ids.add(transfer_id)
            line = _line('applied', change, _change_message(change, status='applied'))
            if line['visibleToPlayer']:
                lines.append(line)
    for entry in validation.get('modified') or []:
        if isinstance(entry, dict) and isinstance(entry.get('modifiedChange'), dict):
            change = entry['modifiedChange']
            reason = str(entry.get('reason') or '')
            line = _line('modified', change, _change_message(change, status='modified', reason=reason))
            if line['visibleToPlayer']:
                lines.append(line)
    for entry in validation.get('rejected') or []:
        if isinstance(entry, dict) and isinstance(entry.get('change'), dict):
            change = entry['change']
            reason = str(entry.get('reason') or 'Change rejected.')
            lines.append(_line('rejected', change, f"Could not apply {change.get('type')}: {reason}"))
    return lines


def _lines_from_pre_validation(pre_validation: dict[str, Any]) -> list[dict[str, Any]]:
    lines: list[dict[str, Any]] = []
    for result in pre_validation.get('validatedActions') or []:
        if not isinstance(result, dict) or result.get('status') != 'invalid':
            continue
        original = result.get('originalAction') if isinstance(result.get('originalAction'), dict) else {}
        action_type = str(original.get('type') or 'action')
        source = str(original.get('sourceText') or original.get('summary') or action_type)
        reason = str(result.get('reason') or 'Action rejected.')
        lines.append(
            {
                'status': 'rejected',
                'message': f"{source} rejected: {reason}",
                'changeType': action_type,
                'visibleToPlayer': True,
                'changeId': result.get('actionId'),
            }
        )
    return lines


def build_state_log(
    *,
    turn_id: int,
    pre_validation: dict[str, Any] | None = None,
    immediate_validation: dict[str, Any] | None = None,
    post_validation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    lines = []
    if pre_validation:
        lines.extend(_lines_from_pre_validation(pre_validation))
    if immediate_validation:
        lines.extend(_lines_from_validation(immediate_validation))
    if post_validation:
        lines.extend(_lines_from_validation(post_validation))

    seen = set()
    deduped = []
    for line in lines:
        key = (line.get('status'), line.get('message'), line.get('changeId'))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(line)

    return {
        'turnId': turn_id,
        'lines': deduped,
    }


def state_log_message(state_log: dict[str, Any]) -> str:
    lines = [
        str(line.get('message') or '').strip()
        for line in state_log.get('lines') or []
        if isinstance(line, dict) and line.get('visibleToPlayer', True) and str(line.get('message') or '').strip()
    ]
    if not lines:
        return ''
    return 'State updated:\n' + '\n'.join(f'- {line}' for line in lines)
