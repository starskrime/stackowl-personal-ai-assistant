"""PB-CANARY — TelegramCanaryHandler send-path round-trip proof.

A confirmed real send (``rollup == "delivered"``) is the ONLY thing that stamps
the send-path liveness signal — never a fake "alive" on undeliverable/failed/
batched. Mocks ONLY the job deliverer + liveness store (scripted fakes), mirroring
check_in/morning_brief's existing honesty test pattern.
"""

from __future__ import annotations

from typing import Any

import pytest

from stackowl.notifications.proactive_job import ProactiveDeliveryOutcome
from stackowl.scheduler.handlers.telegram_canary import TelegramCanaryHandler
from tests._story_7_2_helpers import disable_guard, make_job

pytestmark = pytest.mark.asyncio


class _FakeJobDeliverer:
    def __init__(self, rollup: str) -> None:
        self._rollup = rollup
        self.calls: list[dict[str, Any]] = []

    async def deliver_for_job(self, job: Any, *, message: str, category: str, **kw: Any) -> Any:
        self.calls.append({"job": job, "message": message, "category": category, **kw})
        return ProactiveDeliveryOutcome(rollup=self._rollup)


class _RecordingLivenessStore:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def mark_alive(self, channel: str) -> None:
        self.calls.append(channel)


@pytest.mark.parametrize(
    ("rollup", "expected_success"),
    [("delivered", True), ("undeliverable", True), ("failed", False), ("partial", False)],
)
async def test_success_follows_rollup(
    monkeypatch: pytest.MonkeyPatch, rollup: str, expected_success: bool
) -> None:
    disable_guard(monkeypatch)
    deliverer = _FakeJobDeliverer(rollup)
    liveness = _RecordingLivenessStore()
    handler = TelegramCanaryHandler(job_deliverer=deliverer, liveness_store=liveness)  # type: ignore[arg-type]

    result = await handler.execute(make_job(handler="telegram_canary"))

    assert result.success is expected_success
    assert result.effect_class == "delivery"


async def test_delivered_stamps_liveness(monkeypatch: pytest.MonkeyPatch) -> None:
    disable_guard(monkeypatch)
    deliverer = _FakeJobDeliverer("delivered")
    liveness = _RecordingLivenessStore()
    handler = TelegramCanaryHandler(job_deliverer=deliverer, liveness_store=liveness)  # type: ignore[arg-type]

    await handler.execute(make_job(handler="telegram_canary"))

    assert liveness.calls == ["telegram_canary"]


@pytest.mark.parametrize("rollup", ["undeliverable", "failed", "partial", "batched", "suppressed"])
async def test_non_delivered_never_stamps_liveness(
    monkeypatch: pytest.MonkeyPatch, rollup: str
) -> None:
    disable_guard(monkeypatch)
    deliverer = _FakeJobDeliverer(rollup)
    liveness = _RecordingLivenessStore()
    handler = TelegramCanaryHandler(job_deliverer=deliverer, liveness_store=liveness)  # type: ignore[arg-type]

    await handler.execute(make_job(handler="telegram_canary"))

    assert liveness.calls == []


async def test_marker_body_is_fixed_and_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The marker body is deterministic — never templatized/varied per run."""
    disable_guard(monkeypatch)
    deliverer = _FakeJobDeliverer("delivered")
    handler = TelegramCanaryHandler(job_deliverer=deliverer, liveness_store=None)

    await handler.execute(make_job(handler="telegram_canary"))
    await handler.execute(make_job(handler="telegram_canary"))

    assert len(deliverer.calls) == 2
    assert deliverer.calls[0]["message"] == deliverer.calls[1]["message"]
    assert deliverer.calls[0]["category"] == "canary"


async def test_no_deliverer_skips_honestly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Legacy/unit construction with no deliverer wired — never a fake 'delivered'."""
    disable_guard(monkeypatch)
    handler = TelegramCanaryHandler()

    result = await handler.execute(make_job(handler="telegram_canary"))

    assert result.success is True
    assert result.metadata["delivery_status"] == "skipped"
    assert result.metadata["reason"] == "no_deliverer"


async def test_handler_name_and_trigger_kind() -> None:
    handler = TelegramCanaryHandler()
    assert handler.handler_name == "telegram_canary"
    assert handler.trigger_kind == "seeded"


async def test_canary_sends_ephemeral_not_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """The canary send must be silent/self-deleting, not a visible plain send."""
    disable_guard(monkeypatch)
    deliverer = _FakeJobDeliverer("delivered")
    handler = TelegramCanaryHandler(job_deliverer=deliverer, liveness_store=None)

    await handler.execute(make_job(handler="telegram_canary"))

    assert deliverer.calls[0]["ephemeral"] is True


async def test_canary_opts_out_of_undelivered_outbox_surfacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CANARY-LEAK: the canary marker must never surface in the user-facing
    next-contact banner — its deliver_for_job call opts out via
    surface_undelivered=False regardless of the send outcome."""
    disable_guard(monkeypatch)
    deliverer = _FakeJobDeliverer("failed")
    handler = TelegramCanaryHandler(job_deliverer=deliverer, liveness_store=None)

    await handler.execute(make_job(handler="telegram_canary"))

    assert deliverer.calls[0]["surface_undelivered"] is False


async def test_liveness_write_failure_does_not_flip_honest_delivered_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A liveness-stamp write failure must never turn a genuine confirmed send
    into a reported job failure — the two concerns are independent."""
    disable_guard(monkeypatch)

    class _BrokenLivenessStore:
        async def mark_alive(self, channel: str) -> None:
            raise RuntimeError("db write failed")

    deliverer = _FakeJobDeliverer("delivered")
    handler = TelegramCanaryHandler(
        job_deliverer=deliverer, liveness_store=_BrokenLivenessStore()  # type: ignore[arg-type]
    )

    result = await handler.execute(make_job(handler="telegram_canary"))

    assert result.success is True
