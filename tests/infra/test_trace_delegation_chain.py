from stackowl.infra.trace import TraceContext
from stackowl.pipeline.state import PipelineState


def test_pipeline_state_delegation_chain_defaults_empty():
    s = PipelineState(trace_id="t", session_id="s", input_text="i", channel="cli",
                      owl_name="secretary", pipeline_step="start")
    assert s.delegation_chain == ()
    assert s.evolve(delegation_chain=("a", "b")).delegation_chain == ("a", "b")


def test_trace_context_carries_delegation_chain():
    tok = TraceContext.start("s", trace_id="t", delegation_chain=("secretary", "scout"))
    try:
        assert TraceContext.get()["delegation_chain"] == ("secretary", "scout")
    finally:
        TraceContext.reset(tok)
    assert TraceContext.get()["delegation_chain"] == ()
