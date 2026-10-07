"""Retailer adapters for captured pages (04, 2M): pure functions from a plugin folder.

``PRODUCT_ADAPTERS`` names each adapter as ``module:function``. Modules are
imported from ``PLUGINS_PATH`` (``data/plugins/`` mounted read-only at
``/plugins``), so a household can keep retailer-specific code out of this public
repository. The interface is one function::

    def adapter(page: dict) -> list[dict]:   # [{"field": "price", "value": "3.49"}, ...]

``page`` holds the captured page: ``page_url``, ``canonical_url``, ``title``,
``meta``, ``structured_data`` (raw text) and ``dom_text``. An adapter does no
I/O; its answer is untrusted like any other evidence and becomes candidates
with source ``adapter`` only for fields the merge knows. An item
``{"field": "image", "value": "https://…"}`` names a photo the page shows: it is
kept as an address, never fetched here, and only an absolute https address counts.

A missing or failing adapter is logged and skipped, and health lists which
loaded. An adapter that raises during a capture is recorded on the stage
result, and generic extraction carries on.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from app.catalog import proposals as merging
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

Adapter = Callable[[dict[str, Any]], list[dict[str, Any]]]
MAX_CANDIDATES = 50
MAX_IMAGES = 4
MAX_URL = 2048


@dataclass(frozen=True)
class Loaded:
    name: str
    status: str  # loaded | missing | invalid
    adapter: Adapter | None = None


_cache: dict[tuple[tuple[str, ...], str], list[Loaded]] = {}


def _load_one(name: str) -> Loaded:
    module_name, _, function = name.partition(":")
    if not module_name or not function:
        log.warning("product adapter misnamed", extra={"adapter": name})
        return Loaded(name, "invalid")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # a plugin's import error must not stop the app
        log.warning("product adapter missing", extra={"adapter": name, "exc": type(exc).__name__})
        return Loaded(name, "missing")
    adapter = getattr(module, function, None)
    if not callable(adapter):
        log.warning("product adapter is not a function", extra={"adapter": name})
        return Loaded(name, "invalid")
    return Loaded(name, "loaded", adapter)


def adapters() -> list[Loaded]:
    """The configured adapters, imported once per configuration."""
    settings = get_settings()
    key = (tuple(settings.product_adapters), settings.plugins_path)
    if key not in _cache:
        path = Path(settings.plugins_path)
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
        _cache[key] = [_load_one(name) for name in settings.product_adapters]
    return _cache[key]


def statuses() -> dict[str, str]:
    return {a.name: a.status for a in adapters()}


def _no_floats(value: Any) -> Any:
    """An adapter's numbers as exact strings: never a float past this point."""
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    if isinstance(value, dict):
        return {k: _no_floats(v) for k, v in value.items()}
    return value


def _candidate(item: Any) -> merging.Candidate | None:
    if not isinstance(item, dict):
        return None
    field, value = item.get("field"), item.get("value")
    if not isinstance(field, str) or value is None:
        return None
    value = _no_floats(value)
    candidate = merging.Candidate(field, value, "adapter")
    try:
        merging._check(candidate)
    except merging.UnknownCandidate:
        return None
    return candidate


def _image(item: Any) -> str | None:
    """An adapter's ``image`` item as an absolute https address, or None."""
    if not isinstance(item, dict) or item.get("field") != "image":
        return None
    url = item.get("value")
    if not isinstance(url, str) or len(url) > MAX_URL:
        return None
    parts = urlsplit(url.strip())
    return parts.geturl() if parts.scheme == "https" and parts.hostname else None


@dataclass
class Run:
    candidates: list[merging.Candidate]
    images: list[str]  # photo addresses the adapters read off the page, at most MAX_IMAGES
    records: list[dict[str, Any]]  # one per adapter, for the stage result


def run(page: dict[str, Any]) -> Run:
    """Every loaded adapter's candidates and photo addresses, with a record of each run."""
    candidates: list[merging.Candidate] = []
    images: list[str] = []
    records: list[dict[str, Any]] = []
    for loaded in adapters():
        if loaded.adapter is None:
            records.append({"adapter": loaded.name, "status": loaded.status})
            continue
        try:
            answer = loaded.adapter(dict(page))
        except Exception as exc:
            log.warning("product adapter raised", extra={"adapter": loaded.name})
            records.append({"adapter": loaded.name, "error": type(exc).__name__})
            continue
        items = (answer if isinstance(answer, list) else [])[:MAX_CANDIDATES]
        found = [c for item in items if (c := _candidate(item))]
        candidates.extend(found)
        fields = sorted({c.field for c in found})
        record: dict[str, Any] = {"adapter": loaded.name, "fields": fields}
        shown = [url for item in items if (url := _image(item))]
        if shown:
            record["images"] = len(shown)
            images.extend(url for url in shown if url not in images)
        records.append(record)
    return Run(candidates, images[:MAX_IMAGES], records)
