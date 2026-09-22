"""Vendor-specific deterministic line parsers: the plug-in interface.

A parser is consulted before the generic language-model parser when one is
registered for the purchase's vendor. None are registered in Phase 2; the
registry exists so a later phase can add one without touching the pipeline.

Register with ``@register("vendor name")`` (matched case-insensitively against
``vendor.name``) or ``@register(vendor_id)``. ``parse`` returns a
:class:`ReceiptLines` or ``None`` when the text is not in the layout it knows,
in which case the generic parser runs.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Protocol

from app.ingest.schemas import ReceiptLines


class VendorParser(Protocol):
    name: str
    version: str

    def parse(self, receipt_text: str) -> ReceiptLines | None: ...


_REGISTRY: dict[str, VendorParser] = {}


def _key(vendor: str | uuid.UUID) -> str:
    return str(vendor).strip().lower()


def register(vendor: str | uuid.UUID) -> Callable[[type[VendorParser]], type[VendorParser]]:
    def decorator(cls: type[VendorParser]) -> type[VendorParser]:
        _REGISTRY[_key(vendor)] = cls()
        return cls

    return decorator


def unregister_all() -> None:
    _REGISTRY.clear()


def find(vendor_id: uuid.UUID | None, vendor_name: str | None) -> VendorParser | None:
    for key in (vendor_id, vendor_name):
        if key is not None:
            parser = _REGISTRY.get(_key(key))
            if parser is not None:
                return parser
    return None
