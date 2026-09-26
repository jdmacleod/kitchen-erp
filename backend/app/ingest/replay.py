"""Recorded model responses for deterministic, offline tests.

:class:`RecordedTransport` stands in for the Ollama server. It reads the JSON
schema title of each request (``ReceiptHeader``, ``ReceiptLines`` or
``ProductRank``) to pick the stage (``header``, ``lines``, ``rank``) and answers
from a fixture's ``llm_responses`` map. A value may be a single object or a list
of objects consumed in order (the last one repeats), so a test can script
"invalid, then valid". Strings are sent verbatim, which lets a fixture record
malformed JSON.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

STAGE_BY_SCHEMA_TITLE = {"ReceiptHeader": "header", "ReceiptLines": "lines", "ProductRank": "rank"}


class RecordedTransport(httpx.AsyncBaseTransport):
    def __init__(self, responses: Mapping[str, Any]) -> None:
        self._queues: dict[str, list[Any]] = {
            stage: list(value) if isinstance(value, list) else [value]
            for stage, value in responses.items()
        }
        self.requests: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        title = body.get("format", {}).get("title") if isinstance(body, dict) else None
        stage = STAGE_BY_SCHEMA_TITLE.get(title, title)
        self.requests.append(
            {
                "stage": stage,
                "url": str(request.url),
                "body": body,
                # What httpx was told to wait: connect, read, write and pool, in seconds.
                "timeout": request.extensions.get("timeout"),
            }
        )
        queue = self._queues.get(stage or "")
        if not queue:
            return httpx.Response(500, json={"error": f"no recorded response for {stage}"})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        content = item if isinstance(item, str) else json.dumps(item)
        return httpx.Response(
            200,
            json={
                "model": body.get("model"),
                "message": {"role": "assistant", "content": content},
                "done": True,
            },
        )
