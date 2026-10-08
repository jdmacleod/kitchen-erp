"""Duplicates against sizes and variants (04, 2P, criterion 100).

Every name here is invented; none is a household product.
"""

import uuid

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.catalog.sameness import DISTINGUISHING, Facts, compare, name_key

OZ8 = {"qty": "8", "unit": "oz"}
OZ16 = {"qty": "16", "unit": "oz"}
G227 = {"qty": "227", "unit": "g"}  # 8 oz, within 5%


def facts(name, brand=None, pack=None, gtin=None, ingredient_id=None):
    return Facts(name=name, brand=brand, pack=pack, gtin=gtin, ingredient_id=ingredient_id)


@pytest.mark.parametrize(
    ("a", "b", "kind"),
    [
        # Letter case, trademark signs, filler and plurals hide nothing.
        (facts("Juniper Oat Rounds"), facts("juniper oat round"), "same"),
        (facts("Fernhill™ Plum Jam", "Fernhill"), facts("Plum Jam", "Fernhill"), "same"),
        (facts("Kale Pesto made with Walnuts"), facts("Walnut Kale Pesto"), "same"),
        (facts("Fresh Original Lark Loaf"), facts("Lark Loaf"), "same"),
        (facts("Moss Bay's Kelp Chips", "Moss Bay's"), facts("Kelp Chips"), "same"),
        # The same words in two sizes, from the pack or from the name.
        (facts("Plum Jam", pack=OZ8), facts("Plum Jam", pack=OZ16), "other_size"),
        (facts("Plum Jam, 8 oz"), facts("Plum Jam, 16 oz"), "other_size"),
        (facts("Plum Jam", pack=OZ8), facts("Plum Jam", pack=G227), "same"),
        (facts("Plum Jam", pack=OZ8), facts("Plum Jam"), "same"),
        # A distinguishing word, or a different barcode, makes a variant.
        (facts("Red Quince Vinegar"), facts("White Quince Vinegar"), "variant"),
        (facts("Hot Fennel Links"), facts("Sweet Fennel Links"), "variant"),
        (facts("Sliced Tarn Cheese"), facts("Tarn Cheese"), "variant"),
        (
            facts("Plum Jam", gtin="00000000000017"),
            facts("Plum Jam", gtin="00000000000024"),
            "variant",
        ),
        # Anything else is only similar.
        (facts("Plum Jam"), facts("Plum Chutney"), "similar"),
        (facts("Plum Jam", "Fernhill"), facts("Plum Jam", "Copperleaf"), "similar"),
        (facts("Plum Jam", "Copperleaf Organic"), facts("Organic Plum Jam"), "same"),
        (facts("Plum Jam"), facts("Organic Plum Jam"), "variant"),
        # Filed under two ingredients, it is still the same product; a merge settles it.
        (
            facts("Plum Jam", ingredient_id=uuid.UUID(int=1)),
            facts("Plum Jam", ingredient_id=uuid.UUID(int=2)),
            "same",
        ),
    ],
)
def test_verdicts(a, b, kind):
    assert compare(a, b).kind == kind
    assert compare(b, a).kind == kind


def test_a_different_ingredient_is_reported():
    a = facts("Plum Jam", ingredient_id=uuid.UUID(int=1))
    b = facts("Plum Jam", ingredient_id=uuid.UUID(int=2))
    assert compare(a, b).reasons == ("words", "ingredient")


def test_variant_names_the_words_that_differ():
    v = compare(facts("Red Quince Vinegar"), facts("White Quince Vinegar"))
    assert (v.only_a, v.only_b) == (("red",), ("white",))


_word = st.text(alphabet="bcdfghjklmnpqrtvwxz", min_size=4, max_size=8)
_names = st.lists(_word, min_size=1, max_size=4).map(" ".join)
_packs = st.one_of(
    st.none(),
    st.builds(
        lambda q, u: {"qty": str(q), "unit": u},
        st.integers(min_value=1, max_value=5000),
        st.sampled_from(["g", "kg", "oz", "lb", "ml", "l", "each"]),
    ),
)
_gtins = st.one_of(st.none(), st.sampled_from(["00000000000017", "00000000000024"]))
_facts = st.builds(facts, _names, st.one_of(st.none(), _word), _packs, _gtins)


@given(_facts, _facts)
def test_compare_is_symmetric(a, b):
    assert compare(a, b).kind == compare(b, a).kind


@given(_facts, _facts)
def test_same_never_holds_across_barcodes_or_dimensions(a, b):
    if compare(a, b).kind != "same":
        return
    assert not (a.gtin and b.gtin and a.gtin != b.gtin)
    if a.pack and b.pack:
        mass_or_volume = {"g": "m", "kg": "m", "oz": "m", "lb": "m", "ml": "v", "l": "v"}
        assert mass_or_volume.get(a.pack["unit"], "e") == mass_or_volume.get(b.pack["unit"], "e")


@given(_names, st.integers(min_value=1, max_value=999), st.sampled_from(["oz", "g", "ct", "fl oz"]))
def test_a_printed_size_never_changes_the_key(name, qty, unit):
    assert name_key(f"{name}, {qty} {unit}").words == name_key(name).words


def test_distinguishing_words_survive_the_key():
    for word in DISTINGUISHING:
        assert word in name_key(f"{word} tarn cheese").words or word.endswith("s")
