from stackowl.authz.bounds import BoundsSpec
from stackowl.owls.manifest import OwlAgentManifest
from stackowl.owls.owl_revalidator import revalidate_agent_owls
from stackowl.owls.registry import OwlRegistry


def _m(name, **kw):
    base = dict(name=name, role=name, system_prompt="p", model_tier="fast")
    base.update(kw)
    return OwlAgentManifest(**base)


def _reg(*manifests):
    reg = OwlRegistry()
    for m in manifests:
        reg.register(m, source_name="test")
    return reg


def test_reclamps_agent_owl_whose_bounds_exceed_ceiling():
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    wide = _m("scout", origin="agent", created_by="secretary",
              creation_ceiling=ceiling, bounds=BoundsSpec(tools=frozenset({"read_file", "shell"})))
    reg = _reg(wide)
    revalidate_agent_owls(reg)
    assert reg.get("scout").bounds.tools == frozenset({"read_file"})


def test_agent_owl_without_ceiling_fails_closed_to_empty_bounds():
    rogue = _m("ghost", origin="agent", created_by="x",
               creation_ceiling=None, bounds=BoundsSpec(tools=frozenset({"shell"})))
    reg = _reg(rogue)
    revalidate_agent_owls(reg)
    assert reg.get("ghost").bounds.tools == frozenset()


def test_agent_owl_with_unbounded_ceiling_fails_closed():
    # A PRESENT but tools-unbounded ceiling is a corruption signal for an agent owl
    # (the mint path always persists a concrete-tools ceiling) -> deny-all.
    rogue = _m("wide", origin="agent", created_by="x",
               creation_ceiling=BoundsSpec(tools=None),
               bounds=BoundsSpec(tools=frozenset({"shell", "read_file"})))
    reg = _reg(rogue)
    revalidate_agent_owls(reg)
    assert reg.get("wide").bounds.tools == frozenset()


def test_human_and_builtin_owls_untouched():
    human = _m("h", origin="human", bounds=BoundsSpec(tools=frozenset({"shell"})))
    builtin = _m("b", origin="builtin", bounds=None)
    reg = _reg(human, builtin)
    revalidate_agent_owls(reg)
    assert reg.get("h").bounds.tools == frozenset({"shell"})
    assert reg.get("b").bounds is None


def test_is_idempotent():
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    wide = _m("scout", origin="agent", created_by="s", creation_ceiling=ceiling,
              bounds=BoundsSpec(tools=frozenset({"read_file", "shell"})))
    reg = _reg(wide)
    revalidate_agent_owls(reg)
    revalidate_agent_owls(reg)
    assert reg.get("scout").bounds.tools == frozenset({"read_file"})


def test_one_bad_owl_does_not_abort_the_rest():
    ceiling = BoundsSpec(tools=frozenset({"read_file"}))
    good = _m("good", origin="agent", created_by="s", creation_ceiling=ceiling,
              bounds=BoundsSpec(tools=frozenset({"read_file", "shell"})))
    rogue = _m("ghost", origin="agent", created_by="x", creation_ceiling=None,
               bounds=BoundsSpec(tools=frozenset({"shell"})))
    reg = _reg(good, rogue)
    revalidate_agent_owls(reg)
    assert reg.get("good").bounds.tools == frozenset({"read_file"})
    assert reg.get("ghost").bounds.tools == frozenset()
