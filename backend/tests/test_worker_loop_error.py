"""A worker that starts before `kerp migrate` says what to do, not just an exception name."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.worker import loop_error_fields


async def test_a_missing_table_names_the_migration(db_session):
    with pytest.raises(Exception) as caught:
        await db_session.execute(text("SELECT 1 FROM table_that_is_not_migrated_yet"))
    await db_session.rollback()
    fields = loop_error_fields(caught.value)
    assert fields["exc_type"] == "ProgrammingError"
    assert "kerp migrate" in fields["hint"]


def test_other_errors_carry_no_hint():
    assert loop_error_fields(ConnectionRefusedError()) == {"exc_type": "ConnectionRefusedError"}
