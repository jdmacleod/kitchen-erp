"""The model's reading of a clipped page's text (2M), kept to the page's own product.

A product page also shows other products: "you may also like", "frequently bought
together". Read over the whole page, the model can describe one of those instead
(issue 281). So when the page's own data (structured data, meta or an adapter)
already names the product, that name goes to the model as the first line of the
data, and the task asks only about that product. A reading whose name shares no
word with the page's name is about another product, and is dropped whole: its
brand and pack would be that product's too.

The page's name is untrusted text like the rest of the page: it travels inside the
delimited data block, never in the task, and the reading still counts only if it
validates as :class:`ProductReading` (non-negotiable 7).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from app.catalog import proposals as merging
from app.ingest import llm
from app.services.identify import PRODUCT_SYSTEM_PROMPT, ProductReading, reading_candidates

# Sources that come from the page itself, or from the person, and so name its product.
NAMING_SOURCES = frozenset({"person", "adapter", "page_data", "page_meta"})
TITLE_PREFIX = "Product this page is about: "
MAX_TITLE_CHARS = 200

PAGE_TASK = (
    "The text is from a store's web page for one grocery product. Its first line, "
    f'"{TITLE_PREFIX}…", names that product as the page\'s own data gives it. '
    "Describe only that product: its name, brand, the net quantity of its pack as a "
    "number and a unit (g, kg, ml, l, oz, lb, fl oz or each), a short category such "
    "as 'canned beans' or 'flour', and its ingredients list if one is shown. The page "
    "also shows other products, for example under 'you may also like', 'frequently "
    "bought together', 'customers also viewed' or 'related items'; never take a name, "
    "brand or pack from them. Use null for anything the page does not say about that "
    "product. Give your confidence from 0 to 1 that the name and brand are right."
)
PLAIN_TASK = (
    "The text is from a store's web page for one grocery product. Say what that "
    "product is: its name, brand, the net quantity of its pack as a number and a unit "
    "(g, kg, ml, l, oz, lb, fl oz or each), a short category such as 'canned beans' "
    "or 'flour', and its ingredients list if one is shown. Other products the page "
    "shows, for example under 'you may also like' or 'related items', are not it. Use "
    "null for anything you cannot find. Give your confidence from 0 to 1 that the name "
    "and brand are right."
)

# Words that say nothing about which product a name is.
_FILLER = frozenset({"and", "the", "with", "for", "of", "fresh", "organic", "pack", "count"})


def page_title(candidates: Iterable[merging.Candidate]) -> str | None:
    """The product's name as the page (or the person) gives it, or None."""
    fields = merging.merge(candidates)
    state = fields.get("title")
    if not state or state["source"] not in NAMING_SOURCES:
        return None
    text = " ".join(str(state["value"]).split())[:MAX_TITLE_CHARS]
    return text or None


def _words(text: str) -> set[str]:
    words = re.findall(r"[^\W_]+", text.casefold())
    return {w for w in words if len(w) > 2 and not w.isdigit() and w not in _FILLER}


def same_product(reading_title: str, title: str) -> bool:
    """Whether the model's name could be the page's product: they share a word."""
    return bool(_words(reading_title) & _words(title))


def page_text(text: str, title: str | None) -> str:
    """The data the model reads: the page's name first, when known, then its text."""
    return f"{TITLE_PREFIX}{title}\n\n{text}" if title else text


async def read_page(text: str, title: str | None) -> list[merging.Candidate]:
    """The model's reading of the page as capped ``model`` candidates, about ``title``.

    Empty when the model named a product that shares no word with ``title``.
    """
    task = PAGE_TASK if title else PLAIN_TASK
    reading, _ = await llm.LlmClient().extract(
        ProductReading, task, page_text(text, title), system=PRODUCT_SYSTEM_PROMPT
    )
    named = (reading.name or "").strip()
    if title and named and not same_product(named, title):
        return []
    return reading_candidates(reading, photo=False)
