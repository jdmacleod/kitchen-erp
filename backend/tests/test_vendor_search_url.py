"""A vendor's search page, offered on its waiting receipt lines (04, 2D)."""

from __future__ import annotations

import pytest

from tests.pricebook_helpers import make_location


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.example.test/search?q={q}",
        "http://shop.example.test/s/{q}",
        "https://shop.example.test/find/{q}/all",
    ],
)
async def test_a_search_page_with_one_place_for_the_words_is_kept(admin_client, url):
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    r = await admin_client.patch(f"/api/v1/vendors/{loc['vendor']['id']}", json={"search_url": url})
    assert r.status_code == 200, r.text
    assert r.json()["search_url"] == url


@pytest.mark.parametrize(
    "url",
    [
        "https://shop.example.test/search",  # nowhere for the words
        "https://shop.example.test/?q={q}&again={q}",  # twice
        "javascript:alert({q})",  # not a web address
        "shop.example.test/?q={q}",  # no scheme
        "https://shop.example.test/?q={query}",
    ],
)
async def test_a_search_page_must_say_once_where_the_words_go(admin_client, url):
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    r = await admin_client.patch(f"/api/v1/vendors/{loc['vendor']['id']}", json={"search_url": url})
    assert r.status_code == 422, r.text


async def test_an_empty_search_page_clears_it(admin_client):
    loc = await make_location(admin_client, "Juniper Market", "Juniper Market")
    path = f"/api/v1/vendors/{loc['vendor']['id']}"
    await admin_client.patch(path, json={"search_url": "https://shop.example.test/?q={q}"})
    r = await admin_client.patch(path, json={"search_url": ""})
    assert r.status_code == 200 and r.json()["search_url"] is None
