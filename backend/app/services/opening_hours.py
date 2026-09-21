"""OSM ``opening_hours`` strings: validation on save, evaluation in the household zone.

Parsing and evaluation are delegated to ``opening-hours-py`` (MIT OR Apache-2.0).
Every instant is converted to ``HOUSEHOLD_TIMEZONE`` before evaluation, so
"08:00" means eight o'clock where the household lives, on both sides of a
daylight-saving change.
"""

from __future__ import annotations

import re
from datetime import datetime
from functools import lru_cache
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from opening_hours import OpeningHours, ParserError, State

from app.core.config import get_settings

if TYPE_CHECKING:
    from app.models.geo import VendorLocation

_POSITION = re.compile(r"-->\s*\d+:(\d+)")
_EXPECTED = re.compile(r"^\s*=\s*(.+?)\s*$", re.MULTILINE)


class OpeningHoursError(ValueError):
    """The string is not valid opening_hours syntax. ``message`` says where and why."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def household_zone() -> ZoneInfo:
    return ZoneInfo(get_settings().household_timezone)


def _describe(text: str, exc: ParserError) -> str:
    raw = str(exc)
    position = _POSITION.search(raw)
    expected = _EXPECTED.search(raw)
    where = f" at column {position.group(1)}" if position else ""
    why = f": {expected.group(1)}" if expected else ""
    return f"Invalid opening_hours syntax{where}{why}."


def normalize_hours(text: str | None) -> str | None:
    """Strip and validate; ``None`` stays ``None``. Raises OpeningHoursError."""
    if text is None:
        return None
    stripped = text.strip()
    if not stripped:
        raise OpeningHoursError("opening_hours must not be empty; omit it or send null.")
    try:
        _parsed(stripped, household_zone().key)
    except ParserError as exc:
        raise OpeningHoursError(_describe(stripped, exc)) from None
    return stripped


def validate_hours(text: str) -> str | None:
    """Return an error message, or ``None`` when the string is valid."""
    try:
        normalize_hours(text)
    except OpeningHoursError as exc:
        return exc.message
    return None


@lru_cache(maxsize=1024)
def _parsed(text: str, tz_key: str) -> OpeningHours:
    return OpeningHours(text, timezone=ZoneInfo(tz_key))


def to_household(instant: datetime) -> datetime:
    """Attach the household zone to a naive instant, or convert an aware one into it."""
    tz = household_zone()
    if instant.tzinfo is None:
        return instant.replace(tzinfo=tz)
    return instant.astimezone(tz)


def evaluate(text: str, instant: datetime) -> bool:
    """Is a place with these hours open at ``instant``? ``unknown`` counts as not open."""
    local = to_household(instant)
    state, _comment = _parsed(text, local.tzinfo.key).state(local)  # type: ignore[union-attr]
    return state == State.OPEN


def effective_hours(location: VendorLocation) -> tuple[str | None, bool]:
    """(hours, inherited). A stall without hours of its own uses its parent's."""
    if location.opening_hours is not None:
        return location.opening_hours, False
    parent = location.parent
    if parent is not None and parent.opening_hours is not None:
        return parent.opening_hours, True
    return None, False


def is_open_at(location: VendorLocation, instant: datetime) -> bool | None:
    """``None`` when the location's hours are unknown; requires ``location.parent`` loaded."""
    hours, _ = effective_hours(location)
    if hours is None:
        return None
    return evaluate(hours, instant)
