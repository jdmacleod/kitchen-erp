"""Read models for the price book (Phase 2E): history, offers, comparison, panels."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings


def stale_thresholds() -> dict[str, int]:
    s = get_settings()
    return {
        "fresh": s.stale_days_fresh,
        "refrigerated": s.stale_days_refrigerated,
        "shelf_stable": s.stale_days_shelf_stable,
    }


_STALE_SQL = """
    CASE i.perishability
        WHEN 'fresh' THEN CAST(:stale_fresh AS integer)
        WHEN 'refrigerated' THEN CAST(:stale_refrigerated AS integer)
        ELSE CAST(:stale_shelf_stable AS integer)
    END
"""


def _params(**extra: Any) -> dict[str, Any]:
    t = stale_thresholds()
    return {
        "stale_fresh": t["fresh"],
        "stale_refrigerated": t["refrigerated"],
        "stale_shelf_stable": t["shelf_stable"],
        "now": datetime.now(UTC),
        **extra,
    }


_CELL_KEYS = (
    "product_id",
    "product_name",
    "brand",
    "quality_rating",
    "location_id",
    "location_name",
    "observation_id",
    "observed_at",
    "is_promo",
    "norm_unit_price",
    "norm_unit",
    "stale",
)


def _row(r) -> dict[str, Any]:
    return dict(r)


async def product_history(db: AsyncSession, product_id: uuid.UUID) -> dict[str, Any]:
    """Every current observation of a product, plus the latest at each location with its age."""
    points = await db.execute(
        text(
            """
            SELECT pc.observation_id, pc.observed_at, pc.price, pc.qty, pc.unit, pc.is_promo,
                   pc.source, pc.norm_unit_price, pc.norm_unit, pc.norm_status,
                   vl.id AS location_id, vl.name AS location_name,
                   v.id AS vendor_id, v.name AS vendor_name, v.price_scope,
                   CASE WHEN v.price_scope = 'chain' THEN v.id::text ELSE vl.id::text END AS series
            FROM price_current pc
            JOIN vendor_location vl ON vl.id = pc.vendor_location_id
            JOIN vendor v ON v.id = vl.vendor_id
            WHERE pc.product_id = CAST(:pid AS uuid)
            ORDER BY pc.observed_at, pc.observation_id
            """
        ),
        {"pid": product_id},
    )
    latest = await db.execute(
        text(
            f"""
            SELECT ol.applies_to_location_id AS location_id, vl.name AS location_name,
                   v.id AS vendor_id, v.name AS vendor_name, v.price_scope,
                   ol.observation_id, ol.observed_at, ol.price, ol.qty, ol.unit, ol.is_promo,
                   ol.norm_unit_price, ol.norm_unit, ol.norm_status,
                   EXTRACT(EPOCH FROM (CAST(:now AS timestamptz) - ol.observed_at)) / 86400
                       AS age_days,
                   (CAST(:now AS timestamptz) - ol.observed_at)
                       > make_interval(days => {_STALE_SQL}) AS stale
            FROM offer_latest ol
            JOIN vendor_location vl ON vl.id = ol.applies_to_location_id
            JOIN vendor v ON v.id = vl.vendor_id
            JOIN product p ON p.id = ol.product_id
            JOIN ingredient i ON i.id = p.ingredient_id
            WHERE ol.product_id = CAST(:pid AS uuid)
            ORDER BY ol.observed_at DESC
            """
        ),
        _params(pid=product_id),
    )
    return {
        "points": [_row(r) for r in points.mappings()],
        "latest": [_row(r) for r in latest.mappings()],
    }


# One point per day (the household's day), the cheapest that day with its product
# and vendor, so a year of frequent prices is at most 366 points. The range is
# taken over every price in the window, not just the daily points.
_INGREDIENT_HISTORY_SQL = text(
    """
    WITH window_prices AS (
        SELECT pc.observation_id, pc.observed_at, pc.norm_unit_price, pc.norm_unit, pc.is_promo,
               pc.source, p.id AS product_id, p.name AS product_name,
               vl.id AS location_id, v.id AS vendor_id, v.name AS vendor_name,
               (pc.observed_at AT TIME ZONE CAST(:tz AS text))::date AS day
        FROM price_current pc
        JOIN product p ON p.id = pc.product_id AND p.active
        JOIN vendor_location vl ON vl.id = pc.vendor_location_id
        JOIN vendor v ON v.id = vl.vendor_id
        LEFT JOIN purchase_line pl ON pl.id = pc.purchase_line_id
        LEFT JOIN purchase pu ON pu.id = pl.purchase_id
        WHERE p.ingredient_id = CAST(:iid AS uuid)
          AND pc.observed_at >= CAST(:since AS timestamptz)
          AND pc.norm_unit_price IS NOT NULL
          AND (pc.purchase_line_id IS NULL OR pu.status = 'committed')
    ),
    price_range AS (
        SELECT min(norm_unit_price) AS low, max(norm_unit_price) AS high FROM window_prices
    )
    SELECT DISTINCT ON (w.day) w.*, r.low, r.high
    FROM window_prices w CROSS JOIN price_range r
    ORDER BY w.day, w.norm_unit_price, w.observed_at, w.observation_id
    """
)

_RANGE_KEYS = ("day", "low", "high")


async def ingredient_history(
    db: AsyncSession, ingredient_id: uuid.UUID, days: int
) -> dict[str, Any]:
    """Normalized unit prices of an ingredient's active products over the last ``days``.

    Voided observations are already gone from ``price_current``. A price that
    can't be compared has no normalized value and is left out (G8). Only shelf
    prices and committed purchases count: a draft or reopened purchase is not
    settled yet, and its prices may still change.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    rows = list(
        (
            await db.execute(
                _INGREDIENT_HISTORY_SQL,
                {"iid": ingredient_id, "since": since, "tz": get_settings().household_timezone},
            )
        ).mappings()
    )
    points = [{k: v for k, v in r.items() if k not in _RANGE_KEYS} for r in rows]
    return {
        "days": days,
        "points": points,
        "low": rows[0]["low"] if rows else None,
        "high": rows[0]["high"] if rows else None,
    }


async def ingredient_offers(
    db: AsyncSession,
    ingredient_id: uuid.UUID,
    *,
    min_quality: int | None = None,
    exclude_stale: bool = False,
    exclude_promo: bool = False,
) -> list[dict[str, Any]]:
    view = "offer_latest_regular" if exclude_promo else "offer_latest"
    rows = await db.execute(
        text(
            f"""
            SELECT ol.product_id, p.name AS product_name, p.brand, p.quality_rating,
                   p.pack_qty, p.pack_unit,
                   ol.applies_to_location_id AS location_id, vl.name AS location_name,
                   v.id AS vendor_id, v.name AS vendor_name, v.price_scope,
                   ol.observation_id, ol.observed_at, ol.price, ol.qty, ol.unit, ol.is_promo,
                   ol.norm_unit_price, ol.norm_unit, ol.norm_status,
                   EXTRACT(EPOCH FROM (CAST(:now AS timestamptz) - ol.observed_at)) / 86400
                       AS age_days,
                   (CAST(:now AS timestamptz) - ol.observed_at)
                       > make_interval(days => {_STALE_SQL}) AS stale
            FROM {view} ol
            JOIN product p ON p.id = ol.product_id AND p.active
            JOIN ingredient i ON i.id = p.ingredient_id
            JOIN vendor_location vl ON vl.id = ol.applies_to_location_id
            JOIN vendor v ON v.id = vl.vendor_id
            WHERE p.ingredient_id = CAST(:iid AS uuid)
              AND (CAST(:min_quality AS integer) IS NULL OR coalesce(p.quality_rating, 0) >=
                  CAST(:min_quality AS integer))
            ORDER BY (ol.norm_unit_price IS NULL), ol.norm_unit_price, ol.observed_at DESC
            """
        ),
        _params(iid=ingredient_id, min_quality=min_quality),
    )
    out = [_row(r) for r in rows.mappings()]
    if exclude_stale:
        out = [r for r in out if not r["stale"]]
    return out


async def compare(
    db: AsyncSession,
    ingredient_ids: list[uuid.UUID],
    *,
    min_quality: int | None = None,
    exclude_stale: bool = False,
    exclude_promo: bool = False,
) -> dict[str, Any]:
    """Vendors as columns, ingredients as rows, best qualifying normalized price per cell."""
    if not ingredient_ids:
        return {"vendors": [], "rows": []}
    view = "offer_latest_regular" if exclude_promo else "offer_latest"
    rows = await db.execute(
        text(
            f"""
            WITH offers AS (
                SELECT p.ingredient_id, i.name AS ingredient_name, i.canonical_unit,
                       v.id AS vendor_id, v.name AS vendor_name,
                       ol.product_id, p.name AS product_name, p.brand, p.quality_rating,
                       ol.applies_to_location_id AS location_id, vl.name AS location_name,
                       ol.observation_id, ol.observed_at, ol.is_promo,
                       ol.norm_unit_price, ol.norm_unit,
                       (CAST(:now AS timestamptz) - ol.observed_at)
                           > make_interval(days => {_STALE_SQL}) AS stale
                FROM {view} ol
                JOIN product p ON p.id = ol.product_id AND p.active
                JOIN ingredient i ON i.id = p.ingredient_id
                JOIN vendor_location vl ON vl.id = ol.applies_to_location_id
                JOIN vendor v ON v.id = vl.vendor_id
                WHERE p.ingredient_id = ANY(CAST(:ids AS uuid[])) AND ol.norm_unit_price IS NOT NULL
                  AND (CAST(:min_quality AS integer) IS NULL OR coalesce(p.quality_rating, 0) >=
                      CAST(:min_quality AS integer))
                  AND (NOT CAST(:exclude_stale AS boolean) OR NOT (
                        (CAST(:now AS timestamptz) - ol.observed_at)
                            > make_interval(days => {_STALE_SQL})))
            )
            SELECT DISTINCT ON (ingredient_id, vendor_id) *
            FROM offers
            ORDER BY ingredient_id, vendor_id, norm_unit_price, observed_at DESC
            """
        ),
        _params(ids=list(ingredient_ids), min_quality=min_quality, exclude_stale=exclude_stale),
    )
    cells = [_row(r) for r in rows.mappings()]
    vendors: dict[uuid.UUID, str] = {}
    for c in cells:
        vendors.setdefault(c["vendor_id"], c["vendor_name"])
    names = await db.execute(
        text(
            "SELECT id, name, canonical_unit FROM ingredient WHERE id = ANY(CAST(:ids AS uuid[]))"
        ),
        {"ids": list(ingredient_ids)},
    )
    ingredient_rows = {r["id"]: dict(r) for r in names.mappings()}
    out_rows = []
    for iid in ingredient_ids:
        info = ingredient_rows.get(iid)
        if info is None:
            continue
        mine = [c for c in cells if c["ingredient_id"] == iid]
        best = min((c["norm_unit_price"] for c in mine), default=None)
        out_rows.append(
            {
                "ingredient_id": iid,
                "ingredient_name": info["name"],
                "canonical_unit": info["canonical_unit"],
                "cells": {
                    str(c["vendor_id"]): {
                        **{k: c[k] for k in _CELL_KEYS},
                        "cheapest": best is not None and c["norm_unit_price"] == best,
                    }
                    for c in mine
                },
            }
        )
    return {
        "vendors": [{"id": vid, "name": name} for vid, name in vendors.items()],
        "rows": out_rows,
    }


async def location_panel(
    db: AsyncSession, location_id: uuid.UUID, days: int = 30
) -> dict[str, Any]:
    since = datetime.now(UTC) - timedelta(days=days)
    visit = await db.execute(
        text(
            """
            SELECT max(purchased_at) AS last_visit,
                   coalesce(
                       sum(total) FILTER (WHERE purchased_at >= CAST(:since AS timestamptz)), 0
                   ) AS spend,
                   count(*) FILTER (WHERE purchased_at >= CAST(:since AS timestamptz)) AS visits
            FROM purchase WHERE vendor_location_id = CAST(:lid AS uuid) AND status = 'committed'
            """
        ),
        {"lid": location_id, "since": since},
    )
    summary = dict(visit.mappings().one())
    recent = await db.execute(
        text(
            """
            SELECT pc.observation_id, pc.observed_at, pc.price, pc.qty, pc.unit, pc.is_promo,
                   pc.norm_unit_price, pc.norm_unit, pc.norm_status,
                   p.id AS product_id, p.name AS product_name, p.brand
            FROM price_current pc
            JOIN product p ON p.id = pc.product_id
            WHERE pc.vendor_location_id = CAST(:lid AS uuid)
            ORDER BY pc.observed_at DESC, pc.observation_id DESC
            LIMIT 12
            """
        ),
        {"lid": location_id},
    )
    return {
        "last_visit": summary["last_visit"],
        "spend": Decimal(str(summary["spend"])),
        "visits": int(summary["visits"]),
        "period_days": days,
        "recent": [_row(r) for r in recent.mappings()],
    }


async def cheapest_by_location(
    db: AsyncSession,
    ingredient_id: uuid.UUID,
    *,
    min_quality: int | None = None,
    exclude_stale: bool = False,
) -> list[dict[str, Any]]:
    """Per active location, the best qualifying normalized price for an ingredient."""
    rows = await db.execute(
        text(
            f"""
            WITH offers AS (
                SELECT ol.applies_to_location_id AS location_id, vl.name AS location_name,
                       vl.lat, vl.lon, v.id AS vendor_id, v.name AS vendor_name, v.kind,
                       ol.product_id, p.name AS product_name, p.quality_rating,
                       ol.observation_id, ol.observed_at, ol.is_promo,
                       ol.norm_unit_price, ol.norm_unit,
                       (CAST(:now AS timestamptz) - ol.observed_at)
                           > make_interval(days => {_STALE_SQL}) AS stale
                FROM offer_latest ol
                JOIN product p ON p.id = ol.product_id AND p.active
                JOIN ingredient i ON i.id = p.ingredient_id
                JOIN (
                    SELECT vl.id, vl.name, vl.vendor_id, pl.lat, pl.lon
                    FROM vendor_location vl JOIN place pl ON pl.id = vl.place_id
                    WHERE vl.active
                ) vl ON vl.id = ol.applies_to_location_id
                JOIN vendor v ON v.id = vl.vendor_id
                WHERE p.ingredient_id = CAST(:iid AS uuid) AND ol.norm_unit_price IS NOT NULL
                  AND (CAST(:min_quality AS integer) IS NULL OR coalesce(p.quality_rating, 0) >=
                      CAST(:min_quality AS integer))
            )
            SELECT DISTINCT ON (location_id) * FROM offers
            WHERE NOT CAST(:exclude_stale AS boolean) OR NOT stale
            ORDER BY location_id, norm_unit_price, observed_at DESC
            """
        ),
        _params(iid=ingredient_id, min_quality=min_quality, exclude_stale=exclude_stale),
    )
    return [_row(r) for r in rows.mappings()]
