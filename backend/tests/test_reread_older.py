"""Drafts read with older line rules, re-checked together (04, 2S; issue 382).

The fixture's first reading here runs under rules version 0, as if read before
the current rules; sometimes with the pass that joins quantity rows switched off
too, so today's rules would change its lines. Every vendor and wording is invented.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.errors import ApiError
from app.ingest import assess, structure
from app.services import reread
from tests import ingest_helpers as ih
from tests.ingest_helpers import load_fixture, run_job, stage_outputs, upload_fixture
from tests.pricebook_helpers import make_location

receipts_dir = ih.receipts_dir
recorded = ih.recorded

FIXTURE = "two_line_quantities"
OLDER = "/api/v1/rereads/older-rules"


def _no_joining(lines, *_, **__):
    return lines, []


async def _older_reading(
    client: httpx.AsyncClient, recorded, monkeypatch: pytest.MonkeyPatch, *, changes: bool
) -> tuple[str, str, Any]:
    """Read the fixture under rules version 0; with ``changes``, rows left unjoined too."""
    fixture = load_fixture(FIXTURE)
    transport = recorded(fixture)
    _, job = await upload_fixture(client, fixture)
    with monkeypatch.context() as m:
        m.setattr(assess, "LINE_RULES_VERSION", 0)
        if changes:
            m.setattr(structure, "merge_quantity_lines", _no_joining)
        await run_job(job["id"])
    outputs = await stage_outputs(client, job["id"])
    assert outputs["lines"]["rules_version"] == 0
    return outputs["lines"]["purchase_id"], job["id"], transport


async def _older(client: httpx.AsyncClient) -> dict[str, Any]:
    r = await client.get(OLDER)
    assert r.status_code == 200, r.text
    return r.json()


async def _recheck(client: httpx.AsyncClient) -> dict[str, Any]:
    r = await client.post(f"{OLDER}/recheck")
    assert r.status_code == 200, r.text
    return r.json()


async def _inbox_kinds(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    r = await client.get("/api/v1/inbox")
    assert r.status_code == 200, r.text
    return {item["kind"]: item for item in r.json()["items"]}


async def test_a_reading_records_its_rules_and_is_not_older(admin_client, receipts_dir, recorded):
    fixture = load_fixture(FIXTURE)
    recorded(fixture)
    _, job = await upload_fixture(admin_client, fixture)
    await run_job(job["id"])
    outputs = await stage_outputs(admin_client, job["id"])
    assert outputs["lines"]["rules_version"] == assess.LINE_RULES_VERSION
    assert (await _older(admin_client))["older"] == 0
    assert "older_rules" not in await _inbox_kinds(admin_client)


async def test_rechecking_lists_the_drafts_that_would_change(
    admin_client, receipts_dir, recorded, monkeypatch
):
    purchase_id, job_id, transport = await _older_reading(
        admin_client, recorded, monkeypatch, changes=True
    )
    found = await _older(admin_client)
    assert (found["older"], found["ready"], found["checked"]) == (1, [], None)
    row = (await _inbox_kinds(admin_client))["older_rules"]
    assert row["title"] == "1 draft was read with older rules"
    assert (row["action_label"], row["action_route"]) == ("Re-check it", "/shop/receipts/recheck")

    calls = len(transport.requests)
    done = await _recheck(admin_client)
    assert len(transport.requests) == calls  # no model call
    assert done["checked"] == {"ready": 1, "no_changes": 0, "skipped": 0}
    assert done["older"] == 0
    [ready] = done["ready"]
    assert ready["purchase_id"] == purchase_id
    assert ready["counts"]["changed"] + ready["counts"]["added"] + ready["counts"]["removed"] > 0
    assert (ready["adds_up"], ready["off_by"]) == (True, None)

    # Nothing is applied: the draft's lines are as they were, and its proposal opens.
    purchase = (await admin_client.get(f"/api/v1/purchases/{purchase_id}")).json()
    assert purchase["status"] == "draft"
    assert purchase["reread"]["open"] == {"id": ready["reread_id"], "status": "ready"}
    assert (await stage_outputs(admin_client, job_id))["lines"]["rules_version"] == 0
    kinds = await _inbox_kinds(admin_client)
    assert "older_rules" not in kinds and "reread" in kinds

    # Discarding it is a person's answer under today's rules: it is not counted again.
    r = await admin_client.post(f"/api/v1/rereads/{ready['reread_id']}/dismiss")
    assert r.status_code == 200, r.text
    assert (await _older(admin_client))["older"] == 0


async def test_applying_records_todays_rules(admin_client, receipts_dir, recorded, monkeypatch):
    _, job_id, _ = await _older_reading(admin_client, recorded, monkeypatch, changes=True)
    [ready] = (await _recheck(admin_client))["ready"]
    r = await admin_client.post(f"/api/v1/rereads/{ready['reread_id']}/apply")
    assert r.status_code == 200, r.text
    lines = (await stage_outputs(admin_client, job_id))["lines"]
    assert lines["rules_version"] == assess.LINE_RULES_VERSION
    assert (await _older(admin_client))["older"] == 0


async def test_a_draft_whose_recheck_changes_nothing_stops_being_counted(
    admin_client, receipts_dir, recorded, monkeypatch
):
    await _older_reading(admin_client, recorded, monkeypatch, changes=False)
    assert (await _older(admin_client))["older"] == 1
    done = await _recheck(admin_client)
    assert done["checked"] == {"ready": 0, "no_changes": 1, "skipped": 0}
    assert (done["older"], done["ready"]) == (0, [])
    assert "older_rules" not in await _inbox_kinds(admin_client)

    # A later change to the rules makes it older again.
    monkeypatch.setattr(assess, "LINE_RULES_VERSION", assess.LINE_RULES_VERSION + 1)
    assert (await _older(admin_client))["older"] == 1


async def test_committed_purchases_are_left_out(admin_client, receipts_dir, recorded, monkeypatch):
    purchase_id, _, _ = await _older_reading(admin_client, recorded, monkeypatch, changes=True)
    location = await make_location(admin_client, "Lantern Row Grocery", "Lantern Row Grocery")
    r = await admin_client.patch(
        f"/api/v1/purchases/{purchase_id}", json={"vendor_location_id": location["id"]}
    )
    assert r.status_code == 200, r.text
    r = await admin_client.post(f"/api/v1/purchases/{purchase_id}/commit")
    assert r.status_code == 200, r.text
    assert (await _older(admin_client))["older"] == 0
    done = await _recheck(admin_client)
    assert done["checked"] == {"ready": 0, "no_changes": 0, "skipped": 0}
    r = await admin_client.get(f"/api/v1/purchases/{purchase_id}/rereads/latest")
    assert r.status_code == 404  # no re-read was made for it


async def test_a_draft_whose_reading_kept_no_answer_is_left_out(
    admin_client, receipts_dir, recorded, monkeypatch, owner_conn
):
    _, job_id, _ = await _older_reading(admin_client, recorded, monkeypatch, changes=True)
    assert (await _older(admin_client))["older"] == 1
    await owner_conn.execute(
        "INSERT INTO ingest_stage_result (id, job_id, stage, adapter, adapter_version, output, "
        "duration_ms) SELECT gen_random_uuid(), job_id, stage, adapter, adapter_version, "
        "output - 'read_lines', duration_ms FROM ingest_stage_result "
        "WHERE job_id = $1::uuid AND stage = 'lines'",
        job_id,
    )
    assert (await _older(admin_client))["older"] == 0


async def test_a_draft_that_cannot_be_rechecked_is_skipped_not_failed(
    admin_client, receipts_dir, recorded, monkeypatch
):
    await _older_reading(admin_client, recorded, monkeypatch, changes=True)

    async def opened_elsewhere(*_, **__):
        raise ApiError(409, "reread_open", "This receipt already has a re-read open.")

    monkeypatch.setattr(reread, "request", opened_elsewhere)
    done = await _recheck(admin_client)
    assert done["checked"] == {"ready": 0, "no_changes": 0, "skipped": 1}
    assert done["older"] == 1
