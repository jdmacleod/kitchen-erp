"""Receipt wording keys move to normalizer version 3 (#258).

Version 3 repeats the clean-up of a line's ends until nothing more comes off,
where version 2 ran it once. A version 2 key could therefore change when it was
normalized again ("0.B-" gave "0 B", then "0"). Version 3 is version 2 run until
the text stops changing, so the only keys that move are the ones version 2 left
unfinished, and each moves to what normalizing it again would have given.

The re-keying is migration 0030's, run again with the version 3 normalizer and
a backup table of its own: lines take the key of their own text, aliases the
key their lines now carry, aliases that come to share a key at one vendor are
combined by the same rules, and naming suggestions whose key moves are dropped.
0030 is loaded from its file rather than copied; migrations never change, so it
is as frozen as a copy would be. ``kerp migrate --check-normalize`` counts what
this would change without changing anything.

Downgrade puts back what upgrade changed, as 0030's downgrade does, and drops
the backup table.

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-12
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

revision = "0049"
down_revision = "0048"
branch_labels = None
depends_on = None

BACKUP = "normalize_v3_backup"

# --- normalizer version 3, frozen --------------------------------------------------
# A copy of app/services/normalize.py at version 3. tests/test_migration_0049.py
# checks the copy against the module while the module is still at version 3.

_WS = re.compile(r"\s+")
_CODE_TOKEN = re.compile(r"[A-Z]?\d{4,}")
_PRICE = re.compile(
    r"(?:(?:\d++(?:[.,]\d++)?\s*+(?:LBS|LB|KG|OZ|EA)?\s*+@\s*+)?"
    r"\$?-?\d++[.,]\d{2}(?:\s*+/\s*+(?:LB|KG|OZ|EA))?)"
)
_BARE_AT = re.compile(r"@+")
_NON_TEXT = re.compile(r"[^A-Z0-9&/%\'\- ]+")
_FLAG_TOKEN = frozenset({"F", "T", "N", "B", "E", "X", "TX", "FS", "NF", "TF"})
_DASH = "-"


def _is_flag(token: str) -> bool:
    return token in _FLAG_TOKEN or set(token) == {"*"}


def _clean_ends(tokens: list[str]) -> list[str]:
    start, end = 0, len(tokens)
    while True:
        changed = False
        while end - start > 1 and _CODE_TOKEN.fullmatch(tokens[start]):
            start += 1
            changed = True
        while end - start > 1 and _is_flag(tokens[end - 1]):
            end -= 1
            changed = True
        while start < end and not tokens[start].lstrip(_DASH):
            start += 1
            changed = True
        if start < end and tokens[start].startswith(_DASH):
            tokens[start] = tokens[start].lstrip(_DASH)
            changed = True
        while start < end and not tokens[end - 1].rstrip(_DASH):
            end -= 1
            changed = True
        if start < end and tokens[end - 1].endswith(_DASH):
            tokens[end - 1] = tokens[end - 1].rstrip(_DASH)
            changed = True
        if not changed:
            return tokens[start:end]


def normalize_v3(raw: str) -> str:
    text = raw.upper()
    text = _PRICE.sub(" ", text)
    text = _BARE_AT.sub(" ", text)
    text = _NON_TEXT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return " ".join(_clean_ends(text.split(" ") if text else []))


# --- 0030's re-keying, with version 3 ------------------------------------------------


def _load_0030() -> Any:
    path = Path(__file__).resolve().parent / "0030_normalize_v2.py"
    spec = importlib.util.spec_from_file_location("migration_0030_for_0049", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # This module's own copy: alembic's loaded 0030 is a different object and is
    # left as it was. 0030's functions read these names when they run.
    module.normalize_v2 = normalize_v3
    module.BACKUP = BACKUP
    # A line with no text of its own is re-keyed from its stored key, which version
    # 3 finishes exactly as it would the text the key was made from.
    module.LINES_SQL = module.LINES_SQL.replace(
        "pl.raw_text,", "COALESCE(pl.raw_text, pl.raw_text_norm) AS raw_text,", 1
    )
    return module


_rekey = _load_0030()
LINES_SQL = _rekey.LINES_SQL
ALIASES_SQL = _rekey.ALIASES_SQL
SUGGESTIONS_SQL = _rekey.SUGGESTIONS_SQL
plan = _rekey.plan


def upgrade() -> None:
    _rekey.upgrade()


def downgrade() -> None:
    _rekey.downgrade()
