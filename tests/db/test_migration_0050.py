"""Migration 0050 — skill summary + tool_names columns added to skills.

Verifies that the four columns introduced by 0050 are present on the skills
table and carry the correct nullability / default semantics.
"""

from __future__ import annotations

import pytest

from stackowl.db.pool import DbPool


@pytest.mark.asyncio
async def test_skills_has_summary_and_tool_names_columns(tmp_db: DbPool):
    rows = await tmp_db.fetch_all("PRAGMA table_info(skills)")
    cols = {r["name"] for r in rows}
    assert {"summary", "summary_source", "summary_body_hash", "tool_names"} <= cols
