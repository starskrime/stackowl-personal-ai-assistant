import logging

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


# ---------------------------------------------------------------------------
# H1 — no-hidden-errors: unexpected persona injection failures must be loud
# ---------------------------------------------------------------------------

class _BrokenRegistry:
    """Stub registry whose .get() raises an unexpected non-not-found error."""

    def get(self, name: str):  # noqa: D102
        raise ValueError(f"simulated internal registry error for {name!r}")


@pytest.mark.asyncio
async def test_assemble_logs_error_on_unexpected_registry_exception(caplog):
    """When registry.get raises something other than OwlNotFoundError, assemble
    must self-heal (return a state) AND log at ERROR level."""
    set_services(StepServices(owl_registry=_BrokenRegistry()))
    from stackowl.pipeline.steps import assemble

    s = _state(owl_name="broken_owl", memory_context="some memory")
    with caplog.at_level(logging.ERROR, logger="stackowl.engine"):
        out = await assemble.run(s)

    # Self-heals: returns a valid PipelineState
    assert out is not None
    # Memory context is still present even without persona
    assert out.system_prompt is not None
    assert "some memory" in out.system_prompt
    # Loud ERROR log was emitted
    assert any(
        r.levelno == logging.ERROR and "persona injection FAILED" in r.getMessage()
        for r in caplog.records
    ), f"Expected ERROR log not found. Records: {[r.getMessage() for r in caplog.records]}"


@pytest.mark.asyncio
async def test_assemble_unknown_owl_degrades_quietly(caplog):
    """An OwlNotFoundError (unknown owl name) must NOT log at ERROR — it's a
    legitimate degradation for system/parliament routes. assemble must
    self-heal to memory-only."""
    reg = OwlRegistry.with_default_secretary()  # 'nonexistent_owl' is not registered
    set_services(StepServices(owl_registry=reg))
    from stackowl.pipeline.steps import assemble

    s = _state(owl_name="nonexistent_owl", memory_context="ctx")
    with caplog.at_level(logging.DEBUG, logger="stackowl.engine"):
        out = await assemble.run(s)

    assert out is not None
    assert out.system_prompt is not None
    assert "ctx" in out.system_prompt
    # Must NOT have an ERROR record for this path
    assert not any(
        r.levelno == logging.ERROR for r in caplog.records
    ), f"Unexpected ERROR for unknown-owl path: {[r.getMessage() for r in caplog.records]}"
