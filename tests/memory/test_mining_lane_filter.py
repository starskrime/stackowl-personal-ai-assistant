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
    """Pins the constant against the sites that actually create these keys. Before
    DEBT-35 they were f-strings in two files with nothing tying them together.

    THREE MORE, MEASURED 2026-08-31 by scanning every table that carries a
    session_key — 4,150 distinct lanes, of which 523 were ``shadow-``
    (owls/shadow_validator.py:271), 96 ``job:`` (scheduler/scheduler.py:133) and
    23 ``recover-`` (pipeline/durable/recovery.py:509). All minted in src/ with no
    user input near them; ``recover-`` is the durable retry driver."""
    assert set(MACHINE_LANE_PREFIXES) == {
        "goal-", "incident-", "job:", "recover-", "shadow-",
    }


# test_mine_all_SKIPS_and_MARKS_machine_lanes REMOVED with ConversationMiner
# (D08.1) — but what it recorded is worth keeping, because it CORRECTS the root
# cause first written for that removal.
#
# The filter was real and landed 2026-08-04. It guarded `mine_all`. It did NOT
# guard `mine_session`, which the conversation-boundary handler called on every
# rolled lane — so machine lanes kept being mined through the other door, and
# incident-prefixed facts were still being staged on 2026-08-09, five days after
# the filter shipped. A guard on one path and not its sibling: the same
# half-wired shape as the archived-skill FTS leg.
#
# is_machine_lane and MACHINE_LANE_PREFIXES themselves SURVIVE and are still
# asserted above: the brief's interactive-vs-machine lane split reads them, and
# they are complete — `retry-` appears only as a staged_facts source_ref, never
# as a session_key (measured: 0 of 11,734 outcomes).
