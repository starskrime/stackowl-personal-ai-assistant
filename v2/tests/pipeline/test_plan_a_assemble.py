from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import Message


def _state(**kw):
    base = dict(trace_id="t", session_id="s", input_text="hi",
                channel="cli", owl_name="default", pipeline_step="start")
    base.update(kw)
    return PipelineState(**base)


def test_state_defaults_history_and_system_prompt():
    s = _state()
    assert s.history == ()
    assert s.system_prompt is None


def test_state_evolve_carries_history():
    s = _state().evolve(history=(Message(role="user", content="prev"),))
    assert s.history[0].content == "prev"
    assert s.evolve(system_prompt="SYS").system_prompt == "SYS"
