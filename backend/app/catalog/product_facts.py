"""What a product's name says about its attributes (03, 1H amendment 2026-10-27). Pure.

An ingredient is a commodity; a product carries what tells its items apart, as PLU
codes, GS1 classification and USDA names do. This module reads those facts from a
product's name and brand, for a new product's attributes and for review. It never
decides alone: a person's own attributes always win.

``VARIETIES`` lists cultivars by their commodity, as stores and the produce trade name
them. A cultivar is a proper noun ("Fuji", "Yukon Gold"); a type that recipes call for
by name and don't swap freely (red onion, cherry tomato, sweet potato) is an ingredient
of its own and is not here.
"""

from __future__ import annotations

import re
from typing import Any

from app.catalog.attributes import COUNT_SIZE, SEAFOOD_SPECIES
from app.catalog.names import normalize_name, singulars

# Commodity (the ingredient's head noun, singular) -> its cultivars, as displayed.
VARIETIES: dict[str, tuple[str, ...]] = {
    "apple": (
        "Ambrosia", "Braeburn", "Cosmic Crisp", "Envy", "Fuji", "Gala", "Golden Delicious",
        "Granny Smith", "Honeycrisp", "Jazz", "Opal", "Pink Lady", "Red Delicious",
        "SugarBee", "SweeTango", "McIntosh", "Jonagold", "Rave", "Kanzi", "Pinata",
    ),
    "pear": ("Anjou", "Bartlett", "Bosc", "Comice", "Forelle", "Seckel", "Starkrimson"),
    "potato": ("Yukon Gold", "Kennebec", "Russet Burbank", "Adirondack Blue"),
    "avocado": ("Hass", "Reed", "Bacon", "Fuerte", "Lamb Hass"),
    "lemon": ("Meyer", "Eureka", "Lisbon"),
    "orange": ("Navel", "Cara Cara", "Valencia", "Heirloom Navel"),
    "mango": ("Ataulfo", "Honey", "Kent", "Keitt", "Tommy Atkins", "Haden", "Champagne"),
    "onion": ("Walla Walla", "Vidalia", "Maui", "Texas Sweet"),
    "grape": ("Autumncrisp", "Cotton Candy", "Thompson", "Moon Drops", "Sweet Globe"),
    "tomato": ("Roma", "Beefsteak", "Heirloom", "Campari", "San Marzano"),
    "peach": ("Elberta", "Saturn", "Donut"),
    "cherry": ("Bing", "Rainier", "Lapins"),
    "plum": ("Black Splendor", "Santa Rosa", "Dapple Dandy"),
    "pineapple": ("Honeyglow", "Pinkglow", "MD-2"),
    "kiwi": ("Hayward", "SunGold"),
    "banana": ("Cavendish", "Manzano"),
}  # fmt: skip

_MEAT_SPECIES_WORDS = {
    "beef": "beef", "steak": "beef", "ribeye": "beef", "sirloin": "beef", "brisket": "beef",
    "chuck": "beef", "tri tip": "beef", "filet mignon": "beef", "flap": "beef",
    "new york strip": "beef", "eye round": "beef", "eye of round": "beef",
    "pork": "pork", "bacon": "pork", "ham": "pork", "prosciutto": "pork",
    "pancetta": "pork", "chicken": "chicken", "turkey": "turkey", "lamb": "lamb",
    "goat": "goat", "duck": "duck",
}  # fmt: skip

_SEAFOOD_DETAILS: dict[str, tuple[str, ...]] = {
    "salmon": ("Atlantic", "sockeye", "coho", "king", "chinook", "keta", "pink"),
    "tuna": ("yellowfin", "ahi", "albacore", "bluefin", "skipjack", "bigeye"),
    "shrimp": ("white", "tiger", "pink", "brown", "rock", "spot"),
    "cod": ("Atlantic", "Pacific", "black"),
    "crab": ("Dungeness", "king", "snow", "blue"),
}
_SEAFOOD_PLURALS = {"anchovies": "anchovy", "sardines": "sardine", "scallops": "scallop"}

_LEAN = re.compile(r"\b(\d{2})\s*%?\s*/\s*(\d{1,2})\s*%?(?!\d)")
_LEAN_WORD = re.compile(r"\b(\d{2})\s*%\s*lean\b", re.IGNORECASE)
_COUNT = re.compile(rf"(?<![\w/]){COUNT_SIZE}(?![\w/])", re.IGNORECASE)


def _words(text: str) -> str:
    """Normalized words, with hyphens split: "bone-in" reads as "bone in"."""
    return " ".join(normalize_name(text).replace("-", " ").split())


def _text(name: str, brand: str | None = None) -> str:
    return f" {_words(f'{name} {brand or ""}')} "


def _has(text: str, phrase: str) -> bool:
    return f" {_words(phrase)} " in text


def commodity(ingredient_name: str) -> str | None:
    """The ``VARIETIES`` commodity an ingredient's name is, by its head noun."""
    words = _words(ingredient_name).split()
    if not words:
        return None
    head = words[-1]
    for form in (head, *singulars(head)):
        if form in VARIETIES:
            return form
    return None


def variety(name: str, ingredient_name: str) -> str | None:
    """The cultivar a product's name gives for its ingredient's commodity, longest first."""
    found = commodity(ingredient_name)
    if found is None:
        return None
    text = _text(name)
    for v in sorted(VARIETIES[found], key=len, reverse=True):
        if _has(text, v):
            return v
    return None


def variety_words(head: str, words: frozenset[str]) -> frozenset[str]:
    """The words of every cultivar of ``head`` that ``words`` hold whole, so a matcher
    can read "honeycrisp apple" as an apple."""
    found = commodity(head)
    if found is None:
        return frozenset()
    out: set[str] = set()
    for v in VARIETIES[found]:
        vw = set(_words(v).split())
        if vw <= words:
            out |= vw
    return frozenset(out)


def organic(name: str, brand: str | None = None) -> bool:
    return bool(re.search(r"\borganics?\b", f"{name} {brand or ''}", re.IGNORECASE))


def lean_pct(name: str) -> int | None:
    """80 for "80/20" or "80% lean"; a pair must add up to 100."""
    if m := _LEAN_WORD.search(name):
        return int(m.group(1))
    for m in _LEAN.finditer(name):
        lean, fat = int(m.group(1)), int(m.group(2))
        if lean + fat == 100 and 50 <= lean <= 99:
            return lean
    return None


# Each fact: (key, phrase -> value pairs, first phrase found wins). "Select" alone is a
# brand word too ("Tarn Select"), so only "USDA Select" grades.
_MEAT_WORDS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("bone", (("boneless", "boneless"), ("bone in", "bone_in"))),
    ("skin", (("skinless", "skinless"), ("skin on", "skin_on"))),
    ("form", (("sliced", "sliced"), ("shaved", "sliced"), ("cubed", "cubed"),
              ("diced", "cubed"), ("stew", "cubed"))),
    ("grade", (("prime", "prime"), ("choice", "choice"), ("usda select", "select"))),
)  # fmt: skip
_SEAFOOD_WORDS: tuple[tuple[str, tuple[tuple[str, str], ...]], ...] = (
    ("origin", (("wild", "wild"), ("farmed", "farmed"), ("farm raised", "farmed"))),
    ("form", (("canned", "canned"), ("in olive oil", "canned"), ("in oil", "canned"),
              ("smoked", "smoked"), ("fillet", "fillet"), ("fillets", "fillet"),
              ("loin", "loin"), ("loins", "loin"), ("steak", "steak"), ("steaks", "steak"),
              ("peeled", "peeled"),
              ("whole", "whole"), ("sashimi", "sliced"))),
)  # fmt: skip


def _first(text: str, phrases: dict[str, str] | tuple[tuple[str, str], ...]) -> str | None:
    """The value of the first phrase the text has; longest first when given a dict."""
    pairs = (
        sorted(phrases.items(), key=lambda i: -len(i[0])) if isinstance(phrases, dict) else phrases
    )
    return next((value for phrase, value in pairs if _has(text, phrase)), None)


def _words_facts(text: str, table: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]) -> dict:
    return {key: v for key, pairs in table if (v := _first(text, pairs)) is not None}


def meat(name: str, ingredient_name: str, brand: str | None = None) -> dict[str, Any]:
    """Meat attributes the words say, or {} when they don't name the species."""
    species_of = _text(f"{name} {ingredient_name}")
    species = _first(species_of, _MEAT_SPECIES_WORDS)
    if species is None:
        return {}
    out: dict[str, Any] = {"species": species, **_words_facts(_text(name), _MEAT_WORDS)}
    if _has(species_of, "ground"):
        out["form"] = "ground"
    if (lean := lean_pct(name)) is not None:
        out["lean_pct"] = lean
    if organic(name, brand):
        out["organic"] = True
    return out


def seafood(name: str, ingredient_name: str, brand: str | None = None) -> dict[str, Any]:
    """Seafood attributes the words say, or {} when they don't name the species."""
    text = _text(name)
    names = {s: s for s in SEAFOOD_SPECIES if s != "other"} | _SEAFOOD_PLURALS
    species = _first(_text(f"{name} {ingredient_name}"), names)
    if species is None:
        return {}
    out: dict[str, Any] = {"species": species, **_words_facts(text, _SEAFOOD_WORDS)}
    details = {d: d for d in _SEAFOOD_DETAILS.get(species, ())}
    if (detail := _first(text, tuple(details.items()))) is not None:
        out["species_detail"] = detail
    if species in ("shrimp", "scallop") and (m := _COUNT.search(name)):
        out["count_size"] = m.group(0).upper()
    if organic(name, brand):
        out["organic"] = True
    return out


def facts_for(
    category_key: str | None, name: str, ingredient_name: str, brand: str | None = None
) -> dict[str, Any]:
    """The attributes a new product's words give it in its ingredient's category."""
    if category_key == "meat":
        return meat(name, ingredient_name, brand)
    if category_key == "seafood":
        return seafood(name, ingredient_name, brand)
    out: dict[str, Any] = {}
    if organic(name, brand):
        out["organic"] = True
    if category_key == "produce" and (v := variety(name, ingredient_name)):
        out["variety"] = v
    return out
