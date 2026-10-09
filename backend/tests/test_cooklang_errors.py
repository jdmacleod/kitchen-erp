"""Syntax errors report a line and a column; `parse` never raises (07, 3B, 12)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.recipes.cooklang import ParseError, Recipe, parse


@pytest.mark.parametrize(
    ("source", "line", "column", "fragment"),
    [
        ("Add @milk{1%cup to the pan", 1, 10, "never closed"),
        ("First step.\n\nThen @hot milk{1", 3, 15, "never closed"),
        ("Fry in #pan{ for a while", 1, 12, "never closed"),
        ("Wait ~{3%minutes", 1, 7, "never closed"),
        ("Add @butter{}(softened to the bowl", 1, 14, "note on `@butter` is never closed"),
        ("Stir.\n[- a comment that\nnever ends", 2, 1, "`[-` is never closed"),
        ("---\ntitle: Open\n", 1, 1, "never closed"),
        (">> no colon here\nStir.", 1, 1, ">> key: value"),
        ("Stir.\n\n  >> : empty key", 3, 3, ">> key: value"),
        ("---\ntitle: a\n- stray\n---\n", 3, 1, "front matter"),
    ],
)
def test_syntax_errors_locate_themselves(
    source: str, line: int, column: int, fragment: str
) -> None:
    result = parse(source)
    assert isinstance(result, ParseError), result
    assert (result.line, result.column) == (line, column), result
    assert fragment in result.message
    assert str(result).startswith(f"line {line}, column {column}: ")


def test_error_after_front_matter_counts_file_lines() -> None:
    result = parse("---\ntitle: Counted\ntags: [x]\n---\n\nAdd @salt{")
    assert isinstance(result, ParseError)
    assert (result.line, result.column) == (6, 10)


def test_invalid_yaml_that_is_not_key_value_reports_its_line() -> None:
    result = parse("---\ntitle: a\nlist:\n  - b\n c: [\n---\n")
    assert isinstance(result, ParseError)
    assert result.line >= 2
    assert "front matter" in result.message


@pytest.mark.parametrize(
    "source",
    [
        "Message me @ example",
        "It is ~ 5",
        "Recipe # 10{}",
        "Add @? thyme",
        "@{} and #{}",
        "Stray } and % and ) here",
        "Dashes --- in text and -- a comment",
        "﻿BOM first",
        "Windows\r\nline\r\n\r\nendings",
        "",
        "\n\n\n",
        "---\n---\n",
    ],
)
def test_not_errors(source: str) -> None:
    assert isinstance(parse(source), Recipe)


@settings(max_examples=600, deadline=None)
@given(st.text())
def test_arbitrary_text_never_raises(text: str) -> None:
    result = parse(text)
    assert isinstance(result, Recipe | ParseError)
    if isinstance(result, ParseError):
        assert result.line >= 1 and result.column >= 1


_MARKUP = st.sampled_from(
    [
        "@",
        "#",
        "~",
        "{",
        "}",
        "%",
        "(",
        ")",
        "[-",
        "-]",
        "--",
        "---",
        "=",
        ">>",
        ":",
        "\n",
        "\n\n",
        " ",
        "?",
        "/",
        "½",
    ]
)


@settings(max_examples=600, deadline=None)
@given(st.lists(st.one_of(_MARKUP, st.text(max_size=4)), max_size=24).map("".join))
def test_dense_markup_never_raises(text: str) -> None:
    result = parse(text)
    assert isinstance(result, Recipe | ParseError)


@settings(max_examples=300, deadline=None)
@given(st.text())
def test_front_matter_never_raises(body: str) -> None:
    result = parse(f"---\n{body}\n---\nStir.")
    assert isinstance(result, Recipe | ParseError)
    if isinstance(result, Recipe):
        assert all(isinstance(key, str) for key in result.front_matter)
