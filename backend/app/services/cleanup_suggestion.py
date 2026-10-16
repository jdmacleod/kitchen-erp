"""One Tidy the catalog suggestion (#349): what it changes, and how sure it is (issue 372)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal

Kind = Literal[
    "category", "ingredient_name", "usda_link", "ingredient_merge", "product_name", "product_brand"
]
# ``sure``: a rule settled it, so "Accept these N" may apply it with the rest.
# ``guess``: a search or a resemblance found it; accepted one at a time.
Confidence = Literal["sure", "guess"]


@dataclass(frozen=True)
class Suggestion:
    key: str
    kind: Kind
    subject_id: uuid.UUID
    subject: str
    before: str
    after: str
    reason: str
    # What accepting applies: a category, a name, a USDA id ("" unlinks), a
    # survivor id, a kind, or "" to clear a brand.
    value: str
    other_id: uuid.UUID | None = None
    confidence: Confidence = "sure"

    @property
    def ready(self) -> bool:
        """Whether accepting it as offered applies something; a category nobody could
        guess needs the person to choose one first."""
        return self.kind != "category" or bool(self.value)

    @property
    def choosable(self) -> bool:
        """Whether the person may choose the value: a category, from the nine keys."""
        return self.kind == "category"


def suggestion_key(kind: str, subject: uuid.UUID, value: str) -> str:
    """The kind, the row and what it proposes: a skip holds until the data changes."""
    digest = hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest()[:10]
    return f"{kind}:{subject}:{digest}"
