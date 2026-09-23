#!/usr/bin/env python3
"""Every setting `.env.example` documents must actually reach a container.

Compose passes a variable into a service only if that service's `environment:`
block names it. A setting can therefore be documented in `.env.example`, written
into `.env` by an operator, and have no effect whatsoever — the container never
sees it, the application falls back to its code default, and nothing anywhere
says so. Configuration that looks applied but is not is worse than configuration
that is missing, because the operator stops looking.

Seven of the twenty-four documented settings were in exactly that state when
this check was written: OCR_ADAPTERS, INGEST_MAX_ATTEMPTS, the three
INGEST_BACKOFF_* values, INGEST_LOCATION_RADIUS_M and LLM_MAX_RETRIES. Setting
INGEST_MAX_ATTEMPTS=1 changed nothing; the worker still retried five times.

This compares the two files directly, so the failure mode cannot come back
quietly. Variables that are deliberately not forwarded belong in NOT_FORWARDED
below, with the reason written down.

    python3 -m tools.check_compose_env
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_EXAMPLE = REPO_ROOT / ".env.example"
COMPOSE = REPO_ROOT / "compose.yaml"

# Documented for the host or for Compose itself rather than for the application
# inside a container. Each one needs a reason, not just an entry.
NOT_FORWARDED = {
    "DB_OWNER_PASSWORD": "consumed by compose to build the database URLs",
    "DB_APP_PASSWORD": "consumed by compose to build the database URLs",
    "DB_PORT": "host port binding, not application configuration",
    "API_PORT": "host port binding, not application configuration",
    "WEB_PORT": "host port binding, not application configuration",
    "WEB_BIND": "host bind address, not application configuration",
    "RECIPES_PATH": "a host path compose mounts; the container sees /data/recipes",
}

VARIABLE = re.compile(r"\$\{([A-Z0-9_]+)")
ENVIRONMENT_KEY = re.compile(r"^(\s*)environment:")


def documented_settings() -> list[str]:
    names = []
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        names.append(line.split("=", 1)[0].strip())
    return names


def forwarded_variables() -> set[str]:
    """Variables referenced inside an `environment:` block, and nowhere else.

    Scanning the whole file would accept a setting that only appears under
    `ports:` or `volumes:` — referenced, but never handed to the application,
    which is the exact failure this check exists to catch.
    """
    found: set[str] = set()
    block_indent: int | None = None
    for raw in COMPOSE.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if block_indent is not None and indent <= block_indent:
            block_indent = None  # dedented back out of the block
        match = ENVIRONMENT_KEY.match(raw)
        if match:
            block_indent = len(match.group(1))
            continue
        if block_indent is not None:
            found.update(VARIABLE.findall(raw))
    return found


def main() -> int:
    documented = documented_settings()
    forwarded = forwarded_variables()

    orphaned = [n for n in documented if n not in forwarded and n not in NOT_FORWARDED]
    stale = sorted(n for n in NOT_FORWARDED if n not in documented)

    if not orphaned and not stale:
        print(
            f"check_compose_env: {len(documented)} documented setting(s) reach a container "
            f"({len(NOT_FORWARDED)} host-only, by design)"
        )
        return 0

    if orphaned:
        print(
            f"check_compose_env: {len(orphaned)} documented setting(s) never reach a container\n",
            file=sys.stderr,
        )
        for name in orphaned:
            print(f"  {name}", file=sys.stderr)
        print(
            "\nAdd each to the `environment:` block in compose.yaml (api and worker "
            "share it\nthrough the &app-env anchor), or list it in NOT_FORWARDED in this "
            "file with\nthe reason it is host-only.",
            file=sys.stderr,
        )
    if stale:
        print(
            f"\ncheck_compose_env: {len(stale)} NOT_FORWARDED entr(ies) are no longer in "
            ".env.example; remove them:",
            file=sys.stderr,
        )
        for name in stale:
            print(f"  {name}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
