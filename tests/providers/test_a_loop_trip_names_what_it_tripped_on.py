"""When the turn stops early, the log must say what it stopped ON.

WHY THIS EXISTS, and it cost a whole investigation to find.

There are TWO loop detectors in this platform and they report the same kind of
event in two different shapes:

  * `[guardrails] tool-loop warning` — `{"tool", "code", "count", "args_hash"}`.
    You can read it and know what happened.
  * `[openai] complete_with_tools: loop guard tripped` (12 in the retained
    window) and `max_iterations reached` (33) — **`{"provider"}` and nothing
    else.** 45 events that cannot say which tool, how many calls, or what the
    turn was doing.

MEASURED 2026-09-06. Twenty-seven traces got stuck in the retained window and
NOT ONE is diagnosable from its own log line. Reconstructing a single one meant
joining the log to `task_outcomes.tool_sequence` by hand — which is how this
session established that a bound refusal does NOT cause a loop (refusal traces
have a median of 20 tool calls against 3 for all tool-using turns, so refusals
and loops share a common cause in turn LENGTH). That answer should have been one
grep, and it was an hour.

THE INFORMATIVE SHAPE ALREADY EXISTS TWENTY LINES AWAY. In the same function,
the auto-escalate branch logs `{"provider": ..., "calls": len(all_calls)}`. So
the author already knew the call count belonged on this kind of line and put it
on one branch of three. That is defect shape 3 — two copies of one rule, the
weaker copies on the paths that matter — and it is the SECOND instance found in
two loops, after `incident_escalation` logged one suppression shape aggregated
and two per-cluster.

WHAT THE LINES MUST CARRY, and why this is not "log more". `tripped_on()` names
the signature that actually reached the break threshold, which is the single
fact the old line omitted and the only one that identifies the loop. `calls` is
the length that distinguishes "spun on one tool" from "did 50 legitimate things
and ran out of budget" — the exact distinction that took a hand-join to make.
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path

import pytest

from stackowl.providers._react import LoopGuard

_SRC = Path("src/stackowl/providers/openai_provider.py")

_LOOP_MESSAGES = (
    "loop guard tripped",
    "max_iterations reached",
)


@lru_cache(maxsize=1)
def _tree() -> ast.Module:
    """ONE parse shared by every helper — a second parse would make node
    identity comparisons silently impossible, which already produced a
    vacuously-passing guard in this repo on 2026-09-06."""
    return ast.parse(_SRC.read_text(encoding="utf-8"))


def _fields_on(needle: str) -> list[set[str]]:
    """The `_fields` keys each logging call carrying *needle* reports."""
    out: list[set[str]] = []
    for node in ast.walk(_tree()):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        parts: list[str] = []
        for a in node.args:
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                parts.append(a.value)
            elif isinstance(a, ast.JoinedStr):
                parts += [
                    p.value for p in a.values
                    if isinstance(p, ast.Constant) and isinstance(p.value, str)
                ]
        if not any(needle in p for p in parts):
            continue
        for kw in node.keywords:
            if kw.arg != "extra" or not isinstance(kw.value, ast.Dict):
                continue
            for k, v in zip(kw.value.keys, kw.value.values, strict=False):
                if isinstance(k, ast.Constant) and k.value == "_fields" and isinstance(v, ast.Dict):
                    out.append({
                        kk.value for kk in v.keys
                        if isinstance(kk, ast.Constant) and isinstance(kk.value, str)
                    })
    return out


class TestTheGuardNamesItsCause:
    @pytest.mark.parametrize("needle", _LOOP_MESSAGES)
    def test_the_line_reports_more_than_the_provider_name(self, needle: str) -> None:
        """THE DEFECT ITSELF. `{"provider": "NeraAiRaw"}` identifies the vendor,
        which is the one thing nobody investigating a stuck turn needs to know."""
        found = _fields_on(needle)
        assert found, f"no logging call carries {needle!r} any more"

        for fields in found:
            assert fields != {"provider"}, (
                f"{needle!r} still reports only the provider — a stuck turn cannot "
                "be diagnosed from its own log line"
            )

    @pytest.mark.parametrize("needle", _LOOP_MESSAGES)
    def test_it_reports_how_many_calls_the_turn_made(self, needle: str) -> None:
        """`calls` is what separates 'spun on one tool' from 'did fifty
        legitimate things and ran out of budget'. The escalate branch in this
        same function has always reported it."""
        for fields in _fields_on(needle):
            assert "calls" in fields, f"{needle!r} does not say how many calls: {fields}"

    def test_the_loop_guard_line_names_the_repeated_signature(self) -> None:
        """The one fact that identifies WHICH loop. Only the trip line can carry
        it — max_iterations has no single offending signature by definition."""
        for fields in _fields_on("loop guard tripped"):
            assert fields & {"tripped_on", "signature", "tool"}, (
                f"the trip line never names what it tripped on: {fields}"
            )

    def test_the_guard_sees_a_real_population(self) -> None:
        """VACUITY CONTROL. If the AST walk matched nothing, every assertion
        above would pass over an empty list."""
        total = sum(len(_fields_on(n)) for n in _LOOP_MESSAGES)
        assert total >= 3, f"only found {total} loop/max-iteration log calls"


class TestLoopGuardCanNameItsTrip:
    """`tripped()` returns a bool, so the caller had nothing to log even if it
    wanted to. The accessor is the enabling half of the fix."""

    def test_tripped_on_returns_the_signature_that_broke(self) -> None:
        guard = LoopGuard(warn_at=2, break_at=3)
        for _ in range(3):
            guard.observe("browser_navigate", {"url": "https://example.test"})

        assert guard.tripped()
        assert "browser_navigate" in (guard.tripped_on() or "")

    def test_it_is_none_before_anything_trips(self) -> None:
        guard = LoopGuard(warn_at=2, break_at=3)
        guard.observe("web_search", {"q": "a"})
        guard.observe("web_search", {"q": "b"})

        assert not guard.tripped()
        assert guard.tripped_on() is None

    def test_it_names_the_OFFENDER_not_merely_the_busiest(self) -> None:
        """A turn that legitimately calls one tool many times with DIFFERENT
        args must not be reported as the cause — that is the headhunter case
        (46 web_searches, 46 different queries, real work)."""
        guard = LoopGuard(warn_at=2, break_at=3)
        for i in range(10):
            guard.observe("web_search", {"q": f"query {i}"})
        for _ in range(3):
            guard.observe("browser_navigate", {"url": "https://stuck.test"})

        assert "browser_navigate" in (guard.tripped_on() or "")
