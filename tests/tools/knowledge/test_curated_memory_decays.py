"""Curated memory must forget, or it stops being able to learn.

MEASURED 2026-08-18 across every log the platform has ever written::

    add: stored          16
    replace: stored      27
    remove: dropped       5      <- the ONLY decay that has ever happened
    at_capacity refused  36      <- more writes REFUSED than ever succeeded
    nudge: due           35

13,655 characters of fact were offered and thrown away in three days, 35 of the 36
refusals against ``USER.md``. The design's answer to a full file was "the agent
consolidates": ``add`` refuses, and a nudge asks the model to merge or remove. Live
traffic says that actuator does not work — 35 nudges bought 5 removals while 36
writes were lost. Asking politely is not a mechanism.

THIS IS THE FOURTH DEFECT SHAPE IN CLAUDE.md — "No decay. Anything that only
appends will poison its reader."

WHAT WAS ALREADY DECIDED, AND NEVER BUILT. The module docstring states the rule
outright: *"Storing it (not just checking it) lets EVICTION prefer ``until_changed``
over ``permanent`` instead of evicting by age."* Durability is stored on every entry
for precisely this, and nothing ever evicted anything. Same shape as
``should_decompose`` — a mechanism declared, wired to nothing.

WHY EVICTING THE OLDEST ``until_changed`` IS THE RIGHT DEFAULT. The choice is not
"lose something vs lose nothing" — it is "lose the newest fact or the oldest". Today
the NEW one is discarded, every time. For a durability whose whole name says it
holds only *until changed*, the newer fact is the better bet. ``permanent`` is never
touched, and no entry is dropped while any ``until_changed`` remains that would make
room.

NO AGE COLUMN WAS ADDED. Entries are appended, so file order already IS insertion
order — the first ``until_changed`` in the file is the oldest one there.
"""

from __future__ import annotations

import pytest

from stackowl.memory.curated import CuratedMemory


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setenv("STACKOWL_HOME", str(tmp_path))
    m = CuratedMemory(root=tmp_path / "memory")
    return m


#: Big enough that adding one MUST displace something. The earlier version of this
#: helper used entries so small that filling the file evicted it down to a steady
#: state and the final add then fitted without pressure — the tests passed against
#: a path they never took, which is the exact failure mode this codebase keeps
#: finding. Sized from the real budget instead of a guess.
_BIG = 300


def _fill_to_capacity(mem: CuratedMemory, target: str, *, durability: str) -> int:
    """Add entries until the file is genuinely full. Returns how many landed.

    Stops on the first refusal (permanent) or when eviction starts recycling
    (until_changed), so the file is at true capacity either way.
    """
    stored = 0
    for i in range(40):
        r = mem.add(target, f"{durability[:4]}-{i:02d} " + "x" * 90,
                    durability=durability)
        if not r.ok:
            break
        stored += 1
        if mem.used_chars(target) > mem.budget_for(target) - 120:
            break
    return stored


class TestAFullFileMakesRoomInsteadOfLosingTheNewFact:
    def test_the_new_fact_survives_a_full_file(self, mem: CuratedMemory) -> None:
        """The regression that cost 13,655 chars: the write was simply refused."""
        _fill_to_capacity(mem, "user", durability="until_changed")

        result = mem.add("user", "the fact that mattered " + "y" * _BIG,
                         durability="until_changed")

        assert result.ok, result.message
        assert any("the fact that mattered" in e.text for e in mem.entries("user"))

    def test_the_oldest_until_changed_is_what_goes(self, mem: CuratedMemory) -> None:
        """File order is insertion order, so the first one there is the oldest."""
        _fill_to_capacity(mem, "user", durability="until_changed")
        before = [e.text for e in mem.entries("user")]

        mem.add("user", "brand new " + "y" * _BIG, durability="until_changed")

        after = [e.text for e in mem.entries("user")]
        assert before[0] not in after
        assert before[-1] in after

    def test_it_stays_inside_the_budget(self, mem: CuratedMemory) -> None:
        _fill_to_capacity(mem, "user", durability="until_changed")

        mem.add("user", "brand new " + "y" * _BIG, durability="until_changed")

        assert mem.used_chars("user") <= mem.budget_for("user")


class TestPermanentIsNeverEvicted:
    def test_a_permanent_entry_survives_pressure(self, mem: CuratedMemory) -> None:
        """This is the difference between decay and data loss. "Bakir prefers
        root-cause fixes over patches" is the one entry in his real profile that is
        actually about him, and it is permanent."""
        mem.add("user", "Bakir prefers root-cause fixes over patches",
                durability="permanent")
        _fill_to_capacity(mem, "user", durability="until_changed")

        mem.add("user", "another one " + "y" * _BIG, durability="until_changed")

        kept = [e.text for e in mem.entries("user")]
        assert any("root-cause fixes" in t for t in kept)

    def test_a_file_of_only_permanent_entries_still_REFUSES(
        self, mem: CuratedMemory,
    ) -> None:
        """Eviction must never become a licence to delete durable facts. With
        nothing evictable left, the honest answer is still the refusal — and the
        model is asked to consolidate, exactly as before."""
        _fill_to_capacity(mem, "user", durability="permanent")

        result = mem.add("user", "one more " + "y" * _BIG, durability="permanent")

        assert not result.ok
        assert "consolidate" in result.message.lower()


class TestTheEvictionIsVisible:
    def test_the_caller_is_told_what_was_dropped(self, mem: CuratedMemory) -> None:
        """A silent delete is the thing this codebase keeps having to fix. The
        model gets told, so it can re-add anything it still needs."""
        _fill_to_capacity(mem, "user", durability="until_changed")

        result = mem.add("user", "brand new " + "y" * _BIG,
                         durability="until_changed")

        assert result.ok
        assert "made room" in result.message.lower() or "dropped" in result.message.lower()


class TestTheEvictionRuleIsAgeAndNothingCleverer:
    def test_a_mislabelled_rule_is_protected_by_marking_it_permanent(
        self, mem: CuratedMemory,
    ) -> None:
        """MEASURED on Bakir's real secretary.md, 2026-08-19: making room for one
        note evicted the 840-char stale incident log (rightly) AND "Telegram
        replies must stay under 2048 tokens" — a 61-char rule worth keeping, lost
        for being first in the file.

        A "drop whichever entry frees the space in one go" rule was tried and
        REVERTED: size is not a staleness signal, and it selected the oldest
        sufficient entry — the small rule it was meant to protect. Age is the only
        staleness evidence this format carries. Durability is the control, and it
        works: the same rule marked permanent survives the same pressure.

        THE PROBE SIZE CHANGED IN D08.4, THE SUBJECT DID NOT. It used to be a
        literal 713 characters. D08.4 added a per-entry ceiling
        (``MAX_ENTRY_BUDGET_FRACTION``) because a single write could otherwise
        evict the whole store, and 713 sits just over it for the ``user`` budget —
        so the write was refused and no eviction was triggered at all, which tested
        nothing. The probe is now derived from the ceiling rather than hardcoded,
        so it cannot drift out of step with it again. It is still large enough to
        force eviction, which is what this test is about.

        The ceiling is not arbitrary and it is not tuned to make this pass: measured
        against the live store, the largest real entry is 39.9% of its target's
        budget, so a 50% ceiling refuses ZERO of the 32 entries that exist."""
        mem.add("user", "Telegram replies must stay under 2048 tokens",
                durability="permanent")
        mem.add("user", "a big stale block " + "s" * 600,
                durability="until_changed")

        biggest_allowed = mem._max_entry_chars("user")
        mem.add("user", "the new fact " + "n" * (biggest_allowed - 13),
                durability="until_changed")

        kept = [e.text for e in mem.entries("user")]
        assert any("2048 tokens" in t for t in kept)
        assert not any("a big stale block" in t for t in kept)
        assert mem.used_chars("user") <= mem.budget_for("user")
