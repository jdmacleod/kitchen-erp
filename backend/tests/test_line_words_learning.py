"""A vendor's printed words learned from the products people name (#388)."""

from __future__ import annotations

import uuid

from sqlalchemy import delete, select

from app.models import LineWord
from app.services import line_words, resolution
from tests.catalog_helpers import seed_units_via_service
from tests.pricebook_helpers import make_location
from tests.resolution_helpers import make_receipt_purchase


async def _waiting(client, admin, db_session, vendor_name: str, raw_text: str) -> dict:
    loc = await make_location(client, vendor_name, f"{vendor_name} North")
    pid = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": raw_text, "line_total": "4.10"}]
    )
    await resolution.resolve_purchase(db_session, uuid.UUID(pid))
    r = await client.post(f"/api/v1/purchases/{pid}/commit")
    assert r.status_code == 200, r.text
    return loc


async def _name(client, vendor_id: str, norm: str, name: str) -> None:
    rows = [
        {"vendor_id": vendor_id, "raw_text_norm": norm, "name": name,
         "ingredient": {"name": "greek yogurt", "category": "dairy"}},
    ]  # fmt: skip
    r = await client.post("/api/v1/to-identify/name-products", json={"rows": rows})
    assert r.status_code == 200, r.text
    assert r.json()["results"][0]["error"] is None


def _offered(
    found: dict[str, list[line_words.Expansion]],
) -> dict[str, list[tuple[str, int, bool]]]:
    return {w: [(e.expansion, e.count, e.own) for e in found[w]] for w in sorted(found)}


async def test_naming_a_line_teaches_its_words_at_the_vendor_and_anywhere(
    admin_client, admin, db_session
):
    await seed_units_via_service(db_session)
    quay = await _waiting(admin_client, admin, db_session, "Quayside Grocer", "GRK YGRT PLN 3.10")
    await _name(admin_client, quay["vendor"]["id"], "GRK YGRT PLN", "Greek yogurt plain")
    quay_id = uuid.UUID(quay["vendor"]["id"])
    assert _offered(await line_words.expansions(db_session, quay_id)) == {
        "grk": [("greek", 1, True)],
        "pln": [("plain", 1, True)],
        "ygrt": [("yogurt", 1, True)],
    }
    # Another vendor that hasn't printed these words gets the any-vendor fallback.
    tern = await _waiting(admin_client, admin, db_session, "Tern Market", "OAT MLK 2.40")
    assert _offered(await line_words.expansions(db_session, uuid.UUID(tern["vendor"]["id"]))) == {
        "grk": [("greek", 1, False)],
        "pln": [("plain", 1, False)],
        "ygrt": [("yogurt", 1, False)],
    }


async def test_a_vendors_own_expansion_wins_and_an_undo_stops_offering_it(
    admin_client, admin, db_session
):
    await seed_units_via_service(db_session)
    quay = await _waiting(admin_client, admin, db_session, "Quayside Grocer", "PLN BAGEL 1.10")
    tern = await _waiting(admin_client, admin, db_session, "Tern Market", "PLN BAGEL 1.20")
    quay_id, tern_id = uuid.UUID(quay["vendor"]["id"]), uuid.UUID(tern["vendor"]["id"])
    await line_words.learn(db_session, quay_id, "PLN BAGEL", "Plain bagel")
    await line_words.learn(db_session, quay_id, "PLN BAGEL", "Plain bagel")
    await line_words.learn(db_session, tern_id, "PLN BAGEL", "Plantain bagel")
    assert _offered(await line_words.expansions(db_session, tern_id))["pln"] == [
        ("plantain", 1, True)
    ]
    assert _offered(await line_words.expansions(db_session, quay_id))["pln"] == [("plain", 2, True)]
    await line_words.forget(db_session, tern_id, "PLN", "Plantain")
    # Undone at the vendor and at any vendor: the vendor falls back to the shared row.
    assert _offered(await line_words.expansions(db_session, tern_id))["pln"] == [
        ("plain", 2, False)
    ]


async def test_the_backfill_learns_from_aliases_once(admin_client, admin, db_session):
    await seed_units_via_service(db_session)
    quay = await _waiting(admin_client, admin, db_session, "Quayside Grocer", "GRK YGRT 3.10")
    await _name(admin_client, quay["vendor"]["id"], "GRK YGRT", "Greek yogurt")
    learned = sorted(
        (r.vendor_id is None, r.word, r.expansion, r.confirmed_count)
        for r in (await db_session.execute(select(LineWord))).scalars()
    )
    await db_session.execute(delete(LineWord))
    assert await line_words.backfill(db_session) == 4
    again = sorted(
        (r.vendor_id is None, r.word, r.expansion, r.confirmed_count)
        for r in (await db_session.execute(select(LineWord))).scalars()
    )
    assert again == learned
    assert await line_words.backfill(db_session) == 0
