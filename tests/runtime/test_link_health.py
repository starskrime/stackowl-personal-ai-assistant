"""``GatewayCoreLinkHealthContributor`` -- surfaces a gateway/core Hello
mismatch in the health sweep (Spec 2.3). Mirrors
``tests/journal/test_health_contributor.py``'s shape.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from stackowl.ipc.frames import HelloFrame
from stackowl.runtime import link_health
from stackowl.runtime.link_health import GatewayCoreLinkHealthContributor

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_link_health() -> Generator[None]:
    link_health.reset_for_tests()
    yield
    link_health.reset_for_tests()


def _hello(pid: int = 1) -> HelloFrame:
    return HelloFrame(sender_pid=pid, highest_migration=1, registry_digest="d")


class TestTheContributor:
    async def test_a_healthy_link_reports_ok(self) -> None:
        status = await GatewayCoreLinkHealthContributor().health_check()
        assert status.status == "ok"
        assert status.remedy is None

    async def test_a_mismatch_degrades_with_a_remedy(self) -> None:
        link_health.note_mismatch(_hello(1), _hello(2))
        status = await GatewayCoreLinkHealthContributor().health_check()
        assert status.status == "degraded"
        assert status.remedy is not None
        assert status.message is not None

    async def test_never_reports_down(self) -> None:
        for _ in range(10):
            link_health.note_mismatch(_hello(1), _hello(2))
        status = await GatewayCoreLinkHealthContributor().health_check()
        assert status.status != "down"

    async def test_a_later_match_clears_the_degraded_state(self) -> None:
        link_health.note_mismatch(_hello(1), _hello(2))
        assert (await GatewayCoreLinkHealthContributor().health_check()).status == "degraded"

        link_health.note_match()
        status = await GatewayCoreLinkHealthContributor().health_check()
        assert status.status == "ok"
        assert status.remedy is None


class TestTheCounter:
    def test_mismatch_count_starts_at_zero(self) -> None:
        assert link_health.mismatch_count() == 0

    def test_mismatch_count_increments_on_each_mismatch(self) -> None:
        link_health.note_mismatch(_hello(1), _hello(2))
        link_health.note_mismatch(_hello(1), _hello(2))
        assert link_health.mismatch_count() == 2

    def test_a_match_resets_the_counter_to_zero(self) -> None:
        link_health.note_mismatch(_hello(1), _hello(2))
        link_health.note_match()
        assert link_health.mismatch_count() == 0
