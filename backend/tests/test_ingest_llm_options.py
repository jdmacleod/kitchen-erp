"""The request options that decide whether a long receipt survives the model.

Ollama's defaults are small: the context window is 2048 tokens and the
prediction budget is shorter still. A weekly shop of 40-60 lines plus the
ReceiptLines schema overruns both, and neither failure announces itself — the
server answers 200 with content that is truncated mid-object, or empty. The
reply then fails schema validation, the job retries twice more, and it lands in
review with no lines and the code "invalid_model_output", which points at the
model rather than at the request that was sent.

Measured against gpt-oss:20b before `num_ctx` and `num_predict` were set:
25 item lines parsed; 45 returned 2054 characters ending mid-object; 70
returned nothing at all. Afterwards, 100 parse.

These tests assert the options are on the wire rather than that a model behaves,
so they need no model server and cannot go flaky.
"""

from __future__ import annotations

import base64
import json
from typing import Any

import httpx
import pytest

from app.ingest.errors import InvalidModelOutput, ModelMissing, ModelUnavailable
from app.ingest.header import HEADER_TASK
from app.ingest.lines import LINES_TASK
from app.ingest.llm import (
    NUM_CTX,
    NUM_PREDICT,
    VISION_NUM_CTX,
    VISION_NUM_PREDICT,
    VISION_RETRY,
    VISION_SYSTEM_PROMPT,
    CallLedger,
    LlmClient,
    Usage,
)
from app.ingest.replay import RecordedTransport
from app.ingest.schemas import ReceiptHeader, ReceiptLines
from tests.ingest_helpers import load_fixture

# A receipt this long is ordinary, not an edge case, and it is what the old
# defaults failed on. Roughly four characters to a token, one JSON object of
# about 120 characters emitted per line.
LONGEST_SUPPORTED_LINES = 60


def _client(responses: dict) -> tuple[LlmClient, RecordedTransport]:
    transport = RecordedTransport(responses)
    return LlmClient(transport=transport), transport


async def test_extract_sends_context_and_prediction_budget():
    fixture = load_fixture("independent_minimal")
    client, transport = _client(fixture.llm_responses)

    await client.extract(ReceiptLines, LINES_TASK, fixture.ocr_text)

    options = transport.requests[-1]["body"]["options"]
    assert options["num_ctx"] == NUM_CTX
    assert options["num_predict"] == NUM_PREDICT
    # Temperature stays pinned: extraction is not a place for sampling.
    assert options["temperature"] == 0


async def test_header_extraction_sends_them_too():
    fixture = load_fixture("independent_minimal")
    client, transport = _client(fixture.llm_responses)

    await client.extract(ReceiptHeader, HEADER_TASK, fixture.ocr_text)

    options = transport.requests[-1]["body"]["options"]
    assert options["num_ctx"] == NUM_CTX
    assert options["num_predict"] == NUM_PREDICT


@pytest.mark.parametrize("budget", [NUM_CTX, NUM_PREDICT])
def test_budgets_cover_a_full_weekly_shop(budget: int):
    """A guard on the constants themselves, so nobody trims them back by eye.

    The prompt, the schema and the receipt all share the context window, and the
    reply has to fit in the prediction budget. Both are sized for a receipt of
    LONGEST_SUPPORTED_LINES lines with room to spare; drop either below that and
    long receipts start coming back truncated with no error to explain it.
    """
    chars_per_line = 120
    tokens_needed = LONGEST_SUPPORTED_LINES * chars_per_line / 4
    assert budget >= tokens_needed * 2, (
        f"{budget} tokens leaves no margin for a {LONGEST_SUPPORTED_LINES}-line receipt"
    )


def test_the_corpus_still_contains_a_long_receipt():
    """The fixture that would have caught this. Length is the point of it."""
    fixture = load_fixture("long_till_receipt")
    assert len(fixture.expected_lines) >= 50
    assert len(fixture.ocr_text.splitlines()) >= 50


# --- images, usage and the model a server cannot serve (spec 04, 2J) ----------------


class Scripted(httpx.AsyncBaseTransport):
    """Answers each request with the next scripted (status, body); the last repeats."""

    def __init__(self, *replies: tuple[int, Any]) -> None:
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        status, body = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(body, str):
            return httpx.Response(status, text=body)
        return httpx.Response(status, json=body)


def _answer(content: str, done_reason: str = "stop", **usage: int) -> tuple[int, dict]:
    return 200, {"message": {"content": content}, "done_reason": done_reason, **usage}


VALID_HEADER = '{"merchant_name": "LANTERN GROCERY", "total": "8.15"}'
# Ollama 0.34.4's answers, captured with synthetic requests.
NOT_FOUND = {"error": "model 'no-such-model:1b' not found"}
NOT_MULTIMODAL = {
    "error": '{"error":{"code":400,"message":"Multimodal data provided, but model does '
    'not support multimodal requests.","type":"invalid_request_error"}}'
}


@pytest.mark.parametrize(
    ("status", "body", "reason"),
    [(404, NOT_FOUND, "not_found"), (400, NOT_MULTIMODAL, "not_multimodal")],
)
async def test_a_model_the_server_cannot_serve_is_model_missing(status, body, reason):
    client = LlmClient(transport=Scripted((status, body)))
    with pytest.raises(ModelMissing) as missing:
        await client.chat({"model": "stub"})
    assert missing.value.reason == reason
    # Still a ModelUnavailable with the same code and detail: the text path and
    # naming keep waiting exactly as before.
    assert isinstance(missing.value, ModelUnavailable)
    assert (missing.value.code, missing.value.detail) == ("model_unavailable", f"http_{status}")


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (404, "<html>Not Found</html>"),  # a proxy's own 404
        (404, {"error": "page not found"}),
        (400, {"error": "invalid request"}),
        (500, NOT_FOUND),  # a server in trouble, whatever it says
        (503, {"error": "busy"}),
    ],
)
async def test_any_other_refusal_still_waits(status, body):
    client = LlmClient(transport=Scripted((status, body)))
    with pytest.raises(ModelUnavailable) as unavailable:
        await client.chat({"model": "stub"})
    assert not isinstance(unavailable.value, ModelMissing)
    assert unavailable.value.detail == f"http_{status}"


async def test_usage_is_read_from_the_reply_in_seconds():
    reply = _answer(
        VALID_HEADER,
        prompt_eval_count=1611,
        eval_count=420,
        total_duration=12_500_000_000,
        load_duration=3_250_000_000,
    )
    client = LlmClient(transport=Scripted(reply))
    await client.chat({"model": "stub"})
    assert client.last_usage == Usage(
        prompt_tokens=1611, completion_tokens=420, seconds=12.5, load_seconds=3.25
    )
    # A reply without them reports nothing rather than zero.
    client._transport = Scripted(_answer(VALID_HEADER))
    await client.chat({"model": "stub"})
    assert client.last_usage == Usage(None, None, None, None)


async def test_images_go_in_the_user_message_with_their_own_prompt_and_room():
    transport = Scripted(_answer(VALID_HEADER))
    client = LlmClient(transport=transport)
    image = b"\x89PNG synthetic"

    header, attempts = await client.extract(
        ReceiptHeader, HEADER_TASK, "", images=[image], think=False
    )

    assert (header.merchant_name, attempts) == ("LANTERN GROCERY", 1)
    body = transport.bodies[-1]
    system, user = body["messages"]
    assert system == {"role": "system", "content": VISION_SYSTEM_PROMPT}
    assert user["content"] == HEADER_TASK  # no text block: the image is the data
    assert user["images"] == [base64.b64encode(image).decode()]
    assert body["think"] is False
    assert body["options"] == {
        "temperature": 0,
        "num_ctx": VISION_NUM_CTX,
        "num_predict": VISION_NUM_PREDICT,
    }
    assert VISION_NUM_CTX == 16384 and VISION_NUM_PREDICT == 8192


async def test_a_text_call_sends_no_think_flag():
    transport = Scripted(_answer(VALID_HEADER))
    await LlmClient(transport=transport).extract(ReceiptHeader, HEADER_TASK, "TOTAL 8.15")
    assert "think" not in transport.bodies[-1]


async def test_vision_asks_once_more_a_little_warmer_then_gives_up():
    transport = Scripted(_answer("not json"), _answer(VALID_HEADER))
    client = LlmClient(transport=transport, max_retries=2)

    _, attempts = await client.extract(
        ReceiptHeader, HEADER_TASK, "", images=[b"img"], retry=VISION_RETRY
    )
    assert attempts == 2
    assert [b["options"]["temperature"] for b in transport.bodies] == [0, 0.3]

    transport = Scripted(_answer("not json"))
    client = LlmClient(transport=transport, max_retries=2)
    with pytest.raises(InvalidModelOutput) as invalid:
        await client.extract(ReceiptHeader, HEADER_TASK, "", images=[b"img"], retry=VISION_RETRY)
    # One retry, not the text path's two.
    assert invalid.value.attempts == 2 and len(transport.bodies) == 2


async def test_out_of_room_is_asked_again_only_at_another_temperature():
    cut_off = _answer('{"merchant_na', done_reason="length")

    transport = Scripted(cut_off, _answer(VALID_HEADER))
    _, attempts = await LlmClient(transport=transport).extract(
        ReceiptHeader, HEADER_TASK, "", images=[b"img"], retry=VISION_RETRY
    )
    assert attempts == 2

    # The text path repeats the same temperature, so it stops at once (#60).
    transport = Scripted(cut_off)
    with pytest.raises(InvalidModelOutput) as out_of_room:
        await LlmClient(transport=transport, max_retries=2).extract(
            ReceiptHeader, HEADER_TASK, "TOTAL 8.15"
        )
    assert out_of_room.value.code == "model_out_of_room"
    assert len(transport.bodies) == 1


async def test_one_ledger_records_every_attempt_of_every_client():
    ledger = CallLedger()
    primary = LlmClient(
        model="vision-a",
        role="primary",
        ledger=ledger,
        transport=Scripted(
            _answer("not json", eval_count=7, load_duration=2_000_000_000),
            _answer(VALID_HEADER, eval_count=30),
        ),
    )
    second = LlmClient(
        model="vision-b", role="second", ledger=ledger, transport=Scripted((404, NOT_FOUND))
    )

    await primary.extract(ReceiptHeader, HEADER_TASK, "", images=[b"img"], retry=VISION_RETRY)
    with pytest.raises(ModelMissing):
        await second.extract(ReceiptHeader, HEADER_TASK, "", images=[b"img"], retry=VISION_RETRY)

    assert [(c.role, c.model, c.outcome) for c in ledger.calls] == [
        ("primary", "vision-a", "invalid"),
        ("primary", "vision-a", "ok"),
        ("second", "vision-b", "missing"),
    ]
    assert [c.completion_tokens for c in ledger.calls] == [7, 30, None]
    assert ledger.calls[0].load_seconds == 2.0
    assert ledger.seconds == sum(c.seconds for c in ledger.calls) >= 0
    assert ledger.as_json()[2] == {
        "role": "second",
        "model": "vision-b",
        "outcome": "missing",
        "seconds": ledger.calls[2].seconds,
        "load_seconds": None,
        "prompt_tokens": None,
        "completion_tokens": None,
    }
