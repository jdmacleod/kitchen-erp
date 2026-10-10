"""`parse(text) -> Recipe | ParseError`. Pure: a string in, a structure out, no I/O.

The grammar, as this parser reads it:

- A file may open with a `---` fenced YAML block (line 1 exactly); `>> key: value`
  lines anywhere in the body add to the same mapping.
- `= Name` (optionally `== Name ==`) starts a section.
- Steps are paragraphs: lines joined by a single space, separated by blank lines.
- `-- …` runs to the end of the line; `[- … -]` may span lines. Both vanish.
- `@name{qty%unit}(note)`, `#name{qty}`, `~name{qty%unit}`; `@name`, `#name`
  and `~name` without braces are single words; `~{qty%unit}` is anonymous;
  `@?name` and `#?name` are optional.
- A `{` or `(` opened by a component and never closed on its line, a `[-`
  never closed, a `>>` without a colon, and front matter that does not close
  or does not parse are errors with a line and a column. Everything else that
  is not a component is text.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field, replace
from decimal import Decimal

from app.recipes.cooklang.frontmatter import (
    FrontMatter,
    parse_front_matter_block,
    parse_metadata_line,
)
from app.recipes.cooklang.model import (
    Cookware,
    IngredientRef,
    Item,
    ParseError,
    Quantity,
    Recipe,
    Section,
    Step,
    Text,
    Timer,
)
from app.recipes.cooklang.quantities import parse_number, parse_quantity
from app.units.parse import parse_unit

_BOM = "\N{ZERO WIDTH NO-BREAK SPACE}"
_MARKERS = "@#~"
_STOP_IN_NAME = "@#~{}"
# Characters a single-word name may contain between two name characters.
_JOINERS = "-_'’"


def _is_punctuation(char: str) -> bool:
    return unicodedata.category(char).startswith("P")


def _name_char(char: str) -> bool:
    return not char.isspace() and not _is_punctuation(char) and char not in _MARKERS


def _strip_fences(line: str) -> str:
    return line.strip().strip("=").strip()


@dataclass
class _Scan:
    """Mutable state while walking the lines of a body."""

    in_block_comment: bool = False
    block_comment_at: tuple[int, int] = (0, 0)
    sections: list[Section] = field(default_factory=list)
    section_name: str | None = None
    section_line: int = 0
    section_started: bool = False
    steps: list[Step] = field(default_factory=list)
    items: list[Item] = field(default_factory=list)
    step_line: int = 0
    metadata: FrontMatter = field(default_factory=dict)

    def end_step(self) -> None:
        items = _tidy(self.items)
        if items:
            self.steps.append(Step(tuple(items), self.step_line))
        self.items = []

    def end_section(self) -> None:
        self.end_step()
        if self.section_started or self.steps:
            self.sections.append(Section(self.section_name, tuple(self.steps), self.section_line))
        self.steps = []

    def start_section(self, name: str | None, line: int) -> None:
        self.end_section()
        self.section_name = name
        self.section_line = line
        self.section_started = True


def _tidy(items: list[Item]) -> list[Item]:
    """Merge adjacent text, trim the step's outer edges, drop empty text."""
    merged: list[Item] = []
    for item in items:
        if isinstance(item, Text) and merged and isinstance(merged[-1], Text):
            merged[-1] = Text(merged[-1].value + item.value)
        else:
            merged.append(item)
    if merged and isinstance(merged[0], Text):
        merged[0] = Text(merged[0].value.lstrip())
    if merged and isinstance(merged[-1], Text):
        merged[-1] = Text(merged[-1].value.rstrip())
    return [item for item in merged if not (isinstance(item, Text) and not item.value)]


def _split_front_matter(
    lines: list[str],
) -> tuple[FrontMatter, int] | ParseError:
    """The front matter mapping and the index of the first body line."""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            block = parse_front_matter_block(lines[1:index], 2)
            if isinstance(block, ParseError):
                return block
            return block, index + 1
    return ParseError("front matter opened with `---` is never closed", 1, 1)


def _servings(value: object) -> tuple[Decimal | None, str | None]:
    """`servings: 4` → (4, "4"); `servings: 4 to 6` → (None, "4 to 6"); anything else → nothing."""
    if not isinstance(value, str) or not value.strip():
        return None, None
    text = value.strip()
    return parse_number(text), text


def parse(text: str) -> Recipe | ParseError:
    """Parse a Cooklang document. Returns a `ParseError` instead of raising, always."""
    if not isinstance(text, str):
        return ParseError("not text", 1, 1)
    text = text.replace("\r\n", "\n").replace("\r", "\n").removeprefix(_BOM)
    lines = text.split("\n")
    head = _split_front_matter(lines)
    if isinstance(head, ParseError):
        return head
    front_matter, body_start = head
    scan = _Scan(metadata=dict(front_matter))
    for offset, line in enumerate(lines[body_start:]):
        failure = _scan_line(scan, line, body_start + offset + 1)
        if failure is not None:
            return failure
    if scan.in_block_comment:
        line_number, column = scan.block_comment_at
        return ParseError("block comment opened with `[-` is never closed", line_number, column)
    scan.end_section()
    title = scan.metadata.get("title")
    servings, servings_text = _servings(scan.metadata.get("servings"))
    return Recipe(
        front_matter=scan.metadata,
        sections=tuple(scan.sections),
        title=title if isinstance(title, str) and title.strip() else None,
        servings=servings,
        servings_text=servings_text,
    )


def _scan_line(scan: _Scan, line: str, line_number: int) -> ParseError | None:
    """Fold one line into the scan state."""
    start = 0
    if scan.in_block_comment:
        close = line.find("-]")
        if close < 0:
            return None  # the whole line is comment; it neither joins nor splits
        scan.in_block_comment = False
        start = close + 2
        if not line[start:].strip():
            return None
    else:
        stripped = line.strip()
        if stripped.startswith(">>"):
            scan.end_step()
            parsed = parse_metadata_line(line, line_number)
            if isinstance(parsed, ParseError):
                return parsed
            key, value = parsed
            scan.metadata[key] = value
            return None
        if stripped.startswith("="):
            scan.start_section(_strip_fences(line) or None, line_number)
            return None
        if not stripped:
            scan.end_step()
            return None

    items, had_comment, failure = _scan_inline(scan, line, start, line_number)
    if failure is not None:
        return failure
    has_content = any(not isinstance(item, Text) or item.value.strip() for item in items)
    if not has_content:
        # Only whitespace survived: a comment-only line is skipped, a blank line splits.
        if not had_comment and not scan.in_block_comment:
            scan.end_step()
        return None
    if not scan.items:
        scan.step_line = line_number
    elif items:
        scan.items.append(Text(" "))
    scan.items.extend(items)
    return None


def _scan_inline(
    scan: _Scan, line: str, start: int, line_number: int
) -> tuple[list[Item], bool, ParseError | None]:
    """Items on one line from `start`. Also reports whether a comment was seen."""
    items: list[Item] = []
    buffer: list[str] = []
    had_comment = False
    i = start
    n = len(line)

    def flush() -> None:
        if buffer:
            items.append(Text("".join(buffer)))
            buffer.clear()

    while i < n:
        two = line[i : i + 2]
        if two == "--":
            run = len(line[i:]) - len(line[i:].lstrip("-"))
            if run == 2:
                had_comment = True
                break
            buffer.append("-" * run)  # `---` and longer are text, not a comment
            i += run
            continue
        if two == "[-":
            had_comment = True
            close = line.find("-]", i + 2)
            if close < 0:
                scan.in_block_comment = True
                scan.block_comment_at = (line_number, i + 1)
                break
            i = close + 2
            continue
        char = line[i]
        if char in _MARKERS:
            result = _component(line, i, line_number)
            if isinstance(result, ParseError):
                return items, had_comment, result
            if result is not None:
                item, i = result
                flush()
                items.append(item)
                continue
        buffer.append(char)
        i += 1
    flush()
    return items, had_comment, None


def _component(line: str, at: int, line_number: int) -> tuple[Item, int] | ParseError | None:
    """A component starting at `line[at]`, with the index after it, or None for plain text."""
    marker = line[at]
    j = at + 1
    optional = False
    if marker in "@#" and j < len(line) and line[j] == "?":
        optional = True
        j += 1
    if j >= len(line) or line[j].isspace():
        return None

    # Multi-word form: a `{` on this line with no other marker before it.
    brace = _find_brace(line, j)
    if brace is not None:
        name = line[j:brace].rstrip()
        if not name and marker != "~":
            return None
        close = line.find("}", brace + 1)
        if close < 0:
            return ParseError(f"`{marker}{name}{{` is never closed", line_number, brace + 1)
        body = line[brace + 1 : close]
        end = close + 1
        note: str | None = None
        if marker == "@" and end < len(line) and line[end] == "(":
            note_close = line.find(")", end + 1)
            if note_close < 0:
                return ParseError(f"note on `@{name}` is never closed", line_number, end + 1)
            note = line[end + 1 : note_close].strip() or None
            end = note_close + 1
        item = _build(marker, name or None, body, note, optional, line_number)
        return _place(item, at, end, j, j + len(name)), end

    # Single-word form: name characters, allowing a joiner between two of them.
    k = j
    while k < len(line) and (
        _name_char(line[k])
        or (line[k] in _JOINERS and k > j and k + 1 < len(line) and _name_char(line[k + 1]))
    ):
        k += 1
    name = line[j:k]
    if not name:
        return None
    return _place(_build(marker, name, "", None, optional, line_number), at, k, j, k), k


def _place(item: Item, start: int, end: int, name_start: int, name_end: int) -> Item:
    """An ingredient with its token and name spans recorded; other items unchanged."""
    if not isinstance(item, IngredientRef):
        return item
    return replace(item, span=(start, end), name_span=(name_start, name_end))


def _find_brace(line: str, start: int) -> int | None:
    for k in range(start, len(line)):
        char = line[k]
        if char == "{":
            return k
        if char in _STOP_IN_NAME or line[k : k + 2] in ("--", "[-"):
            return None
    return None


def _build(
    marker: str, name: str | None, body: str, note: str | None, optional: bool, line: int
) -> Item:
    quantity_text, percent, unit_text = body.partition("%")
    unit = unit_text.strip() if percent else ""
    if marker == "#":
        return Cookware(name or "", _cookware_quantity(body), optional, line)
    quantity: Quantity = parse_quantity(quantity_text)
    if marker == "~":
        return Timer(name, quantity, unit or None, line)
    return IngredientRef(
        raw_name=name or "",
        quantity=quantity,
        unit_text=unit or None,
        unit=parse_unit(unit) if unit else None,
        note=note,
        optional=optional,
        line=line,
    )


def _cookware_quantity(body: str) -> Quantity:
    """Cookware takes a count or a description, never a unit: the whole body is the quantity."""
    return parse_quantity(body)
