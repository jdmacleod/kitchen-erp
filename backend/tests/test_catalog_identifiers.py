"""Product codes, weighed-item labels, attributes and listing addresses (03, 1H).

Every code here is built from an invented body with its check digit computed,
or written with spaces and joined, so no test vector is a real product's.
"""

import string
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from app.catalog.attributes import validate_attributes
from app.catalog.barcodes import DEFAULT_LAYOUT, RwLayout, parse_random_weight
from app.catalog.identifiers import (
    AmbiguousCode,
    InvalidGtin,
    check_digit,
    classify_barcode,
    display,
    gs1_ok,
    gtin14,
    lookup_keys,
    upce_to_upca,
)
from app.catalog.listings import canonical_url


def j(spaced: str) -> str:
    return spaced.replace(" ", "")


def with_check(body: str) -> str:
    return body + str(check_digit(body))


def bad_check(code: str) -> str:
    return code[:-1] + str((int(code[-1]) + 1) % 10)


digits = st.text(alphabet=string.digits, min_size=1, max_size=1)


@given(st.text(alphabet=string.digits, min_size=11, max_size=11))
def test_upca_ean13_and_gtin14_forms_meet(body):
    upca = with_check(body)
    assert gtin14(upca) == "00" + upca
    assert gtin14("0" + upca) == gtin14(upca)
    assert gtin14("00" + upca) == gtin14(upca)
    if not upca.startswith("0000"):  # GS1 reserves that range of GTIN-12 for GTIN-8
        assert display("gtin", gtin14(upca)) == upca


@given(st.text(alphabet=string.digits, min_size=11, max_size=13))
def test_a_wrong_check_digit_is_refused(body):
    code = bad_check(with_check(body))
    with pytest.raises(InvalidGtin):
        gtin14(code)


def test_upce_expands_to_upca():
    # The textbook UPC-E example and its UPC-A expansion.
    assert upce_to_upca(j("0425 2614")) == j("0421 0000 5264")
    assert upce_to_upca(j("2425 2614")) is None  # number system 2 is not UPC-E


def test_eight_digits_need_a_symbology_when_valid_both_ways():
    ambiguous = None
    for n in range(1, 100000):
        code = with_check(f"0{n:06d}")
        upca = upce_to_upca(code)
        if upca and gs1_ok(code) and gs1_ok(upca) and upca.zfill(14) != code.zfill(14):
            ambiguous = code
            break
    assert ambiguous is not None
    with pytest.raises(AmbiguousCode):
        gtin14(ambiguous)
    assert gtin14(ambiguous, "ean8") == ambiguous.zfill(14)
    assert gtin14(ambiguous, "upce") == upce_to_upca(ambiguous).zfill(14)
    assert gtin14(ambiguous, "ean8") != gtin14(ambiguous, "upce")
    assert set(lookup_keys(ambiguous)) == {
        ("gtin", gtin14(ambiguous, "ean8")),
        ("gtin", gtin14(ambiguous, "upce")),
    }


def test_ean8_display_and_lookup():
    ean8 = with_check("9638507")  # number system 9: never UPC-E
    assert gtin14(ean8) == "000000" + ean8
    assert display("gtin", gtin14(ean8)) == ean8
    assert lookup_keys(ean8) == [("gtin", "000000" + ean8)]


def test_classify_keeps_other_codes_as_entered():
    assert classify_barcode(" 4011 ") == ("other", "4011")
    assert classify_barcode("ABC-123") == ("other", "ABC-123")
    assert classify_barcode(j("12 345 678 9")) == ("other", j("12 345 678 9"))  # nine digits
    with pytest.raises(InvalidGtin):
        classify_barcode(bad_check(with_check(j("01234 567890"))))


def test_lookup_keys_never_raise():
    assert lookup_keys(bad_check(with_check(j("01234 567890")))) == []
    assert lookup_keys("") == []
    assert lookup_keys("4011") == [("other", "4011")]


# --- weighed-item labels ------------------------------------------------------


def test_default_layout_reads_item_and_price():
    label = with_check(j("25123 401299"))  # item 51234, price field 01299
    rw = parse_random_weight(label)
    assert rw is not None and rw.item == "51234" and rw.price == Decimal("12.99")
    assert rw.weight is None


def test_zeroed_price_gives_none():
    label = with_check(j("25123 400000"))
    rw = parse_random_weight("00" + label)
    assert rw is not None and rw.item == "51234" and rw.price is None


def test_weight_layout_and_other_layouts():
    weight = RwLayout.from_json(
        {
            "item_start": 2,
            "item_len": 4,
            "price_start": 7,
            "price_len": 4,
            "price_kind": "weight_hundredths_lb",
        }
    )
    rw = parse_random_weight(with_check(j("20987 600350")), weight)
    assert (
        rw is not None and rw.item == "9876" and rw.weight == Decimal("3.50") and rw.price is None
    )
    none = RwLayout.from_json({"price_kind": "none"})
    assert parse_random_weight(with_check(j("25123 401299")), none).price is None
    assert RwLayout.from_json(None) == DEFAULT_LAYOUT
    with pytest.raises(ValueError):
        RwLayout.from_json({"item_start": 0})


def test_not_a_weighed_item_label():
    assert parse_random_weight(with_check(j("01234 567890"))) is None  # doesn't start with 2
    assert parse_random_weight(bad_check(with_check(j("25123 401299")))) is None
    assert parse_random_weight(j("25123 40129")) is None  # ten digits


@given(st.text(alphabet=string.digits, min_size=10, max_size=10))
def test_any_valid_label_parses_exactly(body):
    label = with_check("2" + body)
    rw = parse_random_weight(label)
    assert rw is not None and rw.item == label[1:6]
    field = int(label[6:11])
    assert rw.price == (Decimal(field) / 100 if field else None)


# --- attributes ---------------------------------------------------------------


def test_meat_attributes_validate():
    stored = validate_attributes(
        "meat", {"species": "pork", "primal": "shoulder", "bone": "bone_in"}
    )
    assert stored == {"species": "pork", "primal": "shoulder", "bone": "bone_in"}
    with pytest.raises(ValueError):
        validate_attributes("meat", {"species": "unicorn"})
    with pytest.raises(ValueError):
        validate_attributes("meat", {"species": "pork", "colour": "pink"})


def test_categories_without_a_model_take_only_an_empty_object():
    assert validate_attributes("produce", {}) == {}
    assert validate_attributes(None, None) == {}
    with pytest.raises(ValueError):
        validate_attributes("produce", {"species": "pork"})


# --- listing addresses ----------------------------------------------------------


def test_canonical_url_strips_query_fragment_and_store_scope():
    url, store = canonical_url(
        "https://Shop.Cardinal.example/pickup/store/417/product/hash-browns-061528/?utm=x#reviews"
    )
    assert url == "https://shop.cardinal.example/product/hash-browns-061528"
    assert store == "store/417"


def test_canonical_link_wins_but_the_page_keeps_its_store():
    url, store = canonical_url(
        "https://shop.cardinal.example/pickup/store/417/product/oat-milk",
        canonical="https://shop.cardinal.example/product/oat-milk",
    )
    assert url == "https://shop.cardinal.example/product/oat-milk"
    assert store == "store/417"


def test_unscoped_and_invalid_addresses():
    assert canonical_url("https://larkspur.example/p/hash-browns-061528") == (
        "https://larkspur.example/p/hash-browns-061528",
        None,
    )
    with pytest.raises(ValueError):
        canonical_url("javascript:alert(1)")
