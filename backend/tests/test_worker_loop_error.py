"""A worker that starts before `kerp migrate` says what to do, not just an exception name."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.worker import loop_error_fields


async def test_a_missing_table_names_the_migration(db_session):
    with pytest.raises(Exception) as caught:  # noqa: PT011
        await db_session.execute(text("SELECT 1 FROM table_that_is_not_migrated_yet"))
    await db_session.rollback()
    fields = loop_error_fields(caught.value)
    assert fields["exc_type"] == "ProgrammingError"
    assert "kerp migrate" in fields["hint"]


def test_other_errors_carry_no_hint():
    assert loop_error_fields(ConnectionRefusedError()) == {"exc_type": "ConnectionRefusedError"}


# Regression: the worker resolves receipts but never installed the model ranker, so a
# line it read was never ranked against a shortlist. Found by /devex-review on 2026-10-05.
def test_the_worker_installs_the_model_ranker(monkeypatch):
    from app.core.config import get_settings
    from app.services import resolution
    from app.worker import prepare

    monkeypatch.setattr(get_settings(), "llm_ranker_enabled", True)
    resolution.set_ranker(None)
    try:
        assert prepare() is True
        assert resolution._ranker is not None
    finally:
        resolution.set_ranker(None)


# Regression: a scan that changed something logged its counts as top-level extras, and
# ``created`` is a LogRecord attribute, so the log call itself raised (KeyError).
def test_a_recipe_scan_logs_its_counts():
    import logging
    from datetime import UTC, datetime

    from app.core.logging import JsonFormatter
    from app.schemas.recipes import ScanOut
    from app.worker import scan_log_fields

    counts = dict.fromkeys(
        ("files", "settling", "created", "updated", "moved", "missing", "parse_errors"), 1
    )
    result = ScanOut(mounted=True, scanned_at=datetime.now(UTC), proposals=0, **counts)
    record = logging.getLogger("app.worker").makeRecord(
        "app.worker",
        logging.INFO,
        __file__,
        1,
        "recipes scanned",
        (),
        None,
        extra=scan_log_fields(result),
    )
    assert '"created": 1' in JsonFormatter().format(record)
