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
    assert [i["path"] for i in items] == sorted([*names, "index_scratch.cook"])
    assert set(items[0]) == {
        "id",
        "path",
        "title",
        "status",
        "dirty",
        "content_hash",
        "last_indexed_at",
    }
    assert items[0]["title"] == "index_barley_moon_stew"
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
    assert detail["front_matter"] is None and detail["parse_error_message"] is None

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
    assert (await admin_client.delete(f"/api/v1/recipes/{old_id}")).status_code == 409
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
    assert (await admin_client.delete(f"/api/v1/recipes/{item['id']}")).status_code == 204
    assert (await admin_client.get(f"/api/v1/recipes/{item['id']}")).status_code == 404
    assert (await admin_client.delete(f"/api/v1/recipes/{item['id']}")).status_code == 404
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
