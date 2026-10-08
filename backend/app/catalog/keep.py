"""How long an ingredient keeps, unopened, in each place it can be stored (2Q).

Times are whole days from the USDA storage charts (FSIS "Food Product Dating",
the FoodSafety.gov cold storage chart, the food bank guides that reprint them)
and UC ANR 8406 for nuts, taken from the low end of each range so a best-by date
errs early. ``None`` means the charts give no time for that place: raw chicken
does not keep at room temperature. Freezer times are for quality; frozen food
kept frozen stays safe.

A standard-list entry carries its own times. An ingredient typed in by name
takes the defaults for its perishability here. Pure: no I/O.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from app.catalog import perishability
from app.catalog.perishability import Perishability

Place = Literal["room", "fridge", "freezer"]
PLACES: tuple[Place, ...] = get_args(Place)

Days = StrictInt


class KeepTimes(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    room: Days | None = Field(ge=0)
    fridge: Days | None = Field(ge=0)
    freezer: Days | None = Field(ge=0)

    def days(self, place: Place) -> int | None:
        return getattr(self, place)


# Where an ingredient of each perishability is kept, unless a person says otherwise.
STORED_IN: dict[Perishability, Place] = {
    "shelf_stable": "room",
    "shelf_months": "room",
    "refrigerated": "fridge",
    "fresh": "fridge",
    "frozen": "freezer",
}

# Keep times for a name typed in, by its perishability: the shortest the charts
# give for foods of that kind.
BY_PERISHABILITY: dict[Perishability, KeepTimes] = {
    "shelf_stable": KeepTimes(room=365, fridge=None, freezer=None),
    "shelf_months": KeepTimes(room=60, fridge=None, freezer=None),
    "refrigerated": KeepTimes(room=None, fridge=14, freezer=None),
    "fresh": KeepTimes(room=None, fridge=3, freezer=None),
    "frozen": KeepTimes(room=None, fridge=None, freezer=240),
}


def stored_in(value: str) -> Place:
    """Where an ingredient of perishability ``value`` is kept."""
    return STORED_IN.get(value, "room")  # type: ignore[call-overload]


def default_for(category: str | None) -> KeepTimes:
    """The keep times an ingredient in ``category`` (free text) starts with."""
    return BY_PERISHABILITY[perishability.default_for(category)]


def best_by(purchased_on: date, days: int | None) -> date | None:
    """The inferred best-by date: the purchase's local date plus the keep time."""
    return None if days is None else purchased_on + timedelta(days=days)
