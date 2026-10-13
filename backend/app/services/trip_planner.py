"""The trip planner (17, 4B): which stores, in what order, buying what at each.

``plan`` is pure: it takes the list's unchecked items, the candidate locations,
the offer book (``trip_offers``), the leg matrix (``travel``) and the settings
that matter, and returns the three variants. Everything that touches the
database or the clock sits in ``create_plan``, ``candidates`` and ``choose``.

**The search.** Items are independent given a set of stops: each goes to the
stop in the set with its cheapest offer, so the search is over sets and their
order. The planner enumerates every set of 1 to ``max_stops`` candidates
(1,940 sets at 15 candidates and 4 stops), assigns items and sums item cost
once per set, then scores every ordering of the set (at most 35,715 orderings
in total): driving with the multiplier at each leg's projected departure in
``household_timezone``, overhead per stop (the location's ``stop_overhead_min``,
else the plan's), arrival-hours checks (an ordering that reaches a closed
location is dropped; unknown hours are allowed and annotated), and mileage when
a rate is given. The objective is item cost plus value of time times (driving
plus stop time) in hours, plus mileage rate times miles. A set whose stop would
buy nothing is dominated by the set without it and is not ordered.

A set that cannot source an item some other set can is infeasible (coverage is
compared as (required items, deferrable items), so a list that needs more
stops than allowed still plans, buying the most it can); an item no set can
source is annotated ``unsourceable``. ``fastest`` is the feasible ordering with
the least driving plus stop time, ties by objective; ``cheapest`` the least
item cost, ties by time; ``balanced`` the least objective. Each variant states
its marginal trade against ``balanced`` in money (item cost plus mileage) and
minutes.

**Annotations** per variant: ``forced_stop``, ``defer_suggestion`` (a deferrable
item alone at its stop: the plan is re-solved without it, at most ``max_stops``
re-solves per plan, and the saving reported), ``stale_dependent``,
``unsourceable``, ``estimated_legs``, ``hours_unknown`` and ``closed_excluded``.
Money is quantized to four places (the price book's rule); seconds are whole,
a multiplied leg rounding half up.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal
from itertools import combinations, permutations
from math import comb, perm
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import Settings
from app.core.errors import ApiError
from app.core.ids import new_id
from app.models import AppUser
from app.models.geo import HomeBase, Place, VendorLocation
from app.models.plan import (
    ShoppingListItem,
    TravelLeg,
    TripPlan,
    TripPlanStop,
    TripPlanStopItem,
    TripPlanVariant,
)
from app.schemas.plan import (
    Annotation,
    StopItemOut,
    StopOut,
    Trade,
    TripCreate,
    TripOut,
    TripSummary,
    VariantOut,
    VariantSummary,
)
from app.services import shopping_lists, travel, travel_tables, trip_offers
from app.services.opening_hours import is_open_at
from app.services.packs import CTX, money
from app.services.trip_offers import Offer, OfferBook

KINDS = ("fastest", "cheapest", "balanced")
METERS_PER_MILE = Decimal("1609.344")
DEFAULT_STOP_OVERHEAD_MIN = 10

PairKey = tuple[uuid.UUID, uuid.UUID]
IsOpen = Callable[[uuid.UUID, datetime], bool | None]


def enumeration_bound(candidates: int, max_stops: int) -> tuple[int, int]:
    """(sets, orderings) the search visits at most: C(n,1..k) and P(n,1..k)."""
    sizes = range(1, min(max_stops, candidates) + 1)
    return sum(comb(candidates, k) for k in sizes), sum(perm(candidates, k) for k in sizes)


# --- inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Candidate:
    location_id: uuid.UUID
    place_id: uuid.UUID
    overhead_min: int | None  # the location's own; None takes the plan's
    hours_known: bool
    distance_m: int  # straight line from the kitchen, for the candidate order


@dataclass(frozen=True, slots=True)
class Leg:
    seconds: int
    meters: int
    estimate: bool


_NO_LEG = Leg(0, 0, False)


@dataclass(frozen=True, slots=True)
class PlanItem:
    item_id: uuid.UUID
    label: str
    deferrable: bool
    pinned: bool  # a product item, or an ingredient item with a minimum quality


@dataclass(frozen=True, slots=True)
class PlanInputs:
    origin_place_id: uuid.UUID
    destination_place_id: uuid.UUID
    planned_at: datetime  # aware
    value_of_time_per_hour: Decimal
    max_stops: int
    mileage_rate: Decimal | None
    stop_overhead_min: int
    timezone: str
    multipliers: travel_tables.Multipliers


# --- outputs --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StopPlan:
    location_id: uuid.UUID
    leg_seconds: int  # free-flow, as stored
    leg_meters: int
    leg_estimate: bool
    multiplier: Decimal
    arrive_at: datetime
    overhead_min: int
    hours_known: bool
    items: tuple[Offer, ...]
    subtotal: Decimal


@dataclass(slots=True)
class Variant:
    kind: str
    stops: list[StopPlan]
    item_cost: Decimal
    driving_seconds: int  # projected: free-flow times the multipliers
    stop_seconds: int
    mileage_cost: Decimal
    total_cost: Decimal
    estimated: bool
    forced_item_ids: frozenset[uuid.UUID]
    annotations: list[dict[str, Any]] = field(default_factory=list)
    trade_money: Decimal = Decimal(0)
    trade_minutes: Decimal = Decimal(0)

    @property
    def total_seconds(self) -> int:
        return self.driving_seconds + self.stop_seconds


@dataclass(slots=True)
class PlanResult:
    variants: dict[str, Variant]
    sets_visited: int
    orderings_visited: int
    unsourceable: list[dict[str, Any]]


# --- the pure search ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Route:
    """One ordering of a set, scored: candidate indices, the arrival offset and
    multiplier per stop, and the totals."""

    order: tuple[int, ...]
    arrivals: tuple[int, ...]  # seconds after planned_at
    factors: tuple[Decimal, ...]
    driving: int
    stop_seconds: int
    meters: int
    estimated: bool

    @property
    def seconds(self) -> int:
        return self.driving + self.stop_seconds


@dataclass(slots=True)
class _SetScore:
    by_time: _Route | None  # least driving + stop time, ties by metres
    by_cost: _Route | None  # least time cost + mileage, ties by time
    orderings: int


@dataclass(slots=True)
class _Best:
    """A set and ordering that leads for one variant kind."""

    chosen: tuple[int, ...]
    route: _Route
    per_stop: dict[int, list[Offer]]
    item_cost: Decimal
    total_cost: Decimal


def _minutes(seconds: int) -> Decimal:
    return (Decimal(seconds) / 60).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)


class _Search:
    """The enumeration over integer indices: candidates 0..n-1, items 0..m-1, and
    places 0..p-1 (origin, destination, then each candidate's place). UUIDs are
    never hashed in the inner loops."""

    def __init__(
        self,
        inputs: PlanInputs,
        items: list[PlanItem],
        candidates: list[Candidate],
        legs: dict[PairKey, Leg],
        is_open: IsOpen,
    ) -> None:
        self.inputs = inputs
        self.items = items
        self.candidates = candidates
        self.is_open = is_open
        self.sets_visited = 0
        self.orderings_visited = 0
        self._scores: dict[tuple[int, ...], _SetScore] = {}
        self._multiplier: dict[int, Decimal] = {}
        self._open: dict[tuple[int, int], bool | None] = {}
        self._mult_seconds: dict[tuple[int, Decimal], int] = {}
        self._time_cost: dict[int, Decimal] = {}
        self.ever_open: set[int] = set()
        self.ever_scored: set[int] = set()

        place_ids = list(
            dict.fromkeys(
                [inputs.origin_place_id, inputs.destination_place_id]
                + [c.place_id for c in candidates]
            )
        )
        index = {pid: i for i, pid in enumerate(place_ids)}
        self.origin = index[inputs.origin_place_id]
        self.destination = index[inputs.destination_place_id]
        self.place_of = [index[c.place_id] for c in candidates]
        n = len(place_ids)
        self.leg_table: list[list[Leg]] = [[_NO_LEG] * n for _ in range(n)]
        for (a, b), leg in legs.items():
            if a in index and b in index:
                self.leg_table[index[a]][index[b]] = leg
        self.overheads = [
            c.overhead_min if c.overhead_min is not None else inputs.stop_overhead_min
            for c in candidates
        ]

    # -- time of day, cached per minute of the trip --

    def multiplier_at(self, offset: int) -> Decimal:
        key = offset // 60
        found = self._multiplier.get(key)
        if found is None:
            at = self.inputs.planned_at + timedelta(seconds=key * 60)
            found = travel_tables.multiplier_at(at, self.inputs.multipliers, self.inputs.timezone)
            self._multiplier[key] = found
        return found

    def multiplied(self, seconds: int, factor: Decimal) -> int:
        key = (seconds, factor)
        found = self._mult_seconds.get(key)
        if found is None:
            found = int((Decimal(seconds) * factor).to_integral_value(ROUND_HALF_UP))
            self._mult_seconds[key] = found
        return found

    def open_at(self, index: int, offset: int) -> bool | None:
        candidate = self.candidates[index]
        if not candidate.hours_known:
            return None
        key = (index, offset // 60)
        if key not in self._open:
            at = self.inputs.planned_at + timedelta(seconds=(offset // 60) * 60)
            self._open[key] = self.is_open(candidate.location_id, at)
        return self._open[key]

    # -- orderings --

    def _route(self, order: tuple[int, ...]) -> _Route | None:
        """Score one ordering; None when it reaches a closed location."""
        at = 0
        meters = 0
        driving = 0
        stop_seconds = 0
        estimated = False
        previous = self.origin
        arrivals: list[int] = []
        factors: list[Decimal] = []
        leg_table = self.leg_table
        for index in order:
            place = self.place_of[index]
            leg = leg_table[previous][place]
            factor = self.multiplier_at(at)
            seconds = self.multiplied(leg.seconds, factor)
            at += seconds
            driving += seconds
            meters += leg.meters
            estimated = estimated or leg.estimate
            self.ever_scored.add(index)
            is_open = self.open_at(index, at)
            if is_open is False:
                return None
            if is_open:
                self.ever_open.add(index)
            arrivals.append(at)
            factors.append(factor)
            overhead = self.overheads[index] * 60
            at += overhead
            stop_seconds += overhead
            previous = place
        if self.destination != previous:
            leg = leg_table[previous][self.destination]
            driving += self.multiplied(leg.seconds, self.multiplier_at(at))
            meters += leg.meters
            estimated = estimated or leg.estimate
        return _Route(
            order, tuple(arrivals), tuple(factors), driving, stop_seconds, meters, estimated
        )

    def time_cost(self, seconds: int) -> Decimal:
        found = self._time_cost.get(seconds)
        if found is None:
            hours = CTX.divide(Decimal(seconds), Decimal(3600))
            found = money(CTX.multiply(self.inputs.value_of_time_per_hour, hours))
            self._time_cost[seconds] = found
        return found

    def mileage_cost(self, meters: int) -> Decimal:
        if self.inputs.mileage_rate is None:
            return Decimal(0)
        miles = CTX.divide(Decimal(meters), METERS_PER_MILE)
        return money(CTX.multiply(self.inputs.mileage_rate, miles))

    def route_cost(self, route: _Route) -> Decimal:
        return self.time_cost(route.seconds) + self.mileage_cost(route.meters)

    def score_set(self, chosen: tuple[int, ...]) -> _SetScore:
        found = self._scores.get(chosen)
        if found is not None:
            return found
        by_time: _Route | None = None
        by_cost: _Route | None = None
        best_cost: tuple[Decimal, int] | None = None
        count = 0
        for order in permutations(chosen):
            count += 1
            route = self._route(order)
            if route is None:
                continue
            if by_time is None or (route.seconds, route.meters) < (by_time.seconds, by_time.meters):
                by_time = route
            cost = (self.route_cost(route), route.seconds)
            if best_cost is None or cost < best_cost:
                by_cost, best_cost = route, cost
        self.orderings_visited += count
        score = _SetScore(by_time, by_cost, count)
        self._scores[chosen] = score
        return score

    # -- items --

    def index_offers(self, book: OfferBook, items: list[PlanItem]) -> list[list[Offer | None]]:
        return [
            [book.offers.get((item.item_id, c.location_id)) for c in self.candidates]
            for item in items
        ]

    def assign(
        self,
        chosen: tuple[int, ...],
        offers: list[list[Offer | None]],
        deferrable: list[bool],
    ) -> tuple[dict[int, list[Offer]], Decimal, tuple[int, int]] | None:
        """Each item to its cheapest stop in the set; None when a stop would buy nothing.

        Returns the offers per stop, the item cost and the coverage as
        (required items, deferrable items)."""
        per_stop: dict[int, list[Offer]] = {index: [] for index in chosen}
        total = Decimal(0)
        required = optional = 0
        for row, is_deferrable in zip(offers, deferrable, strict=True):
            best: Offer | None = None
            best_index = -1
            for index in chosen:
                offer = row[index]
                if offer is not None and (best is None or offer.line_cost < best.line_cost):
                    best, best_index = offer, index
            if best is None:
                continue
            per_stop[best_index].append(best)
            total += best.line_cost
            if is_deferrable:
                optional += 1
            else:
                required += 1
        for offers_here in per_stop.values():
            if not offers_here:
                return None
        return per_stop, money(total), (required, optional)

    def solve(
        self, book: OfferBook, items: list[PlanItem], useful: list[int]
    ) -> dict[str, Variant] | None:
        """The three variants over the useful candidates, or None when nothing can be bought."""
        offers = self.index_offers(book, items)
        deferrable = [i.deferrable for i in items]
        best: dict[str, tuple[tuple[Any, ...], _Best]] = {}
        feasible_coverage: tuple[int, int] | None = None
        for size in range(1, min(self.inputs.max_stops, len(useful)) + 1):
            for chosen in combinations(useful, size):
                self.sets_visited += 1
                assigned = self.assign(chosen, offers, deferrable)
                if assigned is None:
                    continue
                per_stop, item_cost, coverage = assigned
                score = self.score_set(chosen)
                if score.by_time is None or score.by_cost is None:
                    continue  # closed at every arrival in every ordering
                if feasible_coverage is None or coverage > feasible_coverage:
                    feasible_coverage = coverage
                    best = {}
                elif coverage < feasible_coverage:
                    continue
                by_time, by_cost = score.by_time, score.by_cost
                time_total = money(item_cost + self.route_cost(by_time))
                cost_total = money(item_cost + self.route_cost(by_cost))
                for kind, route, total, key in (
                    ("fastest", by_time, time_total, (by_time.seconds, time_total, chosen)),
                    ("cheapest", by_time, time_total, (item_cost, by_time.seconds, chosen)),
                    ("balanced", by_cost, cost_total, (cost_total, by_cost.seconds, chosen)),
                ):
                    if kind not in best or key < best[kind][0]:
                        best[kind] = (key, _Best(chosen, route, per_stop, item_cost, total))
        if len(best) != 3:
            return None
        variants = {kind: self._variant(kind, best[kind][1]) for kind in KINDS}
        balanced = variants["balanced"]
        for variant in variants.values():
            out_of_pocket = variant.item_cost + variant.mileage_cost
            variant.trade_money = out_of_pocket - (balanced.item_cost + balanced.mileage_cost)
            variant.trade_minutes = _minutes(variant.total_seconds - balanced.total_seconds)
        return variants

    def _variant(self, kind: str, lead: _Best) -> Variant:
        route = lead.route
        stops: list[StopPlan] = []
        previous = self.origin
        for index, arrival, factor in zip(route.order, route.arrivals, route.factors, strict=True):
            candidate = self.candidates[index]
            leg = self.leg_table[previous][self.place_of[index]]
            offers = tuple(sorted(lead.per_stop[index], key=lambda o: str(o.item_id)))
            stops.append(
                StopPlan(
                    location_id=candidate.location_id,
                    leg_seconds=leg.seconds,
                    leg_meters=leg.meters,
                    leg_estimate=leg.estimate,
                    multiplier=factor,
                    arrive_at=self.inputs.planned_at + timedelta(seconds=arrival),
                    overhead_min=self.overheads[index],
                    hours_known=candidate.hours_known,
                    items=offers,
                    subtotal=money(sum((o.line_cost for o in offers), Decimal(0))),
                )
            )
            previous = self.place_of[index]
        return Variant(
            kind=kind,
            stops=stops,
            item_cost=lead.item_cost,
            driving_seconds=route.driving,
            stop_seconds=route.stop_seconds,
            mileage_cost=self.mileage_cost(route.meters),
            total_cost=lead.total_cost,
            estimated=route.estimated,
            forced_item_ids=frozenset(),
        )


def _note(kind: str, text: str, **extra: Any) -> dict[str, Any]:
    note: dict[str, Any] = {"kind": kind}
    for key, value in extra.items():
        if value is None:
            continue
        note[key] = str(value) if isinstance(value, uuid.UUID | Decimal) else value
    note["text"] = text
    return note


def plan(
    inputs: PlanInputs,
    items: list[PlanItem],
    candidates: list[Candidate],
    book: OfferBook,
    legs: dict[PairKey, Leg],
    is_open: IsOpen,
) -> PlanResult:
    """The three variants, annotated. Pure: no I/O, no clock.

    Raises ``ApiError(422, "unsourceable_list")`` when no candidate can fill any
    item at all; otherwise an item nobody can fill is annotated, never fatal.
    """
    if not 1 <= inputs.max_stops <= 4:
        raise ValueError("max_stops must be between 1 and 4")
    if len(candidates) > 15:
        raise ValueError("at most 15 candidates are scored")
    search = _Search(inputs, items, candidates, legs, is_open)
    labels = {i.item_id: i.label for i in items}
    by_id = {i.item_id: i for i in items}

    useful = [
        index
        for index, candidate in enumerate(candidates)
        if any((i.item_id, candidate.location_id) in book.offers for i in items)
    ]
    variants = search.solve(book, items, useful) if useful else None
    sets_visited, orderings_visited = search.sets_visited, search.orderings_visited
    if variants is None:
        raise ApiError(
            422,
            "unsourceable_list",
            "No candidate location has a qualifying price for any item on the list.",
            {"unsourceable": _unsourceable(items, candidates, book, set(), labels)},
        )

    # Candidates with known hours that were closed at every projected arrival.
    closed = {
        c.location_id
        for index, c in enumerate(candidates)
        if c.hours_known and index in search.ever_scored and index not in search.ever_open
    }
    unsourceable = _unsourceable(items, candidates, book, closed, labels)

    # Forced stops: a pinned item that exactly one candidate can fill.
    forced: dict[uuid.UUID, uuid.UUID] = {}
    for item in items:
        if not item.pinned:
            continue
        where = [c.location_id for c in candidates if (item.item_id, c.location_id) in book.offers]
        if len(where) == 1:
            forced[item.item_id] = where[0]

    # Stale dependence: does cheapest stay cheapest without stale prices?
    stale_free = trip_offers.without_stale(book)
    without = search.solve(stale_free, items, useful) if stale_free.offers != book.offers else None

    # Defer suggestions: re-solve without the deferrable items alone at a stop.
    resolves: dict[frozenset[uuid.UUID], dict[str, Variant] | None] = {}

    for kind, variant in variants.items():
        variant.forced_item_ids = frozenset(
            o.item_id for s in variant.stops for o in s.items if o.item_id in forced
        )
        notes = variant.annotations
        for item_id, location_id in forced.items():
            notes.append(
                _note(
                    "forced_stop",
                    f"{labels[item_id]} is sold at one candidate only.",
                    item_id=item_id,
                    location_id=location_id,
                )
            )
        for stop in variant.stops:
            if all(by_id[o.item_id].deferrable for o in stop.items):
                dropped = frozenset(o.item_id for o in stop.items)
                if dropped not in resolves:
                    if len(resolves) >= inputs.max_stops:
                        continue
                    remaining = [i for i in items if i.item_id not in dropped]
                    resolves[dropped] = search.solve(book, remaining, useful) if remaining else None
                again = resolves[dropped]
                if again is None:
                    continue
                saving = variant.total_cost - again[kind].total_cost
                minutes = _minutes(variant.total_seconds - again[kind].total_seconds)
                if saving <= 0 and minutes <= 0:
                    continue
                names = ", ".join(labels[i] for i in sorted(dropped, key=lambda i: labels[i]))
                notes.append(
                    _note(
                        "defer_suggestion",
                        f"Defer {names} to skip this stop: saves {saving} and {minutes} minutes.",
                        item_id=next(iter(dropped)) if len(dropped) == 1 else None,
                        location_id=stop.location_id,
                        amount=saving,
                        minutes=minutes,
                    )
                )
        stale_amount = money(
            sum((o.line_cost for s in variant.stops for o in s.items if o.stale), Decimal(0))
        )
        if stale_amount > 0:
            text = f"{stale_amount} of the item cost rests on stale prices."
            if kind == "cheapest":
                same = without is not None and _same_stops(without["cheapest"], variant)
                text += (
                    " Cheapest stays cheapest without them."
                    if same
                    else " Without them, another set of stops would be cheapest."
                )
            notes.append(_note("stale_dependent", text, amount=stale_amount))
        notes.extend(unsourceable)
        bought = {o.item_id for s in variant.stops for o in s.items}
        for item in items:
            if item.item_id in bought or any(
                n["kind"] == "unsourceable" and n.get("item_id") == str(item.item_id)
                for n in unsourceable
            ):
                continue
            notes.append(
                _note(
                    "unsourceable",
                    f"{item.label} needs a stop beyond the {inputs.max_stops} allowed.",
                    item_id=item.item_id,
                    reason="max_stops",
                )
            )
        if variant.estimated:
            n = sum(s.leg_estimate for s in variant.stops)
            notes.append(_note("estimated_legs", f"Drive times are estimates for {n} leg(s)."))
        for stop in variant.stops:
            if not stop.hours_known:
                notes.append(
                    _note(
                        "hours_unknown",
                        "Opening hours are unknown; the stop may be closed.",
                        location_id=stop.location_id,
                    )
                )
        for location_id in sorted(closed, key=str):
            notes.append(
                _note(
                    "closed_excluded",
                    "Closed at every projected arrival; left out.",
                    location_id=location_id,
                )
            )

    return PlanResult(
        variants=variants,
        sets_visited=sets_visited,
        orderings_visited=orderings_visited,
        unsourceable=unsourceable,
    )


def _same_stops(a: Variant, b: Variant) -> bool:
    return {s.location_id for s in a.stops} == {s.location_id for s in b.stops}


def _unsourceable(
    items: list[PlanItem],
    candidates: list[Candidate],
    book: OfferBook,
    closed: set[uuid.UUID],
    labels: dict[uuid.UUID, str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        where = [c.location_id for c in candidates if (item.item_id, c.location_id) in book.offers]
        if any(w not in closed for w in where):
            continue
        if where:
            reason = "closed"
        else:
            reasons = {book.reasons.get((item.item_id, c.location_id)) for c in candidates}
            if "cant_convert" in reasons:
                reason = "cant_convert"
            elif "quality" in reasons:
                reason = "quality"
            else:
                reason = "no_offer"
        why = {
            "closed": "every store that sells it is closed at arrival",
            "cant_convert": "its amount cannot be converted to a product's pack",
            "quality": "no product of that quality has a price",
            "no_offer": "no candidate has a price for it",
        }[reason]
        out.append(
            _note(
                "unsourceable",
                f"{labels[item.item_id]}: {why}.",
                item_id=item.item_id,
                reason=reason,
            )
        )
    return out


# --- I/O: candidates, the plan, the choice ---------------------------------------------------


def _label(item: ShoppingListItem) -> str:
    if item.kind == "product" and item.product is not None:
        return item.product.name
    if item.ingredient is not None:
        return item.ingredient.name
    return str(item.id)


async def candidates(
    db: AsyncSession, home: HomeBase, settings: Settings
) -> tuple[list[Candidate], dict[uuid.UUID, VendorLocation]]:
    """Active vendor locations within ``plan_candidate_radius_km`` of the kitchen,
    nearest first by straight line, at most ``plan_max_candidates``."""
    rows = (
        (
            await db.execute(
                select(VendorLocation)
                .where(VendorLocation.active)
                .options(selectinload(VendorLocation.parent))
                .execution_options(populate_existing=True)
            )
        )
        .unique()
        .scalars()
        .all()
    )
    radius_m = int(settings.plan_candidate_radius_km * 1000)
    near: list[tuple[int, str, VendorLocation]] = []
    for row in rows:
        distance = travel_tables.haversine_m(
            home.place.lat, home.place.lon, row.place.lat, row.place.lon
        )
        if distance <= radius_m:
            near.append((distance, str(row.id), row))
    near.sort()
    chosen = near[: settings.plan_max_candidates]
    out = [
        Candidate(
            location_id=row.id,
            place_id=row.place_id,
            overhead_min=row.stop_overhead_min,
            hours_known=row.opening_hours is not None
            or (row.parent is not None and row.parent.opening_hours is not None),
            distance_m=distance,
        )
        for distance, _, row in chosen
    ]
    return out, {row.id: row for _, _, row in chosen}


def _legs(matrix: dict[PairKey, TravelLeg]) -> dict[PairKey, Leg]:
    return {k: Leg(v.seconds, v.meters, v.source == "estimate") for k, v in matrix.items()}


async def _place(db: AsyncSession, place_id: uuid.UUID) -> Place:
    row = await db.get(Place, place_id)
    if row is None:
        raise ApiError(404, "not_found", "No such place.", {"place_id": str(place_id)})
    return row


async def create_plan(
    db: AsyncSession, user: AppUser, payload: TripCreate, settings: Settings
) -> TripPlan:
    """Plan a trip from the kitchen's open list and store it with its three variants."""
    home = await shopping_lists.get_home_base(db, payload.home_base_id)
    lst = await shopping_lists.open_list(db, payload.home_base_id)
    rows = await shopping_lists.unchecked_items(db, lst.id)
    if not rows:
        raise ApiError(422, "empty_list", "The list has no unchecked items to plan for.")
    origin = payload.origin_place_id or home.place_id
    destination = payload.destination_place_id or origin
    await _place(db, origin)
    await _place(db, destination)

    found, locations = await candidates(db, home, settings)
    if not found:
        raise ApiError(
            422,
            "no_candidates",
            f"No active vendor location within {settings.plan_candidate_radius_km} km "
            "of the kitchen.",
        )
    book = await trip_offers.offers(db, rows, [c.location_id for c in found], settings)
    matrix = await travel.legs_for(
        db, [origin, destination, *(c.place_id for c in found)], settings
    )
    planned_at = payload.planned_at
    if planned_at.tzinfo is None:
        planned_at = planned_at.replace(tzinfo=UTC)
    inputs = PlanInputs(
        origin_place_id=origin,
        destination_place_id=destination,
        planned_at=planned_at.astimezone(UTC),
        value_of_time_per_hour=(
            payload.value_of_time_per_hour
            if payload.value_of_time_per_hour is not None
            else settings.plan_value_of_time_per_hour
        ),
        max_stops=payload.max_stops,
        mileage_rate=payload.mileage_rate,
        stop_overhead_min=(
            payload.stop_overhead_min
            if payload.stop_overhead_min is not None
            else DEFAULT_STOP_OVERHEAD_MIN
        ),
        timezone=settings.household_timezone,
        multipliers=travel_tables.multipliers(settings.plan_traffic_multipliers),
    )
    items = [
        PlanItem(
            item_id=i.id,
            label=_label(i),
            deferrable=i.deferrable,
            pinned=i.kind == "product" or i.min_quality is not None,
        )
        for i in rows
    ]

    def is_open(location_id: uuid.UUID, at: datetime) -> bool | None:
        return is_open_at(locations[location_id], at)

    result = plan(inputs, items, found, book, _legs(matrix), is_open)

    row = TripPlan(
        id=new_id(),
        list_id=lst.id,
        home_base_id=home.id,
        origin_place_id=origin,
        destination_place_id=destination,
        planned_at=inputs.planned_at,
        value_of_time_per_hour=inputs.value_of_time_per_hour,
        max_stops=inputs.max_stops,
        mileage_rate=inputs.mileage_rate,
        stop_overhead_min=inputs.stop_overhead_min,
        status="planned",
        created_by=user.id,
    )
    db.add(row)
    for kind in KINDS:
        variant = result.variants[kind]
        variant_row = TripPlanVariant(
            id=new_id(),
            plan_id=row.id,
            kind=kind,
            item_cost=variant.item_cost,
            driving_seconds=variant.driving_seconds,
            stop_seconds=variant.stop_seconds,
            mileage_cost=variant.mileage_cost,
            total_cost=variant.total_cost,
            estimated=variant.estimated,
            annotations=variant.annotations,
        )
        db.add(variant_row)
        for seq, stop in enumerate(variant.stops, start=1):
            stop_row = TripPlanStop(
                id=new_id(),
                variant_id=variant_row.id,
                seq=seq,
                vendor_location_id=stop.location_id,
                leg_seconds=stop.leg_seconds,
                leg_meters=stop.leg_meters,
                leg_source="estimate" if stop.leg_estimate else "osrm",
                multiplier=stop.multiplier,
                arrive_at=stop.arrive_at,
                overhead_min=stop.overhead_min,
                subtotal=stop.subtotal,
                hours_known=stop.hours_known,
            )
            db.add(stop_row)
            for offer in stop.items:
                db.add(
                    TripPlanStopItem(
                        id=new_id(),
                        stop_id=stop_row.id,
                        list_item_id=offer.item_id,
                        product_id=offer.product_id,
                        packs=offer.packs,
                        pack_price=offer.pack_price,
                        line_cost=offer.line_cost,
                        observation_id=offer.observation_id,
                        price_stale=offer.stale,
                        forced=offer.item_id in variant.forced_item_ids,
                    )
                )
    await db.commit()
    return await get_plan(db, row.id)


def _plan_query():
    return (
        select(TripPlan)
        .options(
            selectinload(TripPlan.variants)
            .selectinload(TripPlanVariant.stops)
            .selectinload(TripPlanStop.items)
        )
        .execution_options(populate_existing=True)
    )


async def get_plan(db: AsyncSession, plan_id: uuid.UUID) -> TripPlan:
    row = (
        (await db.execute(_plan_query().where(TripPlan.id == plan_id)))
        .unique()
        .scalar_one_or_none()
    )
    if row is None:
        raise ApiError(404, "not_found", "No such trip plan.")
    return row


async def list_plans(db: AsyncSession, home_base_id: uuid.UUID) -> list[TripPlan]:
    await shopping_lists.get_home_base(db, home_base_id)
    stmt = (
        _plan_query()
        .where(TripPlan.home_base_id == home_base_id)
        .order_by(TripPlan.created_at.desc(), TripPlan.id.desc())
    )
    return list((await db.execute(stmt)).unique().scalars())


async def choose(db: AsyncSession, plan_row: TripPlan, variant_id: uuid.UUID) -> TripPlan:
    """Freeze one variant as the prediction; a second choice is a 409."""
    if plan_row.chosen_variant_id is not None:
        raise ApiError(409, "already_chosen", "A variant was already chosen for this plan.")
    if plan_row.status != "planned":
        raise ApiError(409, "plan_ended", f"This plan is {plan_row.status}.")
    if variant_id not in {v.id for v in plan_row.variants}:
        raise ApiError(404, "not_found", "No such variant on this plan.")
    plan_row.chosen_variant_id = variant_id
    plan_row.chosen_at = datetime.now(UTC)
    await db.commit()
    return await get_plan(db, plan_row.id)


async def abandon(db: AsyncSession, plan_row: TripPlan) -> TripPlan:
    if plan_row.status not in ("planned", "underway"):
        raise ApiError(409, "plan_ended", f"This plan is {plan_row.status}.")
    plan_row.status = "abandoned"
    plan_row.finished_at = datetime.now(UTC)
    await db.commit()
    return await get_plan(db, plan_row.id)


async def delete_plan(db: AsyncSession, plan_row: TripPlan) -> None:
    """An unchosen plan is derived and may go; a chosen one is the record of a trip."""
    if plan_row.chosen_variant_id is not None:
        raise ApiError(409, "plan_chosen", "A chosen plan is kept; abandon it instead.")
    await db.delete(plan_row)
    await db.commit()


# --- output ----------------------------------------------------------------------------------


def _trade(variant: TripPlanVariant, balanced: TripPlanVariant) -> Trade:
    out_of_pocket = variant.item_cost + variant.mileage_cost
    seconds = (variant.driving_seconds + variant.stop_seconds) - (
        balanced.driving_seconds + balanced.stop_seconds
    )
    return Trade(
        money=out_of_pocket - (balanced.item_cost + balanced.mileage_cost),
        minutes=_minutes(seconds),
    )


def _variant_summary(variant: TripPlanVariant, balanced: TripPlanVariant) -> dict[str, Any]:
    return {
        "id": variant.id,
        "kind": variant.kind,
        "item_cost": variant.item_cost,
        "driving_seconds": variant.driving_seconds,
        "stop_seconds": variant.stop_seconds,
        "mileage_cost": variant.mileage_cost,
        "total_cost": variant.total_cost,
        "estimated": variant.estimated,
        "trade": _trade(variant, balanced),
        "stop_count": len(variant.stops),
    }


def _stop_out(stop: TripPlanStop) -> StopOut:
    return StopOut(
        id=stop.id,
        seq=stop.seq,
        vendor_location_id=stop.vendor_location_id,
        vendor_location_name=stop.location.name,
        leg_seconds=stop.leg_seconds,
        leg_meters=stop.leg_meters,
        leg_source=stop.leg_source,  # type: ignore[arg-type]
        multiplier=stop.multiplier,
        arrive_at=stop.arrive_at,
        overhead_min=stop.overhead_min,
        subtotal=stop.subtotal,
        hours_known=stop.hours_known,
        done_at=stop.done_at,
        purchase_id=stop.purchase_id,
        items=[
            StopItemOut(
                id=i.id,
                list_item_id=i.list_item_id,
                product_id=i.product_id,
                product_name=i.product.name,
                packs=i.packs,
                pack_price=i.pack_price,
                line_cost=i.line_cost,
                observation_id=i.observation_id,
                price_stale=i.price_stale,
                forced=i.forced,
                actual_price=i.actual_price,
                actual_qty=i.actual_qty,
                actual_unit=i.actual_unit,
                got_here=i.got_here,
            )
            for i in stop.items
        ],
    )


def _ordered(plan_row: TripPlan) -> tuple[list[TripPlanVariant], TripPlanVariant]:
    by_kind = {v.kind: v for v in plan_row.variants}
    return [by_kind[k] for k in KINDS if k in by_kind], by_kind["balanced"]


def _base(plan_row: TripPlan) -> dict[str, Any]:
    return {
        "id": plan_row.id,
        "list_id": plan_row.list_id,
        "home_base_id": plan_row.home_base_id,
        "origin_place_id": plan_row.origin_place_id,
        "destination_place_id": plan_row.destination_place_id,
        "planned_at": plan_row.planned_at,
        "value_of_time_per_hour": plan_row.value_of_time_per_hour,
        "max_stops": plan_row.max_stops,
        "mileage_rate": plan_row.mileage_rate,
        "stop_overhead_min": plan_row.stop_overhead_min,
        "status": plan_row.status,
        "chosen_variant_id": plan_row.chosen_variant_id,
        "created_by": plan_row.created_by,
        "created_at": plan_row.created_at,
        "chosen_at": plan_row.chosen_at,
        "finished_at": plan_row.finished_at,
    }


def trip_summary(plan_row: TripPlan) -> TripSummary:
    variants, balanced = _ordered(plan_row)
    return TripSummary(
        **_base(plan_row),
        variants=[VariantSummary(**_variant_summary(v, balanced)) for v in variants],
    )


def trip_out(plan_row: TripPlan) -> TripOut:
    variants, balanced = _ordered(plan_row)
    return TripOut(
        **_base(plan_row),
        variants=[
            VariantOut(
                **_variant_summary(v, balanced),
                annotations=[Annotation.model_validate(a) for a in v.annotations],
                stops=[_stop_out(s) for s in v.stops],
            )
            for v in variants
        ],
    )
