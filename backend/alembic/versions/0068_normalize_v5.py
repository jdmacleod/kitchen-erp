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

New here, for what came after 0030's rules:

* A kitchen supply alias (0063) is its own decision, one per category: two
  supply aliases of different categories that come to share a key are
  outvoted as different decisions are, never merged. A line a supply alias
  marked on its own, outvoted, returns to To identify and loses its category
  with its resolution (the two go together, by check constraint).
* A product proposal clipped for a group of waiting lines (0065) names the
  group by its key, so that key moves too, from the stored key alone.

0030's ``upgrade`` is followed step for step here, with the returned lines
clearing their supply category; its plan, backup and downgrade are used as
they are.

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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

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
    ).replace("pl.product_id,", "pl.product_id, pl.supply_category,", 1)
    module.ALIASES_SQL = module.ALIASES_SQL.replace(
        "product_id,", "product_id, supply_category,", 1
    )
    module.LINE_RETURN_COLUMNS = [*module.LINE_RETURN_COLUMNS, "supply_category"]
    module._decision = _decision
    module._line_decision = _line_decision
    return module


def _decision(alias: dict[str, Any]) -> tuple[str, Any]:
    if alias["disposition"] == "ignore":
        return ("ignore", None)
    if alias["disposition"] == "supply":
        return ("supply", alias.get("supply_category"))
    return ("product", alias["product_id"])


def _line_decision(line: dict[str, Any]) -> tuple[str, Any] | None:
    """What an alias decided for this line on its own, or None if a person did."""
    if not line["auto"]:
        return None
    if line["resolution"] == "alias":
        return ("product", line["product_id"])
    if line["resolution"] == "ignored":
        return ("ignore", None)
    if line["resolution"] == "supply":
        return ("supply", line.get("supply_category"))
    return None


_rekey = _load_0030()
LINES_SQL = _rekey.LINES_SQL
ALIASES_SQL = _rekey.ALIASES_SQL
SUGGESTIONS_SQL = _rekey.SUGGESTIONS_SQL
plan = _rekey.plan

# --- product proposals (0065) ----------------------------------------------------------

# Whether proposals have keys yet (0065): a dry run may read an older database.
HAS_KEYS_SQL = (
    "SELECT 1 FROM information_schema.columns "
    "WHERE table_name = 'product_proposal' AND column_name = 'for_raw_text_norm'"
)
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


def _ids(name: str) -> sa.BindParameter:
    return sa.bindparam(name, type_=ARRAY(UUID(as_uuid=True)))


def _rekey_upgrade() -> None:
    """0030's upgrade, step for step, except that a returned line also loses its
    supply category, which the check constraint ties to its resolution."""
    op.create_table(
        BACKUP,
        sa.Column(
            "id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")
        ),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("row_id", UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("columns", ARRAY(sa.Text()), nullable=False),
        sa.Column("before", JSONB(), nullable=False),
        sa.Column("after", JSONB()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
    )
    conn = op.get_bind()
    lines = [dict(r) for r in conn.execute(sa.text(LINES_SQL)).mappings()]
    aliases = [dict(r) for r in conn.execute(sa.text(ALIASES_SQL)).mappings()]
    suggestions = [dict(r) for r in conn.execute(sa.text(SUGGESTIONS_SQL)).mappings()]
    p = plan(lines, aliases, suggestions)

    returned = set(p.lines_returned)
    back_up = _rekey._back_up
    back_up(
        conn,
        "purchase_line",
        "returned",
        sorted(returned, key=str),
        _rekey.LINE_RETURN_COLUMNS,
    )
    back_up(
        conn,
        "purchase_line",
        "rekeyed",
        [i for i in p.line_keys if i not in returned],
        ["raw_text_norm"],
    )
    for alias_id, update in p.alias_updates.items():
        back_up(conn, "receipt_alias", "updated", [alias_id], sorted(update))
    back_up(conn, "receipt_alias", "deleted", p.alias_deletes, [])
    back_up(conn, "naming_suggestion", "deleted", p.suggestion_deletes, [])

    if p.line_keys:
        ids = list(p.line_keys)
        conn.execute(
            sa.text(
                "UPDATE purchase_line pl SET raw_text_norm = u.k "
                "FROM unnest(:ids, :keys) AS u(id, k) WHERE pl.id = u.id"
            ).bindparams(_ids("ids"), sa.bindparam("keys", type_=ARRAY(sa.Text))),
            {"ids": ids, "keys": [p.line_keys[i] for i in ids]},
        )
    if p.lines_returned:
        conn.execute(
            sa.text(
                "UPDATE purchase_line SET resolution = 'unmatched', product_id = NULL, "
                "supply_category = NULL, resolution_confidence = NULL, "
                "suggestions = '[]'::jsonb, flags = array_remove(flags, 'price_outlier') "
                "WHERE id = ANY(:ids)"
            ).bindparams(_ids("ids")),
            {"ids": p.lines_returned},
        )
    if p.alias_deletes:
        conn.execute(
            sa.text("DELETE FROM receipt_alias WHERE id = ANY(:ids)").bindparams(_ids("ids")),
            {"ids": p.alias_deletes},
        )
    rekeyed = [i for i, u in p.alias_updates.items() if "raw_text_norm" in u]
    if rekeyed:
        # Through a placeholder, so a chain of renames never collides with itself.
        conn.execute(
            sa.text(
                "UPDATE receipt_alias SET raw_text_norm = '~0068~' || id::text WHERE id = ANY(:ids)"
            ).bindparams(_ids("ids")),
            {"ids": rekeyed},
        )
    for alias_id, update in p.alias_updates.items():
        sets = ", ".join(f"{column} = :{column}" for column in update)
        conn.execute(
            sa.text(f"UPDATE receipt_alias SET {sets} WHERE id = :id").bindparams(
                sa.bindparam("id", type_=UUID(as_uuid=True))
            ),
            {"id": alias_id, **update},
        )
    if p.suggestion_deletes:
        conn.execute(
            sa.text("DELETE FROM naming_suggestion WHERE id = ANY(:ids)").bindparams(_ids("ids")),
            {"ids": p.suggestion_deletes},
        )
    _rekey._record_after(conn, "purchase_line")
    _rekey._record_after(conn, "receipt_alias")


def upgrade() -> None:
    _rekey_upgrade()
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
            _ids("ids"),
            sa.bindparam("keys", type_=ARRAY(sa.Text)),
        ),
        {"ids": ids, "keys": [moved[i] for i in ids]},
    )
    _rekey._record_after(conn, "product_proposal")


def downgrade() -> None:
    # Proposals first: 0030's downgrade drops the backup table.
    _rekey._restore_updates(op.get_bind(), "product_proposal")
    _rekey.downgrade()
