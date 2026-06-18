"""Tests for migration 0052: trust column on staged_facts + committed_facts.

Both tables should default new rows to 'untrusted' when no trust value is supplied.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_committed_trust_defaults_untrusted(tmp_db):
    await tmp_db.execute(
        "INSERT INTO committed_facts (fact_id, content, embedding, embedding_model, committed_at, source_type, source_ref, tags) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("f1", "legacy fact", b"", "m", "t", "webpage", "ref", "[]"))
    rows = await tmp_db.fetch_all("SELECT trust FROM committed_facts WHERE fact_id = ?", ("f1",))
    assert rows[0]["trust"] == "untrusted"


@pytest.mark.asyncio
async def test_staged_trust_defaults_untrusted(tmp_db):
    await tmp_db.execute(
        "INSERT INTO staged_facts (fact_id, content, source_type, source_ref, confidence, staged_at, reinforcement_count, status) "
        "VALUES (?,?,?,?,?,?,?,?)",
        ("s1", "x", "conversation", "ref", 0.5, "t", 0, "staged"))
    rows = await tmp_db.fetch_all("SELECT trust FROM staged_facts WHERE fact_id = ?", ("s1",))
    assert rows[0]["trust"] == "untrusted"
