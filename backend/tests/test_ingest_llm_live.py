"""Opt-in: the fixture corpus against a live model, reporting accuracy.

Run with ``KERP_LLM_TESTS=1 uv run pytest -m llm -s`` and a reachable
OLLAMA_BASE_URL. Nothing here asserts exact output; the report is what
matters when deciding whether to swap models.
"""

from __future__ import annotations

import os
from decimal import Decimal

import pytest

from app.ingest.header import HEADER_TASK
from app.ingest.lines import LINES_TASK, parse_model_lines
from app.ingest.llm import LlmClient
from app.ingest.schemas import ReceiptHeader, ReceiptLines
from tests.ingest_helpers import fixture_names, load_fixture

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not os.environ.get("KERP_LLM_TESTS"), reason="set KERP_LLM_TESTS=1"),
]


async def test_corpus_accuracy_report(capsys):
    client = LlmClient(transport=None)
    header_hits = header_total = 0
    kind_hits = kind_total = 0
    field_hits = field_total = 0
    for name in fixture_names():
        fixture = load_fixture(name)
        header, _ = await client.extract(ReceiptHeader, HEADER_TASK, fixture.ocr_text)
        for key in ("merchant_name", "store_identifier", "subtotal", "tax", "total"):
            header_total += 1
            got = getattr(header, key)
            want = fixture.expected_header.get(key)
            if (str(got) if got is not None else None) == want:
                header_hits += 1
        parsed, _ = await client.extract(ReceiptLines, LINES_TASK, fixture.ocr_text)
        lines = parse_model_lines(parsed)
        for exp, line in zip(fixture.expected_lines, lines, strict=False):
            kind_total += 1
            kind_hits += line.line_kind == exp["line_kind"]
            for key in ("qty", "unit_price", "line_total"):
                field_total += 1
                want = None if exp[key] is None else Decimal(exp[key])
                field_hits += getattr(line, key) == want
        print(f"{name}: {len(lines)} lines (expected {len(fixture.expected_lines)})")
    print(f"header fields: {header_hits}/{header_total}")
    print(f"line kinds: {kind_hits}/{kind_total}")
    print(f"qty/price fields: {field_hits}/{field_total}")
