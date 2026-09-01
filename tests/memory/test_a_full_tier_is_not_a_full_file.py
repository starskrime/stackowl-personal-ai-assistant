"""The refusal told the model the file was full when 404 characters were free.

MEASURED 2026-09-01, and this is a defect I introduced six hours earlier. The
``UNTIL_CHANGED_RESERVE_SHARE`` reserve was right — the permanent tier had eaten
98.2% of the live ``USER.md`` and every decaying fact was evicted on arrival — but
``add`` handed the RESERVED CEILING to ``_at_capacity``, whose whole vocabulary
(``used_chars``, "Memory is at X/Y chars", ``_usage``) is about the WHOLE FILE.

A per-tier number in a whole-file report. Reproduced exactly::

    used=971  permanent_ceiling=1031  full_budget=1375
    "Memory is at 971/1,031 chars. Adding this (61 chars) would exceed the
     limit. Consolidate now: 'replace' to merge ... or 'remove' what is stale"

The file is 71% full. The model is told to consolidate, so it goes and merges or
DELETES the operator's curated facts to make room that already exists.

AND ON THE THREE TARGETS ALREADY OVER THE CEILING it prints a used greater than
its own denominator. Live right now: user 1,246 permanent against a 1,031 ceiling,
jobmarket 2,039/1,650, owl 2,142/1,650 — each with ZERO characters of anything
else. Those files would report "1,246/1,031", which no reader can act on.

THE MISSING RUNG IS THE REAL COST. When the permanent tier is full but the file is
not, the correct move is to store the fact as ``until_changed`` — and the refusal
never says so. It offers only 'replace' and 'remove', so the only door the model
is shown is the destructive one. That is why a 100%-permanent file kept asking for
consolidation and never got any: on ``jobmarket`` the ask fired three times inside
two minutes and the file is unchanged.

WHAT IS NOT FIXED HERE, deliberately: the three targets already over the ceiling
stay over it. Unwinding them means rewriting entries in Bakir's curated memory,
which is his call and never mine — escalated, not done. The reserve is an
ADMISSION gate and by construction cannot repair state that predates it.
"""

from __future__ import annotations

import pytest

from stackowl.memory.curated import (
    USER_TARGET,
    CuratedMemory,
    Entry,
)


@pytest.fixture
def mem(tmp_path):  # noqa: ANN001, ANN201
    return CuratedMemory(root=tmp_path / "memory")


def _fill_permanent_to_ceiling(mem: CuratedMemory) -> str:
    """Fill ONLY the permanent tier, the way the live user.md is filled.

    Returns the text that was REFUSED. Re-offering that exact text is what makes
    the refusal deterministic: a shorter probe simply fits under the ceiling, and
    a first draft of this test measured that instead of the defect.
    """
    for i in range(500):
        text = f"Permanent fact {i} about how the operator works and what he prefers."
        if not mem.add(USER_TARGET, text, "permanent").ok:
            return text
    raise AssertionError("the permanent tier never hit its ceiling")


def test_the_refusal_never_understates_the_budget(mem) -> None:  # noqa: ANN001
    """The defect: "971/1,031" for a file whose budget is 1,375. A model reading
    that consolidates a file with 404 free characters."""
    refused = _fill_permanent_to_ceiling(mem)
    r = mem.add(USER_TARGET, refused, "permanent")
    assert r.ok is False
    full = mem.budget_for(USER_TARGET)
    assert f"{full:,}" in (r.message or ""), (
        f"the refusal quotes a denominator that is not the file's real budget "
        f"({full}) — it is reporting a per-tier ceiling as if it were the file:\n"
        f"  {r.message}"
    )


def test_used_never_exceeds_the_stated_budget(mem) -> None:  # noqa: ANN001
    """On the three live targets already over the ceiling this printed a used
    LARGER than its own denominator, which no reader can act on."""
    refused = _fill_permanent_to_ceiling(mem)
    r = mem.add(USER_TARGET, refused, "permanent")
    used = mem.used_chars(USER_TARGET)
    full = mem.budget_for(USER_TARGET)
    assert used <= full
    assert f"{used:,}/{full:,}" in (r.message or ""), (
        f"the refusal does not report used against the whole file:\n  {r.message}"
    )


def test_the_refusal_names_the_tier_that_is_actually_full(mem) -> None:  # noqa: ANN001
    """Telling a 100%-permanent file to 'remove what is stale' without saying
    WHICH tier is blocking is why jobmarket asked three times in two minutes and
    freed nothing."""
    refused = _fill_permanent_to_ceiling(mem)
    r = mem.add(USER_TARGET, refused, "permanent")
    assert "permanent" in (r.message or "").lower(), (
        f"the refusal does not say which tier is full:\n  {r.message}"
    )


def test_it_offers_the_NON_destructive_door(mem) -> None:  # noqa: ANN001
    """The missing rung, and the expensive one. When the permanent tier is full
    but the FILE is not, storing the fact as until_changed costs nothing and
    deletes nothing — and the refusal never mentioned it, so the only door the
    model was shown was the destructive one."""
    refused = _fill_permanent_to_ceiling(mem)
    r = mem.add(USER_TARGET, refused, "permanent")
    assert "until_changed" in (r.message or ""), (
        f"a permanent refusal on a file with room left does not offer the "
        f"non-destructive alternative:\n  {r.message}"
    )


def test_a_genuinely_full_file_still_gets_the_consolidation_ask(mem) -> None:  # noqa: ANN001
    """The expensive direction, and it caught a hole in the fix itself.

    "Store it as until_changed instead" is only true while the FILE has room. On
    a file that is genuinely full the decaying tier has nowhere to go either, and
    softening the ask into that advice would send the model down a door that is
    also shut — leaving the fact unsaved and the file untouched.

    A first draft of this test asserted an until_changed write gets refused. It
    never does: `_evict_to_fit` drops the oldest decaying entry to make room, which
    is the decay working. Measuring that would have proved nothing."""
    ceiling = mem._effective_budget(USER_TARGET, "permanent")  # noqa: SLF001
    budget = mem.budget_for(USER_TARGET)
    entries, total = [], 0
    while total < budget - 20:
        text = f"Permanent fact {len(entries)} written before the reserve existed."
        entries.append(Entry(text=text, durability="permanent"))
        total += len(text)
    mem._write(USER_TARGET, entries)  # noqa: SLF001
    assert mem.used_chars(USER_TARGET) > ceiling

    r = mem.add(USER_TARGET, "A further permanent fact that cannot fit anywhere.",
                "permanent")
    assert r.ok is False
    assert "Consolidate" in (r.message or ""), (
        f"a genuinely full file was told to store the fact elsewhere:\n  {r.message}"
    )
    assert "until_changed instead" not in (r.message or "")


def _write_state_that_predates_the_gate(mem: CuratedMemory) -> int:
    """Put the file over the ceiling the way the LIVE files got there — written
    before the reserve existed. Going through `add` could not produce this state,
    which is the whole point: an admission gate cannot create, or repair, it."""
    ceiling = mem._effective_budget(USER_TARGET, "permanent")  # noqa: SLF001
    entries, total = [], 0
    while total <= ceiling:
        text = f"Permanent fact {len(entries)} recorded before the reserve existed."
        entries.append(Entry(text=text, durability="permanent"))
        total += len(text)
    mem._write(USER_TARGET, entries)  # noqa: SLF001
    return total


def test_over_ceiling_targets_are_reportable(mem) -> None:  # noqa: ANN001
    """The three live targets are invisible: at_capacity fires only on a write
    attempt and named neither the tier nor the reason, so nothing could ever tell
    the operator that user, jobmarket and owl have zero room for a decaying
    fact."""
    total = _write_state_that_predates_the_gate(mem)
    over = {t: (perm, ceil, other) for t, perm, ceil, other in mem.over_ceiling_targets()}
    assert USER_TARGET in over, (
        f"a file {total} chars into a "
        f"{mem._effective_budget(USER_TARGET, 'permanent')}-char ceiling is not "  # noqa: SLF001
        f"reported — the condition stays unobservable, which is how three live "
        f"targets reached it unnoticed"
    )
    perm, ceil, other = over[USER_TARGET]
    assert perm > ceil and other == 0


def test_a_healthy_target_is_not_reported(mem) -> None:  # noqa: ANN001
    """The expensive direction: a false alarm on every target would train the
    operator to ignore the report entirely."""
    mem.add(USER_TARGET, "One small permanent fact.", "permanent")
    assert USER_TARGET not in {t for t, *_ in mem.over_ceiling_targets()}
