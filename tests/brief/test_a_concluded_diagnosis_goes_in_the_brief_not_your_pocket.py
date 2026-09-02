"""A finished diagnosis belongs in the brief, not in a critical page.

MEASURED: 12 of one day's 25 CRITICAL Telegram pages were
``incident_escalation: RCA complete`` — the self-heal loop telling Bakir it had
finished a diagnosis. The sink it used is ``_build_health_alert_sink``, which
hardcodes ``urgency="critical"``, and the notification router's decision table
returns "delivered" for every critical message whatever the hour or focus mode.
So a concluded self-heal diagnosis interrupted him exactly as hard as a subsystem
going down.

Asked 2026-09-02, answered: **"Digest only, page if unresolved."**

THE HALF THAT SHIPPED IS THE DIGEST HALF. Removing the page without reporting the
verdict anywhere would not be a digest — it would be deletion. ``record_diagnosis``
already writes an ``incident.diagnosed`` audit row for every conclusion and now
carries the SAME composed text the page used to send, so this section reads a
ledger that already existed rather than adding a store.

THE OTHER HALF WAS NOT SHIPPED, AND THAT IS DELIBERATE. "Page if unresolved" read
literally inverts both branches — and the code's own comment records that **86 of
100 RCAs conclude verified=False**. Paging on unresolved would have taken him from
~12 pages to ~70, which is the opposite of what he asked for. Unresolved
conclusions are COUNTED in this section instead, and the question of what
genuinely deserves an interrupt went back to him rather than being answered by
implementing a rule whose premise the data contradicts.
"""

from __future__ import annotations

import json
import time

import pytest

from stackowl.brief.assemblers import BriefContext, ConcludedIncidentsAssembler

pytestmark = pytest.mark.asyncio


class _Db:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    async def fetch_all(self, sql: str, params: tuple) -> list[dict]:
        assert "incident.diagnosed" in str(params), "the section reads the wrong event"
        assert "audit_log" in sql
        return self._rows


class _AngryDb:
    async def fetch_all(self, *a: object, **k: object) -> list[dict]:
        raise RuntimeError("no such table")


def _row(*, verified: bool, summary: str = "", actor: str = "sig-1") -> dict:
    return {
        "actor": actor, "target": "incident-1", "timestamp": time.time(),
        "details": json.dumps({"verified": verified, "summary": summary}),
    }


def _ctx() -> BriefContext:
    return BriefContext.model_construct()


async def test_a_verified_conclusion_is_REPORTED_with_its_verdict() -> None:
    """The page carried root cause and fix. The brief must carry the same, or the
    move from page to digest loses information."""
    sec = await ConcludedIncidentsAssembler(
        _Db([_row(verified=True, summary="root cause: rc / fix: fx")]),
    ).assemble(_ctx())
    assert sec.omitted is False
    assert any("rc" in i and "fx" in i for i in sec.items)


async def test_unresolved_analyses_are_COUNTED_not_listed() -> None:
    """86 of 100 conclude unverified. Listing them would rebuild the noise this
    section exists to remove; hiding them entirely would misreport how much the
    loop actually resolves."""
    rows = [_row(verified=False, actor=f"s{i}") for i in range(20)]
    sec = await ConcludedIncidentsAssembler(_Db(rows)).assemble(_ctx())
    assert len(sec.items) == 1
    assert "20" in sec.items[0]


async def test_a_quiet_window_omits_the_section_entirely() -> None:
    """A brief that says "no incidents" every morning trains you to skip it."""
    sec = await ConcludedIncidentsAssembler(_Db([])).assemble(_ctx())
    assert sec.omitted is True


async def test_a_ledger_read_failure_OMITS_rather_than_reporting_zero() -> None:
    """The expensive direction. "No incidents diagnosed" when the read failed is
    a false clean bill of health — the mistake this project keeps paying for."""
    sec = await ConcludedIncidentsAssembler(_AngryDb()).assemble(_ctx())
    assert sec.omitted is True
    assert sec.items == []


async def test_one_unparseable_row_does_not_lose_the_others() -> None:
    rows = [{"actor": "s", "target": "t", "timestamp": time.time(), "details": "{not json"},
            _row(verified=True, summary="root cause: rc")]
    sec = await ConcludedIncidentsAssembler(_Db(rows)).assemble(_ctx())
    assert any("rc" in i for i in sec.items)


def test_it_is_WIRED_into_the_morning_brief() -> None:
    """A section nothing constructs is decoration, and the page it replaces is
    already gone — so an unwired section means concluded diagnoses reach nobody."""
    import inspect

    from stackowl.scheduler.handlers import morning_brief

    src = inspect.getsource(morning_brief)
    assert "ConcludedIncidentsAssembler(db=db)" in src


def test_the_paging_branch_is_GONE_from_the_handler() -> None:
    """Structural: if the alert call comes back, the brief section becomes a
    duplicate and the 12-of-25 noise returns with it."""
    import inspect

    from stackowl.scheduler.handlers import incident_escalation

    src = inspect.getsource(incident_escalation.IncidentEscalationHandler._consume_verdict)  # noqa: SLF001
    assert "_compose_verdict_alert" not in src, (
        "the concluded-verdict page is back — it should reach the ledger and the "
        "brief, not the operator's phone"
    )
