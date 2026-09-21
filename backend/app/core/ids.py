"""UUID v7 primary keys: time-ordered, generated in the application."""

from __future__ import annotations

import uuid

from uuid6 import uuid7


def new_id() -> uuid.UUID:
    return uuid7()
