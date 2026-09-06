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


# ``test_orchestrator_declares_the_events_it_actually_publishes`` was DELETED on
# 2026-09-05, not moved. It read orchestrator.py as text and asserted the literal
# strings "budget_exceeded" and "budget_80pct_alert" appeared in it — a SIXTH spelling
# of the event names, and one that directly contradicted the neighbouring rule in
# test_an_event_contract_is_not_four_string_literals.py, which forbids exactly those
# literals outside their owning module. Two guards demanding opposite things is worse
# than either alone: satisfying one breaks the other, and the pair pins the
# declaration's ADDRESS rather than its content, which is why moving the declaration
# somewhere testable broke it.
#
# What it was trying to protect is now asserted properly, one file over, as
# ``test_the_declaration_covers_everything_the_bridge_SUBSCRIBES``: the declared set
# must cover everything the bridge subscribes. That is the real property, it holds
# wherever the declaration lives, and it cannot be satisfied by a string appearing in
# a comment.
