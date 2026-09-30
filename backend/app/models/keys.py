"""Stable keys for vendors and locations (1F), and ingredient slugs (1G).

Vendors get ``slug`` and locations ``<vendor slug>/<location slug>``. An
ingredient created without a standard key gets ``local.<name>``: the dot keeps
it from ever equalling a standard-list key (``app.catalog.names``).

A ``kitchen-erp-vendors/1`` file names every vendor and location by key, so
keys are assigned once, on creation, and never follow a rename. Assignment
happens in one ``before_flush`` hook, so every path that creates a vendor or a
location (the forms, OSM adoption, and later import) gets a key without asking
for one.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.catalog.names import GENERATED_SLUG_PREFIX
from app.models.catalog import Ingredient
from app.models.geo import Vendor, VendorLocation


def slugify(text: str, fallback: str) -> str:
    """Lower-case ASCII words joined by hyphens, at most 80 characters."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")[:80].strip("-")
    return slug or fallback


def _unique(session: Session, column: Any, base: str, pending: set[str]) -> str:
    like = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with session.no_autoflush:
        taken = set(
            session.execute(select(column).where(column.like(f"{like}%", escape="\\"))).scalars()
        )
    taken |= pending
    candidate, n = base, 1
    while candidate in taken:
        n += 1
        candidate = f"{base}-{n}"
    pending.add(candidate)
    return candidate


@event.listens_for(Session, "before_flush")
def _assign_keys(session: Session, _context: Any, _instances: Any) -> None:
    new = list(session.new)
    slugs: set[str] = set()
    for obj in new:
        if isinstance(obj, Vendor) and not obj.slug:
            obj.slug = _unique(session, Vendor.slug, slugify(obj.name or "", "vendor"), slugs)
    keys: set[str] = set()
    for obj in new:
        if isinstance(obj, VendorLocation) and not obj.key:
            vendor = obj.vendor
            if vendor is None and obj.vendor_id is not None:
                with session.no_autoflush:
                    vendor = session.get(Vendor, obj.vendor_id)
            prefix = vendor.slug if vendor is not None and vendor.slug else "vendor"
            base = f"{prefix}/{slugify(obj.name or '', 'location')}"
            obj.key = _unique(session, VendorLocation.key, base, keys)
    ingredient_slugs: set[str] = set()
    for obj in new:
        if isinstance(obj, Ingredient) and not obj.slug:
            base = GENERATED_SLUG_PREFIX + slugify(obj.name or "", "ingredient")
            obj.slug = _unique(session, Ingredient.slug, base, ingredient_slugs)
