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

import pytest

from app.ingest.header import HEADER_TASK
from app.ingest.lines import LINES_TASK
from app.ingest.llm import NUM_CTX, NUM_PREDICT, LlmClient
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
