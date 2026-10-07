"""The ``kitchen-erp-ingredients/1`` file: a household's ingredient vocabulary (spec 03 §1G).

One entry per ingredient: its name and key, category, unit, density, yield,
perishability, spellings, USDA references and measures, and whether it is
active or merged into another. Nothing from purchases, prices, products,
vendors or people. Every number is written as a string, so a file never holds
a float (non-negotiable 1); the reader refuses one.

A file is untrusted text: it is loaded by ``services.interchange`` and validated
here before anything reads it (non-negotiable 7).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

FORMAT = "kitchen-erp-ingredients/1"
FORMATS = (FORMAT,)

Source = Literal["usda", "label", "measured", "llm", "manual"]
Text = Annotated[str, Field(min_length=1, max_length=200)]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FileSource(_Strict):
    name: Annotated[str, Field(max_length=100)]
    exported_at: datetime


class Density(_Strict):
    g_per_ml: Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=5)]
    source: Source
    confirmed: bool = False


class Measure(_Strict):
    label: Annotated[str, Field(min_length=1, max_length=100)]
    qty: Annotated[Decimal, Field(gt=0)]
    source: Source
    confirmed: bool = False


class IngredientEntry(_Strict):
    # The ingredient's slug: a standard-list key, or "local.<name>".
    key: Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[a-z0-9][a-z0-9._-]*$")]
    name: Text
    category: Annotated[str, Field(max_length=100)] | None = None
    unit: Literal["g", "ml", "each"]
    density: Density | None = None
    yield_pct: Annotated[Decimal, Field(gt=0, le=1)] = Decimal("1")
    perishability: Literal["shelf_stable", "refrigerated", "fresh"] = "shelf_stable"
    notes: Annotated[str, Field(max_length=4000)] | None = None
    spellings: Annotated[list[Text], Field(max_length=200)] = []
    # USDA FoodData Central ids; the first is the preferred one.
    fdc: Annotated[list[Annotated[int, Field(gt=0)]], Field(max_length=20)] = []
    measures: Annotated[list[Measure], Field(max_length=100)] = []
    active: bool = True
    # The key of the ingredient this one was merged into; an import skips it.
    merged_into: Annotated[str, Field(max_length=120)] | None = None

    @field_validator("name", "spellings", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            return [v.strip() if isinstance(v, str) else v for v in value]
        return value


class IngredientFile(_Strict):
    format: Literal["kitchen-erp-ingredients/1"]
    source: FileSource
    ingredients: Annotated[list[IngredientEntry], Field(max_length=20000)]


# --- the import report ---------------------------------------------------------

Outcome = Literal["created", "updated", "unchanged", "conflict", "skipped"]


class FieldChange(BaseModel):
    field: str
    value: str | None


class FieldConflict(BaseModel):
    field: str
    current: str | None
    file: str | None


class ImportItem(BaseModel):
    key: str
    name: str
    outcome: Outcome
    changes: list[FieldChange] = Field(default_factory=list)
    conflicts: list[FieldConflict] = Field(default_factory=list)
    # Why an entry was skipped or partly applied, in plain words.
    reason: str | None = None


class ImportCounts(BaseModel):
    created: int
    updated: int
    unchanged: int
    conflicts: int
    skipped: int


class ImportReport(BaseModel):
    dry_run: bool
    counts: ImportCounts
    items: list[ImportItem]
