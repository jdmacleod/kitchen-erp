"""Likely spellings for words OCR misread (2S, U9). Every word here is invented or generic."""

from __future__ import annotations

from app.services.spelling import suggest, words

VOCAB = words(["Flat-leaf parsley", "Serrano pepper", "Jalapeno pepper", "Meadowline yogurt"])


def test_a_word_one_letter_off_a_catalog_word_is_offered_in_its_case():
    assert suggest("ITALIAN PARSLEV", VOCAB) == [{"word": "PARSLEV", "maybe": "PARSLEY"}]
    assert suggest("serrand peppers", VOCAB) == [{"word": "serrand", "maybe": "serrano"}]


def test_a_letter_dropped_or_added_is_one_edit_too():
    assert suggest("MEADOWLNE YOGURT", VOCAB) == [{"word": "MEADOWLNE", "maybe": "MEADOWLINE"}]


def test_a_plural_of_a_known_word_is_not_a_misspelling():
    assert suggest("JALAPENO PEPPERS", VOCAB) == []


def test_short_words_and_unknown_words_far_from_any_are_left_alone():
    assert suggest("ORG BBY KESTRELWOOD", VOCAB) == []


def test_two_equally_near_words_suggest_neither():
    vocab = words(["Kestra blend", "Kestrel blend"])
    assert suggest("KESTRE", vocab) == []


async def test_a_draft_line_carries_its_likely_spelling(admin_client, admin):
    from tests.pricebook_helpers import make_location, make_product
    from tests.resolution_helpers import make_receipt_purchase

    await make_product(admin_client, "flat-leaf parsley", "Kestrel flat-leaf parsley")
    loc = await make_location(admin_client, "Larkspur Grocers", "Larkspur Harbour")
    pid = await make_receipt_purchase(
        admin.id, loc["id"], [{"raw_text": "ITALIAN PARSLEV", "line_total": "0.99"}]
    )
    (line,) = (await admin_client.get(f"/api/v1/purchases/{pid}")).json()["lines"]
    assert line["spelling"] == [{"word": "PARSLEV", "maybe": "PARSLEY"}]
