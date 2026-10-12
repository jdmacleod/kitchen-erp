"""A re-read's proposal: a new reading of a receipt set against its lines (04, 2S).

Pure: rows in, a proposal out, no database. The service in
``app.services.reread`` gathers the inputs and applies the result.

Three readings of a line meet here:

- the **baseline**, what the reader last wrote for the purchase (the latest
  ``lines`` stage result), keyed by line id;
- the **present** lines, as the purchase holds them now;
- the **new** reading, the lines after today's passes.

A present line is a person's when the baseline did not write it (they added it)
or it differs from what the baseline wrote for its id (they edited it). A
baseline line that is no longer present was removed by a person. The new
reading is aligned with the baseline, reader against reader, so a person's
change never confuses the alignment; whatever a person did is then kept.
"""

from __future__ import annotations

import difflib
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from app.ingest.assess import LINE_READING_FLAGS
from app.ingest.lines import (
    ParsedLine,
    reconcile,
    restoring_decimals_reconciles,
)
from app.services.normalize import normalize_receipt_text

# The fields a change can move, in the order the page shows them.
FIELDS = ("raw_text", "line_kind", "qty", "unit", "unit_price", "line_total", "attached", "flags")
_ATTACHABLE = ("discount", "deposit")


@dataclass
class Line:
    """One line, as the baseline, the purchase or the new reading has it."""

    raw_text: str | None
    line_kind: str
    qty: Decimal | None
    unit: str | None
    unit_price: Decimal | None
    line_total: Decimal
    flags: list[str] = field(default_factory=list)
    id: uuid.UUID | None = None
    parent_id: uuid.UUID | None = None  # present lines and the baseline
    parent_at: int | None = None  # the new reading: the parent's index in it
    seq: int = 0

    def key(self) -> tuple[str, str]:
        return (self.line_kind, normalize_receipt_text(self.raw_text or ""))


def _dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def baseline_lines(output: dict[str, Any] | None) -> list[Line]:
    """The lines a ``lines`` stage result wrote, with their ids and attachments."""
    rows = (output or {}).get("lines") or []
    by_seq = {row.get("seq"): row.get("purchase_line_id") for row in rows}
    out = []
    for row in rows:
        if not row.get("purchase_line_id"):
            continue
        parent = by_seq.get(row.get("parent_seq"))
        out.append(
            Line(
                raw_text=row.get("raw_text"),
                line_kind=row.get("line_kind") or "item",
                qty=_dec(row.get("qty")),
                unit=row.get("unit"),
                unit_price=_dec(row.get("unit_price")),
                line_total=_dec(row.get("line_total")) or Decimal("0"),
                flags=list(row.get("flags") or []),
                id=uuid.UUID(row["purchase_line_id"]),
                parent_id=uuid.UUID(parent) if parent else None,
                seq=row.get("seq") or 0,
            )
        )
    return out


def new_lines(parsed: list[ParsedLine]) -> list[Line]:
    at = {line.seq: i for i, line in enumerate(parsed)}
    return [
        Line(
            raw_text=p.raw_text,
            line_kind=p.line_kind,
            qty=p.qty,
            unit=p.unit,
            unit_price=p.unit_price,
            line_total=p.line_total,
            flags=list(p.flags),
            parent_at=None if p.parent_seq is None else at.get(p.parent_seq),
            seq=p.seq,
        )
        for p in parsed
    ]


def _same(a: Decimal | None, b: Decimal | None) -> bool:
    if a is None or b is None:
        return a is b
    return a == b


def _edited(present: Line, written: Line) -> bool:
    """Whether a person changed a line from what the reader wrote for it."""
    return not (
        (present.raw_text or "") == (written.raw_text or "")
        and present.line_kind == written.line_kind
        and _same(present.qty, written.qty)
        and (present.unit or None) == (written.unit or None)
        and _same(present.unit_price, written.unit_price)
        and _same(present.line_total, written.line_total)
        and present.parent_id == written.parent_id
    )


def _agrees(person: Line, reading: Line) -> bool:
    """Whether a new reading of a line says what a person made it say."""
    return (
        (person.raw_text or "") == (reading.raw_text or "")
        and person.line_kind == reading.line_kind
        and _same(person.qty, reading.qty)
        and (person.unit or None) == (reading.unit or None)
        and _same(person.line_total, reading.line_total)
    )


def _align(old: list[Line], new: list[Line]) -> list[tuple[int | None, int | None]]:
    """Pairs of (old index, new index) in receipt order; None on one side is unpaired.

    Equal runs pair up; inside a run that differs, lines pair in order while
    their kinds agree.
    """
    matcher = difflib.SequenceMatcher(
        None, [line.key() for line in old], [line.key() for line in new], autojunk=False
    )
    pairs: list[tuple[int | None, int | None]] = []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            pairs.extend(zip(range(i1, i2), range(j1, j2), strict=True))
            continue
        i, j = i1, j1
        while i < i2 and j < j2:
            if old[i].line_kind == new[j].line_kind:
                pairs.append((i, j))
            else:
                pairs.append((i, None))
                pairs.append((None, j))
            i, j = i + 1, j + 1
        pairs.extend((k, None) for k in range(i, i2))
        pairs.extend((None, k) for k in range(j, j2))
    return pairs


_CENTS = Decimal("0.01")


def _number(value: Decimal | None) -> str | None:
    """A quantity without trailing zeros: 2.3100 reads 2.31, 5.000 reads 5."""
    return None if value is None else f"{value.normalize():f}"


def _money(value: Decimal | None) -> str | None:
    """An amount with at least cents: 3.9900 reads 3.99, 4 reads 4.00, 0.1234 stays."""
    if value is None:
        return None
    plain = value.normalize()
    return f"{plain.quantize(_CENTS) if plain.as_tuple().exponent > -2 else plain:f}"


def _show(line: Line, attached: str | None) -> dict[str, Any]:
    """A line's values as the page shows them: strings, never markup.

    The database keeps amounts to four places and a reading to two, so values
    are written one way here and compared that way.
    """
    return {
        "raw_text": line.raw_text,
        "line_kind": line.line_kind,
        "qty": _number(line.qty),
        "unit": line.unit,
        "unit_price": _money(line.unit_price),
        "line_total": _money(line.line_total),
        "attached": attached,
        "flags": sorted(line.flags),
    }


@dataclass(eq=False)
class _Entry:
    action: str  # changed, unchanged, added, removed, kept
    row: Line | None  # the line as it would be, or None when it would not be there
    present: Line | None = None
    new: Line | None = None
    why: str | None = None
    owner: str = "reader"  # whose values the row carries


def propose(
    *,
    baseline: list[Line],
    present: list[Line],
    reading: list[ParsedLine],
    printed_total: Decimal | None,
    header_tax: Decimal | None,
    present_flags: list[str],
    reading_flags: list[str],
) -> dict[str, Any]:
    """The proposal for one re-read. ``present`` holds live lines only, in order."""
    new = new_lines(reading)
    written = {line.id: line for line in baseline}
    live = {line.id: line for line in present}
    person_added = [line for line in present if line.id not in written]

    entries: list[_Entry] = []
    taken: set[uuid.UUID] = set()  # present lines a new-reading entry stands for
    for old_at, new_at in _align(baseline, new):
        if new_at is None:
            assert old_at is not None
            b = baseline[old_at]
            now = live.get(b.id)
            if now is None:
                continue  # a person removed it, and the reading agrees
            taken.add(b.id)
            if _edited(now, b):
                entries.append(_Entry("kept", now, now, None, "edited", "person"))
            else:
                entries.append(_Entry("removed", None, now))
            continue
        n = new[new_at]
        if old_at is None:
            # A new line. If a person added the same line, theirs stands.
            twin = next((p for p in person_added if p.id not in taken and p.key() == n.key()), None)
            if twin is not None:
                taken.add(twin.id)
                action = "unchanged" if _agrees(twin, n) else "kept"
                entries.append(_Entry(action, twin, twin, n, "added", "person"))
            else:
                entries.append(_Entry("added", n, None, n))
            continue
        b = baseline[old_at]
        now = live.get(b.id)
        if now is None:
            entries.append(_Entry("kept", None, None, n, "removed", "person"))
            continue
        taken.add(b.id)
        if _edited(now, b):
            action = "unchanged" if _agrees(now, n) else "kept"
            entries.append(_Entry(action, now, now, n, "edited", "person"))
        else:
            row = Line(**{**n.__dict__, "id": now.id})
            entries.append(_Entry("pending", row, now, n))

    # Present lines no entry stands for: a person's additions the reading has no
    # line for. They stay, after the present line before them.
    position = {line.id: at for at, line in enumerate(present)}
    for line in present:
        if line.id in taken:
            continue
        entry = _Entry("unchanged", line, line, None, None, "person")
        before = [
            i
            for i, e in enumerate(entries)
            if e.present is not None and position.get(e.present.id, -1) < position[line.id]
        ]
        entries.insert(before[-1] + 1 if before else 0, entry)
        taken.add(line.id)

    # Attachments: the index, among the rows, of the item each row belongs to.
    rows = [e for e in entries if e.row is not None]
    row_at = {id(e): i for i, e in enumerate(rows)}
    index_of_present = {e.present.id: i for i, e in enumerate(rows) if e.present is not None}
    entry_of_new = {id(e.new): e for e in entries if e.new is not None}
    attach: dict[int, int | None] = {}
    for i, e in enumerate(rows):
        target: int | None = None
        if e.owner == "reader":
            assert e.new is not None
            if e.new.parent_at is not None:
                parent = entry_of_new.get(id(new[e.new.parent_at]))
                if parent is not None and parent.row is not None:
                    target = row_at[id(parent)]
        elif e.row.parent_id is not None:
            target = index_of_present.get(e.row.parent_id)
        if (
            target is not None
            and e.row.line_kind in _ATTACHABLE
            and rows[target].row.line_kind == "item"
            and target != i
        ):
            attach[i] = target
        else:
            attach[i] = None

    def attached_text(i: int) -> str | None:
        at = attach.get(i)
        return None if at is None else (rows[at].row.raw_text or "")

    present_attached = {
        line.id: (live[line.parent_id].raw_text if line.parent_id in live else None)
        for line in present
    }

    # Settle the reader's paired lines: changed, or the same as now.
    for i, e in enumerate(rows):
        if e.action != "pending":
            continue
        assert e.present is not None
        before = _show(e.present, present_attached.get(e.present.id))
        after = _show(e.row, attached_text(i))
        # Flags compare only those a reading raises: a resolution's stay as they are.
        written_flags = set(written[e.present.id].flags)
        before["flags"] = sorted(set(e.present.flags) & written_flags)
        e.action = "unchanged" if before == after else "changed"

    changes: list[dict[str, Any]] = []
    for e in entries:
        if e.action == "unchanged":
            continue
        change: dict[str, Any] = {
            "action": e.action,
            "line_id": None if e.present is None else str(e.present.id),
        }
        i = row_at[id(e)] if e.row is not None else None
        if e.present is not None:
            change["before"] = _show(e.present, present_attached.get(e.present.id))
        if e.action in ("changed", "added"):
            change["after"] = _show(e.row, attached_text(i))
        if e.action == "changed":
            written_flags = set(written[e.present.id].flags)
            change["before"]["flags"] = sorted(set(e.present.flags) & written_flags)
            change["fields"] = [f for f in FIELDS if change["before"][f] != change["after"][f]]
        if e.action == "kept":
            change["why"] = e.why
            if e.new is not None and e.present is None:
                change["reading"] = _show(e.new, None)  # what the reading would add back
        changes.append(change)

    out_rows = [
        {
            "line_id": None if e.row.id is None else str(e.row.id),
            "owner": e.owner,
            "raw_text": e.row.raw_text,
            "line_kind": e.row.line_kind,
            "qty": None if e.row.qty is None else str(e.row.qty),
            "unit": e.row.unit,
            "unit_price": None if e.row.unit_price is None else str(e.row.unit_price),
            "line_total": str(e.row.line_total),
            "attach_to": attach[i],
            "flags": list(e.row.flags),
        }
        for i, e in enumerate(rows)
    ]

    def sums(lines: list[Line]) -> tuple[dict[str, Any], list[ParsedLine]]:
        parsed = [
            ParsedLine(
                seq=k + 1,
                raw_text=line.raw_text or "",
                line_kind=line.line_kind,
                qty=line.qty,
                unit=line.unit,
                unit_price=line.unit_price,
                line_total=line.line_total,
                flags=list(line.flags),
            )
            for k, line in enumerate(lines)
        ]
        return reconcile(parsed, printed_total, header_tax), parsed

    before_sums, _ = sums(present)
    after_sums, after_parsed = sums([e.row for e in rows])
    after_flags = [
        f
        for f in reading_flags
        if f in LINE_READING_FLAGS and f not in ("reconcile_mismatch", "decimals_restore_total")
    ]
    if after_sums["mismatch"]:
        after_flags.append("reconcile_mismatch")
        if restoring_decimals_reconciles(after_parsed, printed_total, header_tax):
            after_flags.append("decimals_restore_total")

    counts = {
        action: sum(1 for c in changes if c["action"] == action)
        for action in ("changed", "added", "removed", "kept")
    }
    return {
        "rows": out_rows,
        "changes": changes,
        "counts": counts,
        "before": {
            "reconciliation": before_sums,
            "flags": sorted(
                f for f in present_flags if f in LINE_READING_FLAGS or f == "total_mismatch"
            ),
        },
        "after": {"reconciliation": after_sums, "flags": after_flags},
    }


def has_changes(proposal: dict[str, Any]) -> bool:
    counts = proposal["counts"]
    return bool(counts["changed"] or counts["added"] or counts["removed"])
