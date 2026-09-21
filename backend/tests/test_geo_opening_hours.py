"""Criterion 23: opening_hours validation messages; evaluation across DST in America/Los_Angeles."""

from datetime import UTC, datetime

import httpx
import pytest

from app.services.opening_hours import evaluate, validate_hours
from tests import geo_helpers as gh
from tests.geo_helpers import NEAR_A, make_location

clean_geo = gh.clean_geo

WEEK = "Mo-Fr 08:00-21:00; Sa,Su 09:00-20:00"
SEASONAL = "Apr-Oct Sa 08:00-13:00"


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# Every instant is UTC; the comment gives the household-local reading.
WEEK_CASES = [
    ("2026-03-09T17:00:00", True),  # Mon 10:00 PDT: open
    ("2026-03-09T07:30:00", False),  # Mon 00:30 PDT: closed
    ("2026-03-08T09:30:00", False),  # Sun 01:30 PST, half an hour before the spring jump
    ("2026-03-08T10:30:00", False),  # Sun 03:30 PDT, half an hour after it (02:30 never exists)
    ("2026-03-08T16:30:00", True),  # Sun 09:30 PDT: open
    ("2026-03-09T03:30:00", False),  # Sun 20:30 PDT: closed. Read as PST it would be 19:30, open.
    ("2026-11-01T02:30:00", True),  # Sat 19:30 PDT: open
    ("2026-11-01T08:30:00", False),  # Sun 01:30 PDT, the first 01:30 of the fall-back day
    ("2026-11-01T09:30:00", False),  # Sun 01:30 PST, the second one
    ("2026-11-02T03:30:00", True),  # Sun 19:30 PST: open. Read as PDT it would be 20:30, closed.
]
SEASONAL_CASES = [
    ("2026-06-06T17:00:00", True),  # Sat 10:00 PDT in June: open
    ("2026-06-06T21:00:00", False),  # Sat 14:00 PDT: after closing
    ("2026-06-07T17:00:00", False),  # Sunday
    ("2026-01-03T17:00:00", False),  # Saturday in January: out of season
    ("2026-10-31T17:00:00", True),  # last Saturday of October: still in season
    ("2026-11-07T17:00:00", False),  # first Saturday of November: closed for the winter
    ("2026-04-04T15:30:00", True),  # Sat 08:30 PDT on the first April Saturday
    ("2026-04-04T14:30:00", False),  # Sat 07:30 PDT: before opening
]


@pytest.mark.parametrize(("instant", "expected"), WEEK_CASES)
def test_weekly_hours_evaluate_in_household_zone(instant: str, expected: bool):
    assert evaluate(WEEK, utc(instant)) is expected


@pytest.mark.parametrize(("instant", "expected"), SEASONAL_CASES)
def test_seasonal_hours_evaluate_in_household_zone(instant: str, expected: bool):
    assert evaluate(SEASONAL, utc(instant)) is expected


def test_naive_instants_are_household_local():
    assert evaluate(WEEK, datetime(2026, 3, 9, 10, 0)) is True
    assert evaluate(WEEK, datetime(2026, 3, 9, 22, 0)) is False


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("Mo-Fr 25:00-26:00", "column 7"),
        ("Xx 08:00-12:00", "column 1"),
        ("Mo-Fr 08:00-", "extended_time"),
        ("Mo-Fr 08:00-21:00; Sa,Su 09:00-20:00 garbage", "column 38"),
        ("", "must not be empty"),
        ("   ", "must not be empty"),
    ],
)
def test_invalid_strings_get_a_message_that_locates_the_problem(text: str, fragment: str):
    error = validate_hours(text)
    assert error is not None
    assert fragment in error


@pytest.mark.parametrize("text", [WEEK, SEASONAL, "24/7", "Sa 08:00-13:00; PH off"])
def test_valid_strings(text: str):
    assert validate_hours(text) is None


async def test_validate_endpoint(admin_client: httpx.AsyncClient):
    ok = await admin_client.post("/api/v1/opening-hours/validate", json={"text": SEASONAL})
    assert ok.status_code == 200
    assert ok.json() == {"valid": True, "error": None}
    bad = await admin_client.post(
        "/api/v1/opening-hours/validate", json={"text": "Mo-Fr 25:00-26:00"}
    )
    assert bad.json()["valid"] is False
    assert "column 7" in bad.json()["error"]


async def test_invalid_hours_are_rejected_on_save_with_the_message(
    admin_client: httpx.AsyncClient,
):
    r = await admin_client.post(
        "/api/v1/vendor-locations",
        json={
            "name": "Stand",
            "lat": NEAR_A[0],
            "lon": NEAR_A[1],
            "vendor": {"name": "Stand", "kind": "stand"},
            "opening_hours": "Mo-Fr 25:00-26:00",
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_opening_hours"
    assert "column 7" in r.json()["error"]["message"]
    assert (await admin_client.get("/api/v1/vendor-locations")).json()["items"] == []

    location = await make_location(admin_client, "Stand", NEAR_A, opening_hours=WEEK)
    patched = await admin_client.patch(
        f"/api/v1/vendor-locations/{location['id']}", json={"opening_hours": "Xx 08:00-12:00"}
    )
    assert patched.status_code == 422
    assert patched.json()["error"]["code"] == "invalid_opening_hours"
    # Whitespace is trimmed; an empty string is refused rather than silently stored.
    blank = await admin_client.patch(
        f"/api/v1/vendor-locations/{location['id']}", json={"opening_hours": "  "}
    )
    assert blank.status_code == 422
    trimmed = await admin_client.patch(
        f"/api/v1/vendor-locations/{location['id']}", json={"opening_hours": f"  {SEASONAL} "}
    )
    assert trimmed.json()["opening_hours"] == SEASONAL


async def test_is_open_endpoint_across_dst(admin_client: httpx.AsyncClient):
    location = await make_location(admin_client, "Weekly", NEAR_A, opening_hours=WEEK)
    url = f"/api/v1/vendor-locations/{location['id']}/is-open"
    for instant, expected in WEEK_CASES:
        r = await admin_client.get(url, params={"at": instant + "Z"})
        assert r.status_code == 200, r.text
        assert r.json()["is_open"] is expected, instant
        assert r.json()["effective_opening_hours"] == WEEK
        assert datetime.fromisoformat(r.json()["at"]) == utc(instant)
    now = await admin_client.get(url)
    assert now.status_code == 200 and isinstance(now.json()["is_open"], bool)

    unknown = await make_location(admin_client, "Unknown", NEAR_A)
    r = await admin_client.get(f"/api/v1/vendor-locations/{unknown['id']}/is-open")
    assert r.json()["is_open"] is None and r.json()["effective_opening_hours"] is None
