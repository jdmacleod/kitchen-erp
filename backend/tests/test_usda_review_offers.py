"""What USDA suggestions preselect, and changing a row's USDA food (#359)."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.catalog.names import singular_word
from app.core.ids import new_id
from app.models.catalog import RefUsdaPortion
from app.services.usda_review import _density_offers, _sig3, measure_label, plain_label
from tests.catalog_helpers import make_ingredient
from tests.test_usda_review import _group, _load, _ref, _review


def _portion(label: str, amount: str, unit: str, grams: str) -> RefUsdaPortion:
    return RefUsdaPortion(
        id=uuid.uuid4(),
        fdc_id=1,
        food_description="Syrup, invented",
        portion_label=label,
        portion_amount=Decimal(amount),
        portion_unit=unit,
        gram_weight=Decimal(grams),
        data_type="sr_legacy_food",
    )


@pytest.mark.parametrize(
    ("word", "singular"),
    [
        ("slices", "slice"),
        ("berries", "berry"),
        ("boxes", "box"),
        ("tomatoes", "tomato"),
        ("leaves", "leaf"),
        ("anchovies", "anchovy"),
        ("cloves", "clove"),
        ("glass", "glass"),
        ("hummus", "hummus"),
        ("molasses", "molasses"),
        ("medium", "medium"),
        ("serving", "serving"),
    ],
)
def test_singular_word(word, singular):
    assert singular_word(word) == singular


def test_a_measure_label_keeps_one_form_of_its_first_word():
    assert measure_label("Slices") == "slice"
    assert measure_label("berries, frozen") == "berry, frozen"
    assert measure_label("cup, chopped or diced") == "cup, chopped or diced"
    assert measure_label('stalk, large (11"-12" long)') == 'stalk, large (11"-12" long)'


@pytest.mark.parametrize(
    ("label", "plain"),
    [
        ("cup", True),
        ("cup, chopped or diced", True),
        ("fl oz", True),
        ("tablespoon", True),
        ("medium", True),
        ("slice", True),
        ("clove", True),
        ("extra large", True),
        ("nlea serving", False),
        ("strip medium", False),
        ("berry", False),
        ("oz, boneless", False),
        ("stalk, large", False),
        ("", False),
    ],
)
def test_only_labels_a_receipt_or_recipe_would_name_are_ticked(label, plain):
    assert plain_label(label) is plain


def test_densities_have_three_significant_figures():
    assert _sig3(Decimal("0.528341")) == Decimal("0.528")
    assert _sig3(Decimal("1.17856")) == Decimal("1.18")
    assert _sig3(Decimal("1.2")) == Decimal("1.2")
    assert str(_sig3(Decimal("1.0004"))) == "1"


def test_the_cup_is_preselected_and_a_spread_over_five_percent_is_noted():
    offers, spread = _density_offers(
        [
            _portion("1 tbsp", "1", "tablespoon", "17"),  # 1.150 g/ml
            _portion("1 cup", "1", "cup", "279"),  # 1.179 g/ml
            _portion("1 fl oz", "1", "fl oz", "35.8"),  # 1.211 g/ml
        ]
    )
    assert [(o.portion_label, str(o.density_g_per_ml), o.suggested) for o in offers] == [
        ("1 tbsp", "1.15", False),
        ("1 cup", "1.18", True),
        ("1 fl oz", "1.21", False),
    ]
    assert spread == 5


def test_without_a_cup_the_largest_portion_is_preselected_and_close_values_need_no_note():
    offers, spread = _density_offers(
        [
            _portion("1 tsp", "1", "teaspoon", "4.9"),
            _portion("2 tbsp", "2", "tablespoon", "29.6"),
            _portion("1 medium", "1", "medium", "100"),  # not a volume: no density
        ]
    )
    assert [(o.portion_label, o.suggested) for o in offers] == [("1 tsp", False), ("2 tbsp", True)]
    assert spread is None


async def test_rows_say_what_to_preselect(admin_client, db_session, owner_conn, tmp_path):
    await _load(db_session, tmp_path)
    flour = await make_ingredient(admin_client, "Flour")
    await _ref(owner_conn, flour["id"], 1001)
    for label, unit, grams in (
        ("1 nlea serving", "nlea serving", "30"),
        ("1 slices", "slices", "25"),
        ("1 slice", "slice", "25"),
    ):
        await owner_conn.execute(
            "INSERT INTO ref_usda_portion (id, fdc_id, food_description, portion_label, "
            "portion_amount, portion_unit, gram_weight, data_type) "
            "VALUES ($1, 1001, 'Flour, invented', $2, 1, $3, $4, 'sr_legacy_food')",
            new_id(),
            label,
            unit,
            Decimal(grams),
        )
    f = _group(await _review(admin_client), "Flour")
    assert [(d["portion_label"], d["suggested"]) for d in f["densities"]] == [
        ("1 cup", True),
        ("1 tablespoon", False),
    ]
    assert f["density_spread_pct"] is None
    # "slices" and "slice" are one measure; "nlea serving" is offered unticked.
    assert [(m["label"], m["suggested"]) for m in f["measures"]] == [
        ("nlea serving", False),
        ("slice", True),
    ]

    # A label held in another form is not offered again.
    await admin_client.post(
        f"/api/v1/ingredients/{flour['id']}/measures",
        json={"label": "Slices", "canonical_qty": "25", "source": "measured"},
    )
    f = _group(await _review(admin_client), "Flour")
    assert [m["label"] for m in f["measures"]] == ["nlea serving"]

    # Accepting by either spelling saves the offered one.
    r = await admin_client.post(
        f"/api/v1/usda/review/{flour['id']}", json={"measures": ["NLEA serving"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["saved"] == 1


async def test_a_wrong_food_can_be_changed_or_dropped_from_the_row(
    admin_client, db_session, owner_conn, tmp_path
):
    await _load(db_session, tmp_path)
    garlic = await make_ingredient(admin_client, "Garlic")
    await _ref(owner_conn, garlic["id"], 1001)  # linked to flour by mistake
    assert _group(await _review(admin_client), "Garlic")["fdc_id"] == 1001

    r = await admin_client.post(f"/api/v1/usda/review/{garlic['id']}/link", json={"fdc_id": 1002})
    assert r.status_code == 200, r.text
    g = _group(await _review(admin_client), "Garlic")
    assert g["usda_description"] == "Garlic, raw"
    assert [m["label"] for m in g["measures"]] == ["clove"]
    assert [d["portion_label"] for d in g["densities"]] == ["1 teaspoon"]
    preferred = await owner_conn.fetch(
        "SELECT external_id FROM ingredient_ref WHERE ingredient_id = $1 AND is_preferred",
        uuid.UUID(garlic["id"]),
    )
    assert [p["external_id"] for p in preferred] == ["1002"]

    r = await admin_client.post(f"/api/v1/usda/review/{garlic['id']}/link", json={"fdc_id": 424242})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unknown_food"

    r = await admin_client.post(f"/api/v1/usda/review/{garlic['id']}/link", json={"fdc_id": None})
    assert r.status_code == 200, r.text
    assert _group(await _review(admin_client), "Garlic") is None
    left = await owner_conn.fetchval(
        "SELECT count(*) FROM ingredient_ref WHERE ingredient_id = $1 AND is_preferred",
        uuid.UUID(garlic["id"]),
    )
    assert left == 0

    r = await admin_client.post(f"/api/v1/usda/review/{uuid.uuid4()}/link", json={"fdc_id": None})
    assert r.status_code == 404
