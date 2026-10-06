"""Recorded model responses for deterministic, offline tests.

:class:`RecordedTransport` stands in for the Ollama server. It reads the JSON
schema title of each request (``ReceiptHeader``, ``ReceiptLines``, ``ProductRank``
or ``LineNaming``) to pick the stage (``header``, ``lines``, ``rank``, ``naming``)
and answers from a fixture's ``llm_responses`` map. A value may be a single object or a list
of objects consumed in order (the last one repeats), so a test can script
"invalid, then valid". Strings are sent verbatim, which lets a fixture record
malformed JSON.

A request without a schema, such as an OCR model's plain transcription, is the
``chat`` stage. A key of the form ``"<stage>@<model>"`` (``"lines@vision-a"``) answers only
requests for that model and wins over the plain stage key, so a test can script
two readers that send the same schema. Requests for any other model fall back
to the plain key.

Replies carry the usage fields Ollama reports, derived from the request and the
answer so they are the same on every run: about four characters to a token, a
second per reply and no load time. A streamed request (``"stream": true``, as a
transcription sends) gets the same reply as a single newline-delimited chunk.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx

STAGE_BY_SCHEMA_TITLE = {
    "ReceiptHeader": "header",
    "ReceiptLines": "lines",
    "ProductRank": "rank",
    "LineNaming": "naming",
}


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
        # A request with no schema (a plain transcription) is the "chat" stage.
        stage = STAGE_BY_SCHEMA_TITLE.get(title, title) if title else "chat"
        model = body.get("model") if isinstance(body, dict) else None
        self.requests.append(
            {
                "stage": stage,
                "model": model,
                "url": str(request.url),
                "body": body,
                # What httpx was told to wait: connect, read, write and pool, in seconds.
                "timeout": request.extensions.get("timeout"),
            }
        )
        queue = self._queues.get(f"{stage}@{model}") or self._queues.get(stage or "")
        if not queue:
            return httpx.Response(500, json={"error": f"no recorded response for {stage}"})
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        content = item if isinstance(item, str) else json.dumps(item)
        prompt = json.dumps(body.get("messages"), ensure_ascii=False)
        reply = {
            "model": model,
            "message": {"role": "assistant", "content": content},
            "done": True,
            "done_reason": "stop",
            "prompt_eval_count": len(prompt) // 4,
            "eval_count": len(content) // 4,
            "total_duration": 1_000_000_000,
            "load_duration": 0,
        }
        if body.get("stream"):
            # A streamed request gets the reply as one chunk of newline-delimited JSON.
            return httpx.Response(200, content=(json.dumps(reply) + "\n").encode())
        return httpx.Response(200, json=reply)
