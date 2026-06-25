"""PerchHandler — watch a path, ping when files change (Phase 3 anticipation).

The filesystem analog of WebsiteWatchHandler: snapshot a watched directory,
diff against the prior poll, and on a real change deliver a concise ping through
the SAME durable exactly-once seam. The first poll establishes a baseline (no
spurious ping). This is the "notices things on its own" sensor the platform
lacked (website-watching already exists; filesystem-watching did not).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stackowl.notifications.proactive_job import ProactiveDeliveryOutcome
from stackowl.scheduler.handlers.perch import PerchHandler
from stackowl.scheduler.job import Job

pytestmark = pytest.mark.asyncio


class _FakeDeliverer:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def deliver_for_job(
        self, job: Job, *, message: str, category: str, urgency: str = "normal",
    ) -> ProactiveDeliveryOutcome:
        self.messages.append(message)
        return ProactiveDeliveryOutcome(rollup="delivered", per_channel={"telegram": "delivered"})


def _job(path: Path) -> Job:
    return Job(
        job_id="perch-1", handler_name="perch", schedule="every 5m",
        idempotency_key="perch-1", last_run_at=None,
        next_run_at="2026-06-24T00:00:00+00:00", status="running",
        params={"path": str(path)},
        target_channels=["telegram"], target_addresses={"telegram": 7},
    )


def _handler(state_dir: Path, deliverer: _FakeDeliverer | None = None) -> PerchHandler:
    return PerchHandler(state_dir=state_dir, job_deliverer=deliverer)


async def test_handler_name_and_trigger_kind(tmp_path: Path) -> None:
    h = _handler(tmp_path / "state")
    assert h.handler_name == "perch"
    assert h.trigger_kind == "on_demand"


async def test_first_poll_is_baseline_no_ping(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "a.txt").write_text("hello")
    deliverer = _FakeDeliverer()
    h = _handler(tmp_path / "state", deliverer)

    result = await h.execute(_job(watched))

    assert result.success
    assert result.metadata["first_seen"] is True
    assert result.metadata["changed"] is False
    assert deliverer.messages == []  # no spurious ping on baseline


async def test_no_change_between_polls_no_ping(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "a.txt").write_text("hello")
    deliverer = _FakeDeliverer()
    h = _handler(tmp_path / "state", deliverer)

    await h.execute(_job(watched))           # baseline
    result = await h.execute(_job(watched))  # unchanged

    assert result.metadata["changed"] is False
    assert deliverer.messages == []


async def test_new_file_triggers_ping(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "a.txt").write_text("hello")
    deliverer = _FakeDeliverer()
    h = _handler(tmp_path / "state", deliverer)

    await h.execute(_job(watched))           # baseline
    (watched / "b.txt").write_text("new file")
    result = await h.execute(_job(watched))

    assert result.metadata["changed"] is True
    assert result.metadata["added"] == 1
    assert len(deliverer.messages) == 1
    assert str(watched) in deliverer.messages[0]


async def test_modified_file_triggers_ping(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    target = watched / "a.txt"
    target.write_text("hello")
    deliverer = _FakeDeliverer()
    h = _handler(tmp_path / "state", deliverer)

    await h.execute(_job(watched))           # baseline
    target.write_text("hello world — changed")
    result = await h.execute(_job(watched))

    assert result.metadata["changed"] is True
    assert result.metadata["modified"] == 1
    assert len(deliverer.messages) == 1


async def test_removed_file_triggers_ping(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "a.txt").write_text("hello")
    (watched / "b.txt").write_text("bye")
    deliverer = _FakeDeliverer()
    h = _handler(tmp_path / "state", deliverer)

    await h.execute(_job(watched))           # baseline
    (watched / "b.txt").unlink()
    result = await h.execute(_job(watched))

    assert result.metadata["changed"] is True
    assert result.metadata["removed"] == 1
    assert len(deliverer.messages) == 1


async def test_change_with_no_deliverer_is_honest(tmp_path: Path) -> None:
    watched = tmp_path / "watched"
    watched.mkdir()
    (watched / "a.txt").write_text("hello")
    h = _handler(tmp_path / "state", deliverer=None)

    await h.execute(_job(watched))
    (watched / "b.txt").write_text("new")
    result = await h.execute(_job(watched))

    assert result.metadata["changed"] is True
    assert result.metadata.get("delivery") == "no_deliverer"  # never a fake "sent"


async def test_missing_path_is_structured_failure(tmp_path: Path) -> None:
    h = _handler(tmp_path / "state")
    job = Job(
        job_id="perch-x", handler_name="perch", schedule="every 5m",
        idempotency_key="perch-x", last_run_at=None,
        next_run_at="2026-06-24T00:00:00+00:00", status="running", params={},
    )
    result = await h.execute(job)
    assert not result.success
    assert "path" in (result.error or "").lower()
