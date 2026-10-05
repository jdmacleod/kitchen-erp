"""Clip quality, CQ1: titles lose the store's own name; a brand that is the name is dropped.

The storefronts and their products are invented.
"""

from __future__ import annotations

import json

import pytest

from app.catalog import extract as ladder
from app.catalog.proposals import merge, value


@pytest.mark.parametrize(
    ("title", "url", "meta", "vendor", "expected"),
    [
        (
            "Rolled Oats | Juniper Market",
            "https://www.junipermarket.example.test/p/1",
            {},
            "Juniper Market",
            "Rolled Oats",
        ),
        (
            "Rolled Oats, 30 oz - Lantern",
            "https://lantern.example.test/p/1",
            {"og:site_name": "Lantern"},
            None,
            "Rolled Oats, 30 oz",
        ),
        (
            "Hot Sausage - 16 Oz - canyon",
            "https://www.canyon.example.test/shop/1",
            {},
            "CANYON",
            "Hot Sausage - 16 Oz",
        ),
        (
            "Juniper Market: Large Eggs",
            "https://www.junipermarket.example.test/p/2",
            {},
            "Juniper Market",
            "Large Eggs",
        ),
        (
            "Oats | Organic",
            "https://www.junipermarket.example.test/p/3",
            {},
            "Juniper Market",
            "Oats | Organic",
        ),
        (
            "Juniper Market",
            "https://www.junipermarket.example.test/",
            {},
            "Juniper Market",
            "Juniper Market",
        ),
    ],
)
def test_a_title_loses_only_the_sites_own_name(title, url, meta, vendor, expected):
    names = ladder.site_names(url, meta, vendor)
    assert ladder.clean_title(title, names) == expected


def test_extraction_cleans_every_title_and_drops_a_brand_that_is_the_name():
    name = "Canyon Creek Hot Italian Sausage - 16 Oz"
    ld = json.dumps(
        {"@type": "Product", "name": name, "brand": {"name": name}, "offers": {"price": "6.99"}}
    )
    meta = {"og:title": f"{name} - canyon", "og:site_name": "Canyon"}
    ev = ladder.extract(
        "https://www.canyon.example.test/shop/p/1",
        meta=meta,
        structured_data=[ld],
        vendor_name="CANYON",
    )
    fields = merge(ev.candidates)
    assert value(fields, "title") == name
    # The meta title is the same name now: corroboration (the review shows it once).
    assert {a["value"] for a in fields["title"]["alternatives"]} == {name}
    assert "brand" not in fields


def test_a_real_brand_is_kept():
    ld = json.dumps({"@type": "Product", "name": "Rolled Oats", "brand": {"name": "Larkfield"}})
    ev = ladder.extract("https://shop.example.test/p/1", structured_data=[ld])
    assert value(merge(ev.candidates), "brand") == "Larkfield"


def test_cleaning_stays_fast_on_hostile_titles():
    import time

    names = {"shop"}
    hostile = " - " * 5000 + "x"
    started = time.perf_counter()
    ladder.clean_title(hostile, names)
    assert time.perf_counter() - started < 0.5


# --- CQ2: the page's microdata ---------------------------------------------------------------


def test_microdata_gives_price_brand_item_number_and_a_valid_gtin():
    from tests.test_captures import with_check

    code = with_check("0 1234 5678 901")
    meta = {
        "og:title": "Sparkling Water",
        "itemprop:price": "$2.49",
        "itemprop:brand": "Lantern Bay",
        "itemprop:sku": "LB-12",
        "itemprop:gtin13": code[1:],
    }
    fields = merge(ladder.extract("https://shop.example.test/p/1", meta=meta).candidates)
    assert (value(fields, "price"), value(fields, "brand"), value(fields, "item_number")) == (
        "2.49",
        "Lantern Bay",
        "LB-12",
    )
    assert value(fields, "gtin") == code.zfill(14)


def test_a_microdata_code_that_fails_its_check_digit_is_not_a_gtin():
    from tests.test_captures import with_check

    good = with_check("0 1234 5678 901")[1:]
    meta = {"itemprop:gtin13": good[:-1] + str((int(good[-1]) + 1) % 10)}
    assert "gtin" not in merge(
        ladder.extract("https://shop.example.test/p/1", meta=meta).candidates
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("$2.49", "2.49"),
        ("2.49", "2.49"),
        ("$1,299.00", "1299.00"),
        ("$ 3", "3"),
        ("free", None),
        ("-1", None),
        ("2.49 each", None),
        ("$2,49", None),
    ],
)
def test_money_from_a_tag(raw, expected):
    assert ladder._money(raw) == expected
