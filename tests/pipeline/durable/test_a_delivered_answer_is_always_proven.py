"""The completion seam must run on EVERY path that reaches the user.

MEASURED LIVE 2026-08-20 on the running platform. Two rows sit in the tasks table
reading ``status='completed'`` with ``delivered_at`` NULL — f1f930de554a45d29599c9
d784d0cf2e and 7299dcfaad914378aa2c8f06bb4a6b72, both ``trigger_kind='chat'``,
both destination ``telegram:72055773``. Their answers DID reach Bakir. Nothing in
the platform can tell.

THREE DEFECTS IN ONE LIFECYCLE, and every one of them is failure mode #1 from
PROCESS.md — an actuator wired on only some paths:

1. ``deliver.run`` returns from the stream-miss branch BEFORE the completion seam::

       writer = registry.get_writer(state.trace_id)
       if writer is None:
           await _proactive_fallback(state, services)
           return state                      # <- complete_turn_task never runs

   The proactive fallback is the ONE path that reaches a user whose live stream is
   gone — which is exactly the recovered turn, the case the loop exists for. So
   the single path that most needs a delivery proof is the one that never writes
   one.

2. The seam keys on ``trace_id``; the durable row is keyed by ``task_id``. For an
   ordinary chat turn they are the same string (``enqueue_turn_task`` uses the
   trace id as the task id), so this never showed. A RECOVERY drive mints
   ``trace_id="recover-<12hex>"`` while ``state.task_id`` stays the real task id —
   so the proof would be written against a row that does not exist.

3. ``mark_delivered`` issues its UPDATE and logs "[loop] task COMPLETE" without
   ever asking how many rows it matched, so defect 2 would report success forever.

The rule being enforced is Bakir's, 2026-08-17: *a task is complete when its
outcome reached its DESTINATION, not when the function returned.* The corollary
this file tests is the other half — when the outcome DID reach its destination,
the proof must be written, whichever path carried it.
"""

from __future__ import annotations

import pytest

from stackowl.pipeline.services import StepServices
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.steps import deliver as deliver_step
from stackowl.pipeline.streaming import ResponseChunk, StreamRegistry

pytestmark = pytest.mark.asyncio


class _Deliverer:
    """Stands in for ProactiveDeliverer, reporting whatever status it is given."""

    def __init__(self, status: str = "delivered", raises: bool = False) -> None:
        self._status = status
        self._raises = raises
        self.delivered: list[object] = []

    async def deliver(self, notification: object) -> str:
        if self._raises:
            raise RuntimeError("channel is down")
        self.delivered.append(notification)
        return self._status


class _RecordingStore:
    """The durable-store surface the completion seam uses, and nothing else.

    Deliberately records the ID it was called with rather than asserting on it
    here: defect 2 was invisible precisely because every double used one id for
    both the trace and the task.
    """

    def __init__(self) -> None:
        self.delivered: list[tuple[str, str]] = []
        self.requeued: list[tuple[str, str]] = []

    async def mark_delivered(self, task_id: str, *, result: str) -> None:
        self.delivered.append((task_id, result))

    async def fail_and_requeue(
        self, task_id: str, *, error: str, failure_class: str = "",
        banned: tuple[str, ...] = (),
    ) -> str:
        self.requeued.append((task_id, error))
        return "pending"


def _state(
    *, trace_id: str = "turn-1", task_id: str | None = None,
    target: int | str | None = 4242, depth: int = 0,
) -> PipelineState:
    st = PipelineState(
        trace_id=trace_id,
        session_key="owl:secretary:telegram:dm:72055773",
        input_text="what is the answer",
        channel="telegram",
        owl_name="secretary",
        pipeline_step="deliver",
        delegation_depth=depth,
        reply_target=target,
        interactive=False,
    )
    st = st.evolve(
        responses=[
            ResponseChunk(
                content="the answer is 42", is_final=False, chunk_index=0,
                trace_id=trace_id, owl_name="secretary",
            ),
        ]
    )
    return st if task_id is None else st.evolve(task_id=task_id)


class TestTheProactiveFallbackProvesItsDelivery:
    async def test_an_answer_pushed_after_a_stream_miss_is_recorded_delivered(
        self, monkeypatch
    ) -> None:
        """The exact live case: the reader is gone, the push succeeds, the row
        must carry proof. Without this the turn reads completed-with-no-proof and
        NO sweep can ever see it — ``revive_undelivered_failures`` scans only
        ``status='failed'``."""
        store = _RecordingStore()
        svc = StepServices(
            stream_registry=StreamRegistry(),
            proactive_deliverer=_Deliverer("delivered"),  # type: ignore[arg-type]
            durable_task_store=store,
        )
        monkeypatch.setattr(deliver_step, "get_services", lambda: svc)

        await deliver_step.run(_state(trace_id="orphan-1"))

        assert store.delivered == [("orphan-1", "the answer is 42")]

    @pytest.mark.parametrize(
        ("case", "deliverer", "target"),
        [
            ("no deliverer is wired", None, 4242),
            ("the turn has no durable address", _Deliverer("delivered"), None),
            ("the transport raised", _Deliverer(raises=True), 4242),
            ("the transport reported failed", _Deliverer("failed"), 4242),
        ],
    )
    async def test_a_fallback_that_could_not_send_records_nothing(
        self, monkeypatch, case: str, deliverer: object, target: object
    ) -> None:
        """Four ways the push does not land, and in all four the user got NOTHING.

        Recording delivery here would import the overclaim shape into the one seam
        whose entire job is to refuse it. The row must stay open so the loop can
        recover the turn — which is the loop's whole contract.
        """
        store = _RecordingStore()
        svc = StepServices(
            stream_registry=StreamRegistry(),
            proactive_deliverer=deliverer,  # type: ignore[arg-type]
            durable_task_store=store,
        )
        monkeypatch.setattr(deliver_step, "get_services", lambda: svc)

        await deliver_step.run(_state(trace_id="orphan-2", target=target))

        assert store.delivered == [], f"marked delivered when {case}"


class TestTheProofLandsOnTheDurableRow:
    async def test_a_recovery_drive_proves_against_its_task_id_not_its_trace_id(
        self, monkeypatch
    ) -> None:
        """TaskRecovery mints ``trace_id="recover-<12hex>"`` and keeps the real
        ``task_id`` on the state. Keying the proof on the trace id writes it
        against a row that does not exist — the write-with-no-reader shape, and
        the reason a delivered recovery still reads as undelivered."""
        store = _RecordingStore()
        svc = StepServices(
            stream_registry=StreamRegistry(),
            proactive_deliverer=_Deliverer("delivered"),  # type: ignore[arg-type]
            durable_task_store=store,
        )
        monkeypatch.setattr(deliver_step, "get_services", lambda: svc)

        await deliver_step.run(
            _state(
                trace_id="recover-f1f930de554a",
                task_id="f1f930de554a45d29599c9d784d0cf2e",
            )
        )

        assert store.delivered == [
            ("f1f930de554a45d29599c9d784d0cf2e", "the answer is 42")
        ]

    async def test_an_ordinary_turn_still_proves_against_its_trace_id(
        self, monkeypatch
    ) -> None:
        """``enqueue_turn_task`` keys a chat row by the trace id and sets no
        ``task_id`` on the state. That path must be byte-identical — the fix may
        not move where an ordinary turn's proof lands."""
        store = _RecordingStore()
        registry = StreamRegistry()
        registry.create("turn-1")  # a LIVE reader — the ordinary streaming path
        svc = StepServices(stream_registry=registry, durable_task_store=store)
        monkeypatch.setattr(deliver_step, "get_services", lambda: svc)

        await deliver_step.run(_state(trace_id="turn-1"))

        assert store.delivered == [("turn-1", "the answer is 42")]
