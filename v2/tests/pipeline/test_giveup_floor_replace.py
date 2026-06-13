import pytest
from stackowl.infra import tool_outcome_ledger as tol
from stackowl.pipeline.giveup_floor import surface_consequential_giveup_floor
from stackowl.pipeline.state import PipelineState
from stackowl.pipeline.streaming import ResponseChunk


def _state(text):
    return PipelineState(trace_id="t", session_id="s", input_text="send the email", channel="cli",
                         owl_name="secretary", pipeline_step="deliver",
                         responses=(ResponseChunk(content=text, is_final=False, chunk_index=0,
                                                  trace_id="t", owl_name="secretary"),))


@pytest.mark.asyncio
async def test_replaces_dressed_up_draft_with_honest_floor():
    token = tol.bind()
    try:
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=False)
        s = _state("I have built the full agentic bridge for you. Here are the steps...")
        out = await surface_consequential_giveup_floor(s)
        delivered = "".join(c.content for c in out.responses)
        assert "built the full agentic bridge" not in delivered     # the excuse is GONE
        assert delivered.strip()                                     # floor is non-empty
        assert "send_email" in delivered or "could" in delivered.lower()  # honest floor names capability / couldn't
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_no_replace_when_consequential_succeeded():
    token = tol.bind()
    try:
        tol.record_tool_outcome(name="send_email", action_severity="consequential", success=True)
        s = _state("Done — sent the email.")
        out = await surface_consequential_giveup_floor(s)
        assert out.responses == s.responses
    finally:
        tol.reset(token)


@pytest.mark.asyncio
async def test_no_replace_when_no_consequential_attempt():
    token = tol.bind()
    try:
        s = _state("Here's the answer to your question.")
        out = await surface_consequential_giveup_floor(s)
        assert out.responses == s.responses
    finally:
        tol.reset(token)
