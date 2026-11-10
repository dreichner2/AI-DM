from __future__ import annotations

import base64
import binascii
import json

from flask import jsonify

from aidm_server.validation import coerce_int


class InvalidCursorError(ValueError):
    """A page cursor this API did not issue."""


def clamp_limit(limit: int | None, *, maximum: int = 500) -> int | None:
    if limit is None:
        return None
    return max(1, min(maximum, int(limit)))


def page_limit(raw_value, *, default: int, maximum: int) -> int:
    limit = coerce_int(raw_value)
    if limit is None:
        return default
    return max(1, min(maximum, limit))


def encode_cursor(*values) -> str:
    """Return an opaque cursor for a page's last sort key."""

    raw = json.dumps(list(values), separators=(',', ':')).encode('utf-8')
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def decode_cursor(token: str, *, size: int) -> tuple:
    """Return the sort key ``encode_cursor`` wrapped, or raise ``InvalidCursorError``."""

    try:
        raw = base64.urlsafe_b64decode(token.encode('ascii') + b'=' * (-len(token) % 4))
        values = json.loads(raw)
    except (binascii.Error, UnicodeError, ValueError):
        raise InvalidCursorError('cursor is invalid.') from None
    if not isinstance(values, list) or len(values) != size:
        raise InvalidCursorError('cursor is invalid.')
    return tuple(values)


def limited_page(query, *, limit: int | None):
    capped_limit = clamp_limit(limit)
    if capped_limit is None:
        return PageItems(query.all(), has_more=False)
    rows = query.limit(capped_limit + 1).all()
    return PageItems(rows[:capped_limit], has_more=len(rows) > capped_limit)


def jsonify_page(items, *, payload_for, cursor_for):
    response = jsonify([payload_for(item) for item in items])
    response.headers['X-AIDM-Has-More'] = 'true' if getattr(items, '_has_more', False) else 'false'
    if getattr(items, '_has_more', False) and items:
        response.headers['X-AIDM-Next-Cursor'] = str(cursor_for(items[-1]))
    return response


class PageItems(list):
    def __init__(self, rows, *, has_more: bool):
        super().__init__(rows)
        self._has_more = has_more
