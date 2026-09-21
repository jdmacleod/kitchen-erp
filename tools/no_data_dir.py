#!/usr/bin/env python3
"""Hard-fail a commit that stages anything under data/, a backup, or a reference clone.

These paths are gitignored, so reaching this hook means someone used `git add -f`
or edited the ignore rules. Committing a receipt photograph or the database is the
highest-severity mistake this project can make; committing an AGPL clone into an
MIT repository is the second. Each gets a hook of its own rather than living as a
rule inside the PII scanner.
"""

from __future__ import annotations

import sys
from pathlib import Path

FORBIDDEN_ROOTS = (
    "data",
    "backups",
    "output",
    "reference",
    "cookcli",
    "grocy",
    "kitchenowl",
    "mealie",
    "paprika-recipes",
)
FORBIDDEN_PREFIXES = ("data.bak-", "data.bak.")


def forbidden(path: str) -> bool:
    parts = Path(path).parts
    if not parts:
        return False
    root = parts[0]
    return root in FORBIDDEN_ROOTS or root.startswith(FORBIDDEN_PREFIXES)


def main(argv: list[str]) -> int:
    offenders = [p for p in argv if forbidden(p)]
    if not offenders:
        return 0

    print(
        "Refusing to commit files under a data or reference directory:\n",
        file=sys.stderr,
    )
    for p in offenders:
        print(f"  {p}", file=sys.stderr)
    print(
        "\nThese paths hold real household data or code under other licences."
        "\nGit history is forever without `git filter-repo`. Unstage them with:"
        "\n\n  git restore --staged " + " ".join(offenders) + "\n",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
