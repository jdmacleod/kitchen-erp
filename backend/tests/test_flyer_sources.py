"""Weekly flyers 2U-4 (18; UI-9.5): a vendor location's circular, set up and turned
on or off by a person, its last runs, and "Check now". Stores are invented."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import flyers
from tests.pricebook_helpers import make_location
from tests.test_flyers import (
    candidate,
    queue,
    report,
    requests,
    store,  # noqa: F401  the invented store fixture
)
from tests.test_products_helper import helper  # noqa: F401  the stub helper's client

THURSDAY = date(2026, 11, 5)


@pytest.fixture(autouse=True)
def _in_the_ad_week(monkeypatch):
    monkeypatch.setattr(flyers, "today", lambda now=None: THURSDAY)


def url(location: dict, tail: str = "") -> str:
    return f"/api/v1/vendor-locations/{location['id']}/circular{tail}"


async def test_a_location_without_a_circular_says_so(admin_client):
    location = await make_location(admin_client, "Larkspur Market", "Larkspur Market")
    r = await admin_client.get(url(location))
    assert r.status_code == 200, r.text
    assert r.json() == {"source": None, "runs": [], "checking": False}
    r = await admin_client.patch(url(location), json={"enabled": True})
    assert r.status_code == 404


async def test_a_circular_is_set_up_and_changed(admin_client):
    location = await make_location(admin_client, "Larkspur Market", "Larkspur Market")
    body = {
        "mechanism": "hosted_circular",
        "adapter": "example_hosted",
        "store_ref": {"merchant": "larkspur", "store": "0042"},
        "publish_weekday": 3,
    }
    r = await admin_client.put(url(location), json=body)
    assert r.status_code == 200, r.text
    source = r.json()["source"]
    assert (source["enabled"], source["require_confirmation"], source["store_ref"]) == (
        False,
        False,
        {"merchant": "larkspur", "store": "0042"},
    )
    r = await admin_client.patch(url(location), json={"enabled": True, "publish_weekday": 4})
    assert (r.json()["source"]["enabled"], r.json()["source"]["publish_weekday"]) == (True, 4)
    # A source a model reads asks before its offers are used, unless told otherwise (2W).
    r = await admin_client.put(url(location), json=body | {"mechanism": "image"})
    assert r.json()["source"]["require_confirmation"] is True
    assert r.json()["source"]["enabled"] is False  # a whole replacement


async def test_store_ids_are_strings_and_the_mechanism_is_known(admin_client):
    location = await make_location(admin_client, "Larkspur Market", "Larkspur Market")
    base = {"mechanism": "itemized", "adapter": "example_itemized", "publish_weekday": 3}
    for bad in (
        base | {"store_ref": {"store": 42}},
        base | {"mechanism": "smoke_signal"},
        base | {"publish_weekday": 8},
    ):
        r = await admin_client.put(url(location), json=bad)
        assert r.status_code == 422, bad


async def test_the_card_shows_the_newest_four_runs(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
):
    await queue()
    [request] = await requests(helper)
    for n in range(5):
        names = [candidate(f"Invented Item {i}") for i in range(n + 1)]
        r = await report(
            helper, request["id"], candidates=names, adapter_version=f"itemized-{n + 1}"
        )
        assert r.status_code == 200, r.text
    runs = (await admin_client.get(url(store))).json()["runs"]
    assert [run["offered_count"] for run in runs] == [5, 4, 3, 2]
    assert runs[0]["status"] == "ok"


async def test_check_now_queues_a_read_and_lifts_a_pause(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
    owner_conn,
):
    await owner_conn.execute("UPDATE ad_source SET paused_until = now() + interval '7 days'")
    r = await admin_client.post(url(store, "/check"))
    assert r.status_code == 200, r.text
    assert r.json()["checking"] is True
    assert r.json()["source"]["paused_until"] is None
    [request] = await requests(helper)
    assert request["flyer"]["ad_week"] == "2026-11-04"
    again = await admin_client.post(url(store, "/check"))
    assert (again.status_code, again.json()["error"]["code"]) == (409, "already_checking")

    # Answered, the card no longer says it's checking, and a week already read can be
    # read again.
    r = await report(helper, request["id"], candidates=[candidate("Invented Item")])
    assert r.status_code == 200, r.text
    assert (await admin_client.get(url(store))).json()["checking"] is False
    assert (await admin_client.post(url(store, "/check"))).status_code == 200


async def test_check_now_needs_the_circular_on_and_a_helper(
    admin_client,
    store,  # noqa: F811
):
    r = await admin_client.post(url(store, "/check"))
    assert (r.status_code, r.json()["error"]["code"]) == (409, "no_helper")
    await admin_client.patch(url(store), json={"enabled": False})
    r = await admin_client.post(url(store, "/check"))
    assert (r.status_code, r.json()["error"]["code"]) == (409, "source_off")


async def test_an_unknown_location_is_not_found(admin_client):
    location = {"id": "01000000-0000-7000-8000-000000000000"}
    assert (await admin_client.get(url(location))).status_code == 404
    assert (await admin_client.post(url(location, "/check"))).status_code == 404


async def test_turning_a_circular_off_stops_the_weekly_queue(
    admin_client,
    store,  # noqa: F811
    helper,  # noqa: F811
):
    await admin_client.patch(url(store), json={"enabled": False})
    assert await queue() == 0
    await admin_client.patch(url(store), json={"enabled": True})
    assert await queue() == 1
    assert date.fromisoformat((await requests(helper))[0]["flyer"]["ad_week"]) == THURSDAY - (
        timedelta(days=1)
    )
