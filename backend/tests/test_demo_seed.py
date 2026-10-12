"""The synthetic demo household (`kerp seed demo`) as the screenshots rely on it.

Everything the seed writes is invented; the geography is the synthetic grid.
"""

from __future__ import annotations

import httpx

from app.core.db import get_sessionmaker
from app.services.demo import DEMO_ADMIN_EMAIL, DEMO_ADMIN_PASSWORD, seed_demo


async def test_the_demo_seed_fills_the_first_kitchens_list(client: httpx.AsyncClient):
    async with get_sessionmaker()() as db:
        counts = await seed_demo(db)
    assert counts["home_bases"] == 2 and counts["list_items"] == 9

    r = await client.post(
        "/api/v1/auth/login", json={"email": DEMO_ADMIN_EMAIL, "password": DEMO_ADMIN_PASSWORD}
    )
    assert r.status_code == 200, r.text
    kitchens = (await client.get("/api/v1/home-bases")).json()["items"]
    first = min(kitchens, key=lambda k: k["name"])
    lst = (await client.get("/api/v1/plan/list", params={"home_base_id": first["id"]})).json()
    assert lst["home_base_name"] == "Harbour Flat"
    assert len(lst["unchecked"]) == 8 and len(lst["checked"]) == 1
    by_name = {(i["ingredient"] or i["product"])["name"]: i for i in lst["unchecked"]}
    assert by_name["Eggs"]["claimed_by"]["display_name"] == "Second Cook"
    assert by_name["Yellow onions"]["deferrable"] is True
    assert by_name["Lemons"]["note"] == "for the sorbet"
    assert [a["unit"] for a in by_name["Whole milk"]["amounts"]] == ["l"]
    assert any(i["kind"] == "product" for i in lst["unchecked"])
