"""The recipe endpoints (07, 3A): list, detail, status, rescan, relink, delete.

Session-only: a scoped token is refused (the route walk in test_scopes covers
every route; the explicit check here says why for recipes). Repositories and
recipes are invented and built under a temp dir with dulwich.
"""

from __future__ import annotations

import uuid

import asyncpg
import httpx

from app.core.grants import APP_ROLE
from tests.recipes_helpers import TempRepo, recipes_repo  # noqa: F401
from tests.test_scopes import token

RECIPE_TABLES = ("recipe", "recipe_ingredient", "recipe_name_ignore", "recipe_pin")


async def test_recipes_need_a_session(client: httpx.AsyncClient):
    for method, path in (
        ("GET", "/api/v1/recipes"),
        ("GET", "/api/v1/recipes/status"),
        ("POST", "/api/v1/recipes/rescan"),
        ("GET", f"/api/v1/recipes/{uuid.uuid4()}"),
        ("POST", f"/api/v1/recipes/{uuid.uuid4()}/relink"),
        ("DELETE", f"/api/v1/recipes/{uuid.uuid4()}"),
    ):
        r = await client.request(method, path)
        assert r.status_code == 401, (method, path)


async def test_no_token_scope_reaches_recipes(admin_client: httpx.AsyncClient):
    headers = await token(admin_client, "products:read")
    admin_client.cookies.clear()
    r = await admin_client.get("/api/v1/recipes", headers=headers)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "insufficient_scope"
    r = await admin_client.post("/api/v1/recipes/rescan", headers=headers)
    assert r.status_code == 403


async def test_status_reports_no_repository_and_the_rest_works(
    admin_client: httpx.AsyncClient, tmp_path, monkeypatch
):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "recipes_path", str(tmp_path / "none"))
    r = await admin_client.get("/api/v1/recipes/status")
    assert r.status_code == 200
    body = r.json()
    assert body["mount"] == "missing" and body["mounted"] is False
    assert body["counts"] == {"ok": 0, "parse_error": 0, "missing": 0} and body["total"] == 0
    r = await admin_client.post("/api/v1/recipes/rescan")
    assert r.status_code == 200 and r.json()["mounted"] is False
    assert (await admin_client.get("/api/v1/recipes")).json() == {"items": []}
    # Nothing else is affected: the health check and the catalog answer as before.
    assert (await admin_client.get("/api/v1/health")).status_code == 200
    assert (await admin_client.get("/api/v1/ingredients")).status_code == 200


async def test_rescan_lists_and_shows_recipes(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    names = recipes_repo.seed_fixtures()
    recipes_repo.commit("five")
    recipes_repo.write("index_scratch.cook", "Whisk @egg{2}.\n")  # untracked, so dirty
    r = await admin_client.post("/api/v1/recipes/rescan")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mounted"] and body["created"] == 6 and body["files"] == 6

    r = await admin_client.get("/api/v1/recipes")
    items = r.json()["items"]
    # Ordered by title, not path (UI-7.3): the untitled scratch file sorts by its fallback title.
    assert sorted(i["path"] for i in items) == sorted([*names, "index_scratch.cook"])
    assert [i["title"] for i in items] == sorted((i["title"] for i in items), key=str.lower)
    assert set(items[0]) == {
        "id",
        "path",
        "title",
        "servings",
        "servings_text",
        "status",
        "dirty",
        "content_hash",
        "last_indexed_at",
        "cost",
    }
    assert items[0]["title"] == "Barley moon stew"
    assert items[0]["servings"] == "4" and items[0]["servings_text"] == "4"
    assert [
        i["path"] for i in (await admin_client.get("/api/v1/recipes?dirty=true")).json()["items"]
    ] == ["index_scratch.cook"]
    assert (await admin_client.get("/api/v1/recipes?status=missing")).json()["items"] == []
    assert (await admin_client.get("/api/v1/recipes?status=bogus")).status_code == 422

    r = await admin_client.get(f"/api/v1/recipes/{items[0]['id']}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["head_commit"] == recipes_repo.head()
    assert detail["dirty"] is False and detail["relink"] is None
    assert detail["front_matter"] == {"title": "Barley moon stew", "servings": "4"}
    assert detail["parse_error_message"] is None
    assert [i["raw_name"] for i in detail["ingredients"]][:3] == ["pearl barley", "onion", "carrot"]

    status = (await admin_client.get("/api/v1/recipes/status")).json()
    assert status["mount"] == "mounted" and status["head_commit"] == recipes_repo.head()
    assert status["counts"]["ok"] == 6 and status["last_scan_at"] is not None
    assert (await admin_client.get(f"/api/v1/recipes/{uuid.uuid4()}")).status_code == 404


async def test_relink_confirms_a_proposal_and_delete_needs_a_missing_recipe(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("index_pea_soup.cook", "Simmer @peas{300%g}.\n")
    recipes_repo.write("index_other.cook", "Whisk @egg{2}.\n")
    recipes_repo.commit("two")
    await admin_client.post("/api/v1/recipes/rescan")
    by_path = {i["path"]: i for i in (await admin_client.get("/api/v1/recipes")).json()["items"]}
    old_id = by_path["index_pea_soup.cook"]["id"]
    other_id = by_path["index_other.cook"]["id"]

    # Not missing: neither removable nor relinkable.
    removed = await admin_client.delete(f"/api/v1/recipes/{old_id}")
    assert removed.status_code == 409
    r = await admin_client.post(f"/api/v1/recipes/{old_id}/relink", json={"target_id": other_id})
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_missing"

    recipes_repo.move("index_pea_soup.cook", "index_pea_soup_two.cook")
    recipes_repo.edit("index_pea_soup_two.cook", "Add @mint{some}.\n")
    assert (await admin_client.post("/api/v1/recipes/rescan")).json()["proposals"] == 1
    detail = (await admin_client.get(f"/api/v1/recipes/{old_id}")).json()
    assert detail["status"] == "missing"
    assert detail["relink"]["path"] == "index_pea_soup_two.cook"
    assert detail["relink"]["reason"].startswith("title similarity")
    new_id = detail["relink"]["target_id"]
    assert [
        i["id"] for i in (await admin_client.get("/api/v1/recipes?status=missing")).json()["items"]
    ] == [old_id]

    # Bad targets.
    r = await admin_client.post(f"/api/v1/recipes/{old_id}/relink", json={"target_id": old_id})
    assert r.status_code == 409 and r.json()["error"]["code"] == "same_recipe"
    r = await admin_client.post(
        f"/api/v1/recipes/{old_id}/relink", json={"target_id": str(uuid.uuid4())}
    )
    assert r.status_code == 404
    assert (await admin_client.post(f"/api/v1/recipes/{old_id}/relink", json={})).status_code == 422

    r = await admin_client.post(f"/api/v1/recipes/{old_id}/relink", json={"target_id": new_id})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == old_id and body["path"] == "index_pea_soup_two.cook"
    assert body["status"] == "ok" and body["dirty"] is True and body["relink"] is None
    assert (await admin_client.get(f"/api/v1/recipes/{new_id}")).status_code == 404
    paths = [i["path"] for i in (await admin_client.get("/api/v1/recipes")).json()["items"]]
    assert paths == ["index_other.cook", "index_pea_soup_two.cook"]
    assert (await admin_client.post("/api/v1/recipes/rescan")).json()["created"] == 0


async def test_delete_removes_a_missing_recipe(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    recipes_repo.write("index_a.cook", "Stir @oats{50%g}.\n")
    recipes_repo.write("index_b.cook", "Whisk @egg{2}.\n")
    await admin_client.post("/api/v1/recipes/rescan")
    item, _other = (await admin_client.get("/api/v1/recipes")).json()["items"]
    recipes_repo.remove("index_a.cook")
    await admin_client.post("/api/v1/recipes/rescan")
    assert (await admin_client.get(f"/api/v1/recipes/{item['id']}")).json()["status"] == "missing"
    removed = await admin_client.delete(f"/api/v1/recipes/{item['id']}")
    assert removed.status_code == 204
    assert (await admin_client.get(f"/api/v1/recipes/{item['id']}")).status_code == 404
    removed_again = await admin_client.delete(f"/api/v1/recipes/{item['id']}")
    assert removed_again.status_code == 404
    assert (await admin_client.get("/api/v1/recipes/status")).json()["total"] == 1


async def test_the_runtime_role_has_full_dml_on_the_recipe_tables(app_conn: asyncpg.Connection):
    for table in RECIPE_TABLES:
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert await app_conn.fetchval(
                "SELECT has_table_privilege($1, $2, $3)", APP_ROLE, table, privilege
            ), (table, privilege)
        assert not await app_conn.fetchval(
            "SELECT has_table_privilege($1, $2, 'TRUNCATE')", APP_ROLE, table
        )


async def test_an_alias_may_come_from_a_recipe(owner_conn: asyncpg.Connection):
    definition = await owner_conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = $1",
        "ck_ingredient_alias_source",
    )
    assert "'recipe'" in definition


async def test_list_filters_run_on_the_server(
    admin_client: httpx.AsyncClient,
    recipes_repo: TempRepo,  # noqa: F811
):
    """UI-7.3: search on title or path, and completeness, are the API's filters."""
    recipes_repo.seed_fixtures()
    # No ingredient marks: nothing to price, so its cost is complete once computed.
    recipes_repo.write(
        "drafts/zenith_soup.cook", ">> title: Zenith soup\nBoil the water and wait.\n"
    )
    recipes_repo.commit("six")
    assert (await admin_client.post("/api/v1/recipes/rescan")).status_code == 200

    async def titles(params: dict) -> list[str]:
        r = await admin_client.get("/api/v1/recipes", params=params)
        assert r.status_code == 200, r.text
        return [i["title"] for i in r.json()["items"]]

    assert await titles({"q": "LENTIL"}) == ["Lantern lentils"]  # case folded, on the title
    assert await titles({"q": "drafts/"}) == ["Zenith soup"]  # or on the path
    assert await titles({"q": "100%"}) == []  # a typed % is not a wildcard
    assert await titles({"q": "zz qx"}) == []

    # The scan costs every recipe it indexes (3D, package 6b). Nothing is priced,
    # so the five fixtures are incomplete; the recipe with no lines is complete.
    assert len(await titles({"completeness": "incomplete"})) == 5
    assert await titles({"completeness": "complete"}) == ["Zenith soup"]
    assert (await admin_client.get("/api/v1/recipes?completeness=half")).status_code == 422

    # Its snapshot says so line by line.
    zenith = next(
        i
        for i in (await admin_client.get("/api/v1/recipes")).json()["items"]
        if i["title"] == "Zenith soup"
    )
    cost = await admin_client.get(f"/api/v1/recipes/{zenith['id']}/cost")
    assert cost.status_code == 200, cost.text
    assert cost.json()["completeness"] == {
        "lines_total": 0,
        "lines_priced": 0,
        "lines_unpriced": 0,
        "lines_unconvertible": 0,
        "lines_unmapped": 0,
        "lines_negligible": 0,
    }
    assert await titles({"completeness": "complete"}) == ["Zenith soup"]
    assert "Zenith soup" not in await titles({"completeness": "incomplete"})
    assert await titles({"completeness": "incomplete", "q": "zenith"}) == []
