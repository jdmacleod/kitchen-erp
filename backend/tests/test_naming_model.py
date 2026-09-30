"""The model's suggestions for the naming pass (04, 2I criterion 65; #88).

The model is replayed from recorded answers. Its reply is untrusted: only what
validates as ``LineNaming`` counts, and its ingredient only when it exactly
names a catalog ingredient or a standard-list entry.
"""

from __future__ import annotations

from app.core.db import get_sessionmaker
from app.ingest.llm import BEGIN_DELIMITER, END_DELIMITER
from app.services import naming
from tests import ingest_helpers as ih
from tests.catalog_helpers import make_ingredient
from tests.test_naming import _queue

recorded = ih.recorded


async def _ask(client) -> int:
    r = await client.post("/api/v1/to-identify/naming/suggest")
    assert r.status_code == 200, r.text
    return r.json()["queued"]


async def _answer_all() -> int:
    n = 0
    async with get_sessionmaker()() as db:
        while await naming.run_suggestion_once(db):
            n += 1
    return n


async def _rows(client) -> dict[str, dict]:
    items = (await client.get("/api/v1/to-identify/naming")).json()["items"]
    return {row["raw_text_norm"]: row for row in items}


async def test_only_rows_the_wording_couldnt_name_are_asked_about(
    admin_client, admin, db_session, recorded
):
    await _queue(admin_client, admin, db_session)
    await make_ingredient(admin_client, "broccoli")
    # Broccoli is named by its wording; the flour and chicken rows are not.
    assert await _ask(admin_client) == 2
    rows = await _rows(admin_client)
    assert rows["WT BROCCOLI CROWNS"]["model"] is None
    assert rows["BNLS CHKN BRST"]["model"] == {"status": "asking", "name": None, "ingredient": None}
    # Asking again while waiting queues nothing more.
    assert await _ask(admin_client) == 0


async def test_an_answer_fills_the_row_with_a_known_ingredient(
    admin_client, admin, db_session, recorded
):
    await _queue(admin_client, admin, db_session)
    transport = recorded(
        {"naming": {"product_name": "Boneless chicken breast", "ingredient": "chicken breast"}}
    )
    await _ask(admin_client)
    assert await _answer_all() == 2
    # The wording travels inside the delimited block only.
    sent = transport.requests[0]["body"]["messages"][-1]["content"]
    block = sent.split(BEGIN_DELIMITER)[1].split(END_DELIMITER)[0].strip()
    assert block in {"BNLS CHKN BRST", "RVRBND BREAD FLR 2KG"}
    chicken = (await _rows(admin_client))["BNLS CHKN BRST"]["model"]
    assert chicken["status"] == "done" and chicken["name"] == "Boneless chicken breast"
    assert (chicken["ingredient"]["kind"], chicken["ingredient"]["key"]) == (
        "standard",
        "chicken-breast",
    )


async def test_a_catalog_ingredient_wins_over_its_standard_name(
    admin_client, admin, db_session, recorded
):
    await _queue(admin_client, admin, db_session)
    mine = await make_ingredient(admin_client, "chicken breast")
    recorded({"naming": {"product_name": "Chicken breast", "ingredient": "Chicken Breast"}})
    await _ask(admin_client)
    await _answer_all()
    chicken = (await _rows(admin_client))["BNLS CHKN BRST"]["model"]
    assert (chicken["ingredient"]["kind"], chicken["ingredient"]["id"]) == (
        "ingredient",
        mine["id"],
    )


async def test_an_ingredient_that_exists_nowhere_is_dropped(
    admin_client, admin, db_session, recorded
):
    await _queue(admin_client, admin, db_session)
    recorded({"naming": {"product_name": "moon cheese", "ingredient": "moon cheese wheel"}})
    await _ask(admin_client)
    await _answer_all()
    row = (await _rows(admin_client))["BNLS CHKN BRST"]["model"]
    assert (row["status"], row["name"], row["ingredient"]) == ("done", "Moon cheese", None)


async def test_a_reply_that_doesnt_validate_fails_and_can_be_asked_again(
    admin_client, admin, db_session, recorded
):
    await _queue(admin_client, admin, db_session)
    recorded({"naming": "this is not json"})
    await _ask(admin_client)
    await _answer_all()
    row = (await _rows(admin_client))["BNLS CHKN BRST"]["model"]
    assert row == {"status": "failed", "name": None, "ingredient": None}
    # Nothing was created from it.
    assert (await admin_client.get("/api/v1/to-identify")).json()["items"]
    # A failed row is asked about again; an answered one is not.
    recorded({"naming": {"product_name": "Chicken breast", "ingredient": None}})
    assert await _ask(admin_client) == 2
    await _answer_all()
    assert await _ask(admin_client) == 0


async def test_an_unreachable_model_fails_the_row_not_the_worker(admin_client, admin, db_session):
    await _queue(admin_client, admin, db_session)
    await _ask(admin_client)  # nothing listens at OLLAMA_BASE_URL in tests
    assert await _answer_all() == 2
    row = (await _rows(admin_client))["BNLS CHKN BRST"]["model"]
    assert row["status"] == "failed"


async def test_an_ingredient_named_inside_the_product_name_counts(
    admin_client, admin, db_session, recorded
):
    # "chicken" alone is on no list; the product name names chicken breast outright.
    await _queue(admin_client, admin, db_session)
    recorded({"naming": {"product_name": "Boneless chicken breast", "ingredient": "chicken"}})
    await _ask(admin_client)
    await _answer_all()
    chicken = (await _rows(admin_client))["BNLS CHKN BRST"]["model"]
    assert chicken["ingredient"]["key"] == "chicken-breast"
