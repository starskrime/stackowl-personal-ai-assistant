"""The eviction line must name the constraint that actually bound.

ESC-9 added this line because the budget "dropped tools silently, so an operator asking
'why can't my browser owl type?' had nothing to read". It is at INFO for exactly the
right reason. But it says:

    eligible tools NOT presented — the turn's token budget could not fit them

and on this deployment that is the WRONG CAUSE. Measured 2026-08-21 across the retained
logs: 266 events today, every one reporting `presented == hard_cap == 30`. The operator
set `tool_count_cap: 30` in `~/.stackowl/stackowl.yaml`; the tool schemas serialise to
~7,524 tokens against a 262,144-token window, so the token budget was never close to
binding. The COUNT cap bound, every time.

So the line sends the one person it was written for — an operator asking why a tool
vanished — to look at a token budget that is 2.87% consumed, when the answer is a config
key they set. That is worse than silence, because it is confidently wrong.

The fix is not a reworded string: the code must DECIDE which constraint bound and say
that one. Both are real and either can bind, so the message is derived, not fixed.
"""

from __future__ import annotations

import logging

import pytest

from stackowl.tools.base import Tool, ToolManifest, ToolResult
from stackowl.tools.registry import ToolRegistry


class _T(Tool):
    """A REAL `Tool`, not a hand-rolled double.

    A first draft invented a double and then grew it one attribute at a time as the
    registry reached for `manifest`, then `manifest.consent_category`, … That is how a
    double stops resembling the real thing — the second of this repo's four recurring
    defect shapes — and here it would have been resembling the very class whose
    presentation path is under test. Subclassing costs nothing and cannot drift.
    """

    def __init__(self, name: str, desc: str = "d") -> None:
        self._m = ToolManifest(
            name=name, description=desc,
            parameters={"type": "object", "properties": {}},
            toolset_group="misc",
        )

    @property
    def manifest(self) -> ToolManifest:
        return self._m

    async def execute(self, **kwargs: object) -> ToolResult:  # pragma: no cover
        raise NotImplementedError


def _messages(caplog) -> list[str]:
    return [r.message for r in caplog.records if "NOT presented" in r.message]


def _fields(caplog) -> dict:
    for r in caplog.records:
        if "NOT presented" in r.message:
            return getattr(r, "_fields", {})
    return {}


class TestItNamesTheConstraintThatBound:
    def test_a_count_cap_is_reported_as_a_count_cap(self, caplog) -> None:
        """THE LIVE CASE. 30 presented, 30 cap, a window nowhere near full."""
        caplog.set_level(logging.INFO)
        reg = ToolRegistry()
        for i in range(60):
            reg.register(_T(f"tool_{i:02d}"))

        reg.to_provider_schema(
            "openai",
            budget={"window": 262_144, "fixed_cost_tokens": 100, "max_tools": 30},
        )

        msgs = _messages(caplog)
        assert msgs, "tools were dropped and nothing was logged"
        assert "token budget" not in msgs[0], (
            f"blames the token budget when the COUNT cap bound: {msgs[0]}"
        )
        assert _fields(caplog).get("limited_by") == "tool_count_cap"

    def test_a_real_token_squeeze_is_still_reported_as_one(self, caplog) -> None:
        """The other half must not regress. A tiny window with a generous count cap
        genuinely is a token problem, and must still say so."""
        caplog.set_level(logging.INFO)
        reg = ToolRegistry()
        for i in range(60):
            reg.register(_T(f"tool_{i:02d}", desc="x" * 400))

        reg.to_provider_schema(
            "openai",
            budget={"window": 2_000, "fixed_cost_tokens": 100, "max_tools": 500},
        )

        assert _fields(caplog).get("limited_by") == "token_budget"

    def test_the_dropped_list_says_it_is_truncated(self, caplog) -> None:
        """`dropped` is sliced to 20 while `dropped_count` carries the real figure. I
        read the truncated list as the answer once while measuring this very defect, so
        the field now says what it is."""
        caplog.set_level(logging.INFO)
        reg = ToolRegistry()
        for i in range(60):
            reg.register(_T(f"tool_{i:02d}"))

        reg.to_provider_schema(
            "openai",
            budget={"window": 262_144, "fixed_cost_tokens": 100, "max_tools": 30},
        )

        f = _fields(caplog)
        assert f.get("dropped_count", 0) > len(f.get("dropped", [])), (
            "this case is meant to exercise truncation"
        )
        assert f.get("dropped_truncated") is True

    def test_nothing_is_logged_when_everything_fits(self, caplog) -> None:
        """A line that fires every turn stops being read."""
        caplog.set_level(logging.INFO)
        reg = ToolRegistry()
        for i in range(5):
            reg.register(_T(f"tool_{i}"))

        reg.to_provider_schema(
            "openai",
            budget={"window": 262_144, "fixed_cost_tokens": 100, "max_tools": 30},
        )

        assert not _messages(caplog)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
