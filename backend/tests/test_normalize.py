import pytest

from app.services.normalize import NORMALIZE_VERSION
from app.services.normalize import normalize_receipt_text as norm

TABLE = [
    ("ITAL BOMBA HOT PEP", "ITAL BOMBA HOT PEP"),
    ("  ital  bomba   hot pep ", "ITAL BOMBA HOT PEP"),
    ("ORG BANANAS 4011", "ORG BANANAS 4011"),  # trailing PLU stays: it names the item
    ("4011 ORG BANANAS", "ORG BANANAS"),
    ("0123456789012 KS ALMOND BUTTER", "KS ALMOND BUTTER"),
    ("1234567 KS ALMOND BUTTER 12.99 F", "KS ALMOND BUTTER"),
    ("MILK WHOLE GAL 4.29 T", "MILK WHOLE GAL"),
    ("CHEESE CHEDDAR   3.49 F", "CHEESE CHEDDAR"),
    ("2 @ 1.99 AVOCADO HASS", "AVOCADO HASS"),
    ("2.31 lb @ 3.99/lb HEIRLOOM TOM", "HEIRLOOM TOM"),
    ("HEIRLOOM TOM 2.31 LB @ 3.99 /LB 9.22", "HEIRLOOM TOM"),
    ("SPARKLING WATER CRV 0.10", "SPARKLING WATER CRV"),
    ("COUPON -1.50", "COUPON"),
    ("BEANS BLK 15OZ 0.99 NF", "BEANS BLK 15OZ"),
    ("YOGURT PLAIN 32 OZ *", "YOGURT PLAIN 32 OZ"),
    ("MEMBER SAVINGS $2.00", "MEMBER SAVINGS"),
    ("BOB'S RED MILL OATS", "BOB'S RED MILL OATS"),
    ("HALF & HALF QT", "HALF & HALF QT"),
    ("BREAD--SOURDOUGH", "BREAD--SOURDOUGH"),
    ("- - -", ""),
]


@pytest.mark.parametrize(("raw", "expected"), TABLE)
def test_table(raw: str, expected: str):
    assert norm(raw) == expected


@pytest.mark.parametrize(("raw", "_"), TABLE)
def test_idempotent(raw: str, _):
    once = norm(raw)
    assert norm(once) == once


def test_version_is_recorded_constant():
    assert NORMALIZE_VERSION == "1"
