"""Opt-in: naming (2I) and the product ranker (rung 4) against a live model.

Spec 04 §2O switches the default text model; before a deployment switches, this
runs the two text uses the reading benchmark does not measure. Run with
``KERP_LLM_TESTS=1 uv run pytest -m llm -s`` and a reachable OLLAMA_BASE_URL.
Nothing asserts exact output: the report is what matters when choosing a model.
Every line and product here is invented.
"""

from __future__ import annotations

import os
import uuid

import pytest

from app.core.config import get_settings
from app.ingest.llm import rank_products, suggest_name

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not os.environ.get("KERP_LLM_TESTS"), reason="set KERP_LLM_TESTS=1"),
]

# An abbreviated line, and words a fair naming must contain.
NAMING_CASES = [
    ("BNLS SKNLS CHKN BRST", {"product": ["chicken", "breast"], "ingredient": ["chicken breast"]}),
    ("WHL MILK GAL", {"product": ["milk"], "ingredient": ["whole milk"]}),
    ("FLR TORTILLA 10CT", {"product": ["tortilla"], "ingredient": ["flour tortilla"]}),
    ("ORG BABY SPINACH", {"product": ["spinach"], "ingredient": ["spinach"]}),
    ("LARKFIELD BRD FLR", {"product": ["flour"], "ingredient": ["bread flour", "flour"]}),
]


def _product(name: str, brand: str | None, ingredient: str) -> dict[str, str | None]:
    return {"id": str(uuid.uuid4()), "name": name, "brand": brand, "ingredient": ingredient}


OATS = _product("Rolled Oats 1kg", "Hollow Creek", "rolled oats")
OAT_DRINK = _product("Oat Drink Unsweetened", "Brightfield", "oat milk")
BEANS = _product("Cut Green Beans 14.5oz", "Hollow Creek", "green beans")
FLOUR = _product("Strong White Bread Flour", "Larkfield Mills", "bread flour")
# A line, its shortlist, and the right answer (None: none of them fits).
RANK_CASES = [
    ("HLW CRK ROLLED OATS", [OATS, OAT_DRINK, BEANS], OATS["id"]),
    ("BRTFLD OAT DRNK UNSW", [OATS, OAT_DRINK], OAT_DRINK["id"]),
    ("HLW CRK CUT GRN BNS", [BEANS, OATS], BEANS["id"]),
    ("LRKFLD STRONG FLR", [FLOUR, OATS], FLOUR["id"]),
    ("PAPER TOWELS 6RL", [OATS, FLOUR, BEANS], None),
]


def _has_all(text: str | None, words: list[str]) -> bool:
    return text is not None and all(w in text.lower() for w in words)


async def test_naming_and_ranking_report(capsys):
    named = ingredients = 0
    for line, want in NAMING_CASES:
        answer = await suggest_name(line)
        named += _has_all(answer.product_name, want["product"])
        ingredients += any(_has_all(answer.ingredient, w.split()) for w in want["ingredient"])
    ranked = 0
    for line, shortlist, right in RANK_CASES:
        answer = await rank_products(line, shortlist)
        ranked += answer["product_id"] == right
    with capsys.disabled():
        model = get_settings().llm_model
        print(
            f"\nnaming  ({model}): products {named}/{len(NAMING_CASES)}, "
            f"ingredients {ingredients}/{len(NAMING_CASES)}"
        )
        print(f"ranking ({model}): {ranked}/{len(RANK_CASES)} right, including one with no match")
