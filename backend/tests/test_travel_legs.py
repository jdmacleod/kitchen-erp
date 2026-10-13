"""Drive-time legs (17, 4B, package 5): criteria 11 and 12, the estimate tables of
P2, the compose check of 21, the recompute endpoint and command.

Geography is the synthetic grid of ``geo_helpers`` (open ocean, no home, no
shop). OSRM is never run: the ``table`` call is answered by a hand-written stub,
and the estimate path runs with every socket beyond the database refused.

Hand-computed figures, R = 6,371,000 m: one degree of latitude is 111,195 m;
HOME_A to NEAR_A (0.01 degrees each way at 33.5 degrees) is 1,448 m, under the
3 km band, so the default estimate is 1,448 × 1.3 = 1,882 m at 30 km/h, 226 s;
HOME_A to MID is 28,943 m, beyond 15 km, so 37,626 m at 60 km/h, 2,258 s. With
a road factor of 1.5 and bands ``5:20,:50`` the same pairs give 2,172 m at
20 km/h, 391 s, and 43,415 m at 50 km/h, 3,126 s.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.db import get_sessionmaker
from app.core.grants import APP_ROLE, TRUNCATABLE
from app.core.ids import new_id
from app.models.geo import HomeBase, Place, VendorLocation, point_expr
from app.models.plan import TravelLeg
from app.services import travel, travel_tables
from tests import geo_helpers as gh
from tests.conftest import MEMBER, make_user
from tests.geo_helpers import FAR, HOME_A, MID, NEAR_A, NEAR_B, make_home_base, make_location
from tests.test_shopping_lists import member_client  # noqa: F401

clean_geo = gh.clean_geo
no_network = gh.no_network

D = Decimal
BACKEND_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = BACKEND_ROOT.parent
ROUTING_URL = "http://routing:5000"
LEGS = "/api/v1/geo/travel-legs"


def settings(**overrides: Any) -> Settings:
    # `_env_file=None` keeps a developer's own .env out of the assertions; the
    # compose defaults an operator forwards are the code defaults anyway.
    return Settings(
        _env_file=None,
        database_url="x",
        migration_database_url="y",
        **{"routing_url": "", **overrides},
    )


def hav(a: tuple[str, str], b: tuple[str, str]) -> int:
    return travel_tables.haversine_m(D(a[0]), D(a[1]), D(b[0]), D(b[1]))


# --- the arithmetic (P2) ------------------------------------------------------------------


def test_haversine_on_the_synthetic_grid():
    assert hav(("33.000000", "-120.500000"), ("34.000000", "-120.500000")) == 111195
    assert hav(HOME_A, NEAR_A) == 1448
    assert hav(NEAR_A, HOME_A) == 1448  # symmetric
    assert hav(HOME_A, MID) == 28943
    assert hav(HOME_A, HOME_A) == 0


def test_estimate_is_haversine_times_the_road_factor_at_the_bands_speed():
    s = settings()
    bands = travel_tables.speed_bands(s.plan_estimate_speed_bands)
    factor = s.plan_estimate_road_factor
    assert travel_tables.estimate(1448, factor, bands) == (226, 1882)  # under 3 km: 30 km/h
    assert travel_tables.estimate(13000, factor, bands) == (1521, 16900)  # to 15 km: 40 km/h
    assert travel_tables.estimate(28943, factor, bands) == (2258, 37626)  # beyond: 60 km/h
    # A band covers distances below its bound: exactly 3 km is the next band.
    assert travel_tables.speed_for(2999, bands) == D("30")
    assert travel_tables.speed_for(3000, bands) == D("40")
    assert travel_tables.speed_for(15000, bands) == D("60")


def test_estimate_follows_changed_settings():
    s = settings(plan_estimate_road_factor=D("1.5"), plan_estimate_speed_bands="5:20,:50")
    bands = travel_tables.speed_bands(s.plan_estimate_speed_bands)
    assert travel_tables.estimate(1448, s.plan_estimate_road_factor, bands) == (391, 2172)
    assert travel_tables.estimate(28943, s.plan_estimate_road_factor, bands) == (3126, 43415)


@pytest.mark.parametrize(
    ("local", "expected"),
    [
        (datetime(2026, 10, 14, 5, 59), "1"),  # Wednesday, before the morning band
        (datetime(2026, 10, 14, 6, 0), "1.35"),
        (datetime(2026, 10, 14, 9, 59), "1.35"),
        (datetime(2026, 10, 14, 10, 0), "1.15"),  # bands are [start, end)
        (datetime(2026, 10, 14, 12, 30), "1.15"),
        (datetime(2026, 10, 14, 15, 0), "1.45"),
        (datetime(2026, 10, 14, 19, 59), "1.45"),
        (datetime(2026, 10, 14, 20, 0), "1"),
        (datetime(2026, 10, 14, 23, 30), "1"),
        (datetime(2026, 10, 17, 8, 59), "1"),  # Saturday, before the weekend band
        (datetime(2026, 10, 17, 9, 0), "1.15"),
        (datetime(2026, 10, 18, 18, 59), "1.15"),  # Sunday
        (datetime(2026, 10, 18, 19, 0), "1"),
    ],
)
def test_multiplier_table_by_local_time(local: datetime, expected: str):
    s = settings()
    table = travel_tables.multipliers(s.plan_traffic_multipliers)
    assert local.strftime("%A") in ("Wednesday", "Saturday", "Sunday")
    assert travel_tables.multiplier_at(local, table, s.household_timezone) == D(expected)


def test_multiplier_reads_an_aware_instant_in_the_household_timezone():
    s = settings()
    table = travel_tables.multipliers(s.plan_traffic_multipliers)
    # 02:00 UTC on the 14th is 19:00 on Tuesday the 13th in Los Angeles: the evening band.
    at = datetime(2026, 10, 14, 2, 0, tzinfo=UTC)
    assert travel_tables.multiplier_at(at, table, "America/Los_Angeles") == D("1.45")
    assert travel_tables.multiplier_at(at, table, "UTC") == D("1")  # 02:00 Wednesday


def test_a_changed_multiplier_table_and_a_missing_day_kind():
    table = travel_tables.multipliers("weekend=00-24:2")
    assert travel_tables.multiplier_at(datetime(2026, 10, 17, 3, 0), table, "UTC") == D("2")
    assert travel_tables.multiplier_at(datetime(2026, 10, 14, 8, 0), table, "UTC") == D("1")
    assert travel_tables.multipliers("").weekday == ()


@pytest.mark.parametrize(
    "overrides",
    [
        {"plan_estimate_speed_bands": "3:30,15:40"},  # no unbounded band
        {"plan_estimate_speed_bands": "15:40,3:30,:60"},  # bounds do not increase
        {"plan_estimate_speed_bands": ":60,3:30"},  # unbounded first
        {"plan_estimate_speed_bands": "3:0,:60"},  # a speed of zero
        {"plan_estimate_speed_bands": "fast"},
        {"plan_traffic_multipliers": "weekday=10-06:1.2"},  # start after end
        {"plan_traffic_multipliers": "monday=06-10:1.2"},
        {"plan_traffic_multipliers": "weekday=06-10:1.1,09-12:1.2"},  # overlap
        {"plan_traffic_multipliers": "weekday=06-25:1.1"},
        {"plan_traffic_multipliers": "weekday=06-10:1.1;weekday=12-14:1.2"},
        {"plan_estimate_road_factor": D("0")},
        {"plan_value_of_time_per_hour": D("-1")},
        {"routing_timeout_s": 0},
    ],
)
def test_bad_tables_are_refused_at_boot(overrides: dict[str, Any]):
    with pytest.raises(ValidationError):
        settings(**overrides)


# --- the stub engine and places ------------------------------------------------------------


@dataclass
class FakeOsrm:
    """Answers OSRM's ``table`` call from invented figures and keeps every request."""

    requests: list[httpx.Request] = field(default_factory=list)
    mode: str = "ok"  # ok | refuse | timeout | http500 | bad_code | garbage
    seconds: Any = 600
    meters: Any = 7000
    null_cells: set[tuple[str, str]] = field(default_factory=set)  # (from coord, to coord)
    data_version: str | None = "2026-10-01T00:00:00Z"

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.mode == "refuse":
            raise httpx.ConnectError("connection refused", request=request)
        if self.mode == "timeout":
            raise httpx.ReadTimeout("no answer", request=request)
        if self.mode == "http500":
            return httpx.Response(500, text="boom")
        if self.mode == "bad_code":
            return httpx.Response(200, json={"code": "NoTable", "message": "no"})
        if self.mode == "garbage":
            return httpx.Response(200, text="<html>not json</html>")
        coords = self.coords(request)
        sources = [int(i) for i in request.url.params["sources"].split(";")]
        destinations = [int(i) for i in request.url.params["destinations"].split(";")]
        assert request.url.params["annotations"] == "duration,distance"

        def cell(i: int, j: int, value: Any) -> Any:
            if i == j:
                return 0
            if (coords[i], coords[j]) in self.null_cells:
                return None
            return value

        body: dict[str, Any] = {
            "code": "Ok",
            "durations": [[cell(i, j, self.seconds) for j in destinations] for i in sources],
            "distances": [[cell(i, j, self.meters) for j in destinations] for i in sources],
        }
        if self.data_version is not None:
            body["data_version"] = self.data_version
        return httpx.Response(200, json=body)

    @staticmethod
    def coords(request: httpx.Request) -> list[str]:
        return request.url.path.split("/table/v1/car/", 1)[1].split(";")


@pytest.fixture
def osrm(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeOsrm]:
    fake = FakeOsrm()
    monkeypatch.setattr(travel, "http_transport", httpx.MockTransport(fake.handler))
    yield fake


def coord(point: tuple[str, str]) -> str:
    """The ``lon,lat`` text the client sends for a synthetic grid point."""
    return f"{D(point[1]):f},{D(point[0]):f}"


async def make_places(points: list[tuple[str, str]]) -> list[uuid.UUID]:
    """Bare places on the synthetic grid, written directly: a leg needs nothing else."""
    rows = [
        Place(id=new_id(), lat=D(lat), lon=D(lon), geom=point_expr(D(lat), D(lon)))
        for lat, lon in points
    ]
    async with get_sessionmaker()() as db:
        db.add_all(rows)
        await db.commit()
    return [r.id for r in rows]


async def legs(ids: list[uuid.UUID], s: Settings) -> dict[tuple[uuid.UUID, uuid.UUID], TravelLeg]:
    async with get_sessionmaker()() as db:
        return await travel.legs_for(db, ids, s)


async def stored() -> list[TravelLeg]:
    async with get_sessionmaker()() as db:
        return list((await db.execute(select(TravelLeg))).scalars())


# --- criterion 11: one call, stored osrm, not refetched; estimates without the engine ----


async def test_missing_pairs_are_fetched_in_one_call_and_not_refetched(osrm: FakeOsrm):
    ids = await make_places([HOME_A, NEAR_A, NEAR_B, MID])
    s = settings(routing_url=ROUTING_URL)

    matrix = await legs(ids, s)

    assert len(matrix) == 12 and len(osrm.requests) == 1
    request = osrm.requests[0]
    assert str(request.url).startswith(f"{ROUTING_URL}/table/v1/car/")
    assert set(FakeOsrm.coords(request)) == {
        coord(HOME_A),
        coord(NEAR_A),
        coord(NEAR_B),
        coord(MID),
    }
    assert request.url.params["sources"] == request.url.params["destinations"]
    for (a, b), leg in matrix.items():
        assert (leg.from_place_id, leg.to_place_id) == (a, b)
        assert (leg.seconds, leg.meters, leg.source) == (600, 7000, "osrm")
        assert leg.profile == "car" and leg.engine_version == "2026-10-01T00:00:00Z"
    assert len(await stored()) == 12

    again = await legs(ids, s)
    assert len(osrm.requests) == 1  # nothing missing, nothing asked
    assert {k: (v.seconds, v.meters) for k, v in again.items()} == {
        k: (v.seconds, v.meters) for k, v in matrix.items()
    }


async def test_a_new_place_asks_only_for_its_pairs(osrm: FakeOsrm):
    ids = await make_places([HOME_A, NEAR_A])
    s = settings(routing_url=ROUTING_URL)
    await legs(ids, s)
    extra = await make_places([MID])
    matrix = await legs(ids + extra, s)
    assert len(matrix) == 6 and len(osrm.requests) == 2
    # The second call carries only the pairs that touch the new place, in both directions.
    second = osrm.requests[1]
    assert set(FakeOsrm.coords(second)) == {coord(HOME_A), coord(NEAR_A), coord(MID)}
    assert len(second.url.params["sources"].split(";")) == 3


async def test_unset_routing_url_estimates_every_pair_without_the_network(no_network: None):
    ids = await make_places([HOME_A, NEAR_A, MID])
    matrix = await legs(ids, settings())
    home, near, mid = ids
    assert all(leg.source == "estimate" and leg.engine_version is None for leg in matrix.values())
    assert (matrix[(home, near)].seconds, matrix[(home, near)].meters) == (226, 1882)
    assert (matrix[(near, home)].seconds, matrix[(near, home)].meters) == (226, 1882)
    assert (matrix[(home, mid)].seconds, matrix[(home, mid)].meters) == (2258, 37626)
    assert len(await stored()) == 6


async def test_estimates_follow_changed_settings_in_the_database_too(no_network: None):
    home, near = await make_places([HOME_A, NEAR_A])
    s = settings(plan_estimate_road_factor=D("1.5"), plan_estimate_speed_bands="5:20,:50")
    matrix = await legs([home, near], s)
    assert (matrix[(home, near)].seconds, matrix[(home, near)].meters) == (391, 2172)


async def test_a_null_cell_is_estimated_and_the_rest_stored_osrm(osrm: FakeOsrm):
    home, near = await make_places([HOME_A, NEAR_A])
    osrm.null_cells = {(coord(HOME_A), coord(NEAR_A))}
    matrix = await legs([home, near], settings(routing_url=ROUTING_URL))
    assert matrix[(home, near)].source == "estimate"
    assert (matrix[(home, near)].seconds, matrix[(home, near)].meters) == (226, 1882)
    assert matrix[(near, home)].source == "osrm"


async def test_fractional_seconds_and_metres_round_half_up(osrm: FakeOsrm):
    home, near = await make_places([HOME_A, NEAR_A])
    osrm.seconds, osrm.meters = 612.5, 7000.4
    matrix = await legs([home, near], settings(routing_url=ROUTING_URL))
    assert (matrix[(home, near)].seconds, matrix[(home, near)].meters) == (613, 7000)


async def test_engine_version_from_the_header_when_the_body_has_none(monkeypatch):
    fake = FakeOsrm(data_version=None)

    def with_header(request: httpx.Request) -> httpx.Response:
        response = fake.handler(request)
        response.headers["x-osrm-version"] = "5.27.1"
        return response

    monkeypatch.setattr(travel, "http_transport", httpx.MockTransport(with_header))
    home, near = await make_places([HOME_A, NEAR_A])
    matrix = await legs([home, near], settings(routing_url=ROUTING_URL))
    assert matrix[(home, near)].engine_version == "5.27.1"


@pytest.mark.parametrize("mode", ["refuse", "timeout", "http500", "bad_code", "garbage"])
async def test_an_engine_that_does_not_answer_degrades_to_estimates_and_logs_once(
    osrm: FakeOsrm, mode: str, caplog: pytest.LogCaptureFixture
):
    osrm.mode = mode
    ids = await make_places([HOME_A, NEAR_A, MID])
    with caplog.at_level("WARNING", logger="app.services.travel"):
        matrix = await legs(ids, settings(routing_url=ROUTING_URL))
    assert len(matrix) == 6
    assert all(leg.source == "estimate" for leg in matrix.values())
    warnings = [r for r in caplog.records if "routing engine unavailable" in r.getMessage()]
    assert len(warnings) == 1 and len(osrm.requests) == 1


# --- criterion 12: osrm replaces an estimate, never the reverse; recompute ----------------


async def test_an_estimate_is_replaced_by_the_engine_and_never_the_reverse(osrm: FakeOsrm):
    home, near = await make_places([HOME_A, NEAR_A])
    off, on = settings(), settings(routing_url=ROUTING_URL)

    first = await legs([home, near], off)
    assert first[(home, near)].source == "estimate"
    assert osrm.requests == []  # the engine was never reached while off

    replaced = await legs([home, near], on)
    assert replaced[(home, near)].source == "osrm"
    assert (replaced[(home, near)].seconds, replaced[(home, near)].meters) == (600, 7000)
    assert replaced[(home, near)].computed_at > first[(home, near)].computed_at
    assert len(osrm.requests) == 1

    kept = await legs([home, near], off)  # the engine off again: the answer stands
    assert kept[(home, near)].source == "osrm"
    assert kept[(home, near)].computed_at == replaced[(home, near)].computed_at

    osrm.seconds = 999
    unchanged = await legs([home, near], on)  # answered pairs are not asked again
    assert unchanged[(home, near)].seconds == 600 and len(osrm.requests) == 1


async def test_an_estimate_stays_when_the_engine_fails_again(osrm: FakeOsrm):
    home, near = await make_places([HOME_A, NEAR_A])
    first = await legs([home, near], settings())
    osrm.mode = "refuse"
    second = await legs([home, near], settings(routing_url=ROUTING_URL))
    assert second[(home, near)].source == "estimate"
    assert second[(home, near)].computed_at == first[(home, near)].computed_at
    assert len(osrm.requests) == 1  # it was asked, and the old estimate kept


async def test_recompute_drops_only_estimates_unless_told_all(osrm: FakeOsrm):
    home, near, mid = await make_places([HOME_A, NEAR_A, MID])
    on = settings(routing_url=ROUTING_URL)
    osrm.null_cells = {(coord(HOME_A), coord(MID))}
    before = await legs([home, near, mid], on)
    assert before[(home, mid)].source == "estimate"
    assert sum(leg.source == "osrm" for leg in before.values()) == 5

    osrm.null_cells = set()
    osrm.seconds = 777
    async with get_sessionmaker()() as db:
        counts = await travel.recompute_legs(db, on, only_estimates=True)
    assert (counts.dropped, counts.osrm, counts.estimated) == (1, 1, 0)
    after = {(r.from_place_id, r.to_place_id): r for r in await stored()}
    assert after[(home, mid)].source == "osrm" and after[(home, mid)].seconds == 777
    assert after[(home, near)].seconds == 600  # untouched
    assert after[(home, near)].computed_at == before[(home, near)].computed_at

    async with get_sessionmaker()() as db:
        counts = await travel.recompute_legs(db, settings(), only_estimates=False)
    assert (counts.dropped, counts.osrm, counts.estimated) == (6, 0, 6)
    assert all(r.source == "estimate" for r in await stored())

    async with get_sessionmaker()() as db:
        counts = await travel.recompute_legs(db, on, only_estimates=True)
    assert (counts.dropped, counts.osrm, counts.estimated) == (6, 6, 0)
    assert all(r.source == "osrm" and r.seconds == 777 for r in await stored())


async def test_recompute_with_nothing_stored_is_a_no_op(osrm: FakeOsrm):
    async with get_sessionmaker()() as db:
        counts = await travel.recompute_legs(db, settings(routing_url=ROUTING_URL))
    assert (counts.dropped, counts.osrm, counts.estimated) == (0, 0, 0)
    assert osrm.requests == []


# --- more than a hundred coordinates --------------------------------------------------------


async def test_more_than_a_hundred_places_are_covered_in_blocks(osrm: FakeOsrm):
    n = 120
    points = [(f"{D('33.0') + D(i) * D('0.005'):f}", "-120.500000") for i in range(n)]
    ids = await make_places(points)
    matrix = await legs(ids, settings(routing_url=ROUTING_URL))
    assert len(matrix) == n * (n - 1)
    assert all(leg.source == "osrm" for leg in matrix.values())
    # 50-source by 50-destination blocks: (50, 50, 20) squared is nine calls.
    assert len(osrm.requests) == 9
    for request in osrm.requests:
        assert len(FakeOsrm.coords(request)) <= travel.MAX_TABLE_COORDINATES
    assert len(await stored()) == n * (n - 1)
    assert len(osrm.requests) == 9 and len(
        await legs(ids, settings(routing_url=ROUTING_URL))
    ) == len(matrix)


# --- the endpoints and the command ---------------------------------------------------------


async def place_ids_of(home_base_id: str, location_id: str) -> tuple[uuid.UUID, uuid.UUID]:
    async with get_sessionmaker()() as db:
        home = await db.scalar(
            select(HomeBase.place_id).where(HomeBase.id == uuid.UUID(home_base_id))
        )
        shop = await db.scalar(
            select(VendorLocation.place_id).where(VendorLocation.id == uuid.UUID(location_id))
        )
    assert home is not None and shop is not None
    return home, shop


async def test_get_leg_computes_it_for_real_places_and_marks_the_source(
    admin_client: httpx.AsyncClient, no_network: None
):
    kitchen = await make_home_base(admin_client, "Harbor kitchen", HOME_A)
    shop = await make_location(admin_client, "Pier stand", NEAR_A)
    home, pier = await place_ids_of(kitchen["id"], shop["id"])

    r = await admin_client.get(LEGS, params={"from": str(home), "to": str(pier)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["from_place_id"] == str(home) and body["to_place_id"] == str(pier)
    assert (body["seconds"], body["meters"], body["source"]) == (226, 1882, "estimate")
    assert body["profile"] == "car" and body["engine_version"] is None
    assert body["computed_at"].endswith("Z") or "+00:00" in body["computed_at"]


async def test_get_leg_through_the_engine_and_its_refusals(
    admin_client: httpx.AsyncClient, osrm: FakeOsrm, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(get_settings(), "routing_url", ROUTING_URL)
    home, far = await make_places([HOME_A, FAR])
    r = await admin_client.get(LEGS, params={"from": str(home), "to": str(far)})
    assert r.status_code == 200 and r.json()["source"] == "osrm"
    assert len(osrm.requests) == 1

    same = await admin_client.get(LEGS, params={"from": str(home), "to": str(home)})
    assert same.status_code == 422 and same.json()["error"]["code"] == "same_place"
    unknown = await admin_client.get(LEGS, params={"from": str(home), "to": str(uuid.uuid4())})
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "not_found"
    missing = await admin_client.get(LEGS, params={"from": str(home)})
    assert missing.status_code == 422


async def test_recompute_endpoint_is_admin_only_and_reports_counts(
    admin_client: httpx.AsyncClient,
    member_client: httpx.AsyncClient,  # noqa: F811
    no_network: None,
):
    home, near, mid = await make_places([HOME_A, NEAR_A, MID])
    await legs([home, near, mid], settings())

    refused = await member_client.post(f"{LEGS}/recompute", json={"only_estimates": True})
    assert refused.status_code == 403, refused.text
    member_can_read = await member_client.get(LEGS, params={"from": str(home), "to": str(near)})
    assert member_can_read.status_code == 200

    r = await admin_client.post(f"{LEGS}/recompute", json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"dropped": 6, "osrm": 0, "estimated": 6}
    r = await admin_client.post(f"{LEGS}/recompute", json={"only_estimates": False})
    assert r.json() == {"dropped": 6, "osrm": 0, "estimated": 6}


async def test_the_member_fixture_is_a_member():
    user = await make_user("member")
    assert user.role == "member" and user.email == MEMBER["email"]


async def test_cli_recompute_reports_counts(no_network: None):
    home, near = await make_places([HOME_A, NEAR_A])
    await legs([home, near], settings())
    env = {**os.environ, "ROUTING_URL": ""}
    for flag, expected in (
        ([], "dropped 2 legs; 0 from the engine, 2 estimated"),
        (["--all"], "dropped 2 legs; 0 from the engine, 2 estimated"),
    ):
        done = subprocess.run(
            [sys.executable, "-m", "app.cli", "travel-legs", "recompute", *flag],
            cwd=BACKEND_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr
        assert expected in done.stdout


# --- the migration, the grants and the compose check (criterion 21) ------------------------


async def test_travel_leg_is_migrated_with_truncate_for_the_runtime_role(
    owner_conn: asyncpg.Connection,
):
    assert "travel_leg" in TRUNCATABLE
    columns = {
        r["column_name"]: r["data_type"]
        for r in await owner_conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'travel_leg'"
        )
    }
    assert columns == {
        "from_place_id": "uuid",
        "to_place_id": "uuid",
        "seconds": "integer",
        "meters": "integer",
        "source": "character varying",
        "profile": "character varying",
        "engine_version": "character varying",
        "computed_at": "timestamp with time zone",
    }
    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE"):
        assert await owner_conn.fetchval(
            "SELECT has_table_privilege($1, 'travel_leg', $2)", APP_ROLE, privilege
        ), privilege


async def test_the_database_refuses_a_bad_leg(app_conn: asyncpg.Connection):
    home, near = await make_places([HOME_A, NEAR_A])
    insert = (
        "INSERT INTO travel_leg (from_place_id, to_place_id, seconds, meters, source) "
        "VALUES ($1, $2, $3, $4, $5)"
    )
    with pytest.raises(asyncpg.CheckViolationError):
        await app_conn.execute(insert, home, home, 1, 1, "osrm")
    with pytest.raises(asyncpg.CheckViolationError):
        await app_conn.execute(insert, home, near, -1, 1, "osrm")
    with pytest.raises(asyncpg.CheckViolationError):
        await app_conn.execute(insert, home, near, 1, 1, "guess")
    await app_conn.execute(insert, home, near, 1, 1, "estimate")
    # Deleting a place takes its legs with it: a leg without a place is nothing.
    await app_conn.execute("DELETE FROM place WHERE id = $1", near)
    assert await app_conn.fetchval("SELECT count(*) FROM travel_leg") == 0


def test_compose_env_check_passes_with_the_bbox_host_only():
    done = subprocess.run(
        [sys.executable, "-m", "tools.check_compose_env"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    sys.path.insert(0, str(REPO_ROOT))
    from tools.check_compose_env import NOT_FORWARDED, documented_settings, forwarded_variables

    assert "ROUTING_BBOX" in NOT_FORWARDED and "ROUTING_BBOX" not in forwarded_variables()
    documented = documented_settings()
    assert {"ROUTING_URL", "ROUTING_TIMEOUT_S", "ROUTING_BBOX"} <= set(documented)
    assert {
        "PLAN_ESTIMATE_ROAD_FACTOR",
        "PLAN_ESTIMATE_SPEED_BANDS",
        "PLAN_TRAFFIC_MULTIPLIERS",
        "PLAN_VALUE_OF_TIME_PER_HOUR",
    } <= set(documented)
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "\nROUTING_BBOX=\n" in env_example  # documented, and left unset
    compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert 'profiles: ["routing"]' in compose
    assert "ghcr.io/project-osrm/osrm-backend:v5.27.1" in compose
    assert "./data/routing:/data:ro" in compose
    assert (REPO_ROOT / "Makefile").read_text(encoding="utf-8").count("routing-prepare:") == 1
