from __future__ import annotations

import uuid
from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel

CleanupKind = Literal[
    "category", "ingredient_name", "usda_link", "ingredient_merge", "product_name", "product_brand"
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
    # False for a category nothing could guess: open the ingredient to choose one.
    ready: bool


class CleanupOut(ApiModel):
    items: list[CleanupSuggestionOut]


class CleanupKeysIn(ApiModel):
    keys: list[str] = Field(min_length=1, max_length=200)


class CleanupApplyOut(ApiModel):
    applied: int
    # Keys no longer offered: the data changed since the page was loaded.
    gone: list[str]
