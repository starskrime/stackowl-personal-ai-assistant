import pytest

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.providers.base import Message


def _state(**kw):
    base = dict(trace_id="t", session_id="s", input_text="hi",
                channel="cli", owl_name="default", pipeline_step="start")
    base.update(kw)
    return PipelineState(**base)


def _make_registry_with_default() -> OwlRegistry:
    """Build a registry with a 'default' owl (secretary is mandatory but named
    'secretary', not 'default'). We register a minimal default manifest
    directly so tests can resolve owl_name='default'."""
    reg = OwlRegistry.with_default_secretary()
    reg.register(
        OwlAgentManifest(
            name="default",
            role="primary-assistant",
            system_prompt="You are a helpful default owl.",
            model_tier="standard",
        )
    )
    return reg


def test_state_defaults_history_and_system_prompt():
    s = _state()
    assert s.history == ()
    assert s.system_prompt is None


def test_state_evolve_carries_history():
    s = _state().evolve(history=(Message(role="user", content="prev"),))
    assert s.history[0].content == "prev"
    assert s.evolve(system_prompt="SYS").system_prompt == "SYS"


@pytest.mark.asyncio
async def test_assemble_prepends_persona_to_memory():
    reg = _make_registry_with_default()
    set_services(StepServices(owl_registry=reg))
    from stackowl.pipeline.steps import assemble
    s = _state(owl_name="default", memory_context="## Learned Preferences\n- likes tea")
    out = await assemble.run(s)
    assert out.system_prompt is not None
    assert "likes tea" in out.system_prompt
    manifest = reg.get("default")
    assert manifest.system_prompt.split("\n")[0] in out.system_prompt


@pytest.mark.asyncio
async def test_assemble_handles_no_memory():
    reg = _make_registry_with_default()
    set_services(StepServices(owl_registry=reg))
    from stackowl.pipeline.steps import assemble
    out = await assemble.run(_state(owl_name="default", memory_context=None))
    assert out.system_prompt  # persona alone, never None/empty


def test_assemble_registered_between_classify_and_execute():
    from stackowl.pipeline.registry import PIPELINE_STEPS
    names = [n for n, _ in PIPELINE_STEPS]
    assert "assemble" in names
    assert names.index("classify") < names.index("assemble") < names.index("execute")
