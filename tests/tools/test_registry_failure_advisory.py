"""F-26 — the registry's get/dispatch surface consults the turn-scoped tool
outcome ledger for recent REPEATED failures of the same tool and emits a
read-only ADVISORY (never blocks, never writes negative learning).

The consult reads :func:`stackowl.infra.tool_outcome_ledger.get_outcomes` — the
in-process per-turn ledger already bound by the backend. It writes nothing back
(no negative-learning side effect) and always returns the tool: the advisory is
informational only.
"""

from __future__ import annotations

import logging

from stackowl.infra import tool_outcome_ledger as ledger
from stackowl.tools.base import Tool, ToolResult
from stackowl.tools.registry import ToolRegistry


class _Noop(Tool):
    def __init__(self, name: str = "flaky") -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "noop"

    @property
    def parameters(self) -> dict[str, object]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: object) -> ToolResult:  # pragma: no cover
        return ToolResult(success=True, output="", duration_ms=1.0)


def test_get_advises_on_repeated_prior_failures(caplog) -> None:
    reg = ToolRegistry()
    reg.register(_Noop("flaky"))

    token = ledger.bind()
    try:
        # Two effectful (write) failures of the same tool THIS turn.
        ledger.record_tool_outcome(name="flaky", action_severity="write", success=False)
        ledger.record_tool_outcome(name="flaky", action_severity="write", success=False)

        with caplog.at_level(logging.WARNING, logger="stackowl.tool"):
            tool = reg.get("flaky")

        assert tool is not None  # never blocks — still hands back the tool
        assert any(
            "prior" in r.getMessage().lower() and "flaky" in str(r.__dict__.get("_fields", ""))
            or "flaky" in r.getMessage()
            for r in caplog.records
        ), "expected an advisory log mentioning the repeated-failure tool"
    finally:
        ledger.reset(token)


def test_get_does_not_advise_on_single_failure(caplog) -> None:
    reg = ToolRegistry()
    reg.register(_Noop("flaky"))

    token = ledger.bind()
    try:
        ledger.record_tool_outcome(name="flaky", action_severity="write", success=False)
        with caplog.at_level(logging.WARNING, logger="stackowl.tool"):
            reg.get("flaky")
        advisories = [r for r in caplog.records if "prior-failure" in r.getMessage()]
        assert advisories == []  # one failure is not a REPEATED pattern
    finally:
        ledger.reset(token)


def test_get_consult_is_read_only(caplog) -> None:
    reg = ToolRegistry()
    reg.register(_Noop("flaky"))

    token = ledger.bind()
    try:
        ledger.record_tool_outcome(name="flaky", action_severity="write", success=False)
        ledger.record_tool_outcome(name="flaky", action_severity="write", success=False)
        before = ledger.get_outcomes()
        reg.get("flaky")
        after = ledger.get_outcomes()
        assert before == after  # consult writes NOTHING back to the ledger
    finally:
        ledger.reset(token)


def test_get_unbound_ledger_is_silent(caplog) -> None:
    # No turn ledger bound (e.g. introspection off the turn path) → no crash,
    # no advisory, byte-identical lookup.
    reg = ToolRegistry()
    reg.register(_Noop("flaky"))
    with caplog.at_level(logging.WARNING, logger="stackowl.tool"):
        tool = reg.get("flaky")
    assert tool is not None
    assert [r for r in caplog.records if "prior-failure" in r.getMessage()] == []


def test_get_missing_tool_returns_none() -> None:
    reg = ToolRegistry()
    assert reg.get("nope") is None
