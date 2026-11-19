"""One Tidy the catalog suggestion (#349): what it changes, and how sure it is (issue 372)."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal

Kind = Literal[
    "category",
    "ingredient_name",
    "usda_link",
    "ingredient_merge",
    "product_name",
    "product_brand",
    "product_pack",
    "product_move",
    "unit_mismatch",
]
# ``sure``: a rule settled it, so "Accept these N" may apply it with the rest.
# ``guess``: a search or a resemblance found it; accepted one at a time.
Confidence = Literal["sure", "guess"]


@dataclass(frozen=True)
class Consequence:
    """One thing accepting changes beyond the row itself: a recipe line that loses its
    price or can no longer convert, or a measure dropped or rewritten."""

    text: str
    recipe_id: uuid.UUID | None = None


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
    consequences: tuple[Consequence, ...] = ()
    # A hash of what the consequences rest on (a move or a unit switch): an accept
    # that sends another one was made against a list that has since changed.
    fingerprint: str = ""
    # A unit switch's measures that can't convert and go: the confirm button counts them.
    measures_dropped: int = 0

    @property
    def ready(self) -> bool:
        """Whether accepting it as offered applies something; a category nobody could
        guess needs the person to choose one first."""
        return self.kind != "category" or bool(self.value)

    @property
    def choosable(self) -> bool:
        """Whether the person may choose the value: a category, from the nine keys."""
        return self.kind == "category"


def as_out(s: Suggestion) -> dict:
    """The suggestion as the API sends it (``CleanupSuggestionOut``)."""
    return {
        "key": s.key,
        "kind": s.kind,
        "subject_id": s.subject_id,
        "subject": s.subject,
        "before": s.before,
        "after": s.after,
        "reason": s.reason,
        "ready": s.ready,
        "value": s.value if s.kind == "category" else "",
        "choosable": s.choosable,
        "confidence": s.confidence,
        "consequences": [{"text": c.text, "recipe_id": c.recipe_id} for c in s.consequences],
        "fingerprint": s.fingerprint,
        "measures_dropped": s.measures_dropped,
    }


def fingerprint(*parts: object) -> str:
    """A short, stable hash of the parts, in order."""
    text = "|".join(str(p) for p in parts)
    return hashlib.sha1(text.encode(), usedforsecurity=False).hexdigest()[:16]


def parse_key(key: str) -> tuple[str, uuid.UUID] | None:
    """(kind, subject id) of a key, or None for one that isn't a suggestion's."""
    kind, _, rest = key.partition(":")
    subject, _, _ = rest.partition(":")
    try:
        return kind, uuid.UUID(subject)
    except ValueError:
        return None


def suggestion_key(kind: str, subject: uuid.UUID, value: str) -> str:
    """The kind, the row and what it proposes: a skip holds until the data changes."""
    digest = hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest()[:10]
    return f"{kind}:{subject}:{digest}"
