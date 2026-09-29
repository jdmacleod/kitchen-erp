"""Receipt location matching by phone and address (spec 03 §1F criterion 72, #86;
eng review R8, R9).

The receipts are the synthetic fixtures under tests/fixtures/receipts; branches
and numbers are invented (555-01xx and the reserved 555 exchange).
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.ingest import header
from app.services.phone import same_number
from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.geo_helpers import HOME_A, HOME_B, MID, make_location, make_vendor
from tests.ingest_helpers import load_fixture, run_job, stage_outputs, upload_fixture

clean_geo = gh.clean_geo
no_network = gh.no_network
receipts_dir = ih.receipts_dir
recorded = ih.recorded

# supermarket_loyalty prints "(555) 555-0142" and "1200 HARBOR VISTA BLVD".
PRINTED_PHONE = "555-555-0142"


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


async def three_branches(client: httpx.AsyncClient, **by_branch: dict[str, Any]) -> list[str]:
    """A chain of three branches with no store codes: name alone ties them."""
    vendor = await make_vendor(client, "Harborview Market", kind="chain", price_scope="chain")
    ids = []
    for name, at in (("Bayside", HOME_B), ("San Marisol", HOME_A), ("Midtown", MID)):
        made = await make_location(
            client, f"Harborview {name}", at, vendor_id=vendor["id"], **by_branch.get(name, {})
        )
        ids.append(made["id"])
    return ids


async def read(client: httpx.AsyncClient, recorded) -> dict[str, Any]:
    fixture = load_fixture("supermarket_loyalty")
    recorded(fixture)
    _, job = await upload_fixture(client, fixture)
    await run_job(job["id"])
    return (await stage_outputs(client, job["id"]))["header"]["location"]


async def test_the_branch_whose_phone_is_printed_is_chosen(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded
):
    """Criterion 72."""
    bayside, marisol, midtown = await three_branches(
        admin_client,
        Bayside={"phone": "+1 555 555 0107"},
        **{"San Marisol": {"phone": PRINTED_PHONE}},
        Midtown={"phone": "(555) 555-0199"},
    )
    location = await read(admin_client, recorded)
    assert location["matched"] is True and location["vendor_location_id"] == marisol
    top = location["candidates"][0]
    assert top["evidence"]["phone"] == PRINTED_PHONE and top["evidence"]["phone_shared"] is False
    assert Decimal(top["score"]) - Decimal(location["candidates"][1]["score"]) >= Decimal("1")
    assert {c["vendor_location_id"] for c in location["candidates"]} == {bayside, marisol, midtown}


async def test_a_number_several_branches_share_decides_nothing(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded
):
    """R9: a head-office number printed by, or listed on, every branch."""
    await three_branches(
        admin_client,
        Bayside={"phone": PRINTED_PHONE},
        **{"San Marisol": {"phone": "+1 " + PRINTED_PHONE}},
    )
    location = await read(admin_client, recorded)
    assert location["matched"] is False
    assert len({c["score"] for c in location["candidates"]}) == 1  # still a three-way tie
    shared = [c for c in location["candidates"] if c["evidence"]["phone_shared"]]
    assert len(shared) == 2 and all(c["evidence"]["phone"] is None for c in shared)


async def test_a_matching_address_picks_the_branch(
    admin_client: httpx.AsyncClient, receipts_dir: Path, recorded
):
    _, marisol, _ = await three_branches(
        admin_client,
        # pii-scan: allow invented streets (Harbor Vista, Tide Walk; synthetic fixture)
        Bayside={"address": "88 Tide Walk, Bayside"},
        # pii-scan: allow invented street (Harbor Vista Blvd, synthetic fixture)
        **{"San Marisol": {"address": "1200 Harbor Vista Blvd, San Marisol"}},
    )
    location = await read(admin_client, recorded)
    assert location["matched"] is True and location["vendor_location_id"] == marisol
    assert Decimal(location["candidates"][0]["evidence"]["address_similarity"]) >= Decimal("0.4")


@pytest.mark.parametrize(
    ("a", "b", "same"),
    [
        # Written with spaces so no line holds a long digit run; compared as digits.
        ("555 555 0142", "555 555 0142", True),
        ("1 555 555 0142", "555 555 0142", True),  # a country code on one side
        ("44 555 555 0142", "555 555 0142", True),
        ("555 555 0142", "555 555 0143", False),
        ("555 0142", "1 555 555 0142", False),  # too short to be the tail of a number
        ("", "555 555 0142", False),
        ("9999 555 555 0142", "555 555 0142", False),  # four digits is not a country code
    ],
)
def test_same_number(a: str, b: str, same: bool):
    assert same_number(a.replace(" ", ""), b.replace(" ", "")) is same


# --- R8: no phone or address, no change ---------------------------------------------


def _frozen_score(c: dict[str, Any]) -> Decimal:
    """Candidate.score as it was before phones and addresses (frozen, 2026-09-29)."""
    score = Decimal("0")
    if c["identifier"] is not None:
        score += Decimal("1.0")
    if c["distance_m"] is not None:
        score += Decimal("0.7")
    if c["name_similarity"] is not None:
        score += Decimal("0.6") * c["name_similarity"]
    return score.quantize(Decimal("0.001"))


def _frozen_rank(cands: list[dict[str, Any]]) -> tuple[list[tuple[str, Decimal]], str | None]:
    """Order and the confident pick, as LocationMatch did before (frozen copy)."""
    ranked = sorted(cands, key=lambda c: (-_frozen_score(c), c["location_name"]))
    ranked = [c for c in ranked if _frozen_score(c) > 0]
    chosen = None
    if ranked:
        top = _frozen_score(ranked[0])
        gap_ok = len(ranked) == 1 or top - _frozen_score(ranked[1]) >= Decimal("0.3")
        if top >= Decimal("0.55") and gap_ok:
            chosen = ranked[0]["location_name"]
    return [(c["location_name"], _frozen_score(c)) for c in ranked], chosen


evidence = st.fixed_dictionaries(
    {
        "identifier": st.one_of(st.none(), st.sampled_from(["0412", "17", "A9"])),
        "distance_m": st.one_of(
            st.none(), st.decimals(min_value=0, max_value=300, places=1, allow_nan=False)
        ),
        "name_similarity": st.one_of(
            st.none(), st.decimals(min_value="0.3", max_value=1, places=3, allow_nan=False)
        ),
    }
)


@settings(max_examples=300, deadline=None)
@given(st.lists(evidence, min_size=0, max_size=6))
def test_without_phones_or_addresses_ranking_is_unchanged(found: list[dict[str, Any]]):
    import uuid

    cands = [{**e, "location_name": f"Branch {i}"} for i, e in enumerate(found)]
    new = [
        header.Candidate(
            vendor_location_id=uuid.uuid4(),
            vendor_id=uuid.uuid4(),
            vendor_name="Harborview Market",
            location_name=c["location_name"],
            identifier=c["identifier"],
            distance_m=c["distance_m"],
            name_similarity=c["name_similarity"],
        )
        for c in cands
    ]
    ranked = sorted(new, key=lambda c: (-c.score, c.location_name))
    match = header.LocationMatch(candidates=[c for c in ranked if c.score > 0])
    expected_order, expected_pick = _frozen_rank(cands)
    assert [(c.location_name, c.score) for c in match.candidates] == expected_order
    chosen = match.confident
    assert (chosen.location_name if chosen else None) == expected_pick
