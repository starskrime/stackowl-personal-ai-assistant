"""``model.called`` is recorded from the REAL chokepoint every provider round
funnels through -- ``ModelProvider._resilient_round`` /
``_dispatch_post_llm`` (Story 2.7), not a hand-built call to the recording
helper. Success, a classified fault, and the no-trace-id skip.
"""

from __future__ import annotations

import json

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.providers.base import ModelProvider

pytestmark = pytest.mark.asyncio


class _Probe(ModelProvider):
    """Minimal concrete provider -- ModelProvider is abstract (mirrors
    ``tests/providers/test_a_call_near_the_window_says_so.py``'s own ``_Probe``)."""

    @property
    def name(self) -> str:
        return "probe-provider"

    @property
    def protocol(self):  # noqa: ANN201
        return "openai"

    async def complete(self, messages, model, **kwargs):  # noqa: ANN001,ANN003,ANN201
        raise NotImplementedError

    def stream(self, messages, model, **kwargs):  # noqa: ANN001,ANN003,ANN201
        raise NotImplementedError


def _provider(db_pool: DbPool | None) -> _Probe:
    provider = _Probe.__new__(_Probe)
    provider._cost_tracker = None  # type: ignore[attr-defined]
    provider._db_pool = db_pool  # type: ignore[attr-defined]
    provider._breaker = None  # type: ignore[attr-defined]
    provider._limiter = None  # type: ignore[attr-defined]
    provider._cooldown_hours = None  # type: ignore[attr-defined]
    return provider


async def _rows(tmp_db: DbPool, trace_id: str) -> list:
    return await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE trace_id = ? AND type = 'model.called'",
        (trace_id,),
    )


class TestModelCalledIsRecordedFromTheRealChokepoint:
    async def test_a_successful_round_records_ok_with_no_error_code(
        self, tmp_db: DbPool
    ) -> None:
        provider = _provider(tmp_db)
        token = TraceContext.start(trace_id="trace-model-ok", owl_name="scout")
        try:
            async def _round() -> str:
                return "the completion"

            result = await provider._resilient_round(_round)
        finally:
            TraceContext.reset(token)

        assert result == "the completion"
        rows = await _rows(tmp_db, "trace-model-ok")
        assert len(rows) == 1
        row = rows[0]
        assert row["outcome"] == "ok"
        assert row["target_id"] == "probe-provider"
        assert row["actor_kind"] == "owl"
        assert row["actor_id"] == "scout"
        assert row["duration_ms"] is not None
        attrs = json.loads(row["attrs"])
        assert attrs["provider"] == "probe-provider"
        assert attrs["error_code"] is None

    async def test_a_classified_fault_records_failed_with_a_health_error_code(
        self, tmp_db: DbPool
    ) -> None:
        provider = _provider(tmp_db)
        token = TraceContext.start(trace_id="trace-model-fail", owl_name="scout")
        try:
            async def _round() -> str:
                raise TimeoutError("upstream timed out")

            with pytest.raises(TimeoutError):
                await provider._resilient_round(_round)
        finally:
            TraceContext.reset(token)

        rows = await _rows(tmp_db, "trace-model-fail")
        assert len(rows) == 1
        row = rows[0]
        assert row["outcome"] == "failed"
        attrs = json.loads(row["attrs"])
        # classify_health_error(str(TimeoutError("upstream timed out"))) -> "timeout"
        assert attrs["error_code"] == "timeout"

    async def test_no_trace_context_records_nothing(self, tmp_db: DbPool) -> None:
        """A health-probe ``complete()`` call with no turn in progress -- absent
        or ``trace_id=None`` -- must never be journaled with an empty trace."""
        provider = _provider(tmp_db)

        async def _round() -> str:
            return "probe response"

        # No TraceContext.start() at all -- the ambient default (trace_id=None).
        result = await provider._resilient_round(_round)
        assert result == "probe response"

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = 'probe-provider'"
        )
        assert rows == []

    async def test_no_db_pool_wired_never_raises(self) -> None:
        """A standalone/test provider with no DbPool injected is a silent no-op."""
        provider = _provider(None)
        token = TraceContext.start(trace_id="trace-no-pool")
        try:
            async def _round() -> str:
                return "ok"

            assert await provider._resilient_round(_round) == "ok"
        finally:
            TraceContext.reset(token)
