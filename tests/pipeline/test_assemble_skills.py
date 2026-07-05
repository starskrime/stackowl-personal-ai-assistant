from types import SimpleNamespace

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


class _CatalogStore:
    """Fake store that also answers list_enabled (for the global catalog path)."""
    def __init__(self, owned, enabled):
        self._owned, self._enabled = owned, enabled
    async def get_many_by_name(self, names):
        return [s for s in self._owned if s.name in names]
    async def list_enabled(self):
        return list(self._enabled)


def _settings(global_catalog: bool):
    return SimpleNamespace(skills=SimpleNamespace(global_catalog=global_catalog))


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


@pytest.mark.asyncio
async def test_global_catalog_surfaced_for_default_owl_when_enabled():
    """The default Secretary owns no skills, but with the catalog flag ON it must
    still learn that installed skills exist (CATALOG region, names only)."""
    FOCUS_TRACKER.clear_all()
    reg = OwlRegistry.with_default_secretary()
    store = _CatalogStore(
        owned=[],
        enabled=[_Sk("dl-video", "learned"), _Sk("hello", "learned")],
    )
    set_services(StepServices(owl_registry=reg, skill_store=store,
                              settings=_settings(global_catalog=True)))
    from stackowl.pipeline.steps import assemble
    out = await assemble.run(_state(owl_name="secretary"))
    sp = out.system_prompt or ""
    assert "CATALOG" in sp
    assert "dl-video" in sp and "hello" in sp
    assert "skill_view" in sp


@pytest.mark.asyncio
async def test_global_catalog_surfaces_every_source():
    """Native (builtin), installed (external), user, and synthesized (learned)
    skills must ALL be visible to the platform — the catalog is source-agnostic."""
    FOCUS_TRACKER.clear_all()
    reg = OwlRegistry.with_default_secretary()
    enabled = [
        _Sk("native-skill", "builtin"),
        _Sk("installed-skill", "installed"),
        _Sk("hand-written", "user"),
        _Sk("synthesized-skill", "learned"),
    ]
    store = _CatalogStore(owned=[], enabled=enabled)
    set_services(StepServices(owl_registry=reg, skill_store=store,
                              settings=_settings(global_catalog=True)))
    from stackowl.pipeline.steps import assemble
    sp = (await assemble.run(_state(owl_name="secretary"))).system_prompt or ""
    for name in ("native-skill", "installed-skill", "hand-written", "synthesized-skill"):
        assert name in sp, f"{name} (a real installed skill) not visible in the catalog"


@pytest.mark.asyncio
async def test_global_catalog_off_is_byte_identical_to_no_block():
    """Flag OFF → no skills block at all (byte-identical baseline preserved)."""
    FOCUS_TRACKER.clear_all()
    reg = OwlRegistry.with_default_secretary()
    enabled = [_Sk("dl-video", "learned"), _Sk("hello", "learned")]

    set_services(StepServices(owl_registry=reg,
                              skill_store=_CatalogStore(owned=[], enabled=enabled),
                              settings=_settings(global_catalog=False)))
    from stackowl.pipeline.steps import assemble
    off = (await assemble.run(_state(owl_name="secretary"))).system_prompt or ""

    # Same owl, a store with nothing relevant and no catalog → the true baseline.
    set_services(StepServices(owl_registry=reg,
                              skill_store=_CatalogStore(owned=[], enabled=[]),
                              settings=_settings(global_catalog=False)))
    baseline = (await assemble.run(_state(owl_name="secretary"))).system_prompt or ""

    assert "dl-video" not in off and "CATALOG" not in off
    assert off == baseline


@pytest.mark.asyncio
async def test_global_catalog_skipped_when_settings_absent():
    """Unconfigured (no settings wired) → feature OFF, baseline untouched."""
    FOCUS_TRACKER.clear_all()
    reg = OwlRegistry.with_default_secretary()
    store = _CatalogStore(owned=[], enabled=[_Sk("dl-video", "learned")])
    set_services(StepServices(owl_registry=reg, skill_store=store))  # settings=None
    from stackowl.pipeline.steps import assemble
    sp = (await assemble.run(_state(owl_name="secretary"))).system_prompt or ""
    assert "dl-video" not in sp


@pytest.mark.asyncio
async def test_owls_block_lists_other_owls_excludes_self():
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(name="Brain", role="research", system_prompt="P",
                                  model_tier="fast"))
    set_services(StepServices(owl_registry=reg, skill_store=_FakeStore([])))
    from stackowl.pipeline.steps import assemble
    out = await assemble.run(_state(owl_name="secretary"))
    sp = out.system_prompt or ""
    assert "Brain" in sp
    # The acting owl's own name must not appear via the owls_block (its persona
    # is injected separately, not as a "this owl exists" fact).
    owls_block_line = next(line for line in sp.splitlines() if "Brain" in line)
    assert "secretary" not in owls_block_line


@pytest.mark.asyncio
async def test_owls_block_fails_open_when_registry_list_raises():
    class _RaisingRegistry:
        def get(self, name):
            raise Exception("not found")

        def list(self):
            raise Exception("boom")

    set_services(StepServices(owl_registry=_RaisingRegistry(), skill_store=_FakeStore([])))
    from stackowl.pipeline.steps import assemble
    # Must not raise — registry.list() blowing up should degrade to no owls_block.
    out = await assemble.run(_state(owl_name="secretary"))
    assert "boom" not in (out.system_prompt or "")


@pytest.mark.asyncio
async def test_conversational_turn_gets_no_skills_block():
    """A conversational intent_class turn must NOT carry skill-block tokens even when
    the owl owns skills.  assemble gates the skills block on intent_class != 'conversational'
    so lean turns stay lean (no playbook tokens added to system_prompt)."""
    FOCUS_TRACKER.clear_all()
    reg = OwlRegistry.with_default_secretary()
    reg.register(OwlAgentManifest(name="rsr", role="research", system_prompt="P",
                                  model_tier="fast", skills=("research_skill",)))
    set_services(StepServices(owl_registry=reg, skill_store=_FakeStore([_Sk("research_skill")])))
    from stackowl.pipeline.steps import assemble
    out = await assemble.run(_state(intent_class="conversational"))
    sp = out.system_prompt or ""
    assert "Do the thing." not in sp, (
        f"skill summary leaked into conversational system_prompt: {sp!r}"
    )
    assert "research_skill" not in sp, (
        f"skill name leaked into conversational system_prompt: {sp!r}"
    )
