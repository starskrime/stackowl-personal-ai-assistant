import pytest

from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.registry import OwlRegistry
from stackowl.pipeline.services import StepServices, set_services
from stackowl.pipeline.state import PipelineState
from stackowl.skills.skill_focus import FOCUS_TRACKER


class _FakeStore:
    def __init__(self, skills): self._skills = skills
    async def get_many_by_name(self, names):
        return [s for s in self._skills if s.name in names]


class _Sk:
    def __init__(self, name, source="builtin", summary="Do the thing.",
                 description="d", when_to_use="w"):
        self.name, self.source, self.summary = name, source, summary
        self.description, self.when_to_use = description, when_to_use
        self.embedding = None


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
    # No query embedding -> fallback: the owned skill is injected (FULL/ACTIVE tier).
    assert "Do the thing." in out.system_prompt
    assert "research_skill" in out.system_prompt
    assert "ACTIVE" in out.system_prompt


@pytest.mark.asyncio
async def test_no_owned_skills_no_skill_block():
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(name="plain", role="r", system_prompt="P", model_tier="fast"))
    set_services(StepServices(owl_registry=reg, skill_store=_FakeStore([])))
    from stackowl.pipeline.steps import assemble
    out = await assemble.run(_state(owl_name="plain"))
    assert "skill_reference" not in (out.system_prompt or "")


@pytest.mark.asyncio
async def test_assemble_tiers_by_query_embedding():
    FOCUS_TRACKER.clear_all()
    rel = _Sk("rel", "builtin", "rel summary", "d", "w")
    rel.embedding = [1.0, 0.0]
    irrel = _Sk("irrel", "builtin", "irrel summary", "d", "w")
    irrel.embedding = [0.0, 1.0]
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(name="o", role="r", system_prompt="P",
                                  model_tier="fast", skills=("rel", "irrel")))
    set_services(StepServices(owl_registry=reg,
                              skill_store=_FakeStore([rel, irrel])))
    from stackowl.pipeline.steps import assemble
    state = _state(owl_name="o", query_embedding=(1.0, 0.0))
    out = await assemble.run(state)
    sp = out.system_prompt or ""
    assert "ACTIVE" in sp and "rel" in sp  # rel relevant -> ACTIVE


@pytest.mark.asyncio
async def test_assemble_fallback_when_no_query_embedding():
    FOCUS_TRACKER.clear_all()
    a = _Sk("a", "builtin", "sa", "d", "w")
    a.embedding = [1.0]
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(name="o", role="r", system_prompt="P",
                                  model_tier="fast", skills=("a",)))
    set_services(StepServices(owl_registry=reg, skill_store=_FakeStore([a])))
    from stackowl.pipeline.steps import assemble
    state = _state(owl_name="o", query_embedding=None)
    out = await assemble.run(state)
    assert "a" in (out.system_prompt or "")  # still injected via manifest-order fallback
