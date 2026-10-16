"""The model on the naming pass without a button, and its shortlist (#387)."""

from __future__ import annotations

import uuid

from app.core.config import get_settings
from app.ingest.llm import BEGIN_DELIMITER, END_DELIMITER
from app.services import line_words
from tests import ingest_helpers as ih
from tests.catalog_helpers import make_ingredient, make_product
from tests.test_naming import _queue
from tests.test_naming_model import _answer_all, _ask, _rows

recorded = ih.recorded


async def test_opening_the_page_asks_only_when_the_setting_is_on(
    admin_client, admin, db_session, monkeypatch
):
    await _queue(admin_client, admin, db_session)
    assert get_settings().naming_auto_suggest is False
    assert (await _rows(admin_client))["BNLS CHKN BRST"]["model"] is None

    monkeypatch.setattr(get_settings(), "naming_auto_suggest", True)
    rows = await _rows(admin_client)
    assert rows["BNLS CHKN BRST"]["model"] == {"status": "asking", "name": None, "ingredient": None}
    # Opening it again queues nothing new: each row is asked once.
    assert await _ask(admin_client) == 0


async def test_a_failed_answer_waits_for_the_button(
    admin_client, admin, db_session, monkeypatch, recorded
):
    await _queue(admin_client, admin, db_session)
    recorded({"naming": "not json at all"})
    monkeypatch.setattr(get_settings(), "naming_auto_suggest", True)
    await _rows(admin_client)
    await _answer_all()
    assert (await _rows(admin_client))["BNLS CHKN BRST"]["model"]["status"] == "failed"
    # Opening the page doesn't ask again; the button does.
    assert (await _rows(admin_client))["BNLS CHKN BRST"]["model"]["status"] == "failed"
    assert await _ask(admin_client) == 2


async def test_the_shortlist_travels_as_data_with_the_line(
    admin_client, admin, db_session, recorded
):
    loc = await _queue(admin_client, admin, db_session)
    chicken = await make_ingredient(admin_client, "chicken breast")
    await make_product(admin_client, chicken["id"], "Boneless chicken breast")
    await line_words.learn(db_session, uuid.UUID(loc["vendor"]["id"]), "BRST", "Breast")
    await db_session.commit()
    transport = recorded({"naming": {"product_name": "Chicken breast", "ingredient": None}})
    await _ask(admin_client)
    await _answer_all()
    sent = [r["body"]["messages"][-1]["content"] for r in transport.requests]
    asked = next(m for m in sent if "BNLS CHKN BRST" in m)
    task, rest = asked.split(BEGIN_DELIMITER)
    block = rest.split(END_DELIMITER)[0]
    assert "they are hints, not answers" in task
    assert "Boneless chicken breast" not in task  # catalog text stays out of the task
    assert "Product names already in this kitchen: Boneless chicken breast" in block
    assert "This store's abbreviations: brst = breast" in block
