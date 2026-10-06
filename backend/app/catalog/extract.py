"""Generic extraction from a captured vendor page (04, 2M). Pure: no I/O.

The ladder, each rung a source the merge ranks (app.catalog.proposals):

```
structured data (JSON-LD, raw text)  ─▶ page_data   name, brand, GTIN, SKU, price, size, images
meta tags (Open Graph, product:*)    ─▶ page_meta   title, brand, price, item number, image
the address                          ─▶ address     a title from the slug, an item number
```

Everything a page sends is untrusted (non-negotiable 7). Structured data is
parsed from its raw text with ``parse_float=Decimal``, so a price such as 0.10
stays exactly 0.10 from the page to the observation (non-negotiable 1); a block
that is not JSON is skipped, never evaluated.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from app.catalog.identifiers import AmbiguousCode, InvalidGtin, classify_barcode
from app.catalog.proposals import Candidate
from app.units.parse import UnitParseFailure, parse_unit

MAX_IMAGES = 12
# Bounded on every side so a long run of digits cannot make it backtrack: a
# quantity starts where no digit precedes it, has at most six digits before the
# point and four after, and sits at most three spaces from its unit.
_PACK = re.compile(
    r"(?<![\d.])(\d{1,6}(?:\.\d{1,4})?)\s{0,3}(fl\.?\s{0,2}oz|kg|mg|g|ml|l|lbs?|oz|ct|count)\b",
    re.IGNORECASE,
)
# A multipack, "6 x 330 ml": at most three digits of pieces, then a size (bounded as above).
_MULTIPACK = re.compile(
    r"(?<![\d.])(\d{1,3})\s{0,2}[x\u00d7]\s{0,2}(\d{1,6}(?:\.\d{1,4})?)\s{0,3}"
    r"(fl\.?\s{0,2}oz|kg|mg|g|ml|l|lbs?|oz)\b",
    re.IGNORECASE,
)
# Pieces beside a size: "5 ct", "12 count", "6 pk", "6-pack", "8 pieces", "10 pcs".
_PIECES = re.compile(
    r"(?<![\d.])(\d{1,5})\s{0,2}-?\s{0,2}(ct|count|pk|pack|pieces|pcs)\b", re.IGNORECASE
)
# Pack sizes are read from titles and short texts; never scan more than this.
PACK_TEXT_LIMIT = 2000
_METRIC = {"g", "kg", "mg", "ml", "l"}
_SKU_PARAMS = ("sku", "item", "itemid", "item_id", "productid", "product_id", "pid")
_DIGITS = re.compile(r"(?<![0-9])([0-9]{4,14})(?![0-9])")
# Words a store's routes and placeholder titles are made of ("product-details", "Item").
_ROUTE_WORDS = frozenset(
    {"product", "products", "prod", "detail", "details", "item", "items", "view"}
    | {"p", "dp", "ip", "pd", "shop", "buy", "page"}
)


def _generic(text: str) -> bool:
    """True when the text is only route words: a page's kind, not its product's name."""
    words = re.findall(r"[a-z]+", text.lower())
    return all(w in _ROUTE_WORDS for w in words)


@dataclass
class PageEvidence:
    candidates: list[Candidate] = field(default_factory=list)
    images: list[str] = field(default_factory=list)


def pack_from_text(text: str | None) -> dict[str, str] | None:
    """A pack size in text such as "15.5 oz/439 g", metric preferred; None when there is none."""
    return size_from_text(text)[0]


def size_from_text(text: str | None) -> tuple[dict[str, str] | None, dict[str, str] | None]:
    """A pack size and the pieces it holds, from text such as "6 x 330 ml" (1980 ml, 6
    pieces) or "14 oz, 4 ct" (14 oz, 4 pieces). Metric is preferred for the size. A count
    alone is the pack itself ("12 ct" is 12 each), and no pieces."""
    if not text:
        return None, None
    text = text[:PACK_TEXT_LIMIT]
    if multi := _MULTIPACK.search(text):
        count, qty, unit_text = multi.groups()
        unit = parse_unit(unit_text)
        if not isinstance(unit, UnitParseFailure) and int(count) > 0:
            total = Decimal(qty) * int(count)
            return {"qty": format(total, "f"), "unit": unit}, {"count": str(int(count))}
    sizes, counts = [], []
    for qty, unit_text in _PACK.findall(text):
        word = unit_text.lower()
        unit = parse_unit("each" if word in ("ct", "count") else unit_text)
        if isinstance(unit, UnitParseFailure):
            continue
        (counts if unit == "each" else sizes).append(
            {"qty": format(Decimal(qty), "f"), "unit": unit}
        )
    metric = [p for p in sizes if p["unit"] in _METRIC]
    size = (metric or sizes or [None])[0]
    if size is None:
        return (counts or [None])[0], None
    pieces = next((int(n) for n, _ in _PIECES.findall(text) if int(n) > 0), None)
    return size, ({"count": str(pieces)} if pieces else None)


def _size_candidates(text: str | None, source: str) -> list[Candidate]:
    pack, pieces = size_from_text(text)
    out = [Candidate("pack", pack, source)] if pack else []
    if pack and pieces:
        out.append(Candidate("pieces", pieces, source))
    return out


_DOLLARS = re.compile(r"^\$?\s{0,2}(\d{1,3}(?:,\d{3}){0,3}|\d{1,9})(\.\d{1,4})?$")


def _money(value: Any) -> str | None:
    """An amount from structured data or a tag: 3.49, "3.49", or "$1,299.00" (a store's
    microdata writes the sign). Nothing else is guessed at."""
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if found := _DOLLARS.match(text):
        text = found.group(1).replace(",", "") + (found.group(2) or "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    return format(amount, "f") if amount.is_finite() and amount >= 0 else None


def _gtin(value: Any) -> str | None:
    if value is None:
        return None
    try:
        scheme, code = classify_barcode(str(value).strip())
    except (InvalidGtin, AmbiguousCode, ValueError):
        return None
    return code if scheme == "gtin" else None


def _text(value: Any) -> str | None:
    if isinstance(value, Mapping):
        value = value.get("name")
    if isinstance(value, list):
        value = value[0] if value else None
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text[:200] or None


def _types(node: Mapping[str, Any]) -> set[str]:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return {str(k).rsplit("/", 1)[-1] for k in kinds if k}


def _nodes(data: Any) -> Iterator[Mapping[str, Any]]:
    if isinstance(data, list):
        for item in data:
            yield from _nodes(item)
    elif isinstance(data, Mapping):
        yield data
        if "@graph" in data:
            yield from _nodes(data["@graph"])


def _images(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, Mapping):
        url = value.get("url") or value.get("contentUrl")
        return [url] if isinstance(url, str) else []
    if isinstance(value, list):
        return [u for item in value for u in _images(item)]
    return []


# UN/CEFACT Rec 20 codes schema.org uses for a unit price's reference quantity.
_UNIT_CODES = {
    "LBR": "lb",
    "KGM": "kg",
    "GRM": "g",
    "ONZ": "oz",
    "LTR": "l",
    "MLT": "ml",
    "OZA": "fl_oz",
    "H87": "each",
    "EA": "each",
    "C62": "each",
}
_UNKNOWN = object()


def _unit_price(spec: Any) -> dict[str, str] | object | None:
    """A UnitPriceSpecification as {amount, qty, unit}; _UNKNOWN when its unit is not
    one this deployment knows; None when it is not a unit price."""
    if not isinstance(spec, Mapping) or "UnitPriceSpecification" not in _types(spec):
        return None
    ref = spec.get("referenceQuantity")
    amount = _money(spec.get("price"))
    if not isinstance(ref, Mapping) or amount is None:
        return None
    code = str(ref.get("unitCode") or "").strip().upper()
    unit = _UNIT_CODES.get(code)
    if unit is None and isinstance(ref.get("unitText"), str):
        parsed = parse_unit(ref["unitText"])
        unit = None if isinstance(parsed, UnitParseFailure) else parsed
    qty = _money(ref.get("value", 1))
    if unit is None or qty is None or Decimal(qty) <= 0:
        return _UNKNOWN
    return {"amount": amount, "qty": qty, "unit": unit}


def _offer(offers: Any) -> str | dict[str, str] | None:
    """The first offer's price: a bare amount (for 1 each), or {amount, qty, unit} when
    the offer's own price is a unit price ("0.69 per pound"). A unit price that only
    sits beside a different offer price is a comparison figure and is not taken; a
    unit price in a unit this deployment does not know gives no price at all."""
    for offer in offers if isinstance(offers, list) else [offers]:
        if not isinstance(offer, Mapping):
            continue
        price = _money(offer.get("price") or offer.get("lowPrice"))
        specs = offer.get("priceSpecification")
        specs = specs if isinstance(specs, list) else [specs]
        for spec in specs:
            unit_price = _unit_price(spec)
            if unit_price is None:
                continue
            own = price is None or Decimal(_money(spec.get("price")) or "-1") == Decimal(price)
            if own:  # the offer's price is this unit price
                return None if unit_price is _UNKNOWN else unit_price  # type: ignore[return-value]
        if price is None and isinstance(offer.get("priceSpecification"), Mapping):
            price = _money(offer["priceSpecification"].get("price"))
        if price is not None:
            return price
    return None


def _size(node: Mapping[str, Any], name: str | None) -> list[Candidate]:
    for key in ("size", "weight", "netContent"):
        value = node.get(key)
        if isinstance(value, Mapping):
            qty, unit = value.get("value"), value.get("unitText") or value.get("unitCode")
            if (
                qty is not None
                and unit
                and (found := _size_candidates(f"{qty} {unit}", "page_data"))
            ):
                return found
        elif isinstance(value, str) and (found := _size_candidates(value, "page_data")):
            return found
    return _size_candidates(name, "page_data")


def from_structured_data(blocks: list[str]) -> PageEvidence:
    out = PageEvidence()
    for raw in blocks:
        try:
            data = json.loads(raw, parse_float=Decimal)
        except (ValueError, RecursionError):
            continue
        for node in _nodes(data):
            if "Product" not in _types(node) and "ProductGroup" not in _types(node):
                continue
            name = _text(node.get("name"))
            add = out.candidates.append
            if name:
                add(Candidate("title", name, "page_data"))
            if brand := _text(node.get("brand")):
                add(Candidate("brand", brand, "page_data"))
            for key in ("gtin14", "gtin13", "gtin12", "gtin8", "gtin"):
                if code := _gtin(node.get(key)):
                    add(Candidate("gtin", code, "page_data"))
                    break
            for key in ("sku", "productID", "mpn"):
                if sku := _text(node.get(key)):
                    add(Candidate("item_number", sku, "page_data"))
                    break
            if (price := _offer(node.get("offers"))) is not None:
                add(Candidate("price", price, "page_data"))
            out.candidates.extend(_size(node, name))
            out.images.extend(_images(node.get("image")))
    return out


# Open Graph and product tags, then the page's microdata (sent as "itemprop:<name>").
_META_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("title", ("og:title", "twitter:title", "itemprop:name")),
    ("brand", ("product:brand", "og:brand", "itemprop:brand")),
    ("price", ("product:price:amount", "og:price:amount", "itemprop:price")),
    ("item_number", ("product:retailer_item_id", "product:sku", "itemprop:sku")),
)
_META_GTINS = (
    "itemprop:gtin14",
    "itemprop:gtin13",
    "itemprop:gtin12",
    "itemprop:gtin8",
    "itemprop:gtin",
)


def from_meta(meta: Mapping[str, str]) -> PageEvidence:
    out = PageEvidence()
    lowered = {k.strip().lower(): v for k, v in meta.items() if isinstance(v, str)}
    for field_name, keys in _META_FIELDS:
        for key in keys:
            value = lowered.get(key)
            if not value:
                continue
            value = _money(value) if field_name == "price" else _text(value)
            if field_name == "title" and value and _generic(value):
                continue
            if value:
                out.candidates.append(Candidate(field_name, value, "page_meta"))
                break
    for key in _META_GTINS:
        if code := _gtin(lowered.get(key)):
            out.candidates.append(Candidate("gtin", code, "page_meta"))
            break
    out.candidates.extend(_size_candidates(lowered.get("og:title"), "page_meta"))
    for key in ("og:image", "og:image:url", "twitter:image"):
        if lowered.get(key):
            out.images.append(lowered[key])
            break
    return out


def from_address(url: str) -> PageEvidence:
    """A title from the address's slug and an item number from its digits or query."""
    out = PageEvidence()
    parts = urlsplit(url)
    segments = [unquote(s) for s in parts.path.split("/") if s]
    query = {k.lower(): v for k, v in parse_qs(parts.query).items()}
    for key in _SKU_PARAMS:
        if query.get(key) and (value := query[key][0].strip()):
            out.candidates.append(Candidate("item_number", value[:64], "address"))
            break
    else:
        for segment in reversed(segments):
            if found := _DIGITS.search(segment):
                out.candidates.append(Candidate("item_number", found.group(1), "address"))
                break
    slug = next(
        (
            s
            for s in reversed(segments)
            if re.search(r"[A-Za-z]{3,}", s) and "-" in s and not _generic(s)
        ),
        None,
    )
    if slug:
        words = re.sub(r"\.[a-z]{2,5}$", "", slug)
        words = _DIGITS.sub(" ", words)
        title = " ".join(w for w in re.split(r"[-_+\s]+", words) if w).strip()
        if title:
            out.candidates.append(Candidate("title", title[:1].upper() + title[1:], "address"))
            out.candidates.extend(_size_candidates(title, "address"))
    return out


# A title's site-name segment: "Rolled Oats | Juniper Market", "Oats, 30 oz - Lantern".
_SEPARATORS = re.compile(r"\s{1,3}[|\-\u2013\u2014]\s{1,3}|\s{0,3}:\s{1,3}")


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def site_names(page_url: str, meta: Mapping[str, str], vendor_name: str | None) -> set[str]:
    """The names a store's pages call themselves: og:site_name, the vendor, the host label."""
    names = {meta.get("og:site_name") or "", vendor_name or ""}
    host = (urlsplit(page_url).hostname or "").removeprefix("www.")
    names.add(host.split(".")[0])
    return {_squash(n) for n in names if _squash(n)}


def clean_title(title: str, names: set[str]) -> str:
    """The title without a leading or trailing segment that is only the site's name."""
    seps = list(_SEPARATORS.finditer(title[:PACK_TEXT_LIMIT]))
    if not seps:
        return title
    last, first = seps[-1], seps[0]
    if _squash(title[last.end() :]) in names and title[: last.start()].strip():
        return title[: last.start()].strip()
    if _squash(title[: first.start()]) in names and title[first.end() :].strip():
        return title[first.end() :].strip()
    return title


def extract(
    page_url: str,
    *,
    meta: Mapping[str, str] | None = None,
    structured_data: list[str] | None = None,
    vendor_name: str | None = None,
) -> PageEvidence:
    """Every generic rung together; the merge decides which value each field keeps.

    Titles lose the store's own name ("| Juniper Market", "- lantern"), and a brand that is
    only the product's name again is dropped: neither is a fact about the product."""
    out = PageEvidence()
    for rung in (
        from_structured_data(structured_data or []),
        from_meta(meta or {}),
        from_address(page_url),
    ):
        out.candidates.extend(rung.candidates)
        out.images.extend(i for i in rung.images if i not in out.images)
    out.images = out.images[:MAX_IMAGES]
    names = site_names(page_url, meta or {}, vendor_name)
    titles = {_squash(str(c.value)) for c in out.candidates if c.field == "title"}
    cleaned = []
    for c in out.candidates:
        if c.field == "title" and isinstance(c.value, str):
            title = clean_title(c.value, names)
            cleaned.append(Candidate(c.field, title, c.source, c.confidence, c.via))
        elif not (c.field == "brand" and isinstance(c.value, str) and _squash(c.value) in titles):
            cleaned.append(c)
    out.candidates = cleaned
    return out
