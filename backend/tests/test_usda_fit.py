"""Whether a USDA record describes an ingredient (#343). Pure: no database."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.catalog.standard import standard_list
from app.catalog.usda_fit import fits

DESCRIPTIONS = json.loads(
    (Path(__file__).parent / "fixtures" / "usda" / "standard_descriptions.json").read_text()
)["descriptions"]


@pytest.mark.parametrize(
    ("name", "description"),
    [
        ("rice water", "Watermelon, raw"),
        ("lanternroot", "Rye grain"),
        ("roasted duck stock", "Seeds, breadfruit seeds, roasted"),
        ("soft herbs", "Meat extender"),
        ("sea bream", "Oat bran, raw"),
    ],
)
def test_a_record_that_never_names_the_food_is_a_misfit(name, description):
    assert fits(name, description).verdict == "misfit"


@pytest.mark.parametrize(
    ("name", "description"),
    [
        ("fresh red chiles", "Spices, chili powder"),
        ("whole peeled tomatoes", "Tomatoes, sun-dried"),
        ("frozen meatballs", "Meatballs, meatless"),
        ("goat leg", "Goat, ground, raw"),
        ("coconut water", "Nuts, coconut milk, canned"),
    ],
)
def test_another_form_of_the_food_is_doubtful(name, description):
    assert fits(name, description).verdict == "doubtful"


@pytest.mark.parametrize(
    ("name", "description"),
    [
        ("plum tomatoes", "Tomatoes, red, ripe, raw"),
        ("baby spinach", "Spinach, baby"),
        ("ketchup", "Catsup"),
        ("beef, ground", "Beef, ground, 80% lean meat / 20% fat, raw"),
        ("short ribs", "Beef, rib, shortribs, separable lean and fat, raw"),
        ("star fruit", "Carambola, (starfruit), raw"),
        ("cod", "Fish, cod, Pacific, raw (may have been previously frozen)"),
        ("dried guajillo chiles", "Peppers, hot chile, sun-dried"),
    ],
)
def test_a_record_that_names_the_food_fits(name, description):
    assert fits(name, description).verdict == "fits"


def test_water_never_meets_watermelon_by_spelling():
    assert fits("water", "Watermelon, raw").verdict == "misfit"


def test_every_standard_entry_fits_its_own_usda_record():
    unfit = []
    for entry in standard_list().ingredients:
        if entry.fdc is None:
            continue
        assert str(entry.fdc) in DESCRIPTIONS, (
            f"{entry.key}: add the description of USDA food {entry.fdc} to the fixture"
        )
        fit = fits(entry.name, DESCRIPTIONS[str(entry.fdc)])
        if fit.verdict != "fits":
            unfit.append((entry.name, DESCRIPTIONS[str(entry.fdc)], fit.reason))
    assert unfit == []
