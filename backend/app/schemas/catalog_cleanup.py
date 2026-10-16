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
]


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


class CleanupOut(ApiModel):
    items: list[CleanupSuggestionOut]


class CleanupKeysIn(ApiModel):
    keys: list[str] = Field(min_length=1, max_length=200)


class CleanupApplyIn(CleanupKeysIn):
    # A key's value chosen instead of the one offered: a category key, for a category.
    chosen: dict[str, str] = Field(default_factory=dict, max_length=200)


class CleanupApplyOut(ApiModel):
    applied: int
    # Keys no longer offered: the data changed since the page was loaded.
    gone: list[str]
    # Guesses named with others, left unapplied: a guess is accepted on its own.
    held: list[str]
