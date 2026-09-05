"""D18.8 — the trace win is real, and nothing would have noticed it regressing.

D18.8 is an AHEAD item, so its job is to VERIFY the claim rather than restate it.
Verified against the live platform on 2026-09-05, and it holds:

    cost records with a BLANK trace_id
      2026-08-29 (when the defect was found)   54.5%   67,383 of 123,648
      2026-08-30                                 3%
      2026-08-31 .. 2026-09-04                 0-2%
      2026-09-05                                 0%    (0 of 103)

A real conversational turn produces a properly nested tree — 56 records across 11
spans, `triage` and `dispatch` and `memory.recall` all parented to the backend run
span. The propagation the map claims is genuinely there.

**WHAT IS MISSING IS THE ALARM.** The 54.5% was found because a human happened to
query for it. `_bind_job_trace` is pinned by three tests, but those pin the CODE —
that a scheduled job binds a lane. Nothing watches the EFFECT, so a new background
caller that reaches a provider outside any TraceContext would reappear exactly as
the first one did: silently, and visible only to whoever thought to look.

That is this repo's guide star asked of its own observability: if this degrades
silently, what notices? Measured answer, before this: nothing.

THE RESIDUAL IS REAL BUT SMALL AND SHRINKING, and it is characterised rather than
guessed: 142 records since 2026-08-30, **every one with `system_prompt_chars = 0`**,
no owl, 180-244 input tokens, falling from 107 on 08-30 to 1 on 09-04. A utility
call with no persona. It is NOT attributed to a specific caller here, because
`cost_records` carries no column naming the code path that made the call — which is
precisely why the first 54.5% needed a person to reason it out, and is recorded as
the open question rather than a guess (ESC-140).
"""

from __future__ import annotations

import pytest

from stackowl.health.contributors import UnattributedSpendContributor


class _FakeDb:
    def __init__(self, rows: list[dict] | Exception) -> None:
        self._rows = rows

    async def fetch_all(self, *_args: object, **_kwargs: object) -> list[dict]:
        if isinstance(self._rows, Exception):
            raise self._rows
        return self._rows


@pytest.mark.asyncio
async def test_a_clean_window_reports_ok_with_its_denominator() -> None:
    """The OK case must say what it looked at.

    A healthy report that cannot say its denominator is the trap this repo has
    already paid for: `store_cadence`'s first live run returned a clean zero while
    a date bug had skipped almost every store it claimed to cover.
    """
    contributor = UnattributedSpendContributor(_FakeDb([{"total": 500, "blank": 3}]))
    status = await contributor.health_check()

    assert status.status == "ok"
    assert "500" in (status.message or ""), "the OK case must carry its denominator"


@pytest.mark.asyncio
async def test_a_regression_is_reported_degraded() -> None:
    """The 54.5% shape must raise its hand."""
    contributor = UnattributedSpendContributor(_FakeDb([{"total": 1000, "blank": 545}]))
    status = await contributor.health_check()

    assert status.status == "degraded"
    assert "54" in (status.message or "")


@pytest.mark.asyncio
async def test_a_tiny_window_is_never_degraded() -> None:
    """Three records, two blank, is 67% and means nothing.

    A denominator too small to carry a rate is the "0 exemptions over 7 browser
    calls" shape: a ratio computed over almost nothing is not a measurement.
    """
    contributor = UnattributedSpendContributor(_FakeDb([{"total": 3, "blank": 2}]))
    status = await contributor.health_check()

    assert status.status == "ok"
    assert "too few" in (status.message or "").lower()


@pytest.mark.asyncio
async def test_an_instrument_failure_is_not_reported_as_a_regression() -> None:
    """"I could not measure it" and "it has regressed" are different claims.

    Reporting the first as the second is the instrument lying, which this repo has
    paid for — and it is why the sibling contributor says the same thing in its own
    docstring.
    """
    contributor = UnattributedSpendContributor(_FakeDb(RuntimeError("db gone")))
    status = await contributor.health_check()

    assert status.status == "degraded", "an unmeasurable check is degraded, never down"
    assert "could not" in (status.message or "").lower()


def test_the_contributor_is_registered_on_the_health_sweep() -> None:
    """Built-but-not-wired is the shape this whole item is about.

    A contributor that exists and is never registered watches nothing, which would
    make this test file decoration about decoration.
    """
    import ast
    import pathlib

    assembly = pathlib.Path(__file__).resolve().parents[2] / "src" / "stackowl" / "scheduler" / "assembly.py"
    tree = ast.parse(assembly.read_text(encoding="utf-8"))

    # ASSERT THE REGISTER CALL, NOT THE MENTION. The first version of this test
    # searched the file for the class NAME — and mutation testing caught it: deleting
    # the `agg.register(...)` line left the IMPORT behind, so the name was still
    # present and the test still passed. It was guarding a string, not a wiring.
    registered = any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "register"
        and any(
            isinstance(arg, ast.Call)
            and isinstance(arg.func, ast.Name)
            and arg.func.id == "UnattributedSpendContributor"
            for arg in node.args
        )
        for node in ast.walk(tree)
    )
    assert registered, (
        "UnattributedSpendContributor is never passed to agg.register() in scheduler "
        "assembly — it would exist and watch nothing, which is the built-but-not-wired "
        "shape this contributor was written to detect in the first place"
    )
