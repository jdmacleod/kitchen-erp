"""Front matter: a leading `---` YAML block, or the older `>> key: value` lines.

Every key is kept as written, nested keys included. Scalars stay strings: the
loader below resolves nothing but `null`, so `servings: 4` arrives as "4" and
`yield: 1.5` as "1.5", never as an int or a float. The application parses the
few keys it reads (`title`, `servings`) itself, in Decimal.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import yaml

from app.recipes.cooklang.model import ParseError

FrontMatter = dict[str, Any]


class _VerbatimLoader(yaml.SafeLoader):
    """SafeLoader with every implicit scalar type but null switched off."""


_VerbatimLoader.yaml_implicit_resolvers = {
    first: [(tag, regexp) for tag, regexp in resolvers if tag == "tag:yaml.org,2002:null"]
    for first, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _stringify(value: object) -> object:
    """Belt and braces: an explicit `!!float` tag or a date still never escapes as such."""
    if isinstance(value, dict):
        return {_key(key): _stringify(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_stringify(item) for item in value]
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _key(key: object) -> str:
    return key if isinstance(key, str) else str(key)


def _key_value_lines(lines: Iterable[str], first_line: int) -> FrontMatter | ParseError:
    """The pre-YAML form: one `key: value` per line, split at the first colon."""
    mapping: FrontMatter = {}
    for offset, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, colon, value = line.partition(":")
        key = key.strip()
        if not colon or not key:
            return ParseError("front matter line is not `key: value`", first_line + offset, 1)
        mapping[key] = value.strip()
    return mapping


def parse_front_matter_block(lines: list[str], first_line: int) -> FrontMatter | ParseError:
    """Parse the lines between the `---` fences. `first_line` is the file line of `lines[0]`.

    YAML first; when the block is not a YAML mapping (an old file with
    `cooking time    :30 mins`, say) the lines are read as `key: value` pairs.
    """
    text = "\n".join(lines)
    try:
        loaded = yaml.load(text, Loader=_VerbatimLoader)  # a SafeLoader subclass
    except yaml.YAMLError as exc:
        fallback = _key_value_lines(lines, first_line)
        if isinstance(fallback, ParseError):
            mark = getattr(exc, "problem_mark", None)
            if mark is not None:
                return ParseError(
                    f"front matter is not valid YAML: {getattr(exc, 'problem', None) or exc}",
                    first_line + mark.line,
                    mark.column + 1,
                )
            return ParseError(f"front matter is not valid YAML: {exc}", first_line, 1)
        return fallback
    except Exception as exc:  # a pathological document; the contract is never to raise
        return ParseError(f"front matter could not be read: {type(exc).__name__}", first_line, 1)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        return _key_value_lines(lines, first_line)
    result = _stringify(loaded)
    assert isinstance(result, dict)
    return result


def parse_metadata_line(line: str, line_number: int) -> tuple[str, str] | ParseError:
    """`>> key: value` → (key, value). The `>>` has already been seen by the caller."""
    body = line.strip()[2:]
    key, colon, value = body.partition(":")
    key = key.strip()
    if not colon or not key:
        column = line.index(">>") + 1
        return ParseError("metadata line is not `>> key: value`", line_number, column)
    return key, value.strip()
