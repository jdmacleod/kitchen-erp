from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel

CleanupKind = Literal[
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


class CleanupConsequenceOut(ApiModel):
    """One thing an accept changes beyond its row: a recipe line (linked by its recipe)
    that loses its price or can no longer convert, or a measure dropped or rewritten."""

    text: str
    recipe_id: uuid.UUID | None = None


class CleanupSuggestionOut(ApiModel):
    """One proposed fix (#349): what it changes, from what, to what, and why."""

    key: str
    kind: CleanupKind
    subject_id: uuid.UUID
    subject: str
    before: str
    after: str
    reason: str
    # False for a category nothing could guess: choose one before accepting.
    ready: bool
    # What accepting applies as offered: the category key, for a category; else "".
    value: str
    # Whether the person may choose the value instead (a category, from the nine keys).
    choosable: bool
    # "sure": a rule settled it, and "Accept these N" may apply it. "guess": a search
    # or a resemblance found it; accepted one at a time, or with a value chosen (issue 372).
    confidence: Literal["sure", "guess"]
    # A move's or a unit switch's consequences, read from the latest cost snapshots.
    consequences: list[CleanupConsequenceOut] = Field(default_factory=list)
    # Sent back with an accept: a different one now is 409 cleanup_changed.
    fingerprint: str = ""
    # A unit switch's measures that would be dropped (the red confirm counts them).
    measures_dropped: int = 0


class CleanupOut(ApiModel):
    items: list[CleanupSuggestionOut]


class CleanupKeysIn(ApiModel):
    keys: list[str] = Field(min_length=1, max_length=200)


class CleanupApplyIn(CleanupKeysIn):
    # A key's value chosen instead of the one offered: a category key, for a category.
    chosen: dict[str, str] = Field(default_factory=dict, max_length=200)
    # A key's fingerprint as listed: the consequences the person was shown.
    fingerprints: dict[str, str] = Field(default_factory=dict, max_length=200)


class CleanupResultOut(ApiModel):
    """What one accepted move or unit switch changed."""

    key: str
    prices_renormalized: int = 0
    recipes_recomputed: int = 0
    measures_dropped: int = 0
    measures_rewritten: int = 0


class CleanupApplyOut(ApiModel):
    applied: int
    # Keys no longer offered: the data changed since the page was loaded.
    gone: list[str]
    # Guesses named with others, left unapplied: a guess is accepted on its own.
    held: list[str]
    results: list[CleanupResultOut] = Field(default_factory=list)


class UnitSwitchOut(ApiModel):
    """An ingredient's switch to its standard entry's unit (#527), or null when its
    unit is the entry's or it has none; offered here even when skipped in Tidy."""

    suggestion: CleanupSuggestionOut | None


class UnitSwitchIn(ApiModel):
    # The fingerprint of the switch as shown; a different one now is 409 cleanup_changed.
    fingerprint: str | None = Field(default=None, max_length=64)
