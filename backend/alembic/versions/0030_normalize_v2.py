"""Receipt wording keys move to normalizer version 2 (04, 2D; #125).

Version 2 reads a comma inside a price as a decimal point, so "KELP CRISPS 7,25"
and "KELP CRISPS 7.25" share one key where version 1 kept "7 25" in the first.
Every stored key is re-made:

- ``purchase_line.raw_text_norm`` from the line's own ``raw_text``.
- ``receipt_alias`` keys. An alias stores only its normalized wording, and
  version 1 had already dropped the comma, so a key cannot be re-normalized on
  its own. It takes the version 2 key its own lines now carry: the lines at the
  same vendor whose stored key equals the alias's. Where those lines disagree
  the commonest wins, and an alias with no lines keeps its key.
- Aliases that now share a key at one vendor are combined. Those making the
  same decision (the same product, or both ignore) merge into the oldest row,
  with their confirmations added up and the latest sighting kept. Where the
  decisions differ, the decision with the most confirmations stays (a tie goes
  to the most recently seen), and lines the losing decision resolved on its own
  go back to To identify. A line a person decided is left as it is. Price
  observations are facts and are not touched: a returned line's live price is
  voided by the decision a person makes for it, as on any re-pointed line.
- ``naming_suggestion`` rows whose key changes are dropped, because their
  suggested names were made from wording that still carried price digits. The
  naming pass asks again.

Every changed or deleted row is copied first into ``normalize_v2_backup``,
with the columns that changed and their values before and after. The
downgrade restores each row whose changed columns still hold what this
migration wrote, re-inserts deleted rows whose key is still free, and drops the
table. Rows a person has changed since are left as they are.

``plan`` is pure, so ``kerp migrate --check-normalize`` can count what this
would change without changing anything.

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-06
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter, defaultdict
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

BACKUP = "normalize_v2_backup"

# --- normalizer version 2, frozen ----------------------------------------------
# A copy of app/services/normalize.py at version 2. A migration must not change
# when the module does; tests/test_migration_0030.py checks the copy against the
# module while the module is still at version 2.

_WS = re.compile(r"\s+")
_LEADING_CODE = re.compile(r"^(?:[A-Z]?\d{4,}\s+)+")
_PRICE = re.compile(
    r"(?:(?:\d++(?:[.,]\d++)?\s*+(?:LBS|LB|KG|OZ|EA)?\s*+@\s*+)?"
    r"\$?-?\d++[.,]\d{2}(?:\s*+/\s*+(?:LB|KG|OZ|EA))?)"
)
_BARE_AT = re.compile(r"@+")
_NON_TEXT = re.compile(r"[^A-Z0-9&/%\'\- ]+")
_FLAG_TOKEN = frozenset({"F", "T", "N", "B", "E", "X", "TX", "FS", "NF", "TF"})


def _strip_trailing_flags(text: str) -> str:
    parts = text.split(" ")
    while len(parts) > 1 and (parts[-1] in _FLAG_TOKEN or set(parts[-1]) == {"*"}):
        parts.pop()
    return " ".join(parts)


def normalize_v2(raw: str) -> str:
    text = raw.upper()
    text = _PRICE.sub(" ", text)
    text = _BARE_AT.sub(" ", text)
    text = _NON_TEXT.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    text = _LEADING_CODE.sub("", text)
    text = _strip_trailing_flags(text)
    text = text.strip("- ")
    return _WS.sub(" ", text).strip()


# --- the plan ----------------------------------------------------------------------

Decision = tuple[str, Any]  # ("product", product_id) or ("ignore", None)


class Plan:
    """Every change upgrade makes. A plain class: kerp loads this file by path,
    outside sys.modules, where a dataclass cannot resolve its annotations."""

    def __init__(self) -> None:
        self.line_keys: dict[Any, str] = {}  # line id -> new key
        self.lines_returned: list[Any] = []
        self.alias_updates: dict[Any, dict[str, Any]] = {}
        self.alias_deletes: list[Any] = []
        self.suggestion_deletes: list[Any] = []
        self.aliases_merged = 0  # aliases folded into another making the same decision
        self.aliases_outvoted = 0  # aliases dropped for a different decision

    def summary(self) -> dict[str, int]:
        return {
            "lines re-keyed": len(self.line_keys),
            "aliases re-keyed": sum(1 for u in self.alias_updates.values() if "raw_text_norm" in u),
            "aliases merged": self.aliases_merged,
            "aliases outvoted": self.aliases_outvoted,
            "aliases dropped with no wording": len(self.alias_deletes)
            - self.aliases_merged
            - self.aliases_outvoted,
            "lines returned to To identify": len(self.lines_returned),
            "naming suggestions dropped": len(self.suggestion_deletes),
        }


def _decision(alias: dict[str, Any]) -> Decision:
    if alias["disposition"] == "ignore":
        return ("ignore", None)
    return ("product", alias["product_id"])


def _line_decision(line: dict[str, Any]) -> Decision | None:
    """What an alias decided for this line on its own, or None if a person did."""
    if not line["auto"]:
        return None
    if line["resolution"] == "alias":
        return ("product", line["product_id"])
    if line["resolution"] == "ignored":
        return ("ignore", None)
    return None


def plan(
    lines: list[dict[str, Any]],
    aliases: list[dict[str, Any]],
    suggestions: list[dict[str, Any]],
) -> Plan:
    """Work out every change. Pure: rows in, decisions out.

    lines: id, vendor_id, raw_text, raw_text_norm, resolution, auto (no person
    decided it), product_id, removed. aliases: id, vendor_id, raw_text_norm,
    disposition, product_id, confirmed_count, last_seen_at, created_at.
    suggestions: id, vendor_id, raw_text_norm.
    """
    out = Plan()
    new_key: dict[Any, str] = {}
    # (vendor, old key) -> the version 2 keys its lines now carry.
    carried: dict[tuple[Any, str], Counter[str]] = defaultdict(Counter)
    for line in lines:
        old = line["raw_text_norm"]
        new = normalize_v2(line["raw_text"]) if line["raw_text"] is not None else old
        new_key[line["id"]] = new
        if new != old:
            out.line_keys[line["id"]] = new
        if line["vendor_id"] is not None and old:
            carried[(line["vendor_id"], old)][new] += 1

    def moved(vendor_id: Any, old: str) -> str:
        counts = carried.get((vendor_id, old))
        if not counts:
            return old
        best = max(counts.values())
        tied = sorted(k for k, n in counts.items() if n == best)
        return old if old in tied else tied[0]

    groups: dict[tuple[Any, str], list[dict[str, Any]]] = defaultdict(list)
    for alias in aliases:
        key = moved(alias["vendor_id"], alias["raw_text_norm"])
        if not key:
            out.alias_deletes.append(alias["id"])  # nothing left to match on
            continue
        groups[(alias["vendor_id"], key)].append(alias)

    returned: set[Any] = set()
    for (vendor_id, key), members in groups.items():
        by_decision: dict[Decision, list[dict[str, Any]]] = defaultdict(list)
        for alias in members:
            by_decision[_decision(alias)].append(alias)

        weight = {
            d: (sum(r["confirmed_count"] for r in rows), max(r["last_seen_at"] for r in rows))
            for d, rows in by_decision.items()
        }
        winner = max(by_decision, key=weight.__getitem__)
        kept_rows = sorted(by_decision[winner], key=lambda r: (r["created_at"], str(r["id"])))
        keep = kept_rows[0]
        update: dict[str, Any] = {}
        if keep["raw_text_norm"] != key:
            update["raw_text_norm"] = key
        if len(kept_rows) > 1:
            update["confirmed_count"] = sum(r["confirmed_count"] for r in kept_rows)
            update["last_seen_at"] = max(r["last_seen_at"] for r in kept_rows)
            out.aliases_merged += len(kept_rows) - 1
        if update:
            out.alias_updates[keep["id"]] = update
        out.alias_deletes.extend(r["id"] for r in kept_rows[1:])
        losers = {d for d in by_decision if d != winner}
        for decision in losers:
            out.alias_deletes.extend(r["id"] for r in by_decision[decision])
            out.aliases_outvoted += len(by_decision[decision])
        if losers:
            for line in lines:
                if (
                    line["vendor_id"] == vendor_id
                    and new_key[line["id"]] == key
                    and not line["removed"]
                    and _line_decision(line) in losers
                ):
                    returned.add(line["id"])
    out.lines_returned = sorted(returned, key=str)

    for suggestion in suggestions:
        if (
            moved(suggestion["vendor_id"], suggestion["raw_text_norm"])
            != suggestion["raw_text_norm"]
        ):
            out.suggestion_deletes.append(suggestion["id"])
    return out


# --- reading and writing ---------------------------------------------------------

LINES_SQL = """
    SELECT pl.id, vl.vendor_id, pl.raw_text, pl.raw_text_norm, pl.resolution,
           pl.resolved_by IS NULL AS auto, pl.product_id, pl.removed_at IS NOT NULL AS removed
    FROM purchase_line pl
    JOIN purchase p ON p.id = pl.purchase_id
    LEFT JOIN vendor_location vl ON vl.id = p.vendor_location_id
"""
ALIASES_SQL = """
    SELECT id, vendor_id, raw_text_norm, disposition, product_id, confirmed_count,
           last_seen_at, created_at
    FROM receipt_alias
"""
SUGGESTIONS_SQL = "SELECT id, vendor_id, raw_text_norm FROM naming_suggestion"

LINE_RETURN_COLUMNS = [
    "raw_text_norm",
    "resolution",
    "product_id",
    "resolution_confidence",
    "suggestions",
    "flags",
]


def _ids(name: str) -> sa.BindParameter:
    return sa.bindparam(name, type_=ARRAY(UUID(as_uuid=True)))


def _back_up(conn: Any, table: str, action: str, ids: list[uuid.UUID], columns: list[str]) -> None:
    if not ids:
        return
    conn.execute(
        sa.text(
            f"INSERT INTO {BACKUP} (table_name, row_id, action, columns, before) "
            f"SELECT :table, t.id, :action, :columns, to_jsonb(t) FROM {table} t "
            "WHERE t.id = ANY(:ids)"
        ).bindparams(_ids("ids"), sa.bindparam("columns", type_=ARRAY(sa.Text))),
        {"table": table, "action": action, "columns": columns, "ids": ids},
    )


def _record_after(conn: Any, table: str) -> None:
    conn.execute(
        sa.text(
            f"UPDATE {BACKUP} k SET after = to_jsonb(t) FROM {table} t "
            "WHERE k.table_name = :table AND k.row_id = t.id AND k.after IS NULL "
            "AND k.action <> 'deleted'"
        ),
        {"table": table},
    )


def upgrade() -> None:
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

    # Back up first: every row is copied as it stands before anything changes.
    returned = set(p.lines_returned)
    _back_up(conn, "purchase_line", "returned", sorted(returned, key=str), LINE_RETURN_COLUMNS)
    _back_up(
        conn,
        "purchase_line",
        "rekeyed",
        [i for i in p.line_keys if i not in returned],
        ["raw_text_norm"],
    )
    for alias_id, update in p.alias_updates.items():
        _back_up(conn, "receipt_alias", "updated", [alias_id], sorted(update))
    _back_up(conn, "receipt_alias", "deleted", p.alias_deletes, [])
    _back_up(conn, "naming_suggestion", "deleted", p.suggestion_deletes, [])

    # Lines: new keys, then the returned lines lose the outvoted alias's decision.
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
                "resolution_confidence = NULL, suggestions = '[]'::jsonb, "
                "flags = array_remove(flags, 'price_outlier') WHERE id = ANY(:ids)"
            ).bindparams(_ids("ids")),
            {"ids": p.lines_returned},
        )

    # Aliases: the losers go first so their keys are free, and keys move through a
    # placeholder so a chain of renames never collides with itself mid-statement.
    if p.alias_deletes:
        conn.execute(
            sa.text("DELETE FROM receipt_alias WHERE id = ANY(:ids)").bindparams(_ids("ids")),
            {"ids": p.alias_deletes},
        )
    rekeyed = [i for i, u in p.alias_updates.items() if "raw_text_norm" in u]
    if rekeyed:
        conn.execute(
            sa.text(
                "UPDATE receipt_alias SET raw_text_norm = '~0030~' || id::text WHERE id = ANY(:ids)"
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
    _record_after(conn, "purchase_line")
    _record_after(conn, "receipt_alias")


def _restore_updates(conn: Any, table: str) -> None:
    """Put back each row's changed columns where they still hold what upgrade wrote."""
    column_sets = conn.execute(
        sa.text(
            f"SELECT DISTINCT columns FROM {BACKUP} "
            "WHERE table_name = :table AND action <> 'deleted'"
        ).columns(sa.column("columns", ARRAY(sa.Text()))),
        {"table": table},
    ).scalars()
    for columns in list(column_sets):
        assignments = ", ".join(f"{c} = b.{c}" for c in columns)
        unchanged = " AND ".join(f"x.{c} IS NOT DISTINCT FROM a.{c}" for c in columns)
        conn.execute(
            sa.text(
                f"UPDATE {table} x SET {assignments} "
                f"FROM {BACKUP} k, "
                f"LATERAL jsonb_populate_record(NULL::{table}, k.before) b, "
                f"LATERAL jsonb_populate_record(NULL::{table}, k.after) a "
                f"WHERE k.table_name = :table AND k.action <> 'deleted' "
                f"AND k.columns = :columns AND x.id = k.row_id AND {unchanged}"
            ).bindparams(sa.bindparam("columns", type_=ARRAY(sa.Text))),
            {"table": table, "columns": columns},
        )


def _restore_aliases(conn: Any) -> None:
    """Put back updated aliases, through placeholder keys like the upgrade.

    An alias is restored when its changed columns still hold what upgrade wrote
    and its old key is free, or held only by another alias being restored.
    """
    rows = conn.execute(
        sa.text(
            f"SELECT k.row_id, k.columns, k.before, k.after, to_jsonb(r) AS now "
            f"FROM {BACKUP} k JOIN receipt_alias r ON r.id = k.row_id "
            "WHERE k.table_name = 'receipt_alias' AND k.action = 'updated'"
        ).columns(
            sa.column("row_id", UUID(as_uuid=True)),
            sa.column("columns", ARRAY(sa.Text())),
            sa.column("before", JSONB()),
            sa.column("after", JSONB()),
            sa.column("now", JSONB()),
        )
    ).mappings()
    restorable = [
        r for r in rows if all(r["now"].get(c) == r["after"].get(c) for c in r["columns"])
    ]
    moving = {r["row_id"] for r in restorable}
    occupied = {
        (str(vendor_id), key)
        for alias_id, vendor_id, key in conn.execute(
            sa.text("SELECT id, vendor_id, raw_text_norm FROM receipt_alias")
        )
        if alias_id not in moving
    }
    restorable = [
        r
        for r in restorable
        if (r["before"]["vendor_id"], r["before"]["raw_text_norm"]) not in occupied
    ]
    if not restorable:
        return
    conn.execute(
        sa.text(
            "UPDATE receipt_alias SET raw_text_norm = '~0030~' || id::text WHERE id = ANY(:ids)"
        ).bindparams(_ids("ids")),
        {"ids": [r["row_id"] for r in restorable]},
    )
    for r in restorable:
        columns = sorted(set(r["columns"]) | {"raw_text_norm"})
        assignments = ", ".join(f"{c} = b.{c}" for c in columns)
        conn.execute(
            sa.text(
                f"UPDATE receipt_alias x SET {assignments} "
                "FROM jsonb_populate_record(NULL::receipt_alias, CAST(:before AS jsonb)) b "
                "WHERE x.id = :id"
            ).bindparams(sa.bindparam("id", type_=UUID(as_uuid=True))),
            {"id": r["row_id"], "before": json.dumps(r["before"])},
        )


def downgrade() -> None:
    conn = op.get_bind()
    _restore_updates(conn, "purchase_line")
    _restore_aliases(conn)
    conn.execute(
        sa.text(
            "INSERT INTO receipt_alias "
            f"SELECT r.* FROM {BACKUP} k, "
            "LATERAL jsonb_populate_record(NULL::receipt_alias, k.before) r "
            "WHERE k.table_name = 'receipt_alias' AND k.action = 'deleted' "
            "AND EXISTS (SELECT 1 FROM vendor v WHERE v.id = r.vendor_id) "
            "AND (r.product_id IS NULL "
            "OR EXISTS (SELECT 1 FROM product p WHERE p.id = r.product_id)) "
            "ON CONFLICT DO NOTHING"
        )
    )
    # A suggestion's ingredient may have gone since; the column is ON DELETE SET NULL.
    conn.execute(
        sa.text(
            f"UPDATE {BACKUP} k SET before = jsonb_set(k.before, '{{ingredient_id}}', 'null') "
            "WHERE k.table_name = 'naming_suggestion' AND k.before->>'ingredient_id' IS NOT NULL "
            "AND NOT EXISTS (SELECT 1 FROM ingredient i "
            "WHERE i.id = (k.before->>'ingredient_id')::uuid)"
        )
    )
    conn.execute(
        sa.text(
            "INSERT INTO naming_suggestion "
            f"SELECT r.* FROM {BACKUP} k, "
            "LATERAL jsonb_populate_record(NULL::naming_suggestion, k.before) r "
            "WHERE k.table_name = 'naming_suggestion' "
            "AND EXISTS (SELECT 1 FROM vendor v WHERE v.id = r.vendor_id) "
            "ON CONFLICT DO NOTHING"
        )
    )
    op.drop_table(BACKUP)
