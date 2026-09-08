"""A planned envelope must fit the model's window, not just the count cap.

ESC-35, answered 2026-08-23: *"Demote `tool_count_cap` to an opt-out ceiling and
size the presented set against the resolved model window."* DEBT-238 did the
demotion. This is the other half, and it was never built.

MEASURED 2026-09-08 against the real 77-tool catalogue. The `restrict_to` branch
of `execute.py` called `to_provider_schema` with `max_tools` and NO `budget`:

    restrict_to, max_tools=40  ->  40 tools   (window ignored)
    restrict_to, max_tools=150 ->  77 tools   (window ignored)
    budgeted,    window=4096   ->  20 tools

`_window` is resolved immediately above that branch — an awaited call that may
probe the provider — and then DISCARDED. A resolved value with no reader.

WHOSE FAULT THE URGENCY IS. DEBT-238 raised the shipped cap from 40 to 150. On the
budgeted path that is safe and was measured: the window clips to the 20-tool
guaranteed set at <= 8192 whatever the cap says. On THIS path nothing clips, so the
same commit took a lean-window model from 40 schemas to 77. The DEBT-238 safety
measurement covered the budgeted path only and reported the change safe; it was
safe on the path it measured. That is why these tests exercise the ENVELOPE path
specifically.

WHY A FIT AND NOT A NEW LEAN CONSTANT. The budgeted path's lean behaviour is
DERIVED: `fit_items` never drops the guaranteed set and no discretionary tool fits
a small budget, so its "20" IS the guaranteed set (measured: guaranteed=20,
ranked=57, catalogue=77). A lean ceiling written here would be a second copy of
that rule, free to disagree. The same `tool_budget_tokens` + `fit_items` are used
instead, so the two paths agree by construction.
"""

from __future__ import annotations

import pytest

from stackowl.tools.registry import ToolRegistry


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    return ToolRegistry.with_defaults()


@pytest.fixture(scope="module")
def envelope(registry: ToolRegistry) -> frozenset[str]:
    """The self-heal ban shape: D05.8 records it producing 75-78 names."""
    return frozenset(t.name for t in registry.all())


def _present(registry, envelope, *, window=None, fixed_cost=0, max_tools=150):
    budget = (
        None if window is None
        else {"window": window, "fixed_cost_tokens": fixed_cost, "max_tools": max_tools}
    )
    return registry.to_provider_schema(
        "openai", restrict_to=envelope, max_tools=max_tools, budget=budget
    )


def test_a_lean_window_narrows_the_envelope(registry, envelope) -> None:
    """THE DEFECT. A 4k-window model was handed the entire envelope."""
    lean = _present(registry, envelope, window=4096, fixed_cost=1024)
    unbounded = _present(registry, envelope, window=None)

    assert len(lean) < len(unbounded), (
        f"a 4096-token window presented {len(lean)} tool schemas, the same as the "
        f"no-window path ({len(unbounded)}) — the envelope is still sized by count "
        f"alone and the resolved window is still being discarded"
    )


def test_a_wide_window_is_left_alone(registry, envelope) -> None:
    """NO REGRESSION FOR CAPABLE MODELS, and the other half of the denominator.

    A test that only asserts 'lean is smaller' passes for a change that shrinks
    EVERY turn. The whole point is that the bound is window-relative.
    """
    wide = _present(registry, envelope, window=262_144, fixed_cost=1024)
    unbounded = _present(registry, envelope, window=None)
    assert len(wide) == len(unbounded), (
        f"a 262k window presented {len(wide)} of {len(unbounded)} — the fit is "
        f"clipping a model that can afford the whole envelope"
    )


def test_discovery_survives_a_window_that_fits_nothing(registry, envelope) -> None:
    """THE FLOOR. `always_present` is non-evictable on this path by construction;
    a fit that could starve it would remove the turn's only way to ask for more."""
    from stackowl.tools._infra.presentation import PresentationConfig

    starved = _present(registry, envelope, window=512, fixed_cost=99_999)
    names = {s.get("function", s).get("name") for s in starved}  # type: ignore[union-attr]
    always = PresentationConfig().always_present
    present_always = {n for n in names if n in always}
    assert present_always, (
        "a negative token budget dropped every discovery tool; the model cannot "
        "even reach tool_search to recover"
    )


def test_the_order_is_preserved_not_reranked(registry, envelope) -> None:
    """`fit_items` returns guaranteed FIRST, which would reorder a set `select()`
    deliberately orders. It is a no-op here only because `select()` already emits
    `always_present` at the front — measured, 5 of 38, all leading. If either side
    changes, the envelope silently starts arriving in a different order, and that
    is a prompt-cache defect (D05.2), not a cosmetic one."""
    wide = _present(registry, envelope, window=262_144, fixed_cost=1024)
    unbounded = _present(registry, envelope, window=None)

    def names(schemas):
        return [s.get("function", s).get("name") for s in schemas]

    assert names(wide) == names(unbounded), (
        "the fitted envelope is in a different ORDER from the unfitted one; the "
        "candidates are being re-ranked rather than merely narrowed"
    )


def test_without_a_budget_the_path_is_byte_identical(registry, envelope) -> None:
    """BACKWARD COMPATIBILITY, and it is what keeps this change safe to land.

    Every existing caller that passes `restrict_to` without a `budget` — several
    tests do — must be untouched. The fit is opt-in on the budget's presence.
    """
    a = _present(registry, envelope, window=None, max_tools=40)
    b = registry.to_provider_schema("openai", restrict_to=envelope, max_tools=40)
    assert a == b


def test_the_cut_leaves_a_witness(registry, envelope, caplog) -> None:
    """A CUT WITH NO WITNESS IS THE SAME DEFECT AS A WRITE WITH NO READER.

    Its sibling in `select()` says exactly that, and earned it: that line fired
    ZERO times in eight days while its counterpart fired 955, because the branch
    returned above both log sites. A window-driven cut is a cut, and production
    runs at INFO, so a DEBUG line here would not exist when it was needed.
    """
    import logging

    with caplog.at_level(logging.INFO):
        _present(registry, envelope, window=4096, fixed_cost=1024)

    hits = [r for r in caplog.records if "does not fit the model" in r.getMessage()]
    assert hits, (
        "the window dropped tools and said nothing; the operator cannot tell a "
        "narrowed envelope from a small plan"
    )
    fields = getattr(hits[0], "_fields", {})
    for key in ("dropped_count", "presented", "planned", "window"):
        assert key in fields, f"the witness omits {key!r}: {fields}"
    assert fields["dropped_count"] > 0
    assert fields["presented"] + fields["dropped_count"] == fields["planned"], (
        f"the witness does not add up: {fields}"
    )
