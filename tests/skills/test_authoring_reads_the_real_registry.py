"""D09.5 — the authoring gate must consult the REAL tool registry, and an
empty answer must SKIP the rule rather than fail every write.

THE OUTAGE THIS PINS. `agent:synthesizer` authored 101 skills and then stopped
dead: its last successful create was 2026-08-07T08:30:40Z. It kept running every
night — 41 `[synth] synthesize_one: gated write refused — skipping` lines across
the retained 9-day window, five per night, including today — and the scheduler
reported the job COMPLETED each time. Sixteen days of a silently dead
self-improvement loop, and nothing noticed.

The dominant refusal reason was::

    does not meet the authoring standard (v1): tool_names: backticked token(s)
    that are not registered tools: web_fetch, web_search

`web_fetch` is in the guaranteed always-present tool base set. It cannot be
unregistered. The registry the validator consulted was EMPTY.

ROOT CAUSE, and it is two defects stacked::

    names = getattr(registry, "names", None)
    return frozenset(names() if callable(names) else names or ())

1. ``ToolRegistry`` has no ``names`` member at all — its accessor is ``all()``
   returning ``list[Tool]``. ``hasattr(registry, "names")`` is False, so the
   getattr yields None.
2. ``None or ()`` is ``()``, so the function returns an EMPTY FROZENSET — never
   the ``None`` its own docstring promises. `standard.validate_body` takes the
   ``known_tools is not None`` branch and every backticked token in every skill
   body becomes a violation.

So the designed fail-open ("a registry that is unavailable must never block the
agent from learning") was unreachable. It failed CLOSED, at WARNING, forever.

WHY NO TEST CAUGHT IT — and why this file exists. Every one of the twelve
``known_tools=`` call sites in ``test_authoring_standard.py`` passes a literal
``frozenset({...})``. Not one constructs a real ``ToolRegistry``. The validator
was covered; the BRIDGE from the registry to the validator was not, and the bug
lived entirely in the bridge. That is CLAUDE.md's second defect shape — a test
double that stopped resembling the real thing — so these tests deliberately use
a real registry and derive their expectations from the same source the code
reads, never from a hand-written list of tool names.
"""

from __future__ import annotations

import pytest

from stackowl.skills import authoring, standard
from stackowl.tools.registry import ToolRegistry


class _NoAccessorRegistry:
    """A registry object that can answer nothing. Neither `names` nor `all`."""


class _EmptyAllRegistry:
    """Present and callable, but genuinely holds no tools."""

    def all(self) -> list[object]:
        return []


class _NamesAccessorRegistry:
    """A duck-typed registry exposing `names()` — the shape the old code assumed.

    Kept working on purpose: the fix must widen what is accepted, not swap one
    hard-coded accessor for another.
    """

    def all(self) -> list[object]:  # pragma: no cover — names() wins
        return []

    def names(self) -> list[str]:
        return ["alpha_tool", "beta_tool"]


def _services_with(monkeypatch: pytest.MonkeyPatch, registry: object) -> None:
    class _Services:
        tool_registry = registry

    monkeypatch.setattr(
        "stackowl.pipeline.services.get_services", lambda: _Services(), raising=False
    )


# ---------------------------------------------------------------------------
# The bridge: a real registry must actually be read
# ---------------------------------------------------------------------------

def test_a_real_ToolRegistry_yields_its_real_tool_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exact production failure, reproduced against the real class.

    Expectation derived from the registry itself — never a literal list — so
    this cannot drift from the code the way the old fixtures did.
    """
    registry = ToolRegistry.with_defaults()
    expected = {t.name for t in registry.all()}
    assert expected, "a defaults registry with no tools would make this vacuous"

    _services_with(monkeypatch, registry)
    got = authoring._known_tool_names()

    assert got is not None, "a populated registry must NOT read as 'unknown'"
    assert got == frozenset(expected)


def test_the_tool_that_the_outage_rejected_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`web_fetch` was refused 5 nights a week as 'not a registered tool'."""
    registry = ToolRegistry.with_defaults()
    if "web_fetch" not in {t.name for t in registry.all()}:
        pytest.skip("web_fetch not in this build's defaults")

    _services_with(monkeypatch, registry)
    got = authoring._known_tool_names()
    assert got is not None and "web_fetch" in got


def test_a_names_accessor_is_still_honoured(monkeypatch: pytest.MonkeyPatch) -> None:
    """Widen, do not swap: a registry exposing `names()` keeps working."""
    _services_with(monkeypatch, _NamesAccessorRegistry())
    assert authoring._known_tool_names() == frozenset({"alpha_tool", "beta_tool"})


# ---------------------------------------------------------------------------
# The fail-open: empty is NOT knowledge
# ---------------------------------------------------------------------------

def test_an_unanswerable_registry_returns_None_not_an_empty_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """THE ROOT CAUSE. `frozenset()` means "every tool name is wrong"; `None`
    means "do not judge tool names". The docstring promises the second."""
    _services_with(monkeypatch, _NoAccessorRegistry())
    assert authoring._known_tool_names() is None


def test_a_registry_holding_no_tools_also_returns_None(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a well-formed registry that is simply empty must skip the rule.

    This is the half that would still have bitten after only fixing the
    accessor: during boot, or with a registry not yet populated, `all()`
    legitimately returns [] and the old code would fail every write again.
    """
    _services_with(monkeypatch, _EmptyAllRegistry())
    assert authoring._known_tool_names() is None


def test_no_services_at_all_returns_None(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> object:
        raise RuntimeError("services not initialised")

    monkeypatch.setattr(
        "stackowl.pipeline.services.get_services", _boom, raising=False
    )
    assert authoring._known_tool_names() is None


def test_a_missing_tool_registry_attribute_returns_None(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _services_with(monkeypatch, None)
    assert authoring._known_tool_names() is None


# ---------------------------------------------------------------------------
# End to end: the body the synthesizer kept getting refused
# ---------------------------------------------------------------------------

_BODY_CITING_A_REAL_TOOL = """\
# Scheduled Web Check

## When to Use
When a recurring check of a web page is needed.

## Prerequisites
None.

## How to Run
Call `web_fetch` with the target URL.

## Quick Reference
`web_fetch` — retrieve a page.

## Procedure
1. Call `web_fetch`.
2. Compare against the previous result.

## Pitfalls
Do not poll faster than the page changes.

## Verification
The fetched content is returned.
"""


def test_a_body_citing_a_registered_tool_is_NOT_a_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The end-to-end shape of 41 refusals."""
    registry = ToolRegistry.with_defaults()
    if "web_fetch" not in {t.name for t in registry.all()}:
        pytest.skip("web_fetch not in this build's defaults")
    _services_with(monkeypatch, registry)

    known = authoring._known_tool_names()
    rules = {v.rule for v in standard.validate_body(
        _BODY_CITING_A_REAL_TOOL, known_tools=known
    )}
    assert "tool_names" not in rules, (
        "citing a genuinely registered tool must never be a tool_names violation"
    )


def test_an_unanswerable_registry_does_not_manufacture_tool_name_violations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fail-open, proven where it matters — at the validator, not just at
    the accessor."""
    _services_with(monkeypatch, _EmptyAllRegistry())
    known = authoring._known_tool_names()
    rules = {v.rule for v in standard.validate_body(
        _BODY_CITING_A_REAL_TOOL, known_tools=known
    )}
    assert "tool_names" not in rules
