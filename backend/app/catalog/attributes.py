"""Kind-specific product details (03, 1H), validated by category.

An ingredient is a commodity and a product carries what tells its items apart, the
way PLU codes, GS1 classification and USDA names do (03, 1H amendment 2026-10-27):
produce its variety (a Fuji apple is a product of "apple"), organic growing and size
grade; meat its species, cut, bone, skin, form, grade and lean ratio; seafood its
species, market name, wild or farmed, count size and form. Every other category takes
only ``organic``. Primal, cut and variety stay free text until a controlled list earns
its keep.
"""

from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator

ProductKind = Literal["branded", "private_label", "random_weight", "loose", "unbranded_vendor"]
PRODUCT_KINDS: tuple[str, ...] = (
    "branded",
    "private_label",
    "random_weight",
    "loose",
    "unbranded_vendor",
)


MeatSpecies = Literal["lamb", "pork", "beef", "chicken", "turkey", "goat", "duck", "other"]
MEAT_SPECIES: tuple[str, ...] = get_args(MeatSpecies)
MeatForm = Literal["whole", "sliced", "cubed", "ground"]


class MeatAttributes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species: MeatSpecies
    primal: str | None = Field(default=None, max_length=60)
    cut: str | None = Field(default=None, max_length=60)
    bone: Literal["bone_in", "boneless"] | None = None
    skin: Literal["skin_on", "skinless"] | None = None
    # USDA quality grade ("prime", "choice", "select") or a store's own word; lower case.
    grade: str | None = Field(default=None, max_length=40)
    form: MeatForm | None = None
    # The lean share of a ground meat's lean/fat ratio: 80 for "80/20".
    lean_pct: int | None = Field(default=None, ge=50, le=99)
    organic: bool | None = None

    @field_validator("grade")
    @classmethod
    def _grade_lower(cls, value: str | None) -> str | None:
        return value.strip().lower() or None if value is not None else None


# Market species, after the FDA Seafood List's acceptable market names; the list's
# finer name ("Atlantic", "sockeye") is ``species_detail``.
SeafoodSpecies = Literal[
    "salmon", "tuna", "shrimp", "cod", "halibut", "branzino", "sea bass", "snapper",
    "trout", "tilapia", "catfish", "mackerel", "sardine", "anchovy", "swordfish",
    "mahi mahi", "pollock", "haddock", "sole", "flounder", "scallop", "crab", "lobster",
    "clam", "mussel", "oyster", "squid", "octopus", "other",
]  # fmt: skip
SEAFOOD_SPECIES: tuple[str, ...] = get_args(SeafoodSpecies)
SeafoodForm = Literal["whole", "fillet", "loin", "steak", "sliced", "peeled", "smoked", "canned"]
# Shrimp and scallops are sized by pieces per pound: "26/30", or "U15" for under 15.
COUNT_SIZE = r"(?:\d{1,3}/\d{1,3}|U\d{1,3})"


class SeafoodAttributes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species: SeafoodSpecies
    species_detail: str | None = Field(default=None, max_length=40)
    origin: Literal["wild", "farmed"] | None = None
    count_size: str | None = Field(default=None, pattern=f"^{COUNT_SIZE}$")
    form: SeafoodForm | None = None
    organic: bool | None = None


ProduceGrade = Literal["small", "medium", "large", "extra_large", "jumbo"]
PRODUCE_GRADES: tuple[str, ...] = ("small", "medium", "large", "extra_large", "jumbo")


class ProduceAttributes(BaseModel):
    """Organic and conventional of one food are two products of one ingredient, so a
    comparison can keep them apart or show them together. A size grade matters to
    produce sold by the piece; sold by weight, a large and a small cost the same."""

    model_config = ConfigDict(extra="forbid")

    organic: bool | None = None
    grade: ProduceGrade | None = None
    # The cultivar, as the store names it ("Honeycrisp", "Yukon Gold").
    variety: str | None = Field(default=None, max_length=40)

    @field_validator("variety")
    @classmethod
    def _variety_trimmed(cls, value: str | None) -> str | None:
        return " ".join(value.split()) or None if value is not None else None


class GeneralAttributes(BaseModel):
    """Every other food: organic or not, like produce, and nothing else."""

    model_config = ConfigDict(extra="forbid")

    organic: bool | None = None


MODELS: dict[str, type[BaseModel]] = {
    "meat": MeatAttributes,
    "seafood": SeafoodAttributes,
    "produce": ProduceAttributes,
}


def validate_attributes(category_key: str | None, data: dict[str, Any] | None) -> dict[str, Any]:
    """The stored form of a product's attributes, or ValueError (and pydantic's) when invalid."""
    if not data:
        return {}
    model = MODELS.get(category_key or "", GeneralAttributes)
    return model.model_validate(data).model_dump(exclude_none=True)


def fitting(category_key: str | None, data: dict[str, Any] | None) -> dict[str, Any]:
    """What of a product's attributes another category has a place for: organic stays
    when produce moves to the pantry; a meat's species doesn't survive the move."""
    model = MODELS.get(category_key or "", GeneralAttributes)
    kept = {k: v for k, v in (data or {}).items() if k in model.model_fields}
    try:
        return validate_attributes(category_key, kept)
    except ValueError:
        return {}


def is_organic(attributes: dict[str, Any] | None) -> bool:
    return bool((attributes or {}).get("organic"))
