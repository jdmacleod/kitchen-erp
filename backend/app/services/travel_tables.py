"""The drive-time arithmetic (17, 4B), pure and importable by configuration.

Three tables live here as settings strings, parsed once and validated at boot:

- ``PLAN_ESTIMATE_SPEED_BANDS`` (``3:30,15:40,:60``): an upper bound in km and a
  speed in km/h per band, the last band unbounded. A band covers straight-line
  distances below its bound: 30 km/h under 3 km, 40 km/h to 15 km, 60 beyond.
- ``PLAN_ESTIMATE_ROAD_FACTOR`` (``1.3``): road distance over straight-line.
- ``PLAN_TRAFFIC_MULTIPLIERS`` (``weekday=06-10:1.35,10-15:1.15,15-20:1.45;
  weekend=09-19:1.15``): hour bands in ``household_timezone`` and the factor
  free-flow time is multiplied by inside each; 1.0 outside every band.

An estimate is haversine times the road factor at the band's speed. It is what a
leg gets when the routing engine is off or does not answer; a stored leg is
always free-flow, and the multiplier applies at planning time, per leg, at the
leg's projected departure (P2). Nothing here touches the database or the
network; ``services/travel.py`` does the I/O.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from functools import lru_cache
from zoneinfo import ZoneInfo

EARTH_RADIUS_M = Decimal("6371000")
_HOURS = re.compile(r"^(\d{1,2})-(\d{1,2})$")
DAY_KINDS = ("weekday", "weekend")


@dataclass(frozen=True)
class SpeedBand:
    upper_km: Decimal | None  # straight-line distances below this; None = unbounded
    speed_kmh: Decimal


@dataclass(frozen=True)
class HourBand:
    start: int  # inclusive hour, 0-23
    end: int  # exclusive hour, 1-24
    multiplier: Decimal


@dataclass(frozen=True)
class Multipliers:
    weekday: tuple[HourBand, ...]
    weekend: tuple[HourBand, ...]


def _decimal(text: str, what: str) -> Decimal:
    try:
        value = Decimal(text.strip())
    except InvalidOperation as exc:
        raise ValueError(f"{what}: {text!r} is not a number") from exc
    if not value.is_finite() or value <= 0:
        raise ValueError(f"{what}: {text!r} must be a positive number")
    return value


@lru_cache(maxsize=8)
def speed_bands(text: str) -> tuple[SpeedBand, ...]:
    """Parse ``PLAN_ESTIMATE_SPEED_BANDS``; raises ``ValueError`` with the reason."""
    bands: list[SpeedBand] = []
    for part in text.split(","):
        if ":" not in part:
            raise ValueError(f"speed bands: {part!r} is not 'km:kmh'")
        bound, speed = part.split(":", 1)
        upper = None if not bound.strip() else _decimal(bound, "speed bands: upper bound")
        bands.append(SpeedBand(upper, _decimal(speed, "speed bands: speed")))
    if not bands or bands[-1].upper_km is not None:
        raise ValueError("speed bands: the last band must be unbounded (':60')")
    bounded = [b.upper_km for b in bands[:-1]]
    if any(b is None for b in bounded):
        raise ValueError("speed bands: only the last band may be unbounded")
    if any(a >= b for a, b in zip(bounded, bounded[1:], strict=False)):  # noqa: RUF007
        raise ValueError("speed bands: upper bounds must increase")
    return tuple(bands)


def _hour_bands(text: str, kind: str) -> tuple[HourBand, ...]:
    bands: list[HourBand] = []
    for part in filter(None, (p.strip() for p in text.split(","))):
        if ":" not in part:
            raise ValueError(f"multipliers ({kind}): {part!r} is not 'HH-HH:factor'")
        hours, factor = part.rsplit(":", 1)
        m = _HOURS.match(hours.strip())
        if m is None:
            raise ValueError(f"multipliers ({kind}): {hours!r} is not 'HH-HH'")
        start, end = int(m.group(1)), int(m.group(2))
        if not (0 <= start < end <= 24):
            raise ValueError(f"multipliers ({kind}): {hours!r} must satisfy 0 <= start < end <= 24")
        bands.append(HourBand(start, end, _decimal(factor, f"multipliers ({kind}): factor")))
    bands.sort(key=lambda b: b.start)
    for a, b in zip(bands, bands[1:], strict=False):  # noqa: RUF007
        if b.start < a.end:
            raise ValueError(
                f"multipliers ({kind}): bands {a.start}-{a.end} and {b.start}-{b.end} overlap"
            )
    return tuple(bands)


@lru_cache(maxsize=8)
def multipliers(text: str) -> Multipliers:
    """Parse ``PLAN_TRAFFIC_MULTIPLIERS``; a day kind left out has no bands (×1.0)."""
    by_kind: dict[str, tuple[HourBand, ...]] = {}
    for group in filter(None, (g.strip() for g in text.split(";"))):
        if "=" not in group:
            raise ValueError(f"multipliers: {group!r} is not 'weekday=...' or 'weekend=...'")
        kind, bands = group.split("=", 1)
        kind = kind.strip().lower()
        if kind not in DAY_KINDS:
            raise ValueError(f"multipliers: unknown day kind {kind!r}")
        if kind in by_kind:
            raise ValueError(f"multipliers: {kind} given twice")
        by_kind[kind] = _hour_bands(bands, kind)
    return Multipliers(weekday=by_kind.get("weekday", ()), weekend=by_kind.get("weekend", ()))


# --- distance -------------------------------------------------------------------------


def haversine_m(lat1: Decimal, lon1: Decimal, lat2: Decimal, lon2: Decimal) -> int:
    """Great-circle metres between two points, rounded to the metre.

    Trigonometry runs in floating point; the result is a whole number of metres,
    which is how ``travel_leg.meters`` stores it. Distance is neither money nor
    a quantity, so this is the one place a float is allowed to touch geography.
    """
    phi1, phi2 = math.radians(float(lat1)), math.radians(float(lat2))
    dphi = phi2 - phi1
    dlambda = math.radians(float(lon2) - float(lon1))
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return int((EARTH_RADIUS_M * Decimal(repr(c))).to_integral_value(ROUND_HALF_UP))


def speed_for(meters: int, bands: tuple[SpeedBand, ...]) -> Decimal:
    """The band's speed for a straight-line distance; the last band catches the rest."""
    km = Decimal(meters) / 1000
    for band in bands:
        if band.upper_km is None or km < band.upper_km:
            return band.speed_kmh
    return bands[-1].speed_kmh  # unreachable: the last band is unbounded


def estimate(meters: int, road_factor: Decimal, bands: tuple[SpeedBand, ...]) -> tuple[int, int]:
    """``(seconds, road_meters)`` for a straight-line distance: haversine × road
    factor, at the speed of the distance's band."""
    road_m = Decimal(meters) * road_factor
    speed_ms = speed_for(meters, bands) * 1000 / 3600
    seconds = (road_m / speed_ms).to_integral_value(ROUND_HALF_UP)
    return int(seconds), int(road_m.to_integral_value(ROUND_HALF_UP))


# --- time of day ----------------------------------------------------------------------


def multiplier_at(when: datetime, table: Multipliers, timezone: str) -> Decimal:
    """The factor for a departure at ``when``.

    An aware datetime is read in ``timezone``; a naive one is taken as already
    local. Saturday and Sunday are the weekend. Bands are [start, end) in whole
    hours; outside every band the factor is 1.
    """
    local = when.astimezone(ZoneInfo(timezone)) if when.tzinfo is not None else when
    bands = table.weekend if local.weekday() >= 5 else table.weekday
    hour = local.hour
    for band in bands:
        if band.start <= hour < band.end:
            return band.multiplier
    return Decimal("1")
