"""The naming pass's suggested name from learned words, and undoing one (#384)."""

from __future__ import annotations

import uuid

from app.services import line_words, resolution
from tests.catalog_helpers import seed_units_via_service
from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase


async def _waiting(client, admin, db_session, raw_texts: list[str]) -> dict:
    loc = await make_location(client, "Quayside Grocer", "Quayside Grocer North")
    lines = [{"raw_text": t, "line_total": "4.10"} for t in raw_texts]
    pid = await make_receipt_purchase(admin.id, loc["id"], lines)
    await resolution.resolve_purchase(db_session, uuid.UUID(pid))
    r = await client.post(f"/api/v1/purchases/{pid}/commit")
    assert r.status_code == 200, r.text
    return loc


async def _rows(client) -> dict:
    r = await client.get("/api/v1/to-identify/naming")
    assert r.status_code == 200, r.text
    return {row["raw_text_norm"]: row for row in r.json()["items"]}


async def test_learned_words_fill_the_name_and_an_undo_is_forgotten(
    admin_client, admin, db_session
):
    await seed_units_via_service(db_session)
    loc = await _waiting(admin_client, admin, db_session, ["SKYR QPRT 3.10", "QPRT BAR 2.10"])
    vendor = uuid.UUID(loc["vendor"]["id"])
    # "qprt" is no catalog word: only what this vendor taught can name it.
    await line_words.learn(db_session, vendor, "QPRT", "Quaport")
    await db_session.commit()
    rows = await _rows(admin_client)
    assert rows["SKYR QPRT"]["name"] == "Skyr quaport"
    assert rows["SKYR QPRT"]["name_changes"] == [
        {"index": 1, "printed": "qprt", "shown": "quaport", "source": "learned"}
    ]
    body = {
        "rows": [
            {"vendor_id": str(vendor), "raw_text_norm": "SKYR QPRT", "name": "Skyr qprt",
             "ingredient": {"name": "skyr", "category": "dairy"},
             "forget_words": [{"word": "qprt", "expansion": "quaport"}]},
        ]
    }  # fmt: skip
    r = await admin_client.post("/api/v1/to-identify/name-products", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["error"] is None
    # Undone at the vendor and anywhere: the other line keeps its printed word.
    assert (await _rows(admin_client))["QPRT BAR"]["name_changes"] == []


async def test_completions_offer_learned_words_first_then_the_vocabulary(
    admin_client, admin, db_session
):
    await seed_units_via_service(db_session)
    loc = await _waiting(admin_client, admin, db_session, ["QPRT BAR 2.10"])
    vendor = uuid.UUID(loc["vendor"]["id"])
    await line_words.learn(db_session, vendor, "QPRT", "Quaport")
    await db_session.commit()
    r = await admin_client.get(
        "/api/v1/to-identify/naming/words", params={"vendor_id": str(vendor), "prefix": "qu"}
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items[0] == {"word": "quaport", "source": "learned"}
    # The standard list's words follow, shortest first ("quinoa" is on it).
    assert {"word": "quinoa", "source": "vocabulary"} in items[1:]
    one = await admin_client.get(
        "/api/v1/to-identify/naming/words", params={"vendor_id": str(vendor), "prefix": "q"}
    )
    assert one.json()["items"] == []  # one letter completes too much
