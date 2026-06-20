"""WS-D — WebsiteWatchHandler delivers a change notification (Issue 2).

A user watching a site must actually be told when it changes. The handler polls
the URL, hashes the canonical content, and on a real change (NOT the first poll)
funnels a concise human message through the SAME durable, exactly-once
:class:`ProactiveJobDeliverer` seam goal_execution/check_in/morning_brief use.

These tests pin the REAL producer path (the handler's own change detection drives
the delivery — no event-bus indirection):

* first poll (no prior hash) records the baseline and delivers NOTHING;
* a second poll whose content CHANGED delivers exactly once with the change
  message, addressed from the job's durable target, and records the honest
  rollup in JobResult.metadata;
* an UNCHANGED second poll delivers nothing;
* no deliverer wired → no crash, honest metadata (no fake "delivered").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from stackowl.scheduler.handlers.website_watch import WebsiteWatchHandler
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


class _FakeRuntime:
    """Minimal CamoufoxRuntime stand-in returning a scripted page body."""

    def __init__(self, html: str) -> None:
        self.html = html
        self.available = True

    @property
    def unavailable_reason(self) -> str | None:
        return None

    async def acquire_domain_slot(self, _url: str) -> None:
        return None

    async def open_context(self, *, owner_key: str) -> _FakeContext:  # noqa: ARG002
        return _FakeContext(self.html)

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


_URL = "https://example.com/watched"


def _watch_job() -> Any:
    return make_job(
        handler="website_watch",
        params={"url": _URL},
        target_channels=["telegram"],
        target_addresses={"telegram": 12345},
    )


async def test_first_poll_records_baseline_and_does_not_deliver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    disable_guard(monkeypatch)
    runtime = _FakeRuntime(html="<html><body>v1</body></html>")
    deliverer = _FakeJobDeliverer()
    handler = WebsiteWatchHandler(
        runtime=runtime,  # type: ignore[arg-type]
        state_dir=tmp_path,
        job_deliverer=deliverer,  # type: ignore[arg-type]
    )

    result = await handler.execute(_watch_job())

    assert result.success is True
    assert result.metadata["first_seen"] is True
    assert result.metadata["changed"] is False
    assert deliverer.calls == [], "first poll establishes a baseline, never pings"


async def test_change_delivers_once_with_message_and_records_rollup(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    disable_guard(monkeypatch)
    runtime = _FakeRuntime(html="<html><body>v1</body></html>")
    deliverer = _FakeJobDeliverer(rollup="delivered")
    handler = WebsiteWatchHandler(
        runtime=runtime,  # type: ignore[arg-type]
        state_dir=tmp_path,
        job_deliverer=deliverer,  # type: ignore[arg-type]
    )
    job = _watch_job()

    # First poll seeds the baseline (no delivery).
    await handler.execute(job)
    assert deliverer.calls == []

    # The page changed → second poll must deliver exactly once.
    runtime.html = "<html><body>v2 — price dropped!</body></html>"
    result = await handler.execute(job)

    assert result.metadata["changed"] is True
    assert len(deliverer.calls) == 1, "a real change delivers exactly once"
    call = deliverer.calls[0]
    assert call["job"] is job
    assert call["category"] == "website_watch"
    assert _URL in call["message"]
    # Honest rollup recorded in the result metadata.
    assert result.metadata.get("delivery") == "delivered"


async def test_unchanged_second_poll_does_not_deliver(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    disable_guard(monkeypatch)
    runtime = _FakeRuntime(html="<html><body>stable</body></html>")
    deliverer = _FakeJobDeliverer()
    handler = WebsiteWatchHandler(
        runtime=runtime,  # type: ignore[arg-type]
        state_dir=tmp_path,
        job_deliverer=deliverer,  # type: ignore[arg-type]
    )
    job = _watch_job()

    await handler.execute(job)  # baseline
    result = await handler.execute(job)  # identical content

    assert result.metadata["changed"] is False
    assert deliverer.calls == [], "no change → no ping"


async def test_undeliverable_rollup_recorded_honestly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    disable_guard(monkeypatch)
    runtime = _FakeRuntime(html="<html><body>a</body></html>")
    deliverer = _FakeJobDeliverer(rollup="undeliverable")
    handler = WebsiteWatchHandler(
        runtime=runtime,  # type: ignore[arg-type]
        state_dir=tmp_path,
        job_deliverer=deliverer,  # type: ignore[arg-type]
    )
    job = _watch_job()

    await handler.execute(job)
    runtime.html = "<html><body>b</body></html>"
    result = await handler.execute(job)

    assert result.metadata["changed"] is True
    assert len(deliverer.calls) == 1
    # Never claims delivered when the rollup is undeliverable.
    assert result.metadata.get("delivery") == "undeliverable"


async def test_no_deliverer_wired_no_crash_and_honest_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    disable_guard(monkeypatch)
    runtime = _FakeRuntime(html="<html><body>one</body></html>")
    # No job_deliverer at all — the wiring gap must not crash the poll.
    handler = WebsiteWatchHandler(
        runtime=runtime,  # type: ignore[arg-type]
        state_dir=tmp_path,
    )
    job = _watch_job()

    await handler.execute(job)
    runtime.html = "<html><body>two</body></html>"
    result = await handler.execute(job)

    assert result.success is True
    assert result.metadata["changed"] is True
    # A detected change with no deliverer is reported honestly, never "delivered".
    assert result.metadata.get("delivery") in (None, "no_deliverer")
