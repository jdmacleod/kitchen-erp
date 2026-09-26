"""How long ingest waits for the model server, and for what (#34).

An unroutable model server took 75 s to report, because connecting was bounded
by the same budget as a whole generation; and a 25-line receipt used 94.6 s of
a flat 120 s. These assert the timeouts that go on the wire, so they need no
model server.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.core.config import get_settings
from app.ingest import lines as lines_mod
from app.ingest.errors import ModelTimeout, ModelUnavailable
from app.ingest.llm import LlmClient


class Capture(httpx.AsyncBaseTransport):
    """Answers every chat with an empty reply and keeps the timeouts it was sent."""

    def __init__(self, raise_: Exception | None = None) -> None:
        self.timeouts: list[dict[str, Any]] = []
        self.raise_ = raise_

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.timeouts.append(request.extensions["timeout"])
        if self.raise_ is not None:
            raise self.raise_
        return httpx.Response(200, json={"message": {"content": "{}"}})


async def test_connecting_is_bounded_apart_from_the_answer():
    transport = Capture()
    await LlmClient(transport=transport).chat({"model": "stub"})
    sent = transport.timeouts[-1]
    assert sent["connect"] == get_settings().llm_connect_timeout_seconds == 5.0
    assert sent["read"] == get_settings().llm_timeout_seconds


async def test_a_stage_can_ask_for_a_longer_budget_without_waiting_longer_to_connect():
    transport = Capture()
    await LlmClient(transport=transport).chat({"model": "stub"}, timeout_seconds=300)
    assert transport.timeouts[-1]["read"] == 300
    assert transport.timeouts[-1]["connect"] == 5.0
    # A budget shorter than the connect bound caps it.
    await LlmClient(transport=transport).chat({"model": "stub"}, timeout_seconds=2)
    assert transport.timeouts[-1]["connect"] == 2


async def test_an_unreachable_server_is_unavailable_and_a_slow_one_names_its_budget():
    with pytest.raises(ModelUnavailable):
        await LlmClient(transport=Capture(httpx.ConnectTimeout("no route"))).chat({"model": "stub"})
    with pytest.raises(ModelTimeout) as slow:
        await LlmClient(transport=Capture(httpx.ReadTimeout("slow"))).chat(
            {"model": "stub"}, timeout_seconds=207
        )
    assert "207s" in str(slow.value.detail)


def test_the_lines_budget_grows_with_the_receipt_and_stops_at_the_job_lock():
    settings = get_settings()
    text = "SYNTHETIC GROCER\n\nOATS 3.29\nMILK 2.49\n   \nTOTAL 5.78\n"
    # Four lines with something on them; blank ones do not count.
    assert (
        lines_mod.lines_budget_seconds(text)
        == settings.llm_timeout_seconds + 4 * settings.llm_lines_seconds_per_line
    )
    long = "\n".join(f"ITEM {i} 1.00" for i in range(500))
    assert lines_mod.lines_budget_seconds(long) == 0.8 * settings.ingest_lock_timeout_seconds
