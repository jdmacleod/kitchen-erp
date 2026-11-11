"""Receipt wording keys move to normalizer version 5 (#489).

Version 5 drops a regular-price or saving phrase that follows the name
("REGULAR PRICE ... YOU SAVED ..."), and the tax flag it had left in the middle,
so a line whose text carries its regular-price row keys as the name alone and
meets the alias the vendor's other lines taught. Text with no such phrase
normalizes as before, so the only keys that move are lines whose printed text has
one.

The re-keying is migration 0030's, run again with the version 5 normalizer and a
backup table of its own, as 0049 and 0059 did: lines take the key of their own
text, aliases the key their lines now carry, aliases that come to share a key at
one vendor are combined by the same rules, and naming suggestions whose key moves
are dropped. A line with no text of its own is re-keyed from its stored key,
which version 5 reads to the same key as the text it came from.

New here: a product proposal clipped for a group of waiting lines (0065) names
the group by its key, so that key moves too, from the stored key alone. 0030's
rules came before proposals had one.

``kerp migrate --check-normalize`` counts what this would change without
changing anything. Downgrade puts back what upgrade changed, as 0030's downgrade
does, and drops the backup table.

Revision ID: 0068
Revises: 0067
Create Date: 2026-11-10
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID

from alembic import op

revision = "0068"
down_revision = "0067"
branch_labels = None
depends_on = None

BACKUP = "normalize_v5_backup"

# --- normalizer version 5, frozen --------------------------------------------------
# A copy of app/services/normalize.py at version 5. tests/test_migration_0068.py
# checks the copy against the module while the module is still at version 5.

_WS = re.compile(r"\s+")
_CODE_TOKEN = re.compile(r"[A-Z]?\d{4,}")
_PRICE = re.compile(
    r"(?:(?:\d++(?:[.,]\d++)?\s*+(?:LBS|LB|KG|OZ|EA)?\s*+@\s*+)?"
    r"\$?-?\d++[.,]\d{2}(?!M?L\b)(?:\s*+/\s*+(?:LB|KG|OZ|EA))?)"
)
_BARE_AT = re.compile(r"@+")
_NON_TEXT = re.compile(r"[^A-Z0-9&/%\'\-. ]+")
_STRAY_POINT = re.compile(r"(?<!\d)\.|\.(?!\d)")

_FLAG_TOKEN = frozenset({"F", "T", "N", "B", "E", "X", "TX", "FS", "NF", "TF"})
_DASH = "-"

_PHRASE_OPENERS = (
    (re.compile(r"RE[GSQA]ULAR|REG|ORIG|ORIGINAL"), re.compile(r"PR[IL1]CE")),
    (re.compile(r"YOU"), re.compile(r"SAVED?")),
)
_HAS_LETTER = re.compile(r"[A-Z]")


def _is_flag(token: str) -> bool:
    return token in _FLAG_TOKEN or set(token) == {"*"}


def _phrase_at(tokens: list[str], i: int) -> bool:
    if i + 1 >= len(tokens):
        return False
    first, second = tokens[i].strip(_DASH), tokens[i + 1].strip(_DASH)
    return any(a.fullmatch(first) and b.fullmatch(second) for a, b in _PHRASE_OPENERS)


def _is_name_word(token: str) -> bool:
    word = token.strip(_DASH)
    return bool(
        _HAS_LETTER.search(word)
        and not _is_flag(word)
        and not _CODE_TOKEN.fullmatch(word)
        and not any(a.fullmatch(word) or b.fullmatch(word) for a, b in _PHRASE_OPENERS)
    )


def _cut_saving_phrase(tokens: list[str]) -> list[str]:
    named = False
    for i, token in enumerate(tokens):
        if named and _phrase_at(tokens, i):
            return tokens[:i]
        named = named or _is_name_word(token)
    return tokens


def _clean_ends(tokens: list[str]) -> list[str]:
    start, end = 0, len(tokens)  # tokens[start:end] is the text kept so far
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


def normalize_v5(raw: str) -> str:
    text = raw.upper()
    text = _PRICE.sub(" ", text)
    text = _BARE_AT.sub(" ", text)
    text = _NON_TEXT.sub(" ", text)
    text = _STRAY_POINT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return " ".join(_clean_ends(_cut_saving_phrase(text.split(" ") if text else [])))


# --- 0030's re-keying, with version 5 ------------------------------------------------


def _load_0030() -> Any:
    path = Path(__file__).resolve().parent / "0030_normalize_v2.py"
    spec = importlib.util.spec_from_file_location("migration_0030_for_0068", path)
    assert spec is not None and spec.loader is not None  # noqa: S101
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # This module's own copy: alembic's loaded 0030 is a different object and is
    # left as it was. 0030's functions read these names when they run.
    module.normalize_v2 = normalize_v5
    module.BACKUP = BACKUP
    # A line with no text of its own is re-keyed from its stored key.
    module.LINES_SQL = module.LINES_SQL.replace(
        "pl.raw_text,", "COALESCE(pl.raw_text, pl.raw_text_norm) AS raw_text,", 1
    )
    return module


_rekey = _load_0030()
LINES_SQL = _rekey.LINES_SQL
ALIASES_SQL = _rekey.ALIASES_SQL
SUGGESTIONS_SQL = _rekey.SUGGESTIONS_SQL
plan = _rekey.plan

# --- product proposals (0065) ----------------------------------------------------------

PROPOSALS_SQL = (
    "SELECT id, for_raw_text_norm FROM product_proposal WHERE for_raw_text_norm IS NOT NULL"
)


def plan_proposals(proposals: list[dict[str, Any]]) -> dict[Any, str]:
    """Proposal id -> new key, for each proposal whose key moves."""
    moved = {}
    for proposal in proposals:
        key = normalize_v5(proposal["for_raw_text_norm"])
        if key != proposal["for_raw_text_norm"]:
            moved[proposal["id"]] = key
    return moved


def upgrade() -> None:
    _rekey.upgrade()
    conn = op.get_bind()
    proposals = [dict(r) for r in conn.execute(sa.text(PROPOSALS_SQL)).mappings()]
    moved = plan_proposals(proposals)
    if not moved:
        return
    ids = list(moved)
    _rekey._back_up(conn, "product_proposal", "rekeyed", ids, ["for_raw_text_norm"])
    conn.execute(
        sa.text(
            "UPDATE product_proposal pp SET for_raw_text_norm = u.k "
            "FROM unnest(:ids, :keys) AS u(id, k) WHERE pp.id = u.id"
        ).bindparams(
            sa.bindparam("ids", type_=ARRAY(UUID(as_uuid=True))),
            sa.bindparam("keys", type_=ARRAY(sa.Text)),
        ),
        {"ids": ids, "keys": [moved[i] for i in ids]},
    )
    _rekey._record_after(conn, "product_proposal")


def downgrade() -> None:
    # Proposals first: 0030's downgrade drops the backup table.
    _rekey._restore_updates(op.get_bind(), "product_proposal")
    _rekey.downgrade()
