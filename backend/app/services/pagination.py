"""Keyset pagination cursors.

Every list endpoint paginates on a time-ordered UUID rather than an offset, so
the cursor is just that id in a URL-safe form. The helpers live here rather than
in whichever service happened to need them first: catalog, pricebook and
purchases all use them, and keeping them in catalog made pricebook import
catalog, which made catalog import pricebook back inside a function body.
"""

from __future__ import annotations

import base64
import json
import uuid

from app.core.errors import ApiError


def encode_cursor(value: uuid.UUID) -> str:
    return base64.urlsafe_b64encode(value.bytes).decode().rstrip("=")


def decode_cursor(cursor: str | None) -> uuid.UUID | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return uuid.UUID(bytes=base64.urlsafe_b64decode(padded))
    except (ValueError, TypeError) as exc:
        raise ApiError(400, "bad_cursor", "The cursor is not valid.") from exc


def encode_keyset(sort_value: str, value: uuid.UUID) -> str:
    """A cursor for a list ordered by (sort_value, id), carrying both.

    The sort value travels in the cursor rather than being looked up again, so a
    row renamed between two page loads cannot move the page boundary.
    """
    raw = json.dumps([sort_value, str(value)], separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_keyset(cursor: str | None) -> tuple[str, uuid.UUID] | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        sort_value, value = json.loads(base64.urlsafe_b64decode(padded))
        if not isinstance(sort_value, str):
            raise ValueError("sort value")
        return sort_value, uuid.UUID(value)
    except (ValueError, TypeError) as exc:
        raise ApiError(400, "bad_cursor", "The cursor is not valid.") from exc
