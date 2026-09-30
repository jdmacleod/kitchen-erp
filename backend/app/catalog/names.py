"""Ingredient name keys and generated spellings (1G). Pure: no I/O.

``normalize_name`` makes the key that ``ingredient_alias.name_norm`` stores and
that search compares, so every spelling of an ingredient meets on one string.
It is versioned: an edit that changes its output for any input must bump
``NAMES_VERSION``, because stored keys were made by the old version.

The rules (03, 1G):
- Unicode NFKC, then case-folded.
- Accents are stripped in the key only; names keep them for display.
- Quantity fragments ("28 oz", "500g", "1 1/2 cups") are removed. A number
  alone is kept, because "00 flour" and "7 grain" name what you buy.
- Punctuation is removed, except hyphens inside a word.
- No stemming. Plurals are separate spellings made by ``plural``, so "grass"
  can never become "gras".

``plural`` gives the plural of a name's last word, or ``None`` when the name
should not get one: mass nouns, and words already ending in a single "s".
"""

from __future__ import annotations

import re
import unicodedata

NAMES_VERSION = "1"

# Standard-list keys: lowercase ASCII words joined by single hyphens. Generated
# slugs start with "local." and so can never be one (03, 1G; O6).
STANDARD_KEY_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
GENERATED_SLUG_PREFIX = "local."

_MAX_PASSES = 8
_UNITS = frozenset(
    {
        "g", "gr", "gram", "grams", "kg", "kgs", "kilo", "kilos", "mg",
        "oz", "ounce", "ounces", "floz", "lb", "lbs", "pound", "pounds",
        "ml", "l", "cl", "dl", "liter", "liters", "litre", "litres",
        "cup", "cups", "tbsp", "tbs", "tablespoon", "tablespoons",
        "tsp", "teaspoon", "teaspoons", "pt", "pint", "pints", "qt", "quart", "quarts",
        "gal", "gallon", "gallons", "ct", "count", "pk", "pack", "packs",
        "ea", "each", "doz", "dozen",
    }
)  # fmt: skip
_NUMBER = re.compile(r"\d+(?:[.,/]\d+)?(?:-\d+(?:[.,/]\d+)?)?")
_NUMBER_WITH_UNIT = re.compile(r"(\d+(?:[.,/]\d+)?(?:-\d+(?:[.,/]\d+)?)?)([a-z]+)")
_APOSTROPHES = str.maketrans("", "", "'’ʼ")


def _strip_accents(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    kept = "".join(c for c in decomposed if not unicodedata.combining(c))
    return unicodedata.normalize("NFKC", kept)


def _keep_char(c: str) -> str:
    if c in "-./" or c.isspace():
        return c
    return c if unicodedata.category(c)[0] in "LN" else " "


def _is_quantity(token: str) -> bool:
    """A number with a unit glued on: "500g", "1.5kg", "28oz"."""
    m = _NUMBER_WITH_UNIT.fullmatch(token)
    return m is not None and m.group(2) in _UNITS


def _drop_quantities(tokens: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if _is_quantity(token):
            i += 1
            continue
        if _NUMBER.fullmatch(token):
            # A number is a quantity only when a unit follows it: "28 oz", "1 1/2 cups",
            # "12 fl oz". A lone number stays ("00 flour").
            j = i + 1
            while j < len(tokens) and _NUMBER.fullmatch(tokens[j]):
                j += 1
            fluid = tokens[j : j + 2]
            if len(fluid) == 2 and fluid[0] == "fl" and fluid[1] in ("oz", "ounce", "ounces"):
                i = j + 2
                continue
            if j < len(tokens) and tokens[j] in _UNITS:
                i = j + 1
                continue
        out.append(token)
        i += 1
    return out


def _clean_token(token: str) -> list[str]:
    """Split on dots and slashes, then keep only hyphens inside a word."""
    words = []
    for part in re.split(r"[./]+", token):
        word = re.sub(r"-{2,}", "-", part).strip("-")
        if word:
            words.append(word)
    return words


def _one_pass(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _strip_accents(text).casefold()
    text = text.translate(_APOSTROPHES).replace("⁄", "/")
    text = "".join(_keep_char(c) for c in text)
    tokens = _drop_quantities(text.split())
    words = [w for token in tokens for w in _clean_token(token)]
    return " ".join(words)


def normalize_name(text: str) -> str:
    """The comparison key for an ingredient name or spelling. Idempotent."""
    current = text
    for _ in range(_MAX_PASSES):
        following = _one_pass(current)
        if following == current:
            return following
        current = following
    return current


# Mass nouns and names cooks never pluralize. Data, not logic: add to it freely.
UNCOUNTABLE = frozenset(
    {
        "asparagus", "bacon", "basil", "beef", "beer", "bread", "broccoli", "broth",
        "butter", "buttermilk", "celery", "cheese", "chocolate", "cilantro", "cinnamon",
        "cocoa", "coffee", "corn", "cornmeal", "cornstarch", "couscous", "cream", "cumin",
        "dill", "flour", "garlic", "gelatin", "ghee", "ginger", "ham", "honey", "juice",
        "kale", "ketchup", "lamb", "lard", "lettuce", "mayonnaise", "meat", "milk",
        "mint", "miso", "molasses", "mustard", "nutmeg", "oil", "oregano", "paprika",
        "parsley", "pasta", "pork", "powder", "quinoa", "rice", "rosemary", "rum",
        "sage", "salmon", "salt", "sauce", "shortening", "soda", "spaghetti", "spinach",
        "stock", "sugar", "syrup", "tahini", "tea", "thyme", "tofu", "tuna", "turmeric",
        "vanilla", "vinegar", "vodka", "water", "whiskey", "wine", "yeast", "yogurt",
        "zest",
    }
)  # fmt: skip
IRREGULAR = {
    "tomato": "tomatoes",
    "potato": "potatoes",
    "mango": "mangoes",
    "leaf": "leaves",
    "loaf": "loaves",
    "half": "halves",
    "knife": "knives",
}
_VOWELS = frozenset("aeiou")


def plural_word(word: str) -> str | None:
    """The plural of one lowercase word, or ``None`` when it should not have one."""
    if not word.isalpha() or word in UNCOUNTABLE:
        return None
    if word in IRREGULAR:
        return IRREGULAR[word]
    if word.endswith("s") and not word.endswith("ss"):
        return None  # already plural ("almonds"), or a mass noun ("hummus")
    if word.endswith(("ss", "x", "z", "ch", "sh")):
        return word + "es"
    if len(word) > 1 and word.endswith("y") and word[-2] not in _VOWELS:
        return word[:-1] + "ies"
    return word + "s"


def plural(name: str) -> str | None:
    """The name with its last word pluralized, or ``None``.

    "cherry tomato" → "cherry tomatoes", "peach" → "peaches", "berry" →
    "berries", "rice" → None, "green beans" → None.
    """
    head, _, last = name.strip().rpartition(" ")
    if not last or not last.isascii():
        return None
    word = plural_word(last.lower())
    if word is None:
        return None
    if last.isupper() and len(last) > 1:
        word = word.upper()
    elif last[0].isupper():
        word = word[0].upper() + word[1:]
    return f"{head} {word}" if head else word
