"""Tidy product names and brands as they are saved (2P). Pure: no database, no I/O.

Names arrive from store pages, receipts and typing. A page's name with a trademark
sign after the brand should be stored without it, so a person can type the name and
a search or a duplicate check finds it with confidence:

- trademark signs (U+2122, U+00AE, U+00A9, U+2120) are removed;
- curly quotes and apostrophes become straight ones, and a double prime (U+2033)
  becomes an inch mark ("), which is kept;
- en and em dashes and the minus sign become a hyphen;
- non-breaking and other unusual spaces become a plain space, and invisible
  characters (zero-width spaces, byte-order marks) are removed;
- runs of spaces collapse to one, and the ends are trimmed. A space left in front of
  a comma, colon or semicolon by a removed sign is dropped too.

Nothing else changes: letters, case, digits and other punctuation are kept.
"""

from __future__ import annotations

import re

_MARKS = [0x2122, 0x00AE, 0x00A9, 0x2120]
_INVISIBLE = [0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF]
_SINGLE_QUOTES = [0x2018, 0x2019, 0x201A, 0x201B, 0x2032, 0x02BC]
_DOUBLE_QUOTES = [0x201C, 0x201D, 0x201E, 0x201F, 0x2033]
_DASHES = [0x2010, 0x2011, 0x2012, 0x2013, 0x2014, 0x2015, 0x2212]
_SPACES_ODD = [0x00A0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000]
_WHITESPACE = [ord(c) for c in "\t\n\r\v\f"]

_TABLE: dict[int, str | None] = {
    **dict.fromkeys(_MARKS + _INVISIBLE),
    **dict.fromkeys(_SINGLE_QUOTES, "'"),
    **dict.fromkeys(_DOUBLE_QUOTES, '"'),
    **dict.fromkeys(_DASHES, "-"),
    **dict.fromkeys(_SPACES_ODD + _WHITESPACE, " "),
}
_SPACES = re.compile(r" {2,}")
_BEFORE_PUNCT = re.compile(r" +([,;:])")


def tidy(text: str | None) -> str | None:
    """The text with signs removed and typography made plain; None stays None."""
    if text is None:
        return None
    out = text.translate(_TABLE)
    out = _SPACES.sub(" ", out)
    return _BEFORE_PUNCT.sub(r"\1", out).strip()


# --- short names (#347) ----------------------------------------------------------
#
# A product's name is short: no brand in front, no pack size, sentence case. The
# brand and the pack have fields of their own, so a retailer's title ("HARBOR LIGHT
# Thick Cut Bacon, 12 oz") repeats both; its short name is "Thick cut bacon".

# A number with a unit, or a count of things: "12 oz", "16 fl. oz.", "6 cans",
# "12 pk", "1/2 gal", "2.25 lb", "48 OZ (3 LBS)".
_QTY = (
    r"\d+(?:[./]\d+)?\s{0,2}(?:-\s?)?"
    r"(?:fl\.?\s{0,2}oz\.?|oz\.?|lbs?\.?|pounds?|ounces?|kg|g|grams?|mg|ml|l|liters?|litres?"
    r"|gal(?:lons?)?|qt|quarts?|pt|pints?|ct|count|pk|pack|packs|cans?|bottles?|bags?"
    r"|boxes|box|jars?|pieces|pcs|each|ea|rolls|links|slices|sheets|eggs|bars|cups|pods)\b"
)
# Words that only say how the pack is sold: "Bag", "Clamshell", "Each", "Can".
# "Bunch" stays in a name: no unit says it, so the name is where it is kept.
_CONTAINERS = (
    r"(?:bag|bottle|box|can|carton|clamshell|container|each|jar|pack|package|pouch|tub|tin|"
    r"case|loaf|bundle|tray)"
)
_PACK_PHRASE = re.compile(
    rf"(?:net\s+wt\.?\s*)?{_QTY}(?:\s*(?:[x×/]|per)\s*{_QTY})*(?:\s*\({_QTY}[^)]*\))?"
    rf"(?:\s+{_CONTAINERS}\b)?\.?",
    re.IGNORECASE,
)
# A trailing piece of the title that is only pack: ", 12 oz", " - 5 Oz", " (255g)",
# " 12oz Bag", ", Each", ", 6 cans / 12 fl oz".
_TRAILING_PACK = re.compile(
    rf"(?:\s*[,\-–:|/]\s*|\s+|\s*\()(?:{_PACK_PHRASE.pattern}|{_CONTAINERS})\)?\s*$",
    re.IGNORECASE,
)
# Retail banners in capitals ending in "!": "BIG DEAL!", "NEW!".
_BANNER = re.compile(r"\s*\b[A-Z]{2,}(?:\s+[A-Z]{2,})*!")
_UNIT_WORDS = frozenset(
    {"oz", "lb", "lbs", "g", "kg", "mg", "ml", "l", "fl", "ct", "pk", "gal", "qt", "pt"}
)
# Words kept in capitals when a name is put in sentence case.
ACRONYMS = frozenset(
    {"IPA", "BBQ", "USDA", "XL", "XXL", "NY", "USA", "UK", "PB&J", "BLT", "GF", "TV", "DIY"}
)


def _brand_lead(words: list[str], keys: set[str]) -> int:
    """How many leading words are a known brand: never all of them."""
    from app.catalog.brand_names import name_key

    for n in range(len(words) - 1, 0, -1):
        if name_key(" ".join(words[:n])) in keys:
            return n
    return 0


def _sentence_case(text: str, keep: set[str]) -> str:
    """Sentence case for a name in capitals or Title Case; others are left alone."""
    words = text.split(" ")
    # Units and sizes say nothing about the name's case: "Snap Peas, 32 oz" is Title Case.
    lettered = [
        w
        for w in words
        if any(c.isalpha() for c in w)
        and not any(c.isdigit() for c in w)
        and w.strip(",.()").lower() not in _UNIT_WORDS
    ]
    if not lettered:
        return text
    upper = sum(1 for w in lettered if w.isupper() and len(w) > 1)
    titled = sum(1 for w in lettered if w[:1].isupper())
    shouting = upper / len(lettered) >= 0.6
    title_case = len(lettered) >= 2 and titled == len(lettered)
    if not (shouting or title_case):
        return text
    out = []
    for i, word in enumerate(words):
        bare = word.strip(",.()!:;")
        if bare.upper() in ACRONYMS:
            out.append(word.upper())
            continue
        if bare.lower() in keep:
            # A proper noun or brand word: "DIJON" becomes "Dijon", "McKenzie" stays.
            out.append(word.capitalize() if word.isupper() else word)
            continue
        if any(c.isdigit() for c in bare) or bare.lower() in _UNIT_WORDS:
            out.append(word if not word.isupper() else word.lower())
            continue
        inner = word.replace("-", " ").split()
        if not shouting and any(c.isupper() for part in inner for c in part[1:]):  # McCormick
            out.append(word)
            continue
        lowered = word.lower()
        out.append(lowered[:1].upper() + lowered[1:] if i == 0 else lowered)
    return " ".join(out)


def short_name(
    title: str,
    brand: str | None = None,
    *,
    has_pack: bool = True,
    brand_keys: frozenset[str] | set[str] = frozenset(),
    proper_nouns: frozenset[str] | set[str] = frozenset(),
) -> str:
    """The short name for a product titled ``title`` (#347). Never empty, idempotent.

    ``brand`` and ``brand_keys`` (other spellings of brands, as ``brand_names.name_key``
    keys) are dropped from the front, and a trailing ", Brand" too. Pack text is
    dropped from the end only when the product's pack is known (``has_pack``), so
    nothing is lost. ``proper_nouns`` (lowercase) keep their capitals.
    """
    from app.catalog.brand_names import NEVER_BRANDS, is_brand_key, name_key

    original = tidy(title) or ""
    text = _BANNER.sub("", original).strip()
    # Another product's brand that is also a word for the food ("Stone", "Organic")
    # stays in this name; the product's own brand goes unless it only describes it.
    keys = {k for k in brand_keys if is_brand_key(k)}
    if brand and (key := name_key(brand)) and key not in NEVER_BRANDS:
        keys.add(key)
        # A brand's own first words lead many titles: "Sierra Nevada" for "Sierra
        # Nevada Brewing Co.". Two words at least, so a one-word lead stays a word.
        brand_words = key.split()
        keys.update(" ".join(brand_words[:n]) for n in range(2, len(brand_words)))
    for _ in range(4):
        before = text
        if has_pack:
            text = _TRAILING_PACK.sub("", text).strip(" ,-–:|/")
        words = text.split()
        if keys and (lead := _brand_lead(words, keys)):
            text = " ".join(words[lead:]).lstrip(" ,-–:|/")
        tail = re.split(r"\s*(?:,|\s-\s|\bby\b)\s*", text)
        if keys and len(tail) > 1 and name_key(tail[-1]) in keys:
            text = text[: text.rfind(tail[-1])].rstrip(" ,-–:|/").removesuffix(" by").strip()
        if text == before:
            break
    keep = {w.lower() for w in proper_nouns} | {w for k in keys for w in k.split()}
    text = _sentence_case(text, keep)
    return text or original or title
