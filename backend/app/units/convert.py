"""convert(qty, from_unit, context) -> CanonicalQty | ConversionFailure.

Resolution order is fixed and documented in the Phase 1 specification:

1. `from_unit` is a named measure label for the ingredient: multiply by its
   canonical quantity.
2. `from_unit` is a count unit and a product with a pack is supplied: convert
   the pack quantity recursively and multiply.
3. `from_unit` shares a dimension with the canonical unit: apply unit factors.
4. It crosses mass and volume: product density override, else ingredient
   density, else `no_density`.
5. It is a count unit with no measure or pack: `unknown_measure`, or `no_pack`
   when a product was supplied but has no pack.

A null quantity fails with `no_qty`. There is no default density.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Context, Decimal
from typing import Literal

from app.units.table import BASE_UNIT, Unit, units_by_code

CONVERT_VERSION = "1"

_CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)

FailureCode = Literal["no_density", "unknown_measure", "no_pack", "no_qty"]
BridgeKind = Literal["none", "density", "density_override", "measure", "pack"]


@dataclass(frozen=True, slots=True)
class Measure:
    label: str
    canonical_qty: Decimal
    source: str
    confirmed: bool


@dataclass(frozen=True, slots=True)
class Pack:
    qty: Decimal
    unit: str


@dataclass(frozen=True, slots=True)
class ProductContext:
    pack: Pack | None = None
    density_g_per_ml: Decimal | None = None
    density_source: str | None = None
    density_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class ConversionContext:
    canonical_unit: str  # g, ml, or each
    density_g_per_ml: Decimal | None = None
    density_source: str | None = None
    density_confirmed: bool = False
    measures: tuple[Measure, ...] = ()
    product: ProductContext | None = None
    units: Mapping[str, Unit] = field(default_factory=units_by_code)


@dataclass(frozen=True, slots=True)
class Provenance:
    bridge_kind: BridgeKind
    source: str | None = None
    confirmed: bool | None = None
    detail: str | None = None  # measure label, or the pack as text
    via: Provenance | None = None  # the inner conversion when a pack was crossed

    @property
    def rests_on_unconfirmed(self) -> bool:
        if self.bridge_kind != "none" and self.confirmed is False:
            return True
        return self.via.rests_on_unconfirmed if self.via is not None else False


NO_BRIDGE = Provenance(bridge_kind="none")


@dataclass(frozen=True, slots=True)
class CanonicalQty:
    qty: Decimal
    unit: str
    provenance: Provenance
    version: str = CONVERT_VERSION


@dataclass(frozen=True, slots=True)
class ConversionFailure:
    code: FailureCode
    message: str
    version: str = CONVERT_VERSION


def _mul(a: Decimal, b: Decimal) -> Decimal:
    return _CTX.multiply(a, b)


def _div(a: Decimal, b: Decimal) -> Decimal:
    return _CTX.divide(a, b)


def convert_between(
    qty: Decimal, from_unit: str, to_unit: str, units: Mapping[str, Unit]
) -> Decimal:
    """Same-dimension conversion through the base unit. Raises on a dimension mismatch."""
    src, dst = units[from_unit], units[to_unit]
    if src.dimension != dst.dimension:
        raise ValueError(f"{from_unit} and {to_unit} differ in dimension")
    return _div(_mul(qty, src.to_base_factor), dst.to_base_factor)


def _find_measure(label: str, measures: tuple[Measure, ...]) -> Measure | None:
    wanted = label.strip().lower()
    for measure in measures:
        if measure.label.strip().lower() == wanted:
            return measure
    return None


def convert(
    qty: Decimal | None, from_unit: str, context: ConversionContext
) -> CanonicalQty | ConversionFailure:
    if qty is None:
        return ConversionFailure("no_qty", "No quantity was given.")
    qty = Decimal(qty)
    units = context.units
    canonical = context.canonical_unit
    canonical_dim = units[canonical].dimension
    if BASE_UNIT[canonical_dim] != canonical:
        raise ValueError(f"canonical unit must be one of {sorted(BASE_UNIT.values())}")

    # 1. Named measure.
    measure = _find_measure(from_unit, context.measures)
    if measure is not None:
        return CanonicalQty(
            _mul(qty, measure.canonical_qty),
            canonical,
            Provenance("measure", measure.source, measure.confirmed, measure.label),
        )

    unit = units.get(from_unit)
    if unit is None:
        return ConversionFailure(
            "unknown_measure", f"'{from_unit}' is neither a unit nor a measure of this ingredient."
        )

    # 2. Count unit with a pack. "1 each" of a packaged product means one pack,
    # whatever the canonical unit; other count units (dozen) reach a pack only
    # when the canonical unit is not itself a count.
    product = context.product
    pack_applies = (
        unit.dimension == "count"
        and product is not None
        and product.pack is not None
        and (unit.code == "each" or canonical_dim != "count")
    )
    if unit.dimension == "count" and (pack_applies or canonical_dim != "count"):
        if pack_applies:
            each = _mul(qty, unit.to_base_factor)
            inner_context = ConversionContext(
                canonical_unit=canonical,
                density_g_per_ml=context.density_g_per_ml,
                density_source=context.density_source,
                density_confirmed=context.density_confirmed,
                measures=(),  # a pack is expressed in units, never in measures
                product=ProductContext(
                    pack=None,
                    density_g_per_ml=product.density_g_per_ml,
                    density_source=product.density_source,
                    density_confirmed=product.density_confirmed,
                ),
                units=units,
            )
            inner = convert(product.pack.qty, product.pack.unit, inner_context)
            if isinstance(inner, ConversionFailure):
                return inner
            return CanonicalQty(
                _mul(each, inner.qty),
                canonical,
                Provenance(
                    "pack",
                    None,
                    True,
                    f"{product.pack.qty} {product.pack.unit}",
                    via=inner.provenance if inner.provenance.bridge_kind != "none" else None,
                ),
            )
        # 5. Count unit with nothing to bridge it.
        if product is not None:
            return ConversionFailure("no_pack", "The product has no pack size.")
        return ConversionFailure(
            "unknown_measure", f"No measure or pack turns '{from_unit}' into {canonical}."
        )

    # 3. Same dimension.
    if unit.dimension == canonical_dim:
        return CanonicalQty(_mul(qty, unit.to_base_factor), canonical, NO_BRIDGE)

    # 4. Mass <-> volume through a density.
    if {unit.dimension, canonical_dim} == {"mass", "volume"}:
        product = context.product
        if product is not None and product.density_g_per_ml is not None:
            density, prov = (
                product.density_g_per_ml,
                Provenance("density_override", product.density_source, product.density_confirmed),
            )
        elif context.density_g_per_ml is not None:
            density, prov = (
                context.density_g_per_ml,
                Provenance("density", context.density_source, context.density_confirmed),
            )
        else:
            return ConversionFailure(
                "no_density", "Crossing mass and volume needs a density; none is known."
            )
        base = _mul(qty, unit.to_base_factor)  # in g or ml
        if unit.dimension == "volume":  # ml -> g
            return CanonicalQty(_mul(base, density), canonical, prov)
        return CanonicalQty(_div(base, density), canonical, prov)  # g -> ml

    # Mass or volume asked for in a count canonical: no bridge exists.
    return ConversionFailure(
        "unknown_measure", f"No measure turns {unit.dimension} into {canonical}."
    )
