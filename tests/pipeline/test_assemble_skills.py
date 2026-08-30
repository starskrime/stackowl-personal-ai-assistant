import itertools
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


class _SpyCatalogStore:
    """Fake store recording which retrieval tier assemble.py's three-tier
    fallback (LAT.2) actually picked — implements all three so the router's
    own condition (not a missing hasattr) decides."""
    def __init__(self, owned, enabled):
        self._owned, self._enabled = owned, enabled
        self.calls: list[str] = []
    async def get_many_by_name(self, names):
        return [s for s in self._owned if s.name in names]
    async def list_enabled(self):
        self.calls.append("list_enabled")
        return list(self._enabled)
    async def semantic_recall(self, query_embedding, *, limit):
        self.calls.append("semantic_recall")
        return [(s, 1.0) for s in self._enabled]
    async def hybrid_recall(self, query_text, query_embedding, *, limit):
        self.calls.append("hybrid_recall")
        return [(s, 1.0) for s in self._enabled]


def _settings(global_catalog: bool):
    return SimpleNamespace(skills=SimpleNamespace(global_catalog=global_catalog))


class _Sk:
    # `summary` went with D09.3 slice 5 (migration 0110). The injector composes
    # description + when_to_use now, which is what D10.2's <=60-char description
    # and required rich when_to_use were sized for — so the double stands in for
    # a real skill by carrying those two, not a third cached field.
    def __init__(self, name, source="builtin",
                 description="Fetch a page.", when_to_use="When a page is needed."):
        self.name, self.source = name, source
        self.description, self.when_to_use = description, when_to_use
        self.embedding = None


#: Each call is a NEW incarnation. The skill catalogue is now frozen per
#: (incarnation, owl) — as the `profile` block already is — so a constant
#: session_key across tests made one test inherit another's frozen catalogue.
#: Production conversation_ids are unique per incarnation; this makes the fixture
#: match that rather than papering over it with a global reset.
_INCARNATION = itertools.count()


def _state(**kw):
    base = dict(trace_id="t", session_key=f"s-{next(_INCARNATION)}", input_text="hi",
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
    # D01.1 slice 4b — the owned skill still reaches the prompt, which is this
    # test's subject. What changed is the TIER: Q9 is "names + descriptions
    # always loaded; full body fetched on demand", so an owned skill now lands
    # under AVAILABLE with a skill_view pointer instead of ACTIVE with its body.
    assert "Fetch a page." in out.system_prompt
    assert "research_skill" in out.system_prompt
    assert "AVAILABLE" in out.system_prompt
    assert "skill_view research_skill" in out.system_prompt


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
    rel = _Sk("rel", "builtin", "d", "w")
    rel.embedding = [1.0, 0.0]
    irrel = _Sk("irrel", "builtin", "d", "w")
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
    # D01.1 slice 4b — the INVERSE is now the invariant. Tiering by
    # query_embedding was removed: it made the block differ per turn, the
    # largest reason invariant I1 was unreachable (skills_len 4169 -> 0 across
    # two turns of ONE conversation, measured 2026-07-27). Pinned as a removal,
    # DEBT-12 style, so query-shaped tiering cannot be quietly reintroduced.
    assert "ACTIVE" not in sp, "no skill may be promoted to a FULL body by the query"
    assert "rel" in sp, "but every skill is still catalogued"


@pytest.mark.asyncio
async def test_assemble_fallback_when_no_query_embedding():
    FOCUS_TRACKER.clear_all()
    a = _Sk("a", "builtin", "d", "w")
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
    # D01.1 slice 4b — the region is rendered with descriptions now rather than
    # as a bare name list, so the header moved from CATALOG to AVAILABLE. The
    # subject is unchanged and still asserted below: an owl that owns NO skills
    # must still learn that installed ones exist.
    assert "AVAILABLE" in sp
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
async def test_global_catalog_routes_to_hybrid_recall_when_both_signals_present():
    """LAT.2 three-tier fallback, tier 1: query_text + query_embedding both
    usable -> hybrid_recall (not semantic_recall, not list_enabled)."""
    FOCUS_TRACKER.clear_all()
    reg = OwlRegistry.with_default_secretary()
    store = _SpyCatalogStore(owned=[], enabled=[_Sk("dl-video", "learned")])
    set_services(StepServices(owl_registry=reg, skill_store=store,
                              settings=_settings(global_catalog=True)))
    from stackowl.pipeline.steps import assemble
    state = _state(owl_name="secretary", input_text="download a video",
                    query_embedding=(1.0, 0.0))
    await assemble.run(state)
    # D01.1 slice 4b — query-shaped retrieval removed. list_enabled() is the
    # only path now, because hybrid_recall(query_text, query_vec) made the
    # catalogue depend on what was just typed.
    assert store.calls == ["list_enabled"]


@pytest.mark.asyncio
async def test_global_catalog_routes_to_semantic_recall_when_only_embedding_present():
    """LAT.2 three-tier fallback, tier 2: only query_embedding usable (no
    input_text) -> semantic_recall."""
    FOCUS_TRACKER.clear_all()
    reg = OwlRegistry.with_default_secretary()
    store = _SpyCatalogStore(owned=[], enabled=[_Sk("dl-video", "learned")])
    set_services(StepServices(owl_registry=reg, skill_store=store,
                              settings=_settings(global_catalog=True)))
    from stackowl.pipeline.steps import assemble
    state = _state(owl_name="secretary", input_text="", query_embedding=(1.0, 0.0))
    await assemble.run(state)
    # D01.1 slice 4b — semantic_recall(query_vec) is query-shaped too, so it
    # is gone for the same reason as hybrid_recall above.
    assert store.calls == ["list_enabled"]


@pytest.mark.asyncio
async def test_global_catalog_routes_to_list_enabled_when_neither_signal_present():
    """LAT.2 three-tier fallback, tier 3: neither usable -> list_enabled()
    (today's behavior, byte-identical)."""
    FOCUS_TRACKER.clear_all()
    reg = OwlRegistry.with_default_secretary()
    store = _SpyCatalogStore(owned=[], enabled=[_Sk("dl-video", "learned")])
    set_services(StepServices(owl_registry=reg, skill_store=store,
                              settings=_settings(global_catalog=True)))
    from stackowl.pipeline.steps import assemble
    state = _state(owl_name="secretary", input_text="", query_embedding=None)
    await assemble.run(state)
    assert store.calls == ["list_enabled"]


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
    # D01.1 slice 4b — INVERTED by Bakir's Q9: "names + descriptions ALWAYS
    # loaded". The old skip kept chat turns lean, a real concern when the block
    # injected full BODIES. A catalogue is names and descriptions only, and a
    # block that vanishes on some turns forfeits the prefix cache on EVERY turn
    # — costing more than the tokens it saved. Depth still arrives on demand
    # through skill_view.
    assert "research_skill" in sp, "the catalogue is present on a conversational turn"
    assert "skill_view research_skill" in sp, "with the pointer to fetch the body"
