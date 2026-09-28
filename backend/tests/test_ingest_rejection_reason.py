"""Why a model reply was rejected is logged in schema terms, never in its own words."""

from __future__ import annotations

import json

from app.ingest.llm import rejection_reason
from app.ingest.schemas import ReceiptLines

SECRET = "QUAYSIDE OAT MILK"  # stands in for receipt text that must not reach the log


def test_names_the_field_and_the_error_type():
    reply = json.dumps(
        {"lines": [{"raw_text": SECRET, "line_kind": "item", "line_total": "three"}]}
    )
    reason = rejection_reason(ReceiptLines, reply)
    assert reason == "lines.0.line_total:decimal_parsing"
    assert SECRET not in reason


def test_truncated_json_and_the_wrong_shape():
    assert rejection_reason(ReceiptLines, '{"lines": [{"raw_text": "') == "not_json"
    assert rejection_reason(ReceiptLines, "[]") == "not_object:list"


def test_a_missing_field_and_a_bad_kind():
    reply = json.dumps({"lines": [{"raw_text": SECRET, "line_kind": SECRET}]})
    reason = rejection_reason(ReceiptLines, reply)
    assert reason == "lines.0.line_kind:literal_error; lines.0.line_total:missing"
    assert SECRET not in reason


def test_caps_the_list():
    reply = json.dumps({"lines": [{"raw_text": "x", "line_kind": "item"}] * 7})
    assert rejection_reason(ReceiptLines, reply).endswith("(+2 more)")
