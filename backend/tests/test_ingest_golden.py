"""Golden stage outputs for every receipt fixture (spec 04, criterion 66).

The text reader and the header total rule moved out of the stages into
``app.ingest.readers`` so the reading benchmark measures the same code the
pipeline runs. This test pins what the ``header`` and ``lines`` stages write,
and the model requests they make, for every fixture, so a refactor of the
reading path cannot change either without a failing test.

Regenerate only on purpose, when a change is meant to alter stage output:
``KERP_UPDATE_GOLDEN=1 uv run pytest tests/test_ingest_golden.py``. Review the
diff of ``tests/fixtures/golden/`` before committing it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from tests import geo_helpers as gh
from tests import ingest_helpers as ih
from tests.ingest_helpers import fixture_names, load_fixture, run_job, stage_outputs, upload_fixture

no_network = gh.no_network
receipts_dir = ih.receipts_dir
recorded = ih.recorded

GOLDEN_DIR = Path(__file__).parent / "fixtures" / "golden"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


@pytest.fixture(autouse=True)
def _offline(no_network: None) -> None:
    return None


def _scrub(value: Any) -> Any:
    """Replace generated ids, which differ on every run, with a placeholder."""
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, str):
        return UUID.sub("<uuid>", value)
    return value


# Hex digits spelled as letters: a hex digest's digit runs read as loyalty-card
# numbers to the PII scanner. 16 characters still tell a changed prompt apart.
_HEX_AS_LETTERS = str.maketrans("0123456789", "ghijklmnop")  # pii-scan: allow digit alphabet


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16].translate(_HEX_AS_LETTERS)


def _request_record(request: dict[str, Any]) -> dict[str, Any]:
    body = request["body"]
    messages = json.dumps(body.get("messages"), sort_keys=True, ensure_ascii=False)
    return {
        "stage": request["stage"],
        "options": body.get("options"),
        "read_timeout": (request.get("timeout") or {}).get("read"),
        "messages_digest": _digest(messages),
    }


@pytest.mark.parametrize("name", fixture_names())
async def test_stage_outputs_match_golden(
    name: str,
    admin_client: httpx.AsyncClient,
    receipts_dir: Path,
    recorded,
):
    fixture = load_fixture(name)
    transport = recorded(fixture)
    _, job = await upload_fixture(admin_client, fixture)
    await run_job(job["id"])
    outputs = await stage_outputs(admin_client, job["id"])
    actual = _scrub(
        {
            "header": outputs["header"],
            "lines": outputs["lines"],
            "requests": [_request_record(r) for r in transport.requests],
        }
    )

    path = GOLDEN_DIR / f"{name}.json"
    if os.environ.get("KERP_UPDATE_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(actual, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
    expected = json.loads(path.read_text())
    assert actual == expected
