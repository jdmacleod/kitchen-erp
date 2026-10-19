"""Kind-specific product details (03, 1H), validated by category.

Meat and seafood have a model, and produce has one for what its name used to carry
alone: whether it was grown organically, and its size grade. Every other category
takes an empty object. Primal and cut stay free text until a controlled list earns
its keep.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

ProductKind = Literal["branded", "private_label", "random_weight", "loose", "unbranded_vendor"]
PRODUCT_KINDS: tuple[str, ...] = (
    "branded",
    "private_label",
    "random_weight",
    "loose",
    "unbranded_vendor",
)


class MeatAttributes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    species: Literal["lamb", "pork", "beef", "chicken", "turkey", "goat", "duck", "other"]
    primal: str | None = Field(default=None, max_length=60)
    cut: str | None = Field(default=None, max_length=60)
    bone: Literal["bone_in", "boneless"] | None = None
    skin: Literal["skin_on", "skinless"] | None = None
    grade: str | None = Field(default=None, max_length=40)
    form: Literal["whole", "sliced", "cubed", "ground"] | None = None


ProduceGrade = Literal["small", "medium", "large", "extra_large", "jumbo"]
PRODUCE_GRADES: tuple[str, ...] = ("small", "medium", "large", "extra_large", "jumbo")


class ProduceAttributes(BaseModel):
    """Organic and conventional of one food are two products of one ingredient, so a
    comparison can keep them apart or show them together. A size grade matters to
    produce sold by the piece; sold by weight, a large and a small cost the same."""

    model_config = ConfigDict(extra="forbid")

    organic: bool | None = None
    grade: ProduceGrade | None = None


MODELS: dict[str, type[BaseModel]] = {
    "meat": MeatAttributes,
    "seafood": MeatAttributes,
    "produce": ProduceAttributes,
}


def validate_attributes(category_key: str | None, data: dict[str, Any] | None) -> dict[str, Any]:
    """The stored form of a product's attributes, or ValueError (and pydantic's) when invalid."""
    data = data or {}
    model = MODELS.get(category_key or "")
    if model is None:
        if data:
            raise ValueError("this ingredient's category has no product attributes")
        return {}
    if not data:
        return {}
    return model.model_validate(data).model_dump(exclude_none=True)


def is_organic(attributes: dict[str, Any] | None) -> bool:
    return bool((attributes or {}).get("organic"))
