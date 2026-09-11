"""A function quiet on EVERY path is not asymmetric, so the asymmetry guard is blind to it.

WHAT THAT COST. `owls/dna_injector.py::inject` appends a behavioural directive to
an owl's system prompt. It is the single event that answers whether the DNA
subsystem — traits, evolution, attribution, a shadow gate, a nightly coordinator,
several thousand lines — has ever changed how an owl behaves. All three of its log
calls were DEBUG, and this deployment has written **0 DEBUG records out of
677,108**.

ESC-116 then asked the operator to choose between narrowing the injector's
deadband, widening trait movement, or RETIRING the whole subsystem, on the stated
premise that it "has never once changed how an owl behaves". MEASURED 2026-09-11,
that premise was false twice over: it cited `_HIGH_THRESHOLD = 0.7` /
`_LOW_THRESHOLD = 0.3`, two constants the injector had stopped using (it consults
`DIRECTIVE_LATCH`, enter 0.62/0.38), and against the band the code ACTUALLY uses
**1,595 of 20,617 recorded outcomes** carry a trait past it — currently, on every
recent day.

`test_a_background_subsystem_that_declines_still_says_so` exists to catch exactly
this family and could not see it, because it looks for a function that is LOUD on
one return and quiet on another. `inject` was quiet on all of them.

REPORTED, NOT GATED, and the reason is the one this repo has paid for repeatedly:
49 functions match across the background packages. A guard failing 49 things that
each look reasonable gets bypassed rather than satisfied, which is why `doc_check`
is also a report. The COUNT is what makes the class drainable.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect
import pathlib
import sys

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _report():
    spec = importlib.util.spec_from_file_location(
        "logging_visibility", _ROOT / "scripts" / "logging_visibility.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["logging_visibility"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.tripwire
def test_the_walk_sees_a_constructed_silent_decider(tmp_path: pathlib.Path) -> None:
    """VACUITY CONTROL on a CONSTRUCTED population, never on the live count.

    A floor under the live number fails the day the class is drained — which is
    the whole point of reporting it. This repo has now paid for that shape twice
    (`test_the_sweep_sees_a_real_population`, and the done-validate ratchet), so
    the control builds its own population instead.
    """
    mod = _report()
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "silent.py").write_text(
        "from x import log\n"
        "def decides(a):\n"
        "    if a:\n"
        "        log.engine.debug('quiet one')\n"
        "        return 1\n"
        "    log.engine.debug('quiet two')\n"
        "    return 2\n",
        encoding="utf-8",
    )
    (pkg / "loud.py").write_text(
        "from x import log\n"
        "def acts(a):\n"
        "    if a:\n"
        "        log.engine.info('loud')\n"
        "        return 1\n"
        "    log.engine.debug('quiet')\n"
        "    return 2\n",
        encoding="utf-8",
    )
    (pkg / "mute.py").write_text(
        "def nothing(a):\n    return a\n", encoding="utf-8"
    )

    found = {k.split("::")[-1] for k, _u, _r in mod.fully_silent(tmp_path, ("pkg",))}

    assert "decides" in found, (
        "the walk cannot see a function whose every log call is DEBUG — which is "
        "the entire population this report exists to name"
    )
    assert "acts" not in found, (
        "a function with a LOUD return was reported as fully silent; that one "
        "belongs to the asymmetry report, and double-counting would inflate both"
    )
    assert "nothing" not in found, (
        "a function that logs NOTHING AT ALL was reported. It is not the shape: "
        "most helpers log nothing and always will, and including them would bury "
        "the ones that tried to say something at a level nobody reads"
    )


@pytest.mark.tripwire
def test_the_report_is_not_a_gate() -> None:
    """It must never fail the build.

    49 live matches. A guard that fails 49 reasonable-looking things is bypassed
    rather than satisfied — the recorded reason `doc_check` is a report too.

    THE FIRST VERSION OF THIS ASSERTION WAS WRONG, and reading it after the edit
    is what showed it: it split the file at `def fully_silent` and checked the
    REMAINDER, which includes `main()` — a function that legitimately calls
    `sys.exit`. It failed on correct code. A guard that fires on correct work is
    the failure this repo pays for most, so it reads the function's OWN body now.
    """
    mod = _report()
    body = ast.unparse(
        next(
            n
            for n in ast.walk(ast.parse(inspect.getsource(mod)))
            if isinstance(n, ast.FunctionDef) and n.name == "fully_silent"
        )
    )
    assert "sys.exit" not in body and "raise " not in body, (
        "the fully-silent walk can now fail the run. It is a report."
    )


@pytest.mark.tripwire
def test_the_injector_says_when_dna_actually_acts() -> None:
    """The defect that produced this file, pinned where it happened.

    `owls` IS in the gate's background set as of this change — it had to be, or
    the report above could not see `inject`, the function it was written for. This
    pins the ONE line whose absence made a live escalation unanswerable.
    """
    import inspect
    import textwrap

    from stackowl.owls import dna_injector as mod

    src = textwrap.dedent(inspect.getsource(mod.DNAPromptInjector.inject))
    tree = ast.parse(src)
    acted = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.args
        and isinstance(n.args[0], ast.Constant)
        and "directives appended" in str(n.args[0].value)
    ]
    assert acted, "the 'directives appended' line is gone — that IS the act"
    assert acted[0].func.attr == "info", (
        f"DNA changing an owl's prompt is logged at {acted[0].func.attr!r}. "
        "Production has written 0 DEBUG records out of 677,108, so at that level "
        "the question 'has DNA ever changed how an owl behaves' is unanswerable — "
        "which is exactly how ESC-116 came to ask the operator to retire the "
        "subsystem on a premise nothing could check."
    )


@pytest.mark.tripwire
def test_the_dead_thresholds_are_gone_and_the_latch_is_the_source() -> None:
    """RETIRED MEANS DELETED, and here the dead constants were load-bearing —
    for a CLAIM, not for behaviour. `_HIGH_THRESHOLD = 0.7` / `_LOW_THRESHOLD =
    0.3` had no reader (measured: they appeared only at their own definition),
    and ESC-116 quoted them as the live band while the injector had consulted
    `DIRECTIVE_LATCH` at 0.62/0.38 since FR-1. A dead constant that a record
    cites as live is worse than one nobody mentions."""
    from stackowl.owls import dna_injector as mod

    src = (_ROOT / "src" / "stackowl" / "owls" / "dna_injector.py").read_text(
        encoding="utf-8"
    )
    assert "_HIGH_THRESHOLD" not in src and "_LOW_THRESHOLD" not in src, (
        "the superseded 0.7/0.3 constants are back in the injector. The live band "
        "is the latch's, and two sources for one threshold is how a record comes "
        "to quote the wrong one."
    )
    assert "DIRECTIVE_LATCH" in src, "the injector no longer consults the latch"
    assert not hasattr(mod, "_HIGH_THRESHOLD")
