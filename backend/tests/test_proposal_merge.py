"""The proposal merge (04, 2L): precedence by source, confidence only within one (PR12)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.catalog.proposals import (
    Candidate,
    UnknownCandidate,
    candidates_of,
    has_conflict,
    merge,
    model_confidence,
    packs_disagree,
    with_person,
)

D = Decimal


def gtin(body: str) -> str:
    """A GTIN-14 from its spaced body, with its check digit computed."""
    from app.catalog.identifiers import check_digit

    digits = body.replace(" ", "")
    return digits + str(check_digit(digits))


G1 = gtin("000 1234 5678 90")
G2 = gtin("000 9876 5432 10")


def test_the_higher_source_wins_whatever_the_confidence():
    fields = merge(
        [
            Candidate("brand", "Model Guess", "model", D("0.6")),
            Candidate("brand", "Page Brand", "page_meta"),
            Candidate("brand", "Maker Brand", "manufacturer"),
        ]
    )
    assert fields["brand"]["value"] == "Maker Brand"
    assert fields["brand"]["source"] == "manufacturer"
    assert [a["source"] for a in fields["brand"]["alternatives"]] == ["page_meta", "model"]


def test_title_ranks_an_adapter_above_the_manufacturer():
    fields = merge(
        [
            Candidate("title", "Maker title", "manufacturer"),
            Candidate("title", "Shop title", "adapter"),
        ]
    )
    assert fields["title"]["value"] == "Shop title"


def test_confidence_breaks_ties_only_within_one_source():
    fields = merge(
        [
            Candidate("title", "Low", "model", D("0.2")),
            Candidate("title", "High", "model", D("0.55")),
        ]
    )
    assert fields["title"]["value"] == "High"


def test_a_model_answer_only_fills_and_its_confidence_is_capped():
    assert model_confidence("0.95", photo=False) == D("0.6")
    assert model_confidence(0.9, photo=True) == D("0.5")
    fields = merge(
        [
            Candidate("title", "From the model", "model", D("0.6")),
            Candidate("title", "Addr", "address"),
        ]
    )
    assert fields["title"]["value"] == "From the model"  # it fills an otherwise weak field
    with pytest.raises(UnknownCandidate):
        merge([Candidate("title", "x", "model", D("0.9"))])


def test_unknown_fields_and_sources_are_refused():
    with pytest.raises(UnknownCandidate):
        merge([Candidate("colour", "red", "page_data")])
    with pytest.raises(UnknownCandidate):
        merge([Candidate("ingredients_text", "flour", "page_meta")])


def test_two_gtins_conflict_and_a_person_settles_it():
    fields = merge([Candidate("gtin", G1, "page_data"), Candidate("gtin", G2, "scan")])
    assert fields["gtin"]["source"] == "scan" and fields["gtin"]["conflict"]
    assert has_conflict(fields) == ["gtin"]
    settled = with_person(fields, {"gtin": G1})
    assert settled["gtin"]["source"] == "person" and not settled["gtin"]["conflict"]
    assert {a["source"] for a in settled["gtin"]["alternatives"]} == {"scan", "page_data"}


def test_the_same_gtin_twice_is_corroboration():
    fields = merge([Candidate("gtin", G1, "page_data"), Candidate("gtin", G1, "scan")])
    assert not fields["gtin"]["conflict"]


@pytest.mark.parametrize(
    ("a", "b", "disagree"),
    [
        ({"qty": "400", "unit": "g"}, {"qty": "410", "unit": "g"}, False),
        ({"qty": "400", "unit": "g"}, {"qty": "450", "unit": "g"}, True),
        ({"qty": "1", "unit": "kg"}, {"qty": "1000", "unit": "g"}, False),
        ({"qty": "16", "unit": "oz"}, {"qty": "454", "unit": "g"}, False),
        ({"qty": "1", "unit": "l"}, {"qty": "1", "unit": "kg"}, True),
        ({"qty": "1", "unit": "nope"}, {"qty": "1", "unit": "g"}, True),
    ],
)
def test_pack_sizes_more_than_five_percent_apart_disagree(a, b, disagree):
    assert packs_disagree(a, b) is disagree


def test_pack_conflict_is_flagged():
    fields = merge(
        [
            Candidate("pack", {"qty": "400", "unit": "g"}, "page_data"),
            Candidate("pack", {"qty": "500", "unit": "g"}, "page_meta"),
        ]
    )
    assert fields["pack"]["conflict"]


def test_merged_fields_round_trip_to_candidates():
    original = [
        Candidate("title", "A", "page_data"),
        Candidate("title", "B", "model", D("0.4")),
        Candidate("price", "3.49", "page_data"),
    ]
    fields = merge(original)
    assert merge(candidates_of(fields)) == fields
