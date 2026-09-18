"""``delegation.hopped`` is recorded from the REAL ``A2ADelegator.delegate()``
chokepoint at each of its three return points (Story 2.7) -- mirrors
``tests/owls/test_a2a_fidelity.py``'s harness shape (real ``A2AQueue``, real
``StepServices``), with a real ``tmp_db`` wired as ``StepServices.db_pool``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from stackowl.db.pool import DbPool
from stackowl.infra.trace import TraceContext
from stackowl.messaging.a2a import A2AMessage, A2AQueue
from stackowl.owls.a2a_delegation import A2ADelegator, A2AResult
from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState

pytestmark = pytest.mark.asyncio


def _parent(**kw: Any) -> PipelineState:
    return PipelineState(
        trace_id=kw.pop("trace_id", "trace-deleg-fixture"),
        session_key="s", input_text="go", channel="cli",
        owl_name="secretary", pipeline_step="dispatch", **kw,
    )


async def _hop_rows(tmp_db: DbPool, trace_id: str) -> list:
    return await tmp_db.fetch_all(
        "SELECT * FROM journal_events WHERE trace_id = ? AND type = 'delegation.hopped'",
        (trace_id,),
    )


class TestTimeoutPathRecordsFailed:
    async def test_timeout(self, tmp_db: DbPool) -> None:
        q = A2AQueue()
        deleg = A2ADelegator(
            a2a_queue=q, services=StepServices(db_pool=tmp_db), timeout_seconds=0.05,
        )

        async def _blocking(*args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(60)

        token = TraceContext.start(trace_id="trace-deleg-timeout", owl_name="secretary")
        try:
            with patch.object(deleg, "_run_specialist", new=_blocking):
                res = await deleg.delegate(
                    from_owl="secretary", to_owl="ghost", sub_task="x",
                    parent_state=_parent(trace_id="trace-deleg-timeout"),
                )
        finally:
            TraceContext.reset(token)

        assert res.status == "timeout"
        rows = await _hop_rows(tmp_db, "trace-deleg-timeout")
        assert len(rows) == 1
        row = rows[0]
        assert row["outcome"] == "failed"
        assert row["target_kind"] == "owl"
        assert row["target_id"] == "ghost"
        assert row["actor_kind"] == "owl"
        assert row["actor_id"] == "secretary"
        attrs = json.loads(row["attrs"])
        assert attrs["status"] == "timeout"
        assert attrs["from_owl"] == "secretary"
        assert attrs["to_owl"] == "ghost"


class TestChildErrorPathRecordsFailed:
    async def test_child_error(self, tmp_db: DbPool) -> None:
        from stackowl.exceptions import StackOwlError

        q = A2AQueue()
        deleg = A2ADelegator(
            a2a_queue=q, services=StepServices(db_pool=tmp_db), timeout_seconds=5.0,
        )

        token = TraceContext.start(trace_id="trace-deleg-child-error", owl_name="secretary")
        try:
            with patch.object(q, "receive", side_effect=StackOwlError("bang")):
                res = await deleg.delegate(
                    from_owl="secretary", to_owl="broken", sub_task="y",
                    parent_state=_parent(trace_id="trace-deleg-child-error"),
                )
        finally:
            TraceContext.reset(token)

        assert res.status == "child_error"
        rows = await _hop_rows(tmp_db, "trace-deleg-child-error")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "failed"
        attrs = json.loads(rows[0]["attrs"])
        assert attrs["status"] == "child_error"


class TestSuccessPathRecordsOk:
    async def test_ok(self, tmp_db: DbPool) -> None:
        q = A2AQueue()
        deleg = A2ADelegator(
            a2a_queue=q, services=StepServices(db_pool=tmp_db), timeout_seconds=5.0,
        )
        reply = A2AMessage.now(
            from_owl="scout", to_owl="secretary", content="the answer",
            message_type="response", trace_id="trace-deleg-ok", status="ok", error=None,
        )

        token = TraceContext.start(trace_id="trace-deleg-ok", owl_name="secretary")
        try:
            with (
                patch.object(deleg, "_run_specialist", new=AsyncMock(return_value=None)),
                patch.object(q, "receive", new=AsyncMock(return_value=reply)),
            ):
                res = await deleg.delegate(
                    from_owl="secretary", to_owl="scout", sub_task="do something",
                    parent_state=_parent(trace_id="trace-deleg-ok"),
                )
        finally:
            TraceContext.reset(token)

        assert isinstance(res, A2AResult)
        assert res.status == "ok"
        rows = await _hop_rows(tmp_db, "trace-deleg-ok")
        assert len(rows) == 1
        row = rows[0]
        assert row["outcome"] == "ok"
        assert row["target_id"] == "scout"
        attrs = json.loads(row["attrs"])
        assert attrs["status"] == "ok"


class TestEmptyResponseStillRecordsOk:
    """The Boundaries collapse rule: outcome is OK for both 'ok' AND 'empty'."""

    async def test_empty(self, tmp_db: DbPool) -> None:
        q = A2AQueue()
        deleg = A2ADelegator(
            a2a_queue=q, services=StepServices(db_pool=tmp_db), timeout_seconds=5.0,
        )
        reply = A2AMessage.now(
            from_owl="scout", to_owl="secretary", content="   ",
            message_type="response", trace_id="trace-deleg-empty", status=None, error=None,
        )

        token = TraceContext.start(trace_id="trace-deleg-empty", owl_name="secretary")
        try:
            with (
                patch.object(deleg, "_run_specialist", new=AsyncMock(return_value=None)),
                patch.object(q, "receive", new=AsyncMock(return_value=reply)),
            ):
                res = await deleg.delegate(
                    from_owl="secretary", to_owl="scout", sub_task="noop",
                    parent_state=_parent(trace_id="trace-deleg-empty"),
                )
        finally:
            TraceContext.reset(token)

        assert res.status == "empty"
        rows = await _hop_rows(tmp_db, "trace-deleg-empty")
        assert len(rows) == 1
        assert rows[0]["outcome"] == "ok"
        attrs = json.loads(rows[0]["attrs"])
        assert attrs["status"] == "empty"


class TestNoTraceContextRecordsNothing:
    async def test_no_trace_id(self, tmp_db: DbPool) -> None:
        q = A2AQueue()
        deleg = A2ADelegator(
            a2a_queue=q, services=StepServices(db_pool=tmp_db), timeout_seconds=5.0,
        )
        reply = A2AMessage.now(
            from_owl="scout", to_owl="secretary", content="hi",
            message_type="response", trace_id="", status="ok", error=None,
        )

        with (
            patch.object(deleg, "_run_specialist", new=AsyncMock(return_value=None)),
            patch.object(q, "receive", new=AsyncMock(return_value=reply)),
        ):
            await deleg.delegate(
                from_owl="secretary", to_owl="no-trace-scout", sub_task="noop",
                parent_state=_parent(trace_id=""),
            )

        rows = await tmp_db.fetch_all(
            "SELECT * FROM journal_events WHERE target_id = 'no-trace-scout'"
        )
        assert rows == []
