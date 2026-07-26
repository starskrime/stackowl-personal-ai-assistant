"""DEBT-7 part 3 — the wiring audit must be able to answer "wired".

``declared_event_publishers`` in startup/orchestrator.py was
``frozenset()`` — a hardcoded empty set, never assigned anything else
anywhere in the tree. So the dangling-event check compared subscribers
against NOTHING and could only ever answer "dangling". It reported the two
budget events correctly by accident; it would have said exactly the same
about perfectly-wired ones.

That matters beyond tidiness: this audit is what surfaced DEBT-7 in the first
place, and a signal that is permanently red is one everybody learns to
ignore. It also means the audit could never have caught a genuinely new
dangling subscription, because every subscription already looked dangling.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from stackowl.scheduler.base import HandlerRegistry
from stackowl.startup.wiring_audit import audit_scheduler_wiring


class _FakeDb:
    """Minimal DbPool-like: the audit only needs ``fetch_all``."""

    async def fetch_all(self, sql: str, params: Any = None) -> list[dict[str, Any]]:
        return []


async def test_a_declared_publisher_is_not_reported_dangling() -> None:
    """CHARACTERIZATION — the audit FUNCTION is fine; it produces a clean
    verdict as soon as it is handed a real declared set. Pinned here to locate
    the defect precisely: the bug is in the CALLER, which passes an empty set,
    not in this logic. Without this the fix could be mis-aimed at the audit."""
    report = await audit_scheduler_wiring(
        _FakeDb(),
        HandlerRegistry.instance(),
        allowed_events=frozenset({"budget_exceeded"}),
        declared_publishers=frozenset({"budget_exceeded"}),
    )

    assert report.dangling_events == []


async def test_an_undeclared_publisher_is_still_reported_dangling() -> None:
    """The check must keep WORKING — declaring the real publishers must not
    defang the detector for a genuinely dangling subscription."""
    report = await audit_scheduler_wiring(
        _FakeDb(),
        HandlerRegistry.instance(),
        allowed_events=frozenset({"nobody_emits_this"}),
        declared_publishers=frozenset({"budget_exceeded"}),
    )

    assert report.dangling_events == ["nobody_emits_this"]


def test_orchestrator_declares_the_events_it_actually_publishes() -> None:
    """CostTracker emits both budget thresholds and conversation_cost_report
    emits the cost report. All three must be declared, or every boot warns
    about working wiring forever."""
    src = Path("src/stackowl/startup/orchestrator.py").read_text(encoding="utf-8")

    assert "declared_event_publishers: frozenset[str] = frozenset()" not in src
    for event in ("budget_exceeded", "budget_80pct_alert"):
        assert event in src, f"{event} is emitted but not declared"
    # The cost report is declared via its module constant rather than a literal.
    assert "COST_REPORT_EVENT" in src
