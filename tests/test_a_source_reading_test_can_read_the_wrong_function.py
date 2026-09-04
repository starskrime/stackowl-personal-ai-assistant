"""`inspect.getsource` reads the FILE; the test holds a module imported earlier.

MEASURED 2026-09-04. The full suite came back `3 failed` where the previous run
was green, all three in `tests/skills/test_canonical_dedup.py`. The assertion
message said the canonical-dedup rung had been dropped. It had not. The text
those tests read back was the body of a completely different function::

    E  assert 'op="reinforce"' in '        log.skills.info(
           "[synth] discover: exit", ...

The suite had been running detached for 30 minutes while D10.3 added ~97 lines
to `synthesizer.py`. `inspect.getsource(fn)` starts at ``fn.__code__.co_firstlineno``
— a line number fixed when the module was imported — and reads the block found
there in the file AS IT IS NOW. Shift the file and the read lands somewhere else,
silently.

TWO DIRECTIONS, AND THE QUIET ONE IS WORSE. A shifted read that lands on text
NOT containing the asserted token is a false RED with a misleading message,
which is what happened and cost a diagnosis. A shifted read that lands on text
that HAPPENS to contain it is a false GREEN — and across 139 call sites in 82
test files asserting short tokens like `owner_id` or `op="reinforce"` against
large modules, that is not a remote possibility.

The guard is not "don't edit while the suite runs" — that is a rule about
behaviour, and this programme has learned that a rule with no enforcement is a
rule nobody follows. It is that a read which lands on the wrong function must
SAY SO, wherever and whenever it happens.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tests._source_helpers import _real_getsource, guarded_getsource


def _write_module(path: Path, padding: int) -> None:
    path.write_text(
        "\n".join(["# pad"] * padding) + textwrap.dedent('''
        def the_one_we_want():
            """marker: WANTED"""
            return 1


        def a_completely_different_function():
            """marker: OTHER"""
            return 2
        '''),
        encoding="utf-8",
    )


def _import_from(path: Path, name: str):  # noqa: ANN202
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_a_shifted_file_makes_the_raw_read_lie(tmp_path: Path) -> None:
    """The bug itself, reproduced. Not a hypothetical: this is the 2026-09-04 suite."""
    p = tmp_path / "shifty.py"
    _write_module(p, padding=0)
    mod = _import_from(p, "shifty")

    _write_module(p, padding=40)  # the edit, mid-run

    # The RAW function: conftest installs the guard over `inspect.getsource` for
    # the whole session, and this test's subject is the unguarded behaviour it
    # exists to catch. That the guard is in the way here is the proof it is on.
    src = _real_getsource(mod.the_one_we_want)

    # MEASURED, and worse than "it returns a different function". The read
    # starts 40 lines early and `getblock` keeps consuming until the block is
    # syntactically complete, so it returns junk PLUS the real body:
    assert src.lstrip().startswith("# pad"), (
        "the read must be shown to begin somewhere other than the declaration; "
        "if this ever fails, getsource stopped trusting stale line numbers and "
        "this guard is no longer needed"
    )
    assert "WANTED" in src, (
        "THE FALSE GREEN, demonstrated: a shifted read swept the wanted function "
        "up along with the junk, so `assert 'WANTED' in src` would have PASSED "
        "while reading from the wrong offset. Every one of the 139 live call "
        "sites asserts exactly that shape."
    )


def test_the_guard_names_what_actually_happened(tmp_path: Path) -> None:
    p = tmp_path / "shifty2.py"
    _write_module(p, padding=0)
    mod = _import_from(p, "shifty2")
    _write_module(p, padding=40)

    with pytest.raises(AssertionError, match="the_one_we_want"):
        guarded_getsource(mod.the_one_we_want)


def test_the_guard_is_transparent_when_the_file_is_stable(tmp_path: Path) -> None:
    """The control. A guard that fires on a healthy read is worse than none."""
    p = tmp_path / "stable.py"
    _write_module(p, padding=7)
    mod = _import_from(p, "stable")

    assert "WANTED" in guarded_getsource(mod.the_one_we_want)


def test_the_guard_handles_the_shapes_this_repo_actually_reads() -> None:
    """Decorated functions, methods, classes and modules — every form the 139
    live call sites use. A guard that only understood plain functions would fail
    open on exactly the code this repo writes."""
    from stackowl.skills.loader import SkillLoader, _stray_skill_dirs
    from stackowl.skills.synthesizer import SkillSynthesizer

    assert "_stray_skill_dirs" in guarded_getsource(_stray_skill_dirs)
    assert "class SkillLoader" in guarded_getsource(SkillLoader)
    assert "_reinforce_if_known" in guarded_getsource(
        SkillSynthesizer._reinforce_if_known
    )
    # A module has no name to check against; the guard must pass it through
    # rather than invent a rule for it.
    import stackowl.skills.loader as loader_mod

    assert "SkillLoader" in guarded_getsource(loader_mod)


def test_the_guard_fails_open_on_anything_it_cannot_parse() -> None:
    """It must never turn an unusual-but-fine read into a failure — pytest itself
    calls getsource, and a guard that raises inside the runner is worse than the
    bug it prevents."""

    class _Odd:
        __name__ = "not_matching_anything"

    # No source at all: the underlying error must surface unchanged, not be
    # replaced by this guard's message.
    with pytest.raises((OSError, TypeError)):
        guarded_getsource(_Odd())
