"""A vendor page's canonical address (03, 1H). Pure; no I/O.

A listing is keyed by vendor and canonical address: the page's canonical link
when it has one, without query string or fragment, and without a store-scope
path segment, which is kept apart as ``store_ref``.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from urllib.parse import urlsplit, urlunsplit

# Storefront paths that scope a page to one store, as "<kind>/<n>". Retailer
# plugins (04, 2M) may pass their own patterns.
DEFAULT_SCOPE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^/(?:pickup|delivery|shop|instore)/(store/\d{1,10})(?=/)"),
)


def canonical_url(
    page_url: str,
    canonical: str | None = None,
    scope_patterns: Sequence[re.Pattern[str]] = DEFAULT_SCOPE_PATTERNS,
) -> tuple[str, str | None]:
    """The address a listing is keyed by, and the store scope stripped from it.

    A page's canonical link is used only when it plausibly names the same product:
    the same host, and a word or number from the page's own last path segment. A
    store whose canonical link is its search page would otherwise give every
    product one listing."""
    if canonical and not _same_product(page_url, canonical):
        canonical = None
    parts = urlsplit((canonical or page_url).strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("a listing address must be an http or https URL")
    path, store_ref = _strip_scope(parts.path or "/", scope_patterns)
    if store_ref is None and canonical:
        # The canonical link is often store-free; the page's own address says
        # which store's price was on screen.
        store_ref = _strip_scope(urlsplit(page_url.strip()).path or "/", scope_patterns)[1]
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", "")), store_ref


_TOKENS = re.compile(r"[a-z0-9]{4,40}")


def _same_product(page_url: str, canonical: str) -> bool:
    page, canon = urlsplit(page_url.strip()), urlsplit(canonical.strip())
    if (page.hostname or "").removeprefix("www.") != (canon.hostname or "").removeprefix("www."):
        return False
    segments = [s for s in (page.path or "").lower().split("/") if s]
    tokens = _TOKENS.findall(segments[-1]) if segments else []
    if not tokens:
        return True  # nothing on the page's address to check against
    return any(t in (canon.path or "").lower() for t in tokens)


def _strip_scope(path: str, scope_patterns: Sequence[re.Pattern[str]]) -> tuple[str, str | None]:
    for pattern in scope_patterns:
        match = pattern.match(path)
        if match:
            return path[match.end() :] or "/", match.group(1)
    return path, None
