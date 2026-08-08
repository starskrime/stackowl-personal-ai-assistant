"""Migration 0050 — the columns it added to ``skills``, as they stand today.

0050 introduced four columns: summary, summary_source, summary_body_hash and
tool_names. Migration 0110 dropped the three summary ones (D09.3 slice 5), so
this file now asserts the SURVIVOR and the removal.

Asserting the removal is the point. A test that only checked ``tool_names``
would pass just as happily if 0110 had never run, and the whole hazard with a
column drop is a migration that silently did not apply.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool


@pytest.mark.asyncio
async def test_skills_has_tool_names_column(tmp_db: DbPool):
    rows = await tmp_db.fetch_all("PRAGMA table_info(skills)")
    cols = {r["name"] for r in rows}
    assert "tool_names" in cols


@pytest.mark.asyncio
async def test_the_summary_columns_are_gone(tmp_db: DbPool):
    """Migration 0110. If this fails, 0110 did not apply — and the application
    reads a schema it no longer matches."""
    rows = await tmp_db.fetch_all("PRAGMA table_info(skills)")
    cols = {r["name"] for r in rows}
    assert not ({"summary", "summary_source", "summary_body_hash"} & cols)


@pytest.mark.asyncio
async def test_skills_fts_no_longer_indexes_summary(tmp_db: DbPool):
    """The FTS side of 0110. An index still describing a dropped column makes
    every keyword query a hard error, so it cannot be left to the next re-scan."""
    rows = await tmp_db.fetch_all("PRAGMA table_info(skills_fts)")
    cols = {r["name"] for r in rows}
    assert cols == {"name", "description", "when_to_use"}
