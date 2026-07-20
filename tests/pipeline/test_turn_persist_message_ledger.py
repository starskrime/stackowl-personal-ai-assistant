import pytest

from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk
from stackowl.pipeline.turn_persist import persist_turn


class _FakeMessageLedgerStore:
    def __init__(self):
        self.completed = []
        self.failed = []

    async def mark_completed(self, trace_id):
        self.completed.append(trace_id)
        return True

    async def mark_failed(self, trace_id, *, reason):
        self.failed.append((trace_id, reason))
        return True


@pytest.mark.asyncio
async def test_floored_turn_marks_message_ledger_failed(monkeypatch):
    ledger = _FakeMessageLedgerStore()

    class FakeServices:
        memory_bridge = None
        retry_queue_store = None
        message_ledger_store = ledger

    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: FakeServices()
    )

    state = PipelineState(
        trace_id="trace-floor", session_id="sess-x", input_text="do the thing",
        channel="telegram", owl_name="secretary", pipeline_step="respond",
        responses=(
            ResponseChunk(
                content="I couldn't fully complete this...", is_final=False,
                chunk_index=0, trace_id="trace-floor", owl_name="secretary", is_floor=True,
            ),
        ),
    )

    await persist_turn(state)

    assert ledger.completed == []
    assert len(ledger.failed) == 1
    assert ledger.failed[0][0] == "trace-floor"
    assert ledger.failed[0][1]  # a non-empty reason string


@pytest.mark.asyncio
async def test_clean_turn_marks_message_ledger_completed(monkeypatch):
    ledger = _FakeMessageLedgerStore()

    class FakeServices:
        memory_bridge = None
        retry_queue_store = None
        message_ledger_store = ledger

    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: FakeServices()
    )

    state = PipelineState(
        trace_id="trace-clean", session_id="sess-x", input_text="hello",
        channel="telegram", owl_name="secretary", pipeline_step="respond",
        responses=(
            ResponseChunk(
                content="hi there", is_final=True,
                chunk_index=0, trace_id="trace-clean", owl_name="secretary",
            ),
        ),
    )

    await persist_turn(state)

    assert ledger.completed == ["trace-clean"]
    assert ledger.failed == []


@pytest.mark.asyncio
async def test_missing_message_ledger_store_is_a_noop(monkeypatch):
    """None -> byte-identical to before this feature existed (no attribute error)."""

    class FakeServices:
        memory_bridge = None
        retry_queue_store = None
        # message_ledger_store deliberately absent — getattr default must apply.

    monkeypatch.setattr(
        "stackowl.pipeline.turn_persist.get_services", lambda: FakeServices()
    )

    state = PipelineState(
        trace_id="trace-none", session_id="sess-x", input_text="hello",
        channel="telegram", owl_name="secretary", pipeline_step="respond",
        responses=(
            ResponseChunk(
                content="hi there", is_final=True,
                chunk_index=0, trace_id="trace-none", owl_name="secretary",
            ),
        ),
    )

    await persist_turn(state)  # must not raise
