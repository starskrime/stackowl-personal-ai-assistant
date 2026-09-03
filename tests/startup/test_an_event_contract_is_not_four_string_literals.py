"""An event name is a contract. It may not be four independent string literals.

WHAT THE BOOT LOG SAYS, every boot:

    [startup] wiring audit: 38 handlers — 29 seeded, 9 on_demand, 0 event;
    0 dangling   {"dangling_events": []}

"0 dangling events" is produced by comparing one hand-written list against
another hand-written list. ``event_bridge._ALLOWED_EVENTS`` spells three event
names as literals; ``orchestrator.declared_event_publishers`` spells the same
three again, two as literals and one imported from its publisher; the publisher
``cost_tracker`` spells two of them a third time at its ``emit`` calls. Nothing
checks that the three spellings agree.

THIS HAS ALREADY GONE WRONG ONCE, and the code says so in its own words:

    DEBT-7 — this was `frozenset()`, so the dangling-event check compared
    subscribers against NOTHING and could only ever answer "dangling". It
    flagged the two budget events correctly by accident and would have said the
    same about perfectly-wired ones, which also means it could never have caught
    a genuinely NEW dangling subscription.

That was the vacuous direction. The other direction is still open and is worse,
because it is silent: rename or delete a publisher's emit and the auditor's own
copy of the string stays behind, so the audit reports the subscription as WIRED
forever. The guard whose entire purpose is finding dangling half-edges holds a
hand-maintained half-edge of its own.

THE CODEBASE ALREADY DOES THIS CORRECTLY ONCE. ``conversation_cost_report``
exports ``COST_REPORT_EVENT`` and the orchestrator imports it, so deleting that
publisher breaks the import — loudly, at boot, instead of quietly at audit time.
This makes that the rule rather than the exception: every subscribed event is
named by exactly one constant, owned by the module that emits it.

MEASURED CONTEXT, and deliberately NOT claimed as the defect. 130,285 of 130,288
cost rows are $0.00 and total spend ever is $0.019, because this deployment's
model is unpriced — so ``budget_exceeded`` and ``budget_80pct_alert`` have
publishers that will not fire here. That is a deployment fact, not a code fault;
it is recorded because it means "0 dangling" is already less reassuring than it
reads, and because a static declaration check cannot see it either way.
"""

from __future__ import annotations

import pytest

from stackowl.notifications.event_bridge import _ALLOWED_EVENTS
from stackowl.providers.conversation_cost_report import COST_REPORT_EVENT
from stackowl.providers.cost_tracker import (
    BUDGET_EXCEEDED_EVENT,
    BUDGET_WARNING_EVENT,
)
from stackowl.startup.wiring_audit import audit_scheduler_wiring

pytestmark = pytest.mark.anyio


class _Registry:
    """A HandlerRegistry stand-in with no handlers — this suite is about EVENTS."""

    def all(self) -> dict[str, object]:
        return {}


class _Db:
    async def fetch_all(self, _sql: str, _params: object = None) -> list[dict[str, str]]:
        return []


# --------------------------------------------------------------------------- #
# One constant per event, owned by the publisher                               #
# --------------------------------------------------------------------------- #


def test_every_subscribed_event_is_named_by_a_publisher_constant() -> None:
    """THE ROOT CAUSE. ``_ALLOWED_EVENTS`` held three bare literals, so the
    subscriber's spelling and the publisher's spelling were independent facts
    that happened to match."""
    owned = {BUDGET_EXCEEDED_EVENT, BUDGET_WARNING_EVENT, COST_REPORT_EVENT}
    assert set(_ALLOWED_EVENTS) == owned, (
        "the bridge subscribes to a name no publisher module exports (or has "
        f"stopped exporting): {set(_ALLOWED_EVENTS) ^ owned}"
    )


@pytest.mark.tripwire
def test_the_audit_chain_holds_no_literal_copy_of_an_event_name() -> None:
    """THE GUARD THAT MAKES IT STICK. Each of the three modules in the chain —
    publisher, subscriber, auditor — must reference the constant, never re-spell
    the string. A fourth copy added later is exactly how the auditor comes to
    disagree with reality while reporting success."""
    import inspect

    from stackowl.notifications import event_bridge
    from stackowl.providers import cost_tracker
    from stackowl.startup import orchestrator

    offenders: list[str] = []
    for module, names in (
        (cost_tracker, (BUDGET_EXCEEDED_EVENT, BUDGET_WARNING_EVENT)),
        (event_bridge, (BUDGET_EXCEEDED_EVENT, BUDGET_WARNING_EVENT, COST_REPORT_EVENT)),
        (orchestrator, (BUDGET_EXCEEDED_EVENT, BUDGET_WARNING_EVENT, COST_REPORT_EVENT)),
    ):
        source = inspect.getsource(module)
        for name in names:
            # The DEFINITION line is the one legitimate literal, and it lives in
            # the publisher only.
            body = source.replace(f'= "{name}"', "")
            if f'"{name}"' in body or f"'{name}'" in body:
                offenders.append(f"{module.__name__} re-spells {name!r}")
    assert not offenders, (
        "an event name is spelled as a literal outside its owning constant — a "
        "rename there is silent:\n  " + "\n  ".join(offenders)
    )


# --------------------------------------------------------------------------- #
# The instrument must still be able to say "dangling"                          #
# --------------------------------------------------------------------------- #


async def test_a_genuinely_unpublished_subscription_is_still_reported() -> None:
    """THE VACUITY CONTROL. DEBT-7's first value made this check answer
    "dangling" for everything; the fix could just as easily make it answer
    "wired" for everything, and both look like a clean boot log. A zero over a
    zero is not a pass, so the check is exercised against a subscription that
    genuinely has no publisher."""
    report = await audit_scheduler_wiring(
        _Db(), _Registry(),
        allowed_events={"an_event_nobody_emits"},
        declared_publishers=set(_ALLOWED_EVENTS),
    )
    assert report.dangling_events == ["an_event_nobody_emits"], report.dangling_events


async def test_the_live_wiring_reports_no_dangling_subscription() -> None:
    """And the real configuration passes — measured, not assumed. This is the
    assertion the boot log makes every morning; it belongs in a test too, so a
    publisher deleted between boots fails here first."""
    report = await audit_scheduler_wiring(
        _Db(), _Registry(),
        allowed_events=_ALLOWED_EVENTS,
        declared_publishers={
            BUDGET_EXCEEDED_EVENT, BUDGET_WARNING_EVENT, COST_REPORT_EVENT,
        },
    )
    assert report.dangling_events == []


def test_the_publisher_emits_the_name_it_exports() -> None:
    """The constant is only worth having if the emit uses it. A module that
    exports ``BUDGET_EXCEEDED_EVENT`` and then emits a literal has moved the
    problem rather than fixed it."""
    import inspect

    from stackowl.providers import cost_tracker

    source = inspect.getsource(cost_tracker)
    assert "emit(BUDGET_EXCEEDED_EVENT" in source, (
        "cost_tracker exports the constant but emits something else"
    )
    assert "emit(BUDGET_WARNING_EVENT" in source
