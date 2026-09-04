"""A caller pacing a fill against the public budget spins forever.

FOUND BY THE FULL SUITE, which is the only thing that could find it. Launched
2026-09-04, the run reached 78% and stayed there: 30 minutes at one marker,
child process pinned at 65% CPU with its CPU time advancing second for second.
Not hung — spinning. ``py-spy dump`` named it exactly::

    test_remember_can_refuse_the_user_too   (tests/test_story_6_7.py:93)
      -> curated.add                        (memory/curated.py:730)
        -> _at_capacity                     (memory/curated.py:943)
          -> log.warning -> json.dumps

The loop::

    while mem.used_chars(USER_TARGET) < mem.budget_for(USER_TARGET) - 80:
        mem.add(USER_TARGET, f"Existing entry number {i}...", "permanent")

THE ARITHMETIC MAKES IT INFINITE BY CONSTRUCTION. ``budget_for`` and
``used_chars`` are WHOLE-FILE numbers; ``add`` admits a ``permanent`` write only
while the projected file stays under ``_effective_budget``, which is the budget
minus the 25% reserve that exists so the decaying tier always has somewhere to
live. Measured::

    USER_BUDGET_CHARS      1375
    permanent ceiling      1031   (75%)
    loop target            1295   (budget - 80)

1031 < 1295, so permanent-only writes stop growing the file at 1031 and the
condition never becomes false. Every further iteration is refused, logs a
warning, and serialises it to JSON — which is the spin.

THE CAUSE IS AN ASYMMETRY IN THE PUBLIC API, not a typo in the test. A write is
admitted against a PER-DURABILITY ceiling, and the only public numbers describe
the WHOLE FILE. There was no way to ask "how much room does a write of THIS
durability still have", so any caller pacing a loop on what the class exposes
gets this wrong. ``_effective_budget`` knew the answer and was private.

THIS IS THE SECOND CASUALTY OF ONE MISMATCH. TIERFULL (2026-09-01) found the same
per-tier/whole-file confusion in the REPORTING — "add() handed the RESERVED
CEILING to _at_capacity, whose entire vocabulary ... is about the WHOLE FILE" —
and fixed the message. The API asymmetry underneath it stayed, and this test has
been spinning since. Nobody saw it because ``tests/test_story_6_7.py`` sits
directly in ``tests/``, which no package path runs, so only a full suite reaches
it — and this test is what prevented any full suite from finishing.
"""

from __future__ import annotations

import pytest

from stackowl.memory.curated import (
    UNTIL_CHANGED_RESERVE_SHARE,
    USER_BUDGET_CHARS,
    USER_TARGET,
    CuratedMemory,
)

#: The measured numbers above, so a change to the reserve fails here loudly.
PERMANENT_CEILING = int(USER_BUDGET_CHARS * (1.0 - UNTIL_CHANGED_RESERVE_SHARE))


@pytest.fixture()
def mem(tmp_path) -> CuratedMemory:  # type: ignore[no-untyped-def]
    return CuratedMemory(root=tmp_path / "memory")


def _fill_permanent(mem: CuratedMemory, *, stop_at: int = 80, cap: int = 500) -> int:
    """Fill the permanent tier with UNIQUE entries until it is (nearly) full.

    UNIQUE, and that is the point of the helper. ``add`` short-circuits a
    duplicate — "Entry already present — nothing to do" — WITHOUT writing, so a
    loop that re-adds identical text never grows the file and never terminates.
    The first draft of this suite did exactly that and spun for three minutes at
    99% CPU, reproducing the bug it was written to guard, and ``py-spy`` named it
    the same way it named the original.

    Hard-bounded as well: a test that can hang is worse than the defect it
    protects against, and this file exists because one such test made every full
    suite run impossible.
    """
    written = 0
    for i in range(cap):
        if mem.headroom_for(USER_TARGET, "permanent") <= stop_at:
            return written
        mem.add(USER_TARGET, f"Existing entry number {i} about how I work.", "permanent")
        written += 1
    raise AssertionError(
        f"the permanent tier never filled in {cap} writes — headroom_for is not shrinking"
    )


# --------------------------------------------------------------------------- #
# The regression                                                               #
# --------------------------------------------------------------------------- #


def test_a_permanent_fill_loop_terminates(mem: CuratedMemory) -> None:
    """THE DEFECT. Paced against the tier it actually writes to, the fill ends."""
    written = _fill_permanent(mem)

    assert written > 0, "nothing was written at all"
    assert mem.headroom_for(USER_TARGET, "permanent") <= 80


def test_headroom_is_per_durability_not_whole_file(mem: CuratedMemory) -> None:
    """THE ASYMMETRY, stated. On an empty store the two durabilities already
    disagree by the reserve — the number the old loop was blind to."""
    assert mem.headroom_for(USER_TARGET, "until_changed") == USER_BUDGET_CHARS
    assert mem.headroom_for(USER_TARGET, "permanent") == PERMANENT_CEILING
    assert mem.headroom_for(USER_TARGET, "permanent") < mem.budget_for(USER_TARGET)


def test_headroom_never_goes_negative(mem: CuratedMemory) -> None:
    """A target already OVER its ceiling is a live condition — three were measured
    on 2026-09-01. A negative headroom would make ``while headroom > n`` true
    again and spin exactly as before."""
    # Push the file PAST the permanent ceiling the way the live ones got there —
    # written before the reserve existed, which `over_ceiling_targets` reports on
    # (user 1,246 against a 1,031 ceiling, measured 2026-09-01). The admission
    # gate cannot produce this state, so it is written directly.
    from stackowl.memory.curated import Entry

    mem._write(  # noqa: SLF001 — reproducing a state the gate refuses to create
        USER_TARGET,
        [Entry(text="x" * 200, durability="permanent") for _ in range(8)],
    )
    assert mem.used_chars(USER_TARGET) > PERMANENT_CEILING, "the setup did not go over"

    assert mem.headroom_for(USER_TARGET, "permanent") == 0, (
        "an over-ceiling target reported negative headroom, which would make "
        "`while headroom > n` true again and spin exactly as the original did"
    )


def test_a_write_is_refused_exactly_when_headroom_says_so(mem: CuratedMemory) -> None:
    """The number must agree with the gate it describes, or it is a second
    opinion rather than an answer."""
    _fill_permanent(mem)

    assert mem.add(USER_TARGET, "y" * 300, "permanent").ok is False


def test_the_reserve_still_admits_the_tier_it_protects(mem: CuratedMemory) -> None:
    """The reserve exists so the decaying tier always has somewhere to live. A
    file full to the PERMANENT ceiling must still accept an ``until_changed``
    write, or this fix would have closed the door the reserve holds open."""
    _fill_permanent(mem)

    assert mem.headroom_for(USER_TARGET, "until_changed") > 0
    assert mem.add(USER_TARGET, "I prefer terse replies", "until_changed").ok is True


def test_a_duplicate_write_does_not_move_the_headroom(mem: CuratedMemory) -> None:
    """WHY THE HELPER INSISTS ON UNIQUE TEXT, pinned so the next reader does not
    rediscover it by hanging. ``add`` reports success for a duplicate and writes
    nothing, so a fill loop using constant text makes no progress at all."""
    mem.add(USER_TARGET, "I use uv, not npm", "permanent")
    before = mem.headroom_for(USER_TARGET, "permanent")

    result = mem.add(USER_TARGET, "I use uv, not npm", "permanent")

    assert result.ok is True, "a duplicate is reported as success"
    assert mem.headroom_for(USER_TARGET, "permanent") == before, (
        "a duplicate changed the headroom — the helper's uniqueness would be moot"
    )


# --------------------------------------------------------------------------- #
# The sweep, made executable                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.tripwire
def test_no_fill_loop_paces_against_the_whole_file_budget() -> None:
    """TWO tests spun on this exact shape before anyone swept for it.

    The first (``tests/test_story_6_7.py``) froze the full suite at 78% for 30
    minutes. It was fixed, the suite relaunched, and it froze again at 86% for 13
    minutes on the second (``tests/tools/knowledge/test_memory_curated_writes.py``)
    — same condition, different file, found only because the run was watched a
    second time. Fixing the first instance without sweeping for siblings cost a
    full hour of suite time.

    THE SHAPE: a ``while`` whose condition asks ``budget_for`` — the WHOLE-FILE
    number — to pace a loop whose body writes a durability the gate caps lower.
    It cannot terminate, and it burns CPU logging a refusal per pass.

    Asserted over the whole tree rather than remembered, because "remember to
    check the other one" is exactly what did not happen."""
    import ast as _ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]           # tests/
    src = root.parent / "src"
    offenders: list[str] = []
    for base in (root, src):
        for path in base.rglob("*.py"):
            try:
                tree = _ast.parse(path.read_text(errors="ignore"))
            except SyntaxError:  # pragma: no cover — the syntax gate covers this
                continue
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.While):
                    continue
                cond = _ast.unparse(node.test)
                if "budget_for" in cond and "headroom_for" not in cond:
                    offenders.append(f"{path.relative_to(base.parent)}:{node.lineno}  while {cond}")
    assert not offenders, (
        "a loop paces on the whole-file budget while its body writes a "
        "durability the admission gate caps lower — this spins forever:\n  "
        + "\n  ".join(offenders)
    )
