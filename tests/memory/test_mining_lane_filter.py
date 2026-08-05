"""DEBT-35 — the conversation miner must skip lanes the platform minted itself.

An incident or goal lane is the machine talking to itself: it cannot contain a
fact about the USER by construction, yet each one costs a full LLM call. Measured
when the debt was filed: 871 of 943 queued sessions (92%) were incident lanes and
only 34 (4%) looked human.
"""

from __future__ import annotations

import pytest

from stackowl.sessions.models import MACHINE_LANE_PREFIXES, is_machine_lane


@pytest.mark.parametrize("key", ["goal-abc123", "incident-deadbeef"])
def test_platform_minted_lanes_are_machine(key):
    assert is_machine_lane(key)


@pytest.mark.parametrize(
    "key",
    [
        "owl:scout:telegram:dm:12345",
        "owl:secretary:cli:dm:local",
        "20260805_120000_abcd1234",
    ],
)
def test_human_and_neutral_lanes_are_not_machine(key):
    """A wrong answer here silently drops REAL user facts, so the check keys on
    our own minted prefixes rather than inferring from content."""
    assert not is_machine_lane(key)


def test_none_and_empty_are_not_machine():
    assert not is_machine_lane(None)
    assert not is_machine_lane("")


def test_the_prefixes_match_where_they_are_minted():
    """Pins the constant against the two sites that actually create these keys —
    goal_execution.py and incident_escalation.py. Before DEBT-35 these were
    f-strings in two files with nothing tying them together."""
    assert set(MACHINE_LANE_PREFIXES) == {"goal-", "incident-"}


@pytest.mark.asyncio
async def test_mine_all_SKIPS_and_MARKS_machine_lanes():
    """Skipped is not enough — an unmarked lane is re-queued every 30 minutes
    forever, which is the DEBT-32 ratchet all over again."""
    from stackowl.memory.conversation_miner import ConversationMiner

    marked: list[str] = []

    class _Db:
        async def fetch_all(self, sql, params=None):
            return [
                {"source_ref": "incident-aaa"},
                {"source_ref": "goal-bbb"},
                {"source_ref": "owl:scout:telegram:dm:1"},
            ]

        async def execute(self, sql, params=None):
            if params and len(params) >= 2:
                marked.append(params[1])

    m = object.__new__(ConversationMiner)
    m._db = _Db()
    mined: list[str] = []

    async def _mine_session(key):
        mined.append(key)
        return 0

    m.mine_session = _mine_session  # type: ignore[method-assign]

    await m.mine_all()

    assert mined == ["owl:scout:telegram:dm:1"], f"machine lanes were mined: {mined}"
    assert "incident-aaa" in marked and "goal-bbb" in marked, (
        "a skipped lane must be MARKED, or it returns to the queue forever"
    )
