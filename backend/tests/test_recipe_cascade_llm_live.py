"""Opt-in: the cascade's model tier against a live model (07, 3C; VS2).

Run with ``KERP_LLM_TESTS=1 uv run pytest -m llm -s`` and a reachable
OLLAMA_BASE_URL. The schema admits only the shortlist, so the checks here are
about judgement, not format: a prepared name should pick its ingredient, a
form word should not be read past, and a name that is none of the candidates
should get nothing. The report is printed for choosing a model; one miss is
information, not a failure. Every name here is invented or generic.
"""

from __future__ import annotations

import os

import pytest

from app.ingest.llm import suggest_recipe_names

pytestmark = [
    pytest.mark.llm,
    pytest.mark.skipif(not os.environ.get("KERP_LLM_TESTS"), reason="set KERP_LLM_TESTS=1"),
]

SHORTLIST = [
    "garlic",
    "garlic powder",
    "cumin",
    "cumin seed",
    "shallot",
    "onion",
    "basil",
    "bay leaves",
    "butter",
    "unsalted butter",
]

# A recipe name, the labels an acceptable first pick may be, and labels that must not appear.
CASES = [
    ("minced garlic", {"garlic"}, {"garlic powder"}),
    ("finely chopped shallots", {"shallot"}, {"onion"}),
    ("ground cumin", {"cumin", "cumin seed"}, set()),
    ("fresh basil leaves", {"basil"}, {"bay leaves"}),
    ("softened butter", {"butter"}, set()),
    ("lanternfruit", set(), set(SHORTLIST)),  # none of them
]


async def test_model_picks_from_the_shortlist_and_reports() -> None:
    misses = []
    for name, acceptable, forbidden in CASES:
        picked = await suggest_recipe_names(name, SHORTLIST)
        assert all(label in SHORTLIST for label in picked)
        assert len(picked) <= 2
        verdict = "ok"
        if (acceptable and (not picked or picked[0] not in acceptable)) or (
            set(picked) & forbidden
        ):
            verdict = "miss"
            misses.append(name)
        print(f"{verdict:4} {name!r:28} -> {picked}")
    # Judgement, not format: more than half wrong means the model is not usable here.
    assert len(misses) <= len(CASES) // 2, misses
