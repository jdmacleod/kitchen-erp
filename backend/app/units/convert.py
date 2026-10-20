"""convert(qty, from_unit, context) -> CanonicalQty | ConversionFailure.

Resolution order is fixed and documented in the Phase 1 specification:

1. `from_unit` is a named measure label for the ingredient: multiply by its
   canonical quantity. When the canonical unit is a count, a size grade
   ("large", "extra large") names which pieces, not a fraction of one: "12
   large" eggs are 12 eggs, whatever the "large" measure weighs against a
   medium one. The one exception is "each" when a product with a pack
   is supplied: that is one pack (step 2), even if the ingredient has an
   "each" measure, which then weighs the pack's pieces (issue 357).
2. `from_unit` is a count unit and a product with a pack is supplied: convert
   the pack quantity recursively and multiply. When the canonical unit is a
   count and the pack states its pieces (19 oz, 5 pieces), one pack is that
   many pieces. When the pack itself is counted in pieces (8 each) and the
   canonical unit is not a count, one piece is the ingredient's measure for one
   ("each", else "whole", else "medium"); without such a measure the pack has
   no weight and the conversion fails with `no_pack` (issue 357).
3. `from_unit` shares a dimension with the canonical unit: apply unit factors.
4. It crosses mass and volume: product density override, else ingredient
   density, else `no_density`.
5. It is a count unit with no measure or pack: `unknown_measure`, or `no_pack`
   when a product was supplied but has no pack.

Bunches (issue 371) are a family of their own: a bunch is never a piece. A
quantity in bunches, or a pack counted in bunches, reaches the canonical unit
only through the ingredient's "bunch" measure (provenance `measure`, or `pack`
via it). Without one it fails with `no_bunch_weight`, and the failure says how
many bunches it was, so a price can still be compared per bunch.

A null quantity fails with `no_qty`. There is no default density.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Context, Decimal
from typing import Literal

from app.units.table import CANONICAL_UNITS, Unit, units_by_code

# 2: "each" of a packed product is the pack; packs in pieces (issue 357).
# 3: bunches, through the ingredient's "bunch" measure only (issue 371).
# 4: a size grade of an ingredient counted in pieces is that many pieces.
CONVERT_VERSION = "4"

# Words that grade a piece by size. For an ingredient counted in pieces they
# say which pieces, never how many: "12 large" is 12.
SIZE_GRADES = frozenset({"small", "medium", "large", "extra large", "extra-large", "xl", "jumbo"})

_CTX = Context(prec=28, rounding=ROUND_HALF_EVEN)

FailureCode = Literal["no_density", "unknown_measure", "no_pack", "no_qty", "no_bunch_weight"]
BUNCH = "bunch"  # the unit, and the label of the measure that weighs one
BridgeKind = Literal["none", "density", "density_override", "measure", "pack", "pack_count"]


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
    count: int | None = None  # pieces in the pack, when it states them beside its size


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
    # For `no_bunch_weight`: how many bunches the quantity was, so its price can
    # still be compared per bunch (issue 371). None for every other failure.
    bunches: Decimal | None = None


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


# Measure labels that stand for one of the ingredient, the most general first.
PIECE_LABELS = ("each", "whole", "medium")


def _piece_measure(measures: tuple[Measure, ...]) -> Measure | None:
    """The ingredient's measure for one piece, if it has one."""
    for label in PIECE_LABELS:
        found = _find_measure(label, measures)
        if found is not None:
            return found
    return None


def _pieces(qty: Decimal, pack: Pack, units: Mapping[str, Unit], measures, canonical: str):
    """``qty`` packs of ``pack``, a pack counted in pieces, through a piece measure."""
    piece = _piece_measure(measures)
    if piece is None:
        return ConversionFailure(
            "no_pack",
            f"The pack is {pack.qty} {pack.unit} and nothing says what one piece weighs.",
        )
    pieces = _mul(pack.qty, units[pack.unit].to_base_factor)
    return CanonicalQty(
        _mul(qty, _mul(pieces, piece.canonical_qty)),
        canonical,
        Provenance(
            "pack",
            None,
            True,
            f"{pack.qty} {pack.unit}",
            via=Provenance("measure", piece.source, piece.confirmed, piece.label),
        ),
    )


def _bunches(
    bunches: Decimal, measures: tuple[Measure, ...], canonical: str
) -> CanonicalQty | ConversionFailure:
    """``bunches`` bunches through the ingredient's "bunch" measure, never anything else."""
    measure = _find_measure(BUNCH, measures)
    if measure is None:
        return ConversionFailure(
            "no_bunch_weight", "Nothing says what one bunch weighs.", bunches=bunches
        )
    return CanonicalQty(
        _mul(bunches, measure.canonical_qty),
        canonical,
        Provenance("measure", measure.source, measure.confirmed, measure.label),
    )


def _bunch_pack(
    each: Decimal, pack: Pack, measures: tuple[Measure, ...], canonical: str
) -> CanonicalQty | ConversionFailure:
    """``each`` packs of a pack counted in bunches ("1 bunch")."""
    inner = _bunches(_mul(each, pack.qty), measures, canonical)
    if isinstance(inner, ConversionFailure):
        return inner
    return CanonicalQty(
        inner.qty,
        canonical,
        Provenance("pack", None, True, f"{pack.qty} {pack.unit}", via=inner.provenance),
    )


def _through_pack(
    each: Decimal, product: ProductContext, context: ConversionContext
) -> CanonicalQty | ConversionFailure:
    """``each`` packs of the product (step 2): its pieces, its pieces' measure, or its size."""
    assert product.pack is not None  # noqa: S101 - the caller checked
    units = context.units
    canonical = context.canonical_unit
    canonical_dim = units[canonical].dimension
    pack_dim = units[product.pack.unit].dimension if product.pack.unit in units else None
    if pack_dim == "bunch":
        return _bunch_pack(each, product.pack, context.measures, canonical)
    if canonical_dim == "count" and product.pack.count and pack_dim != "count":
        # The pack's stated pieces; its size never becomes a count (no piece weight).
        return CanonicalQty(
            _mul(each, Decimal(product.pack.count)),
            canonical,
            Provenance(
                "pack_count",
                None,
                True,
                f"{product.pack.qty} {product.pack.unit}, {product.pack.count} pieces",
            ),
        )
    if pack_dim == "count" and canonical_dim != "count":
        return _pieces(each, product.pack, units, context.measures, canonical)
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


def _named(qty: Decimal, from_unit: str, context: ConversionContext) -> CanonicalQty | None:
    """``qty`` in a measure of the ingredient, or None when ``from_unit`` names none.
    A size grade of an ingredient counted in pieces is that many pieces."""
    canonical = context.canonical_unit
    units = context.units
    if units[canonical].dimension == "count" and from_unit.strip().lower() in SIZE_GRADES:
        return CanonicalQty(_mul(qty, units[canonical].to_base_factor), canonical, NO_BRIDGE)
    measure = _find_measure(from_unit, context.measures)
    if measure is None:
        return None
    return CanonicalQty(
        _mul(qty, measure.canonical_qty),
        canonical,
        Provenance("measure", measure.source, measure.confirmed, measure.label),
    )


def convert(  # noqa: C901
    qty: Decimal | None, from_unit: str, context: ConversionContext
) -> CanonicalQty | ConversionFailure:
    if qty is None:
        return ConversionFailure("no_qty", "No quantity was given.")
    qty = Decimal(qty)
    units = context.units
    canonical = context.canonical_unit
    canonical_dim = units[canonical].dimension
    if canonical not in CANONICAL_UNITS:
        raise ValueError(f"canonical unit must be one of {sorted(CANONICAL_UNITS)}")

    # 1. "each" of a product with a pack is one pack, before any measure: an
    # "each" measure weighs one piece, and a pack may hold many (issue 357).
    product = context.product
    if from_unit == "each" and product is not None and product.pack is not None:
        return _through_pack(_mul(qty, units["each"].to_base_factor), product, context)

    # 1. Named measure, or a size grade of pieces.
    named = _named(qty, from_unit, context)
    if named is not None:
        return named

    unit = units.get(from_unit)
    if unit is None:
        return ConversionFailure(
            "unknown_measure", f"'{from_unit}' is neither a unit nor a measure of this ingredient."
        )

    # Bunches: the "bunch" measure (already tried above) or a typed failure (issue 371).
    if unit.dimension == "bunch":
        return _bunches(_mul(qty, unit.to_base_factor), context.measures, canonical)

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
            assert product is not None  # noqa: S101 - pack_applies says so
            return _through_pack(_mul(qty, unit.to_base_factor), product, context)
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
