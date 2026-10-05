"""Clip quality, CQ4: a clip whose images the browser could not read hands their
addresses to the lookup helper, which fetches them. The store is invented."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from app.core.config import get_settings
from app.schemas.products_interchange import FORMAT
from tests import test_page_captures as tpc
from tests import test_products_helper as tph

helper = tph.helper
store = tpc.store
post_answer = tph.post_answer
IMAGES = [f"https://cdn.juniper-market.example.test/img/oats-{n}.jpg" for n in range(6)]


@pytest.fixture(autouse=True)
def media_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "media"
    monkeypatch.setattr(get_settings(), "media_path", str(root))
    return root


def clip(images: list[dict] | None = None) -> dict:
    return {**tpc.page(), "image_urls": IMAGES, "images": images or []}


def jpeg() -> str:
    out = io.BytesIO()
    Image.new("RGB", (320, 240), (180, 140, 60)).save(out, format="JPEG")
    return base64.b64encode(out.getvalue()).decode()


async def queue(helper) -> list[dict]:
    r = await helper.get("/api/v1/lookup-requests", headers=helper.read_headers)
    return [i for i in r.json()["items"] if i["kind"] == "image"]


async def test_without_a_helper_nothing_is_queued(admin_client, store, owner_conn):
    r = await admin_client.post("/api/v1/product-captures", json=clip())
    assert r.status_code == 201, r.text
    assert await owner_conn.fetchval("SELECT count(*) FROM lookup_request") == 0


async def test_unreadable_images_go_to_the_helper_four_at_most(admin_client, store, helper):
    r = await admin_client.post("/api/v1/product-captures", json=clip())
    assert r.status_code == 201, r.text
    assert [i["value"] for i in await queue(helper)] == IMAGES[:4]


async def test_a_clip_that_saved_its_own_images_asks_for_none(admin_client, store, helper):
    saved = [{"url": IMAGES[0], "data_base64": jpeg()}]
    r = await admin_client.post("/api/v1/product-captures", json=clip(saved))
    assert r.status_code == 201, r.text
    assert await queue(helper) == []


async def test_the_helpers_photo_joins_the_proposal(admin_client, store, helper):
    proposal = (await admin_client.post("/api/v1/product-captures", json=clip())).json()
    first = (await queue(helper))[0]
    body = json.dumps(
        {
            "format": FORMAT,
            "request_id": first["id"],
            "photos": [
                {
                    "data_base64": jpeg(),
                    "source_kind": "vendor_listing",
                    "source_url": first["value"],
                }
            ],
        }
    )
    r = await post_answer(helper, body)
    assert r.status_code == 200 and r.json()["outcome"] == "merged", r.text
    photos = (await admin_client.get(f"/api/v1/product-proposals/{proposal['id']}")).json()[
        "photos"
    ]
    assert [p["source_url"] for p in photos] == [first["value"]]
    assert first["id"] not in [i["id"] for i in await queue(helper)]
