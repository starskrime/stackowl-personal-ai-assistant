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

    def test_the_dropped_list_is_complete(self, caplog) -> None:
        """`dropped` names EVERY evicted tool. It used to be sliced to 20.

        THIS TEST PREVIOUSLY ASSERTED THE OPPOSITE, and the reversal is deliberate
        rather than a test bent to fit a change. Its original form pinned
        `dropped_truncated is True` because a reader (me) had once mistaken the
        truncated list for the whole answer, so the flag was added to say what the
        field was. That mitigation was correct about the danger and wrong about the
        remedy, and D05.8 measured why:

        * the flag read `true` on 178 of 178 records in a day — never once false, so
          it marked the permanent state rather than an edge case, and a reader learns
          nothing from a constant;
        * the slice was not arbitrary. `dropped` follows rank order, and rank order
          for a tool with neither a usage score nor a declared priority is the
          ALPHABET, so the field had a fixed alphabetical ceiling. `objective`,
          `run_tests`, `send_message`, `session_search`, `shell`, `todo`,
          `update_plan` and `web_search` could never appear in it however often they
          were dropped — and this programme read that field as evidence for a week.

        The list is bounded by construction (at most the catalog, of short strings),
        which is the same reasoning `cache_audit` already applies to its own name
        lists. So the honest fix is to emit all of it; `dropped_truncated` stays in
        the record as a permanently-false contract marker so a consumer parsing the
        field does not have to guess which era wrote it.
        """
        caplog.set_level(logging.INFO)
        reg = ToolRegistry()
        for i in range(60):
            reg.register(_T(f"tool_{i:02d}"))

        reg.to_provider_schema(
            "openai",
            budget={"window": 262_144, "fixed_cost_tokens": 100, "max_tools": 30},
        )

        f = _fields(caplog)
        dropped = f.get("dropped", [])
        assert f.get("dropped_count", 0) > 20, "this case must exceed the old slice"
        assert len(dropped) == f.get("dropped_count"), (
            "the emitted list is shorter than the count beside it — still truncated"
        )
        assert f.get("dropped_truncated") is False

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
