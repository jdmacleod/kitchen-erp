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
    """The address a listing is keyed by, and the store scope stripped from it."""
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


def _strip_scope(path: str, scope_patterns: Sequence[re.Pattern[str]]) -> tuple[str, str | None]:
    for pattern in scope_patterns:
        match = pattern.match(path)
        if match:
            return path[match.end() :] or "/", match.group(1)
    return path, None
