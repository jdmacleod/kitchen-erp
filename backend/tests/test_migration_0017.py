"""Migration 0017 moves product barcodes into identifiers and back, byte for byte (03, 1H).

Seeded with invented barcodes of every form at revision 0016, then upgraded,
checked, downgraded and checked again. Codes are built with computed check
digits; none is a real product's.
"""

import os
import subprocess
import sys
import uuid

import asyncpg

from app.catalog.identifiers import check_digit, gs1_ok, upce_to_upca
from tests.conftest import BACKEND_ROOT, run_alembic


def j(spaced: str) -> str:
    return spaced.replace(" ", "")


def with_check(body: str) -> str:
    return body + str(check_digit(body))


def bad_check(code: str) -> str:
    return code[:-1] + str((int(code[-1]) + 1) % 10)


def ambiguous_eight() -> str:
    for n in range(1, 100000):
        code = with_check(f"0{n:06d}")
        upca = upce_to_upca(code)
        if upca and gs1_ok(code) and gs1_ok(upca) and upca.zfill(14) != code.zfill(14):
            return code
    raise AssertionError("no ambiguous code found")


async def _engine_reset() -> None:
    # Pooled connections hold statements prepared against the old schema.
    from app.core.db import dispose_engine

    await dispose_engine()


async def test_barcodes_move_and_come_back(owner_conn: asyncpg.Connection):
    upca = with_check(j("04812 300001"))
    ean13 = with_check(j("59012 3412345"))
    gtin = with_check(j("10614 14100004"))
    ean8 = with_check("9638507")
    ambiguous = ambiguous_eight()
    seeded = {
        "upca": upca,
        "ean13": ean13,
        "gtin14": gtin,
        "same_as_upca": "0" + upca,  # the same product code typed in EAN-13 form
        "ean8": ean8,
        "ambiguous": ambiguous,
        "plu_with_vendor": "4011",
        "plu_without_vendor": "4012",
        "bad_check": bad_check(with_check(j("04812 300002"))),
        "text": " ABC-123 ",
    }

    run_alembic("downgrade", "0016")
    await _engine_reset()
    try:
        ingredient = uuid.uuid4()
        vendor = uuid.uuid4()
        await owner_conn.execute(
            "INSERT INTO ingredient (id, name, canonical_unit, slug) VALUES ($1, 'test', 'g', $2)",
            ingredient,
            f"test-{ingredient.hex[:8]}",
        )
        await owner_conn.execute(
            "INSERT INTO vendor (id, name, slug, kind, price_scope) "
            "VALUES ($1, 'Invented Stall', 'invented-stall', 'stand', 'location')",
            vendor,
        )
        ids: dict[str, uuid.UUID] = {}
        for i, (label, code) in enumerate(seeded.items()):
            ids[label] = uuid.uuid4()
            await owner_conn.execute(
                "INSERT INTO product (id, ingredient_id, name, brand, barcode, "
                "exclusive_vendor_id, created_at) "
                "VALUES ($1, $2, $3, $4, $5, $6, now() + make_interval(secs => $7))",
                ids[label],
                ingredient,
                label,
                "Invented Brand" if label == "upca" else None,
                code,
                vendor if label == "plu_with_vendor" else None,
                i,
            )
        ids["no_barcode"] = uuid.uuid4()
        await owner_conn.execute(
            "INSERT INTO product (id, ingredient_id, name) VALUES ($1, $2, 'loose item')",
            ids["no_barcode"],
            ingredient,
        )

        # The dry run reports and changes nothing.
        dry = subprocess.run(
            [sys.executable, "-c", "from app.cli import cli; cli()", "migrate", "--check-barcodes"],
            cwd=BACKEND_ROOT,
            env=os.environ.copy(),
            capture_output=True,
            text=True,
            check=True,
        )
        assert "10 product barcodes would move" in dry.stdout
        assert "kept as other codes" in dry.stdout
        with_barcode = "SELECT count(*) FROM product WHERE barcode IS NOT NULL"
        assert await owner_conn.fetchval(with_barcode) == 10

        run_alembic("upgrade", "0017")
        rows = {
            r["product_id"]: r
            for r in await owner_conn.fetch(
                "SELECT product_id, scheme, value, vendor_id, source, legacy_value "
                "FROM product_identifier"
            )
        }
        by = {label: rows[pid] for label, pid in ids.items() if pid in rows}
        assert by["upca"]["scheme"] == "gtin" and by["upca"]["value"] == upca.zfill(14)
        assert by["ean13"]["value"] == ean13.zfill(14)
        assert by["gtin14"]["value"] == gtin
        assert (by["same_as_upca"]["scheme"], by["same_as_upca"]["value"]) == ("other", "0" + upca)
        assert by["ean8"]["value"] == ean8.zfill(14)
        assert (by["ambiguous"]["scheme"], by["ambiguous"]["value"]) == ("other", ambiguous)
        plu = by["plu_with_vendor"]
        assert (plu["scheme"], plu["vendor_id"]) == ("plu", vendor)
        assert (by["plu_without_vendor"]["scheme"], by["plu_without_vendor"]["vendor_id"]) == (
            "other",
            None,
        )
        assert by["bad_check"]["scheme"] == "other"
        assert by["text"]["value"] == " ABC-123 "
        assert {r["source"] for r in by.values()} == {"migrated_barcode"}
        assert all(by[label]["legacy_value"] == code for label, code in seeded.items())
        assert "no_barcode" not in by

        kinds = {r["id"]: r["kind"] for r in await owner_conn.fetch("SELECT id, kind FROM product")}
        assert kinds[ids["plu_with_vendor"]] == "unbranded_vendor"
        assert kinds[ids["upca"]] == "branded"  # a brand
        assert kinds[ids["ean8"]] == "branded"  # a barcode, no brand
        assert kinds[ids["no_barcode"]] == "loose"

        run_alembic("downgrade", "0016")
        restored = {
            r["id"]: r["barcode"] for r in await owner_conn.fetch("SELECT id, barcode FROM product")
        }
        assert all(restored[ids[label]] == code for label, code in seeded.items())
        assert restored[ids["no_barcode"]] is None
    finally:
        run_alembic("upgrade", "head")
        await _engine_reset()
