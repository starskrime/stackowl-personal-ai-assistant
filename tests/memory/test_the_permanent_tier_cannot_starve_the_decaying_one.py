"""A tier that can take everything will, and the tier below it dies quietly.

MEASURED 2026-09-01 on Bakir's live ``USER.md``: **1,350 of 1,375 characters
used — 98.2% — across SIX entries, every single one ``[permanent]``.** Decay
never touches ``permanent``, and its docstring is right that this "is what
separates decay from data loss". So ``until_changed`` had TWENTY-FIVE characters
of room, and any fact stored at that durability was evicted almost the moment it
arrived.

It is not hypothetical. Three separate days carry
``[curated] decay: evicted the oldest until_changed entry to make room``, and the
text is his::

    "Dental: Cigna DHMO (in-network-only); in-network dentists near 75025."   (85)
    "Jobmarket deliverable: user wants all 5 categories ... in downloads/."  (172)

Forty ``at_capacity: asking for consolidation`` events say the file has been full
for days.

THE DECAY WAS NEVER THE DEFECT. It did exactly what it promises: prefer
``until_changed`` over ``permanent``, evict the oldest, never touch permanent.
What was missing is a bound on the tier ABOVE it — nothing stopped ``permanent``
consuming the whole file.

SAME SHAPE, SAME FIX AS THE SKILL CATALOGUE. ``CATALOG_RESERVE_SHARE`` exists in
the skill injector because the catalogue tier — the cheap one, the index of what
EXISTS — was being starved by the expensive text tiers, and nothing protected it.
This is that failure in the memory store, and it takes the same answer: a derived
share the greedy tier may not cross. Past it, a ``permanent`` write hits the
at-capacity path and ASKS for consolidation, which is a mechanism that already
exists and already runs forty times over.

WHAT THIS DOES NOT FIX, and is escalated instead: two of the six permanent
entries are jobmarket CONFIGURATION (~730 chars, 54% of the file) — "Jobmarket:
verify every posting LIVE ... Deliverable: deduped md list ..." and "Jobmarket
filter rule ... FULLY REMOTE". Bakir's own standing rule is that USER.md is "the
person only; job config/logs go on the owl". Moving them is a change to HIS
curated memory and is his call, not mine.
"""

from __future__ import annotations

import pytest

from stackowl.memory.curated import (
    OWL_BUDGET_CHARS,
    UNTIL_CHANGED_RESERVE_SHARE,
    USER_BUDGET_CHARS,
    USER_TARGET,
    CuratedMemory,
)


@pytest.fixture
def mem(tmp_path):  # noqa: ANN001, ANN201
    """Same construction as tests/memory/test_curated.py's fixture."""
    return CuratedMemory(root=tmp_path / "memory")


def test_permanent_cannot_consume_the_whole_budget(mem) -> None:  # noqa: ANN001
    """The live defect: six permanent entries at 98.2% left nothing behind."""
    ceiling = mem._effective_budget(USER_TARGET, "permanent")  # noqa: SLF001
    assert ceiling < mem.budget_for(USER_TARGET), (
        "permanent may still take the entire file — the decaying tier has "
        "nowhere to live and every until_changed fact is evicted on arrival"
    )


def test_until_changed_still_sees_the_whole_budget(mem) -> None:  # noqa: ANN001
    """The reserve exists FOR this tier, so it must not also be charged for it."""
    assert mem._effective_budget(USER_TARGET, "until_changed") == mem.budget_for(  # noqa: SLF001
        USER_TARGET
    )


def test_the_reserve_holds_the_facts_that_were_actually_evicted(mem) -> None:  # noqa: ANN001
    """Derived, not chosen: the two real evictions were 85 and 172 characters, so
    the reserve has to hold entries of that size or it is decoration."""
    reserved = USER_BUDGET_CHARS - mem._effective_budget(USER_TARGET, "permanent")  # noqa: SLF001
    assert reserved >= 172 + 85, (
        f"the reserve is {reserved} chars — smaller than the two entries the "
        f"live store actually evicted (172 and 85)"
    )


def test_an_until_changed_fact_survives_a_full_permanent_tier(mem) -> None:  # noqa: ANN001
    """End to end, and the whole point: fill permanent to ITS ceiling, then store
    the kind of fact that was being lost, and find it still there."""
    i = 0
    while True:
        text = f"Permanent fact number {i} about the user and how they work."
        if mem.add(USER_TARGET, text, "permanent").ok is False:
            break
        i += 1
        assert i < 200, "permanent never hit its ceiling — the reserve is not applied"

    dental = "Dental: Cigna DHMO (in-network-only); in-network dentists near 75025."
    assert mem.add(USER_TARGET, dental, "until_changed").ok, (
        "a real until_changed fact could not be stored even with the reserve"
    )
    assert any(dental in e.text for e in mem.entries(USER_TARGET))


def test_the_owl_target_gets_the_same_protection(mem) -> None:  # noqa: ANN001
    """Owls accumulate operational facts fastest, so if the reserve applied only
    to the user target the same starvation would just move."""
    assert mem._effective_budget("scout", "permanent") < OWL_BUDGET_CHARS  # noqa: SLF001


def test_the_share_is_stated_with_its_measurement() -> None:
    """The number alone is not the fix — a later reader lowering it to "make room
    for one more permanent fact" would restore the starvation, so the 98.2% has
    to live beside the constant."""
    import inspect

    from stackowl.memory import curated

    assert 0.0 < UNTIL_CHANGED_RESERVE_SHARE < 1.0
    src = inspect.getsource(curated)
    marker = src.split("UNTIL_CHANGED_RESERVE_SHARE = ")[0][-2000:]
    assert "98.2" in marker and "Dental" in marker, (
        "the measurement that justifies the reserve is not stated next to it"
    )
