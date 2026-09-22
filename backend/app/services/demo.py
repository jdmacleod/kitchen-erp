"""A synthetic household, for screenshots, demos, and a first look at the UI.

Everything here is invented. The vendors do not exist, the products are made up,
the people are `example.com` addresses, and every coordinate lies in the synthetic
grid `SECURITY.md` reserves for fixtures: the open Pacific west of the Californian
coast, latitude 33 to 34 and longitude -121 to -120. That box is inside the tile
extract's bounding box, so the map renders, and it contains no home and no shop.

This exists because a public repository needs screenshots and a stranger needs
something to look at, and the only other source of either is the household's own
database. Seeding a throwaway database from this module is the supported way to
produce both. Nothing in here reads `data/`, and no test depends on it.

The generator is deterministic — one fixed seed, dates counted back from a fixed
day — so that re-running it produces the same database and a screenshot diff shows
a real UI change rather than fresh random numbers.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.identity import AppUser
from app.models.purchases import Purchase
from app.schemas.catalog import IngredientCreate, ProductCreate
from app.schemas.purchases import LineIn, ManualPurchaseIn
from app.services import catalog, geo, identity, pricebook, purchases

# One fixed seed and one fixed end date: the demo database is a pure function of
# this module, so screenshots taken from it differ only when the UI differs.
SEED = 20260922
AS_OF = datetime(2026, 9, 15, 17, 30, tzinfo=UTC)
WEEKS = 34

DEMO_ADMIN_EMAIL = "demo@example.com"
DEMO_ADMIN_PASSWORD = "demo-only-not-a-real-password"  # pii-scan: allow demo placeholder
DEMO_MEMBER_EMAIL = "second.cook@example.com"

# The synthetic grid from SECURITY.md. Nothing real is inside it.
HOME_BASES = [
    ("Harbour Flat", Decimal("33.2410"), Decimal("-120.7820")),
    ("Ridge Cottage", Decimal("33.8630"), Decimal("-120.2240")),
]

# (vendor name, kind, price scope, [(location name, lat, lon, address, hours)])
VENDORS: list[tuple[str, str, str, list[tuple[str, str, str, str, str | None]]]] = [
    (
        "Northgate Grocers",
        "chain",
        "chain",
        [
            (
                "Harbour",
                "33.2486",
                "-120.7715",
                "14 Anchor Parade",  # pii-scan: allow invented address
                "Mo-Su 07:00-22:00",
            ),
            (
                "Ridge",
                "33.8574",
                "-120.2361",
                "2 Lantern Way",  # pii-scan: allow invented address
                "Mo-Su 08:00-21:00",
            ),
        ],
    ),
    (
        "Tideline Market",
        "market",
        "location",
        [
            (
                "Saturday market",
                "33.2702",
                "-120.8014",
                "Quayside car park",
                "Sa 08:00-13:00",
            ),
        ],
    ),
    (
        "Pellar & Sons",
        "independent",
        "location",
        [
            (
                "Pellar & Sons",
                "33.8129",
                "-120.3308",
                "9 Kiln Street",  # pii-scan: allow invented address
                "Tu-Sa 09:00-18:00",
            ),
        ],
    ),
    (
        "Salt Flat Provisions",
        "independent",
        "location",
        [
            (
                "Salt Flat Provisions",
                "33.4915",
                "-120.6002",
                "Unit 3, Saltworks Yard",
                "We-Su 10:00-17:00",
            ),
        ],
    ),
]

# A stall inside Tideline Market, so the parent/stall relationship has something
# to show. (name, lat, lon)
STALL = ("Bramble Row Produce", "33.2704", "-120.8011")

# (ingredient, category, canonical unit, density g/ml or None, perishability,
#  [(brand, product name, pack qty, pack unit, typical unit price)])
Sku = tuple[str | None, str, str, str, str]
CATALOG: list[tuple[str, str, str, str | None, str, list[Sku]]] = [
    (
        "Bread flour",
        "Dry goods",
        "g",
        None,
        "shelf_stable",
        [
            ("Kestrel Mills", "Strong white bread flour", "1.5", "kg", "3.40"),
            ("Kestrel Mills", "Stoneground wholemeal", "1", "kg", "2.85"),
        ],
    ),
    (
        "Olive oil",
        "Oils",
        "ml",
        "0.91",
        "shelf_stable",
        [
            ("Verano", "Extra virgin olive oil", "750", "ml", "11.20"),
            (None, "House olive oil", "1", "l", "8.60"),
        ],
    ),
    (
        "Whole milk",
        "Dairy",
        "ml",
        "1.03",
        "refrigerated",
        [
            ("Marsh Dairy", "Whole milk", "2", "l", "3.10"),
        ],
    ),
    (
        "Unsalted butter",
        "Dairy",
        "g",
        None,
        "refrigerated",
        [
            ("Marsh Dairy", "Unsalted butter", "250", "g", "4.05"),
        ],
    ),
    (
        "Eggs",
        "Dairy",
        "each",
        None,
        "refrigerated",
        [
            (None, "Free-range eggs, large", "12", "each", "5.45"),
        ],
    ),
    (
        "Carrots",
        "Produce",
        "g",
        None,
        "fresh",
        [
            (None, "Carrots, loose", "1", "kg", "1.95"),
        ],
    ),
    (
        "Yellow onions",
        "Produce",
        "g",
        None,
        "fresh",
        [
            (None, "Yellow onions", "2", "kg", "2.60"),
        ],
    ),
    (
        "Tomatoes",
        "Produce",
        "g",
        None,
        "fresh",
        [
            (None, "Vine tomatoes", "500", "g", "2.75"),
        ],
    ),
    (
        "Dried chickpeas",
        "Dry goods",
        "g",
        None,
        "shelf_stable",
        [
            ("Harrow Pantry", "Dried chickpeas", "500", "g", "2.15"),
        ],
    ),
    (
        "Short-grain rice",
        "Dry goods",
        "g",
        None,
        "shelf_stable",
        [
            ("Harrow Pantry", "Short-grain rice", "1", "kg", "3.95"),
        ],
    ),
    (
        "Chicken thighs",
        "Meat",
        "g",
        None,
        "fresh",
        [
            (None, "Chicken thighs, bone-in", "1", "kg", "7.40"),
        ],
    ),
    (
        "Coffee beans",
        "Beverages",
        "g",
        None,
        "shelf_stable",
        [
            ("Quill Roasters", "Filter blend, whole bean", "340", "g", "13.50"),
        ],
    ),
    (
        "Parmesan",
        "Dairy",
        "g",
        None,
        "refrigerated",
        [
            (None, "Parmesan wedge", "200", "g", "8.90"),
        ],
    ),
    (
        "Sea salt",
        "Pantry",
        "g",
        None,
        "shelf_stable",
        [
            ("Salt Flat", "Flaked sea salt", "250", "g", "4.60"),
        ],
    ),
]


async def _already_seeded(db: AsyncSession) -> bool:
    n = await db.scalar(select(func.count()).select_from(Purchase))
    return bool(n)


async def seed_demo(db: AsyncSession, *, force: bool = False) -> dict[str, int]:
    """Fill an empty database with the synthetic household. Returns row counts.

    Refuses a database that already holds purchases unless `force` is set: the
    point of this data is that it is the only thing in there.
    """
    if not force and await _already_seeded(db):
        raise RuntimeError(
            "this database already holds purchases; seed a throwaway database "
            "instead, or pass --force if you are certain it is a demo"
        )

    rng = random.Random(SEED)
    counts = {
        "users": 0,
        "home_bases": 0,
        "vendors": 0,
        "locations": 0,
        "ingredients": 0,
        "products": 0,
        "purchases": 0,
        "observations": 0,
    }

    admin = await _ensure_user(db, DEMO_ADMIN_EMAIL, "Demo Household", "admin")
    member = await _ensure_user(db, DEMO_MEMBER_EMAIL, "Second Cook", "member")
    counts["users"] = 2

    home_ids = []
    for name, lat, lon in HOME_BASES:
        hb = await geo.create_home_base(db, name=name, lat=lat, lon=lon, label=name)
        home_ids.append(hb.id)
    counts["home_bases"] = len(home_ids)

    locations = []
    for vendor_name, kind, scope, sites in VENDORS:
        vendor = await geo.create_vendor(db, name=vendor_name, kind=kind, price_scope=scope)
        counts["vendors"] += 1
        for site_name, lat, lon, address, hours in sites:
            loc = await geo.create_location(
                db,
                {
                    "vendor_id": vendor.id,
                    "name": site_name,
                    "lat": Decimal(lat),
                    "lon": Decimal(lon),
                    "address": address,
                    "opening_hours": hours,
                    "receipt_identifiers": [vendor_name.upper()],
                },
            )
            locations.append(loc)
            counts["locations"] += 1
            if vendor_name == "Tideline Market":
                stall_name, slat, slon = STALL
                stall = await geo.create_location(
                    db,
                    {
                        "vendor_id": vendor.id,
                        "name": stall_name,
                        "lat": Decimal(slat),
                        "lon": Decimal(slon),
                        "parent_location_id": loc.id,
                    },
                )
                locations.append(stall)
                counts["locations"] += 1

    products: list[tuple[Any, Decimal]] = []
    for name, category, canonical, density, perish, skus in CATALOG:
        ingredient = await catalog.create_ingredient(
            db,
            IngredientCreate(
                name=name,
                category=category,
                canonical_unit=canonical,
                perishability=perish,
                **(
                    {"density_g_per_ml": Decimal(density), "density_source": "manual"}
                    if density
                    else {}
                ),
            ),
        )
        counts["ingredients"] += 1
        for brand, sku_name, pack_qty, pack_unit, price in skus:
            product = await catalog.create_product(
                db,
                ProductCreate(
                    ingredient_id=ingredient.id,
                    brand=brand,
                    name=sku_name,
                    pack_qty=Decimal(pack_qty),
                    pack_unit=pack_unit,
                ),
            )
            products.append((product, Decimal(price)))
            counts["products"] += 1

    # A shop every few days across the period, so the price history has shape and
    # the same product is seen at more than one vendor. Times are generated first
    # and sorted: the purchase list paginates on a time-ordered id, so insertion
    # order is display order, and dates must not wander backwards inside it.
    moments: list[datetime] = []
    for week in range(WEEKS):
        for _ in range(rng.choice((1, 1, 2))):
            moments.append(
                AS_OF
                - timedelta(weeks=WEEKS - week)
                + timedelta(days=rng.randrange(0, 5), hours=rng.randrange(0, 9))
            )
    moments.sort()

    first = moments[0]
    for index, when in enumerate(moments):
        elapsed_weeks = (when - first).days // 7
        location = rng.choice(locations)
        basket = rng.sample(products, rng.randrange(3, 7))
        lines = []
        for product, base in basket:
            # A gentle drift plus the odd promotion, so the charts are not flat.
            drift = Decimal(1) + Decimal(elapsed_weeks) / Decimal(400)
            jitter = Decimal(rng.randrange(-6, 7)) / Decimal(100)
            price = (base * drift * (Decimal(1) + jitter)).quantize(Decimal("0.01"))
            qty = Decimal(rng.choice(("1", "1", "1", "2", "2", "3")))
            lines.append(LineIn(product_id=product.id, qty=qty, unit="each", unit_price=price))
        user = admin if index % 3 else member
        await purchases.create_manual(
            db,
            user,
            ManualPurchaseIn(vendor_location_id=location.id, purchased_at=when, lines=lines),
        )
        counts["purchases"] += 1
        counts["observations"] += len(lines)

    await pricebook.recompute_all(db)
    return counts


async def _ensure_user(db: AsyncSession, email: str, display_name: str, role: str) -> AppUser:
    existing = await db.scalar(select(AppUser).where(AppUser.email == email))
    if existing is not None:
        return existing
    return await identity.create_user(
        db,
        email=email,
        display_name=display_name,
        password=DEMO_ADMIN_PASSWORD,
        role=role,
    )
