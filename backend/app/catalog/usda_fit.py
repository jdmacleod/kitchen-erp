"""Whether a USDA FoodData Central record describes an ingredient (#343). Pure: no I/O.

A wrong link is worse than none: a cooking liquid linked to a melon record takes
the melon's food group, density and measures. So a link is taken on its own only
when the record names the ingredient's food. The verdict is:

- **fits**: the record names the ingredient's head noun ("tomatoes" in "plum
  tomatoes"), singular or plural, or a cook's synonym for it ("catsup" for
  "ketchup"), and names no form the ingredient doesn't (a powder for fresh chiles);
- **doubtful**: the record names the food but as another form, or names one of
  the ingredient's other words but not its head ("lamb leg" against ground lamb);
- **misfit**: the record never names the ingredient's food.

A head that only says which part or shape ("chops", "greens", "buns") gives way
to the word before it. Words in parentheses count as names ("Carambola,
(starfruit)") but never as forms ("may have been previously frozen"). Nothing is
guessed by similarity: "water" never meets "watermelon".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from app.catalog.names import normalize_name, plural_word, singulars

Verdict = Literal["fits", "doubtful", "misfit"]

# Forms that make a record a different thing to cook with. "Canned", "frozen",
# "dried" and "roasted" are left out: USDA uses them for the ordinary bought form
# of nuts, beans and fish. Data, not logic.
FORMS = frozenset(
    {
        "powder", "powdered", "ground", "sun-dried", "cooked", "fried", "boiled",
        "braised", "meatless", "imitation", "extender", "candied", "concentrate",
    }
)  # fmt: skip

# A form word the ingredient may give in other words.
_SAME_FORM = {
    "ground": frozenset({"minced"}),
    "sun-dried": frozenset({"dried", "dry"}),
    "powder": frozenset({"powdered"}),
    "powdered": frozenset({"powder"}),
}

# Foods bought as a form, so the record's form word is no surprise.
_FORM_IS_THE_FOOD = {
    "powder": frozenset({"gelatin", "cocoa", "custard"}),
    "concentrate": frozenset({"bottled"}),
}

# Words that say how a food was bought or made, never which food: they never
# stand in for a head the record doesn't name.
DESCRIPTORS = frozenset(
    {
        "roasted", "toasted", "dried", "fresh", "frozen", "canned", "bottled", "smoked",
        "whole", "sliced", "diced", "chopped", "raw", "organic", "store-bought",
        "homemade", "large", "small", "baby", "wild", "thick", "thin", "cut", "instant",
    }
)  # fmt: skip

_STOPWORDS = frozenset({"the", "on", "of", "and", "with", "in", "for", "a"})

# Head nouns that name a part, a cut or a shape rather than the food.
GENERIC_HEADS = frozenset(
    {
        "greens", "fruit", "quarters", "chops", "ribs", "steak", "steaks", "buns",
        "tenders", "rinds", "cob", "fillets", "fillet", "cutlets", "strips", "leaves",
        "sprigs", "florets", "wedges", "slices", "pieces", "bites", "meat",
    }
)  # fmt: skip

# A cook's word and the words a USDA record uses for the same food. Data, not
# logic: add to it freely, but never a word that names another food.
SYNONYMS: dict[str, frozenset[str]] = {
    "chile": frozenset({"chili", "pepper"}),
    "chilli": frozenset({"chili", "pepper"}),
    "chili": frozenset({"chile", "pepper"}),
    "poblano": frozenset({"pepper"}),
    "jalapeno": frozenset({"pepper"}),
    "serrano": frozenset({"pepper"}),
    "habanero": frozenset({"pepper"}),
    "garbanzo": frozenset({"chickpea"}),
    "chickpea": frozenset({"garbanzo"}),
    "scallion": frozenset({"onion"}),
    "cilantro": frozenset({"coriander"}),
    "rabe": frozenset({"raab"}),
    "bresaola": frozenset({"beef"}),
    "daikon": frozenset({"radish"}),
    "ghee": frozenset({"butter"}),
    "ketchup": frozenset({"catsup"}),
    "kielbasa": frozenset({"sausage"}),
    "kiwi": frozenset({"kiwifruit"}),
    "kombu": frozenset({"kelp", "seaweed"}),
    "nori": frozenset({"laver", "seaweed"}),
    "lychee": frozenset({"litchi"}),
    "harina": frozenset({"flour"}),
    "skyr": frozenset({"yogurt"}),
    "soppressata": frozenset({"salami"}),
    "sunchoke": frozenset({"jerusalem-artichoke", "artichoke"}),
    "mint": frozenset({"spearmint", "peppermint"}),
    "sprout": frozenset({"sprouted"}),
    "choy": frozenset({"choi"}),
    "peppercorn": frozenset({"pepper"}),
    "melon": frozenset({"gourd"}),
}

_WORD = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
_PARENS = re.compile(r"\([^)]*\)")


@dataclass(frozen=True)
class Fit:
    verdict: Verdict
    reason: str


def _forms_of(word: str) -> set[str]:
    out = {word, *singulars(word)}
    if (p := plural_word(word)) is not None:
        out.add(p)
    return out


def _words(text: str) -> list[str]:
    return _WORD.findall(normalize_name(text.replace(",", " ")))


def _readings(words: list[str], *, synonyms: bool) -> set[str]:
    """Every singular and plural reading of some words, their parts and pairs."""
    out: set[str] = set()
    joined = [a + b for a, b in pairwise(words)]
    for w in [*words, *joined]:
        for f in _forms_of(w):
            out.add(f)
            if synonyms:
                out |= SYNONYMS.get(f, frozenset())
        if "-" in w:
            for part in w.split("-"):
                out |= _forms_of(part)
    return out


def _content_words(name: str) -> list[str]:
    """The ingredient's words in eating order: a "beef, ground" name turned round."""
    first, comma, rest = name.partition(",")
    words = [*_words(rest), *_words(first)] if comma else _words(name)
    return [w for w in words if w not in _STOPWORDS]


def fits(ingredient_name: str, description: str) -> Fit:
    """Whether the record described as ``description`` is the ingredient's food."""
    words = _content_words(ingredient_name)
    if not words:
        return Fit("misfit", "the ingredient has no name to compare")
    record = _readings(_words(description), synonyms=False)
    named_last = words[-1]
    head = named_last
    i = len(words) - 1
    while head in GENERIC_HEADS and i > 0:
        i -= 1
        head = words[i]
    record_forms = _words(_PARENS.sub(" ", description))
    name_readings = _readings(words, synonyms=True)

    def named(word: str) -> bool:
        return bool(_readings([word], synonyms=True) & record)

    # A two-word food the record writes as one ("short ribs", "shortribs"), or a
    # part the record names itself ("flat iron steak", "...top blade steak").
    pairs = {a + b for a, b in pairwise(words)}
    head_named = named(head) or bool(pairs & record)
    if not head_named and named_last in GENERIC_HEADS and named(named_last):
        head_named = True
    if not head_named:
        others = [w for w in words if w != head and w not in FORMS | DESCRIPTORS and named(w)]
        if not others:
            return Fit("misfit", f"the USDA record never names {head!r}")
        return Fit("doubtful", f"the USDA record names {others[0]!r} but not {head!r}")
    for word in record_forms:
        if word not in FORMS or word in name_readings:
            continue
        if _SAME_FORM.get(word, frozenset()) & name_readings:
            continue
        if _FORM_IS_THE_FOOD.get(word, frozenset()) & name_readings:
            continue
        return Fit("doubtful", f"the USDA record is {word} and the ingredient isn't")
    return Fit("fits", "the USDA record names the ingredient's food")
