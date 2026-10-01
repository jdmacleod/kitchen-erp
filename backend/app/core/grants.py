"""The runtime role's privileges at the migration head, in one place.

Migrations set these privileges step by step. A restore loads the dump with
`--no-privileges`, so `kerp restore` re-applies this list instead. A migration
that changes the runtime role's privileges (a new append-only table, a column
grant) must update this module too: `tests/test_grants.py` fails until the
privileges after a restore equal the privileges after migrating.
"""

from __future__ import annotations

APP_ROLE = "kerp_app"

# Full DML on ordinary tables, views and sequences, including tables created later.
BASE = (
    f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}",
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}",
    f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {APP_ROLE}",
    "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
    f"GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {APP_ROLE}",
    f"ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {APP_ROLE}",
)

# Facts: SELECT and INSERT only (0005). A trigger is the second line of defence.
APPEND_ONLY = ("price_observation", "price_observation_void", "ingest_stage_result")

# Append-only apart from the decision columns, which the runtime role may set (0012).
DECISION_ONLY = {"vendor_suggestion": ("status", "decided_by", "decided_at")}

# Derived data the runtime role may empty wholesale before a rebuild (0005).
TRUNCATABLE = ("price_norm",)


def statements() -> tuple[str, ...]:
    """Every statement, in order, that gives the runtime role its head privileges."""
    out = list(BASE)
    out.append(f"REVOKE UPDATE, DELETE ON {', '.join(APPEND_ONLY)} FROM {APP_ROLE}")
    for table, columns in DECISION_ONLY.items():
        out.append(f"REVOKE UPDATE, DELETE ON {table} FROM {APP_ROLE}")
        out.append(f"GRANT UPDATE ({', '.join(columns)}) ON {table} TO {APP_ROLE}")
    out.extend(f"GRANT TRUNCATE ON {table} TO {APP_ROLE}" for table in TRUNCATABLE)
    return tuple(out)
