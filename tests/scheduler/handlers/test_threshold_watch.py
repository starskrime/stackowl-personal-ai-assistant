"""ADR-C — ThresholdWatchHandler: edge-triggered numeric predicate with hysteresis.

The conditional sibling of website_watch. It fetches a NUMBER from a generic
source, evaluates ``(op, threshold)``, and fires ONLY on a false→true EDGE — never
again while the predicate stays true (the flood ADR-C warns about), re-arming only
after it crosses back to false.

These tests pin the producer path with a scripted fake runtime (no real browser):
* false→true fires exactly once;
* stays-true second poll does NOT fire (hysteresis);
* cross back to false then true again fires again (re-arm);
* the number is extracted robustly from the source text;
* a malformed source (no number) fails honestly;
* the handler passes the scheduler reachability audit (registered + on_demand).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stackowl.scheduler.base import HandlerRegistry
from stackowl.scheduler.handlers.threshold_watch import (
    ThresholdWatchHandler,
    register_threshold_watch_handler,
)
from stackowl.startup.wiring_audit import audit_scheduler_wiring
from tests._story_7_2_helpers import disable_guard, make_job

pytestmark = pytest.mark.asyncio


class _FakePage:
    def __init__(self, html: str) -> None:
        self._html = html

    async def goto(self, *_a: Any, **_kw: Any) -> None:
        return None

    async def content(self) -> str:
        return self._html

    async def close(self) -> None:
        return None


class _FakeContext:
    def __init__(self, html: str) -> None:
        self._html = html

    async def new_page(self) -> _FakePage:
        return _FakePage(self._html)

    async def close(self) -> None:
        return None

    async def route(self, pattern: str, handler: Any) -> None:
        pass  # FX-05 follow-up — real BrowserContext.route(); not asserted here.


class _FakeRuntime:
    """CamoufoxRuntime stand-in returning a page whose text carries ``value``."""

    def __init__(self, value: float) -> None:
        self.value = value
        self.available = True

    @property
    def unavailable_reason(self) -> str | None:
        return None

    @property
    def _html(self) -> str:
        return f"<html><body><span>Current reading: {self.value}</span></body></html>"

    async def acquire_domain_slot(self, _url: str) -> None:
        return None

    async def open_context(self, *, owner_key: str) -> _FakeContext:  # noqa: ARG002
        return _FakeContext(self._html)

    async def record_navigation(self) -> None:
        return None


class _FakeJobDeliverer:
    """Records ``deliver_for_job`` calls and returns a scripted outcome rollup."""

    def __init__(self, rollup: str = "delivered") -> None:
        self._rollup = rollup
        self.calls: list[dict[str, Any]] = []

    async def deliver_for_job(
        self,
        job: Any,
        *,
        message: str,
        category: str,
        urgency: str = "normal",
    ) -> Any:
        self.calls.append(
            {"job": job, "message": message, "category": category, "urgency": urgency}
        )
        from stackowl.notifications.proactive_job import ProactiveDeliveryOutcome

        return ProactiveDeliveryOutcome(rollup=self._rollup)


_SOURCE = "https://example.com/reading"


def _threshold_job() -> Any:
    return make_job(
        handler="threshold_watch",
        params={"watch_source": _SOURCE, "op": "gt", "threshold": 70000.0, "owner": "tony"},
        target_channels=["telegram"],
        target_addresses={"telegram": 12345},
    )


async def test_false_to_true_fires_exactly_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    disable_guard(monkeypatch)
    runtime = _FakeRuntime(value=65000.0)  # below → predicate FALSE
    deliverer = _FakeJobDeliverer()
    handler = ThresholdWatchHandler(
        runtime=runtime,  # type: ignore[arg-type]
        state_dir=tmp_path,
        job_deliverer=deliverer,  # type: ignore[arg-type]
    )
    job = _threshold_job()

    # First poll: FALSE — establishes the baseline, never fires.
    r1 = await handler.execute(job)
    assert r1.metadata["first_seen"] is True
    assert r1.metadata["state"] is False
    assert deliverer.calls == []

    # Crosses ABOVE → false→true edge → fires exactly once.
    runtime.value = 71000.0
    r2 = await handler.execute(job)
    assert r2.metadata["fired"] is True
    assert len(deliverer.calls) == 1
    call = deliverer.calls[0]
    assert call["category"] == "threshold_watch"
    assert "71000" in call["message"]
    assert r2.metadata.get("delivery") == "delivered"


async def test_stays_true_does_not_fire_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    disable_guard(monkeypatch)
    runtime = _FakeRuntime(value=65000.0)
    deliverer = _FakeJobDeliverer()
    handler = ThresholdWatchHandler(
        runtime=runtime,  # type: ignore[arg-type]
        state_dir=tmp_path,
        job_deliverer=deliverer,  # type: ignore[arg-type]
    )
    job = _threshold_job()

    await handler.execute(job)  # baseline FALSE
    runtime.value = 72000.0
    await handler.execute(job)  # false→true → fire
    assert len(deliverer.calls) == 1

    # Still above on the next poll — hysteresis: NO second ping.
    runtime.value = 73000.0
    r3 = await handler.execute(job)
    assert r3.metadata["state"] is True
    assert r3.metadata["fired"] is False
    assert len(deliverer.calls) == 1, "stays-true must not re-fire (flood guard)"


async def test_crosses_back_then_true_again_re_arms(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    disable_guard(monkeypatch)
    runtime = _FakeRuntime(value=65000.0)
    deliverer = _FakeJobDeliverer()
    handler = ThresholdWatchHandler(
        runtime=runtime,  # type: ignore[arg-type]
        state_dir=tmp_path,
        job_deliverer=deliverer,  # type: ignore[arg-type]
    )
    job = _threshold_job()

    await handler.execute(job)  # baseline FALSE
    runtime.value = 71000.0
    await handler.execute(job)  # fire (1)
    assert len(deliverer.calls) == 1

    # Cross BACK below → re-arm, no fire.
    runtime.value = 60000.0
    r_back = await handler.execute(job)
    assert r_back.metadata["state"] is False
    assert r_back.metadata["fired"] is False
    assert len(deliverer.calls) == 1

    # Cross above AGAIN → fresh false→true edge → fires again.
    runtime.value = 80000.0
    r_again = await handler.execute(job)
    assert r_again.metadata["fired"] is True
    assert len(deliverer.calls) == 2, "a fresh crossing must re-fire after re-arm"


async def test_number_extraction_handles_commas_and_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    disable_guard(monkeypatch)
    # Comma-grouped number embedded in surrounding text.
    runtime = _FakeRuntime(value=0.0)
    runtime.value = 1234.5  # used via _html; but assert the extractor directly too
    handler = ThresholdWatchHandler(
        runtime=runtime,  # type: ignore[arg-type]
        state_dir=tmp_path,
    )
    assert handler._extract_number("Price: 1,234.50 USD today") == 1234.5
    assert handler._extract_number("no digits here") is None
    assert handler._extract_number("-42 below zero") == -42.0

    # End-to-end: the value parsed from the page drives the predicate.
    job = make_job(
        handler="threshold_watch",
        params={"watch_source": _SOURCE, "op": "lt", "threshold": 2000.0},
    )
    r = await handler.execute(job)
    assert r.success is True
    assert r.metadata["value"] == 1234.5
    assert r.metadata["state"] is True  # 1234.5 < 2000


async def test_no_number_in_source_fails_honestly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    disable_guard(monkeypatch)

    class _NoNumberRuntime(_FakeRuntime):
        @property
        def _html(self) -> str:
            return "<html><body>nothing numeric here</body></html>"

    handler = ThresholdWatchHandler(
        runtime=_NoNumberRuntime(value=0.0),  # type: ignore[arg-type]
        state_dir=tmp_path,
    )
    job = _threshold_job()
    r = await handler.execute(job)
    assert r.success is False
    assert r.error is not None and "numeric" in r.error.lower()


async def test_handler_passes_reachability_audit() -> None:
    """Registered + declares on_demand → the wiring audit never flags it dangling."""

    class _FakeDb:
        async def fetch_all(self, _sql: str, _params: Any) -> list[dict[str, Any]]:
            return []  # no seeded rows — a seeded handler WOULD dangle; on_demand must not

    HandlerRegistry.reset()
    try:
        registry = HandlerRegistry.instance()
        runtime = _FakeRuntime(value=1.0)
        register_threshold_watch_handler(runtime, Path("/tmp/threshold-test"))  # type: ignore[arg-type]
        assert registry.get("threshold_watch") is not None

        report = await audit_scheduler_wiring(
            _FakeDb(), registry, allowed_events=[], declared_publishers=[]
        )
        assert "threshold_watch" not in report.dangling_handlers
        assert report.on_demand >= 1
    finally:
        HandlerRegistry.reset()
