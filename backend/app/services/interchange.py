"""What every interchange file shares: reading it safely, and writing it without
overwriting a person's edit (spec 03 §1F, §1G).

Vendor files (``kitchen-erp-vendors``) and ingredient files
(``kitchen-erp-ingredients``) are both untrusted text (non-negotiable 7). A
file is read here, never executed: YAML anchors and aliases are refused, numbers
must be written as strings so nothing becomes a float, the ``format`` must be
one this code knows, and the result is validated against the file's Pydantic
model before anything looks at it.

Writing follows one rule for every writer that is not a person: a field keeps
its value when a person changed it since a source last wrote it. Each object
records, per field, what a source last wrote (``field_source[name].imported``),
so a field is a person's exactly while it differs from that.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from app.core.errors import ApiError

MAX_BYTES = 5 * 1024 * 1024


def bad_file(message: str, **details: Any) -> ApiError:
    return ApiError(422, "bad_export", message, details or None)


# --- reading -------------------------------------------------------------------


def _loader(kind: str) -> type[yaml.SafeLoader]:
    class _Loader(yaml.SafeLoader):
        """SafeLoader still expands anchors and aliases; this one refuses them."""

        def compose_node(self, parent: Any, index: Any) -> Any:  # type: ignore[override]
            event = self.peek_event()
            if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
                raise bad_file(f"YAML anchors and aliases are not allowed in {kind}.")
            return super().compose_node(parent, index)

    return _Loader


def _no_floats(node: Any, path: str = "") -> None:
    if isinstance(node, float):
        raise bad_file(f"{path or 'A value'} is a floating-point number; write it as a string.")
    if isinstance(node, dict):
        for k, v in node.items():
            _no_floats(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _no_floats(v, f"{path}[{i}]")


def _refuse_float(text: str) -> Any:
    raise bad_file(f"{text} is a floating-point number; write numbers as strings.")


def load(
    raw: bytes,
    fmt: str | None,
    *,
    kind: str,
    formats: Iterable[str],
    shape: str,
    max_bytes: int = MAX_BYTES,
) -> dict[str, Any]:
    """Bytes to a plain document with a known ``format``, or 422 ``bad_export``.

    ``kind`` names the file in messages ("a vendor file"); ``shape`` says what one
    document holds ("a format, a source and vendors"). Nothing is written.
    """
    if len(raw) > max_bytes:
        megabytes = max_bytes // (1024 * 1024)
        raise bad_file(f"The file is over the {megabytes} MB limit.", limit_bytes=max_bytes)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise bad_file("The file is not UTF-8 text.") from None
    if fmt is None:
        fmt = "json" if text.lstrip().startswith(("{", "[")) else "yaml"
    try:
        if fmt == "json":
            data = json.loads(text, parse_float=_refuse_float, parse_constant=_refuse_float)
        else:
            data = yaml.load(text, Loader=_loader(kind))  # noqa: S506 - SafeLoader subclass
            _no_floats(data)
    except ApiError:
        raise
    except (ValueError, yaml.YAMLError) as exc:
        raise bad_file(f"The file is not valid {fmt.upper()}: {exc}") from None
    if not isinstance(data, dict):
        raise bad_file(f"The file must hold one document with {shape}.")
    known = list(formats)
    if data.get("format") not in known:
        raise bad_file(
            f"Unsupported format {data.get('format')!r}; expected one of {', '.join(known)}."
        )
    return data


def validate[M: BaseModel](model: type[M], data: dict[str, Any]) -> M:
    """The document as ``model``, or 422 ``bad_export`` naming the first problems."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        errors = [
            {"at": ".".join(str(p) for p in e["loc"]), "problem": e["msg"]}
            for e in exc.errors()[:20]
        ]
        raise bad_file(
            f"The file does not match {data['format']}: {errors[0]['at']}: {errors[0]['problem']}",
            errors=errors,
        ) from None


# --- writing -------------------------------------------------------------------


def edit_outcome(current: Any, last_written: Any, value: Any) -> str:
    """What writing ``value`` over ``current`` would do.

    ``unchanged`` when they are equal; ``kept`` when a person changed the field
    since a source last wrote it (it no longer equals ``last_written``), so their
    edit wins; otherwise ``filled`` (it was empty) or ``updated``.
    """
    if value == current:
        return "unchanged"
    if current != last_written:
        return "kept"
    return "filled" if current is None else "updated"


def recorded(obj: Any, name: str) -> Any:
    """What a source last wrote to ``obj.<name>``, per its ``field_source``, or None."""
    record = (obj.field_source or {}).get(name)
    if isinstance(record, dict) and "imported" in record:
        return record["imported"]
    return None


def write_unless_edited(
    obj: Any,
    name: str,
    value: Any,
    *,
    source: str,
    ref: str | None,
    now: datetime,
    only_if_empty: bool = False,
    last_written: Callable[[Any, str], Any] = recorded,
    stored: Callable[[Any], Any] = lambda v: v,
) -> str:
    """Write ``value`` to ``obj.<name>`` unless a person has edited the field.

    ``field_source[name].imported`` always advances to what the source now says,
    so a field is a person's exactly while it differs from its source. With
    ``only_if_empty`` a field that holds any value is left entirely alone.
    ``stored`` turns the value into what JSON can keep (a Decimal as a string);
    the comparison uses the same form. Returns the ``edit_outcome``.
    """
    current = getattr(obj, name)
    if only_if_empty and current is not None:
        return "unchanged" if value == current else "kept"
    previous = last_written(obj, name)
    outcome = edit_outcome(
        stored(current) if current is not None else None,
        previous,
        stored(value) if value is not None else None,
    )
    if outcome in ("filled", "updated"):
        setattr(obj, name, value)
    imported = stored(value) if value is not None else None
    record = {"source": source, "ref": ref, "checked_at": now.isoformat(), "imported": imported}
    obj.field_source = {**(obj.field_source or {}), name: record}
    return outcome
