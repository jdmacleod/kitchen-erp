"""Drive-time legs between places (17, 4B): the OSRM table client with an estimate
fallback.

A leg is the free-flow time and distance from one place to another, stored once
per ordered pair in ``travel_leg``. ``legs_for`` is what the planner calls: it
collects the pairs among the places it was given, finds the ones missing or
estimated, and makes one OSRM ``table`` call for all of them,
``GET {ROUTING_URL}/table/v1/car/{lon,lat;…}`` with ``sources``, ``destinations``
and ``annotations=duration,distance``, with timeout ``ROUTING_TIMEOUT_S``. An
answered cell is stored ``osrm``; a null cell, a timeout, a refused connection,
a non-``Ok`` code or an unset ``ROUTING_URL`` stores ``estimate``
(``travel_tables.estimate``). An estimate is
replaced when the engine next answers for that pair, never the reverse: the
upsert only overwrites rows whose source is ``estimate``, and an estimate never
overwrites anything. Nothing here raises for the engine's sake: a failed call
is logged once and the legs degrade to estimates, so the planner never refuses
to plan because the container is off (P2, P3).

Time of day is not stored: ``travel_tables.multiplier_at`` applies per leg at
planning time. The response is parsed with ``parse_float=Decimal`` and read only
against the shape expected (non-negotiable 7); values are whole seconds and
metres. A table call carries at most ``MAX_TABLE_COORDINATES`` points; beyond
that the pairs are covered by blocks of sources against blocks of destinations.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import httpx
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.logging import get_logger
from app.models.geo import Place
from app.models.plan import TravelLeg
from app.services import travel_tables

log = get_logger(__name__)

PROFILE = "car"
MAX_TABLE_COORDINATES = 100
# With more points than one call takes, sources and destinations go in blocks of
# half the cap, so a block pair never exceeds it.
_BLOCK = MAX_TABLE_COORDINATES // 2
# Rows per INSERT: a hundred places make 9,900 legs, and asyncpg caps a statement
# at 32,767 parameters.
_INSERT_BATCH = 2000
USER_AGENT = "kitchen-erp/0.1 (self-hosted household kitchen system; OSRM table client)"

# Tests set this to an httpx.MockTransport; production leaves it None (real sockets).
http_transport: httpx.AsyncBaseTransport | None = None

Pair = tuple[uuid.UUID, uuid.UUID]


@dataclass(frozen=True)
class Answer:
    seconds: int
    meters: int
    engine_version: str | None


@dataclass(frozen=True)
class FillCounts:
    osrm: int
    estimated: int


@dataclass(frozen=True)
class RecomputeCounts:
    dropped: int
    osrm: int
    estimated: int


# --- places and existing rows -----------------------------------------------------------


async def _places(db: AsyncSession, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, Place]:
    wanted = set(ids)
    rows = (await db.execute(select(Place).where(Place.id.in_(wanted)))).scalars().all()
    found = {p.id: p for p in rows}
    missing = wanted - found.keys()
    if missing:
        raise ApiError(404, "not_found", "No such place.", {"place_id": str(min(missing))})
    return found


async def _existing(db: AsyncSession, ids: Iterable[uuid.UUID]) -> dict[Pair, TravelLeg]:
    ids = set(ids)
    rows = (
        await db.execute(
            select(TravelLeg)
            .where(TravelLeg.from_place_id.in_(ids), TravelLeg.to_place_id.in_(ids))
            .execution_options(populate_existing=True)
        )
    ).scalars()
    return {(r.from_place_id, r.to_place_id): r for r in rows}


# --- the OSRM table call ------------------------------------------------------------------


def _coord(place: Place) -> str:
    return f"{place.lon:f},{place.lat:f}"


def _whole(value: Any) -> int | None:
    """A non-negative whole number from a JSON cell, or None for null or nonsense."""
    if isinstance(value, bool) or not isinstance(value, int | Decimal):
        return None
    if not Decimal(value).is_finite() or value < 0:
        return None
    return int(Decimal(value).to_integral_value(ROUND_HALF_UP))


def _version(data: dict[str, Any], response: httpx.Response) -> str | None:
    for candidate in (data.get("data_version"), response.headers.get("x-osrm-version")):
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()[:80]
    return None


def _parse_table(
    body: str, response: httpx.Response, sources: list[uuid.UUID], destinations: list[uuid.UUID]
) -> dict[Pair, Answer]:
    data = json.loads(body, parse_float=Decimal)
    if not isinstance(data, dict) or data.get("code") != "Ok":
        code = data.get("code") if isinstance(data, dict) else None
        raise ValueError(f"routing engine answered {code!r}")
    durations, distances = data.get("durations"), data.get("distances")
    if not (isinstance(durations, list) and isinstance(distances, list)):
        raise ValueError("routing engine answer has no durations or distances")
    if len(durations) != len(sources) or len(distances) != len(sources):
        raise ValueError("routing engine answer has the wrong number of rows")
    version = _version(data, response)
    answers: dict[Pair, Answer] = {}
    for i, source in enumerate(sources):
        d_row, m_row = durations[i], distances[i]
        if not (isinstance(d_row, list) and isinstance(m_row, list)):
            raise ValueError("routing engine answer has a malformed row")
        if len(d_row) != len(destinations) or len(m_row) != len(destinations):
            raise ValueError("routing engine answer has the wrong number of columns")
        for j, destination in enumerate(destinations):
            if source == destination:
                continue
            seconds, meters = _whole(d_row[j]), _whole(m_row[j])
            if seconds is None or meters is None:
                continue  # a null cell: unreachable or off the extract; estimated instead
            answers[(source, destination)] = Answer(seconds, meters, version)
    return answers


def _blocks(ids: list[uuid.UUID], size: int) -> list[list[uuid.UUID]]:
    return [ids[i : i + size] for i in range(0, len(ids), size)]


async def _table(
    pairs: set[Pair], places: dict[uuid.UUID, Place], settings: Settings
) -> dict[Pair, Answer]:
    """Every pair the engine answers, in as few calls as the coordinate cap allows.

    Never raises: the first failure is logged once and whatever was answered
    before it is returned; the rest of the pairs become estimates.
    """
    sources = sorted({a for a, _ in pairs}, key=str)
    destinations = sorted({b for _, b in pairs}, key=str)
    if len(set(sources) | set(destinations)) <= MAX_TABLE_COORDINATES:
        calls = [(sources, destinations)]
    else:
        calls = [(s, d) for s in _blocks(sources, _BLOCK) for d in _blocks(destinations, _BLOCK)]
    base = settings.routing_url.rstrip("/")
    answers: dict[Pair, Answer] = {}
    timeout = httpx.Timeout(settings.routing_timeout_s)
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, timeout=timeout, transport=http_transport
    ) as client:
        for src, dst in calls:
            points = list(dict.fromkeys(src + dst))
            index = {pid: i for i, pid in enumerate(points)}
            url = f"{base}/table/v1/{PROFILE}/" + ";".join(_coord(places[p]) for p in points)
            params = {
                "sources": ";".join(str(index[p]) for p in src),
                "destinations": ";".join(str(index[p]) for p in dst),
                "annotations": "duration,distance",
            }
            try:
                response = await client.get(url, params=params)
                if response.status_code != 200:
                    raise ValueError(f"routing engine answered HTTP {response.status_code}")
                got = _parse_table(response.text, response, src, dst)
            except (httpx.HTTPError, ValueError) as exc:
                log.warning(
                    "routing engine unavailable; legs estimated instead",
                    extra={"reason": f"{type(exc).__name__}: {exc}"[:200], "url": base},
                )
                return answers
            answers.update((pair, answer) for pair, answer in got.items() if pair in pairs)
    return answers


# --- filling pairs -------------------------------------------------------------------------


def _estimate_row(a: Place, b: Place, settings: Settings) -> dict[str, Any]:
    straight = travel_tables.haversine_m(a.lat, a.lon, b.lat, b.lon)
    seconds, meters = travel_tables.estimate(
        straight,
        settings.plan_estimate_road_factor,
        travel_tables.speed_bands(settings.plan_estimate_speed_bands),
    )
    return {
        "from_place_id": a.id,
        "to_place_id": b.id,
        "seconds": seconds,
        "meters": meters,
        "source": "estimate",
        "profile": PROFILE,
        "engine_version": None,
    }


async def _fill(
    db: AsyncSession,
    pairs: set[Pair],
    places: dict[uuid.UUID, Place],
    settings: Settings,
    *,
    keep_estimates_for: set[Pair],
) -> FillCounts:
    """Write legs for ``pairs``: the engine's answers where it gave them, estimates
    for the rest, except that a pair in ``keep_estimates_for`` already holds an
    estimate and is left alone when the engine did not answer."""
    answered = await _table(pairs, places, settings) if settings.routing_url else {}
    osrm_rows = [
        {
            "from_place_id": a,
            "to_place_id": b,
            "seconds": ans.seconds,
            "meters": ans.meters,
            "source": "osrm",
            "profile": PROFILE,
            "engine_version": ans.engine_version,
        }
        for (a, b), ans in answered.items()
    ]
    estimate_rows = [
        _estimate_row(places[a], places[b], settings)
        for (a, b) in sorted(pairs, key=lambda p: (str(p[0]), str(p[1])))
        if (a, b) not in answered and (a, b) not in keep_estimates_for
    ]
    for i in range(0, len(osrm_rows), _INSERT_BATCH):
        stmt = insert(TravelLeg).values(osrm_rows[i : i + _INSERT_BATCH])
        # The engine's answer replaces an estimate and never an earlier answer.
        stmt = stmt.on_conflict_do_update(
            index_elements=[TravelLeg.from_place_id, TravelLeg.to_place_id],
            set_={
                "seconds": stmt.excluded.seconds,
                "meters": stmt.excluded.meters,
                "source": stmt.excluded.source,
                "profile": stmt.excluded.profile,
                "engine_version": stmt.excluded.engine_version,
                "computed_at": func.now(),
            },
            where=(TravelLeg.source == "estimate"),
        )
        await db.execute(stmt)
    for i in range(0, len(estimate_rows), _INSERT_BATCH):
        # An estimate never overwrites anything, whatever is there.
        stmt = insert(TravelLeg).values(estimate_rows[i : i + _INSERT_BATCH])
        await db.execute(stmt.on_conflict_do_nothing())
    await db.commit()
    return FillCounts(osrm=len(osrm_rows), estimated=len(estimate_rows))


async def legs_for(
    db: AsyncSession, place_ids: Iterable[uuid.UUID], settings: Settings
) -> dict[Pair, TravelLeg]:
    """Every ordered pair among ``place_ids``, computing the missing ones.

    Pairs already answered by the engine are not refetched; pairs holding an
    estimate are asked again and replaced when the engine answers. A place that
    does not exist is a 404.
    """
    ids = list(dict.fromkeys(place_ids))
    places = await _places(db, ids)
    wanted = {(a, b) for a in ids for b in ids if a != b}
    existing = await _existing(db, ids)
    needed = {p for p in wanted if p not in existing or existing[p].source == "estimate"}
    if needed:
        await _fill(
            db,
            needed,
            places,
            settings,
            keep_estimates_for={p for p in needed if p in existing},
        )
        existing = await _existing(db, ids)
    return {p: existing[p] for p in wanted}


async def leg_between(
    db: AsyncSession, from_id: uuid.UUID, to_id: uuid.UUID, settings: Settings
) -> TravelLeg:
    if from_id == to_id:
        raise ApiError(422, "same_place", "A leg joins two different places.")
    return (await legs_for(db, [from_id, to_id], settings))[(from_id, to_id)]


async def recompute_legs(
    db: AsyncSession, settings: Settings, *, only_estimates: bool = True
) -> RecomputeCounts:
    """Drop legs and compute them again: estimates by default, every leg with
    ``only_estimates=False`` (the CLI's ``--all``)."""
    query = select(TravelLeg.from_place_id, TravelLeg.to_place_id)
    if only_estimates:
        query = query.where(TravelLeg.source == "estimate")
    pairs: set[Pair] = {(a, b) for a, b in (await db.execute(query)).all()}
    if not pairs:
        return RecomputeCounts(dropped=0, osrm=0, estimated=0)
    deletion = delete(TravelLeg)
    if only_estimates:
        deletion = deletion.where(TravelLeg.source == "estimate")
    await db.execute(deletion)
    places = await _places(db, {p for pair in pairs for p in pair})
    counts = await _fill(db, pairs, places, settings, keep_estimates_for=set())
    return RecomputeCounts(dropped=len(pairs), osrm=counts.osrm, estimated=counts.estimated)
