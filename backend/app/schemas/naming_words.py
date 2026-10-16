"""Word completions for a Name on the naming pass (04, 2I; #385)."""

from __future__ import annotations

from typing import Literal

from app.schemas.base import ApiModel


class WordCompletion(ApiModel):
    word: str
    source: Literal["learned", "vocabulary"]


class WordCompletions(ApiModel):
    items: list[WordCompletion]
