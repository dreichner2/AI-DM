from __future__ import annotations

from typing import Any

from aidm_server.canon_text import int_or_default


def _change_type(change: dict[str, Any]) -> str:
    return str(change.get('type') or '').strip()


def _item_name(change: dict[str, Any]) -> str:
    item = change.get('item') if isinstance(change.get('item'), dict) else {}
    return str(change.get('itemName') or change.get('item_name') or item.get('name') or 'Item')


def _amount(change: dict[str, Any], key: str = 'amount') -> int:
    return max(0, int_or_default(change.get('actualAmount', change.get(key)), default=0))


def _change_message(change: dict[str, Any], *, status: str, reason: str | None = None) -> str:
    change_type = _change_type(change)
    quantity = max(1, int_or_default(change.get('quantity'), default=1))
    if change.get('transferId') and str(change.get('reason') or '').strip():
        return str(change.get('reason')).strip()
    if change_type == 'inventory.add':
        return f"Added {_item_name(change)} x{quantity}."
    if change_type == 'inventory.remove':
        return f"Removed {_item_name(change)} x{quantity}."
    if change_type == 'currency.add':
        return f"Added {_amount(change)} {str(change.get('currency') or '').lower()}."
    if change_type == 'currency.remove':
        return f"Removed {_amount(change)} {str(change.get('currency') or '').lower()}."
    if change_type == 'health.heal':
        suffix = ' (capped at max).' if status == 'modified' else '.'
        return f"Restored {_amount(change)} HP{suffix}"
    if change_type == 'health.damage':
        return f"Took {_amount(change)} damage."
    if change_type == 'inventory.mark_used':
        return f"Marked {_item_name(change)} as recently used."
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
    for entry in validation.get('accepted') or []:
        if isinstance(entry, dict) and isinstance(entry.get('change'), dict):
            change = entry['change']
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
