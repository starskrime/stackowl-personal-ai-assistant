import pytest

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, set_services
from stackowl.pipeline.state import PipelineState


class _FakeStore:
    def __init__(self, skills): self._skills = skills
    async def get_many_by_name(self, names):
        return [s for s in self._skills if s.name in names]


class _Sk:
    def __init__(self, name, source="builtin", summary="Do the thing."):
        self.name, self.source, self.summary = name, source, summary
        self.description, self.when_to_use = "d", "w"


def _state(**kw):
    base = dict(trace_id="t", session_id="s", input_text="hi",
                channel="cli", owl_name="rsr", pipeline_step="start")
    base.update(kw)
    return PipelineState(**base)


@pytest.mark.asyncio
async def test_owned_skill_summary_injected():
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(name="rsr", role="research", system_prompt="P",
                                  model_tier="fast", skills=("research_skill",)))
    set_services(StepServices(owl_registry=reg, skill_store=_FakeStore([_Sk("research_skill")])))
    from stackowl.pipeline.steps import assemble
    out = await assemble.run(_state())
    assert "Do the thing." in out.system_prompt
    assert "As rsr" in out.system_prompt


@pytest.mark.asyncio
async def test_no_owned_skills_no_skill_block():
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(name="plain", role="r", system_prompt="P", model_tier="fast"))
    set_services(StepServices(owl_registry=reg, skill_store=_FakeStore([])))
    from stackowl.pipeline.steps import assemble
    out = await assemble.run(_state(owl_name="plain"))
    assert "skill_reference" not in (out.system_prompt or "")
